---
name: dbt-authoring-schema-yaml
description: Use when writing or editing a dbt schema YAML file — documenting a model or its columns, choosing which tests a column needs, adding a model contract with enforced data types and constraints, or writing a unit test. Also use when a description starts with "This model..." or "Contains...", or when a model has no YAML at all.
metadata:
  phase: build
---

# Authoring schema YAML

The YAML file is where a model stops being private. Its descriptions surface in BI tools as field labels and tooltips, in the docs site, and in whatever retrieval an agent uses to decide which model answers a question. Its tests are the only automated statement of what the model guarantees.

Both are usually written last, in a hurry, and it shows.

| Sub-document | Covers |
|---|---|
| [data-tests.md](data-tests.md) | Which tests earn their cost, `severity` / `error_if` / `warn_if`, `where` and `store_failures`, where a test belongs, `dbt-utils` and `dbt_expectations` tests worth knowing, testing grain, referential direction, additivity, volume and timezone correctness, CI cost |
| [governance.md](governance.md) | Contracts and constraint enforcement per platform, model versions and `deprecation_date`, groups, `access`, and owners, and the order in which to adopt them |
| [exposures.md](exposures.md) | Declaring downstream consumers, and why a missing exposure is not evidence of no consumer |

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | What it decides |
|---|---|
| `naming.yaml_file_pattern` | What to name the file |
| `testing.primary_key` | Tests expected on a primary key |
| `testing.primary_key_incremental` | Override for incremental models |
| `testing.foreign_key` | Tests expected on a foreign key |
| `testing.foreign_key_severity` | `warn` or `error` on relationship tests |
| `naming.surrogate_key_column` | The key column's name |
| `naming.timestamp_column_suffix` | Suffix that declares a timezone |

**Absent field → generic guidance, labelled as generic.** With no `testing` object, recommend `unique` + `not_null` on the primary key and `not_null` on foreign keys, and state that this is the generic default rather than the project's declared policy.

## File naming and placement

Read `naming.yaml_file_pattern`. A common shape is `_<model_name>.yml`, with the leading underscore sorting the YAML above the `.sql` file in a directory listing.

With no contract, look at what the project already does:

```bash
ls models/<some_existing_directory>/
```

Match it. Do not introduce a second convention — a project with two YAML naming schemes has two places to look for every model.

Put the file **in the same directory as the model**. One YAML file per model is the maintainable arrangement: a shared `schema.yml` covering twenty models becomes a merge-conflict magnet and makes it impossible to tell at a glance whether a model is documented.

## Descriptions: entity and grain, nothing else

A description has one job — tell the reader **what one row is**. That is it.

```yaml
version: 2

models:
  - name: daily_orders_by_region
    description: "Daily order counts and revenue by region and product. One row per date, region, and product."
```

The formula that works: **`<what the rows are>` + `<one row per ...>`**. For a periodic model, include the period. For a source-facing model, name the system the data comes from.

### Why not "This model..."

```yaml
# bad
description: "This model contains order data."
description: "A table that stores daily metrics."
description: "This dbt model transforms and aggregates raw data into a clean format."
description: "Order data."
```

Three concrete reasons, not stylistic preference:

1. **The description appears next to the model name.** The reader already knows it is a model. "This model contains" spends the first four words of a tooltip restating the context.
2. **BI tools render it as a field label or hover text.** A business user sees "This model contains order data" while looking at a field called Order Data. It answers nothing.
3. **The opener displaces the grain.** Descriptions get truncated in every UI that shows them. Whatever occupies the first clause is what survives — make it the entity, not the preamble.

The fourth example fails differently: it is short but says nothing. It names the entity and omits the grain, which is the half that matters.

### Why not implementation

```yaml
# bad — describes how, not what
description: "Joins the orders staging model with the customer dimension and aggregates by date."
```

- It goes stale the moment the SQL is refactored, and nothing checks it.
- It is already visible: the reader can see the `ref()` calls and the lineage graph.
- A consumer choosing between two models needs to know what each contains, not how each was built.

### Inline strings, not folded scalars

```yaml
# good
description: "Daily order counts and revenue by region. One row per date and region."
description: Daily order counts and revenue by region. One row per date and region.

# avoid
description: >
  Daily order counts and revenue by region.
  One row per date and region.
```

`>` and `|` exist for genuinely multi-line content. A description should be one or two sentences, which fits on a line. The folded form invites the multi-paragraph essay that nobody reads, indentation errors that silently change the string, and inconsistency between files. Anything genuinely longer belongs in a `docs` block or `meta`.

### Length

One to two sentences. Longer descriptions are not read; they are scrolled past. If there is more to say — caveats, known gaps, owner, refresh expectations — use `meta:` or a `{% docs %}` block, both of which are structured and can be surfaced selectively.

## Column documentation

Document every column. An undocumented column is one the next person has to reverse-engineer from SQL, and it is what a BI user sees as a bare snake_case name with no tooltip.

A description earns its place by adding what the name cannot:

| Column role | State | Example |
|---|---|---|
| Surrogate key | Which columns it hashes | `"Surrogate key over order_date, region, product_id."` |
| Natural / primary key | What the entity is | `"Unique identifier for the customer, from the source system."` |
| Foreign key | Which model it points at | `"Foreign key to the customer dimension."` |
| Measure | Unit and period | `"Total order revenue in USD for the date."` |
| Rate / ratio | Numerator, denominator, scale | `"Orders divided by sessions, as a decimal. 0.05 = 5%."` |
| Boolean | The exact condition | `"True when the order has shipped and has not been returned."` |
| Categorical | The full value set | `"Order status: pending, shipped, delivered, cancelled."` |
| Timestamp / date | The timezone | `"Order placement time in UTC."` |

Restating the name adds nothing:

```yaml
# bad
- name: customer_name
  description: "The name of the customer."

# good
- name: customer_name
  description: "Customer display name from the source CRM. Not unique."
```

The second version tells a reader two things the column name does not: where it comes from, and that they cannot group by it safely.

## Test selection by column role

Tests are not a coverage target. Each one should encode a specific claim that, if violated, means the model is wrong.

Before writing any test, check two spellings against the project:

- **`tests:` or `data_tests:`.** The key was renamed to disambiguate data tests from unit tests. Both work, but a single resource cannot mix them. Use whichever the project already uses — the examples below use `tests:` and translate directly.
- **Where the arguments go.** Newer dbt versions expect a generic test's arguments under an `arguments:` key and warn when they are top-level; older versions do not recognise `arguments:` at all. Framework settings — `severity`, `where`, `store_failures`, `tags` — always belong under `config:`. Getting this wrong is the usual cause of a test that parses with no arguments, or an error naming the framework rather than your YAML.

[data-tests.md](data-tests.md) has the full treatment: which tests are worth their compute, severity thresholds, bounding an expensive test, `store_failures`, package tests, and the assertions most projects never make.

### Primary key

Read `testing.primary_key`, or `testing.primary_key_incremental` when the model is incremental.

```yaml
columns:
  - name: <naming.surrogate_key_column>
    description: "Surrogate key over order_date, region, product_id."
    tests:
      - unique
      - not_null
```

This is the one test set that is never optional. Without it, nothing in the project asserts the model's grain, and the grain is the model's central claim.

**Why incremental models often get a different set.** `unique` scans the whole table on every run. On a large, long-history incremental model that runs frequently, the test can cost more than the model does, and teams respond by deleting it — which is worse than a cheaper version of it. The usual answer is a bounded variant that checks only the recently-written window and falls back to a full scan on a full refresh. `testing.primary_key_incremental` names whatever the project uses for this. With no contract field, use `unique` + `not_null` and flag the cost if the table is large; do not invent a custom test name.

There is an important exception: **if the incremental model rewrites historical rows** — a dimension-style incremental with updates that can touch any row regardless of date — a recency-bounded test cannot catch a duplicate introduced in old data. Those models need the full-table test.

### Foreign key

Read `testing.foreign_key` and `testing.foreign_key_severity`.

```yaml
  - name: customer_id
    description: "Foreign key to the customer dimension."
    tests:
      - not_null
      - relationships:
          to: ref('<dimension_model>')
          field: customer_id
          config:
            severity: warn
```

Severity is a genuine judgment call, which is why it is in the contract rather than fixed here. `error` on a relationship test blocks the pipeline when a dimension is late — and referential gaps in analytics data are frequently a normal, temporary condition rather than a defect. `warn` surfaces the gap without stopping the build. If `foreign_key_severity` is absent, `warn` is the safer default for a relationship test, and `error` is right for `not_null` on a key the model cannot function without.

### Categorical columns

```yaml
  - name: order_status
    description: "Order status: pending, shipped, delivered, cancelled."
    tests:
      - not_null
      - accepted_values:
          values: ['pending', 'shipped', 'delivered', 'cancelled']
```

`accepted_values` is the test most likely to catch a real upstream change, because a new enum value appearing in a source is common and otherwise invisible. It is also the test most likely to fail for a legitimate reason. Keep the description's value list and the test's list in sync — when they disagree, both are untrustworthy.

### Measures

```yaml
  - name: total_amount
    description: "Total order revenue in USD for the date."
    tests:
      - dbt_expectations.expect_column_values_to_be_between:
          min_value: 0
          config:
            severity: warn
```

Bound a measure only where the bound is genuinely a business rule. "Revenue is never negative" is a real claim in most businesses and a false one where refunds are netted — check before asserting it. A bound chosen arbitrarily produces an alert that gets muted, which is worse than no test.

### What not to test

- **Do not add `not_null` to a column that is legitimately nullable.** The test will fail, someone will set it to `warn`, and the warning will be ignored along with the real ones.
- **Do not test the same claim twice** in two packages' syntaxes. Pick the project's convention.
- **Do not test upstream problems downstream.** If a source has duplicates, test the source-facing model, where the failure names the actual cause.
- **Do not repeat a claim the upstream model already asserts.** Uniqueness tested again in a downstream model that neither joins, unions, deduplicates, nor changes the grain is an extra scan that can only fail if the upstream test did. The exception matters: **any model that does change the grain has a new claim to make**, and testing it there is not a duplicate.
- **Do not bound a measure with a number chosen to make the test pass.** It asserts nothing and will be believed.

## Model contracts and constraints

A **contract** makes a model's schema enforced at build time. dbt verifies that the model's output has exactly the declared columns with the declared types, and fails the build if it does not.

```yaml
models:
  - name: daily_orders_by_region
    description: "Daily order counts and revenue by region and product. One row per date, region, and product."
    config:
      contract:
        enforced: true
    columns:
      - name: order_date
        data_type: date
        description: "Order date in UTC."
        constraints:
          - type: not_null
      - name: region
        data_type: varchar
        description: "Sales region name."
        constraints:
          - type: not_null
      - name: total_amount
        data_type: decimal(38,6)
        description: "Total order revenue in USD for the date."
```

### Contracts versus tests

They fail at different times, and that is the whole value.

| | Enforced at | Catches |
|---|---|---|
| Contract | Build time, before data is written | Wrong type, missing column, extra column |
| Test | After the model is built | Wrong values in a correct schema |

A contract turns "a consumer's dashboard broke because a column changed type" into "the build failed with a clear message." That is a strictly better failure.

### What `enforced: true` requires

- **Every column must be listed with a `data_type`.** Omit one and the build fails. This is the cost, and it is real: the YAML now has to be maintained in lockstep with the SQL.
- **The type strings are dialect-specific.** `varchar`, `string`, `text`, `decimal(38,6)`, `numeric`, `timestamp`, `timestamp_ntz` are not uniformly available. Check `project.warehouse` and use what that adapter accepts.
- **Type mismatches fail hard.** A column the SQL produces as an integer, declared as `varchar`, fails the build. Usually the SQL is what should change — declaring the type you wanted and casting to it is how the contract earns its keep.

### Constraints

`constraints` sit alongside `data_type` and are enforced by the warehouse, so **support varies substantially by platform**. `not_null` is widely enforced. `primary_key`, `unique`, `foreign_key`, and `check` are on some platforms informational metadata only — declared, visible, and not actually verified. dbt's own documentation classifies each constraint per adapter into enforced, definable-but-not-enforced, and not definable; [governance.md](governance.md) has the comparison and the two flags that suppress the resulting warnings.

This distinction matters: a `primary_key` constraint that the warehouse does not enforce provides documentation, not a guarantee. **Keep the `unique` test even when a `primary_key` constraint is declared** unless you have confirmed your platform enforces it. Check the adapter's documentation for `project.warehouse` rather than assuming. Some engines will additionally *trust* an unenforced key for query rewriting, which means a false declaration can produce wrong results rather than merely undetected ones.

### When to enforce a contract

Worth the maintenance cost:

- Consumer-facing models that BI tools or external systems read
- Any model whose schema other teams depend on
- Models with a versioned interface

Usually not worth it:

- Source-facing and transformation models under active development, where the schema changes weekly and every change means two edits
- Models with one consumer inside the same project

Enforce it at the boundary, not everywhere. Contracts also apply only to `table` and `incremental` materializations — they are silently not applied to a view.

## Versions, deprecation, and access

Three related features, all optional, all model-only, and all worth adopting later than descriptions and tests rather than earlier.

| Feature | Answers | Adopt when |
|---|---|---|
| `versions` + `latest_version` | "How do I break this schema without breaking consumers today?" | A genuinely breaking change to a contracted model with consumers outside your control |
| `deprecation_date` | "When does this stop existing?" | Any time you want a `ref()` to it to start warning — no other machinery required |
| `group` + `access` + `owner` | "Who owns this, and who is allowed to build on it?" | As soon as a layer exists that nothing outside it should `ref()` |

`access: private` is the only mechanism in dbt that mechanically prevents an unwanted dependency: a violating `ref()` fails at parse time rather than at review. Where a project has a convention that a layer is terminal, marking it private converts that convention into an enforced rule.

Two things that surprise people: `deprecation_date` does **not** drop the relation — a deprecated model keeps building and keeps costing — and a model with an enforced contract **cannot be deleted** before its deprecation date has passed.

See [governance.md](governance.md) for the mechanics: how versions are defined as diffs from a shared column list, prereleases, `alias` for keeping an old relation name, and the adoption order. Removing a model afterwards is `dbt-breaking-changes`.

## Sources and freshness

Source YAML is `dbt-sources-and-seeds`. Two points belong here because they are YAML-authoring decisions that get made wrong:

**`loaded_at_field` versus warehouse metadata.** With a `loaded_at_field`, dbt runs a `max()` over that column and measures the freshness of the *data*. Without one, on adapters that support it, dbt uses warehouse metadata about when the table was last modified — which is cheaper and measures the freshness of the *write*. These differ, and the difference is the failure: a load that ran on schedule and inserted yesterday's rows is fresh by metadata and stale by data. Prefer a `loaded_at_field` on anything whose timeliness matters, and prefer a column recording **when the warehouse received the row**, not when the business event happened — an event timestamp makes freshness a measure of business activity, so a quiet weekend reads as a broken pipeline.

**Freshness is not completeness.** A run that succeeded and inserted zero rows passes every freshness check. Pair freshness with a row-count or volume assertion; see [data-tests.md](data-tests.md).

## Unit tests

A unit test checks the model's **logic** against fixed input rows. It runs without touching warehouse data, which makes it the only way to test a branch that current production data does not exercise.

```yaml
unit_tests:
  - name: revenue_treats_null_amount_as_zero
    model: daily_orders_by_region
    given:
      - input: ref('<upstream_model>')
        rows:
          - {order_date: '2024-01-01', region: 'north', product_id: 'p1', amount: null}
          - {order_date: '2024-01-01', region: 'north', product_id: 'p1', amount: 100.00}
    expect:
      rows:
        - {order_date: '2024-01-01', region: 'north', product_id: 'p1', total_amount: 100.00}
```

Use them where the logic is conditional and the failure would be plausible: `case` expressions with several branches, `coalesce` chains, date-boundary arithmetic, division guarded against zero, currency or unit conversion, deduplication tie-breaking.

Do not use them as a substitute for schema tests. A unit test proves the transformation is right for the rows you supplied; a schema test proves the actual data satisfies the model's claims. They answer different questions.

Two practical notes: name the test after the behavior it pins (`revenue_treats_null_amount_as_zero`, not `test_1`), and supply only the columns the assertion needs — an over-specified fixture breaks every time an unrelated column is added.

The full treatment — fixture formats, mocking a source, testing incremental logic, and what unit tests cannot cover — is in `dbt-unit-tests`.

## Exposures

A model's YAML says what the model is. An exposure says who consumes it — and it is the only record of an external consumer that lives in the repository. Six skills in this library ask "who reads this?" before permitting a change, and every one of them degrades to guesswork when the answer was never written down.

Read `bi.use_exposures` from the contract before deciding whether a missing exposure is an omission or simply not this project's practice.

See [exposures.md](exposures.md) for the fields that matter, how to write a description that is worth having, the one command that proves the exposure landed in the graph, and why a missing exposure is never evidence that nothing consumes a model.

## Full example

```yaml
version: 2

models:
  - name: daily_orders_by_region
    description: "Daily order counts and revenue by region and product. One row per date, region, and product."
    config:
      contract:
        enforced: true
    columns:
      - name: <naming.surrogate_key_column>
        data_type: varchar
        description: "Surrogate key over order_date, region, product_id."
        constraints:
          - type: not_null
        tests:
          - unique
          - not_null

      - name: order_date
        data_type: date
        description: "Order date in UTC."
        constraints:
          - type: not_null
        tests:
          - not_null

      - name: region
        data_type: varchar
        description: "Sales region name, from the region dimension."
        tests:
          - not_null

      - name: product_id
        data_type: varchar
        description: "Foreign key to the product dimension."
        tests:
          - not_null
          - relationships:
              to: ref('<product_dimension>')
              field: product_id
              config:
                severity: warn

      - name: order_status
        data_type: varchar
        description: "Order status: pending, shipped, delivered, cancelled."
        tests:
          - accepted_values:
              values: ['pending', 'shipped', 'delivered', 'cancelled']

      - name: order_count
        data_type: integer
        description: "Distinct orders placed on the date for this region and product."

      - name: total_amount
        data_type: decimal(38,6)
        description: "Total order revenue in USD for the date."
        tests:
          - dbt_expectations.expect_column_values_to_be_between:
              min_value: 0
              config:
                severity: warn

      - name: loaded_at
        data_type: timestamp
        description: "Time this row was last written by dbt, in UTC."
```

Replace `<naming.surrogate_key_column>` and the referenced model names with the project's actual values. Type names must be valid for `project.warehouse`.

## Verify

```bash
dbt parse                      # the YAML is valid and refs resolve
dbt build --select <model>     # the model builds and its tests pass
```

`dbt parse` catches malformed YAML, a misnamed column, and a test pointing at a column that does not exist — all of which are easy to write and invisible on inspection. A test that passes because it silently applies to nothing is worse than no test.

See AGENTS.md for the rules this skill assumes rather than restates.

## Completion checklist

- [ ] File named per `naming.yaml_file_pattern`, in the same directory as the model
- [ ] Model description states entity and grain, inline string, no "This model..." opener
- [ ] Description says what the model contains, not how it is built
- [ ] Every column documented; no description that restates its column name
- [ ] Timezone stated on every timestamp and date column
- [ ] Categorical descriptions list the full value set, matching the `accepted_values` test
- [ ] `tests:` versus `data_tests:` matches the project; argument syntax matches its dbt version
- [ ] Primary key tested per `testing.primary_key` (or the incremental override)
- [ ] Incremental models that rewrite history use full-table PK tests, not recency-bounded ones
- [ ] Foreign keys tested per `testing.foreign_key` with the contract's severity
- [ ] No `not_null` on a legitimately nullable column
- [ ] No claim tested twice across layers; grain re-tested wherever the grain changes
- [ ] Grain, referential direction, additivity, and volume each asserted or consciously skipped
- [ ] Tests named where a generated name would be unreadable in a failure log
- [ ] Contract decision made deliberately; if enforced, every column has a dialect-valid `data_type`
- [ ] `unique` test retained alongside any unenforced `primary_key` constraint
- [ ] A new version created only for a genuinely breaking change; `deprecation_date` set where one is planned
- [ ] `group`, `access`, and `owner` set where a layer should not be built on
- [ ] Source freshness uses a warehouse-arrival `loaded_at_field`, not a business-event timestamp
- [ ] Unit tests added for conditional logic, named after the behavior they pin
- [ ] `dbt parse` clean; `dbt build` passes

## The failure modes that recur

1. **A test that silently applies to nothing.** The column name in the YAML does not match the model's output — a typo, or a column that was renamed in the SQL. The test passes because it has no rows to check. `dbt parse` catches this; nothing else will.
2. **A description written for the docs site and read in a dashboard.** "This model contains..." looks acceptable in the dbt docs UI and useless as a BI tooltip, which is where most people encounter it.
3. **A relationship test set to `error` on a dimension that loads later.** The pipeline fails nightly for a reason that is not a defect. Someone sets it to `warn`, then stops reading warnings, and a real referential break goes unnoticed. Bounding the test with `relationships_where` keeps it at `error` and is almost always the better answer.
4. **An enforced contract on a model still being developed.** Every SQL change now requires a matching YAML change, the friction gets noticed, and the contract is removed — including from the models that needed it.
5. **A `primary_key` constraint trusted as a uniqueness guarantee.** On platforms that treat it as metadata, nothing is enforced. The `unique` test was deleted as redundant, and duplicates arrive unannounced.
6. **Documentation drift after a grain change.** The SQL was updated, the description still claims the old grain, and every consumer reading the description is now misinformed with no failing test anywhere.
7. **Test arguments written in the wrong syntax for the project's dbt version.** The test parses without its arguments and passes vacuously, or fails with an error that points at the framework rather than the YAML.
8. **Freshness treated as completeness.** A run that inserted zero rows passes every freshness check. The source is fresh and empty.
9. **A deprecated model assumed to have stopped building.** It builds every run at full cost, and reports no problem.
10. **The same claim tested at four layers.** Four scans, one possible cause, and a failure list naming three models that are not responsible.
