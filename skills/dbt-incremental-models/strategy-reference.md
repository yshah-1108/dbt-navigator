# Strategy reference

Precise semantics of each `incremental_strategy`, what DML dbt actually generates, and which adapters support what. Read [SKILL.md](SKILL.md) first for the decision; this document is what you consult once you have to be exact.

Everything below is adapter- and version-dependent. **Verify against your own adapter's documentation and your own generated SQL** — `target/run/<project>/models/.../<model>.sql` holds the real statement — before relying on any of it. Where this document says "on adapter X", that is a statement about that adapter only.

## Support by adapter

Per dbt's incremental strategy documentation. Availability changes between adapter versions, so treat this as a starting point and confirm for the version in your `packages.lock`/environment.

| Adapter | `append` | `merge` | `delete+insert` | `insert_overwrite` | `microbatch` |
|---|---|---|---|---|---|
| Postgres | yes | yes | yes | — | yes |
| Redshift | yes | yes | yes | — | yes |
| Snowflake | yes | yes | yes | yes (see caveat) | yes |
| BigQuery | — | yes | — | yes | yes |
| Databricks | yes | yes | yes (Delta, newer versions) | yes | yes |
| Spark | yes | yes (Delta/Iceberg/Hudi) | — | yes | yes |
| Trino | yes | yes | yes | — | yes |
| Athena | yes | yes | — | yes | yes |
| Fabric, Teradata, DuckDB | yes | yes | yes | — | yes |

Two consequences worth stating plainly:

- **`delete+insert` does not exist on BigQuery or Spark.** The advice "use `delete+insert` when the source reprocesses" is not portable. On BigQuery the equivalent is `insert_overwrite` over the reprocessed partitions; on Databricks it is `replace_where`.
- **A strategy config that is invalid for the adapter fails at build time**, not at parse time, so a copied config can pass review and fail in the first run of a scheduled job.

### Defaults differ, and the default is not always `merge`

| Adapter | Default strategy |
|---|---|
| Snowflake, BigQuery, Databricks | `merge` |
| Redshift, Postgres | `append` when no `unique_key` is set; `delete+insert` when one is |
| Spark | `append` |

**Never leave `incremental_strategy` unset and assume you know what runs.** The same model file with the same `unique_key` performs an upsert on Snowflake and a delete-then-insert on Redshift. Both are defensible; they are not the same, and they differ in what happens to rows that vanish upstream.

## `merge`

The generated statement, in the default implementation:

```sql
merge into <target> as DBT_INTERNAL_DEST
using <temp_relation> as DBT_INTERNAL_SOURCE
    on (DBT_INTERNAL_SOURCE.<key> = DBT_INTERNAL_DEST.<key>)
       and (<incremental_predicates...>)
when matched then update set <columns> = DBT_INTERNAL_SOURCE.<columns>
when not matched then insert (<columns>) values (<columns>)
```

Properties that follow directly from that shape:

- **No `unique_key` means no match condition.** The default implementation substitutes `FALSE` for the join predicate and omits the `when matched` clause entirely, so every incoming row is inserted. `merge` without a key *is* `append`, with none of the protection its name implies. dbt-bigquery documents `unique_key` as required for `merge`; most other adapters silently degrade.
- **Nothing is ever deleted** (except on adapters offering an explicit not-matched-by-source action, below). A key absent from the incoming set is untouched.
- **Columns in the target that the model no longer selects keep their old values** on matched rows, because the update sets only the columns dbt knows about.
- **A duplicate key in the incoming data is a hard error on some warehouses and silent corruption on others.** Snowflake raises a non-deterministic-merge error; BigQuery raises `UPDATE/MERGE must match at most one source row for each target row`. That error is a *feature* — the alternative is an arbitrary winner. Note that dbt's own Snowflake documentation suggests resolving this error by switching to `delete+insert`; **treat that as a workaround, not a fix.** It resolves the error by removing the check, so the duplicates land and the model silently carries a broken grain from then on. Deduplicate the incoming set or correct the key instead. Switch strategies only when you have established the duplicates are legitimate and the grain is what you intend.
- **A null in any `unique_key` column breaks matching**, because `null = null` is not true. The row is never matched, so it is inserted again on every run. This is one of the most common causes of an incremental model that grows duplicates slowly. Either guarantee non-null keys, or use a surrogate key macro that hashes nulls to a stable placeholder.

### Restricting which columns an update touches

```sql
{{ config(
    incremental_strategy = 'merge',
    unique_key = 'order_id',
    merge_update_columns = ['order_status', 'updated_at'],
) }}
```

| Config | Effect |
|---|---|
| `merge_update_columns` | Only these columns are written by `when matched`. Everything else keeps its target value. |
| `merge_exclude_columns` | Everything except these is written. Useful for preserving a first-seen timestamp. |

- **The two are mutually exclusive.** Setting both raises a compiler error.
- They apply to `merge` only. With `delete+insert` or `insert_overwrite` they are silently ignored, because those strategies replace whole rows and have no update clause. A config that appears to protect a column and does not is worse than no config.
- `merge_exclude_columns` is matched case-insensitively against the target's columns in the default implementation; `merge_update_columns` is used as given. On a case-sensitive or quoted-identifier setup, a mis-cased name in `merge_update_columns` can produce a column that is never updated with no error. Check the generated `update set` list.
- The failure mode to name: **a column added to the model but not to `merge_update_columns` is populated on insert and frozen forever on update.** New rows have it, existing rows never gain it, and both look plausible.

### Adapter-specific merge clauses

Some adapters expose the full `merge` grammar. This is where `merge` stops being "never deletes".

On dbt-databricks (1.9 and later), and only there: `target_alias`, `source_alias`, `matched_condition`, `not_matched_condition`, `not_matched_by_source_condition`, `not_matched_by_source_action`, `skip_matched_step`, `skip_not_matched_step`, `merge_with_schema_evolution`.

```sql
-- dbt-databricks only. Do not port this to another adapter.
{{ config(
    incremental_strategy = 'merge',
    unique_key = 'order_id',
    target_alias = 't',
    source_alias = 's',
    matched_condition = 't.source_updated_at < s.source_updated_at',
    not_matched_by_source_action = "update set t.is_deleted = true, t.deleted_at = current_timestamp()",
) }}
```

Two patterns this unlocks, both otherwise awkward:

- **Out-of-order protection.** `matched_condition` comparing the source's update timestamp to the target's makes the merge refuse to overwrite a newer row with an older one. Without it, a late-arriving *older* version of a row silently wins.
- **Tombstoning instead of deleting.** `not_matched_by_source_action` can mark rows absent from the source as deleted rather than removing them, which keeps the history queryable and lets downstream models decide.

Both require the predicate to be safe against a partial source. `not_matched_by_source` evaluates against everything the predicate reaches, so if the incoming batch covers one day and the condition does not restrict to that day, the whole table is "not matched by source" and gets actioned.

## `delete+insert`

Two statements in one transaction, in the default implementation:

```sql
delete from <target>
where (<key_columns>) in (
    select distinct <key_columns> from <temp_relation>
)
  and <incremental_predicates...>;

insert into <target> (<columns>)
    select <columns> from <temp_relation>;
```

- **The delete is driven by keys present in the incoming data, intersected with the predicates.** A key that vanished from the source and is not in the incoming set is not deleted by key matching. The predicate is what makes the window match: a predicate covering the reprocessed date range deletes everything in that range, so vanished rows inside it do go away.
- **Whole rows are replaced.** There is no partial update, and `merge_update_columns` does not apply.
- **Duplicate keys in the incoming data are accepted silently.** The delete de-duplicates the key list; the insert does not de-duplicate rows. Where `merge` on Snowflake or BigQuery would raise an error, `delete+insert` inserts both copies. Choosing `delete+insert` to escape a merge error converts a loud failure into a quiet one.
- **It is not atomic on every adapter.** Where the two statements are not wrapped in a transaction the warehouse honours, a failure between them leaves the range deleted and not reinserted. On adapters without transactional DDL/DML for this path, treat a failed run as "verify the range" rather than "rerun and forget".
- On Snowflake, `delete+insert` with a `unique_key` requires a temporary *table* rather than the default temporary *view*; dbt handles this, but it means this strategy writes an extra relation that `merge` does not.

## `append`

`insert into <target> select ... from <temp_relation>`. No key matching, no delete, nothing to prevent anything.

- The only correct choice for genuinely immutable data with no rerun path.
- **Every rerun of an overlapping range duplicates rows.** A retry, a manual rerun, an orchestrator firing twice, a backfill — each adds another copy, and no test catches it unless someone wrote a uniqueness test on a model whose strategy cannot maintain uniqueness.
- Not available on BigQuery as a named strategy; `merge` without a `unique_key` is the equivalent behaviour there.
- A model on `append` has **no safe backfill procedure**. The range must be deleted by hand first. That is a property of the strategy, not something a backfill script can fix.

## `insert_overwrite`

The strategy whose name means the least. The generated shape in the default implementation is a merge that matches nothing and deletes by predicate:

```sql
merge into <target> as DBT_INTERNAL_DEST
using <source> as DBT_INTERNAL_SOURCE
    on FALSE
when not matched by source and <partition predicate> then delete
when not matched then insert (<columns>) values (<columns>)
```

Read the `on FALSE`: nothing ever matches, so the whole operation is "delete what the predicate selects, insert everything incoming". **If the predicate is empty, the predicate selects the entire table.** That single fact explains all the per-adapter behaviour below.

| Adapter | What `insert_overwrite` does |
|---|---|
| BigQuery | Requires `partition_by`; **raises a compiler error without it.** Replaces the partitions present in the incoming data (dynamic) or the partitions you list (static). |
| Spark | Replaces partitions if `partition_by` is set; **replaces the entire table if it is not.** Not supported with `file_format: delta`. Requires dynamic partition-overwrite mode on some connection methods. |
| Databricks | Replaces partitions/clusters if `partition_by` or a clustering config is set; **replaces the entire table if neither is.** Exact mechanism depends on adapter version and compute type. |
| Snowflake | **Always replaces the entire table.** dbt's own documentation describes it as `truncate` + re-`insert`; it has no partition concept. `overwrite_columns` controls the column list, not the scope. |
| Postgres, Redshift, Trino | Not available. |

The portability trap is now concrete: **the same config that replaces yesterday's partition on BigQuery replaces the entire table on Snowflake.** If the source only produced yesterday, the table now contains only yesterday. The run is green.

### BigQuery: static versus dynamic partitions

| | Dynamic (default) | Static (`partitions` config) |
|---|---|---|
| How the replaced set is chosen | dbt queries the temp table for distinct partition values | You supply the list |
| Extra queries | Yes — temp table plus introspection | No |
| Cost | Higher | Lowest of the three options |
| Risk | Replaces whatever happens to be present | Replaces exactly what you named, whether or not the data is there |

- Dynamic mode exposes `_dbt_max_partition`, a **BigQuery scripting variable, not a Jinja variable** — use it bare, without braces, inside the model's `where` clause.
- The replacement list is built with `array_agg(distinct <partition> ignore nulls)`. **Rows whose partition column is null therefore define no partition to replace, while still being inserted** — so they accumulate a fresh copy on every run. If the partition column can be null, filter those rows out or give them a real value; then verify by running twice and counting.
- Static mode requires the literals in `partitions` to match `partition_by.data_type` exactly. A quoting or type mismatch produces an error in the merge filter, not a silent no-op — which is the better failure, but it means the list needs templating care.
- `copy_partitions: true` swaps the merge for the table-copy API, which is substantially cheaper because the insert is not billed. It works only with dynamic mode, copies partitions **sequentially and non-atomically**, and gives up the single-statement visibility of the merge. Do not use it where consistency across several partitions at one instant matters.

Published dbt Labs benchmarking on BigQuery found `merge` into a *clustered* table cheapest and fastest at small volumes, dynamic `insert_overwrite` slower but scaling better as volume grows, and static partitions cheapest overall. Treat those as directional and measure your own model — see `dbt-performance-tuning`.

### Databricks: `replace_where`

Delta-only, and its own strategy rather than a variant of `insert_overwrite`. It replaces exactly the rows matching `incremental_predicates` with the incoming rows, which makes it the closest thing to a scoped `delete+insert` on that platform. With no predicates it degenerates to `append`.

**It inserts by column position, not by column name.** Reorder the `select` list in a model using `replace_where` and, if the types happen to line up, values land in the wrong columns with no error. This is the only strategy in this document where changing the order of a `select` is a data-corruption event, and it is not detectable by any test that does not check values.

## `incremental_predicates`

A list of raw SQL expressions dbt injects into the statement that locates rows in the target. dbt does not parse or validate them.

```sql
incremental_predicates = [
    "DBT_INTERNAL_DEST.ordered_at >= " ~ dbt.dateadd('day', -7, 'current_date')
]
```

What it is for: without a predicate, `merge` and `delete+insert` must consider the whole target table on every run. The target grows; the incoming batch does not. The predicate is how the scan stays proportional to the work.

### The alias trap

`DBT_INTERNAL_DEST` (target) and `DBT_INTERNAL_SOURCE` (incoming) are the aliases in the generated **merge** statement. That is where the documented convention comes from, and it does not generalise cleanly:

| Strategy | Is `DBT_INTERNAL_DEST` in scope? | Is `DBT_INTERNAL_SOURCE` in scope? |
|---|---|---|
| `merge` | Yes | Yes |
| `delete+insert` | Version- and adapter-dependent — the predicate lands in a plain `delete from <target>` | **No.** The incoming rows are inside a subquery, not joined |
| `insert_overwrite` | Yes, in the delete branch | Effectively no — the join is `on FALSE` |

This is not hypothetical. A dbt-adapters release added the target alias to the `delete+insert` delete statement, which broke every Redshift model using that strategy, because Redshift does not accept an alias in `delete from`. Teams pinned the adapter version until it was reverted.

Practical consequences:

- **Read the generated statement in `target/run/` before trusting a predicate.** This is the one config where the correct syntax genuinely depends on your adapter and version pair.
- A predicate referencing `DBT_INTERNAL_SOURCE` works on `merge` and fails or silently misbehaves elsewhere.
- Pin adapter versions if your project depends on this behaviour, and re-read the generated DML after an adapter upgrade. An adapter upgrade is a change to your DML even though no model file changed.
- The `predicates` config name is accepted as a back-compatible alias for `incremental_predicates`. Two names for one thing is a search hazard; prefer the long name and grep for both.

### Correctness obligations

- **The predicate bounds what can change.** A row outside it cannot be updated or deleted this run. Too narrow and corrections silently do not land — the run succeeds, reads the right source rows, and modifies nothing.
- **The predicate must reference columns that exist in the target's own output**, not CTE aliases or upstream column names.
- **It must widen for a backfill.** A predicate hardcoded to the last 30 days makes a backfill of last year a no-op with a green run.
- On `insert_overwrite`, the predicate *is* the delete scope, so an over-wide predicate deletes more than intended and an empty one deletes everything.

## Custom strategies

Define a macro named `get_incremental_<name>_sql(arg_dict)` and set `incremental_strategy: <name>`. dbt does not validate the name; it looks for the macro and errors if it is missing. Custom strategies are not supported on the BigQuery and Spark adapters.

Worth knowing they exist, and worth resisting. A custom strategy is DML that no dbt user outside your project has ever reviewed, in the one place where a mistake is silent and cumulative. Exhaust the built-in strategies plus `incremental_predicates` first, and if you do write one, test it by proving an incremental run equals a full rebuild — see [testing-incrementals.md](testing-incrementals.md).
