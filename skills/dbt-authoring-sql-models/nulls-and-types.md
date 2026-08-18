# Null semantics, types, and time

Three subjects that share one property: the wrong answer is plausible. A join bug doubles a number and somebody notices. These produce results that are quietly, defensibly wrong — a total short by the rows where a flag was null, a timestamp off by exactly one offset, a ratio of zero because two integers were divided.

Everything here is either universal SQL semantics or an explicitly-labelled dialect difference. Check `project.warehouse` before applying anything in the second category.

## Three-valued logic

SQL has three truth values: true, false, and unknown. Any comparison involving null yields unknown, and a `where` clause keeps only rows that evaluate to true. Unknown is discarded exactly like false — but it is not false, and that is where the surprises come from.

| Expression | Result |
|---|---|
| `null = null` | unknown |
| `null <> null` | unknown |
| `null = 1` | unknown |
| `not (null = 1)` | unknown |
| `unknown and false` | false |
| `unknown and true` | unknown |
| `unknown or true` | **true** |
| `unknown or false` | unknown |

The two rows that matter: `unknown and false` is false, so an `and` chain can still be conclusive. `unknown or true` is true, so an `or` chain can too. Everything else propagates.

Consequences that bite:

- **`= null` is never true.** Use `is null` / `is not null`.
- **`<> 'x'` excludes nulls too.** `where status <> 'cancelled'` drops rows where status is null. If those rows should be kept, write `where status is null or status <> 'cancelled'`, or normalise the column upstream so it is never null.
- **`not (a = b)` is not the same as `a <> b` when either is null** — both are unknown, but the negation makes it look like a complement, and readers assume complements partition the data. Two filters written as apparent opposites can both exclude the same rows.
- **`case when <col> = 'x' then 1 else 0 end` maps null to 0.** That is often intended. When it is not, the null has been silently converted into a real value that then gets summed.

### `not in` returns nothing if the list contains a null

`x not in (a, b, c)` expands to `x <> a and x <> b and x <> c`. If any element is null, that conjunct is unknown, and `true and unknown` is unknown — so no row ever qualifies and the query returns an empty result set.

```sql
-- returns ZERO rows if any customer_id in the subquery is null
where customer_id not in (select customer_id from <some_relation>)

-- correct
where not exists (
    select 1 from <some_relation> as r
    where r.customer_id = orders.customer_id
)
```

Positive `in` does not have this defect — a null in the list simply never matches. But `not in` is common enough, and its failure silent enough, that the safe rule is to use `exists` / `not exists` for both. The join-shaped version of this is in [`joins.md`](joins.md).

### Null-safe equality is dialect-specific

Comparing two nullable columns and wanting "both null counts as equal" — change detection, reconciliation, deduplication on a composite key — needs a null-safe operator, and there is no single portable one.

| Platform | Null-safe equality |
|---|---|
| Postgres | `a is not distinct from b` |
| BigQuery | `a is not distinct from b` |
| Databricks / Spark | `a <=> b`, `a is not distinct from b`, or `equal_null(a, b)` |
| Snowflake | `equal_null(a, b)` (also accepts `is not distinct from`) |
| Redshift | **Not supported.** `is distinct from` is documented as an unsupported Postgres feature; it may appear to work and is not guaranteed |
| Portable everywhere | `(a = b) or (a is null and b is null)` |

The portable form is verbose and correct on every engine. Prefer it in a project that targets more than one platform, or in a macro. Do **not** reach for `coalesce(a, '<sentinel>') = coalesce(b, '<sentinel>')` as a substitute: it is correct only if the sentinel cannot occur in the data, it forces a type, and it defeats index and partition pruning on the compared columns.

### Nulls in aggregates

| Function | Null behaviour |
|---|---|
| `count(*)` | Counts rows, including all-null rows |
| `count(<col>)` | **Skips nulls.** Not the same number as `count(*)` |
| `count(distinct <col>)` | Skips nulls |
| `sum`, `min`, `max` | Skip nulls; return **null** over an empty or all-null input, not zero |
| `avg(<col>)` | Skips nulls **in the denominator too** |
| `a + b` | Null if either operand is null |
| `string_agg` / `listagg` / `array_agg` | Vary by engine on whether nulls are skipped — check before relying on it |

Two of these cause most of the damage.

**`sum()` over all-null input returns null, and null propagates.** A downstream calculation on it produces null, which surfaces as a blank cell rather than an error. Wrap metrics in `coalesce(<col>, 0)` before aggregating *when null genuinely means zero*. If null means "unknown", coalescing to zero fabricates a measurement — leave it null and document that the measure can be null.

**`avg()` changes its own denominator.** `avg(amount)` over ten rows with three nulls divides by seven. If the business question is "average across all orders" the answer is `sum(coalesce(amount, 0)) / count(*)`, which is a different number. Decide which one is meant and write it explicitly; `avg()` hides the choice.

Arithmetic across nullable columns has the same problem: `col_a + col_b` is null if either is. `dbt_utils.safe_add([...])` and `safe_subtract([...])` treat nulls as zero across the operands, which is usually what a total wants.

**Never coalesce an identifier.** `coalesce(customer_id, 'unknown')` turns a referential integrity failure into a permanent dimension member that accumulates unrelated rows, and it defeats the `not_null` test that would have reported the real problem.

### Null ordering is not portable, and on one engine it is not even fixed

This matters far more than it looks, because deduplication depends on it — see the ordering rules in [`keys-and-reshaping.md`](keys-and-reshaping.md).

| Platform | Default with `order by <col> asc` |
|---|---|
| Postgres | Nulls last (nulls sort as larger than any value) |
| Redshift | Nulls last in `asc`, nulls first in `desc` |
| Snowflake | Governed by the `default_null_ordering` session parameter — `last` by default, and changeable per session |
| BigQuery | Nulls **first** (null is the lowest value) |

Snowflake's is the dangerous one: the same model can order differently depending on session settings, which means a deduplication that keeps the right row in one environment can keep a different row in another with no code difference.

**Write `nulls first` or `nulls last` explicitly in any `order by` where the column can be null.** It is supported on all four platforms above and it removes the dependency entirely.

## Booleans

- **A nullable boolean is three-valued.** `where not <flag>` keeps rows where the flag is false and **drops** rows where it is null. `where <flag> = false` does the same. If null should be treated as false, write `where not coalesce(<flag>, false)`.
- **Make booleans genuinely boolean at the source-facing layer.** Sources deliver `'Y'`/`'N'`, `'true'`/`'false'`, `1`/`0`, and `'t'`/`'f'`. Convert once, on the way in. A string masquerading as a boolean is truthy in some contexts on some engines and produces silently different filters.
- **Name them so the polarity is unmissable** — `is_`, `has_`, `was_`. `is_active` reads correctly under negation; `active_status` does not, and `not_deleted` produces double negatives at every call site.
- **Document the exact condition in the YAML**, not the fact that it is a flag. A boolean whose condition is only expressed in SQL will be interpreted differently by every consumer.

## Type casting

The rule — **cast once, at the layer that reads `source()`** — is in the parent SKILL.md. What follows is what to watch for when doing it.

### String to number

The commonest source of a silently wrong or intermittently failing model.

- **A cast can fail on one row out of ten million.** The source column was always numeric until somebody entered `'N/A'`. On most engines the whole build then fails, which is the good outcome; on some, with some functions, you get null or a truncated value instead.
- **Use the engine's non-throwing cast for genuinely dirty input**, and know it is dialect-specific: `try_cast` on Snowflake and Databricks, `safe_cast` on BigQuery, neither on Postgres or Redshift (where a regex guard in a `case` expression is the portable substitute). A non-throwing cast converts a loud failure into a null, so pair it with a test that counts the nulls rather than letting them disappear.
- **Empty string is not null.** `cast('' as integer)` errors on some engines and yields null on others. Normalise empty strings to null in the source-facing layer.
- **Leading zeros, thousands separators, and locale decimals.** `'007'` cast to integer is 7 and no longer joins to `'007'`. `'1,234.56'` and `'1.234,56'` both fail a plain numeric cast. Strip and normalise deliberately; do not let the engine guess.
- **Casting a string to a date is the most locale-dependent operation in SQL.** `'01/02/2024'` is two different dates depending on which convention the engine assumes. Use the engine's explicit format-parsing function with the format stated, not a bare cast.

### Numbers

- **Money uses a fixed-precision decimal, never a float.** Floats do not represent decimal fractions exactly, the error accumulates through `sum()`, and two models that should agree will differ in the last places. Reconciliation against a finance system then fails for a reason nobody can find.
- **Integer division truncates on some engines and not others.** `5 / 2` is `2` on Postgres and Redshift, and `2.5` on BigQuery, Snowflake, and Databricks. A conversion rate computed as `count(a) / count(b)` therefore returns `0` on half the platforms. Cast the numerator to a decimal type explicitly — `cast(<numerator> as decimal(38, 6)) / <denominator>` — and the expression is correct everywhere.
- **Guard every denominator.** Division by zero errors on most engines and is the classic 3am failure. `nullif(<denominator>, 0)` is portable and returns null rather than raising; `dbt_utils.safe_divide()` does the same across dialects. BigQuery also has `safe_divide()` natively.
- **Beware precision loss on `sum()` of a narrow decimal.** Some engines widen the result type automatically and some do not. If a total can exceed the declared precision, declare it wider at the source.

### Identifiers

Cast to a string type even when they look numeric. Arithmetic on an identifier is never valid; a string cast prevents implicit coercion between a numeric id in one system and a zero-padded one in another; and it survives the day the upstream system starts issuing alphanumeric ids without a schema migration on your side.

### Type names are not portable

`timestamp`, `timestamp_ntz`, `timestamptz`, `datetime`, `numeric`, `decimal`, `number`, `varchar`, `string`, `text`, `int`, `int64`, `bigint` are not uniformly available or uniformly spelled. Read `project.warehouse` and use what that dialect accepts. Do not copy type names out of an example written for a different engine — and note that model contracts, where every column carries a `data_type`, make this a build-time failure rather than a runtime one (see `dbt-authoring-schema-yaml`).

## Timestamps and time zones

Three distinct semantics hide behind similar type names. Getting the wrong one produces answers that are off by an offset, which is plausible enough to survive review and consistent enough to look deliberate.

| Semantics | What it stores | Correct for |
|---|---|---|
| Instant, zone-aware | A point in absolute time | Event times, anything ordered or compared across regions |
| Civil / wall clock, no zone | A date and time with no instant attached | Local business calendars, contractual dates, birthdays |
| Instant rendered in the session zone | An instant, displayed per session setting | Almost nothing in a warehouse — the same query returns different strings to different users |

Approximate mapping — verify against the adapter's own documentation:

| Platform | Instant | Civil (no zone) | Session-dependent |
|---|---|---|---|
| Snowflake | `timestamp_tz` | `timestamp_ntz` | `timestamp_ltz`. Bare `timestamp` is an alias governed by a session parameter |
| BigQuery | `timestamp` | `datetime` | — |
| Postgres | `timestamptz` | `timestamp` | `timestamptz` output converts on display using the session zone |
| Redshift | `timestamptz` | `timestamp` | as Postgres |
| Databricks / Spark | `timestamp` (instant, interpreted in the session zone) | `timestamp_ntz` | `timestamp` |

The rules that follow from this:

- **Pick one representation for the whole project and record it in the contract.** Storing UTC instants and converting to local time in the consumer-facing layer or the BI tool is the arrangement that fails least often. Mixing zone-aware and zone-naive columns in one project guarantees a comparison between them, and the engine's implicit conversion will use a zone you did not choose.
- **A bare `timestamp` whose meaning is set by a session parameter is a portability hazard.** Two connections with different settings materialise different data from the same model. Name the type explicitly.
- **Apply `naming.timestamp_column_suffix`** if the contract sets one, so the zone is legible in the column name. A column called `created_at` with no suffix, in a project that has both kinds, is a question rather than a fact.
- **`current_timestamp` and `current_date` are session-dependent.** `current_date` in particular can differ by a day between two sessions in different zones, which makes "today's rows" a moving target. Anchor date logic to an explicit zone conversion rather than the session default.
- **Adding an interval across a DST boundary is not the same as adding a fixed duration.** Adding one day to a local timestamp can produce a 23- or 25-hour gap; adding 24 hours to an instant always produces 24 hours. Decide which the business means. Calendar arithmetic belongs on civil dates, elapsed-duration arithmetic on instants.
- **Truncating an instant to a date requires a zone.** `cast(<instant> as date)` uses whatever the engine's default is. If daily numbers must match a local business day, convert to the target zone first and then truncate — otherwise every day's totals are shifted by the offset, which looks like a small trend rather than a bug.

## Date and range boundaries

**Use half-open intervals: `>= start and < end`.** Every time.

```sql
-- correct: no gap, no overlap, no dependence on the column's precision
where <event_at> >= '2024-01-01' and <event_at> < '2024-02-01'

-- wrong for a timestamp column: excludes most of the last day
where <event_at> between '2024-01-01' and '2024-01-31'

-- also wrong: 23:59:59 excludes sub-second values above it
where <event_at> between '2024-01-01 00:00:00' and '2024-01-31 23:59:59'
```

`between` is inclusive at both ends. On a date column that is often what you want; on a timestamp column it either truncates the final day or, when two ranges are stitched together, double-counts the boundary. Half-open ranges tile perfectly: consecutive periods share a boundary value and it belongs to exactly one of them.

The same rule governs interval joins and validity windows ([`joins.md`](joins.md)) and the incremental boundary predicate, where the direction of the inequality is reversed for a different reason — a boundary predicate uses `>=` so that late-arriving rows at the boundary timestamp are reprocessed rather than lost. See `dbt-incremental-models`.

**State the convention in the model description** for anything a consumer will filter on. "Rows are included where the event instant falls in `[start, end)`, in UTC" answers a question that otherwise gets answered wrongly by each consumer independently.

## Strings

- **Comparison is case- and whitespace-sensitive** on most analytical engines, and collation settings can change that per column or per session. Two values that display identically can be different keys.
- **Concatenating null yields null** with the `||` operator and with `concat()` on several engines, so building a composite value out of nullable parts silently produces null. Coalesce each part, or use the engine's null-tolerant concatenation function — and note this is exactly why a hand-rolled surrogate key breaks, as [`keys-and-reshaping.md`](keys-and-reshaping.md) sets out.
- **Normalise join keys once, in the source-facing layer**, into their own column: `lower(trim(<col>)) as <col>_key`. Keep the original for display. Normalising inside an `on` clause or a `where` clause hides the transformation from every reader and prevents the engine from pruning on that column.
- **Empty string, whitespace-only, and null are three different values** and sources use all three for "missing". Pick one — null — and convert the other two on the way in. `dbt_utils.not_empty_string` tests that the choice held.

## Checklist

- [ ] `is null` / `is not null` used; no `= null`
- [ ] `<>` filters checked for whether they should also keep nulls
- [ ] `not exists` used instead of `not in`
- [ ] Null-safe comparison, where needed, written in a form valid on `project.warehouse`
- [ ] Metrics coalesced where null means zero; left null where null means unknown; identifiers never coalesced
- [ ] `avg()` versus explicit `sum()/count(*)` chosen deliberately
- [ ] `nulls first` / `nulls last` stated explicitly in every `order by` over a nullable column
- [ ] Booleans genuinely boolean, null-safe, and named `is_`/`has_`
- [ ] Every cast explicit, with dialect-valid type names, done once at the source-facing layer
- [ ] String-to-number and string-to-date casts guarded, with the format stated for dates
- [ ] Money in fixed-precision decimal; no floats
- [ ] Division: numerator cast to decimal, denominator guarded with `nullif` or a safe-divide macro
- [ ] One timestamp representation chosen project-wide and recorded; type named explicitly rather than left to a session parameter
- [ ] Zone stated for every timestamp and date column, in the name where the contract asks and in the description always
- [ ] Date truncation of instants performed in a stated zone
- [ ] All ranges half-open; no `between` on a timestamp column
- [ ] Join keys normalised in a dedicated column at the source-facing layer

## Failure modes

1. **`not in` against a nullable subquery.** Zero rows returned, which reads as a legitimate "none found".
2. **`sum()` of an all-null column returning null**, propagating through a chain of calculations and appearing as a blank cell rather than a failure.
3. **`avg()` used where the denominator was meant to be all rows.** The number is smaller, plausible, and unverifiable without knowing how many nulls there were.
4. **`where not <flag>` on a nullable boolean.** The null rows vanish. The output is smaller and internally consistent.
5. **Integer division returning zero** on Postgres or Redshift, in SQL that produced the right answer when it was tested on a different engine.
6. **A float used for money.** Totals disagree with the source system in the last decimal places and nobody can find the discrepancy.
7. **`between` on a timestamp column.** The last day of every period is missing all but its first instant. Monthly totals are consistently and slightly short.
8. **A daily grain truncated in the wrong zone.** Every day's total includes some of the neighbouring day. Trends look real; the numbers never reconcile with a local-time source.
9. **A session-dependent timestamp type or null ordering.** Identical code produces different data in two environments, and the diff that would explain it does not exist.
10. **A non-throwing cast used to make a build stop failing.** The bad rows became nulls, the build went green, and the data loss is now invisible.
