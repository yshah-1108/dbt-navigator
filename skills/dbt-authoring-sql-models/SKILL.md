---
name: dbt-authoring-sql-models
description: Use when writing or editing the SQL of a dbt model — creating a new staging, intermediate, or mart model, restructuring CTEs, adding joins or aggregations, casting types, or generating a surrogate key. Covers the import/logical/final CTE pattern, where select * is acceptable, join and null discipline, and where casting belongs.
metadata:
  phase: build
---

# Authoring SQL models

A dbt model is read far more often than it is written. The structure below is not aesthetic preference — each rule exists because its absence produced a bug that was expensive to find.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | What it decides here |
|---|---|
| `layers[]` | Which layers exist, their prefixes, materializations, and what each may reference |
| `naming.surrogate_key_column` | The name of the generated key column |
| `naming.timestamp_column_suffix` | Suffix on timestamp/date columns |
| `sql_style.allowed_join_types` | Which joins are permitted |
| `sql_style.group_by_style` | `all`, explicit columns, or positional |
| `sql_style.keyword_case` | Keyword casing |
| `sql_style.final_cte_name` | The terminal CTE's name (`final` below) |
| `project.warehouse` | Gates every dialect-specific recommendation |

**Absent field → generic guidance, labelled as generic.** Do not invent a project's layer taxonomy, prefixes, or key column name. If `layers` is missing, reason in terms of the three generic concepts — a **source-facing** layer, a **transformation** layer, and a **consumer-facing** layer — and say that is what you are doing.

Naming the model itself is `dbt-project-conventions`. This skill is about what goes inside the file.

## Before writing any SQL: state the grain

One sentence, written down before the first `select`: **what does one row of this model represent?**

Everything else follows from it — the surrogate key columns, the `group by`, the uniqueness test, the clustering choice, and the model description. A model whose grain was never stated is a model whose duplicate rows will be discovered by a business user.

This is not a dbt convention; it is the oldest rule in dimensional modelling. Kimball calls declaring the grain the pivotal step, and the reason is that the declaration is **a binding constraint on everything else**: every column you then consider is either true to that grain or does not belong in the model. Discussions about which columns to include go in circles until the grain is fixed, and columns that are not true to the grain are exactly the ones that produce double-counted totals.

Two corollaries that get skipped:

- **Measures must be true to the grain.** A total that belongs to a coarser grain, repeated on every row of a finer one, will be summed by consumers and will be wrong by the repetition factor. Either allocate it across the rows or leave it at its own grain in its own model.
- **Know each measure's additivity.** Fully additive measures sum across every dimension. Semi-additive ones — balances, counts of a state at a point in time — sum across everything except time, and summing them over time produces a number with no meaning. Non-additive ones — ratios, rates, percentages — cannot be summed at all; store the numerator and denominator and let the consumer divide. Record which kind each measure is in its description, because nothing else in the project can express it.

If you cannot state the grain, you do not yet know what you are building. Ask. `dbt-designing-a-model` is the skill for arriving at one.

## The CTE structure

Three sections, in this order, with no exceptions worth making:

```sql
with

-- 1. import CTEs — one per ref() or source()
orders as (
    select
        order_id,
        customer_id,
        ordered_at,
        amount
    from {{ ref('stg_orders') }}
    where not is_deleted
),

customers as (
    select
        customer_id,
        customer_name,
        region
    from {{ ref('stg_customers') }}
),

-- 2. logical CTEs — one transformation each, named for what it did
joined as (
    select
        orders.order_id,
        orders.customer_id,
        customers.customer_name,
        customers.region,
        orders.ordered_at,
        orders.amount
    from orders
    left join customers
        on orders.customer_id = customers.customer_id
),

aggregated as (
    select
        region,
        cast(ordered_at as date) as order_date,
        count(*) as order_count,
        sum(coalesce(amount, 0)) as total_amount
    from joined
    group by region, cast(ordered_at as date)
),

-- 3. final CTE — the output contract, every column listed
final as (
    select
        region,
        order_date,
        order_count,
        total_amount
    from aggregated
)

select * from final
```

### Why each section exists

**Import CTEs** isolate every external dependency at the top of the file. A reader learns the model's entire dependency set from the first screen, and a `ref()` can be rerouted in one place. Name each one after what it imports, not `a` or `src1`.

**Logical CTEs** each do one thing and are named for the thing they did: `filtered`, `joined`, `aggregated`, `deduplicated`, `renamed`, `unioned`, `pivoted`, `ranked`. A CTE you cannot name in one word is doing two things.

**The final CTE** is the model's published interface. Listing its columns explicitly means a column cannot appear or vanish from the model's output because something changed upstream. That is the entire point, and it is why the rule below matters.

Two questions this pattern does not answer — when a CTE has outgrown the file and should become its own model, and whether `ephemeral` is the right materialisation for it — are in [`structure.md`](structure.md), along with CTE naming, Jinja discipline, and the readability rules that are worth a reviewer's attention rather than a linter's.

## Where `select *` is acceptable

This is the most misapplied rule in dbt style guides. There are three distinct positions and they are not equivalent.

| Position | Verdict | Reason |
|---|---|---|
| `select * from final` as the last statement | **Correct** | The column list is already pinned by `final`. Repeating it adds a second place to maintain. |
| `select *` in an import CTE | **Avoid** | The model silently inherits every upstream column change, and on a columnar warehouse it reads columns nobody uses. |
| `select *` in the `final` CTE | **Wrong** | The model's output contract becomes whatever upstream happens to emit. An upstream rename silently changes this model's schema. |

The one legitimate exception to the import-CTE rule is a passthrough model whose purpose is to be a thin wrapper — for example a staging model that genuinely maps 1:1 and lists its columns in the `renamed` CTE immediately below. Even then, prefer listing columns: the cost is one edit, and the benefit is that `dbt compile` fails loudly when an upstream column disappears rather than the model quietly changing shape.

Selecting explicit columns in import CTEs has a second effect that matters at scale: **filters and column pruning applied in the import CTE reduce what the warehouse reads.** Push row filters (soft-delete flags, active-only, date range) as high as possible — into the import CTE, not into a `where` clause three CTEs later.

## Join discipline

- **Explicit join types only.** Never a comma-separated `from` list. Which types are allowed comes from `sql_style.allowed_join_types`; with no contract, restrict yourself to `inner` and `left` and justify anything else in a comment.
- **No `right join`.** It is a `left join` with the tables in a confusing order. Rewrite it. Every reader has to mentally invert it, and half of them will do so incorrectly.
- **`on`, not `using`.** `using` hides which columns are being compared and collapses them in the output, which surprises people.
- **Qualify every column when more than one relation is in scope.** Unqualified columns in a join are how a column silently comes from the wrong side after someone adds a same-named column upstream.
- **No single-letter aliases.** Use the import CTE's name. `orders.customer_id` costs six characters and saves a lookup.
- **A join that can fan out is a grain change.** Before writing a join, know whether the right side is unique on the join key. If it is not, the row count multiplies and no test will tell you unless one asserts the grain. Either deduplicate the right side first, or accept the new grain deliberately and update the surrogate key.
- **No `or` in an `on` clause.** It fans out for rows that match both branches and it usually defeats hash and merge join strategies, so the plan degrades silently.
- **`not exists`, never `not in`.** A single null in the subquery makes `not in` return zero rows.

The procedure for proving a join is safe *before* writing it — classifying cardinality, detecting fan-out and many-to-many, measuring the match rate, predicting the row count, semi- and anti-join patterns, range and as-of joins — is in [`joins.md`](joins.md). Read it before writing a join in a model anything depends on.

## Null handling

- **`is null` / `is not null`.** `= null` is never true — it evaluates to unknown, and the row silently disappears. This is a correctness rule, not style.
- **`coalesce()` around metrics before aggregating** when null means zero. `sum(amount)` over all-null input returns null, which then propagates through every downstream calculation and appears as a blank cell rather than an error.
- **Do not coalesce identifiers to a sentinel value.** `coalesce(customer_id, 'unknown')` turns a referential integrity problem into a permanent fake dimension member. Leave it null and let the test fail.
- **`left join` plus a `where` clause on the right side is an inner join.** Move the condition into the `on` clause if you meant to keep unmatched rows.
- **`<> 'x'` excludes nulls as well.** If null rows should survive the filter, say so explicitly.
- **State `nulls first` / `nulls last` in any `order by` over a nullable column.** The default differs per engine and on at least one platform it is a session parameter, which means the same code can order differently in two environments.

Three-valued logic in full, null-safe equality per adapter, null behaviour in every aggregate, and null ordering defaults per platform are in [`nulls-and-types.md`](nulls-and-types.md).

## Type casting: once, at the source-facing layer

Cast at the layer that reads `source()`, and nowhere else. Downstream models inherit clean types.

The reason is not tidiness. When casting is scattered, the same column ends up with different types in different models, joins between them do implicit coercion, and the warehouse's coercion rules decide your results. Casting once means one place to look and one place to change.

```sql
renamed as (
    select
        cast(id as varchar)                as order_id,
        cast(cust_id as varchar)           as customer_id,
        cast(qty as integer)               as quantity,
        cast(amt as decimal(38, 6))        as amount,
        cast(created as timestamp)         as created_at,
        cast(order_date as date)           as order_date,
        coalesce(deleted_flag, false)      as is_deleted
    from source
    where id is not null
)
```

Rules that generalize across warehouses:

- **Cast identifier columns to a string type**, even when they look numeric. Arithmetic on an ID is never valid, and a string cast prevents a join between a numeric ID in one system and a zero-padded one in another from silently coercing or failing. It also survives the day the upstream system starts issuing alphanumeric IDs.
- **Use a fixed-precision decimal for money**, never a float. Floating point does not represent currency exactly, and the error accumulates through `sum()`.
- **Guard every denominator** with `nullif(<denominator>, 0)` or a safe-divide macro, and cast the numerator to a decimal type — integer division truncates on some engines and not others, so `count(a) / count(b)` returns `0` on Postgres and Redshift and a fraction elsewhere.
- **Pick one timestamp representation for the whole project and state it in the contract.** Mixing timezone-aware and timezone-naive columns is a class of bug that produces answers that are wrong by exactly one offset — plausible, and therefore not noticed.
- **Apply `naming.timestamp_column_suffix`** if the contract sets one, so a reader knows the zone from the column name.
- **Exact type names are dialect-specific.** `timestamp`, `timestamp_ntz`, `timestamptz`, `datetime`, `numeric`, `decimal`, `number`, `varchar`, `string` are not uniformly available. Check `project.warehouse` and use the names that dialect accepts; do not copy type names from an example written for a different engine.
- **All ranges half-open — `>= start and < end`.** `between` is inclusive at both ends, so on a timestamp column it truncates the final day, and two adjacent `between` ranges double-count the boundary.

Downstream, cast only when you are genuinely transforming a value — `cast(created_at as date)` to change grain is a transformation, not a repair. But note that truncating an instant to a date depends on the zone the engine uses, so a daily grain built without an explicit conversion is shifted by the offset.

String-to-number and string-to-date pitfalls, non-throwing casts per adapter, the three distinct timestamp semantics and which type name carries which on each platform, and boundary conventions in full are in [`nulls-and-types.md`](nulls-and-types.md).

## Surrogate keys

When the grain is a composite of several columns, generate a single key column so uniqueness can be tested and an incremental model can have a `unique_key`.

```sql
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
        order_count,
        total_amount
    from aggregated
)
```

- **Name it from `naming.surrogate_key_column`.** With no contract, `unique_id` or `<entity>_key` are both reasonable — pick one and be consistent within the project.
- **Place it first** in the final CTE. It is the row's identity.
- **Include exactly the grain columns — all of them, and nothing else.** A key missing a grain column produces duplicates. A key containing a non-grain column produces a key that changes when a descriptive attribute changes, which breaks every incremental merge that relies on it.
- **When the grain changes, the key must change with it.** This is the single most common source of duplicate rows in an incremental model: a column was added to the `group by` and the key array was not updated.
- **Do not hand-roll it with string concatenation.** `col_a || col_b` collides (`'ab' || 'c'` = `'a' || 'bc'`) and breaks on nulls. Use the package macro.
- **The key is a hash of the columns' string representations, in array order.** So a type change on a grain column, or a reordering of the array, rewrites every key value in the model — which breaks incremental merges and any downstream join, with no diff in this file to explain it.
- **`not_null` belongs on the grain columns too**, not only on the generated key. The macro substitutes a sentinel for a null component, so the key is non-null even when the grain claim is false.

How the macro works and what follows from it, when a natural or composite key is the better choice, delimiter ambiguity, and the determinism requirement are in [`keys-and-reshaping.md`](keys-and-reshaping.md).

## Aggregation and grouping

Read `sql_style.group_by_style`:

| Value | Write |
|---|---|
| `all` | `group by all` |
| `explicit_columns` | `group by region, order_date` |
| `positional` | `group by 1, 2` |

**`group by all` is not portable.** It has full support on Snowflake, BigQuery, Databricks, DuckDB, and Redshift. It does **not** exist on Postgres. On Trino it is valid syntax but `ALL` is a grouping-set modifier, not column inference — so it runs and groups differently than intended, which is worse than a hard error. If `project.warehouse` is Postgres or Trino, or is unset, use explicit columns. Positional grouping is the worst option: inserting a column into the select list silently regroups the query.

With no contract, use explicit columns. It is correct everywhere and survives a column being reordered.

## Jinja and hardcoded values

Lift magic values to the top of the file where a reader will find them:

```sql
{% set in_scope_statuses = "('completed', 'shipped')" %}
{% set lookback_days = 30 %}
```

- A bare date literal buried in a `where` clause five CTEs down is a maintenance trap.
- An unexplained ID literal needs an inline comment saying what it is. `where record_type = '0Ab12'` is unreadable; the same line with `-- record_type 0Ab12 = wholesale order` is fine.
- Keep Jinja shallow. Nested conditionals that assemble SQL make the compiled output impossible to predict from the source. When Jinja gets complex, compile it and read the result — see `dbt-verification`.
- **Avoid introspective macros in a model you need to unit test or contract.** Anything that queries the warehouse at compile time to discover columns makes the model's schema depend on warehouse state, and unit tests cannot resolve it against mocked inputs.

Where Jinja earns its place and where it costs more than it saves, plus whitespace control, is in [`structure.md`](structure.md).

## Comments: why, never what

`-- join customers` above a join is noise. Comments earn their place by recording a decision that the SQL cannot express: why a category is excluded, what a magic identifier means, why a nonstandard construct was used, why a filter boundary is where it is. Delete narrating comments; keep the ones a future reader would otherwise have to ask about in a meeting.

Note the distinction between the two comment syntaxes: `{# ... #}` is removed at compile time, `-- ...` survives into the warehouse's query history. Use the second for anything a person debugging the running query should see.

## Layer-specific and topic-specific guidance

The generic structure above applies to every layer. What differs per layer — permitted references, materialization, how much logic belongs — is contract-driven. Read `layers[]`, then the relevant sub-document:

| Sub-document | Covers |
|---|---|
| [`staging.md`](staging.md) | Source-facing models: 1:1 mapping, renaming, casting, soft deletes, scaffolding |
| [`intermediate.md`](intermediate.md) | Transformation models: joins, aggregation, deduplication, unions, materialization choice |
| [`marts.md`](marts.md) | Consumer-facing models: dimensions, fact and report tables, grain, surrogate keys, terminal-node rules |
| [`joins.md`](joins.md) | Proving a join preserves the grain: cardinality classification, fan-out and many-to-many detection, match rate, semi/anti-joins, range joins, why `or` in an `on` clause is dangerous |
| [`nulls-and-types.md`](nulls-and-types.md) | Three-valued logic, `not in` with nulls, null-safe equality per adapter, nulls in aggregates and ordering, casting discipline, timestamp semantics, half-open intervals |
| [`keys-and-reshaping.md`](keys-and-reshaping.md) | What the surrogate key macro actually produces, deduplication determinism, `qualify` portability, union safety, pivot and unpivot |
| [`structure.md`](structure.md) | CTE naming, when a CTE should become a model, ephemeral tradeoffs, Jinja discipline, the readability rules worth enforcing |

Two structural rules come from the contract rather than from taste:

- **`layers[].may_reference`** encodes the no-layer-skipping rule. A consumer-facing model that calls `source()` directly bypasses every cast and test in between.
- **`layers[].terminal`** marks layers nothing may `ref()`. Referencing a terminal model creates a dependency the layer's owners did not agree to and did not expect.

If neither field is present, apply the generic rule: **a model reads from the layer immediately upstream of it, not from two layers up, and never from `source()` unless it is the source-facing layer.**

## Verify before claiming done

```bash
dbt compile --select <model>
dbt build --select <model>
```

Compiling proves the Jinja resolves and the refs exist. It does not prove the SQL is correct. Then query the built relation with an explicit database and schema — never `ref()` — and check the two things that actually matter:

- **row count against the stated grain**, and
- **zero duplicates on the key**: `select <key>, count(*) from <relation> group by <key> having count(*) > 1`

See `dbt-verification` for the full procedure. See `AGENTS.md` for the universal rules this skill assumes rather than restates.

## Completion checklist

- [ ] Grain stated in one sentence before writing SQL, and each measure classified additive / semi-additive / non-additive
- [ ] Contract read; anything not in it labelled as generic guidance
- [ ] Import CTEs first, one per `ref()`/`source()`, explicit columns, filters pushed up
- [ ] Logical CTEs each named for one transformation
- [ ] `final` CTE lists every output column; file ends `select * from final`
- [ ] No `select *` in `final`; no `right join`; no implicit joins; no single-letter aliases
- [ ] Columns qualified wherever more than one relation is in scope
- [ ] Every join's cardinality proved by query, not assumed; row count predicted before building and checked after
- [ ] No `or` in any `on` clause; `not exists` used instead of `not in`
- [ ] `is null` / `is not null` throughout; metrics coalesced, identifiers not
- [ ] `nulls first` / `nulls last` stated in every `order by` over a nullable column
- [ ] Casting done once, at the source-facing layer, with dialect-valid type names; denominators guarded
- [ ] One timestamp representation used project-wide; zone stated on every timestamp column
- [ ] All ranges half-open; no `between` on a timestamp column
- [ ] Surrogate key contains exactly the grain columns, named per contract, placed first, deterministic
- [ ] `not_null` tested on the grain columns as well as the generated key
- [ ] Deduplication ordering total, with an explicit tiebreaker
- [ ] `group by` style and any use of `qualify` valid on `project.warehouse`
- [ ] Layer reference rules from `layers[].may_reference` respected
- [ ] Compiled, built, row count and key uniqueness verified by query

## The failure modes that cost the most

1. **Duplicate rows from a fan-out join.** The join looked harmless because the right side was assumed unique on the key. Row count doubles, every downstream sum is wrong, and nothing fails. Check uniqueness of the right side before joining, not after someone reports the number.
2. **A surrogate key that no longer matches the grain.** A column was added to the `group by` and not to the key array. The model produces duplicate keys, and if it is incremental the merge starts overwriting unrelated rows. See `dbt-incremental-models`.
3. **`select *` in the final CTE.** An upstream rename changes this model's output schema with no diff in this file and no failing test. The break surfaces in a consumer, days later, far from the cause.
4. **Casting repeated at three layers with two different types.** Joins between them coerce implicitly, and comparisons that look identical return different results. Cast once, at the source-facing layer.
5. **Null swallowed by `= null`, by a `<>` filter, or by a `where` clause on the outer side of a `left join`.** Rows vanish silently. The output is smaller and entirely plausible, which is why it survives review.
6. **`not in` against a nullable subquery returning zero rows.** Reads as a legitimate "none found," so it is never questioned.
7. **A measure that is not true to the stated grain.** A coarser-grain total repeated on every finer-grain row. Every consumer that sums it gets a multiple of the truth, and no test can express the problem.
8. **A non-deterministic deduplication.** The `order by` ties, so the surviving row differs between runs and between environments, and a full refresh silently changes history.
