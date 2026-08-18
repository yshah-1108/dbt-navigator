# Reducing what you scan

Detailed technique for Step 2 of [SKILL.md](SKILL.md). Warehouse-neutral, and where nearly all real wins come from. Physical layout only helps a query that already gives the engine something to skip on.


- [Filter as early as possible](#filter-as-early-as-possible)
- [Pre-filter the large side of a join](#pre-filter-the-large-side-of-a-join)
- [Do not wrap the filtered column in a function](#do-not-wrap-the-filtered-column-in-a-function)
- [Skipping needs a bound the planner can evaluate](#skipping-needs-a-bound-the-planner-can-evaluate)
- [Why a filter fails to push down](#why-a-filter-fails-to-push-down)
- [Match the data types on both sides of a join](#match-the-data-types-on-both-sides-of-a-join)
- [Extract semi-structured data once](#extract-semi-structured-data-once)
- [Do the work once](#do-the-work-once)
- [Aggregation](#aggregation)
- [Window functions](#window-functions)

## Filter as early as possible

A filter in the outermost `select` still requires the engine to consider everything beneath it, depending on how much it can push down. A filter in the import CTE, adjacent to the table, is unambiguous.

```sql
-- import CTE: named columns, filtered at the source
orders as (
    select
        order_id,
        customer_id,
        order_date,
        amount
    from {{ ref('<upstream_model>') }}
    where order_date >= '<boundary>'
      and is_deleted = false
),
```

Two effects, both real: fewer rows and fewer columns. On a columnar warehouse the column list matters as much as the row filter — `select *` in an import CTE reads every column of a wide table whether or not anything downstream uses them.

## Pre-filter the large side of a join

When joining a small dimension to a large fact table, compute the needed key set first and constrain the fact scan with it, including a range on whatever column the fact table is physically organized by.

```sql
-- give the engine something to skip on before it touches the large table
keys_in_scope as (
    select
        entity_id,
        min(effective_date) as earliest_date
    from {{ ref('<dimension_model>') }}
    where is_active = true
    group by entity_id
),

facts as (
    select
        events.entity_id,
        events.event_date,
        events.metric_value
    from {{ ref('<large_fact_model>') }} as events
    inner join keys_in_scope
        on events.entity_id = keys_in_scope.entity_id
        and events.event_date >= keys_in_scope.earliest_date  -- range predicate enables skipping
)
```

The date predicate is doing most of the work. An `in (subquery)` on the id alone gives the engine no range to prune on, so it often reads the whole fact table and filters afterward.

Be precise about *how* that predicate helps, because it is not the same mechanism as a literal bound. `events.event_date >= keys_in_scope.earliest_date` compares a column to another relation's column, so no engine can evaluate it while planning the scan. It pays off through a runtime filter — a semi-join reduction built from the small side and applied to the large scan while the query runs. Several engines build one, under different names and different eligibility conditions, and some do not build one at all; the per-engine detail is in [warehouse-layout.md](warehouse-layout.md). If the plan shows the large table fully scanned despite the predicate, no filter was built, and the fix is "Skipping needs a bound the planner can evaluate" below.

## Do not wrap the filtered column in a function

This one is nearly universal and it silently defeats every form of data skipping, because statistics are kept on the column, not on `f(column)`.

```sql
-- defeats pruning: the engine cannot compare stored min/max against a function result
where cast(event_time as varchar) like '2024%'
where date_trunc('day', event_time) = '2024-01-01'
where coalesce(event_date, '1900-01-01') >= '2024-01-01'

-- prunes: bare column, compared to a literal
where event_time >= '2024-01-01' and event_time < '2024-01-02'
where event_date >= '2024-01-01'
```

Transform the literal side, never the column side. Where a null needs handling, `where event_date >= '<date>' or event_date is null` preserves pruning on the first branch; `coalesce` does not.

## Skipping needs a bound the planner can evaluate

The most consequential and least-known rule here. Data skipping is decided by comparing a predicate against stored per-block statistics, and on most engines that decision is made *before* execution begins. A bound that only exists once the query is running is not available in time.

```sql
-- often reads the whole table: the bound is not known at planning time
where event_date = (select max(event_date) from {{ ref('<other_model>') }})

-- prunes: a literal, or an expression over constants and the current date
where event_date >= '<literal_date>'
where event_date >= current_date - 3
```

Snowflake's documentation states that it does not prune micro-partitions on a predicate containing a subquery, *even when the subquery returns a constant*. BigQuery behaves the same way for partition elimination and its guidance is to resolve the value first and pass it in. Treat "a subquery in the bound defeats static skipping" as the default assumption on any engine, and verify rather than hope.

In dbt the fix is to resolve the bound at compile time so a literal lands in the SQL:

```sql
{%- set bound_query %}
    select max(<date_column>) as boundary from {{ ref('<upstream_model>') }}
{%- endset %}
{%- if execute %}
    {%- set boundary = run_query(bound_query).columns[0][0] %}
{%- endif %}

select ...
from {{ ref('<large_model>') }}
where <date_column> >= '{{ boundary }}'
```

One extra round trip at compile time turns a full scan into a bounded one. The tradeoffs are real and worth stating when you use it: the value is fixed at compile time, so a long run works from a slightly stale bound, and `dbt compile` now requires warehouse connectivity. Where an incremental model already reads `max()` from itself, this is the same pattern with the same tradeoff.

## Why a filter fails to push down

A filter in an import CTE is unambiguous. A filter applied *above* something the optimizer cannot see through stays where you wrote it, and everything beneath it is computed in full first. These four blockers are engine-independent in cause, even though individual optimizers handle some of them:

| Blocker | Why the optimizer refuses | What to write instead |
|---|---|---|
| A window function between the filter and the table | Pushing a filter under a window changes what the window sees, so results could differ. Optimizers push it only when the filtered column appears in `partition by`, where discarding whole partitions provably cannot change any row's ranking. | Filter inside the CTE that holds the window, or make the filtered column part of `partition by`. |
| An outer join | A predicate on the null-producing side is not equivalent above and below the join: pushed down it removes rows, left above it also removes the manufactured nulls. | Put it in `on` when you mean "before the join", in `where` when you mean "after". These are different questions, not styles. |
| Aggregation | A condition on an aggregate cannot be evaluated before the aggregate exists. | Filter on grouping columns before aggregating; reserve `having` for conditions on the aggregates. |
| A non-deterministic or volatile expression | The optimizer cannot assume a second evaluation returns the same answer, so it will not relocate or duplicate it. | Resolve the value once and compare against the result. |

`union all` is the friendly case: a filter above it can generally be applied to each branch, so filtering a stack of unioned models is usually safe. `distinct`, deduplicating `union`, `limit` and `qualify` are less predictable. When it matters, do not reason about it — read the plan and check whether the scan beneath the filter got smaller. This is also the reason a chain of views can be slow for no visible reason: the filter is in the consumer, and something three views down blocks it.

## Match the data types on both sides of a join

Cheap to fix, invisible in review, paid on every row.

- **A type mismatch forces a cast on one side of the join.** Where that cast lands on the large input, every row is converted at runtime and any skipping that depended on the raw column is gone. Published Snowflake tests measured integer-to-integer joins running two to three times faster than the same join with a string on one side; a cast landing on the large probe input was consistently the worst case. The general effect — narrower fixed-width keys compare and hash faster — is not Snowflake-specific.
- **Prefer a narrow key type.** Integers beat strings; a hash stored as binary beats the same hash stored as hex text, which spends twice the bytes on the same information. Across a large fact table this decides how much memory the join's hash table needs, and memory is what decides whether it spills.
- **Do not join on a value extracted from a semi-structured column.** Extraction is per-row work, and the extracted expression is not a column the engine keeps statistics on.
- **Do not over-specify numeric precision.** Declaring more precision or scale than the data uses costs storage and arithmetic on some engines and nothing measurable on others. This is the weakest claim of the four — verify on your engine before changing a type for this reason alone.

None of this helps a query whose problem is volume. It matters when the join itself is the expensive operator.

## Extract semi-structured data once

Parsing is per-row work that repeats on every run, and again for every path referenced.

- **Do not re-parse a column that is already a structured type.** A parse call over something already stored as a variant, struct or map is pure waste, and it is easy to inherit.
- **Project the few fields that queries filter and join on into typed columns** in a staging model; leave the rest in the payload. On some engines a field promoted to its own column is read on its own, while a field left inside the document forces reading and traversing the document for every row.
- **Filter before expanding an array.** Expansion multiplies rows; a filter after it has already paid for every row produced.
- **Expand once into a model rather than repeatedly in consumers.** Three consumers each flattening the same array is triple the work and three chances to flatten it inconsistently.
- Values with no native representation in the source format — dates and timestamps in JSON — arrive as strings. Comparing or doing arithmetic on them is slower than on a typed column, and casting them in a filter defeats skipping as above.

## Do the work once

- **Deduplicate in one pass.** Where the warehouse supports `qualify`, `qualify row_number() over (...) = 1` replaces a rank-then-filter CTE pair and one extra pass over the data. `qualify` is available on Snowflake, BigQuery, Databricks, DuckDB, and Teradata; it does not exist on Postgres or Redshift, where the two-CTE form remains necessary.
- **Reference a CTE once, or materialize it.** A CTE referenced three times may be computed three times, depending on the engine. If it is expensive and reused, it wants to be its own model.
- **Aggregate before joining, not after.** Joining then grouping multiplies rows before reducing them. Reducing first is usually dramatically cheaper.

The CTE point deserves precision, because "CTEs are free" and "CTEs are optimization fences" are both repeated as universal truths and neither is. Engine behaviour differs, and it differs by version:

| Engine | Behaviour on a CTE referenced more than once |
|---|---|
| BigQuery | Non-recursive CTEs are inlined; each reference re-executes the underlying scan. Two references to a CTE over a large table is two scans and, on on-demand pricing, twice the bytes billed. |
| Postgres | Materialized by default before version 12, inlined by default from 12 onward when referenced once and side-effect free. `with ... as materialized` and `as not materialized` make it explicit. |
| Snowflake, Databricks, Trino, DuckDB, Redshift | Documented behaviour is that the optimizer decides. Reuse is possible but not promised, and it can change between versions. Do not build a cost argument on it. |

The safe conclusion is behavioural, not per-engine: if a CTE is expensive and referenced more than once, promote it to a model and know what it costs. If it is cheap, leave it inline for readability. Either way, verify against the plan rather than the folklore — and note that this is exactly why an ephemeral model referenced by several consumers can be more expensive than the same logic as a table.

## Aggregation

Aggregation is usually either trivial or the whole query, and the difference is cardinality.

- **Compute multiple grouping levels in one pass.** Detail, subtotal and grand total written as three `group by` queries stitched with `union all` scans the source once per branch. `grouping sets`, `rollup` and `cube` express the same thing in one scan. Supported on Snowflake, BigQuery, Databricks, Redshift, Postgres, Trino and DuckDB. `grouping()` identifies which level a row came from. Prefer explicit `grouping sets` over `cube`: `cube` over n columns produces 2^n groupings, most of which nobody asked for.
- **`count(distinct ...)` is the expensive aggregate.** Exact distinctness requires tracking every value seen, so memory grows with the number of distinct values, not the number of rows — which is why it is a frequent cause of spilling. Several `count(distinct)` expressions over different columns in one query multiply that state.
- **Approximate distinct counts trade a stated error for a large drop in memory and time.** The implementations are HyperLogLog variants. Snowflake documents an average relative error of about 1.6%, so a true 1,000,000 typically returns between roughly 983,800 and 1,016,200. Databricks documents a default maximum relative standard deviation of 5%, tunable. BigQuery's `APPROX_COUNT_DISTINCT` uses a fixed system precision, while its `HLL_COUNT` functions expose precision from 10 to 24 with a default of 15. Redshift, Postgres via an extension, and DuckDB offer their own equivalents.
- **Approximate results are not appropriate everywhere, and the boundary is a business question.** Directional dashboards and monitoring tolerate a few percent. Anything invoiced, reported externally, or reconciled against another system does not. Never substitute an approximation into an existing metric without saying so — a silent 1.6% change to a published number is a correctness incident that happens to be fast.
- **Sketches let you pre-aggregate and still combine later.** Where an engine exposes the intermediate sketch (Snowflake `hll_accumulate`/`hll_estimate`, BigQuery `HLL_COUNT.INIT`/`MERGE`, Databricks `hll_sketch_agg`/`hll_union_agg`), a daily model can store one sketch per day and any later period can be estimated by merging them — something a stored exact distinct count cannot do, because distinct counts do not sum. Merging sketches of different precisions degrades to the lowest precision involved.
- **A high-cardinality `group by` is the classic spill.** Grouping by a near-unique key produces almost as many groups as rows and gains nothing over the detail rows. Before enlarging compute, ask whether every column in the `group by` is needed.
- **`count(distinct a, b)` where the pairs are already unique is just `count(*)`.** The mistake is common and expensive, and both forms return the same number, so nothing catches it.

## Window functions

A window function is usually the cheapest way to express a per-group calculation — the alternative self-join is nearly always worse — but the sort underneath it is often the most memory-hungry operator in the query.

- **Every distinct window specification generally means its own sort.** Four windows with the same `partition by` and `order by` can be satisfied by one pass; four windows each ordered differently cannot. Consolidate onto as few specifications as the logic allows.
- **A named `window` clause makes the sharing explicit** and removes the risk of a typo silently creating a second specification. Supported on BigQuery, Databricks, Trino, DuckDB and Postgres. **Snowflake does not support it** — repeat the full `over (...)` there, or generate it from a Jinja variable so one definition produces identical text.
- **Frame defaults are not uniform, and the difference is silent.** With an `order by` present, aggregate window functions default to a running frame from the start of the partition to the current row; without one they cover the whole partition. Snowflake's documentation notes that its default for some ranking-adjacent functions does not follow the ANSI default, and recommends declaring frames explicitly. `range` frames also cost more than `rows` frames because peer groups must be resolved. State the frame you mean.
- **Partition cardinality decides the shape of the work.** A few enormous partitions are hard to parallelize and each one must fit in memory. Millions of tiny partitions mean per-partition overhead dominates. Either extreme presents as a slow window step.
- **`order by` in an inner CTE is usually pure cost.** Nothing downstream is required to preserve it, and it forces a full sort of the intermediate result. A sort with spill is one of the strongest signals a warehouse is undersized *or* that the sort should not exist. Order only in the outermost query, and only when a consumer needs it.
- **A window over all history cannot be computed from an increment.** This constrains incremental conversion, not just performance — see Step 5 in [SKILL.md](SKILL.md).
