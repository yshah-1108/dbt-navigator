# Source-facing (staging) models

The layer that reads `source()`. Read `layers[]` from the contract for this layer's actual name, prefix, path, and materialization; the guidance below is about what belongs inside the file. With no contract, treat "the layer that reads `source()`" as this layer and say you are applying generic guidance.

## What this layer is for

One staging model per source table, mapping 1:1. Its job is to make the raw table safe to use and boring to read:

- rename columns to project vocabulary
- cast every column to its intended type, **once** — this is the only layer that casts for repair
- drop rows that should never be visible downstream (soft deletes, null primary key)
- normalize booleans and metadata columns

## What does not belong here

- **Joins.** A staging model that joins is doing transformation work. Move it downstream.
- **Aggregation.** Same reason.
- **Business logic.** Category mapping, exclusion rules, and derived flags belong in the transformation layer, where they are visible as business decisions rather than buried in a "cleanup" model.
- **Filtering that a consumer might not want.** Removing soft-deleted rows is safe. Removing "test accounts" is a business decision — do it downstream, or document it loudly.

The discipline is worth holding: once a staging model contains logic, every consumer inherits that logic invisibly, and nobody can use the source table without it.

## Prerequisite: the source must be declared

The source table needs a definition before a staging model can reference it. See `dbt-sources-and-seeds`. File naming for source YAML follows the project's own convention — check existing files rather than assuming.

## Scaffolding

The `codegen` package writes the column list for you:

```bash
dbt run-operation generate_base_model --args '{"source_name": "<source>", "table_name": "<table>"}'
```

Treat the output as a draft. It gives you every column with no casts, no renames, and no structure. You still have to decide types, names, and which columns to carry.

## Shape

```sql
with

source as (
    select * from {{ source('<source>', '<table>') }}
),

renamed as (
    select
        -- primary key
        cast(id as varchar) as order_id,

        -- foreign keys: identifiers cast to a string type
        cast(cust_id as varchar) as customer_id,

        -- descriptive attributes
        cast(status as varchar) as order_status,

        -- counts
        cast(qty as integer) as quantity,

        -- money: fixed-precision decimal, never float
        cast(amt as decimal(38, 6)) as amount,

        -- timestamps and dates, with the contract's suffix if it sets one
        cast(created as timestamp) as created_at,
        cast(order_dt as date) as order_date,

        -- booleans: name them is_ / has_ and make them genuinely boolean
        coalesce(deleted_flag, false) as is_deleted,

        -- load metadata, normalized to one project-wide name
        cast(_synced_at as timestamp) as loaded_at
    from source
    where id is not null
),

final as (
    select
        order_id,
        customer_id,
        order_status,
        quantity,
        amount,
        created_at,
        order_date,
        is_deleted,
        loaded_at
    from renamed
)

select * from final
```

`select *` in the `source` CTE is the one acceptable use of it in an import position: the very next CTE pins every column explicitly, so nothing is actually inherited implicitly. The `final` CTE still lists columns.

Exact type names depend on `project.warehouse`. `decimal(38, 6)`, `timestamp`, `varchar`, and `integer` are widely but not universally spelled that way — use what the target dialect accepts.

## Casting is the layer's main job, and it is where it goes wrong

Because this is the only layer that casts for repair, every casting hazard lands here. The full treatment is in [`nulls-and-types.md`](nulls-and-types.md); the four that matter most in a staging model:

- **A cast can fail on one row in ten million.** The column was always numeric until somebody typed `'N/A'`. A non-throwing cast (`try_cast` on Snowflake and Databricks, `safe_cast` on BigQuery, no equivalent on Postgres or Redshift) turns the failure into a null — which means the build goes green and the data loss becomes invisible. If you use one, pair it with a test that counts the nulls.
- **Empty string is not null**, and sources use empty string, whitespace, and null interchangeably for "missing". Normalise all three to null here, or every downstream `is null` check is wrong for a third of the rows.
- **A bare string-to-date cast is locale-dependent.** `'01/02/2024'` is two different dates. Use the engine's explicit format-parsing function with the format stated.
- **Normalise string join keys into their own column** — `lower(trim(<col>)) as <col>_key` — keeping the original for display. Doing it here once means no downstream model has to normalise inside an `on` clause, which would hide the transformation and prevent the engine pruning on the column.

Also worth doing here rather than downstream: extracting fields out of semi-structured columns. Repeating a JSON path extraction in five consumers means five places to fix when the payload shape changes, and the extraction syntax is one of the least portable parts of any dialect.

## Renaming conventions

| Raw pattern | Staging output | Note |
|---|---|---|
| `ID`, `id` | `<entity>_id` | Prefix with the entity so it is unambiguous after a join |
| `CUST_ID`, `custid` | `customer_id` | Spell out abbreviations |
| `CREATED`, `CREATED_AT` | `created_at` + contract suffix | Suffix declares the timezone |
| `DELETED`, `DEL_FLAG` | `is_deleted` | Boolean prefix, boolean type |
| `AMT`, `AMOUNT` | `amount` or `<metric>_amount` | Unit belongs in the description, not the name, unless ambiguous |
| Loader metadata | one project-wide name | Pick one, e.g. `loaded_at`, and use it in every staging model |

Rename toward project vocabulary, not source vocabulary. The whole point of this layer is that downstream models stop caring what the source called things.

## Soft deletes and the guard clause

Most replicated sources mark deletes rather than removing rows, often with more than one flag — one from the replication tool and one from the application. Filter on all of them:

```sql
where not coalesce(replication_deleted, false)
    and not coalesce(app_deleted, false)
```

Two traps:

- **`where flag = false` drops nulls.** A null flag is not `false`, so the row disappears. Use `not coalesce(flag, false)`.
- **A soft-delete filter added later removes rows from history.** If this staging model already feeds an incremental model built with `merge`, previously-loaded rows for now-deleted keys stay in the target forever. See `dbt-incremental-models`.

Before writing the filter, confirm which flags exist and how many rows each one excludes. A flag that excludes nothing is probably the wrong column; one that excludes almost everything is probably inverted.

```sql
select count(*)                                          as total,
       sum(case when <flag_a> then 1 else 0 end)         as flagged_a,
       sum(case when <flag_b> then 1 else 0 end)         as flagged_b
from <raw_relation>
```

## Nulls on the primary key

`where <pk> is not null` in the `renamed` CTE is worth having by default. A null primary key cannot be joined, cannot be tested for uniqueness meaningfully, and usually indicates a partially-written source row.

If the filter removes a meaningful number of rows, that is a finding to report, not a detail to bury.

## Materialization

Read `layers[].materialization`. A view is the usual choice: staging does no expensive work, and a view means downstream models always see current source data with no build step.

Make it a table only when there is a measured reason — an unusually expensive cast or a semi-structured extraction that many models repeat. Say what the measurement was.

## Checklist

- [ ] Source declared before the model was written
- [ ] Exactly one `source()` call; no `ref()`
- [ ] No joins, no aggregation, no business logic
- [ ] Every column cast explicitly, with dialect-valid type names
- [ ] Identifiers cast to a string type; money to fixed-precision decimal
- [ ] Non-throwing casts, if used, paired with a test that counts the resulting nulls
- [ ] Empty strings and whitespace-only values normalised to null
- [ ] String-to-date parsing uses an explicit format, not a bare cast
- [ ] String join keys normalised into their own column, original retained
- [ ] Timestamp/date columns carry the contract's suffix, and one representation used project-wide
- [ ] Booleans named `is_`/`has_` and null-safe
- [ ] Soft-delete flags handled null-safely
- [ ] Primary key null filter present
- [ ] `final` lists every column; file ends `select * from final`
- [ ] Compiled and built; row count compared against the raw table and the difference explained
