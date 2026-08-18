# Keys, deduplication, and reshaping

Three operations that share a failure signature: they produce a result set of a plausible size that is subtly not the one you asked for, and the model's tests pass because the tests describe the shape rather than the content.

## Surrogate keys: what the macro actually does

Before deciding how to build a key, know what the standard implementation produces. `dbt_utils.generate_surrogate_key(['a', 'b'])` compiles to roughly:

```sql
md5(
    coalesce(cast(a as varchar), '_dbt_utils_surrogate_key_null_')
    || '-' ||
    coalesce(cast(b as varchar), '_dbt_utils_surrogate_key_null_')
)
```

Every consequence below follows from that one expression, and none of them are obvious from the call site.

### The key is a hash of string representations

**Recasting a grain column changes every key value in the model.** Nothing in the SQL of this model changed — an upstream column went from integer to decimal, or from `timestamp` to `date`, and its string representation changed with it. Consequences, in order of how expensive they are to discover:

- On an incremental model with `unique_key`, the new keys match nothing in the target. The merge inserts instead of updating, and the table now holds two rows per logical row. The uniqueness test catches this — if there is one, and if it is not bounded to recent data only.
- Any downstream model joining on this key breaks silently, matching nothing.
- Any external consumer holding the key as a reference loses it.

This is why a type change on a grain column is a breaking change even when the values are unchanged. See `dbt-breaking-changes`.

**The order of the array is part of the key.** `['a', 'b']` and `['b', 'a']` produce different hashes. Reordering the array during a tidy-up rewrites every key with no visible change in intent. Keep the array in a fixed order and treat it as an interface.

**Casting is dialect-dependent.** The macro casts through the adapter's string type, so the same logical values can hash differently on two platforms. That only matters if you compare keys across environments running different engines — but if you do, this is why they never match.

### Nulls

A null component becomes the sentinel string, so the key is non-null even when a grain column is null. That is better than the alternative — a hand-rolled concatenation yields null, and a null key silently defeats both the `unique` test and any merge — but it is not a licence to have nulls in the grain.

**A null in a grain column means the grain claim is false.** Two rows that differ only in that a value is present in one and absent in the other are, by the model's own definition, the same row. Test `not_null` on every grain column, not only on the generated key.

The legacy `surrogate_key()` macro, and the `surrogate_key_treat_nulls_as_empty_strings` variable that restores its behaviour, map null to the empty string instead. On a nullable string column that can also legitimately be empty, that collapses two distinct rows into one key. Do not enable the variable in a new project; if it is already set, know that it is why an empty-string row and a null row collide.

### Delimiter ambiguity is the real collision risk

Hash collisions are not the thing to worry about. A 128-bit digest over analytics-scale row counts has a collision probability far below every other risk in the pipeline.

Delimiter ambiguity is a genuine risk. The macro joins components with `-`, so values that themselves contain `-` can produce the same concatenated string from different inputs:

```
['a-', 'b']  ->  'a-' || '-' || 'b'  ->  'a--b'
['a', '-b']  ->  'a'  || '-' || '-b' ->  'a--b'
```

Identical hash, different rows. This is not theoretical for keys built from free-text codes, composite identifiers, region strings, or anything user-supplied. Two defences, both cheap:

- **Prefer grain columns whose values cannot contain the delimiter** — ids and dates, not descriptive text.
- **When a text column must be in the key, keep the `unique` test.** It is what turns a collision into a failed build rather than a lost row.

Concatenation without any delimiter is strictly worse — `'ab' || 'c'` equals `'a' || 'bc'` for every pair of adjacent components — which is the reason not to hand-roll the key at all.

### Rules

- **Include exactly the grain columns: all of them, and nothing else.** Missing one produces duplicates. Including a descriptive attribute produces a key that changes when the description changes, which breaks every merge that relies on it.
- **When the grain changes, the key changes with it** — in the same commit as the `group by` and the uniqueness test. This is the most common source of duplicate rows in an incremental model.
- **The key must be deterministic.** No `current_timestamp`, no random function, no `row_number()`, nothing that depends on run order or on which rows happened to be in scope. A key that changes between runs makes incremental merging impossible and makes the model non-reproducible.
- **Name it from `naming.surrogate_key_column`** and place it first in the `final` CTE.

### When not to generate one

A hashed key is not free: it is opaque in a query result, it has to be recomputed to be reproduced, and it introduces the failure modes above. Two alternatives are often better.

| Situation | Prefer |
|---|---|
| The entity already has a stable single-column identifier | The natural key. A dimension at one row per entity does not need a hash |
| The grain is composite and the model is not incremental | The composite natural key plus `dbt_utils.unique_combination_of_columns` on the column set |
| The grain is composite and the model is incremental, or consumers need one join column | A generated surrogate key |

`unique_combination_of_columns` is also the cheaper test on a large table than `unique` over a concatenation, which is the reason the package recommends it for that case.

## Deduplication

### The ordering must be total

```sql
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
                order by updated_at desc, <tiebreaker> desc
            ) as row_num
        from orders
    ) as ranked
    where row_num = 1
)
```

**If the `order by` can tie, the surviving row is arbitrary and can differ between runs.** Not "unlikely to differ" — engines parallelise, and the row that wins is whichever arrives first. The model becomes non-reproducible, two environments disagree, and a full refresh changes values that were supposed to be stable.

Making the ordering total:

- **Add a tiebreaker that is genuinely unique** within the partition. A source-supplied sequence, a file or batch identifier, an ingestion timestamp with higher resolution, or as a last resort the primary key itself. A second timestamp that ties whenever the first one does adds nothing.
- **State `nulls last` explicitly.** A null in the ordering column sorts differently per engine, and on at least one platform the default is a session parameter — see [`nulls-and-types.md`](nulls-and-types.md). A null `updated_at` sorting first will make the *least* current row win.
- **Do not order by a column the deduplication is meant to choose between** if it can be equal across the duplicates.

Verify it rather than assuming: run the model twice and compare, or check for ties directly.

```sql
-- how many partitions have a tie at the top of the ordering?
-- nonzero means the surviving row is arbitrary
select count(*)
from (
    select <partition_cols>, count(*) as tied
    from <relation>
    where (<partition_cols>, <order_cols>) in (
        select <partition_cols>, max(<order_cols>) from <relation> group by <partition_cols>
    )
    group by <partition_cols>
    having count(*) > 1
) as ties
```

Adapt the shape to the dialect — row-value comparison is not universal — or compute it with a window function.

### `qualify` removes the subquery, and is not portable

| Platform | `qualify` |
|---|---|
| Snowflake, BigQuery, Databricks, Redshift, DuckDB, Teradata | Supported |
| Postgres, MySQL, SQL Server, SQLite | Not supported — use the subquery or CTE form |

On BigQuery, a query whose only filter is `qualify`, with no `where`, `group by`, or `having`, can be rejected; adding `where true` satisfies the requirement. Check `project.warehouse` before using `qualify`, and use the portable subquery form in any project or macro that targets more than one engine.

`dbt_utils.deduplicate(relation=..., partition_by=..., order_by=...)` generates the dialect-appropriate form. Its own documentation carries the same warning: if the `order_by` ties, the surviving row is nondeterministic.

### Deduplication usually hides a defect

Before writing it, answer whether the duplicates should exist.

- **They should** — the source is an append-only change log and you want the latest state. Deduplication is the correct transformation. Say so in a comment, because the next reader cannot tell this case from the next one.
- **They should not** — the loader double-delivered, or a join upstream fanned out. The fix belongs upstream, and the duplicates deserve a test that fails rather than a `row_number()` that absorbs them. Silently deduplicating means the loader defect is now invisible and permanent.

Also decide *where* to deduplicate. Doing it once, as early as the layer boundaries allow, means every consumer inherits it. Doing it in three consumer-facing models means three chances to choose a different tiebreaker and produce three different answers.

## Unions

```sql
unioned as (
    select
        order_id,
        order_date,
        amount,
        'system_a' as source_system
    from system_a_orders

    union all

    select
        order_id,
        order_date,
        amount,
        'system_b' as source_system
    from system_b_orders
)
```

**`union` matches by position, not by name.** Two branches with the same column names in a different order compile, run, and produce plausible garbage — a date column populated with amounts. Nothing in dbt or the warehouse objects, because the types happened to be compatible or coercible.

Defences, in order of strength:

1. **List columns explicitly in every branch, in the same order.** Never `select *` in a union branch: an upstream column addition changes one branch's positional layout and not the other's.
2. **Compare the compiled column lists** when the branches are long. A misalignment 30 columns in is invisible by inspection.
3. **`dbt_utils.union_relations()`** aligns by name, fills missing columns with null, and adds a relation indicator. It costs an introspective query at compile time — which means the model's schema depends on the warehouse's current state, and, importantly, **a model using it cannot be unit tested**, because the column list cannot be resolved against mocked inputs. See `dbt-unit-tests`.

Also:

- **`union all`, not `union`.** `union` deduplicates, which requires a sort or hash over the whole result and is usually accidental. If deduplication is intended, do it explicitly with the ordering rules above, so the surviving row is chosen rather than arbitrary.
- **Add a literal source column.** Without it, no consumer can attribute a row, reconcile a total against one system, or exclude a system that had an outage. It is nearly always part of the grain, and therefore part of the key.
- **Types must agree, not merely coerce.** A `varchar` in one branch and a numeric in the other will silently widen on some engines, giving a column whose type depends on which branch had data. Cast both branches to the intended type in the source-facing layer.
- **Row count is additive and is the cheapest check.** `count(*)` of the union must equal the sum of the branch counts. If it does not, the union is not `union all`, or a branch has a filter you did not intend.

Combining the same concept from several systems raises questions beyond the mechanics — conflicting definitions, differing grains, which system wins. That is `dbt-unifying-sources`.

## Pivot and unpivot

### Pivot

Pivoting turns row values into columns, which means **the model's schema becomes a function of its data.** That is the whole problem.

```sql
-- explicit value list: schema is fixed and reviewable
select
    order_date,
    sum(case when status = 'shipped'   then 1 else 0 end) as shipped_count,
    sum(case when status = 'cancelled' then 1 else 0 end) as cancelled_count
from orders
group by order_date
```

```sql
-- introspective: schema changes when the data changes
select
    order_date,
    {{ dbt_utils.pivot('status', dbt_utils.get_column_values(ref('<orders>'), 'status')) }}
from {{ ref('<orders>') }}
group by order_date
```

The second form runs a query at compile time to discover the values. Consequences:

- A new value appearing in the source **adds a column** to the model, and a value disappearing **removes** one. Downstream models referencing the removed column break; a contracted model fails its contract; a BI consumer loses a field.
- Compilation now depends on warehouse state, so the same commit compiles to different SQL at different times, and cannot compile at all without a connection.
- The model cannot be unit tested, for the same reason `union_relations` cannot.

**Use the explicit list for anything consumer-facing or contracted.** Pair it with an `accepted_values` test on the source column, so a new value produces a failing test — a clear instruction to add a column deliberately — rather than a schema that mutates on its own. Use the introspective form only for exploratory or internal models where a moving schema is acceptable, and say so in the description.

Some engines have a native `pivot` operator with its own syntax and its own requirement to list values. It is not portable; check `project.warehouse`.

### Unpivot

Unpivoting is the safer direction — wide to long — but it unifies types.

- **Every unpivoted column collapses into one value column, so they must share a type.** Mixing a decimal, an integer, and a boolean forces a cast to something that holds all three, usually a string, and downstream arithmetic then needs casting back. Unpivot columns that are genuinely the same kind of measure.
- **`dbt_utils.unpivot()` renders booleans as the strings `'true'`/`'false'`.** Anything comparing the result to a boolean will not match.
- **The field-name column is a new grain column.** It belongs in the surrogate key, and the model's grain statement has to change to include it.
- **Row count multiplies by the number of unpivoted columns**, minus whatever nulls the implementation excludes. Predict it and check it.

## Checklist

- [ ] Key contains exactly the grain columns, in a fixed order, named per contract, placed first
- [ ] `not_null` tested on every grain column, not only on the generated key
- [ ] Key is deterministic — no timestamps, no randomness, no window functions
- [ ] `unique` test retained where any key component is free text (delimiter ambiguity)
- [ ] No hand-rolled concatenation as a key
- [ ] Natural or composite key considered before generating a hash
- [ ] Deduplication `order by` proved total, with an explicit tiebreaker and explicit null ordering
- [ ] `qualify` used only if valid on `project.warehouse`
- [ ] Deduplication justified as the correct transformation, or the upstream defect reported instead
- [ ] Union branches list columns explicitly, in the same order, with matching types
- [ ] `union all` used unless deduplication is deliberate
- [ ] Source-system literal present and included in the key
- [ ] Union row count reconciled against the sum of branch counts
- [ ] Pivot uses an explicit value list for any contracted or consumer-facing model, with `accepted_values` on the source column
- [ ] Unpivot: types unified deliberately, field-name column added to the grain and the key, row count predicted

## Failure modes

1. **A type change upstream rewriting every surrogate key.** The incremental merge stops matching and inserts duplicates instead of updating. Nothing in this model's diff explains it.
2. **A grain column added to the `group by` and not to the key array.** Duplicate keys; on a `merge` incremental, also corrupted existing rows.
3. **A reordered key array.** Every key value changes during what was described in the pull request as a formatting change.
4. **A non-total deduplication ordering.** The surviving row differs between runs and between environments, and a full refresh changes historical values that consumers had already reported.
5. **A null in the ordering column winning the deduplication** because the engine's default sorted nulls first.
6. **Positionally misaligned union branches.** Compiles, runs, and populates columns with the wrong values. No test detects a swap between two columns of the same type.
7. **`union` instead of `union all`.** Legitimate duplicate rows silently removed, totals short, and a sort over the whole result set added to the cost.
8. **An introspective pivot dropping a column** when a value stopped appearing in the source. Downstream models and dashboards break, and the compiled SQL differs from the last run with no code change.
9. **Deduplication used to absorb a loader defect.** The duplicates stop being visible, the loader keeps producing them, and the workaround becomes permanent because nothing records that it is one.
