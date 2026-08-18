# Consumer-facing (mart) models

The layer that BI tools, analysts, and downstream systems query directly. Read `layers[]` for this layer's names, prefixes, materializations, and whether it is `terminal`.

This layer has a property the others do not: **its column names are part of a contract with people outside the dbt project.** A rename here breaks dashboards. That constraint should change how you write it.

## Two kinds of model

Most projects distinguish, whatever they call them:

| Kind | Content | Grain | Typical materialization |
|---|---|---|---|
| Dimension / entity | Descriptive attributes of one entity | One row per entity | `table` or `view` |
| Fact / report / aggregate | Measures at a time-based grain | One row per period × dimensions | `table` or `incremental` |

Read the actual prefixes and materializations from `layers[]`. If the contract does not distinguish them, the distinction still holds conceptually and is worth stating in the model's description.

## Dimension shape

```sql
with

customers as (
    select
        customer_id,
        customer_name,
        customer_status,
        created_at
    from {{ ref('<staging_customers>') }}
),

regions as (
    select
        region_id,
        region_name
    from {{ ref('<staging_regions>') }}
),

joined as (
    select
        customers.customer_id,
        customers.customer_name,
        customers.customer_status,
        regions.region_name,
        customers.created_at
    from customers
    left join regions
        on customers.region_id = regions.region_id
),

final as (
    select
        customer_id,
        customer_name,
        customer_status,
        region_name,
        created_at
    from joined
)

select * from final
```

- **The natural key is the primary key.** A dimension at one row per entity does not need a surrogate key; the entity's identifier is already unique and is what facts join on. Adding a hashed key here just gives consumers a second key to be confused by.
- **Test uniqueness on it.** A dimension that fans out silently multiplies every fact that joins to it — this is the highest-leverage uniqueness test in the project.
- **Slowly-changing history is a different problem.** If consumers need attribute values as of a past date, that is a snapshot, not a dimension. See `dbt-snapshots`.
- **One dimension per concept, shared by every fact that needs it.** Two models describing the same entity with different attribute logic will disagree, and the disagreement surfaces as two dashboards showing different counts for the same thing. If two facts need the entity at different grains, the second one is an aggregate of the first, not an independent definition.
- **Decide what an unmatched fact row joins to.** A fact whose foreign key is null, or points at an entity the dimension does not have, disappears from any inner join and is silently excluded from every total computed that way. The options are to leave it null and test the referential integrity, or to carry an explicit "unknown" member in the dimension. The first is right by default — see the note on not coalescing identifiers in [`nulls-and-types.md`](nulls-and-types.md) — but if consumers routinely inner-join, an explicit unknown member is the only way their totals stay complete. Choose deliberately and document which.

## Fact / report shape

```sql
{{ config(
    materialized = 'incremental',
    unique_key = '<naming.surrogate_key_column>',
    on_schema_change = 'fail',
) }}

with

daily_orders as (
    select
        order_date,
        region,
        product_id,
        order_count,
        total_amount
    from {{ ref('<intermediate_daily_orders>') }}
    {% if is_incremental() %}
        where order_date >= (select max(order_date) from {{ this }})
    {% endif %}
),

products as (
    select
        product_id,
        product_name,
        product_category
    from {{ ref('<dim_products>') }}
),

enriched as (
    select
        daily_orders.order_date,
        daily_orders.region,
        daily_orders.product_id,
        products.product_name,
        products.product_category,
        daily_orders.order_count,
        daily_orders.total_amount
    from daily_orders
    left join products
        on daily_orders.product_id = products.product_id
),

final as (
    select
        {{ dbt_utils.generate_surrogate_key([
            'order_date',
            'region',
            'product_id',
        ]) }} as <naming.surrogate_key_column>,
        order_date,
        region,
        product_id,
        product_name,
        product_category,
        order_count,
        total_amount
    from enriched
)

select * from final
```

The incremental config above is deliberately minimal; the full decision set — strategy, predicates, boundary, schema change, full-refresh safety — is in `dbt-incremental-models`. Do not choose a strategy from this file.

**Not every report needs to be incremental.** If a full rebuild is fast and cheap, `table` is the better choice: no boundary logic to get wrong, no stale rows, no backfill procedure, and every run's output matches the source by construction. Convert to incremental when the rebuild is demonstrably the reason the build is slow — measured, not assumed. Every incremental model is a permanent maintenance obligation, and it is the materialization that most of this library's failure modes belong to.

## Grain is the whole design

Write the grain down before anything else, and keep three things consistent with it:

1. the `group by` in the aggregate that feeds it,
2. the surrogate key column array,
3. the uniqueness test in the YAML.

When these three disagree, the model produces duplicate rows, and if it is incremental with `merge` the duplicates also corrupt existing rows. Every one of the three must change together when the grain changes — and changing the grain of a model consumers already query is a breaking change, not an edit. See `dbt-breaking-changes`.

### Prefer the atomic grain

When choosing between "one row per order" and "one row per order line", the finer grain answers more questions. Dimensional modelling has held this position for decades for a specific reason: an aggregate can always be derived from atomic detail, and no aggregate can answer a question about a dimension it summed away. A model built at a summarised grain has to be rebuilt — as a breaking change, with consumers to migrate — the first time somebody asks a question one level down.

The counterweight is cost, and it is real: atomic detail is larger to store, slower to scan, and slower to query. The right resolution is usually **atomic detail in the transformation layer, with summarised models built on top of it for the consumers that need speed** — not a single summarised model that discards the detail. See `dbt-performance-tuning` for measuring whether the aggregate is worth having, and `dbt-restructuring-dags` if the detail model needs extracting from an existing summary.

### Never mix grains in one model

Two different grains in the same relation means every consumer must filter to one of them, and the ones that do not will double-count. This most often arrives as a "total" row unioned into a detail model, or a coarse-grain measure joined onto a fine-grain fact. Both belong in separate models.

### Classify each measure's additivity

| Kind | Sums across | Examples | How to model it |
|---|---|---|---|
| Fully additive | Every dimension, including time | Counts, amounts, quantities | Store it and let consumers sum freely |
| Semi-additive | Every dimension **except** time | Balances, inventory levels, active-subscription counts | Store it, and state in the description that summing over time is meaningless. Consumers need a point-in-time or averaged aggregation |
| Non-additive | Nothing | Ratios, rates, percentages, averages of averages | **Store the numerator and denominator as separate additive columns** and let the consumer divide |

The non-additive row is the one that causes visible damage. A stored ratio column, averaged across rows by a BI tool, produces an average of ratios rather than a ratio of totals — a different and wrong number. Storing the two additive components makes the correct aggregation the easy one, and this is the standard dimensional-modelling remedy rather than a local preference.

State the classification in each measure's description. Nothing else in the project can express it, and a consumer cannot infer it from the column name or type.

## Enrichment: join descriptive attributes, or let consumers join?

Both are defensible.

**Denormalize into the fact** when the consuming tool cannot join well, or when the same join is repeated in every dashboard. Cost: the fact carries attribute values frozen as of build time, and adding an attribute means rebuilding.

**Leave the join to the consumer** when the dimension is large, changes often, or the BI layer models joins natively. Cost: every consumer must get the join right, and some will not.

Whichever you choose, be consistent within the layer. A layer where half the facts are denormalized and half are not forces every consumer to check which kind they have.

## Column names are an external contract

This layer's column names are read by people who will never open the SQL, and are referenced by dashboards, reverse-ETL syncs, and spreadsheets outside the repository. That changes the cost of getting one wrong.

- **Rename before anyone consumes it, not after.** A rename here is a breaking change requiring coordination; before first publication it is free. See `dbt-breaking-changes`.
- **State units and scales in descriptions.** A rate column that is a decimal in one model and a percentage in another will be formatted wrongly in a dashboard, and the dashboard is where people will see it.
- **Do not encode the source system in the name.** It leaks an implementation detail into a name that consumers will hardcode, and it becomes wrong the day the source changes.
- **Avoid bare ambiguous names** — `id`, `name`, `type`, `status`, `date`, `value`. Prefix with the entity so the column is unambiguous once a consumer joins two of these models together.

The general naming guidance is in [`structure.md`](structure.md); what is specific to this layer is that the names are published, so they are worth more deliberation and are considerably more expensive to change.

## Terminal layers

If `layers[].terminal` is true for this layer, nothing may `ref()` these models.

The rule exists because a terminal layer is where output is shaped for consumption — column names chosen for readability, attributes denormalized, filters applied for a specific audience. Building on top of that couples your model to presentation decisions that will change without anyone considering you a stakeholder.

```bash
# before adding a ref() to a model in a terminal layer, check whether it is one
grep -rn "ref('<model>')" models/
```

If you need the data that a terminal model contains, reference its upstream transformation model instead. If that model does not exist because the logic lives inside the terminal model, extract it — see `dbt-restructuring-dags`.

## Load metadata

A build timestamp column is cheap and repays itself the first time someone asks whether a number is stale:

```sql
current_timestamp as loaded_at
```

Keep the name identical across the layer. Note that on an incremental model this records when the **row** was last written, not when the model last ran — which is usually the more useful of the two, but say which one you mean in the description.

## Checklist

- [ ] Grain stated, and consistent across `group by`, surrogate key array, and uniqueness test
- [ ] Grain chosen deliberately — atomic unless a measured cost reason justifies summarising, and never two grains in one model
- [ ] Every measure classified additive / semi-additive / non-additive, with the classification in its description
- [ ] Non-additive measures stored as numerator and denominator, not as a precomputed ratio
- [ ] Dimension: natural key is the PK and is tested unique; one dimension per concept
- [ ] Unmatched-fact handling decided: null plus a referential test, or an explicit unknown member
- [ ] Fact: surrogate key contains exactly the grain columns, named per contract, placed first
- [ ] Enrichment approach consistent with the rest of the layer
- [ ] Terminal-layer rule checked before adding any `ref()` to a model in this layer
- [ ] Column names chosen deliberately — they are a contract with consumers; units and scales in descriptions
- [ ] Incremental configuration decided using `dbt-incremental-models`, not copied
- [ ] Load metadata column present and consistently named
- [ ] Built, and duplicate-key query returns zero rows
- [ ] Row count reconciled against the upstream model it aggregates
