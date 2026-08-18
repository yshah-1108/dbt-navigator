# Transformation (intermediate) models

The layer between source-facing models and consumer-facing models. Read `layers[]` for this layer's name, prefix, path, and materialization. With no contract, apply this to any model that reads other models and is not itself consumed by BI, and say the guidance is generic.

## When a model belongs here

- **Two or more source-facing models must be combined** — a join, a union, or a lookup enrichment.
- **A grain change is needed** — event level to daily, row level to per-entity, or a deliberate fan-out from a coarse grain to a fine one.
- **Logic is shared by more than one consumer** — putting it here means one definition instead of two that drift.
- **Deduplication or cleanup requires logic** beyond what the source-facing layer should carry.
- **A particularly complex operation should be isolated** so the consumer-facing model that uses it stays readable and the complex part can be tested on its own.

dbt's own structural guidance describes the shape this produces: the DAG narrows as it moves right, because the transformation layer combines many narrow source-conformed concepts into fewer wide business-conformed ones. Several inputs to a model is expected. Several *outputs* from a transformation model is a signal that it is doing more than one job.

## When it does not

If exactly one consumer-facing model will ever use the logic, putting it in a separate model adds a node, a file, a YAML entry, and a build step in exchange for nothing. Inline it and split later if a second consumer appears.

The opposite failure is more common though: logic duplicated in three consumer-facing models, which then diverge by a filter nobody notices. Two consumers is the threshold worth extracting for.

## Shape

```sql
with

orders as (
    select
        order_id,
        customer_id,
        order_date,
        order_status,
        amount
    from {{ ref('<staging_orders>') }}
    where not is_deleted
),

customers as (
    select
        customer_id,
        customer_name,
        region
    from {{ ref('<staging_customers>') }}
),

joined as (
    select
        orders.order_id,
        orders.order_date,
        orders.order_status,
        orders.amount,
        orders.customer_id,
        customers.customer_name,
        customers.region
    from orders
    left join customers
        on orders.customer_id = customers.customer_id
),

aggregated as (
    select
        order_date,
        region,
        count(distinct order_id) as order_count,
        sum(coalesce(amount, 0)) as total_amount
    from joined
    group by order_date, region
),

final as (
    select
        order_date,
        region,
        order_count,
        total_amount
    from aggregated
)

select * from final
```

`group by` style comes from `sql_style.group_by_style` and must be valid on `project.warehouse` — `group by all` does not exist on Postgres, and on Trino its `ALL` is a grouping-set modifier rather than column inference, so it groups differently than intended.

## The three things that go wrong here

### 1. Fan-out from a non-unique join key

This layer is where row counts silently multiply. Before writing a join, establish that the right side is unique on the join key:

```sql
select <join_key>, count(*)
from <right_side_relation>
group by <join_key>
having count(*) > 1
```

If it is not unique, choose deliberately: deduplicate the right side first, aggregate it to the join key, or accept the new grain and update the stated grain and the surrogate key to match. Silently accepting the fan-out is how every downstream sum becomes wrong.

The full procedure — classifying cardinality, ruling out many-to-many, measuring the match rate, and predicting the row count so the build can be checked against it — is in [`joins.md`](joins.md). Aggregating the right side to the join key in its own CTE, rather than joining and then undoing the duplication with `count(distinct ...)`, is covered there too.

### 2. `left join` turned into an inner join by a `where` clause

```sql
-- unmatched rows are dropped: this is an inner join
from orders
left join customers on orders.customer_id = customers.customer_id
where customers.region = 'north'

-- unmatched rows are kept
from orders
left join customers
    on orders.customer_id = customers.customer_id
    and customers.region = 'north'
```

Both are legitimate; only one is usually what was meant.

### 3. Aggregating nulls

`sum(amount)` over all-null input returns null, not zero, and the null propagates through every downstream calculation. Wrap metrics in `coalesce(..., 0)` when null genuinely means zero. Do not do this to identifiers — a null foreign key is a data quality signal, and coalescing it to a sentinel creates a permanent fake dimension member.

Also note `count(column)` skips nulls while `count(*)` does not. Choose the one you meant.

## Deduplication

Two portable forms. Prefer whichever the surrounding project already uses.

```sql
-- window function: works everywhere
deduplicated as (
    select
        order_id,
        customer_id,
        amount,
        updated_at
    from (
        select
            *,
            row_number() over (
                partition by order_id
                order by updated_at desc
            ) as row_num
        from orders
    ) as ranked
    where row_num = 1
)
```

Some warehouses offer a `qualify` clause, which removes the subquery. Check `project.warehouse` before using it — it is not universal.

Two things to get right:

- **The `order by` must be deterministic.** Ordering by a timestamp that ties across duplicate rows makes the surviving row arbitrary and different between runs. Add a tiebreaker column that is genuinely unique within the partition, and state `nulls last` explicitly — the default differs per engine.
- **Deduplicating here hides a source problem.** If duplicates should not exist, the fix belongs upstream and the duplicates deserve a test, not silent removal.

`dbt_utils.deduplicate()` generates the dialect-appropriate form. Determinism, how to check for ties, `qualify` portability, and deciding where in the DAG to deduplicate are in [`keys-and-reshaping.md`](keys-and-reshaping.md).

## Unions

When combining models that represent the same entity from different systems:

```sql
unioned as (
    select
        order_date,
        order_id,
        'system_a' as source_system,
        amount
    from system_a_orders

    union all

    select
        order_date,
        order_id,
        'system_b' as source_system,
        amount
    from system_b_orders
)
```

- **`union all`, not `union`**, unless deduplication is genuinely intended. `union` sorts and deduplicates, which is expensive and usually accidental.
- **Add a literal source column.** Without it, no consumer can attribute a row, reconcile a total against one system, or exclude a system that had an outage.
- **Column lists must match in order and type.** A positional union with two columns transposed compiles and produces plausible garbage. List columns explicitly in both branches, in the same order.
- **The source column is usually part of the grain**, so it belongs in the surrogate key.
- **Row count is additive.** `count(*)` of the union must equal the sum of the branch counts — the cheapest check available here.

`dbt_utils.union_relations()` aligns by name rather than position, at the cost of an introspective query that makes the model impossible to unit test. That tradeoff, and pivot/unpivot, are in [`keys-and-reshaping.md`](keys-and-reshaping.md). Reconciling conflicting definitions across systems is `dbt-unifying-sources`.

## Materialization

Read `layers[].materialization`. When the contract allows a choice:

| Situation | Choose | Why |
|---|---|---|
| Cheap to compute, few consumers | `view` | No storage, always current, no build step |
| Expensive, many consumers | `table` | Compute once instead of once per consumer |
| Large and time-partitioned, grows continuously | `incremental` | Process only new data — see `dbt-incremental-models` |
| Reused only within one model | `ephemeral` | Inlined as a CTE; no object created |

Two cautions. A chain of views is re-executed in full by every consumer, and a five-deep view chain can be dramatically more expensive than one materialized node in the middle. And `ephemeral` cannot be inspected or tested directly — the object does not exist — so it makes debugging harder. Do not use it for anything you might need to query. The full tradeoff table for `ephemeral`, including its effect on unit tests and on where compilation errors surface, is in [`structure.md`](structure.md).

Whichever you choose, base it on a measurement and report the number. See `dbt-performance-tuning`.

## Checklist

- [ ] Justified as shared logic, a grain change, or a combination — not a pass-through
- [ ] Reads only via `ref()`; no `source()` call
- [ ] `layers[].may_reference` respected
- [ ] Right side of every join verified unique on the join key, or fan-out accepted deliberately
- [ ] Row count predicted before building and compared after
- [ ] `left join` filters placed in `on` rather than `where` where unmatched rows must survive
- [ ] Metrics coalesced; identifiers left null
- [ ] Deduplication `order by` is deterministic, with an explicit tiebreaker and explicit null ordering
- [ ] Unions use `union all`, explicit matching column lists, and a source column; row count reconciled against branch counts
- [ ] Materialization choice justified by a measurement
- [ ] Grain restated after the transformation and verified by a duplicate-key query
- [ ] Measures re-checked for additivity if the grain changed
