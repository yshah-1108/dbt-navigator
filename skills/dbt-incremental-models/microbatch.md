# Microbatch

`incremental_strategy: 'microbatch'` — dbt splits one incremental run into several time-bounded queries, one per batch, and replaces each batch independently.

**Availability: dbt Core 1.9 and later.** On earlier versions none of this exists and the hand-written boundary patterns in [boundary-patterns.md](boundary-patterns.md) are the only option. Adapter support is listed in [strategy-reference.md](strategy-reference.md).

## What problem it actually solves

Every hand-rolled incremental model carries four pieces of bespoke logic: a boundary expression, a lookback window, a strategy that tolerates reprocessing, and a backfill mechanism. Each is a place to be wrong, and three of the four fail silently.

Microbatch replaces all four with configuration. You write the model as if it processes exactly one time window; dbt decides which windows to run and how to replace each one.

The other property that is hard to get by hand: **a failed batch is retryable on its own.** `dbt retry` reprocesses only the batches that failed, rather than re-running a whole model whose boundary has now moved past the gap.

## Configuration

| Config | Required | Meaning |
|---|---|---|
| `event_time` | yes | Column holding when the row's event happened. Also set on the model's parents |
| `batch_size` | yes | `hour`, `day`, `month`, or `year` |
| `begin` | yes | Start of time for initial and full-refresh builds |
| `lookback` | no, default `1` | How many batches before the latest bookmark to reprocess each run |
| `concurrent_batches` | no | Override dbt's auto-detection of parallel batch execution |

```sql
{{ config(
    materialized = 'incremental',
    incremental_strategy = 'microbatch',
    event_time = 'ordered_at',
    batch_size = 'day',
    begin = '2023-01-01',
    lookback = 3,
    full_refresh = false,
) }}

select
    order_id,
    customer_id,
    ordered_at,
    order_total
from {{ ref('stg_orders') }}
```

There is no `is_incremental()` block, no boundary subquery, and no `{{ this }}`. If you find yourself adding one, re-read the section on parallelism below — referencing `{{ this }}` changes how dbt schedules the batches.

Some adapters require one extra config because they implement batch replacement differently: a `unique_key` where the mechanism is `merge`, or a `partition_by` where it is partition replacement. A microbatch model that works on one warehouse can therefore fail to build on another with a missing-config error. Check the adapter's page rather than copying a config across platforms.

### `begin` is a promise about cost, not just a date

`begin` is where the first build starts. A daily-grain model with `begin` two years back builds roughly 730 batches on its first run, each its own query. That is usually the intent — but it is also the shape of an accidental five-figure warehouse bill, so state the batch count before the first build:

```
batches ≈ (today − begin) / batch_size
```

If that number is uncomfortable, either move `begin` forward and accept that older history is not in the table, or build the history deliberately in slices with `--event-time-start` / `--event-time-end`.

## `event_time` on parents is what makes it cheap

dbt automatically filters any `ref()` or `source()` **that has `event_time` configured** to the batch's window. Inputs without it are not filtered.

This is the single biggest performance mistake with microbatch: **a parent without `event_time` is scanned in full, once per batch.** A model with a 90-day backfill and one unfiltered large parent performs 90 full scans of it. The hand-written version performed one. Nothing errors, and the only symptom is the bill.

- Set `event_time` on every large parent you want narrowed.
- Small dimension inputs are legitimately left unfiltered — they are joined in whole, which is normally what you want.
- To read an `event_time`-configured parent unfiltered on purpose, use `ref('<model>').render()`. It is rarely right; each batch then scans that input entirely.

### Choosing the column

`event_time` should be when the event *happened*, not when it was loaded. dbt's documentation is explicit that ingestion-tool sync columns are not appropriate, and the reason is semantic: other dbt features read the same config and will treat it as event time regardless of what you meant.

If a load timestamp is genuinely the only column you have, the documented consequences are worth stating precisely rather than discovering:

- A `loaded_at`-style column can produce **duplicate rows across runs**, because the same event can be re-loaded into a later batch while its earlier copy stays in the earlier batch. A lookback reduces this and cannot eliminate it.
- An ingestion timestamp assigned by an extraction tool **changes if the connector is resynced**, which reprocesses data into a second, different batch. That is fine as long as it never happens, or a full refresh follows when it does.

If neither compromise is acceptable, microbatch is the wrong strategy for that model. A hand-written boundary on a load timestamp with `merge` on a row-level key handles the same situation with duplicates prevented by the key rather than by the batch — see [boundary-patterns.md](boundary-patterns.md).

## `lookback`

`lookback` reprocesses N batches before the latest bookmark on every incremental run, which is the microbatch equivalent of a lookback window on a hand-written boundary. Default `1`.

Because every batch is replaced wholesale, reprocessing is free of correctness risk — reprocessing a batch is idempotent by construction. The cost is only compute: `lookback: 7` on a daily model means eight batch queries every run instead of one.

Size it to the source's measured lateness, exactly as with a hand-written window. Late data outside the lookback is still missed; the mechanism does not change that arithmetic, it only makes widening the window safe.

## Batch boundaries and timezones

Each batch filters `event_time >= <start>` and `event_time < <end>` — **start inclusive, end exclusive.** Consecutive batches therefore tile the timeline with no gap and no double-counting, which is the convention worth matching anywhere else you write a range.

**dbt treats `event_time`, `begin`, `--event-time-start` and `--event-time-end` as UTC.** If the column is stored in a local or session-dependent timezone, batch edges are offset by that difference: rows fall into the neighbouring batch, and a daily aggregate is a blend of two partial days. It builds green and every number is slightly wrong. Cast to UTC upstream — type and timezone normalisation belongs in staging.

## Parallelism

dbt auto-detects whether batches can run concurrently. Where it does, batches run in parallel up to the thread count.

The auto-detection has documented conditions: parallel execution is supported on a subset of adapters, the first and last batch always run serially, and **a model that references `{{ this }}` is run sequentially** because concurrent writes to the relation being read would conflict.

Set `concurrent_batches: false` when batch order matters — a running total, a state machine, anything where a batch reads the output of the batch before it. Parallel execution of order-dependent logic does not error. It produces a table that is wrong in a way that depends on thread scheduling, and therefore does not reproduce.

Two related behaviours worth knowing: model `pre-hook`s run only on the first batch and `post-hook`s only on the last, and if the first batch fails the remaining batches are skipped. A hook written on the assumption that it runs once per model still runs once per model — but a hook that needs to run per batch has nowhere to live.

Inside a batch, `model.batch` exposes `id`, `event_time_start` and `event_time_end` for logging. It is populated only during batch execution, so guard access with `{% if model.batch %}`.

## Backfills and full refresh

```bash
# reprocess a specific window; both flags are required together
dbt run --select <model> --event-time-start "2024-09-01" --event-time-end "2024-10-01"
```

This is the whole backfill mechanism. No `--vars`, no bespoke range parameter, no widening of a predicate — the batch boundaries are the range, and each batch is an independent, idempotent query. It is the strongest single argument for microbatch on a model that will need corrections.

- **Set `full_refresh: false`** on microbatch models, as dbt's own documentation recommends. A `--full-refresh` on a large microbatch model rebuilds from `begin`, which is rarely what the person typing it wanted.
- `--full-refresh` on a microbatch model does nothing unless `begin` is configured.
- Interrupted a backfill? Rerun the same start and end. Completed batches are simply rebuilt identically. Compare that with a hand-written backfill, where resuming requires knowing exactly which chunks finished.
- The operational discipline around any backfill — capturing a baseline, verifying per period, rebuilding downstream, notifying consumers — is unchanged and lives in `dbt-shipping-changes`.

## When microbatch is the wrong choice

| Situation | Why |
|---|---|
| No reliable event timestamp | The whole mechanism is derived from `event_time` |
| Not time-series — a dimension keyed by entity | There is no meaningful batch |
| Rows must be updated by key across arbitrary time | Batch replacement is by window, not by key; an update to a two-year-old row needs its batch reprocessed |
| dbt below 1.9 | Not available |
| You need column-level update control | `merge_update_columns` has no equivalent here |
| Batch-order-dependent logic and you want parallelism | You must choose one; sequential batches are correct but slower |

The honest summary: microbatch is the better default for large append-mostly time-series tables on 1.9+, and it is not a general replacement for `merge` on a keyed, mutable table.

## Verify

- Read one batch's compiled SQL and confirm the window is what you expect, in UTC, start-inclusive and end-exclusive.
- Check the run log for the **number of batches**. A model you expected to run 3 batches running 700 means the bookmark or `begin` is not what you assumed.
- Confirm each large parent was filtered. If a parent appears in the compiled batch SQL without a time predicate, it lacks `event_time` and you are paying for a full scan per batch.
- Run twice and compare row counts. Batch replacement should make the second run a no-op on already-complete batches.
- Then the general obligations in [testing-incrementals.md](testing-incrementals.md) apply unchanged.
