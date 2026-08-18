# Mutable sources: updates, deletes, and restatement

A boundary filter handles rows that arrive late. It does not handle rows that **change**, and it cannot handle rows that are **deleted** — a deleted row has no timestamp to be late with, and there is nothing in the source to select.

This document covers the cases where the source is not append-only. Read [SKILL.md](SKILL.md) for strategy selection first; everything here assumes you have already chosen an idempotent strategy.

## Which shape of source do you have?

Ask precisely, because the correct handling differs and the failure modes do not overlap.

| Source shape | What arrives | Deletes are | Handling |
|---|---|---|---|
| Append-only event log | New rows only | Impossible | Boundary filter; `merge` or `append` |
| Row versions with an update timestamp | A new row per change | Absent from later versions | `merge` on the entity key, latest version wins |
| CDC change stream | Insert/update/delete operations | An explicit operation | Below |
| Full snapshot of the table, each load | Everything, every time | Absent rows | Below |
| Reprocessed window | The whole window, recomputed | Absent rows within the window | `delete+insert` scoped to the window |

The single most common mistake is treating shape 5 as shape 2. A source that re-emits an entire window has *told* you a row is gone by omitting it; `merge` cannot hear that, and the stale row survives forever. That is the top failure mode in [SKILL.md](SKILL.md), and it is the reason this table starts with a question about deletes.

## CDC streams

A change-data-capture feed delivers operations, not state. Each row carries an operation type (insert, update, delete) and something orderable — a log sequence number, a commit timestamp, a transaction id.

Two decisions, and the order matters:

**1. Do you want current state or full history?**

- Current state → an incremental model with `merge`, collapsing the change stream to the latest operation per key.
- Full history of changes → the change stream *is* the history. Keep it as an append-only incremental model and derive state from it. Do not run a snapshot over a CDC feed; you would be reconstructing history that you already have, and less accurately.

**2. What orders the operations?**

This is where CDC-fed incrementals break. Deduplicating "latest per key" requires a total order, and a commit timestamp is often not one — two operations in the same millisecond, or from different sessions, tie. A tie means the winner is arbitrary, and therefore differs between runs.

Use the log sequence number or equivalent monotonic position if the feed provides one. If it only provides a timestamp, add a second ordering column that is unique, and if none exists, say in the model's description that the ordering is not total. Do not paper over it.

```sql
-- current state from a CDC feed. Adapt to your warehouse's syntax.
with ranked as (

    select
        *,
        row_number() over (
            partition by customer_id
            order by change_lsn desc          -- total order, not a timestamp
        ) as version_rank
    from {{ ref('stg_customers_cdc') }}
    {% if is_incremental() %}
    where change_committed_at >= coalesce(
        (select max(change_committed_at) from {{ this }}),
        '1900-01-01'
    ) - interval '2 days'
    {% endif %}

)

select * from ranked where version_rank = 1
```

Three things this snippet gets right and that are easy to get wrong:

- **The dedup is outside `is_incremental()`.** A full refresh must collapse the stream too, or the rebuild loads every historical version and the merge then maintains them.
- **The dedup happens after the boundary filter and before the merge**, so what reaches the merge has one row per key — which is what stops the non-deterministic-merge error on warehouses that raise it.
- **The window can span two loads of the same key.** With a lookback, a key updated in both the old and new window appears twice in the filtered set; the `row_number()` is what resolves it. Remove the dedup and this model breaks only sometimes, which is worse than breaking always.

The failure mode to name: **within-batch ordering collapse.** If the boundary window contains three operations on one key and the dedup keeps the wrong one — because the ordering tied, or because the window boundary split the sequence — the target holds a stale version indefinitely. It is not corrected by later runs, because later runs see no new operations for that key.

### Handling the delete operation

A delete in the stream must translate into something in your target. Choose deliberately:

| Approach | Mechanism | Consequence |
|---|---|---|
| Soft delete flag | Keep the row, set `is_deleted` | Downstream must filter. Someone will forget. |
| Physical removal | Post-hook delete, or an adapter's not-matched-by-source action | History gone; downstream is automatically correct |
| Tombstone rows | Keep the delete operation as a row | Most faithful; needs the most careful querying |

If you soft-delete, the flag has to be **impossible to ignore accidentally**. Either the model excludes deleted rows and exposes them in a separate relation, or the column name makes the omission obvious in review. A boolean called `is_deleted` in the middle of a wide fact table will be missed by someone building a dashboard, and the resulting overcount is your model's fault.

If you physically remove, note that `merge` cannot delete on most adapters. The options are a post-hook that deletes flagged keys — which runs outside the merge and therefore is not atomic with it — or an adapter-specific not-matched-by-source action, which exists on dbt-databricks and is not portable. See [strategy-reference.md](strategy-reference.md).

## Full-snapshot sources

Some sources deliver the entire table every load. Absence is the delete signal, and it is the only one you get.

```
Snapshot of `customers` at load time. A customer_id present yesterday
and absent today has been deleted upstream.
```

An incremental model over this shape has a specific problem: **there is no boundary column that means anything.** The whole table arrives every time, and no row is "new". So either:

- **Materialize it as a table.** Usually right. Incremental buys nothing when every row arrives every load, and the table is always correct.
- **Snapshot it** if you need the history of changes. This is exactly what snapshots are for — see `dbt-snapshots`.
- **Incremental with `delete+insert` and a predicate covering the whole table**, which is a table build with extra steps and a stale-row risk if the predicate is ever narrowed.

The failure mode: **an incremental `merge` over a full-snapshot source accumulates every row that was ever deleted upstream.** It looks like a working model with a slowly growing row count, and the growth is deleted records coming back to life. A count against the source is the only thing that reveals it, which is why the reconciliation query in [SKILL.md](SKILL.md) matters here more than anywhere else.

### Detecting hard deletes without a delete signal

If you must keep an incremental model over a source where deletes are only implied, the detection has to be explicit and periodic:

```sql
-- run as a scheduled test or an audit query, not inside the model
select t.<key>
from <database>.<schema>.<target_model> as t
left join <database>.<schema>.<source_relation> as s
    on t.<key> = s.<key>
where s.<key> is null
```

Rows returned are in your target and not in the source. Whether they are deletes or a source outage is a judgment call — which is precisely why this is a query a person reads, not an automatic deletion. **Never wire an automatic delete to this**; a source that failed to load half its rows would empty half your table, and the run would be green.

## Source restatement

The hardest case, because nothing signals it. The source corrects a closed period in place — a finance adjustment, a reclassification, a corrected exchange rate — and updates no timestamp your boundary reads.

Your model's boundary has long since moved past that period. The correction is never picked up. There is no error, no test failure, and no drift in row counts, because the row count did not change. Only the values did.

There is no boundary expression that solves this. The available responses:

| Response | When it fits |
|---|---|
| Periodic full refresh | The table is affordable to rebuild and the source retains history |
| Scheduled trailing backfill wider than the restatement window | Restatements are bounded — e.g. never older than one quarter |
| Comparison query, source versus target, per period | You need to *detect* restatement rather than blanket-reprocess |
| Ask the source owner for a change timestamp | The real fix, and worth asking for before engineering around its absence |

The comparison query is the one worth having regardless:

```sql
-- per-period reconciliation. Non-zero differences mean restatement or drift.
select
    coalesce(s.<period>, t.<period>) as period,
    s.rows  - t.rows  as row_difference,
    s.total - t.total as measure_difference
from (
    select <period>, count(*) as rows, sum(<measure>) as total
    from <database>.<schema>.<source_relation>
    group by 1
) as s
full outer join (
    select <period>, count(*) as rows, sum(<measure>) as total
    from <database>.<schema>.<target_model>
    group by 1
) as t
    on s.<period> = t.<period>
where s.rows <> t.rows or s.total <> t.total
```

Run it over a trailing window on a schedule. It is the only mechanism in this document that detects a restatement you were not told about, and it also catches drift from every other cause — missed late data, clock skew, a run that was skipped. One query, several failure modes.

Restrict it to closed periods. The current period differs legitimately because it is still filling, and an alert that fires every day is an alert nobody reads.

## Deciding: incremental, snapshot, or table

| Requirement | Answer |
|---|---|
| Current state, source is append-mostly, rebuild is expensive | Incremental |
| Current state, whole table arrives each load | Table |
| History of how a mutable row changed over time | Snapshot |
| The source already emits every change | Append-only incremental over the change stream |
| Current state derived from a change stream | Incremental with `merge`, dedup to latest per key |

The asymmetry worth remembering: **an incremental model built wrong can be rebuilt; history that was never captured cannot be recovered.** If there is a real chance someone will ask what a record looked like last quarter, capture it now. That is a snapshot decision, and `dbt-snapshots` covers the strategies, the hard-delete configuration, and the version differences in how deletes are recorded.
