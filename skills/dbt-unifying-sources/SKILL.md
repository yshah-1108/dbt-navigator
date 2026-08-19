---
name: dbt-unifying-sources
description: Use when combining the same business concept from several source systems into one model — a conformed dimension, one metric computed across multiple platforms, an ID crosswalk between systems, or a model that fans several feeds into one grain. Also use when adding a new source system to a model that already unions several.
metadata:
  phase: build
---

# Unifying multiple sources

Two payment processors report the same thing with different column names, different grains, different timezones, and different ideas about what counts as a completed transaction. Unifying them is the most common non-trivial modelling task in a mature project, and the one with the most ways to be quietly wrong.

The output looks correct in every case that goes wrong here. A duplicated source contributes twice and the total is merely *high*. A dropped source contributes nothing and the total is merely *low*. Neither errors, and neither looks anomalous on a chart.

Two sub-documents carry the material that is needed once the shape decision is settled:

| Sub-document | Read it when |
|---|---|
| [`entity-resolution.md`](entity-resolution.md) | The sources do not share a reliable identifier and one has to be constructed — deterministic, waterfall, and probabilistic matching, survivorship rules, and the ID crosswalk as its own model |
| [`source-drift-and-completeness.md`](source-drift-and-completeness.md) | A source is late, absent, or backfilled; a source's schema or vocabulary changed; you are conforming currency, enums, precision, or null semantics; two sources overlap and disagree; or you need a missing source to be visible rather than merely wrong |

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | Use |
|---|---|
| `layers` | which layer a unified model belongs to, and what it may reference |
| `naming.model_pattern`, `naming.separator` | how to name a model whose "source" is several sources |
| `naming.timestamp_column_suffix` | required suffix once you have normalized timezones |
| `naming.surrogate_key_column` | what the grain key is called |
| `project.warehouse` | dialect for the timezone and safe-cast functions |

**Naming.** A unified model has no single source system, so the source segment is the *union* concept, not one system's name. Use a neutral segment — `unified`, `all`, `combined`, or the business concept itself — per the contract's pattern. Naming it after the largest contributing system is a trap: the next system added makes the name a lie, and nobody renames it.

**Without a contract:** follow the generic sequence below, and name the model after the business concept rather than any contributing system.

## Conformed dimensions, and the grid that plans them

Two facts can only be compared safely through a dimension they **both** join to, whose attributes mean the same thing in both. That is what "conformed" means: the same column names, the same domain of values, and the same keys, everywhere the dimension appears. It is a stronger claim than "shared" — a dimension can be physically shared by two facts and still not conformed, if an attribute means something different in each context.

Why this matters more than it sounds: **two fact tables must never be joined directly on their shared dimension keys.** The cardinality of that join is uncontrollable and the result is arbitrary multiplication. The only safe way to combine them is to query each separately, group both by an identical conformed attribute, and align the results on that attribute. Every cross-process comparison therefore rests entirely on the dimension being conformed, and if it is not, the comparison is wrong in a way the SQL cannot show you.

### The planning grid

Processes as rows, dimensions as columns, a mark where the process uses the dimension. Ten minutes with a spreadsheet, and it produces three things nothing else does:

- **A dimension marked in more than one row must be conformed.** That is the definition of the work, and it is the list of what needs a single owner. A dimension marked in one row only is local and carries no conformance obligation — which is equally useful to know, because it is permission not to over-engineer.
- **Reading across a row tests whether a dimension is well-defined for that process.** A dimension that is awkward to mark for one process usually means the process's grain is different from what was assumed.
- **It ranks the risk.** The dimension every process uses is the one whose drift is most expensive. That is where the conformance effort goes first.

Extend it with the grain and the measure list per process and it becomes the map of what exists — which is also the artifact that makes the duplicate-model check in `dbt-designing-a-model` possible at all.

### "Almost conformed" is the dangerous state

Two dimensions for the same concept, with the same key column name and mostly the same attributes, one of which disagrees. Nothing errors. Two reports group by what looks like the same attribute and produce different populations, and the discovery happens when someone compares them.

It is detectable, cheaply, and the check is the one nobody writes:

```sql
-- Do two dimensions for the same concept agree on a shared attribute?
-- Any output means they are not conformed, whatever the column names suggest.
select
    a.<entity_id>,
    a.<attribute> as attribute_in_a,
    b.<attribute> as attribute_in_b
from <dimension_a> as a
join <dimension_b> as b
    on a.<entity_id> = b.<entity_id>
where a.<attribute> <> b.<attribute>
   or (a.<attribute> is null) <> (b.<attribute> is null)
limit 50
```

The second predicate matters: an inequality alone never fires when one side is null, so a comparison written with `<>` only will report two dimensions as conformed when one of them is missing the attribute entirely. This is the same three-valued-logic trap as everywhere else — see `dbt-authoring-sql-models`.

**The remedy is one dimension with one owner, not a reconciliation job.** Where one consumer needs attributes the shared dimension should not carry, add them in a separate relation joined off it rather than forking the dimension — which keeps the conformed dimension small and stable and confines the local enrichment to the consumer that wanted it.

Conformance is a governance property as much as a technical one: one owner, one refresh cadence, one schema, and changes reviewed. A conformed dimension that anyone may extend is a dimension that will diverge.

### Conformed measures, too

If the same measure name appears in two models, either the definitions are identical and the names should match so they can be compared, or they are not and **the names must differ on purpose**. An awkward, specific name that forces a reader to notice is far cheaper than a shared name that lets two definitions be summed together. This is unfixable later, because by then every consumer has assumed the names match.

## 1. Establish the target grain before writing any SQL

Write one sentence: **"one row per ___."**

Everything else follows from that sentence. If you cannot write it, you are not ready to write SQL, and no amount of joining will settle it later.

Then, for every source, answer: **what is its native grain, and is it finer or coarser than the target?**

| Source grain vs target | What must happen |
|---|---|
| Finer (source has more detail) | Aggregate up. Every measure needs an explicit aggregate function. |
| Equal | Pass through. |
| **Coarser (source has less detail)** | **Stop.** You cannot invent detail. Either coarsen the target, or accept that this source is null at the finer dimensions — and say so in the YAML. |

The third row is where unified models go wrong. Joining a coarser source to a finer grain **fans the rows out** and multiplies its measures by the number of matches. See `dbt-data-quality-triage` for how that presents in production.

Two additions that decide most real cases:

**"Coarser" has one more option, and it is a business decision.** A coarser source can be *allocated* down to the finer grain — split proportionally to a measure that exists at the fine grain, or split evenly. That is legitimate and sometimes necessary, but the allocation rule must come from the business, not from you: allocating proportionally to volume rather than to value changes who the numbers favour. If nobody will supply a rule, do not invent one. Keep that source at its own grain in a separate model and let consumers align the two.

**A source's native grain is a claim to verify, not a fact to accept.** The documentation says one row per transaction; the data has 1.4. Check every source before designing around it, because the difference between the claimed and actual grain is exactly the fan-out you are trying to avoid:

```sql
-- Per source, is the claimed grain real?
-- Run this for every source, before the union exists.
select <claimed_grain_columns>, count(*) as n
from <source_relation>
group by <claimed_grain_columns>
having count(*) > 1
order by n desc
limit 20
```

If a source's actual grain is finer than claimed, you now have a choice to make deliberately — aggregate, or add the missing column to the target grain — rather than a `distinct` added during implementation that discards real rows. The three-way classification of that discrepancy (missing grain column, legitimate multiplicity, or upstream defect) is in `dbt-designing-a-model`.

**Sources at genuinely different grains are also a signal that this might be two models.** If one source is at transaction grain and another only ever reports monthly summaries, forcing both into one relation means one of them is always aggregated beyond usefulness or the other is always allocated on an invented rule. Two models at two grains, with the coarser one used for its own questions, is frequently the honest answer and it is worth putting to the requester.

## 2. Choose the shape: union or join

This is the decision the whole model rests on, and it is not a style choice.

| Sources represent | Shape | Why |
|---|---|---|
| **Different rows** of the same concept — separate merchants, separate storefronts, separate payment methods | **`union all`** | Each source contributes its own rows. Nothing overlaps. |
| **Different attributes** of the same rows — one system has the settled amount, another has the refund outcome for the same transaction | **`left join`** onto a spine | Each source adds columns, not rows. |
| Both | Union the row-contributors first, then join the attribute-contributors onto the result | Do them in that order — joining before unioning multiplies the join against every branch. |
| **Partially the same rows** — the sources overlap on some events and not others | Neither, until an event-level identifier exists | A union double-counts the shared events; a join drops the unshared ones. See below |

A prerequisite that is easy to miss: **a join requires a shared key that is genuinely reliable, and a union does not.** If the sources describe the same rows but carry no common identifier, the join is not available until one is constructed — which is entity resolution, a separate piece of work with its own error tolerance, in [`entity-resolution.md`](entity-resolution.md). Discovering this after choosing the join shape is how a model ends up with a match rate of 40% that nobody notices, because a left join produces a full row count with nulls in it.

Getting this backwards is the single most expensive error in this skill. Union sources that should have been joined and every entity appears once per source with most columns null. Join sources that should have been unioned and every row multiplies.

**Ask: if source A and source B both have data for the same entity on the same day, is that the same row or two different rows?** Same row → join. Different rows → union.

That question, and the "partially the same rows" case above, are answered by the business map rather than by any query — whether one population is a subset of the other or the two only overlap, and whether a match rate well under 100% is expected. If the project keeps `context.domain_notes`, its entity-links section should already state both; if not, `dbt-onboarding-to-a-project/mapping-the-business.md` is the procedure for establishing them. Do that before choosing the shape, not after: the measurement tells you the rate, and only a person tells you whether that rate is normal.

### The question has a third answer, and it is common

"I don't know — sometimes the same, sometimes different." That is not indecision, it is the description of an **overlap**, and it means the sources partially cover the same events. Neither shape is correct on its own: a union double-counts the shared events and a join drops the unshared ones.

The only correct handling requires an event-level identifier that survives across sources, so shared events can be deduplicated at the event grain rather than at the entity-period grain. If no such identifier exists, **the overlap is unresolvable and the model must say so and quantify it** rather than adopting whichever convention looks tidiest. The precedence and deduplication procedure, including how to keep the losing value as evidence, is in [`source-drift-and-completeness.md`](source-drift-and-completeness.md).

### Testing the answer instead of trusting it

The union-versus-join question is answerable from the data, and answering it takes one query. Do that rather than reasoning about it:

```sql
-- How many entity-periods appear in both sources?
-- Zero  -> union is safe; the sources are disjoint.
-- All   -> join is right; they describe the same rows.
-- Some  -> overlap. See the third-answer case above.
select
    count(*)                                          as in_a,
    count(b.<entity_id>)                              as also_in_b
from <source_a> as a
left join <source_b> as b
    on  a.<entity_id>      = b.<entity_id>
    and a.<period_column>  = b.<period_column>
```

A match rate near 0% or near 100% is the clear case. Anything in between is the interesting one, and it is either a genuine overlap or a normalisation problem in the join key — check identifier formatting before concluding it is real, because a case or padding difference produces exactly the same partial match rate.

### If a join is the answer, build a spine

When sources contribute attributes rather than rows, joining them to each other in a chain makes the first source's population the population of the whole model — anything absent from it is absent from the output, and any fan-out anywhere multiplies everything downstream of it.

Build an explicit spine instead: a relation of the complete key space, derived from the union of keys across all sources, then left-join each source's attributes onto it. The spine makes the population an explicit decision rather than an artifact of join order, and it is what makes "this entity exists but source B has nothing for it" expressible as a row with nulls instead of a missing row. Note that a spine built from the union of source keys is itself a place where the identifier normalisation must already have been applied, or the same entity appears twice in the spine.

## 3. Build one CTE per source, conformed at its own boundary

Each source gets its own CTE that ends already conforming to the target contract: same column names, same types, same grain, same timezone, same units.

Conform inside each source's CTE — never in the final select. A `case` statement in the final select that switches on which source a row came from means the conforming was not actually done, and the next source added will need that statement extended in a place nobody will find.

```sql
with source_a as (
    select
        cast(<id_column> as varchar)                as entity_id,
        <timestamp_column>                          as event_at,
        'source_a'                                  as source_system,
        sum(<their_name_for_transaction_count>)     as transaction_count,
        sum(<their_name_for_refunds>)               as refund_amount
    from {{ ref('stg_source_a__events') }}
    group by all           -- only if project.warehouse supports it
),

source_b as (
    select
        cast(<id_column> as varchar)                as entity_id,
        <timestamp_column>                          as event_at,
        'source_b'                                  as source_system,
        sum(<different_name_for_transaction_count>) as transaction_count,
        0                                           as refund_amount   -- b does not report refunds
    from {{ ref('stg_source_b__events') }}
    group by all
)
```

**Tag every branch with its origin.** A `source_system` column costs one line and is the only way to answer "which system contributed this row?" without re-deriving the whole model. Without it, every discrepancy investigation starts from nothing. This is also what makes per-source reconciliation in step 6 possible at all.

**Use a literal `0` or `null` for a measure a source genuinely does not report** — and be deliberate about which. `0` asserts "this source reported none"; `null` asserts "this source does not report it". They aggregate differently and mean different things. For a metric a source does not track, `null` is almost always correct: `0` silently drags averages down.

## 4. Reconcile the vocabulary

Three separate problems, routinely conflated:

**Different names for the same thing.** Map them in each source CTE. Straightforward.

**The same name for different things.** The dangerous one. Two processors both report "transactions" and mean different events — one counts authorizations, the other counts settlements. Aliasing them to one column silently blends two definitions into a number that is wrong in a way no test detects.

> Before mapping two columns to one name, confirm they measure the same event. If they do not, they are two columns. Keep them separate and name them for what they actually measure, or pick one definition and exclude the other source from that metric.

**One concept spread across differently-named columns within a single source.** A coalesce chain is the right tool:

```sql
coalesce(<processor_new_column>, <processor_legacy_column>, <gateway_export_column>) as payment_method
```

Order matters — it is a priority list, most authoritative first. Add a comment saying *why* that order. A coalesce chain with no explanation is unmaintainable: the next engineer cannot tell whether the order is deliberate or accidental.

**A `coalesce` chain is a survivorship rule in disguise**, and it is only the right tool when the sources are filling gaps in each other rather than disagreeing. When two sources both have a value and they differ, `coalesce` silently picks the first — which is a precedence decision made by column order. If the sources genuinely disagree, that is a business decision per attribute, and the rules for making it (source priority, recency, completeness) plus the obligation to record which source won are in [`entity-resolution.md`](entity-resolution.md).

### Status and category vocabularies are the third dangerous case

Two sources both have a status column, the values differ, and — much worse — the category *boundaries* differ: one source's "completed" includes partially fulfilled cases and the other's does not. Mapping both to one vocabulary is correct only if the categories genuinely correspond.

The rule that prevents the silent version of this failure: **never give a status mapping a fallback branch.**

```sql
-- Absorbs every future source value silently. Permanently incomplete.
case when <source_status> in ('a', 'b') then 'active' else 'inactive' end

-- Unmapped values become null, and a not_null test turns the next new
-- source value into a build failure with a clear cause.
case
    when <source_status> in ('a', 'b') then 'active'
    when <source_status> in ('c', 'd') then 'inactive'
end
```

Pair it with an accepted-values test on each source's raw status column, and **keep the raw value in a column of its own**. It costs one column and it is the only way to answer "why is this row in that category" without re-deriving the mapping. Precision differences, null-versus-zero-versus-missing semantics, and the rest of the conforming vocabulary are in [`source-drift-and-completeness.md`](source-drift-and-completeness.md).

## 5. Normalize timezone, units, and currency at the source boundary

Every source arrives in its own timezone. Convert in the source CTE, and apply the contract's `naming.timestamp_column_suffix` so the column states its zone in its name.

Gate the function on `project.warehouse`:

| Warehouse | Convert to UTC |
|---|---|
| snowflake | `convert_timezone('UTC', <ts>)` |
| bigquery | `timestamp(<ts>, '<source_tz>')` |
| databricks · postgres · redshift | `<ts> at time zone '<source_tz>'` |
| unknown | Do not guess. State that the conversion was not applied and ask. |

Same discipline for units — one source reports amounts in cents, another in dollars; one reports a rate as `0.42`, another as `42`. These are invisible until someone sums across sources and gets a number 100× off.

Currency is the same problem with an extra dimension: converting requires a rate, and **which rate, as of when, is a business decision**. Transaction-date, period-end, and period-average rates all produce different totals and are all legitimately requested. Keep the original amount and its currency alongside the converted value, always — storing only the converted figure destroys the ability to reconcile against the source, which reports in the original. The full set of currency decisions, including what happens when no rate exists for a date and whether a rate correction restates history, is in [`source-drift-and-completeness.md`](source-drift-and-completeness.md).

## 6. Verify per source, not just in total

The total is the least sensitive check available. Two sources, one doubled and one dropped, produce a plausible total.

**Row count by source** — every source present, each in a sane proportion:

```sql
select source_system, count(*) as rows, sum(<measure>) as total
from <database>.<schema>.<model>
where <date_column> >= '<start>'
group by source_system
order by source_system
```

A source with zero rows is a join or filter that eliminated it. A source with a suspiciously round multiple of its expected rows is a fan-out.

**Reconcile each source against its own staging model.** The unified total for a source must match that source alone. This is the check that catches fan-out, and it must be done per source:

```sql
-- unified, one source only
select sum(<measure>) from <database>.<schema>.<model>
where source_system = 'source_a' and <date_column> = '<date>'

-- that source's staging model, same window
select sum(<measure>) from <database>.<schema>.<stg_source_a>
where <date_column> = '<date>'
```

**Grain uniqueness on the declared key** — including `source_system` if the grain is per-source:

```sql
select <grain_columns>, count(*)
from <database>.<schema>.<model>
group by <grain_columns> having count(*) > 1 limit 20
```

**Overlap check.** If two sources can report the same entity for the same period, confirm whether that is intended. Union-shaped models double-count silently when two feeds overlap:

```sql
select entity_id, <date_column>, count(distinct source_system) as source_count
from <database>.<schema>.<model>
group by 1, 2 having count(distinct source_system) > 1 limit 20
```

Rows here are not automatically wrong — but they must be explained, and the explanation belongs in the model's YAML description.

**A test that fails when a source stops contributing.** The highest-value test on any unified model, and the one most often missing. It has one requirement that makes or breaks it: the expected source set must be **declared**, not derived from the data.

```sql
-- A test that CANNOT work. If a source disappears, the count drops and the
-- comparison passes against the new, smaller number.
select 1 where (select count(distinct source_system) from <model>)
             <> (select count(distinct source_system) from <model>)

-- A test that works: every source in the declared set must be present,
-- and must have contributed recently. Rows returned = test failure.
with expected as (
    select 'source_a' as source_system
    union all select 'source_b'
    union all select 'source_c'          -- or a seed, so it is reviewable
),
actual as (
    select source_system, max(<date_column>) as latest
    from <database>.<schema>.<model>
    group by source_system
)
select
    e.source_system,
    a.latest
from expected as e
left join actual as a using (source_system)
where a.source_system is null              -- source gone entirely
   or a.latest < <expected_recency_bound>  -- source stale
```

`using` is widely supported but not universal, and it resolves column names differently from an explicit `on` in some engines — substitute an explicit join condition if `project.warehouse` is ambiguous.

The recency half matters as much as the presence half, because **stale is far more common than absent** and produces the same partial total. A source that stopped loading three days ago still has rows, still appears in every source list, and still contributes nothing to the periods anyone is looking at.

**Reconcile a measure total per source, not only a row count.** A row count catches a dropped or duplicated source. It does not catch a source whose measure changed meaning, whose unit conversion is wrong by a factor, or whose values went null while its rows kept arriving. Reconcile at least one measure per source against that source's own staging model, over the same window, and record the numbers — a reconciliation with no recorded figures cannot be repeated after the next change.

**Completeness per period, against the declared source set.** The reconciliation above proves each present source is correct. This proves the period is whole:

```sql
select
    <period_column>,
    count(distinct source_system) as sources_present
from <database>.<schema>.<model>
where <period_column> >= '<start>'
group by <period_column>
having count(distinct source_system) < <expected_source_count>
order by <period_column> desc
```

Periods returned here are periods whose total is partial. If that is expected — the newest period, or a source that legitimately started later — it should be visible in the model as a column rather than known only to whoever ran this query. Why a union of differently-fresh sources produces a partial total, and the three ways to make it visible, are in [`source-drift-and-completeness.md`](source-drift-and-completeness.md).

See `dbt-verification` for what counts as proof.

## 7. Document what each source contributes

The YAML must state, per source: which rows it contributes, which measures it does *not* report, and what its native grain and timezone were. A unified model without this is unmaintainable — the next engineer cannot tell an intentional null from a bug. See `dbt-authoring-schema-yaml`.

Four more things that are cheap to write now and unrecoverable later:

- **The date each source began contributing.** Without it, the step change in every total on that date is investigated as a business event, repeatedly.
- **The expected freshness per source**, so "why is today low" has an answer that is a lookup rather than an investigation.
- **The precedence order and why**, wherever two sources can report the same thing. Precedence chosen by column order in a `coalesce` will be silently reordered by whoever tidies the file next.
- **Any known unresolvable overlap, quantified.** A stated limitation is usable. An unstated one becomes someone else's incident.

## Adding a source to an existing unified model

The common case, and it has its own ordering:

1. **Read an existing source CTE first.** It encodes conforming decisions — priority order, unit conversions, null-vs-zero — that must be matched, not reinvented.
2. **Capture per-source totals before the change** (step 6's first query). This is the baseline.
3. **Check the new source's actual grain** against its claimed one, and against the target grain.
4. **Check for overlap with every existing source**, not just the largest. Two feeds both reporting a period during a migration is exactly when this happens.
5. Add the new source's CTE, conformed at its boundary.
6. Add it to the union or join.
7. **Add the new source to the declared expected-source set**, or the presence test silently stops covering it.
8. **Re-run the per-source query and diff.** Every pre-existing source's numbers must be *unchanged*. A new source that moves an existing source's total means it joined rather than unioned, or the grain changed.
9. **Decide backfill or forward-only, and write the decision down.** See below.

Step 8 is the one that gets skipped, and it is the whole test.

### Backfill or forward-only is a decision, not a default

A new source contributes from whenever it starts. On an incremental model, history is not retroactively filled — so the totals show a **step change on the cutover date, indistinguishable on a chart from a genuine business event.**

| Choice | Consequence | Choose when |
|---|---|---|
| **Backfill** the new source's history | All history restates. Anyone who reported the old figures now disagrees with the model | The total is meant to represent everything, and historical comparability matters more than previously published numbers |
| **Forward-only** | A visible step change on the cutover date | The source genuinely only exists from that date, or restating published history is unacceptable |
| **Forward-only, with the cutover recorded in the data** | A step change consumers can see and handle | Usually the best of the three: the `source_system` column plus a documented start date makes the step explainable instead of mysterious |

Not choosing is the failure. It produces the forward-only outcome with none of the documentation, and the step change gets rediscovered every quarter. The mechanics of running the backfill, once chosen, are in `dbt-shipping-changes`.

## Completion checklist

- [ ] Target grain written as one sentence before any SQL
- [ ] Each source's **claimed** grain verified against its data with a `having count(*) > 1` query
- [ ] Each source's native grain compared to the target; coarser sources handled deliberately, not joined blindly
- [ ] Any allocation of a coarser source down to the target grain uses a rule supplied by the business
- [ ] Union-vs-join decided by the "same row or different rows?" question, and the answer **measured** with an overlap query rather than assumed
- [ ] Partial overlap recognised as a third answer needing an event-level identifier, not forced into either shape
- [ ] Where a join was chosen, a reliable shared key confirmed to exist — or entity resolution scoped as its own work
- [ ] Join-shaped models built on an explicit spine, not chained source to source
- [ ] Identifiers normalised identically in every source CTE, and the resulting match rate checked
- [ ] Null join keys handled deliberately — the portable disjunction form, not an equality that silently drops rows
- [ ] One CTE per source, conformed at its own boundary — no conforming in the final select
- [ ] Every branch tagged with `source_system`
- [ ] Columns mapped to one name confirmed to measure the same event
- [ ] Coalesce priority order commented with its reason, and recognised as a survivorship rule where sources disagree rather than merely fill gaps
- [ ] Status and category mappings have **no fallback branch**; unmapped values become null and are caught by a test
- [ ] Accepted-values test on every source's raw status column, so a new source value fails the build
- [ ] Raw source values retained alongside every conformed value
- [ ] Timezone, units, and currency normalized per source, gated on `project.warehouse`
- [ ] Currency conversion states which rate and as of when; original amount and currency retained
- [ ] Missing measures deliberately `null` or `0`, with the choice justified
- [ ] A source emitting `0` where it means "not reported" corrected to null at its own boundary
- [ ] Row count and measure total verified **per source**, not just in total
- [ ] Each source reconciled against its own staging model, with the figures recorded
- [ ] A test exists that fails when a source stops contributing, comparing against a **declared** expected source set — never against the data's own distinct values
- [ ] The presence test also covers staleness, not only absence
- [ ] Per-period completeness checked against the expected source count, and incompleteness exposed as a column rather than known only to whoever ran the query
- [ ] Late-arriving rows considered against the incremental boundary — an event-time filter never sees them
- [ ] Backfilled periods handled by replacement, not accumulation
- [ ] Grain uniqueness tested on the declared key
- [ ] Cross-source overlap checked and either eliminated or documented, with any unresolvable overlap quantified
- [ ] Precedence between disagreeing sources stated per measure, with the losing value retained as evidence — never averaged, never "the larger one"
- [ ] Structural drift detection in place: source column set asserted, explicit casts in every source CTE
- [ ] Conformed dimensions checked for "almost conformed" divergence with a cross-dimension attribute comparison, including the null-versus-null case
- [ ] YAML states what each source contributes and does not contribute, plus each source's start date, expected freshness, and the precedence order
- [ ] When adding a source: pre-change per-source baseline captured, expected-source set updated, post-change diff shows every pre-existing source unchanged
- [ ] Backfill-versus-forward-only decided explicitly and recorded, with the cutover date visible in the data

## Common failure modes

1. **`union all` matches by position, not by name.** `select * from source_a union all select * from source_b` will happily map `refund_amount` onto `transaction_count` if the CTEs list columns in different orders. It does not error — the types often match. **Never `select *` into a union.** List columns explicitly in every branch, in the same order. This is the highest-frequency serious bug in unified models.
2. **Fan-out from a coarser source.** Joining a monthly-grain source to a daily model multiplies its measure by the number of days. The total is high by an integer factor, which reads as growth.
3. **Join where a union belonged.** Every entity appears once per source with most columns null, and totals look low because aggregates skip nulls.
4. **Union where a join belonged.** Every entity appears once per source, and every total is multiplied by the number of sources reporting it.
5. **The same name meaning two different things.** Two processors' "transactions" blended into one column. No test fails. The number is simply not the thing its name claims.
6. **A new source silently changing existing sources' numbers.** Always the shape of the combination, never the new source's own SQL. Caught only by per-source before/after comparison.
7. **Untagged rows.** No `source_system` column, so no discrepancy can be attributed and every investigation restarts from the raw sources.
8. **Overlap treated as impossible.** Two feeds both report a period during a migration, and the overlap double-counts. Check rather than assume; migrations are exactly when this happens.
9. **A source-presence test that counts the sources in the data.** If a source disappears, the count drops and the test passes against the new number. The expected set must be declared somewhere a human reviews, or the test can never fail for the reason it exists.
10. **A stale source passing every presence check.** Its rows are all there, from three days ago. Every recent period is short, every list of sources looks complete, and only a recency bound per source catches it.
11. **A `union all` of differently-fresh sources reported as a total.** For most of the day the newest period contains a complete picture from one source and nothing from another. The chart shows a decline, someone investigates the business, and the number rises by itself later.
12. **An event-time incremental boundary losing late-arriving rows permanently.** Not delayed — never loaded. The model keeps building successfully and that period stays short forever, with nothing in any log to indicate it.
13. **A backfilled period appended instead of replaced.** The source re-delivers a period, an append-only incremental adds the rows to the ones already there, and the period roughly doubles. Reads as a spike.
14. **A status mapping with an `else` branch.** A new source value silently joins whatever the fallback category is. Rows leave the category they belong to, every total stays plausible, and nothing indicates the mapping is now incomplete.
15. **A renamed column that is still present and no longer populated.** Every structural check passes, the reference resolves, the build succeeds, and the values are all null. Only a content test catches it.
16. **Storing only the converted currency amount.** The model can no longer be reconciled against the source system, which reports in the original currency, and a later rate correction cannot be applied without re-extracting.
17. **Averaging two disagreeing sources.** Produces a number neither system can reproduce, so it reconciles against nothing and every investigation into it dead-ends. Precedence is a decision; the mean is an evasion of it.
18. **A placeholder identifier used as a matching key.** An empty string, a default, or `unknown` shared by thousands of records merges them all into one entity. Every per-entity figure for that entity is meaningless and the row count is unchanged.
19. **A waterfall matcher without exclusion between tiers.** A pair matching on two rules appears twice in the crosswalk, and every measure joined through it doubles for exactly those entities. Invisible in a spot check.
20. **A probabilistic threshold chosen by whoever wrote the SQL.** The acceptable error rate is a business decision in both directions, and a threshold set without one has silently made that decision permanently and invisibly.
21. **Two dimensions for the same concept that are "almost conformed".** Same key name, mostly the same attributes, one that disagrees. Two reports group by what looks like the same thing and return different populations. Detectable by a cross-dimension comparison nobody runs.
22. **A step change on the date a source was added, with no record of the decision.** Forward-only was chosen by not choosing. The step gets rediscovered and re-investigated every time someone new looks at a long-range chart.
