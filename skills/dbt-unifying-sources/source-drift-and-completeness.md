# Source drift, arrival, and completeness

Three problems that share one property: **the unified model keeps building successfully and the total is silently partial.** No test fails, no error appears, and the number is low by an amount nobody can compute without knowing what was missing.

This is the material that makes `union all` dangerous in a way the shape decision does not cover. Getting the union right guarantees the arithmetic. It guarantees nothing about whether every source actually contributed.

## 1. Why a union of differently-fresh sources produces a partial total

A union combines whatever each branch currently holds. If one source is loaded hourly and another daily, then for most of the day the unified model contains a complete picture from one source and a stale one from the other — **and the union has no way to express that.** The rows are simply not there.

Consider three sources, all reporting the same measure, loaded on different cadences. At 10:00 the unified model for today contains:

| Source | Loaded through | What the union contains for today |
|---|---|---|
| A | 09:00 today | Nine hours |
| B | 00:00 today (daily, overnight) | Nothing |
| C | 08:00 today, but delayed today | Whatever landed before the delay |

The total for today is neither wrong nor right — it is a partial sum presented as a total, and it will keep rising all day as the other sources land. Every one of these reads as a real business decline:

- A chart of today versus yesterday shows a drop.
- A same-hour comparison against last week shows a drop, because last week's data is complete and today's is not.
- An alert on a percentage change fires, someone investigates the business, and finds nothing.

**The remedy is not to make the sources equally fresh** — you usually cannot. It is to make the incompleteness *visible in the data* so a consumer can exclude or annotate it.

### Make completeness a column, not a footnote

Three constructions, in increasing order of cost and strength. Pick one deliberately.

**A per-source high-water mark relation.** One row per source per period, recording how far that source is loaded. Consumers join to it and filter to periods where every required source is present.

```sql
-- One row per source system: how far is each one actually loaded?
select
    source_system,
    max(<event_timestamp_column>) as loaded_through,
    count(*)                      as rows_loaded
from <unified_relation>
group by source_system
```

**A completeness flag on the unified model itself.** For each period, whether every required source has contributed. This is the version consumers actually use, because it requires no extra join and no knowledge of which sources exist.

```sql
-- Derived per period: is this period complete?
-- <expected_source_count> is a stated number, not count(distinct) over the data --
-- counting the sources present can never detect that one is missing.
select
    <period_column>,
    count(distinct source_system)                                as sources_present,
    count(distinct source_system) = <expected_source_count>       as is_complete
from <unified_relation>
group by <period_column>
```

The comment is the load-bearing part. **Deriving the expected source count from the data makes the check vacuous**: if a source disappears entirely, `count(distinct source_system)` drops and the check passes against the new, smaller number. The expected set must be declared — a seed, a variable, or an accepted-values test on `source_system` — so that a missing source contradicts a written claim.

**Excluding incomplete periods from the model.** The strongest and most disruptive: the model simply does not contain a period until every source has reported. Correct for anything financial or externally reported. The cost is latency, and the cost of the latency is that people build a second, faster model beside it. Only choose this when the requirement to never show a partial figure is real and stated.

### The current partial period is the same problem in miniature

Today, or the current hour, is incomplete by definition. Whether it is included is a design decision that must be written down, because both answers are defensible and they produce visibly different charts. If it is included, label it — a flag, or a documented convention — so a consumer comparing periods knows the last one is not comparable to the others.

## 2. A source that is late, absent, or backfilled

Three distinct situations. The reason to separate them is that the *same* observation — a source's rows are not where you expected — has three causes with three different correct responses.

| Situation | What you observe | Correct response | What makes it dangerous |
|---|---|---|---|
| **Late** | Rows for a period arrive after the model built | Rebuild the affected periods, or ensure the incremental boundary is wide enough to pick them up on the next run | An incremental filtered on event time will never see them. The model is permanently short for that period and keeps building fine |
| **Absent** | A source contributes nothing, for a period or entirely | **Fail loudly.** This is the one case where a silent pass is indefensible | A `union all` of a source that returns zero rows is a valid union with zero rows added. The total is low and nothing indicates why |
| **Backfilled** | A source re-delivers a period it already delivered | Decide whether to replace or accumulate — and it must be replace, or the period double-counts | An append-only incremental adds the backfilled rows to the existing ones. The period's total roughly doubles and reads as a spike |

Late arrival and the incremental boundary interact in a way that is worth stating explicitly, because it is the most common way a unified model becomes permanently wrong:

**If the model is incremental and filters on the event timestamp, a row that arrives late for an old period is outside the filter and is never loaded.** Not delayed — never. The filter must be based on load or ingestion time while the model's *grain* stays event time, or the boundary must be wide enough to cover the worst observed lateness per source. And the worst lateness differs per source, so the boundary is governed by the slowest one. Measure it rather than guessing:

```sql
-- How late does each source actually arrive?
-- Requires a load or ingestion timestamp distinct from the event timestamp.
select
    source_system,
    max(<load_timestamp>  - <event_timestamp>) as worst_lag,
    avg(<load_timestamp>  - <event_timestamp>) as typical_lag
from <unified_relation>
where <event_timestamp> >= <recent_window_start>
group by source_system
```

Date and interval arithmetic is dialect-specific — subtracting two timestamps yields an interval on some engines and a number on others, and several require an explicit date-difference function. Adapt to `project.warehouse`.

The details of widening a boundary, and of backfilling a range once you know it is short, are in `dbt-incremental-models` and `dbt-shipping-changes`. What belongs here is the decision: **per source, what is the worst acceptable lateness, and what happens to a row later than that?** Silently dropped is a choice, and it should be an explicit one.

## 3. Schema drift across sources

Four kinds, and they differ in whether the pipeline notices.

| Drift | Does anything break? | How it presents |
|---|---|---|
| **A new column appears in one source** | No | Nothing, until someone finds the field they needed was available for six months |
| **A column is renamed** | Usually yes, if referenced | A build error, which is the good case. If the old name is still present but no longer populated, no error and the values go null |
| **An enum or status value changes** | **No** | A `case` mapping the old vocabulary silently sends the new value to the `else` branch, or to null. Rows quietly leave the category they belong to |
| **A type changes** | Sometimes | A union branch coerces, and the column's type now depends on which branch had data. Or a numeric arrives as text and comparisons start behaving lexically |

The third is the most expensive, and it is the one most specific to unified models, because a status vocabulary is exactly the kind of thing each source expresses differently and each source can change independently.

### Detect drift rather than discovering it downstream

Three mechanisms, and they catch different things — this is why more than one is worth having.

**Declare the accepted value set for every mapped enum.** An `accepted_values` test on each source's own status column fails when a new value appears, which converts a silent recategorisation into an explicit instruction to extend the mapping. This is the single highest-value test on a unified model and it is the one most often missing.

**Never let a status mapping have a silent fallback.** A `case` whose `else` yields `'other'` or null absorbs every new value forever, and it is indistinguishable from a mapping that is complete.

```sql
-- Absorbs every future value silently. The mapping is now permanently incomplete.
case
    when <source_status> in ('a', 'b') then 'active'
    else 'inactive'
end as status

-- Unmapped values are visible as null and are caught by a not_null test.
case
    when <source_status> in ('a', 'b') then 'active'
    when <source_status> in ('c', 'd') then 'inactive'
end as status
```

The second form leaves nulls for anything unmapped. Pair it with a `not_null` test on the conformed column and a new source value fails the build with a clear cause. This is the pattern to prefer, and the reason is that **the failure mode of the first form has no symptom** — rows silently change category and every total stays plausible.

**Assert the source's column set.** A structural test on each source table that fails when columns are added or removed catches both the new-column case and the rename case before any transformation runs. Available via the common testing packages, and cheaper than it looks: the value is not in blocking the change, it is in learning about it in a build log rather than in a stakeholder question. Where the project uses enforced model contracts, putting one on the source-facing model just above each source catches type and name drift at compile time; contracts do not apply to sources themselves.

**Cast explicitly in every source CTE**, so a type change upstream produces a cast failure at a known place rather than a silent widening in the union. Non-throwing cast functions exist on most engines under different names — useful, but be deliberate: a non-throwing cast turns a type change into nulls, which is a silent failure again unless the resulting column is tested.

### The rename that produces no error

Worth calling out because it defeats every structural check: a source renames a column *and keeps the old one*, now unpopulated. The column set is unchanged, the reference still resolves, the build succeeds, and the values are all null. Only a content test — `not_null`, a row-count expectation, or a freshness check on the measure itself — catches it. This is the argument for testing that a source's measures are non-zero, not merely that its rows exist.

## 4. Conforming: the vocabularies that differ per source

The parent skill covers timezone and units. Five more, each of which produces a specific wrong number.

### Currency, and the as-of question

Converting a monetary value requires a rate, and **which rate is a business decision with a date attached**. The rate as of the transaction, the rate as of period end, or a period average all produce different totals, and different functions legitimately want different ones.

What has to be decided and written down:

- **Which rate, and as of when.** Transaction-date rate is the usual default for operational reporting. Period-end or period-average rates are common for financial reporting, and if both are needed they are two columns, not one column computed two ways.
- **Whether the rate is fixed once applied.** If a rate is later corrected, does history restate? A locked period rate exists precisely so that closed periods do not move. Without a locking mechanism, a rate correction silently restates every previously reported converted figure.
- **Which date, in which timezone.** The conversion date must be derived from the timestamp *after* timezone normalisation. A transaction late in the day in one zone belongs to the next day in another, and therefore to a different rate.
- **What happens when no rate exists** for a currency on a date — weekends, holidays, a newly added currency. Carrying the last known rate forward is the usual answer and requires the rate relation to have validity ranges rather than one row per exact date, so the lookup is a range match rather than an equality that silently drops the row.

**Always keep the original amount and its currency alongside the converted amount.** Storing only the converted value destroys the ability to reconcile against the source system, which reports in the original, and makes a rate correction unfixable without re-extracting.

### Enum and status vocabularies

Two sources both have a status column. The values differ, and — much worse — the *category boundaries* differ: one source's "completed" includes partially fulfilled cases and the other's does not.

Mapping both to one conformed vocabulary is correct only if the categories genuinely correspond. Where they do not, the honest options are to conform at a coarser granularity where they do agree, or to keep the source's native status alongside the conformed one so the difference remains inspectable. **Keep the raw source value in every case.** It costs one column and it is the only way to answer "why is this row in that category" without re-deriving the mapping.

### Precision

One source reports to two decimal places, another to six. Summing works; comparing does not, and a reconciliation between the unified model and a source will differ by an amount that looks like a rounding error and may not be one.

Round at the point of conforming, to a stated precision, and record the precision. Rounding at the end instead means intermediate sums accumulate differences whose magnitude depends on row count — which is why a discrepancy that is invisible at daily grain appears at monthly grain. Also beware float types for monetary values: two engines' float arithmetic can differ in the last places, so a comparison that passes on one platform fails on another.

### Null, zero, and missing

Three states that most sources collapse into fewer than three, and each source collapses them differently.

| Source emits | It probably means | If you get this wrong |
|---|---|---|
| `0` | Measured, and the value was zero | Treating it as missing inflates every average by excluding real zeros |
| `null` | Not measured, or not reported by this source | Treating it as zero drags every average down and asserts a measurement nobody made |
| No row at all | Either "nothing happened" or "this source has not reported yet" — and these are different | Conflating them is the completeness problem in section 1 |

The decision is per measure per source, and the parent skill's rule stands: a measure a source genuinely does not track should be `null`, not `0`. The addition here is that **a source emitting `0` where it means "not reported" must be corrected at the source boundary**, in that source's CTE, to a null — otherwise every average across sources is wrong in a direction that depends on the source mix, and the source mix changes.

### Identifier formatting

The same identifier in two sources, differing by case, by zero-padding, by a prefix, or by a separator. A join on it matches nothing, and the symptom is not an error — it is a left join producing nulls, or an inner join producing an empty result that looks like a filter working.

Normalise identifiers in each source's CTE, and apply the same normalisation everywhere. When the normalisation is lossy — stripping a prefix that turns out to be meaningful, or upper-casing an identifier that is genuinely case-sensitive — it creates false matches, so verify the match rate rather than assuming: a join whose match rate is 100% or 0% is usually a bug in one direction or the other.

## 5. Deduplication and precedence across overlapping sources

Two sources report the same entity for the same period. First, decide which situation you are in — the answers are different.

| Situation | Correct handling |
|---|---|
| Both sources see the same underlying events (a migration overlap, a mirror, two views of one system) | Choose one per (entity, period) by a stated precedence. Keeping both double-counts |
| Each source sees genuinely different events for the same entity | Keep both. This is not duplication and deduplicating it loses real rows |
| Each source sees *some* of the same events and some different ones | The hardest case. Deduplication must be at the **event** grain, not the entity-period grain, which requires an event identifier that survives across sources. If none exists, you cannot deduplicate correctly and must say so |

The third row is the one to be honest about. Without a cross-source event identifier, any deduplication at a coarser grain either drops real events or keeps duplicates, and there is no way to tell which. The output is plausible either way. **Say that the model has an unresolvable overlap and quantify it** rather than choosing a convention that looks tidy.

### Precedence when two sources disagree

When both sources report the same entity-period and the values differ, precedence is a business decision. State it, per measure, with a reason.

Two rules that keep it maintainable:

- **Precedence is not "whichever is larger".** Choosing the higher value systematically biases every total upward and is unjustifiable when written down, which is why it never gets written down.
- **Never average two disagreeing sources.** The result is a number neither system can reproduce, so it reconciles against nothing and every investigation into it dead-ends.

Implement precedence as an explicit rank per source with a documented order, and **keep both values available** — the losing source's value in a separate column, or the discarded rows in a diagnostics model. A disagreement that gets larger over time is a source-quality signal, and discarding the loser destroys the only evidence of it.

```sql
-- Precedence by stated rank, with the alternative retained.
-- Portable form: rank in a subquery, filter outside. `qualify` does the same in
-- one level on the engines that support it -- check project.warehouse.
select *
from (
    select
        *,
        row_number() over (
            partition by <entity_id>, <period_column>
            order by <source_precedence_rank>, <tiebreaker> desc
        ) as source_rank
    from <conformed_union>
) as ranked
where source_rank = 1
```

The `order by` must be total, or the surviving row differs between runs and two environments disagree. `<source_precedence_rank>` should be an explicit numeric column set in each source's CTE — not an alphabetical accident of the source name, which is how precedence silently changes when a source is renamed.

## 6. Adding a new source to an existing union

The checklist in the parent skill covers the mechanics. Two decisions belong here because they are the ones that get skipped and cannot be un-skipped.

### Backfill or forward-only is a stated decision

Adding a source adds its rows from whenever it starts contributing. If the model is incremental, history is not retroactively filled. That produces a **step change in every total on the date the source was added**, which is indistinguishable on a chart from a genuine business event.

Three options, and the wrong outcome is choosing none of them:

| Choice | Consequence | Choose when |
|---|---|---|
| **Backfill** the new source's history | Totals restate for all history. Anyone who reported the old numbers now disagrees with the model | The unified total is meant to represent everything, and historical comparability matters more than previously published figures |
| **Forward-only** | A visible step change on the cutover date | The new source genuinely only exists from that date, or restating history is unacceptable |
| **Forward-only, with the cutover recorded in the data** | A step change that consumers can see and handle | Almost always the best of the three. The `source_system` column plus a documented start date per source makes the step explainable rather than mysterious |

Whichever is chosen, **record the date each source began contributing** in the model's documentation, and prefer a relation over prose so a chart can annotate it. The failure this prevents is the recurring quarterly investigation into a step change that was a deliberate decision nobody wrote down.

### Verify that no existing source moved

The parent skill's step is the whole test: capture per-source totals before, re-run after, and every pre-existing source's numbers must be **unchanged**. Movement means the new source joined rather than unioned, or changed the grain. The reason this is the test is that it is the only check sensitive to the specific error a new source causes — the new source's own numbers can be right while every other source's are wrong.

## 7. Observability: making a missing source visible on a chart

The point of this whole document. A model that is quietly partial is worse than one that fails, because the failure gets fixed and the partial number gets reported.

Four mechanisms, roughly in order of effort:

- **A test that fails when a source stops contributing.** The single highest-value test on any unified model. It must compare against a **declared** expected source set, not against the data's own distinct values, or it can never detect an absence. A recency variant — every source must have contributed within its own expected window — catches the more common partial case where a source is stale rather than gone.
- **Expose the completeness flag as a column** on the unified model, so a consumer can filter or annotate without knowing how many sources exist. A flag nobody can see from the consuming tool is documentation, not observability.
- **Expose per-source loaded-through timestamps** as a small companion relation. It answers "why is today low" in one query instead of an investigation, and it is what turns an incident into a lookup.
- **Register the consumers.** If a chart depends on this model, the dependency should be recorded so a source outage can be communicated to the people reading the affected chart rather than discovered by them. See `dbt-authoring-schema-yaml`.

The test that a source is present is not a nice-to-have on a unified model. It is the only automated protection against the failure mode that defines the whole skill: **the output looks plausible in every case that goes wrong.**
