# Boundary patterns

Templates for the incremental boundary. Read [SKILL.md](SKILL.md) first — strategy choice matters more than any template here, and a perfect boundary on the wrong strategy still loses data.

> **Check whether you need a boundary at all.** On dbt 1.9+, a large time-series model should be evaluated for the `microbatch` strategy before you hand-write any of these patterns. Microbatch derives the window from `event_time` and `batch_size`, which removes the off-by-one boundary bug, the manual lookback, and the bespoke backfill procedure. See [microbatch.md](microbatch.md). The patterns below apply when microbatch does not fit — no reliable event timestamp, not time-series, rows that change arbitrarily far back, or a project below 1.9.

All date arithmetic below uses cross-database macros such as `dbt.dateadd()` where possible. Where a template shows warehouse-specific syntax, gate it on the warehouse — `qualify`, `interval` literals, and date-difference functions are all non-portable.

## Pattern 1 — Lookback from max (the default)

The right starting point for most models. Re-reads a fixed window every run and lets `merge` or `delete+insert` reconcile it.

```sql
{% set lookback_days = var('lookback_days', 3) %}

{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['order_date', 'account_id'],
        on_schema_change='fail',
        incremental_predicates=[
            "DBT_INTERNAL_DEST.order_date >= "
            ~ dbt.dateadd('day', -lookback_days, 'current_date')
        ],
    )
}}

with

orders as (
    select
        order_date,
        account_id,
        order_amount
    from {{ ref('int_orders_daily') }}
    {% if is_incremental() %}
        where order_date >= (
            select coalesce(
                {{ dbt.dateadd('day', -lookback_days, 'max(order_date)') }},
                '1900-01-01'
            )
            from {{ this }}
        )
    {% else %}
        where order_date >= '{{ var("start_date", "2020-01-01") }}'
    {% endif %}
),

final as (
    select
        order_date,
        account_id,
        order_amount
    from orders
)

select * from final
```

Three details that are easy to miss:

- **`coalesce` around `max()`.** On an empty table `max()` returns null, and `where column >= null` matches nothing. The model builds successfully with zero rows and stays empty on every subsequent run, because the table is still empty. A model that is permanently empty and permanently green is a real and confusing failure.
- **The `{% else %}` branch is bounded.** It runs on a new schema and on every `--full-refresh`.
- **The predicate and the boundary use the same window.** A narrower predicate causes duplicates. See SKILL.md.
- **The two windows must not be able to drift apart.** Matching them once is not enough if they are anchored to different clocks. A source filter anchored to `max(...) - lookback` moves with the *data*; a predicate anchored to `current_date - N` moves with the *clock*. They agree while the table is current and diverge the moment it is not — after an outage, a paused schedule, or a restore from an older snapshot. Once the table is more than `N` behind, the source reaches back further than the delete does, and the difference is inserted on top of rows that were never removed. Hourly models feel immune to this and are not; the trigger is any gap longer than the predicate window.

  Floor the predicate at the source boundary so it can only ever be wider:

  ```sql
  -- delete window >= source window, on both clocks
  DBT_INTERNAL_DEST.order_date >= least(
      dateadd(day, -{{ lookback_days }}, current_date),
      (select coalesce(max(order_date), date '2020-01-01') from {{ this }})
  )
  ```

  The wall-clock term is still worth keeping: a predicate anchored *only* to `max()` lets a stale table drag the delete window backwards with the data, widening it silently instead of failing. `least()` keeps that protection while making the duplicate case unreachable — bounded by the clock when the table is current, by the data when it is behind.

  **This one is nearly untestable by rerunning.** Two consecutive runs of a current table produce identical counts and zero duplicates whether or not the floor is present, because the windows only diverge when the table is stale. Verify it by evaluating both expressions against a simulated stale maximum and confirming the delete floor is at or below the source start — a query that needs no build.

## Pattern 2 — Explicit backfill range

Lets a specific range be reprocessed without touching code. Layer this on top of Pattern 1.

```sql
{% set backfill_start = var('backfill_start_date', none) %}
{% set backfill_end = var('backfill_end_date', none) %}

with

orders as (
    select
        order_date,
        account_id,
        order_amount
    from {{ ref('int_orders_daily') }}
    {% if backfill_start %}
        where order_date >= '{{ backfill_start }}'
        {% if backfill_end %}
            and order_date < '{{ backfill_end }}'
        {% endif %}
    {% elif is_incremental() %}
        where order_date >= (
            select coalesce(
                {{ dbt.dateadd('day', -lookback_days, 'max(order_date)') }},
                '1900-01-01'
            )
            from {{ this }}
        )
    {% else %}
        where order_date >= '{{ var("start_date", "2020-01-01") }}'
    {% endif %}
),
```

**Start inclusive, end exclusive.** Consecutive chunks are then contiguous with no gap and no overlap. Mixing the conventions is how a backfill double-counts one day at every chunk boundary — twelve monthly chunks, twelve wrong days, all of them plausible.

When a backfill range is supplied, the `incremental_predicates` window must widen to cover it, or the delete/match phase will not reach the rows being replaced:

```sql
{% if backfill_start %}
    {% set predicate_floor = "'" ~ backfill_start ~ "'" %}
{% else %}
    {% set predicate_floor = dbt.dateadd('day', -lookback_days, 'current_date') %}
{% endif %}
```

## Pattern 3 — Guarding an incomplete current period

If the source is still receiving data for the current period, loading it produces a partial row that looks complete. A consumer comparing today against yesterday sees a drop that is not real.

```sql
    {% if is_incremental() %}
        where order_date >= (
            select coalesce(
                {{ dbt.dateadd('day', -lookback_days, 'max(order_date)') }},
                '1900-01-01'
            )
            from {{ this }}
        )
        -- exclude the still-open period; it loads on the next run
        and order_date < current_date
    {% endif %}
```

The trade-off is explicit: excluding the open period costs freshness, including it costs correctness. Choose deliberately and put the choice in the model's description, because it is the first thing someone will ask about.

Note this interacts with the boundary. If the current period is excluded, `max()` stays one period behind, and the lookback must be wide enough to still cover the excluded period once it closes. With `lookback_days` at or above 1 and `>=`, it is.

## Pattern 4 — Empty-table and first-run safety

Anything computing arithmetic on `max()` in Jinja — as opposed to in SQL — must handle the null case explicitly, because Jinja arithmetic on `None` raises at compile time.

```sql
{% if is_incremental() %}
    {% set boundary_query %}
        select max(order_date) from {{ this }}
    {% endset %}
    {% set max_date = run_query(boundary_query).columns[0][0] if execute else none %}

    {% if max_date %}
        {% set boundary = (max_date - modules.datetime.timedelta(days=lookback_days)) %}
    {% else %}
        {% set boundary = modules.datetime.date(2020, 1, 1) %}
    {% endif %}
{% endif %}
```

Two requirements:

- **Guard on `execute`.** During dbt's parse phase `run_query` returns nothing, and unguarded code fails on `dbt parse` and `dbt ls` even though `dbt run` would work.
- **Handle the null.** An empty table happens more often than expected — first run, a new developer schema, after a `sync_all_columns` rebuild, or after someone dropped the table by hand.

A third requirement is the one that actually breaks builds, and it is invisible in the snippet above: **you do not control the type that comes back.** `columns[0][0]` returns whatever the adapter's driver produced — a `date` on one warehouse, a `datetime` on another, a string on a third, depending on the column type and the driver version. `modules.datetime.timedelta` arithmetic only works on the first two. When it is a string, the subtraction raises at compile time, and the usual reaction is to reach for `modules.datetime.datetime.strptime(...)` to parse it back — which then breaks the moment the driver returns a real date object rather than a string.

That is a loop worth refusing to enter. **Do the arithmetic in SQL and the type question disappears:**

```sql
-- the boundary is computed by the warehouse, which knows the column's type
where order_date >= (
    select coalesce(dateadd(day, -{{ lookback_days }}, max(order_date)),
                    '2020-01-01')
    from {{ this }}
)
```

This is why Pattern 1 is the default and this pattern is the exception. Reach for Jinja arithmetic only when the value must exist at compile time — to build a predicate list, or to choose between code paths — and when you do, cast it explicitly rather than trusting the driver: `{% set max_date = max_raw | string | truncate(10, true, '') %}` and compare as strings, or push the arithmetic back into the query that fetched it.

Prefer expressing the boundary in SQL (Pattern 1) where you can. It has no parse-phase behavior to get wrong, and it does not cost an extra round trip to the warehouse on every parse.

## Pattern 5 — Multiple sources with a cutoff

For a model whose history came from a system that no longer exists.

```sql
{{ config(materialized='incremental', full_refresh=false) }}

with

current_source as (
    select
        order_date,
        account_id,
        order_amount
    from {{ ref('stg_orders_current') }}
    {% if is_incremental() %}
        where order_date >= (
            select coalesce(
                {{ dbt.dateadd('day', -lookback_days, 'max(order_date)') }},
                '{{ var("cutover_date") }}'
            )
            from {{ this }}
        )
    {% else %}
        where order_date >= '{{ var("cutover_date") }}'
    {% endif %}
),

legacy_source as (
    select
        order_date,
        account_id,
        order_amount
    from {{ ref('stg_orders_legacy') }}
    where order_date < '{{ var("cutover_date") }}'
    {% if is_incremental() %}
        -- legacy source is frozen; never re-read it on an incremental run
        and 1 = 0
    {% endif %}
),

unioned as (
    select * from current_source
    union all
    select * from legacy_source
)
```

`full_refresh=false` is mandatory here, not advisory: if the legacy source has been decommissioned, a full refresh replaces real history with an empty union branch. The model runs successfully and the history is gone.

## Pattern 6 — Deduplicating to one row per key

Required on any `merge` model whose source can emit a key more than once, which includes every model with a lookback: a key updated in both the previous and the current window appears twice in the filtered set.

```sql
with

source_rows as (
    select
        order_id,
        ordered_at,
        order_status,
        order_amount,
        source_updated_at,
        source_sequence_id
    from {{ ref('stg_orders') }}
    {% if is_incremental() %}
        where source_updated_at >= (
            select coalesce(
                {{ dbt.dateadd('day', -lookback_days, 'max(source_updated_at)') }},
                '1900-01-01'
            )
            from {{ this }}
        )
    {% endif %}
),

deduplicated as (
    select *
    from source_rows
    qualify row_number() over (
        partition by order_id
        order by source_updated_at desc, source_sequence_id desc
    ) = 1
)

select * from deduplicated
```

Four requirements, each preventing a distinct failure:

- **The dedup is outside `is_incremental()`.** The first run and every `--full-refresh` insert directly with no merge, so a dedup written only in the incremental branch admits every duplicate on the rebuild — and the merge then maintains them faithfully forever. This is the version of the bug that survives a full refresh, which is why it is so hard to explain.
- **The ordering is total.** `source_updated_at desc` alone leaves ties, and a tie makes the winner arbitrary — so reprocessing the same data can produce a different value with no source change, matching row counts, and passing tests. The second ordering column is what makes it deterministic. If no unique column exists, the tiebreak cannot be made deterministic, and that belongs in the model's description rather than being left implicit.
- **The partition matches `unique_key` exactly.** A narrower partition collapses distinct rows; a wider one leaves duplicates that the merge will reject or accept depending on the warehouse.
- **`qualify` is not universal.** It is available on Snowflake, Databricks, BigQuery and Teradata among others; elsewhere use a subquery with `where row_number_column = 1`. Gate on the warehouse.

## Pattern 7 — Load-time watermark for unbounded lateness

When lateness cannot be bounded — so no lookback is wide enough — watermark on the load timestamp instead of the event timestamp. The trade-offs are in the watermark comparison table in [SKILL.md](SKILL.md); the shape is:

```sql
{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key='order_id',
        on_schema_change='fail',
    )
}}

with

source_rows as (
    select
        order_id,
        ordered_at,
        order_amount,
        loaded_at
    from {{ ref('stg_orders') }}
    {% if is_incremental() %}
        -- watermark on load time: catches arbitrarily late events,
        -- at the cost of a target predicate that cannot be narrowed by date
        where loaded_at > (
            select coalesce(max(loaded_at), '1900-01-01') from {{ this }}
        )
    {% endif %}
)

select * from source_rows
```

Two things are different from every other pattern here, and both are deliberate:

- **`>` is correct on a load-time watermark, and `>=` re-reads the last batch.** This is the one exception to the `>=` rule, and it holds only because the watermark is a pipeline-assigned value rather than a business timestamp: re-reading is wasteful but harmless, whereas on an event timestamp `>` permanently skips boundary rows. If you are unsure which situation you are in, use `>=`; the cost of the safe choice is compute, and the cost of the wrong choice is missing data. Either way, correctness now rests entirely on the key rather than on the comparison operator, so the `unique_key` must be genuinely unique and non-null.
- **`incremental_predicates` cannot narrow the target by event date**, because a late row can update a target row of any age. That is the real price of this pattern: the merge scans a wide slice of the target on every run, and the model gets more expensive as the table grows. Where the source's lateness is bounded, the event-time watermark with a lookback is cheaper and should be preferred.

The failure mode this pattern *introduces*: an extraction tool resync rewrites `loaded_at` for rows you already have, and the model reprocesses them. With a correct key that is wasted compute. Without one it is duplication of the entire resynced range.

## Pattern 8 — Separate watermark table

When the boundary cannot be derived from the target — a model whose output does not carry the source's timestamp, a model that aggregates away the time column, or a chain of models that must share one boundary — store the watermark explicitly.

```sql
{% if is_incremental() %}
    where source_updated_at >= (
        select coalesce(max(processed_through_at), '1900-01-01')
        from {{ ref('etl_watermarks') }}
        where model_name = 'fct_orders'
    )
{% endif %}
```

This is the highest-maintenance pattern in this document and should be the last resort. The costs are real:

- **The watermark and the table can disagree.** Updating the watermark before the load succeeds skips a window permanently; updating it in a post-hook that fails leaves it behind, which is the safe direction but reprocesses. Advance the watermark only after a verified load, and prefer reprocessing to skipping.
- **`--full-refresh` does not reset it.** The table is rebuilt from scratch and the watermark still says the model is caught up, so the first incremental run after the rebuild loads nothing into a table that has just been repopulated by a different code path. Reset the watermark as part of any full refresh, and make that a documented step rather than a remembered one.
- **A shared watermark couples models.** One slow or failing model holds the boundary back for everything reading it, which is sometimes the intent and is more often a surprise.

Deriving the boundary from `{{ this }}` (Pattern 1) has none of these failure modes, because the table *is* the watermark and cannot disagree with itself. Exhaust that option first.

## Choosing between the patterns

| Situation | Pattern |
|---|---|
| Standard time-partitioned fact | 1 |
| Needs ad-hoc range reprocessing | 1 + 2 |
| Source still writing to the current period | 1 + 3 |
| Boundary must be computed in Jinja | 4 |
| History from a retired system | 5, always with `full_refresh=false` |
| Source can emit a key more than once | 6, combined with whichever boundary applies |
| Lateness is unbounded or unmeasurable | 7, accepting the wider target scan |
| Boundary cannot be derived from the target | 8, as a last resort |

Patterns 1–5 and 7–8 are alternative boundaries and are mutually exclusive. **Pattern 6 is orthogonal and composes with all of them** — a boundary decides which rows are read, dedup decides which of the read rows survive.

## Anti-patterns

Each of these appears in real projects and each fails silently.

| Anti-pattern | What happens |
|---|---|
| `where ordered_at > (select max(ordered_at) from {{ this }})` | Rows at exactly the boundary timestamp are never loaded, and the window is never revisited |
| `max()` with no `coalesce` | Zero rows on an empty target; the model stays empty and green forever |
| Boundary filter applied after an aggregate instead of before | The source scan is not reduced, so the model is incremental in name and full-scan in cost |
| A hardcoded date literal as the boundary | Works for one run; silently reloads or skips everything after that |
| Dedup inside `is_incremental()` only | Every full refresh loads duplicates, and the merge maintains them |
| `current_date` mixed with a UTC timestamp column | The window shifts by the session offset; edge rows land in the wrong period |
| A boundary column different from the predicate column | The predicate prunes nothing; the target scan stays full-table |
| Filtering the boundary subquery with `where` on `{{ this }}` conditions that no longer match the model | The subquery returns null, and the boundary silently becomes the fallback date |

## What to check after writing any of these

Read the compiled SQL. The boundary is Jinja, so the model file does not tell you what runs:

```bash
dbt compile --select <model>
# then read target/compiled/<project>/models/.../<model>.sql
```

Confirm the boundary resolved to a real value and not to `null`, `None`, or an empty string. A boundary that compiled to nothing is the most common cause of an incremental model that reads all of history or none of it.

Then read the **generated** SQL in `target/run/`, which is where the merge or delete lives. The compiled file shows the `select`; only the run file shows what will be done with it, and `incremental_predicates` appears nowhere else. Confirm:

- The boundary window and the predicate window are the same width.
- The predicate references a column that exists in the target.
- The dedup, if present, is outside the `is_incremental()` guard.

Then build three times and confirm counts are stable on the third — see [testing-incrementals.md](testing-incrementals.md).
