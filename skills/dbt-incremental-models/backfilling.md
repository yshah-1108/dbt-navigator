# Backfilling an incremental model

**The general backfill procedure lives in [`dbt-shipping-changes/backfilling.md`](../dbt-shipping-changes/backfilling.md)** — batching, sequencing, baseline capture, per-batch verification, rebuilding downstream, notifying consumers. Read it for the operational side. It applies unchanged here.

This document covers only what is specific to the incremental **model code**: designing the range mechanism so a backfill is possible at all, and the incremental-specific ways a backfill silently does nothing.

## Whether a backfill is possible is decided by the model, not by the backfill

| Model property | Consequence for backfilling |
|---|---|
| `merge` with a complete, non-null `unique_key` | Idempotent over a range. Rerunning is safe. |
| `merge` with an incomplete `unique_key` | **Duplicates.** The key does not identify a row, so the merge inserts instead of updating. |
| `delete+insert` | Range deleted then reinserted. Correct where rows can also disappear. |
| `append` | **Duplicates, every time.** No safe backfill exists until the range is deleted by hand. |
| `insert_overwrite` | Replaces the partitions covered by the range — check what "partition" means on your adapter. |
| `microbatch` | Range is the mechanism: `--event-time-start` / `--event-time-end`. Nothing to design. |
| No range parameter | Only a full refresh can correct history. On `full_refresh=false`, nothing can. |
| `full_refresh=false`, no range parameter | **The table cannot be corrected at all.** |

The last row is why the range mechanism must be built at the same time as the flag, not later. See the `full_refresh` section of [SKILL.md](SKILL.md).

## Design the range parameter in before you need it

Retrofitting a backfill mechanism during an incident is how the wrong range gets typed.

```sql
{% set backfill_start = var('backfill_start', none) %}
{% set backfill_end = var('backfill_end', none) %}

with

source_data as (
    select
        order_date,
        region,
        product_id,
        amount
    from {{ ref('<upstream_model>') }}
    {% if backfill_start %}
        where order_date >= '{{ backfill_start }}'
        {% if backfill_end %}
            and order_date < '{{ backfill_end }}'
        {% endif %}
    {% elif is_incremental() %}
        where order_date >= (
            select coalesce(max(order_date), '1900-01-01') from {{ this }}
        )
    {% endif %}
),
```

Three properties this has:

- **Start inclusive, end exclusive.** Consecutive chunks then tile the range with no gap and no overlap. A closed-closed convention double-processes every boundary day, which with `append` means duplicates and with `merge` means wasted work.
- **The backfill branch takes precedence over the incremental branch.** Otherwise the boundary predicate clamps the range to recent data and the backfill silently does nothing.
- **A missing end date means "to the present."** Decide which behavior you want and document it, because the alternative — treating a missing end date as an error — is also reasonable and the ambiguity is dangerous.

Note that a deduplication step, if the model has one, must sit **outside** `is_incremental()` so the backfill branch gets it too. A backfill that bypasses the dedup loads every duplicate version in the range.

## The predicate must widen too

The most common reason a backfill silently fails: the range parameter widens but `incremental_predicates` does not, so the target rows the backfill should replace are outside the modifiable window. The run succeeds. Nothing changes.

```sql
{% if backfill_start %}
    {% set predicate_start = "'" ~ backfill_start ~ "'" %}
{% else %}
    {% set predicate_start = dbt.dateadd('day', -30, 'current_date') %}
{% endif %}

{{ config(
    materialized = 'incremental',
    incremental_strategy = 'delete+insert',
    unique_key = ['order_date', 'region', 'product_id'],
    incremental_predicates = [
        "DBT_INTERNAL_DEST.order_date >= " ~ predicate_start
    ],
) }}
```

Then **read the generated statement in `target/run/`** before running the backfill for real. The predicate is the one config whose correct form depends on your adapter and version pair, and whose failure mode is a green run that changes nothing — see the alias notes in [strategy-reference.md](strategy-reference.md). Date function syntax also varies by warehouse; prefer the cross-database macros.

## Before running

The general pre-flight checks are in the canonical document. Three are incremental-specific and worth restating because each one is invisible until after the fact:

1. **Confirm the strategy replaces rather than appends.** Check `incremental_strategy` in the compiled config, not in memory. With `append`, a backfill duplicates every row in the range.
2. **Verify the source still holds the range.** If upstream retention has aged the data out, the backfill replaces real rows with fewer or none. On a `full_refresh=false` model this is unrecoverable.
3. **Test one small chunk in dev first**, using the project's own environment-detection expression to be certain which target you are on. A backfill against production that was meant for dev is not fixed by rerunning it.

Some projects prohibit `--vars` for environment-scoped behavior and use in-SQL environment detection instead. A genuine backfill range is a different thing from a dev date limit, but check the project's own convention before assuming `--vars` is acceptable.

## Downstream incremental models will not revisit the range

A backfill changes historical rows. Any downstream incremental model has already processed that period and will **not** revisit it on its own — its boundary has moved past it.

So the corrected numbers sit in the backfilled model, and every downstream model still reports the old ones. Two tables now disagree, which is worse than the single wrong answer you started with.

```bash
dbt ls --select <model>+
```

Enumerate them, then backfill each with the same range in dependency order. Some cannot accept a range parameter and need a full refresh instead — which may or may not be safe on each of them. Work that out before starting, not after.

## Verify

Per-period, against the baseline, using an explicit database and schema rather than `ref()`:

```sql
select
    order_date,
    count(*) as row_count,
    sum(amount) as total_amount
from <explicit_database>.<explicit_schema>.<model>
where order_date >= '<start>' and order_date < '<end>'
group by order_date
order by order_date
```

- **Every period in the range is present.** A missing date means a chunk was skipped or a boundary was mistyped.
- **No duplicate keys** in the backfilled range.
- **Totals moved as the fix predicted.** A backfill that changes nothing means it did not apply — most likely the predicate did not reach the range.
- **Data outside the range is untouched.** Check the period immediately before the start and immediately after the end.
- **Then run the normal incremental build twice** and confirm counts are stable. If they move, the boundary and the backfilled range overlap in a way that duplicates rows on every future scheduled run — a permanent daily defect created by a one-time fix.

Fuller evidence, including proving the result equals a full rebuild, is in [testing-incrementals.md](testing-incrementals.md).

## Checklist

Additional to the checklist in the canonical backfilling document, not instead of it.

- [ ] Range parameter exists in the model and takes precedence over the incremental boundary
- [ ] Start inclusive, end exclusive; chunks tile with no gaps or overlaps
- [ ] Strategy confirmed to replace rather than append
- [ ] `incremental_predicates` widened to cover the range, verified in the generated DML
- [ ] Deduplication, if any, applies to the backfill branch as well
- [ ] Source verified to still hold the range
- [ ] One small chunk tested in dev first, on a confirmed target
- [ ] Downstream incremental models enumerated and backfilled in dependency order
- [ ] Every period present, no duplicate keys, totals moved as predicted
- [ ] Normal incremental run repeated twice with stable counts

## The incremental-specific failure modes

1. **The predicate did not reach the range.** The run succeeds, reads the right source rows, and cannot modify the target rows. Nothing changes and it looks like it worked.
2. **Downstream incremental models never revisited the period.** The corrected model is right; every report reading from it still shows the old numbers, and now the two disagree.
3. **Backfilling an `append` model.** Every row in the range is duplicated, so totals roughly double for exactly the period that was supposed to be fixed.
4. **Off-by-one on chunk boundaries.** Closed-closed ranges double-process boundary periods; a gap between chunks leaves a period unprocessed and looking normal.
5. **Source retention had already aged the range out.** Real rows are replaced with fewer or none — unrecoverable on a `full_refresh=false` model.
6. **The backfill branch bypassed the dedup**, because the dedup was written inside `is_incremental()`.
7. **The backfilled range overlaps the live boundary**, so every subsequent scheduled run re-adds rows. A one-time fix that becomes a daily defect.
