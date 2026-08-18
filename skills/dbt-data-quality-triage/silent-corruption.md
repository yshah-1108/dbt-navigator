# Silent corruption catalogue

Defect shapes that produce **plausible wrong numbers and no error**. Read [SKILL.md](SKILL.md) first — it holds the reconciliation method, and these shapes are only useful once a gap has been measured. Reading this file as a checklist to apply speculatively is the wrong use of it; matching an observed gap shape to a candidate here is the right one.

The nine pipeline-mechanics modes — freshness gaps, boundary operators, late arrival, merge duplicates, timezone offsets, grain change, fan-out, null propagation, unbackfilled columns — are in SKILL.md. This file covers the rest: arithmetic, calendar, ordering, and consumer-side defects. All of them share one property that makes them expensive: **the query is valid, the run is green, and the output is within the range a reviewer would accept.**

## Arithmetic

### Integer division

**Symptom** — a ratio, rate, or per-unit figure that is suspiciously round, often exactly `0` or `1`. Averages that are integers when they should not be.

Some engines perform integer division when both operands are integers, truncating toward zero; others always produce a fractional result. The same expression therefore returns `0` on one engine and `0.4` on another. This bites hardest during a migration, and in any macro intended to be portable.

**Diagnostic** — do not read the SQL; ask the engine what it does:

```sql
select 2 / 5 as should_be_fractional
```

If that returns `0`, every ratio in the project built from integer columns is truncated. Then find them:

```sql
select
    count(*)                                             as rows,
    count(case when <ratio_column> = 0 then 1 end)       as exactly_zero,
    count(case when <ratio_column> = round(<ratio_column>) then 1 end) as whole_numbers
from <database>.<schema>.<model>
where <denominator_column> > 0
```

A high `whole_numbers` share on a quantity that has no reason to be whole is the finding.

**Fix** — cast one operand to a fractional type before dividing, at the layer where the ratio is computed. Do not rely on the engine's default; the same code will be read by someone on a different engine, and a portable macro must be explicit. Engines differ in both directions, so state which behaviour you are relying on in a comment.

### Division by zero, and the coalesce that hides it

**Symptom** — either an error, or a rate of exactly zero where the correct answer is "undefined". Which one you get is engine-dependent: some raise, some return null.

The common guard replaces the denominator's zero with null so the result is null rather than an error. That is correct. What is not correct is then wrapping the whole thing to turn that null into `0`, because "no orders, so no conversion rate" and "a conversion rate of zero" are different facts, and a downstream average over the second is wrong.

**Fix** — protect the denominator, and let the result stay null. If a consumer needs a zero, that is the consumer's presentation decision, not the model's. Document the null's meaning in the column description.

### Floating-point accumulation

**Symptom** — a total that differs from the sum of its parts in the last few decimal places. Two aggregations of the same data disagreeing by a tiny amount. A comparison of a refactor against its baseline showing thousands of "differences" that are all trivial.

Sums over floating-point columns are order-dependent, and parallel execution does not guarantee an order. The same query over the same data can return a marginally different total between runs — with no code change and nothing wrong.

**Diagnostic** — measure the magnitude before deciding it is a bug:

```sql
select
    sum(<measure>)                          as float_sum,
    sum(cast(<measure> as decimal(38, 9)))  as exact_sum,
    abs(sum(<measure>) - sum(cast(<measure> as decimal(38, 9)))) as drift
from <database>.<schema>.<model>
```

If `drift` is at the scale of floating-point epsilon relative to the total, it is representation, not a defect. If it is larger, something else is going on and the float is a red herring.

**Fix** — money and any quantity that must reconcile exactly belongs in a fixed-point type (`decimal` / `numeric`), typed at staging so downstream inherits it. Floating-point is fine for measured quantities where the last digit does not matter. The mistake is not "using a float"; it is using one for a value someone will reconcile against an accounting system.

**In verification**: this is the specific reason a row-level comparison needs a tolerance on float columns. Comparing with a tolerance and *saying you did* is honest; comparing exactly and then dismissing thousands of diffs as "just rounding" without measuring is not. See `dbt-verification`.

### Currency and rounding order

**Symptom** — a total that is off by a small number of minor units, scaling with row count. Two reports that each round correctly and disagree.

Rounding then summing is not summing then rounding. Across a million rows, rounding each to two places first accumulates a systematic bias, and the direction depends on the rounding mode. Engines also differ on half-way values: round-half-up and round-half-even give different answers on exactly the values that occur most often in prices.

**Fix** — one rule, stated once: carry full precision through every intermediate layer and round only at the point of presentation. Any model that rounds mid-pipeline must say in its description why, because every consumer downstream inherits the bias and cannot see where it came from.

### Null propagation through arithmetic

**Symptom** — a computed column that is null far more often than its inputs are, or a sum that is lower than expected with no rows missing.

Two distinct behaviours, and confusing them is common. In *arithmetic*, one null makes the whole expression null: `a + b + c` is null if any is. In *aggregates*, nulls are skipped: `sum(x)` ignores them, and `avg(x)` divides by the count of non-null values, not by the row count.

So the same null causes an under-count in one place and a silently different denominator in another.

**Diagnostic**

```sql
select
    count(*)                                        as rows,
    count(<column_a>)                               as a_present,
    count(<column_b>)                               as b_present,
    count(<column_a> + <column_b>)                  as sum_present,
    avg(<column_a>)                                 as avg_over_present_only,
    sum(<column_a>) / count(*)                      as avg_over_all_rows
from <database>.<schema>.<model>
```

`sum_present` well below both inputs shows arithmetic propagation. The two averages differing shows that `avg` is answering a different question than the one the consumer asked.

**Fix** — decide explicitly, per column, whether a missing value means zero or means unknown, and encode that decision where the column is created rather than where it is consumed. Never blanket-`coalesce` to zero to make a chart look continuous; that is the failure this whole skill exists to catch.

## Calendar and time

### Week-start conventions

**Symptom** — weekly totals that disagree with another system by exactly the rows falling on one day. Week-over-week comparisons that look shifted. Reports that agree on monthly totals and disagree on weekly ones.

Engines disagree on which day starts a week, and several make it session-configurable, so the same query returns different groupings depending on a setting nobody set deliberately. Some also number ISO weeks differently at year boundaries, where week 1 may contain days from the previous calendar year.

**Diagnostic** — ask the engine rather than reading documentation:

```sql
select
    date '2024-01-07' as a_sunday,
    <week_truncation_expression>('2024-01-07') as truncates_to
```

If a Sunday truncates to itself, weeks start on Sunday here; if it truncates to the preceding Monday, they start on Monday.

**Fix** — never rely on the default. Truncate explicitly to the convention the business uses, define it once in a shared macro or a date dimension, and put the convention in the column description. Year-boundary week numbering is the case to test deliberately, because it is wrong for a few days a year and nobody looks in January.

### Daylight-saving boundaries

**Symptom** — one or two days a year where a local-day total is 23/24 or 25/24 of what it should be. An hourly series with a missing hour or a doubled hour. Reconciliation that succeeds for eleven months.

Any arithmetic that treats a local day as exactly 24 hours is wrong on transition days. Adding a fixed offset to convert a timestamp is wrong for half the year in any zone that observes a shift. On the autumn transition, a local wall-clock hour occurs twice, so a timestamp without a zone is genuinely ambiguous.

**Diagnostic** — count hours per local day across a transition:

```sql
select
    <local_date_expression>       as local_date,
    count(distinct <local_hour_expression>) as hours_present,
    count(*)                      as rows
from <database>.<schema>.<model>
where <timestamp_column> between <before_transition> and <after_transition>
group by 1
order by 1
```

A day with 23 or 25 distinct hours is correct behaviour and confirms the conversion is zone-aware. A day with 24 on a transition date means a fixed offset is being applied, and the series is silently shifted from that date on.

**Fix** — convert using the engine's zone-aware conversion with a named zone, never a numeric offset. Store in a single canonical zone, convert at the boundary where local reporting is required, and encode the zone in the column name. If the project has not declared a reasoning zone, that is a question for a human, not a default to pick.

### Date and timestamp type confusion

**Symptom** — rows landing on the wrong day at a boundary, or a filter silently excluding the last day of a range.

A date is a calendar day; a timestamp is an instant. Comparing them makes the engine coerce one, usually by treating the date as midnight — so `where ts <= '2024-01-31'` excludes almost the whole of 31 January. This produces a shortfall of exactly one day's data at the end of every range, on every report using that pattern.

**Fix** — half-open intervals, always: `>= start and < end_exclusive`. The convention costs nothing, composes across chunks without gaps or overlaps, and removes the entire class. Where a boundary is mixed-type, cast explicitly and say which side you cast.

## Ordering and deduplication

### Non-deterministic deduplication

**Symptom** — a model whose output changes between runs with no code or data change. A "latest row per key" that flips between two values. An incremental model that never converges, or a comparison against a baseline showing diffs that move each time you re-run it.

Ranking within a partition to keep one row per key is the standard deduplication pattern, and it is only deterministic if the ordering uniquely identifies a single row. When two rows tie on the ordering column — the same update timestamp to the second, the same version number — the engine picks one arbitrarily, and *which* one can differ between executions.

**Diagnostic** — measure whether ties exist at all:

```sql
select count(*) as tied_groups
from (
    select <partition_key>, <ordering_column>, count(*) as n
    from <source_database>.<source_schema>.<source_table>
    group by 1, 2
    having count(*) > 1
) t
```

Non-zero means the deduplication is picking arbitrarily among ties, whatever the output looks like today.

**Fix** — add a stable tie-breaker on a unique column as the final ordering term. The tie-breaker is a business decision, not a technicality: it decides which record wins, so state which one and why. Also confirm the ranking function is the one that assigns distinct numbers within a partition — the functions that assign equal numbers to ties will return more than one row per key from a `= 1` filter, which is the opposite of deduplication and inflates every downstream measure.

### Deduplicating on the wrong grain

**Symptom** — deduplication that removes real records, or removes none at all. Counts lower than the source with no explanation.

Partitioning by a key that is coarser than the true grain collapses distinct records into one; partitioning by one that is finer removes nothing while appearing to work. Neither errors.

**Diagnostic** — compare the candidate key's cardinality against the row count before trusting it:

```sql
select
    count(*)                                as rows,
    count(distinct <candidate_key>)         as distinct_candidate,
    count(distinct <candidate_key_plus_one_more_column>) as distinct_wider
from <source_database>.<source_schema>.<source_table>
```

If the wider key is more numerous than the candidate, the candidate is not the grain, and deduplicating on it destroys rows.

## Source-side changes that look like your bug

### Source restatement

**Symptom** — a period you previously reconciled successfully no longer matches. Historical totals that move. A report that was correct last month and is now different for the same dates.

The source rewrote history: a correction, a late-arriving reprocessing, a mapping change applied retroactively. Nothing in the project changed, and no amount of reading your own SQL will find it — this is the specific case where auditing your own model is guaranteed to waste the whole session.

**Diagnostic** — compare a **closed** period against the source now, and against a figure you recorded then:

```sql
select
    <date_column>,
    count(*)        as rows_now,
    sum(<measure>)  as total_now
from <source_database>.<source_schema>.<source_table>
where <date_column> between <closed_period_start> and <closed_period_end>
group by 1
order by 1
```

If this disagrees with the total you previously recorded for a period that should be frozen, the source moved. That is the finding, and it belongs to whoever owns the source.

**Fix** — nothing in the transformation. Establish whether restatement is expected behaviour for this source (many operational systems restate legitimately) and, if so, whether the project's models are designed to pick it up. A model that only ever reads recent data will never absorb a historical correction, so its history diverges from the source permanently and silently. See `dbt-incremental-models`.

### Upstream schema and semantic drift

**Symptom** — a column that is still populated but now means something else. An enumerated value with a new member. A type that widened. Nothing failed.

A new category in a status column silently falls outside a `case` statement's branches and lands in the `else`, or produces a null that a later aggregate skips. Nothing tests for "a value I have not seen before" unless someone wrote that test.

**Diagnostic** — look for first appearances rather than current state:

```sql
select
    <categorical_column>,
    min(<date_column>) as first_seen,
    max(<date_column>) as last_seen,
    count(*)           as rows
from <source_database>.<source_schema>.<source_table>
group by 1
order by first_seen desc
```

A value whose `first_seen` is recent is a new category. Cross-reference it against the branches of every `case` that reads the column.

**Fix** — decide how the new value should be treated, with the human who owns the definition. Then make the next occurrence loud: an accepted-values test, or a `case` whose `else` raises rather than defaulting silently. A `case` with no `else` returns null, which is at least detectable; an `else` that buckets unknowns into a real category is not.

## Consumer-side defects

The last class, and the one most often misdiagnosed as a pipeline bug, because the report is where the wrong number was seen.

| Consumer-side cause | How to tell |
|---|---|
| The report applies its own filter | Query the model with the report's filter removed. If the model then matches the source, the model is correct |
| The report aggregates at a different grain | Compare the report's grouping against the model's documented grain. An average of averages is the classic case, and it is wrong whenever group sizes differ |
| The report joins to something else and fans out | Row count in the report's own result versus the model. This is fan-out, just not in dbt |
| The report converts a timezone again | A shift of exactly the offset, on top of a model that is already correct |
| The report reads a cached or extracted copy | The model is right now; the copy is from before the fix |
| The report reads a different environment or a stale table | The fully-qualified relation the report is pointed at, read from its configuration |
| Two reports disagree and neither is wrong | They ask different questions. Establish both definitions in writing before treating either as a defect |

**The discipline: reproduce the wrong number with a direct query against the model before investigating the model.** If a direct query returns the correct value and the report does not, the defect is between the model and the reader, and every hour spent in the transformation layer is wasted. This is not a way of deflecting the report — it is the only way to know which of the two systems to fix.
