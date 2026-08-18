# Strategy selection — when each is right, and how it fails

Read [SKILL.md](SKILL.md) first for the one question that decides the strategy and the decision summary. This document is the narrative behind each choice: what the strategy does, when it is the right one, and the specific way it loses or duplicates data when it is the wrong one. For the exact generated DML and per-adapter support, see [strategy-reference.md](strategy-reference.md); for `microbatch`, see [microbatch.md](microbatch.md).

- [`merge`](#merge) — updates matched keys, never deletes
- [`delete+insert`](#deleteinsert) — for sources that reprocess a window
- [`append`](#append) — immutable events only
- [`insert_overwrite`](#insert_overwrite) — whole-partition replacement, semantics vary by warehouse

## `merge`

Matches target rows to source rows on `unique_key`, updates matches, inserts the rest.

- **Requires a `unique_key`.** Without one the generated statement has no match condition and no update clause, so `merge` degrades to `append` — silently on most adapters, as a hard error on some. `merge` with no key is not a safety net; it is a misleading name over an append.
- **Never deletes.** This is the critical property and the source of the most expensive failure mode in this document.
- Can update a subset of columns via `merge_update_columns` / `merge_exclude_columns`, and preserves target columns not present in the source.
- **A null in any `unique_key` column defeats matching**, because `null = null` is not true. The row is never matched and is inserted again on every run — duplicates that accumulate slowly and look like real rows. Guarantee non-null key columns, or hash them into a surrogate key with a macro that maps nulls to a stable placeholder.
- **A duplicate key in the incoming data is rejected by some warehouses and accepted by others.** Snowflake raises a non-deterministic-merge error; BigQuery raises "must match at most one source row for each target row". That error is doing you a favour. The fix is to deduplicate in the model — a `row_number()` filter over the key, ordered by the source's own update timestamp — not to switch to a strategy that accepts the ambiguity.
- **Deduplicate unconditionally, not inside `is_incremental()`.** The first run and every full refresh do not go through a merge at all: they insert everything the query returns. Deduplication written only in the incremental branch therefore admits every duplicate on the rebuild, and the merge then perpetuates them. Put the dedup in the main query body.

**When `merge` is wrong:** any source that reprocesses. Suppose the source recomputes yesterday's data and one grain combination legitimately drops out — a product line with no orders that day, an order that was voided. `merge` finds no source row for that key, so it does nothing. The old row stays. The target now reports activity that the source says never happened, and no test detects it because the row is individually valid.

This failure is permanent. It does not self-correct on the next run, or the next hundred. It is only fixed by a full refresh or a targeted delete — and by the time anyone knows to do that, there may be months of them.

## `delete+insert`

Deletes target rows matching the incoming keys, then inserts the new rows.

```sql
{{ config(
    materialized = 'incremental',
    incremental_strategy = 'delete+insert',
    unique_key = ['order_date', 'region', 'product_id'],
    incremental_predicates = [
        "DBT_INTERNAL_DEST.order_date >= dateadd(day, -{{ var('lookback_days', 3) }}, current_date)"
    ],
    on_schema_change = 'fail',
) }}
```

**Use it whenever the source can reprocess a window.** Hourly or daily ETL that rewrites a range, event pipelines that deduplicate after the fact, sources where invalid rows are removed after QA, anything subject to deletion-propagation requirements.

**Availability: not universal.** `delete+insert` does not exist on BigQuery or Spark, and arrived late on Databricks. On BigQuery the equivalent is `insert_overwrite` over the reprocessed partitions; on Databricks it is `replace_where`. Do not write "use `delete+insert` when the source reprocesses" as portable advice.

Three properties to understand precisely:

- **Delete is scoped to the keys present in the incoming data**, plus whatever `incremental_predicates` restricts it to. A key that vanished from the source *and* is not in the incoming set is not deleted by key matching alone. This is exactly why the predicate matters: a predicate covering the reprocessed date range makes the delete cover that whole window, so vanished rows within it do go away.
- **It replaces whole rows.** If you need to update some columns and preserve others, `merge` is the strategy. `delete+insert` has no concept of a partial update, and `merge_update_columns` is silently ignored here.
- **It accepts duplicate keys that `merge` would reject.** The delete uses a distinct key list; the insert inserts every row. So switching from `merge` to `delete+insert` to escape a non-deterministic-merge error does not solve the duplicate — it loads it. Fix the grain instead.

`delete+insert` costs a delete pass that `merge` does not. That is the price of the target matching the source, and on a reprocessing source it is worth paying. Note that on some adapters the two statements are not atomic: a failure between them can leave the range deleted and not reinserted, so treat an interrupted run as "verify the range" rather than "just rerun".

## `append`

Inserts, full stop. No key matching, no deletes.

- The **only** correct choice for genuinely immutable append-only data — raw event logs where a row, once written, is never revised.
- **Any rerun of an overlapping range duplicates rows.** A retried job, an accidental second invocation, a manual rerun after a failure — each adds another copy. There is nothing to prevent it and no test that catches it except a uniqueness test that this model probably does not have.
- Late-arriving data becomes a duplicate if the same window is processed twice.

If there is any chance of reprocessing, correction, or a rerun, `append` is the wrong answer. In practice that describes most tables, which is why `append` is far less often correct than it is chosen.

## `insert_overwrite`

Replaces whole partitions rather than matching rows. Efficient at scale — but **its semantics differ substantially by warehouse, and it is not available everywhere.**

Check the warehouse before recommending it. The full per-adapter table is in [strategy-reference.md](strategy-reference.md); the headline is that the same config means different things:

- On BigQuery it **requires** a `partition_by` config — it raises a compiler error without one — and replaces the partitions present in the incoming data, or the ones you list explicitly.
- On Spark and Databricks it replaces partitions if a partitioning or clustering config is set, and **replaces the entire table if it is not.**
- On Snowflake it **always replaces the entire table.** There is no partition scope. dbt's own documentation describes it as truncate-and-reinsert.
- On Postgres, Redshift and Trino it does not exist.

So a model copied from a BigQuery project to a Snowflake project keeps building, and quietly starts replacing all of history with whatever the last run's window produced.

The shared failure mode is worth naming: **if the incoming data for a partition is empty or incomplete, that partition is replaced with the empty or incomplete data.** A source outage does not produce a failure — it produces a partition that is now silently wrong. Guard against it in the model: if the incoming set for a window is implausibly small, it is better to raise than to write it.

A second, subtler one on the partition-scoped adapters: **rows whose partition column is null define no partition to replace, yet are still inserted.** They therefore accumulate a new copy on every run. Filter them out or give them a real value, then prove it by running twice and counting.

