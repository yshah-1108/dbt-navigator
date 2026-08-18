# Data tests: what to test, where, and at what cost

Tests are the only automated statement of what a model guarantees. They are also the part of a dbt project most likely to be added by coverage instinct rather than by reasoning, which produces a test suite that is expensive to run, noisy to read, and silent about the failures that matter.

The governing question for every test is: **if this fails, does it mean something is wrong, and will anyone act on it?** A test that fails for a legitimate reason gets downgraded to `warn`, then ignored, and takes the real warnings with it.

## Three kinds of test, and what each cannot catch

| Kind | Defined in | Runs against | Catches | Cannot catch |
|---|---|---|---|---|
| Generic | YAML, applied by name to a model or column | Real data, after the model builds | Violations of a declared property: uniqueness, nullability, membership, referential integrity, ranges | Logic that is wrong in a way the data does not currently exhibit |
| Singular | A `.sql` file in `tests/`, returning failing rows | Real data, after the model builds | A specific assertion no generic test expresses — cross-model reconciliation, a business rule spanning several columns | The same blind spot: it can only see the data that exists |
| Unit | YAML, alongside the model | Fixture data, **before** the model builds | Transformation logic against inputs you invent, including cases production has never produced | Anything about real data — completeness, freshness, volume, actual uniqueness |

The division is not a matter of taste. A `not_null` test on a derived flag proves the column is populated; it cannot tell you the derivation is wrong, because a wrong value is still a value. A unit test proves the derivation handles the cases you thought of; it says nothing about whether today's load arrived. **Both are required, and neither substitutes for the other.** Unit tests are `dbt-unit-tests`.

Two version notes that affect everything below:

- **`tests:` was renamed `data_tests:`** to disambiguate from unit tests. `tests:` remains supported as an alias, but a single resource cannot use both keys. Match whatever the project already uses; do not introduce the second spelling into a project using the first.
- **Generic test arguments moved under an `arguments:` key.** From dbt Core 1.10.5 the `require_generic_test_arguments_property` flag defaults to true, and arguments passed as top-level properties raise a deprecation warning. Framework settings — `severity`, `where`, `store_failures`, `tags` — belong under `config:` and always did.

```yaml
# current form
columns:
  - name: order_status
    data_tests:
      - accepted_values:
          arguments:
            values: ['pending', 'shipped', 'delivered', 'cancelled']
          config:
            severity: warn
```

On a project older than 1.10.5 the `arguments:` key is not recognised and the values go at the top level. **Check the project's dbt version before writing either form** — this is the most common cause of a test that parses to nothing or fails with an unhelpful argument error.

## Which tests earn their cost

Every test is a query. On a large table, `unique` is a full scan and a group-by; `relationships` is a join. A suite that costs more than the models it guards is a suite someone will eventually disable, and they will disable all of it.

| Test | Earns its place when | Skip it when |
|---|---|---|
| `unique` on the key | Always. It is the only assertion of the model's grain | Never — but see the bounded-window discussion below for large incrementals |
| `not_null` on the key | Always | Never |
| `not_null` on other columns | The column is genuinely never null and a null indicates a real failure | The column is legitimately nullable. A `not_null` that has to be set to `warn` is worse than no test |
| `relationships` | The join is one consumers rely on and a gap means a broken report | The gap is a routine, expected loading-order artefact — unless bounded with a `where` |
| `accepted_values` | The column is a genuine enum and a new value indicates an upstream change you must react to | The set is large, open, or changes as a matter of course |
| Range bounds on a measure | The bound is a real business rule, not a plausible-looking guess | You picked the number to make the test pass |
| Row-count comparisons | The relationship between two models is exact or has a known bound | The ratio drifts naturally |
| Freshness / recency | The model has a stated refresh expectation | Nobody has stated one — get the expectation first |

**The highest-leverage test in most projects is `unique` on a dimension's key.** A dimension that fans out multiplies every fact joined to it. One test, and it protects everything downstream.

**The lowest-value tests are `not_null` on descriptive columns.** They are cheap to write, numerous, and almost never fire for a reason anyone acts on — which trains readers that a failing test is probably nothing.

## Severity, thresholds, and what they are for

```yaml
data_tests:
  - unique:
      config:
        severity: error
        error_if: ">1000"
        warn_if: ">10"
```

- `severity`: `error` (default) or `warn`.
- `error_if` / `warn_if`: comparison expressions against the failure count. Default `!=0`.

The evaluation order is worth knowing, because it is not symmetric. At `severity: error`, dbt checks `error_if` first; if unmet it checks `warn_if`, and warns if that is met. At `severity: warn`, `error_if` is skipped entirely.

That gives a genuinely useful middle setting: **a small number of failures warns, a large number blocks.** For a relationship test on a dimension that loads slightly behind, ten orphans is a timing artefact and a thousand is a broken pipeline — and the same test can say both.

Two rules about `warn`:

- **`warn` is for assertions that are genuinely advisory** — measurement drift between two sources, expected seasonal variance, a referential gap with a known cause. It is not a mute button. Downgrading a real failure converts a blocking signal into log noise, and the data stays wrong. See `dbt-debugging-failures`.
- **Warnings must be read.** A `warn` nobody looks at is a deleted test that still costs compute. If the project has no mechanism for surfacing warnings, `warn` is equivalent to disabling the test — and `--warn-error` or its scoped form is how CI can promote them when it matters.

### `error_if` to assert an expected failure

Occasionally you want to prove a test *does* fire — validating that a custom generic test catches known-bad rows. Inverting the condition makes zero failures the error:

```yaml
data_tests:
  - <custom_generic_test>:
      config:
        error_if: "<1"
        warn_if: "<0"
```

The `warn_if: "<0"` takes the default warn condition out of play, which is otherwise still `!=0` and will fire. This is niche, but it is the mechanism that turns "I believe this test works" into evidence.

## Bounding an expensive test

Three mechanisms, in increasing order of how much they weaken the assertion.

**`where` restricts the rows tested.** The assertion still holds fully, but only over the subset.

```yaml
data_tests:
  - unique:
      config:
        where: "<partition_column> >= <recent_boundary>"
```

**`limit` caps the rows returned by the test query.** It does not reduce the scan and it does not reduce the assertion — it reduces the size of the failure output. Useful with `store_failures` on a test that can fail in bulk.

**Sampling, by testing a filtered slice**, weakens the assertion to "true of the slice". State that plainly wherever it is done; a test named `unique` that only checks last week is not the test its name implies.

### The bounded-window uniqueness test, and when it is wrong

On a large, long-history incremental model that runs frequently, `unique` over the whole table can cost more than the model does. The usual response is a variant bounded to the recently-written window, falling back to a full scan on a full refresh. `testing.primary_key_incremental` in the contract names whatever the project uses.

**This is only sound if the model appends.** If the incremental model can rewrite historical rows — a dimension-style incremental, a merge whose `unique_key` matches on something other than a date, a backfill — a duplicate introduced in old data is outside the window and will never be found. Those models need the full-table test, and if that is too expensive the answer is to change the model, not to weaken the test.

## `store_failures`: seeing the rows that failed

```yaml
data_tests:
  - unique:
      config:
        store_failures: true
        store_failures_as: table    # table | view | ephemeral (default)
```

`store_failures_as` takes precedence over `store_failures`, and its default, `ephemeral`, stores nothing. Failures land in a schema derived from the target schema with a `_dbt_test__audit` suffix unless configured otherwise, and each run replaces the previous failures for that test — including replacing them with nothing when the test passes.

Worth enabling on tests whose failures need investigating rather than merely counting: a relationship test where you need the orphaned keys, an `accepted_values` test where you need the new value. Not worth enabling everywhere — it creates a relation per test and needs create-schema permission, which is the most common reason `--store-failures` fails on first use.

**Two cautions.** The failures relation contains real data, created by a test rather than by a model, in a schema that appears in no model review. On a column carrying any sensitivity classification, check where it lands before enabling it — see `dbt-handling-sensitive-data`. And a `view` rather than a `table` re-executes the test query whenever anyone selects from it, which on a large model is a surprise cost attached to an innocuous-looking relation.

## Where a test belongs

**Test the claim where the claim originates.** A failure should name the model that caused it, not a model three layers downstream that merely propagated it.

| Assertion | Belongs on |
|---|---|
| The source has no duplicates on its natural key | The source, or the source-facing model |
| A column's values fall in a known set | The layer where the set is first established — usually source-facing |
| The grain of a transformation holds | The transformation model itself |
| A business rule spanning several models holds | The model where the rule is implemented |
| The published output has the shape consumers expect | The consumer-facing model, ideally as a contract rather than a test |

**Do not test the same claim at every layer.** If uniqueness on `order_id` is tested in the source-facing model, testing it again in three downstream models that neither change the grain nor join anything is three extra scans that can only fail if the first one did. The exception is worth stating clearly: **any model that changes the grain, joins, unions, or deduplicates has a new claim to make**, and a uniqueness test there is not a duplicate of the upstream one — it asserts something the upstream test cannot.

The dbt Labs guidance to test heavily at the edges — sources in, published models out — captures most of this. The middle needs tests where the middle makes a claim.

## Naming tests

A generic test's default name is generated and unreadable in a failure log. Naming it costs one line and turns a failure into an instruction:

```yaml
data_tests:
  - name: order_status_has_no_unexpected_values
    test_name: accepted_values
    arguments:
      values: ['pending', 'shipped', 'delivered', 'cancelled']
    config:
      severity: warn
```

Add a `description` to any test whose failure needs context — what to check, whom to ask. Both generic and singular tests support descriptions from dbt 1.9. This is the difference between an on-call engineer diagnosing a failure and an on-call engineer re-deriving what the test was for.

## Package tests worth knowing

Neither package needs to be exhaustively learned. These are the tests that express something the built-in four cannot, and each maps to a failure that is otherwise undetectable.

### dbt-utils

| Test | Asserts | Prevents |
|---|---|---|
| `unique_combination_of_columns` | A column set is unique together | The composite-grain duplicate, without generating or scanning a hashed key. Cheaper than `unique` over a concatenation on a large table |
| `expression_is_true` | An arbitrary SQL predicate holds for every row | Cross-column arithmetic drift — a total that no longer equals the sum of its parts, a bound that depends on another column |
| `accepted_range` | A value falls within bounds, which may be another column or an expression | Negative quantities, a date in the future, a subset count exceeding its total |
| `equal_rowcount` | Two relations have the same row count | A refactor that silently dropped rows; a union branch that stopped loading |
| `fewer_rows_than` | This relation has fewer rows than another | A filtered or aggregated model that has started fanning out |
| `mutually_exclusive_ranges` | Ranges do not overlap, with `gaps` as `allowed`/`not_allowed`/`required` | Overlapping validity windows, which make every as-of join fan out. The single most valuable test on any history or effective-dating model |
| `recency` | The newest row is within an interval | A model that stopped updating while continuing to pass every other test |
| `relationships_where` | Referential integrity over a filtered subset | The relationship test that has to be `warn` because of a known, bounded exception — bound it instead and keep it at `error` |
| `not_null_proportion` | At least *n* proportion of values are non-null | Gradual degradation in a column that is legitimately sometimes null, where `not_null` is unusable |
| `cardinality_equality` | Two columns have the same set of distinct values | A dimension that has quietly lost members |
| `not_accepted_values` | Specific values are absent | A sentinel or placeholder value reappearing after it was supposed to be cleaned |
| `sequential_values` | No gaps in a sequence or time series | A missing day in a date spine, a skipped sequence number |
| `equality` | Two relations are identical, optionally on a column subset with a numeric precision | Refactor equivalence — though `audit_helper` is better at this, below |

Several of these accept `group_by_columns`, which turns a global assertion into a per-group one — uniqueness within a group, recency per entity. That is frequently the assertion actually intended.

### dbt_expectations

Overlapping with dbt-utils and worth adding for four things it does better:

| Test | Asserts |
|---|---|
| `expect_column_values_to_be_between` | Range bounds, including on dates and with row conditions |
| `expect_table_row_count_to_be_between` | Volume is within an expected band — the assertion that catches a partial load, which freshness cannot |
| `expect_column_values_to_match_regex` | Format conformance for codes, identifiers, and structured strings |
| `expect_row_values_to_have_recent_data` | Recency, with a `group_by` variant for per-entity staleness |
| `expect_table_aggregation_to_equal_other_table` | A grouped aggregate matches the same aggregate in another relation — reconciliation as a test rather than a manual query |
| Distributional tests (`within_n_stdevs`, moving-average variants) | Anomaly detection. Powerful, and the ones most likely to become noise |

**Do not install both packages to write the same assertion in two syntaxes.** Pick the project's convention and stay in it; a suite where the same claim appears under two names is a suite nobody can audit.

**Be sceptical of distributional tests.** They fail on legitimate business change — a launch, a seasonal peak, a pricing change — and each false positive costs credibility. Use them where the distribution is genuinely stable and someone has agreed to investigate every alert.

## Testing what usually goes untested

Five things almost no project asserts, each with a cheap test available.

**Grain.** Covered by `unique` on the key — but only if the key matches the grain. If the model has no generated key, `unique_combination_of_columns` over the grain columns is the direct assertion, and it does not require adding a column.

**Referential integrity, in the right direction.** `relationships` on a fact's foreign key asserts every fact points at a real entity. It says nothing about the reverse — entities with no facts — which is usually fine, and about dimension members that have disappeared, which is usually not. `cardinality_equality` or a singular test covers the reverse where it matters. Decide which direction the business cares about; testing the wrong one is a test that passes while the problem is present.

**Additivity.** That a measure is consistent with its parts is an `expression_is_true` away:

```yaml
data_tests:
  - dbt_utils.expression_is_true:
      arguments:
        expression: "<total_column> = <part_a> + <part_b>"
```

This catches the class of bug where a fan-out or a null in one component silently breaks a total, which no column-level test can see. For a semi-additive or non-additive measure, the test is that the components are stored rather than the ratio — which is a review check, not a data test.

**Timezone correctness.** Rarely testable directly, but two proxies work. An `accepted_range` with `max_value` set to the current timestamp catches timestamps in the future, which is what an offset applied in the wrong direction produces. And an `expression_is_true` asserting that a date column equals the truncation of its timestamp column in the intended zone catches the mismatch between an instant and the daily grain derived from it.

**Volume.** Freshness proves the newest row is recent. It does not prove the load was complete — a run that succeeded with zero rows passes every freshness check. `expect_table_row_count_to_be_between`, or `equal_rowcount` against the upstream model, is the assertion that catches a partial load.

## Proving equivalence: `audit_helper`, not a test

When the question is "does this refactored model produce the same rows as before", a test is the wrong instrument. Tests assert properties; equivalence needs a row-level comparison.

`audit_helper.compare_relations` is that comparison, and the acceptance criterion is zero rows differing in either direction. The mechanics, the no-primary-key case, and how to handle floating-point noise from reordered arithmetic are in `dbt-verification`. `dbt-refactoring-safely` is the procedure that uses it.

The reason it is not a test: a comparison against a baseline is a one-time proof about a change, not a standing property of the model. Encoding it as a test leaves a permanent dependency on a relation that will be dropped.

## Test cost in CI

Tests are frequently the majority of CI time, and the fix is usually selection rather than deletion.

- **Test only what changed and what depends on it.** State-based selection — modified nodes plus descendants — is the single largest saving available. See `dbt-command-reference`.
- **`dbt build` rather than `run` then `test`.** It interleaves in DAG order, so a model whose upstream test failed is not built at all, which saves both compute and a confusing failure downstream.
- **Exclude unit tests from production**, where their static inputs re-derive a known answer at real cost. Include them in CI, where they are the cheapest tests available.
- **`--empty` for structural validation.** A build with zero-row inputs validates SQL against the warehouse for near-nothing, which is the right first gate on an expensive model.
- **Bound the expensive tests with `where`** rather than deleting them, and say in the YAML what the bound is.

## Checklist

- [ ] Every test traced to a claim someone would act on if it failed
- [ ] `tests:` versus `data_tests:` matches the project's existing spelling
- [ ] Argument syntax matches the project's dbt version; framework settings under `config:`
- [ ] `unique` + `not_null` on the key, unconditionally
- [ ] No `not_null` on a legitimately nullable column
- [ ] Relationship severity chosen deliberately, or bounded with `relationships_where` instead of downgraded
- [ ] `error_if` / `warn_if` used where a small failure count is tolerable and a large one is not
- [ ] Bounded-window key tests used only on append-only incrementals
- [ ] `store_failures` enabled only where the rows will be investigated, and the destination schema checked
- [ ] Each claim tested once, at the layer that makes it; grain re-tested wherever the grain changes
- [ ] Tests named and, where the failure needs context, described
- [ ] One package convention, not two syntaxes for the same assertion
- [ ] Grain, referential direction, additivity, and volume each asserted or consciously skipped
- [ ] CI selection state-based; unit tests excluded from production
- [ ] `dbt parse` clean, so no test silently applies to nothing

## Failure modes

1. **A test that silently applies to nothing.** The column name in the YAML no longer matches the model's output. The test passes because it has no rows. `dbt parse` catches it; nothing else will.
2. **A `warn` that nobody reads.** Compute spent, assertion effectively deleted, and the appearance of coverage retained.
3. **A relationship test at `error` on a dimension that loads later.** Nightly failures for a non-defect, then a downgrade, then unnoticed real breaks.
4. **A bounded key test on a model that rewrites history.** Duplicates introduced outside the window are unreachable, and the test's name says it checks uniqueness.
5. **The same claim tested at four layers.** Four scans, one possible cause, and a failure list that names three models that are not responsible.
6. **A range bound chosen to make the test pass.** It now asserts nothing, and it will be believed.
7. **Freshness treated as completeness.** A zero-row load passes. The model is fresh and empty.
8. **A distributional test on a metric with legitimate seasonality.** Fires every peak, gets muted, and the muting outlives the season.
9. **`store_failures` on a sensitive column.** Real values persisted into an unreviewed schema by a test rather than a model.
10. **Arguments written in the wrong syntax for the project's dbt version.** The test parses without its arguments or fails with an error that names the framework rather than the YAML.
