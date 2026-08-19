---
name: dbt-data-quality-triage
description: Use when nothing errored but the numbers look wrong, a total does not reconcile, data is stale or missing rows, duplicates appeared, or an aggregate is off by a day. Covers source freshness gaps, incremental boundary gaps, late-arriving data, join fan-out, timezone mismatches, and silent grain changes.
metadata:
  phase: diagnose
---

# Data quality triage

Every test passed. Every job is green. The numbers are wrong.

This is the expensive class of failure, for one reason: **the output is plausible.** A model that errors is discovered in minutes. A model that reports a total slightly low is discovered when someone reconciles a report against another system, which may be weeks later — and every decision made in between used the wrong number.

The discipline that separates this from `dbt-debugging-failures` is the absence of a signal. There is no error text to read, no failing row to inspect. So the method inverts:

> **Reconcile against a source of truth. Do not reason about what the SQL should produce.**

Reading the SQL and concluding it looks correct is how the bug survived review in the first place. The SQL *does* look correct. Get an independent number — the source table, the operational system, a previously-trusted report — and diff against it. The gap's shape tells you the cause, usually immediately.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

Relevant fields: `bi.consumers` (who was shown the wrong numbers, and must be told), `project.timezone` (the timezone the project reasons in), `project.warehouse` (which metadata and history tools exist), `environments.dev` / `environments.prod` (explicit locations for reconciliation queries).

Absent field → generic guidance, labelled as generic. Never invent a project's timezone or its list of BI consumers; if `bi.consumers` is absent, say the project has not declared its consumers and ask.

All queries in this skill use **explicit database and schema**, never `ref()` — a reconciliation query that resolves to the wrong environment produces a confidently wrong answer, which is the exact failure being investigated.

## Start with the shape of the gap

One query, before any theory. Compare the model against its source at the coarsest useful grain:

```sql
select
    <date_column>,
    count(*) as rows,
    sum(<measure>) as total
from <database>.<schema>.<model>
where <date_column> >= <start>
group by <date_column>
order by <date_column>
```

Run the equivalent against the source of truth and diff. The **shape** of the discrepancy narrows the cause faster than reading any code:

| Shape of the gap | Look at first |
|---|---|
| Recent days missing entirely | Source freshness, or a failed load |
| One day short, others fine | Incremental boundary, or late-arriving data |
| Every day slightly low | Boundary `>` vs `>=`, or a filter dropping nulls |
| Every day slightly high | Duplicates, or join fan-out |
| Totals shifted one day, sum unchanged | Timezone |
| Row count doubled, totals doubled | Duplicates |
| Row count up, totals unchanged | Grain change or fan-out with correct de-duplication downstream |
| Old data correct, new data wrong | A recent code or upstream change |
| New data correct, old data wrong | A backfill gap, or a column added without backfill |
| Nulls in a column that used to be populated | Upstream schema change, or a failed load |
| A closed period that used to reconcile no longer does | The source restated history. Not your model |
| The same query returns different numbers on re-run | Non-deterministic deduplication, or float accumulation |
| A ratio is suspiciously round, often exactly zero | Integer division, or a null denominator coerced to zero |
| Weekly totals disagree but monthly ones match | Week-start convention |
| One or two days a year are off by an hour's worth | A daylight-saving transition handled with a fixed offset |
| Only one report is wrong; a direct query is right | The defect is consumer-side, not in the model |

## The differential diagnosis

Six layers can produce a wrong number. Each has a **discriminating test** — a query whose result rules the layer in or out regardless of what the others are doing. Run them in this order, because each one is cheaper than the next and eliminates the layer below it.

The order matters for a second reason: layers 1 and 2 are not your defect, and layer 6 is not even in the warehouse. Investigating the transformation first — the instinct, because it is the code you can see — means three of the six candidates were never eliminated.

| # | Layer | Discriminating test | Rules it in when |
|---|---|---|---|
| 1 | **Source** | Query the raw source directly at the same grain, bypassing every model | The wrong value is already present before any transformation |
| 2 | **Source history** | Re-reconcile a period that previously matched | A closed period has moved — the source restated |
| 3 | **Incremental boundary** | Compare the model against a full-refresh build of the same window, or count rows at the boundary timestamp | The two builds differ, or rows sit exactly at the boundary |
| 4 | **Join cardinality** | Row count before and after the join; uniqueness of the key on the right-hand side | Row count rises across the join, or the right-hand key is not unique |
| 5 | **Time and arithmetic** | Aggregate at hourly grain; shift the comparison by one day; check ratio and rounding behaviour | The grand total matches but the per-period split does not, or a ratio is systematically off |
| 6 | **Consumer** | Reproduce the reported number with a direct query against the model | The direct query is correct and only the report is wrong |

Then the two questions that finish the diagnosis, and are the ones most often skipped:

- **Does the identified cause account for the whole gap?** Quantify it. A cause that explains 80% means there are two defects, and the unexplained 20% is the one someone else finds later.
- **Which layer does the fix belong to?** A cause found at layer 4 does not automatically get fixed at layer 4 — a duplicated dimension key is fixed where the key is meant to be unique, not at the join. See *Decide whose problem it is*.

Arithmetic, calendar, ordering, source-drift and consumer-side shapes — integer division, float accumulation, rounding order, week-start conventions, daylight-saving boundaries, non-deterministic deduplication, source restatement, semantic drift — are catalogued with diagnostics in [silent-corruption.md](silent-corruption.md).

## The failure modes

### 1. Source freshness gap

**Symptom** — recent dates missing or partial. Downstream models built successfully on an input that had not yet arrived, so they are correct with respect to the data they saw and wrong with respect to reality.

**Diagnostic**

```sql
select max(<ts_column>) as latest, count(*) as rows_last_day
from <source_database>.<source_schema>.<source_table>
where <ts_column> >= <yesterday>
```

Compare to the source's expected cadence. `dbt source freshness` formalizes this if freshness is configured.

**Fix** — not in dbt. The load is late or failed; the model is innocent. Report the expected-versus-actual latency and the owning system. Once the source lands, rebuild the affected window. Add or tighten a freshness threshold so the next occurrence errors instead of producing quiet undercounts.

### 2. Incremental boundary gap: `>` versus `>=`

**Symptom** — a small, consistent shortfall, often exactly the rows sitting on the boundary timestamp of each run.

`where <ts_column> > (select max(<ts_column>) from {{ this }})` drops every row whose timestamp equals the current maximum. If the source writes multiple rows at that timestamp and only some had landed at build time, the rest are excluded forever — no error, no test failure.

**Diagnostic** — read the compiled SQL, not the model file, and look at the operator:

```bash
dbt compile --select <model>
# target/compiled/<project>/models/<path>/<model>.sql
```

Then count what the boundary excludes:

```sql
select count(*) as rows_at_boundary
from <source_database>.<source_schema>.<source_table>
where <ts_column> = (select max(<ts_column>) from <database>.<schema>.<model>)
```

Non-zero means rows are being silently dropped every run.

**Fix** — `>=`, which is a universal rule. `>=` reprocesses the boundary rows, which is why the model needs a `unique_key` and an idempotent strategy. Then backfill the already-lost window; changing the operator does not recover history.

### 3. Late-arriving data outside the incremental window

**Symptom** — a day that was correct when built becomes wrong later, or is permanently short. The source received rows for that day after the incremental window had moved past it.

**Diagnostic** — the question is whether the source's *event* time and *load* time diverge:

```sql
select
    <event_date_column>,
    max(<load_ts_column>) as last_loaded,
    count(*) as rows
from <source_database>.<source_schema>.<source_table>
where <load_ts_column> >= <recent_start>
group by <event_date_column>
order by <event_date_column>
```

If rows with an event date of five days ago carry a load timestamp of today, the lookback window must exceed five days. If the source has no load timestamp, the divergence cannot be measured — say so, and treat the window as unverified.

**Fix** — widen the incremental lookback to cover the observed lateness with margin, and confirm the strategy is idempotent so re-reading the window corrects rather than duplicates. Measure the lateness distribution before choosing a number; a window picked by intuition is either wasteful or lossy. See `dbt-incremental-models`.

### 4. Duplicates from `merge` where `delete+insert` was needed

**Symptom** — row count and totals inflated, usually on the most recent days. Primary-key tests may not catch it if they are scoped to a recent window, or if the duplicate rows differ in some column and so the surrogate key differs too.

`merge` updates keys it finds and inserts keys it does not. It has no mechanism for a key that **disappeared** upstream. When a source reprocesses a period and emits a different set of rows, the old rows remain and the new rows are added alongside them.

**Diagnostic**

```sql
select <grain_columns>, count(*) as occurrences
from <database>.<schema>.<model>
group by <grain_columns>
having count(*) > 1
order by occurrences desc
```

Group by the **business grain**, not the surrogate key. If the surrogate key includes a column that changed between reprocessings, duplicates at the business grain will have distinct surrogate keys and every uniqueness test will pass.

**Fix** — `delete+insert` for any source that reprocesses, which is a universal rule. Then full-refresh or delete and rebuild the affected partitions; switching strategy does not remove rows already written.

### 5. Timezone mismatch producing off-by-one-day aggregates

**Symptom** — daily totals shifted by one day. The grand total across the whole range matches; the per-day values do not. Frequently reported as "the dashboard disagrees with the operational system."

Causes:

- A timestamp is converted to a date in one timezone at one layer and a different one at another.
- A source emits one timezone and a column name implies another.
- A date filter is applied in one timezone to a column stored in another, so the window is offset by the utc offset.
- A daylight-saving transition makes one local day 23 or 25 hours long, which no fixed-offset arithmetic handles.

**Diagnostic** — check whether the boundary hours are the entire discrepancy:

```sql
select
    <date_column>,
    sum(<measure>) as total
from <database>.<schema>.<model>
where <date_column> between <start> and <end>
group by <date_column>
order by <date_column>
```

If shifting the comparison by one day aligns the two series, it is a timezone offset, not a data loss. Confirm by aggregating the source at hourly grain and checking whether the difference is concentrated in the first or last hours of each day.

**Fix** — convert once, explicitly, and encode the timezone in the column name using the contract's `naming.timestamp_column_suffix`. Read `project.timezone` for the project's reasoning timezone; if that field is absent, state that the project has not declared one and ask rather than assuming.

### 6. Silent grain change

**Symptom** — row count changed and nobody expected it. Every downstream average, ratio, and distinct count changed with it, while sums may look unaffected.

A column added to a `group by` changes what one row means. That is not adding a column; it is redefining the model. No test catches it, because no test asserts the grain.

**Diagnostic**

```sql
select count(*) as rows, count(distinct <expected_grain_key>) as distinct_grain
from <database>.<schema>.<model>
```

`rows > distinct_grain` means the model is finer-grained than its documented grain, and any downstream consumer joining on that grain is now fanning out.

**Fix** — decide deliberately whether the new grain is intended. If it is, this is a breaking change with downstream consequences — see `dbt-breaking-changes`. If it is not, remove the column from the grouping. Then add a uniqueness test on the intended grain so the next occurrence fails loudly.

### 7. Fan-out from a join assumed to be one-to-many

**Symptom** — measures inflated by a roughly constant factor, or by a factor that varies by dimension. The classic case: a dimension table that gained duplicate keys, so every fact row now matches two dimension rows and every measure doubles.

**Diagnostic** — test the join key on the *right* side before blaming the join:

```sql
select <join_key>, count(*) as occurrences
from <database>.<schema>.<dimension_model>
group by <join_key>
having count(*) > 1
```

Then measure the fan-out directly:

```sql
select
    (select count(*) from <database>.<schema>.<fact_model>)        as fact_rows,
    (select count(*) from <database>.<schema>.<joined_model>)      as joined_rows
```

Any increase from a `left join` to a dimension is fan-out.

**Fix** — de-duplicate the dimension at its own layer, where the key is supposed to be unique, rather than adding `distinct` or a window function to the join. Fixing it at the join hides a broken dimension that every other consumer is also joining to. Add a uniqueness test on the dimension key.

### 8. Null propagation from a failed upstream load

**Symptom** — a column that used to be populated is now null, in part or in whole. Aggregates that use it drop silently, because `sum` and `avg` ignore nulls and `count(column)` excludes them.

A `left join` to an empty or partially-loaded upstream table produces nulls, not an error. Downstream sums quietly shrink.

**Diagnostic** — measure the null rate over time rather than checking the current state:

```sql
select
    <date_column>,
    count(*) as rows,
    count(<column>) as non_null,
    count(*) - count(<column>) as nulls
from <database>.<schema>.<model>
group by <date_column>
order by <date_column>
```

A step change on a specific date dates the incident, which usually identifies the cause immediately.

**Fix** — the upstream load. In the model, do not mask it with `coalesce(<column>, 0)`; that converts a detectable gap into a plausible zero, which is exactly the failure class this skill exists to catch. Add a `not_null` test, or a null-rate threshold test, on the column so the next occurrence is loud.

### 9. A column added to an incremental model, null for history

**Symptom** — a new column is correct for recent rows and null for everything before the change. Any report spanning the boundary shows a partial series and looks like a data loss.

**Diagnostic**

```sql
select
    min(<date_column>) as first_populated
from <database>.<schema>.<model>
where <new_column> is not null
```

If `first_populated` is the deploy date, the column was never backfilled.

**Fix** — full-refresh or a targeted backfill. If the model is `full_refresh=false` because its source cannot reproduce history, the column simply cannot be populated for the past — that is a real constraint, and it must be documented in the column description rather than left for a consumer to discover. See `dbt-adding-columns` and `dbt-shipping-changes`.

## Reconciliation discipline

Three rules that determine whether the investigation converges.

1. **Pick the source of truth before querying.** The operational system, the raw source table, or a report that was trusted before the incident. Write down which one and why. Two models disagreeing with each other proves only that they disagree.

2. **Reconcile at the coarsest grain that shows the gap, then narrow.** Total, then by day, then by dimension, then to individual rows. Starting at row level on a large table wastes time and usually finds a row that is legitimately different.

3. **Isolate one variable per query.** A query that changes the date range and the grain and the filter at once produces a number that explains nothing.

A finding is complete when the discrepancy is **fully accounted for** — the identified cause explains the entire gap, not most of it. A small residual that is waved away is a second, undiagnosed bug. Say what is unexplained.

### The reconciliation sequence

Four steps, in order. Each narrows the window the next one has to search, and skipping straight to step 4 on a large table is how an afternoon produces one legitimately-different row and no diagnosis.

**Step 1 — Two aggregates per day, both sides.** Row count and one measure, grouped by day, from the model and from the source of truth. Two aggregates rather than one, because a count that matches while a sum does not is a completely different investigation from both being wrong.

```sql
select
    <date_column>,
    count(*)        as rows,
    sum(<measure>)  as total
from <database>.<schema>.<model>
where <date_column> >= <start> and <date_column> < <end_exclusive>
group by 1
order by 1
```

Read the two series side by side rather than only their totals: **a matching grand total with per-day differences that cancel is a shift, not a loss**, and the two have nothing in common as causes.

**Step 2 — Date the incident.** Find the first day that disagrees, from the query, not from memory. That date is the most valuable fact in the investigation, because it can be matched against deployment history and upstream job history:

```bash
git log --oneline --since="<first_bad_date>" -- models/
```

A code change on that date makes it a code defect. Nothing on that date, and the cause is upstream or environmental.

**Step 3 — Find the dimension.** Add the most likely dimension to the grouping on both sides. A gap concentrated in one segment, one source system, or one region points at a join or a filter; a gap spread evenly across all of them points at arithmetic or a boundary.

**Step 4 — Go to rows, in the narrowed window only.** Now that the window is one day and one segment, list the rows present on one side and absent on the other, and read them. Row-level comparison techniques and their limits are in `dbt-verification`.

### Two checks that catch what per-day totals miss

**Cohort stability.** Some defects do not change today's number; they change *yesterday's* number tomorrow. Record a figure for a fixed window, and re-measure the same window after the next scheduled build:

```sql
-- run today, and again after the next build; the window is deliberately fixed
select sum(<measure>) as total_for_fixed_window
from <database>.<schema>.<model>
where <date_column> >= <fixed_start> and <date_column> < <fixed_end>
```

A closed window whose total moves means the model is still absorbing changes for a period the business considers final — late arrival, restatement, or a non-idempotent build. A closed window whose total *never* moves, when the source restates, means the opposite defect: corrections are never picked up.

**Tolerance, decided in advance.** Before comparing, state what counts as a match, and why. Some differences are expected: a fixed-point rounding at a different layer, a boundary difference of minutes between two systems' extraction times, floating-point drift at the scale of representation error. "Within 0.01%" is a defensible tolerance if the reason is named; "close enough" is not, because it is exactly how a systematic 0.5% loss survives review. Decide the tolerance from the mechanism, not from the size of the gap you happen to have found.

## Decide whose problem it is, before writing any fix

A cause is not yet an owner. The failure modes above split into two groups, and the fix differs completely: some are defects in this project's logic, and some are defects that arrived already made, where the correct action is to escalate rather than to compensate.

Three tests separate them. Run them against the identified cause, not against the symptom.

1. **Does the raw source already contain the defect?** Query the source table directly, at the same grain, bypassing every model. If the wrong value is present before any transformation touches it, the defect is upstream. Nothing written in this project caused it, and nothing written in this project should be shaped to hide it.
2. **Did the source change without the project changing?** Compare a period you previously reconciled successfully against the source *now*. If a closed period no longer matches, the source rewrote history. That is not a bug in your model, and debugging your model against a moving source burns hours and finds nothing.
3. **Is the transformation faithful to the source?** If the source is right and the output is wrong, the defect is internal, and it is one of the nine modes above.

**When it is upstream, escalate with evidence, not with a suspicion.** Name the source table, the affected window from a query, the specific values that are wrong, and the reconciliation showing the defect predates the project. That is what makes an upstream report actionable instead of a request for someone else to go looking.

**A stopgap inside dbt is sometimes correct, but it is always a debt.** Filtering or correcting an upstream defect at the staging boundary keeps consumers working while the real fix is pending. It becomes permanent damage when it is silent, because the next person reads it as intentional business logic and preserves it. If you write one: comment it as a compensation for a named upstream defect, say what will make it removable, and report it as an open item rather than as a fix.

**What not to do:** patch an upstream defect deep in a mart, where the correction is invisible to anyone reading staging; or apply a correction with no record of what it compensates for, which is how a workaround outlives the bug by years.

## Is it a bug, or is it reality?

Before any of this, one question that is skipped surprisingly often: **is the number actually wrong?** A figure that violates an expectation is not thereby a defect. Real businesses have step changes, and treating one as a bug produces a "fix" that deletes a true signal — which is worse than the reverse, because a suppressed real event is undiscoverable afterwards.

Reality is the likelier explanation when the evidence has these shapes:

| Evidence | Reading |
|---|---|
| The source shows the same figure at the same grain | Reality, or an upstream defect. Not a transformation defect either way |
| The change coincides with a known business or operational event | Reality, until the event is ruled out |
| The change is a step on one date, and no deployment or load touched that date | Something real happened. Look outside the warehouse |
| The move is within historical variance for this measure and grain | Possibly nothing at all. Compare against the same period last year and the same weekday |
| A dimension gained a genuinely new member | Reality. The model's assumptions are stale, not the data |
| Only one segment moved, and that segment had an operational change | Reality, scoped to that segment |

And a defect is the likelier explanation when:

- The gap is a *ratio* — exactly double, exactly half, exactly one day's worth. Real change is rarely arithmetically tidy.
- The gap appears at a boundary: the first or last day of a range, midnight, a month end, a deployment.
- The grand total is unchanged and only the distribution moved. Business events move totals; bugs move buckets.
- The same query returns different answers on re-run. Reality is at least stable.

**When the evidence does not decide it, ask — and ask the right person.** The right person is whoever owns the *definition*, not whoever owns the pipeline. "Did something change in how orders are recorded?" goes to the operational team; "should cancelled orders count toward this total?" goes to whoever defined the metric. Ask with the numbers attached, the window, and your own reading, so the answer is a confirmation or a correction rather than an investigation someone else has to start from scratch.

Two things not to do here. Do not set a threshold to make an alert stop firing before knowing whether the underlying move was real — that converts an unanswered question into a permanently unaskable one. And do not describe an unresolved question as a resolved one: "appears to be a genuine business change, unconfirmed" is a legitimate finding; "expected variance" stated without confirmation is a guess wearing a conclusion's clothes.

**Before escalating a cross-system discrepancy, check whether it is expected.** Two systems' counts of the "same" entity routinely differ by design — a CRM counting signed contracts against a product database counting logins have never matched and are not supposed to — and `context.domain_notes` is where a project records that, if it mapped its entities. A discrepancy that turns out to be definitional is not a data-quality incident, and reporting it as one costs credibility you will want for a real one. Absence of a note is not evidence the gap is a bug; it means nobody wrote it down, so it is still a question.

## Communicating a data incident

Distinct from diagnosing it, and the part with consequences outside the repo. Two failures dominate: telling nobody, and telling everybody a wall of technical detail they cannot act on.

### Find out who saw it

Before deciding what to say, establish who consumed the wrong numbers. Read `bi.consumers` from the contract. Each entry carries a `tool`, a `repo_path` to grep, and a `status` of primary, legacy, or deprecated.

```bash
# Which downstream BI content reads the affected model
grep -rn "<model_name>" <repo_path_from_contract>/
```

Prioritize `primary` consumers, and state whether `legacy` ones were checked rather than leaving it ambiguous. If `bi.consumers` is absent from the contract, say that the project has not declared its consumers, and ask who to notify. **Do not assume nobody saw it** — a search that found nothing is evidence about the places searched, and saved reports, extracts, and notebooks are usually not among them.

### Scale the response to the consequence

Judge by what was decided on the wrong number, not by how interesting the bug is.

| Consequence | Response |
|---|---|
| Numbers left the company, or fed a financial, regulatory, or customer-facing figure | Notify immediately, before the fix. Whoever owns that reporting decides what happens next; that is not an engineering decision |
| A widely-used internal report was wrong for a material period | Notify the report's consumers now, with the window and the magnitude. Do not wait for the fix |
| Wrong for hours, in a report nobody consulted, and now corrected | Tell the owning team, in writing, once. No broadcast |
| Caught before anything consumed it | Note it where the next person will find it. No notification |

The judgement call is the third row versus the second, and the deciding question is whether a decision was made on the number. If that is unknown, treat it as the higher tier — the cost of an unnecessary message is much lower than the cost of someone discovering months later that a figure they used was wrong and nobody said so.

### What to say

Five facts, in this order, in plain language. Cause and remediation come last because they are the parts a consumer cannot act on.

1. **What was wrong, and by how much** — the measure, the direction, the magnitude at a grain they recognise. "Daily order totals were understated by about 4%" beats a description of the defect.
2. **Which window** — first and last date, from a query.
3. **Which reports or datasets** — named, from lineage or a search of the consuming repositories, not from memory.
4. **Status now** — corrected, correction in progress with an expected time, or under investigation. If unknown, say unknown rather than implying a timeline.
5. **What they should do** — re-run a report, discard a figure, or nothing. Most recipients need only this.

State the boundary of what you checked. "Primary consumers checked; legacy content not reviewed" is useful. Silence on that point reads as "everything was checked", which is a claim you did not make and cannot support.

### Backfill or correct forward

Two distinct decisions, and conflating them is how a fix half-lands. Fixing the logic stops future loss and repairs nothing already written; repairing the data does not stop it recurring. Both are needed, and the second is a separate choice.

| Situation | Choice |
|---|---|
| A closed period that people reconcile against, or that fed an external figure | **Backfill.** History has to be right |
| The source can still reproduce the affected window | Backfill — it is available, so the argument for not doing it is weak |
| The source cannot reproduce the window: it has aged out, or the model is deliberately protected from full refresh | **Correct forward**, and document the discontinuity in the model's description. A boundary nobody knows about is worse than a documented one |
| The affected window is outside any period anyone reports on | Correct forward, and say explicitly which window remains wrong |
| A backfill would cost more than the error's consequence | A legitimate decision — but it is the data owner's to make, with the cost and the residual error stated, not a decision to take quietly |

Whichever is chosen, **say what remains wrong afterwards.** A correct-forward decision leaves a permanently wrong window; if that is not written down where a future reader will find it, someone will reconcile against it in a year and open this same investigation. Backfill mechanics are in `dbt-incremental-models` and `dbt-shipping-changes`.

### Close the loop

Two things stop the same incident recurring, and neither is the fix:

- **A test that makes the next occurrence loud.** Every mode in this skill has one available: a freshness threshold, a uniqueness test on the true grain, a null-rate bound, a row-count or volume assertion, an accepted-values test. A defect diagnosed and fixed without a test added is a defect scheduled to return silently. Say which test you added and what it asserts — and if none is possible, say that instead of leaving it implied.
- **The detection gap, named.** How long the wrong data was live before anyone noticed is usually the more useful number than the bug itself. "Wrong for eleven days, found by a person reconciling by hand" is the finding that justifies monitoring; "fixed the boundary operator" is not.

## Completion checklist

- [ ] Established that the number is wrong, rather than assuming it — reality ruled out or the question asked
- [ ] Source of truth named explicitly before any reconciliation query
- [ ] Gap shape characterized: missing, low, high, shifted, or null
- [ ] Two aggregates compared, not one — a count and a measure
- [ ] Grand total checked against per-period values, to distinguish a shift from a loss
- [ ] First affected date established from a query and matched against deployment and load history
- [ ] Diagnostic queries used explicit database and schema, never `ref()`
- [ ] Match tolerance stated in advance, with the mechanism that justifies it
- [ ] Consumer-side causes eliminated by reproducing the reported number with a direct query
- [ ] Cause identified and confirmed to account for the **whole** discrepancy, quantified
- [ ] Ownership decided: defect proven upstream or internal, by querying the raw source directly
- [ ] A previously-reconciled closed period re-checked against the source, to rule out a rewritten history
- [ ] Any in-project stopgap for an upstream defect commented, and reported as an open item rather than a fix
- [ ] Affected date window established from a query, not estimated
- [ ] Fix distinguishes correcting the logic from repairing already-written data — both addressed
- [ ] Backfill-versus-correct-forward decided explicitly, and any permanently-wrong window documented
- [ ] Backfill or rebuild scope stated, including what cannot be recovered
- [ ] A test added so the same failure errors next time instead of being plausible, and named
- [ ] BI consumers from `bi.consumers` checked, or their absence from the contract stated
- [ ] Notification scaled to the consequence, and the limits of what was checked stated
- [ ] Detection gap reported — how long the data was wrong before anyone noticed
- [ ] Any residual, unexplained difference reported rather than rounded away

## The most common failure modes

1. **Reading the SQL instead of reconciling.** The SQL looks correct — that is why the bug shipped. An independent number finds in one query what code review missed for months.
2. **Investigating the transformation first.** It is the layer you can see, so it is the layer you search — while the source, the source's history, and the consumer's own logic are three unexamined candidates. Run the discriminating tests in order.
3. **Patching an upstream defect inside dbt.** A correction written into a mart to compensate for bad source data is invisible to the next reader, who preserves it as business logic. Prove where the defect originates before writing any fix.
4. **Debugging your own model against a source that moved.** If a period you already reconciled no longer matches, the source rewrote history. Hours spent auditing your own SQL will find nothing, because nothing there is wrong.
5. **Treating a real business change as a bug.** A step change with no deployment behind it may be true. "Fixing" it deletes a real signal, and a suppressed real event cannot be recovered later.
6. **Fixing the logic and not the data.** Changing `>` to `>=` stops future loss and recovers nothing already lost. Every logic fix in this skill needs a companion backfill decision, stated explicitly.
7. **Masking a gap with `coalesce`.** Turning a null into a zero converts a detectable failure into a plausible number, which is the most expensive possible outcome.
8. **Accepting a partial explanation.** A cause that explains most of the gap but not all of it means there are two bugs. The remainder is the one that will be found later, by someone else.
9. **Fixing fan-out at the join.** Adding `distinct` or a window function at the join site leaves the duplicated dimension in place for every other consumer. Fix the key where it is meant to be unique.
10. **Adjusting a threshold to stop an alert.** Widening a bound before knowing whether the underlying move was real turns an open question into one nobody will ask again.
11. **Fixing it and adding no test.** The same defect will recur, and next time it will be just as silent. Name the test, or say why none is possible.
12. **Not telling anyone.** The data is corrected and a stale wrong number is still in a deck, a saved report, or someone's memory. Silent correction is how the same question gets asked again next quarter.
