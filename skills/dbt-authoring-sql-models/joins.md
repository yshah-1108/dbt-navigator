# Join safety: proving the grain survives

Almost every "the numbers doubled" incident is a join. The join itself is rarely wrong — what is wrong is that nobody established the cardinality of the right side before writing it, so a one-to-many relationship was treated as one-to-one and the row count multiplied silently.

Nothing in dbt warns you about this. A fan-out join compiles, builds, and passes every `not_null` test in the project. The only automated defence is a uniqueness test on the model's key, and that only fires if the key matches the stated grain.

This document is the procedure that prevents it, plus the join constructs that behave differently from how they read.

## The order of operations

1. State the grain of the output. One sentence.
2. For each join, classify the relationship: 1:1, many:1, 1:many, or many:many.
3. Prove the classification with a query. Do not infer it from a column name ending in `_id`.
4. Only then write the join.
5. After building, compare the row count against the pre-join count and confirm the difference is the one you predicted.

Step 3 is the one that gets skipped, and step 5 is what catches you having skipped it.

## Classify the relationship, then prove it

| Relationship | Effect on row count | Safe by default? |
|---|---|---|
| many:1 (right side unique on the key) | Unchanged | Yes — this is the intended shape of an enrichment join |
| 1:1 | Unchanged | Yes |
| 1:many (right side has repeats) | Multiplies | No — the grain changes |
| many:many | Multiplies by the product | No — almost always a modelling error |

**The only question that matters is whether the right side is unique on the join key.** If it is, the join cannot change the row count. If it is not, it will.

```sql
-- is the right side unique on the join key?
-- zero rows returned = safe to join without changing the grain
select
    <join_key>,
    count(*) as row_count
from <right_side_relation>
group by <join_key>
having count(*) > 1
```

If that returns rows, look at how many and how bad before deciding what to do:

```sql
select
    count(*)                                    as total_rows,
    count(distinct <join_key>)                  as distinct_keys,
    count(*) - count(distinct <join_key>)       as excess_rows,
    max(<join_key>_count)                       as worst_case_multiplier
from (
    select <join_key>, count(*) over (partition by <join_key>) as <join_key>_count
    from <right_side_relation>
) as counted
```

`worst_case_multiplier` is the factor by which the worst-affected left row will be duplicated. A value of 400 on a metric column is how a total ends up 400 times too large for one entity and correct for every other one — which reads as a data problem in one segment rather than as a join bug, and therefore gets investigated in the wrong place.

### Detecting many-to-many before writing it

A many-to-many join produces the cross product within each key. Check both sides:

```sql
-- run the uniqueness probe above against BOTH relations.
-- if neither is unique on the key, the join is many:many
```

Many-to-many between two fact-like relations is nearly always a modelling error rather than a grain to accept. The usual causes are a missing bridge relation, a join key that is not actually a key (a status or a type column), or a join that is missing a second key column — a date, a version, a source system. Adding the missing column to the `on` clause is the fix; deduplicating one side to make the symptom go away is not.

### Match rate: the other half of the check

Uniqueness tells you whether rows multiply. Match rate tells you whether rows disappear or arrive unenriched.

```sql
select
    count(*)                                                    as left_rows,
    count(<right_alias>.<join_key>)                             as matched_rows,
    count(*) - count(<right_alias>.<join_key>)                  as unmatched_rows
from <left_relation> as <left_alias>
left join <right_relation> as <right_alias>
    on <left_alias>.<join_key> = <right_alias>.<join_key>
```

Run this as a `left join` even when you intend an `inner join`. It tells you exactly how many rows the inner join will drop, before you drop them. A 3% unmatched rate might be acceptable; discovering after the fact that it was 40% is how a model ships missing nearly half its data.

An unmatched rate of exactly 0% is also worth a second look — it sometimes means the join key is derived from the same expression on both sides and the join is proving nothing.

## Row count arithmetic as an acceptance test

Before building, predict the row count. After building, check it.

| Join intent | Predicted output rows |
|---|---|
| Enrichment (many:1) | Exactly the left side's row count |
| Filtering via `inner join` | Left rows minus the unmatched count measured above |
| Deliberate fan-out | Left rows × the average multiplier, and the new grain is stated |

```sql
-- after building
select count(*) from <db>.<schema>.<model>
```

If the number does not match the prediction, stop. Do not adjust the prediction to match the output. This is the single cheapest verification in this document and it catches the failure mode that costs the most.

## When fan-out is what you want

Fan-out is not always a bug. Expanding one row per group into one row per member — an order into its items, a subscription into its billing periods, a date range into individual days — is a legitimate transformation. What makes it safe is that it is declared:

1. The stated grain changes and the model description changes with it.
2. The surrogate key gains the column that distinguishes the new rows.
3. The uniqueness test is updated to the new key.
4. Any measure that was additive at the old grain is checked for whether it is still additive at the new one. A total that is repeated on each fanned-out row will be double-counted by every consumer that sums it.

Point 4 is the one that is usually missed. If an order total is repeated on all five of an order's items, `sum(order_total)` is now five times too large, and no test will say so. Either allocate the measure across the rows, or leave it out of the fanned-out model and keep it at its original grain.

## Aggregate before joining

When the right side is not unique on the key and you want a summary rather than a fan-out, aggregate it to the join key in its own CTE first:

```sql
order_items_per_order as (
    select
        order_id,
        count(*)                        as item_count,
        sum(coalesce(item_amount, 0))   as item_amount_total
    from order_items
    group by order_id
),

joined as (
    select
        orders.order_id,
        orders.ordered_at,
        order_items_per_order.item_count,
        order_items_per_order.item_amount_total
    from orders
    left join order_items_per_order
        on orders.order_id = order_items_per_order.order_id
)
```

Two reasons, and only one of them is correctness. The aggregate CTE is unique on the key by construction, so the join cannot fan out. And aggregating before the join means the join processes one row per key rather than every detail row — which is the shape most engines execute more cheaply, and is what dbt Labs' published style guide recommends.

The anti-pattern is joining first and aggregating afterwards with `count(distinct ...)` to undo the duplication. It sometimes produces the right count, it never produces the right `sum()`, and it hides the fan-out from the next reader.

## Join types: what they actually do

| Construct | Behaviour | Verdict |
|---|---|---|
| `inner join` | Rows matching on both sides | Fine, once the drop rate is measured |
| `left join` | All left rows; right columns null when unmatched | The default for enrichment |
| `right join` | A `left join` with the tables in the confusing order | Rewrite it — every reader inverts it mentally and some do so wrongly |
| `full outer join` | All rows from both sides | Legitimate for reconciliation; needs `coalesce` on the key and a source indicator per side |
| `cross join` | Cartesian product | Only with an explicit reason — usually a date spine or a parameter grid |
| `from a, b where ...` | An implicit inner join | Never. A dropped `where` predicate turns it into a cross join with no syntax error |
| `using (col)` | Joins and collapses the column | Prefer `on`. `using` hides which columns are compared and yields one merged column, which surprises readers and breaks `select`-list qualification |

**`full outer join` has a trap worth naming.** After a full outer join, the join key is null on the side that did not match, so `select a.customer_id` loses half the keys. Use `coalesce(a.customer_id, b.customer_id) as customer_id`, and add a flag per side (`a.customer_id is not null as in_source_a`) so a reconciliation query can attribute each row.

## `left join` plus a `where` clause

Filtering the right side of a `left join` in the `where` clause silently converts it to an inner join, because the null produced by an unmatched row fails the predicate. The two forms and when each is right are covered in [`intermediate.md`](intermediate.md).

The inverse of that mistake is a useful pattern rather than a bug:

```sql
-- anti-join: left rows with NO match on the right
from orders
left join <excluded_customers> as excluded
    on orders.customer_id = excluded.customer_id
where excluded.customer_id is null
```

For this to be correct, the column tested `is null` must be one that can never legitimately be null in the right relation — the join key is the safe choice. Testing a nullable descriptive column instead returns matched-but-null rows as well as unmatched ones, and the two are indistinguishable in the output.

## Semi-joins and anti-joins

A semi-join answers "does a match exist?" without bringing columns across, and therefore **cannot fan out**. That property is why it is the right tool for filtering.

| Intent | Construct | Fan-out risk | Null-safe |
|---|---|---|---|
| Keep rows that match | `where exists (select 1 from ... where ...)` | None | Yes |
| Keep rows that match | `where <col> in (select ... )` | None | Yes for the `in` case |
| Keep rows that match, and you need the right side's columns | `inner join` | Yes if the right side is not unique | n/a |
| Exclude rows that match | `where not exists (select 1 from ... where ...)` | None | **Yes** |
| Exclude rows that match | `where <col> not in (select ... )` | None | **No — see below** |
| Exclude rows that match | `left join ... where <right_key> is null` | None | Yes |

```sql
-- semi-join: orders from customers that exist, without joining the customer table
where exists (
    select 1
    from customers
    where customers.customer_id = orders.customer_id
)

-- anti-join: orders whose customer does not exist
where not exists (
    select 1
    from customers
    where customers.customer_id = orders.customer_id
)
```

### `not in` with a nullable subquery returns nothing

If the subquery yields even one null, `not in` is never true for any row, and the query returns zero rows. This is correct three-valued logic and it is the most consequential null trap in SQL, because the result is an empty set rather than an error.

```sql
-- if <col> in the subquery is nullable, this returns NOTHING
where customer_id not in (select customer_id from <some_relation>)

-- correct, and null-safe by construction
where not exists (
    select 1 from <some_relation> as r
    where r.customer_id = orders.customer_id
)
```

**Prefer `not exists` to `not in` unconditionally.** The reasoning is in [`nulls-and-types.md`](nulls-and-types.md); the rule is simple enough to apply without it. `in` does not have the same problem for positive matching, but standardising on `exists` / `not exists` removes the need to remember which is which.

## `or` in a join predicate

```sql
-- avoid
from orders
left join customers
    on orders.customer_id = customers.customer_id
    or orders.customer_email = customers.customer_email
```

Two independent problems.

**Correctness.** A row matching on both branches matches once per matching right row, so a customer reachable by both id and email fans out. Worse, the two branches encode different business rules — identity by key and identity by email — and combining them with `or` makes it impossible to tell which one produced any given row, or to test either one.

**Cost.** An equality predicate lets the engine use a hash or merge join. A disjunction usually cannot be satisfied that way, so the plan degrades toward evaluating the predicate per row pair. On two large relations that is a difference of orders of magnitude, and it will not be obvious from the SQL that it happened. Check the query plan if the model becomes slow after adding an `or`.

The fix is to make the matching rule explicit. Either resolve identity once, upstream, into a single key:

```sql
-- resolve identity in its own model, then join on one column
coalesce(<matched_customer_id>, <fallback_customer_id>) as customer_id
```

or run the two joins separately and union the results with a column recording which rule matched — which is more code, and is honest about the fact that two rules exist.

The same reasoning applies to `on <a> = <b> or <a> is null`: express the intent with a null-safe comparison (see [`nulls-and-types.md`](nulls-and-types.md)) rather than a disjunction.

## Range and as-of joins

Joining on an interval rather than equality is how attribute-as-of-date and price-at-time-of-order questions are answered, and it fans out by default.

```sql
from orders
left join <customer_history>
    on orders.customer_id = <customer_history>.customer_id
    and orders.ordered_at >= <customer_history>.valid_from
    and orders.ordered_at <  <customer_history>.valid_to
```

Three requirements, all easy to omit:

- **Half-open interval.** `>= valid_from and < valid_to`. Using `<=` on both ends matches two history rows at the exact boundary instant and duplicates the order. `between` is inclusive on both ends and therefore has the same defect.
- **The intervals must not overlap.** If the history relation has overlapping validity windows for one entity, every order in the overlap fans out. `dbt_utils.mutually_exclusive_ranges` tests exactly this — see the testing guidance in `dbt-authoring-schema-yaml`.
- **An open-ended final interval needs a value, not a null.** `valid_to` of null fails the `<` comparison, so the current row never matches. Use a far-future sentinel in the history model, or add `or valid_to is null` and accept the disjunction cost knowingly.

Interval joins are also where engines differ most in execution strategy. If one is slow, that is a plan problem rather than a logic problem — see `dbt-performance-tuning`.

## The join key itself

**Nulls never match.** A null on either side of `=` yields unknown, so the row is dropped by an `inner join` and lands unmatched in a `left join`. That is usually the correct behaviour, and it is why coalescing an identifier to a sentinel to force a match is wrong: it fabricates a relationship and converts a measurable data quality problem into a permanent fake member of the dimension.

**Types must match, and matching is not the same as compatible.** A numeric key on one side and a zero-padded string on the other will either coerce (and match nothing, since `'007'` is not `7` as text) or error, and which one you get depends on the engine. Cast identifiers to a string type once in the source-facing layer, as [`nulls-and-types.md`](nulls-and-types.md) sets out, and the problem does not arise.

**Case and whitespace are part of the value.** On most analytical engines string comparison is case-sensitive and whitespace-sensitive, so `'ABC123'`, `'abc123'`, and `'abc123 '` are three different keys. Some engines' collation settings change this, and at least one treats trailing whitespace differently depending on collation. If a join key is a human-entered or externally-supplied string, normalise it in the source-facing layer — `lower(trim(<col>))` into a dedicated join column, keeping the original value for display — rather than normalising inside the `on` clause, which also defeats any clustering or partitioning on that column.

**Qualify every column when more than one relation is in scope.** An unqualified column resolves to whichever side has it, and when someone adds a same-named column to the other side upstream, the resolution can change with no error and no diff in this file.

## Self-joins

A self-join needs distinct CTE names, not aliases of the same relation, or the file becomes unreadable and the qualification ambiguous:

```sql
with

managers as (
    select employee_id as manager_id, employee_name as manager_name
    from {{ ref('<employees>') }}
),

reports as (
    select employee_id, employee_name, manager_id
    from {{ ref('<employees>') }}
),

joined as (
    select
        reports.employee_id,
        reports.employee_name,
        managers.manager_name
    from reports
    left join managers
        on reports.manager_id = managers.manager_id
)
```

Renaming the columns inside each CTE, as above, removes the whole class of "which side did this come from" question. A recursive hierarchy is a different problem and one that unit tests cannot cover — see `dbt-unit-tests` for the limitation.

## Checklist

- [ ] Output grain stated before the first join was written
- [ ] Each join classified 1:1 / many:1 / 1:many / many:many, and the classification proved by query
- [ ] Right side proved unique on the join key, or the fan-out accepted deliberately
- [ ] Many-to-many ruled out on both sides, or the missing key column added
- [ ] Match rate measured with a `left join` before committing to an `inner join`
- [ ] Row count predicted before building and compared after
- [ ] Right side aggregated to the join key where a summary rather than a fan-out was wanted
- [ ] Measures re-checked for additivity if the grain was deliberately changed
- [ ] No `right join`, no comma joins, no `using`
- [ ] `full outer join` keys coalesced and per-side indicators added
- [ ] Filters on the outer side of a `left join` placed in `on`, not `where`, where unmatched rows must survive
- [ ] `not exists` used instead of `not in`
- [ ] No `or` in any `on` clause; identity resolved to one key upstream instead
- [ ] Range joins use half-open intervals, non-overlapping windows, and no null upper bound
- [ ] Join keys have matching types; string keys normalised in the source-facing layer, not in the `on` clause
- [ ] Every column qualified wherever more than one relation is in scope

## Failure modes

1. **Fan-out from an assumed-unique right side.** Row count multiplies, every sum is wrong, nothing fails. Prevented only by probing uniqueness before writing the join.
2. **A deliberate fan-out that double-counts a measure.** The grain change was handled; the measure repeated on each new row was not. Consumers sum it and get a multiple of the truth.
3. **`not in` against a nullable subquery.** Returns zero rows. Reads as "no matches found," which is a plausible business answer, so it survives review.
4. **An inner join that silently dropped 40% of rows.** The unmatched rate was never measured. The output is smaller and internally consistent, which is exactly why nobody questions it.
5. **A `where` clause on the outer side of a `left join`.** Converts it to an inner join. The rows that vanish are the ones the `left join` existed to keep.
6. **An anti-join testing a nullable column for null.** Returns matched-but-null rows alongside genuinely unmatched ones, inflating the "missing" set with rows that are present.
7. **A range join with `<=` on both bounds.** Every row sitting exactly on a boundary is duplicated. Boundaries are rare in sample data and common at midnight.
8. **`or` in the `on` clause.** Fans out for rows matching both branches and degrades the plan, with no indication in the SQL that either happened.
