---
name: dbt-sources-and-seeds
description: Use when defining a new source, adding or tuning freshness thresholds, choosing a loaded_at_field, deciding between source-level and table-level config, debugging a freshness failure, or deciding whether reference data belongs in a seed. Covers why a seed is not a substitute for a source.
metadata:
  phase: build
---

# Sources and seeds

Both are entry points to the DAG. A **source** declares a table someone else loads. A **seed** is a CSV in the repository that dbt loads itself. They are not interchangeable, and the most common mistake here is using a seed where a source belongs.

| Sub-document | Read it when |
|---|---|
| [freshness.md](freshness.md) | You are configuring, choosing a `loaded_at_field` for, setting thresholds on, or debugging source freshness |

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

Relevant field: `naming.yaml_file_pattern`, for what to call the source YAML file. Absent → match the pattern already used in the project's staging directories and say you are following observed convention. Never invent a file-naming scheme.

## Sources

A source definition gives raw tables a stable name so models never hardcode a physical path, records where the data comes from, and enables freshness monitoring. Only the third requires thought.

```yaml
version: 2

sources:
  - name: <source_name>
    database: <database>
    schema: <schema>
    description: "<which system produces this, how it arrives, on what cadence>"
    tables:
      - name: <table_name>
        description: "<what one row represents>"
```

Group tables by the system that produces them, not by the models that consume them — a source is a fact about upstream, and consumption changes. Point the description at what a reader cannot discover from the warehouse: who owns this, how it is loaded, how often. That is what someone debugging stale data at 7am needs.

### Where the config keys live changed across versions

This is the most common reason a correct-looking source YAML does nothing. `freshness`, `loaded_at_field`, `meta` and `tags` moved under a `config:` block over the 1.9–1.10 releases, and older top-level placements were accepted for a period before being deprecated.

| Key | Placement |
|---|---|
| `freshness` | Moved under `config:` in 1.9 |
| `loaded_at_field` | Moved under `config:` in 1.10 |
| `meta`, `tags` | Moved under `config:` in 1.10 |
| `database`, `schema`, `identifier`, `quoting`, `description`, `loader` | Remain top-level properties, not configs |
| `overrides` | Deprecated in 1.10 |

**Match the placement already used in the project rather than the newest form**, and if a freshness block appears to be ignored, check the placement against the project's dbt version before suspecting the thresholds. Mixed placements within one file are the case most likely to leave a table silently unmonitored.

Source properties cannot be set in `dbt_project.yml` the way model properties can, with one exception worth knowing: source **configs** (the contents of `config:`) can be defaulted project-wide under a `sources:` key, which is a reasonable way to give every source a floor threshold.

### Naming the physical location

Four properties decouple the name you use in code from the name the warehouse has. Using them is how a rename upstream becomes a one-line change.

| Property | Purpose |
|---|---|
| `database` | The database or project the tables live in |
| `schema` | The schema or dataset. Defaults to the source `name` when omitted |
| `identifier` | The real table name, when it differs from the `name` you want to write in code |
| `loader` | Free text naming the tool that loads it. Informational only, and worth filling in |

`schema` defaulting to the source name is the trap: a source named for the business system rather than the schema resolves to a schema that does not exist, and the error names a relation nobody recognises. State `schema` explicitly whenever the two differ.

`identifier` is what makes a warehouse-side rename cheap, and it is also the correct answer to an upstream table with an awkward name — a mixed-case or reserved-word table gets a clean `name` in code and its real name in `identifier`.

### Quoting

```yaml
sources:
  - name: <source_name>
    quoting:
      database: false
      schema: false
      identifier: false
    tables:
      - name: <table_name>
        quoting:
          identifier: true    # this one table needs quoting
```

Quoting resolves whether `{{ source() }}` emits `"Database"."Schema"."Table"` or bare identifiers, and table-level settings override source-level ones. Defaults vary by adapter — for most, quoting is on — so this is one to verify against the compiled SQL rather than assume.

The failure it fixes: on a warehouse that folds unquoted identifiers to a single case, a table created with mixed-case quoting can only be selected from with quotes. Unquoted, the query reports the relation does not exist even though it is visibly there. If a source resolves to a name that looks right and the warehouse denies it exists, quoting is the first thing to check. Note that on BigQuery these keys are still named `database` and `schema` while applying to project and dataset.

### External tables

A source table can carry an `external:` dictionary describing a location, file format and partitions rather than a warehouse-managed table.

```yaml
    tables:
      - name: <table_name>
        external:
          location: "<stage or path>"
          file_format: "<format>"
          partitions:
            - name: <partition_column>
              data_type: date
        columns:
          - name: <column>
            data_type: <type>
```

Two things to be clear about. First, `external:` in dbt itself is **just metadata** — an extensible dictionary written into the manifest. dbt does not create or refresh anything from it. Second, the machinery that turns it into DDL comes from the dbt-external-tables package, invoked as an operation (`dbt run-operation stage_external_sources`) and run **before** the models that read those tables.

The failure mode: writing an `external:` block, seeing it parse cleanly, and concluding the external table exists. Nothing created it. Confirm the package is installed and that the staging operation is a step in the schedule, or the source is a definition of a table that is not there.

### Freshness

Freshness monitoring answers one question — did anything arrive recently — and nothing else; it says nothing about volume, completeness, or correctness. It is calculated one of two ways: a column-based `loaded_at_field` that measures the data, or metadata-based (the field omitted) that measures the warehouse object and can report a rarely-loaded table as fresh for the wrong reason. Getting a freshness config right means choosing a real load-time column (never an event-time one), casting it where it is a date or a non-UTC timestamp, deriving thresholds from the observed gap distribution rather than the intended cadence, and deciding deliberately where `dbt source freshness` sits in the job given that it exits non-zero on staleness. The full mechanics, the threshold table, the running/debugging workflow, the `source_status:fresher+` selector, and the list of failures freshness structurally cannot catch are in [freshness.md](freshness.md).

A false freshness config is worse than an honest gap: if no load timestamp exists, configure none and document the actual cadence instead.

### Testing a source

Tests on source tables run against data you do not control, so a failure is a signal to the upstream owner, not a bug in your project. Worth having: `not_null` and `unique` on the key the downstream staging model relies on — a duplicate arriving upstream corrupts every model that joins on it, and catching it at the source is far cheaper than debugging a mart. Keep the count low; extensive testing belongs in staging, where you own the code.

Two decisions specific to source tests, both about who gets woken up:

- **Severity.** A source test at `error` severity blocks every downstream model when it fails, which is right for a key that would corrupt joins and wrong for a nullable attribute nobody depends on. `severity: warn` reports without blocking; use it where the finding is informational.
- **Ownership.** A source test that fails and has no upstream owner to route to becomes a permanently red check, and a permanently red check is functionally the same as no check. If nobody will act on it, do not add it.

Source tests are selectable independently, which is what makes a pre-build gate practical:

```bash
dbt test --select "source:*"                    # every source test
dbt test --select "source:<source_name>"        # one source
dbt build --exclude "source:*"                  # everything except source tests
```

## Seeds

A seed is a CSV in the repository that dbt loads with `dbt seed`. It is version-controlled data, which is its entire appeal and the source of every problem with it.

### When a seed is correct

All four must hold:

| Requirement | Why |
|---|---|
| **Small** — tens to low thousands of rows | The whole file is rewritten on every load and every diff |
| **Static** — changes rarely, by human decision | A changing seed makes the repository a database with no concurrency control |
| **Reference data** — a mapping, lookup, or hierarchy | This is what a seed is for |
| **Authoritatively owned by the repository** | If the real copy lives elsewhere, the seed is a stale fork |

Good fits: a code-to-label mapping, a threshold or tier table, a list of internal test accounts to exclude, a manual grouping with no system of record, a fiscal calendar or holiday list.

### When a seed is a mistake

| Anti-pattern | What goes wrong |
|---|---|
| **Anything that changes regularly** | Every update is a commit, review, and deploy. Someone eventually edits the table directly and the next `dbt seed` overwrites it. |
| **Anything large** | Load times grow, diffs become unreviewable, and the repository carries data it should not. |
| **Anything containing personal data** | Git history is effectively permanent and widely readable. Removing a row does not remove it from history. This is not a style preference. |
| **Data another system owns** | The seed silently diverges from the source of truth. Two answers, no signal. |
| **An export used to avoid building a pipeline** | Correct until the underlying data moves, then wrong with no error. |
| **A one-off fix for bad production data** | Becomes permanent and undocumented. Fix the model or the source. |

The clarifying question: **who edits this, and how often?** A human, rarely, deliberately → seed. A system, continuously → source. Anything between will end badly.

### Size, and the threshold that has a real consequence

There is no hard row limit, but three thresholds matter and one of them is exact:

| Threshold | What happens |
|---|---|
| A few thousand rows | Loading stays fast; a diff stays reviewable |
| Tens of thousands of rows | Loads get slow — dbt inserts the rows rather than bulk-loading them — and nobody reviews the diff any more |
| **1 MiB file size** | dbt stops hashing the contents for state comparison and compares only the **file path** |

That last one is exact and has a concrete consequence: **a seed at or above 1 MiB can be edited in place and `state:modified` will not select it.** A CI job built on state selection will pass without rebuilding it, and the change reaches production on whatever schedule happens to run a full seed. dbt raises a warning about this, which is easy to lose in a long log.

So 1 MiB is not a style guideline; it is the point past which a seed stops participating in change detection. A file approaching it is a file that should be a source table.

### Choosing between a seed and its alternatives

When reference data is needed and a seed feels wrong, these are the options, in rough order of how often they are the better answer:

| Alternative | Right when | Cost |
|---|---|---|
| **A source table**, loaded by the pipeline | The data has an owner outside the repository, or changes on any schedule | Requires asking for ingestion work |
| **A `case` expression or mapping in a staging model** | A handful of values, tightly coupled to one model's logic | Duplicated if a second model needs it |
| **A macro returning a list or dict** | The mapping drives generated SQL — a loop over categories | Invisible in the warehouse; not queryable or joinable |
| **A dbt-managed table built from `union all` literals** | Small, version-controlled, and you want type control without CSV inference | Verbose; the same maintenance burden as a seed with more ceremony |
| **An external table** via the dbt-external-tables package | The data is a file someone else drops in object storage | Extra package plus a staging operation in the schedule |
| **A seed** | The repository genuinely is the authoritative home, and a human edits it rarely | Everything below |

The macro option is the one most often overlooked. A mapping used only to generate SQL — not joined to — does not need to exist in the warehouse at all, and keeping it in a macro avoids a table nobody queries. Conversely, if anything joins to the values, it must be a relation, and the macro is the wrong shape. See `dbt-macros`.

### Column types

dbt infers types from the CSV, and inference is where seeds break quietly:

- A code column of digits with leading zeros is inferred as a number and the zeros are gone.
- A column that is all digits today is numeric; one non-numeric row later flips it to text and downstream casts fail.
- Date formats are inferred inconsistently across warehouses.
- An empty cell may become an empty string or a null depending on the adapter.

Declare types instead:

```yaml
seeds:
  - name: <seed_name>
    description: "<what one row represents, and who maintains it>"
    config:
      column_types:
        <code_column>: varchar(10)
        <effective_date>: date
        <amount>: numeric(12,2)
        <flag>: boolean
```

Declare types for every column whose interpretation matters — identifiers, codes, dates, decimals. The cost of an inferred type is a value that silently changes when someone adds a row.

The leading-zero case is worth spelling out because it is both the most common and the most damaging: a code column of digits is inferred numeric, the zeros disappear, and every join against a properly-typed code column matches nothing. It does not error. Preserve the zeros in the CSV **and** declare `varchar` of the right length; the CSV alone is not enough.

Related configs:

| Config | Purpose |
|---|---|
| `column_types` | Declare types explicitly. Do this |
| `quote_columns` | Quote column names — needed for headers containing a space or a reserved word |
| `delimiter` | Non-comma separator (1.7+) |
| `+enabled: false` | Retire a seed without deleting the file |
| `schema` | Land seeds in a dedicated schema, keeping them out of the modelled ones |

### `dbt seed` and `--full-refresh`

```bash
dbt seed --select <seed_name>
dbt seed --full-refresh --select <seed_name>
```

The distinction is worth understanding, because getting it wrong produces a confusing error:

| Command | What it does |
|---|---|
| `dbt seed` | Truncates the table and reinserts the rows. Deliberately avoids a `drop cascade`, so dependent objects survive |
| `dbt seed --full-refresh` | Drops the table (cascading) and recreates it |

Because a plain `dbt seed` only truncates and inserts, it **fails when the columns changed** — the table structure no longer matches, and the error names an invalid identifier, which reads like a typo rather than a structural mismatch. Any added, removed, renamed or retyped column needs `--full-refresh`.

The cascade is the reason `--full-refresh` is not the default: dropping the seed's table takes dependent views with it, which in a live environment is an outage. So the sequence for a column change is a full refresh followed by rebuilding what depended on it, not a full refresh alone.

One version note worth mentioning because it changes a table's shape: a trailing comma on a CSV row produced an extra empty column on 1.x, and does not on v2. A seed carrying a stray column of nulls is likely this, and it will disappear on upgrade — which is a silent schema change for anything selecting `*`.

### Seeds are not a substitute for a source

This is the mistake with the longest tail. A "temporary" seed standing in for real ingestion becomes permanent, and the failure mode is specific:

- **A seed cannot be stale, as far as dbt knows.** No `loaded_at_field`, no freshness check, no possible alert. A seed that last matched reality eight months ago reports no problem.
- **A source is monitored; a seed is trusted.** Choosing a seed opts out of the entire detection mechanism.
- **The staleness is invisible in the diff.** Nothing changed in the repository — which is the point, and the problem.
- **Correctness depends on someone remembering.** No schedule, no owner, no signal.

If the data has an authoritative home outside the repository, it belongs in a source, even when that means asking for a pipeline. A seed is right when the repository *is* the authoritative home. Where a temporary seed is genuinely unavoidable, write the intended replacement and a date into its description and add a test asserting whatever staleness proxy exists — a maximum effective date, an expected row count — so divergence has some chance of surfacing.

### Documenting and testing a seed

A seed takes the same YAML properties a model does, and the tests worth having are the ones that catch a bad hand-edit — which is the only way a seed goes wrong.

```yaml
seeds:
  - name: <seed_name>
    description: "<what one row represents, who maintains it, and how a change is decided>"
    columns:
      - name: <key_column>
        data_tests:
          - unique
          - not_null
```

`unique` and `not_null` on the key are the high-value pair: a duplicated mapping row silently fans out every join to it, turning one row into several, and no other check catches that. An `accepted_values` test on a status or category column catches a typo in a hand-added row. A row-count assertion catches a truncated paste.

Seeds accept pre- and post-hooks and participate in `on-run-start` / `on-run-end`, which occasionally matters for grants.

```bash
dbt run --select <seed_name>+     # rebuild everything downstream of a seed change
```

## Completion checklist

**Source**

- [ ] `database` and `schema` verified against the warehouse, not assumed — and `schema` stated explicitly wherever it differs from the source name
- [ ] `identifier` used rather than renaming anything, where the physical name is awkward
- [ ] Config keys nested at the placement this dbt version expects, matching the rest of the project
- [ ] Description names the owning system and the load cadence
- [ ] Freshness mechanism chosen deliberately: column-based for tables worth alerting on, metadata-based only where its object-level semantics are acceptable
- [ ] `loaded_at_field` is a load timestamp, not an event timestamp — verified with a query
- [ ] `loaded_at_field` cast where the column is a date, a string, or in a non-UTC zone
- [ ] Thresholds derived from observed gaps, accounting for the schedule and weekend behavior
- [ ] Both `warn_after` and `error_after` absent means no check runs — confirmed that is intended
- [ ] Generic threshold guidance labelled as generic where no contract policy exists
- [ ] Source-level config used where the loader is uniform; every table confirmed to share it
- [ ] Freshness omitted with a documented reason where no valid column exists
- [ ] `dbt source freshness` run and the result reported; its position in the job chosen deliberately given the non-zero exit
- [ ] A volume or completeness assertion exists for anything where an empty load would matter
- [ ] External-table sources: staging operation confirmed to be a scheduled step, not just YAML
- [ ] YAML file named per the contract, or the observed convention stated

**Seed**

- [ ] Small, static, reference data, authoritatively owned by the repository
- [ ] Well under 1 MiB, so state comparison still hashes its contents
- [ ] Alternatives considered explicitly — source table, staging expression, macro, external table
- [ ] Contains no personal data
- [ ] `column_types` declared for identifiers, codes, dates, and decimals
- [ ] Leading zeros preserved in the CSV **and** the column declared as text
- [ ] Description states what a row is, who maintains it, and how a change gets decided
- [ ] `unique` and `not_null` on the key; `accepted_values` on any categorical column
- [ ] `dbt seed` run and the resulting column types checked in the warehouse
- [ ] `--full-refresh` used after any column change, and dependents rebuilt after the cascade
- [ ] If it stands in for a real pipeline: replacement documented and a staleness proxy tested

## The failure modes that cost the most

1. **Event time used as `loaded_at_field`.** Alerts fire for healthy pipelines and stay silent for broken ones. The team mutes the check, and real staleness goes unnoticed for weeks.
2. **Thresholds set from the intended cadence.** Constant noise, then muted alerts, then no monitoring — while the config still claims coverage.
3. **Fresh but empty.** A load that succeeded with zero rows passes every freshness check, as does a load a tenth of its expected size. Detecting either needs a volume assertion, which freshness does not provide.
4. **A freshness block that never runs.** Neither threshold set, config nested at the placement a different dbt version expected, or metadata-based freshness on an adapter that does not support it. The YAML looks complete and nothing is checked.
5. **Metadata-based freshness on a rarely-loaded table.** The object's timestamp advanced for a reason unrelated to data, and the one table where staleness mattered most reported healthy.
6. **A timezone mismatch read as staleness.** The threshold gets widened by the offset, and genuine lateness is now invisible by the same amount.
7. **Source-level config across tables with different loaders.** Half alert falsely, half are unmonitored, and the YAML looks complete.
8. **A seed standing in for a source.** Never stale as far as dbt is concerned, therefore never alerted, therefore wrong for months. The most expensive mistake in this skill.
9. **A seed over 1 MiB, edited in place.** State comparison compares its path, not its contents, so CI passes without rebuilding it and the change lands whenever a full seed next happens to run.
10. **Inferred seed column types.** Leading zeros dropped from codes, or a type flipping when a row is added. Joins fail or silently match nothing.
11. **An `external:` block mistaken for an external table.** dbt stored the metadata and created nothing; the staging operation was never scheduled.
