---
name: dbt-unit-tests
description: Use when a model contains logic that data tests cannot verify — regex, date math, window functions, many-branch case statements, truncation, or custom parsing. Also use before refactoring non-trivial logic, when a bug is reported against a model's transformation, and to cover edge cases that do not yet exist in the data.
metadata:
  phase: prove
---

# Unit tests

Data tests check the data you have. Unit tests check the logic against data you invent — including the cases production has not produced yet.

That difference is the whole point. A `not_null` test on a `is_valid_email` column proves the column is populated. It cannot tell you the regex rejects `missingdot@gmailcom`, because no such row exists yet. When one arrives, the column will be populated, the test will pass, and the value will be wrong.

Available from dbt 1.8+. Verify the project's version before authoring — on an older version these do not exist and the guidance in `dbt-authoring-schema-yaml` is all you have.

Fixture mechanics — formats, typing, nulls versus empty strings, mocking sources, and overriding macros and vars — are in [fixtures.md](fixtures.md).

## What a unit test cannot catch

Worth stating up front, because a project that adopts unit tests sometimes thins its data tests to compensate, which is a net loss.

| Question | Unit test | Data test |
|---|---|---|
| Does this `case` expression handle a null amount? | Yes | No |
| Is the boundary `>` or `>=`? | Yes | No |
| Does the regex reject a malformed value? | Yes | Only once one arrives |
| Is the key actually unique in production? | **No** | Yes |
| Did today's load arrive? | **No** | Yes |
| Are there orphaned foreign keys? | **No** | Yes |
| Did the row count halve? | **No** | Yes |

A unit test runs on invented rows and asserts the transformation. It has no opinion about real data and never will. The two kinds are complements; see the test strategy discussion in `dbt-authoring-schema-yaml`.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | Use |
|---|---|
| `naming.yaml_file_pattern` | where the unit test YAML belongs alongside the model |
| `testing` | the project's overall test policy, for consistency of naming |

No contract field governs unit tests directly. **Without a contract:** follow the generic sequence below and name tests `test_<what_is_being_proven>`.

## 1. Decide whether this model needs one

Unit tests are not free — each one is fixture data that must be maintained. Add one when the model has logic whose correctness is not self-evident:

| Add a unit test | Do not bother |
|---|---|
| Regex | `min()`, `max()`, `sum()` — the warehouse tests these |
| Date and timestamp math | A straight column passthrough |
| Window functions | A simple rename or cast |
| `case when` with many branches | A join whose behavior is obvious |
| Truncation and rounding | Anything with no branching |
| Custom parsing or string manipulation | |
| **Logic that had a bug reported against it** | |
| **Edge cases not yet present in the data** | |
| **Any model about to be refactored** | |
| High-criticality models — contracted, public, or feeding an exposure | |

The three bold rows are the highest-value cases. A bug reported once will recur; a unit test is how you make that impossible rather than unlikely.

**Before refactoring, write the unit test first.** It converts "I believe this is equivalent" into evidence. This complements the output comparison in `dbt-refactoring-safely` — that proves the whole result set is unchanged on real data, this proves specific branches behave correctly on constructed data. Use both: real data cannot reach every branch.

## 2. Put it in the right place

**Unit tests live in a `.yml` file under `models/`, alongside the model.** Not in `tests/` — that directory is reserved for data tests, and dbt will not discover unit tests there.

```bash
ls models/<path_to_model>/           # unit test YAML belongs here
```

Fixture files, if you use them, go in a `fixtures/` subdirectory of a test path.

## 3. Know the constraints before writing

These are hard limits. Discovering them after writing a test wastes the effort:

- **SQL models only.** No Python models.
- **Models in the current project only.** Not models from a package or another project. This means a model whose logic lives mostly in a package macro is only partly testable.
- **Not `materialized_view`**, not recursive SQL, and **not a model using an introspective query** — a macro that queries the warehouse at compile time to discover column values or names cannot be resolved against fixture data. A model built with `dbt_utils.pivot` over discovered values, or `get_column_values`, is therefore untestable as written. If the model needs a unit test, the introspection has to go; see the discussion of introspective macros in `dbt-authoring-sql-models`.
- **Every `ref()` and `source()` in the model must appear as an `input`.** Omit one and compilation fails with "node not found" — a confusing error whose real cause is a missing input.
- **Table names must be aliased** for `join` logic to be testable.
- **Direct parents must exist in the warehouse** before the test can run. They do not need data — see step 6.
- **No coverage of the materialization itself.** You are testing the model's `select`. Whether dbt merged, inserted, or replaced correctly is outside the scope, and there is currently no mechanism for it.
- **One test case is one pass or fail**, not a count of failing rows. A test with twenty fixture rows reports a single result and a diff.

## 4. Write the test

Three parts: which model, what goes in, what must come out.

```yaml
unit_tests:
  - name: test_<what_this_proves>
    description: "Which edge cases this covers, and why they matter."
    model: <model_name>
    given:
      - input: ref('<upstream_model>')
        format: dict
        rows:
          - {<column>: <value>, <other_column>: <value>}
          - {<column>: <value>, <other_column>: <value>}
      - input: ref('<second_upstream>')
        format: dict
        rows:
          - {<column>: <value>}
    expect:
      format: dict
      rows:
        - {<column>: <value>, <derived_column>: <expected_result>}
```

**You only supply the columns the logic touches.** With `dict` and `csv`, unspecified columns are not required. This is what keeps unit tests readable — a model with sixty columns needs fixtures for the three the logic reads, not sixty.

**Name the test after what it proves**, not after the model. `test_is_valid_email_rejects_missing_at_sign` tells a reader what broke when it fails; `test_dim_customers_1` does not.

**Put the edge cases in the fixture, one row each, and make the expected value obviously right.** A fixture row whose correct output requires thought is a fixture row that will be "fixed" to match a bug.

### Input formats

| Format | Use when |
|---|---|
| `dict` | Default. Most readable for a handful of rows. |
| `csv` | Many rows, or the data is naturally tabular. Can be inline or a `fixture:` file. |
| `sql` | **Required** when the input is an ephemeral model. Also useful for generating rows, or forcing an exact type. |

Reference an external fixture instead of inlining:

```yaml
    expect:
      format: csv
      fixture: <fixture_file_name>      # from fixtures/<name>.csv
```

Two things that bite before anything else: **a CSV fixture cannot express an empty string** — an empty field is a null — and **fixture values carry the type of the literal you wrote**, not the type of the real upstream column. Both are covered with the workarounds in [fixtures.md](fixtures.md).

### The edge cases worth constructing

The value of a unit test is proportional to how unlikely the case is to appear in today's data. In rough order of return:

| Case | Why real data will not give it to you |
|---|---|
| A null in every column the logic reads | The current source happens to populate it |
| An empty string where null is expected | Requires a specific upstream defect to occur |
| A value exactly on a boundary — the `>` vs `>=` case | Depends on a timestamp collision you cannot schedule |
| A duplicate on the key, to prove the deduplication tie-break | Only appears when the source misbehaves |
| Zero as a divisor | The guard is never exercised until an outage produces it |
| A negative or refunded amount | Business-dependent, and sometimes never in dev data |
| A date crossing a month, quarter, or year boundary | Arrives once a period, long after the code shipped |
| A timestamp near midnight in a non-UTC zone | The off-by-one-day bug that only fires for some users |
| An unmatched join key | Depends on load ordering |
| A category value not in the accepted set | By definition not present yet |

Each of these is a row in a fixture and a line of expectation. That is the cheapest correctness evidence available anywhere in a dbt project.

## 5. Unit test an incremental model

This has one rule that is easy to get backwards, and getting it backwards makes every such test wrong.

> **The expected output is what will be merged or inserted — not what the final table looks like afterwards.**

You are testing the model's `select`, not dbt's materialization. There is currently no way to unit test whether dbt merged the rows correctly.

Override `is_incremental()` to test both branches, and supply `this` for the existing table:

```yaml
unit_tests:
  - name: test_<model>_full_refresh_mode
    model: <model_name>
    overrides:
      macros:
        is_incremental: false
    given:
      - input: ref('<upstream>')
        rows:
          - {<id>: 1, <event_time>: 2020-01-01}
    expect:
      rows:
        - {<id>: 1, <event_time>: 2020-01-01}

  - name: test_<model>_incremental_mode
    model: <model_name>
    overrides:
      macros:
        is_incremental: true
    given:
      - input: ref('<upstream>')
        rows:
          - {<id>: 1, <event_time>: 2020-01-01}
          - {<id>: 2, <event_time>: 2020-01-02}
      - input: this            # what the model already contains
        rows:
          - {<id>: 1, <event_time>: 2020-01-01}
    expect:
      rows:                    # only the new row is inserted
        - {<id>: 2, <event_time>: 2020-01-02}
```

**This is the best available test of a boundary predicate.** The `>` versus `>=` bug described in `dbt-incremental-models` is exactly what this catches: give the fixture a row whose timestamp equals the existing maximum, and assert whether it should appear. Real data cannot be relied on to produce that case on demand — a fixture can.

You can also override `vars` and environment variables the same way. See [fixtures.md](fixtures.md) for the general form, and for the case that matters most beyond `is_incremental`: **overriding a macro that returns the current date or timestamp**. A unit test whose expected output depends on when it runs will pass today and fail on a boundary nobody changed.

Two limits to keep in view:

- **`is_incremental` overridden to `true` does not simulate the merge.** The `unique_key`, the `incremental_strategy`, and `merge_update_columns` are materialization concerns and are not exercised. A test can prove the `select` returns the right candidate rows and still leave a wrong `unique_key` undetected.
- **A model that references `this` outside an `is_incremental()` block** needs `input: this` in every unit test, including the full-refresh one, usually with `rows: []`.

## 6. Build the parents cheaply, then run

Parents must exist as relations, but they do not need rows. Build empty:

```bash
dbt run --select "<upstream_a> <upstream_b>" --empty
```

For incremental models, the model itself must also exist:

```bash
dbt run --select "config.materialized:incremental" --empty
```

Then run:

```bash
dbt test --select <test_name>                        # one test
dbt test --select "<model_name>,test_type:unit"      # unit tests on one model
dbt test --select "test_type:unit"                   # all unit tests
dbt build --select <model_name>                      # unit tests, then build, then data tests
```

`dbt build` is the right default — it runs unit tests *before* materializing, which is the entire benefit. See `dbt-command-reference`.

## 7. Read the failure properly

A failure prints the diff between actual and expected:

```
actual differs from expected:
@@ ,email           ,is_valid_email_address
→  ,cool@example.com,True→False
```

**Do not assume the model is wrong.** A unit test failure has three possible causes, in decreasing order of likelihood:

1. **The model's logic is wrong** — the test did its job.
2. **The expected value is wrong** — you encoded your misunderstanding into the fixture.
3. **The fixture is unrealistic** — the input could not occur in practice, so the test asserts a case that does not matter.

Decide which before editing anything. Changing the expected value to match the actual output is the standard way a unit test is silently neutered: it will pass forever and prove nothing. If you conclude the expectation was wrong, say so explicitly and say why.

Unit tests return exit code 0 or 1 per test case, not a count of failing rows — one test case is one pass or one fail regardless of how many fixture rows differ.

## 8. Keep them out of production

Fixture inputs are static, so running them in production spends compute to re-derive a known answer. Run them in development and CI only:

```bash
dbt build --exclude-resource-type unit_test    # production builds
```

The corollary: **include them in CI**, where they are the cheapest tests available. They need no warehouse data, they run before the model materializes, and a failure stops the build before anything is written. On a project where CI cost is a constraint, unit tests are the last thing to cut.

## Completion checklist

- [ ] Project confirmed to be on dbt 1.8+
- [ ] Model's logic genuinely warrants a unit test — not a passthrough or a built-in aggregate
- [ ] Model does not use an introspective query, which makes it untestable as written
- [ ] Test YAML placed under `models/`, not `tests/`
- [ ] Every `ref()` and `source()` in the model supplied as an `input`
- [ ] Only the columns the logic touches included in fixtures
- [ ] Empty-string cases not written in `csv`, where they are inexpressible
- [ ] Types forced with `format: sql` where the logic depends on decimal or timezone semantics
- [ ] Any macro returning the current date or timestamp overridden, so the test is deterministic
- [ ] Test named for what it proves, not for the model
- [ ] Edge cases each given their own fixture row with an obviously-correct expectation
- [ ] Ephemeral inputs use `format: sql`
- [ ] Incremental models: both `is_incremental` branches tested
- [ ] Incremental models: expectation is the merged/inserted rows, not the final table
- [ ] `input: this` supplied wherever the model references itself, including full-refresh tests
- [ ] Data tests still in place for uniqueness, freshness, and volume, which unit tests cannot cover
- [ ] Parents built with `--empty` before running
- [ ] Test observed to FAIL when the logic is wrong — not just to pass
- [ ] Excluded from production builds, included in CI

## Common failure modes

1. **A test that has never failed.** If it passed on the first run and you never saw it fail, you do not know it tests anything. Break the logic deliberately, confirm the test catches it, then restore. This is the single most important item in the checklist.
2. **Editing the expectation to make it pass.** Converts a failing test into a passing one that certifies the bug. If the expectation was genuinely wrong, state that conclusion and its reasoning — do not silently adjust the number.
3. **Test placed in `tests/`.** Never discovered, never runs, and nothing warns you. The directory is for data tests.
4. **A missing `input` for a `ref()`.** Fails with "node not found," which reads like a broken model rather than an incomplete test.
5. **Expecting the final table on an incremental model.** The assertion is about the rows being merged. Expecting the post-merge table produces a test that cannot pass and looks like a dbt defect.
6. **Fixtures that drift from reality.** A fixture asserting a shape the source stopped producing tests a world that no longer exists. When a source's schema changes, the unit tests are part of the change.
7. **Unit tests treated as a substitute for data tests.** They verify logic on invented data; they say nothing about whether real data is complete, fresh, or unique. Both are needed — see `dbt-authoring-schema-yaml`.
8. **Running them in production.** Static inputs, known answer, real compute.
9. **A test whose result depends on the current date.** No override on the time-returning macro, so it passes now and fails at a period boundary.
10. **An empty-string case written as a CSV fixture.** The field becomes a null, the branch under test is never reached, and the test reports success.
11. **A bare integer standing in for a decimal measure.** Division truncates in the test and not in production, or the reverse, and the failure points at logic that is correct.
12. **`is_incremental: true` mistaken for a test of the merge.** The `unique_key` and strategy are untested; a wrong `unique_key` passes every unit test in the project.
