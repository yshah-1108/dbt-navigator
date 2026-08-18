---
name: dbt-designing-a-model
description: Use when a new model is requested, when a request arrives as an output rather than a dataset ("I need a dashboard that…", "can you get me a report of…"), when the grain of a proposed model is unclear or unstated, or when deciding between a fact, a dimension, a wide denormalized table, or a bridge. Covers eliciting the real question, checking whether a model already answers it, fixing the grain in writing, choosing the shape and the SCD type, and classifying measure additivity — all before any SQL is written.
metadata:
  phase: decide
---

# Designing a model

Every gate in this library proves a model does what you *claimed*. None of them checks that you were asked for the right thing. A model built at the wrong grain compiles, builds, passes `unique` and `not_null`, reconciles against itself, and is wrong — and it stays wrong until someone tries to reconcile it against a number produced somewhere else, usually a quarter later, usually in front of an audience.

Design is the only place that failure can be caught, because it is the only place where what a row *means* is still a decision rather than an artifact.

This skill is six decisions and a paragraph of writing. It is not a data-modelling course. If a section here does not end in a choice you can write down, it does not belong.

The output of this skill is a **design note**: a grain statement, a column list, a measure list with additivity, and the questions the model answers. It is the input to `dbt-authoring-sql-models`, which starts by asking what the grain is and assumes someone already knows. It is also the artifact `dbt-verification` checks against in its row-count reconciliation — that check is circular unless the grain was written down first, by someone other than the query that produced it.

Two sub-documents carry the material that is too long to inline and is needed only once a shape or an attribute type is actually in question:

| Sub-document | Read it when |
|---|---|
| [`grain-deep-dive.md`](grain-deep-dive.md) | Turning a grain statement into a test, checking for the four multi-grain traps, or sanity-checking a proposed grain against the source before designing around it |
| [`fact-and-dimension-patterns.md`](fact-and-dimension-patterns.md) | Deciding which of the four fact-table types a request needs, designing a dimension (degenerate, junk, role-playing, outrigger, bridge, hierarchy), choosing among the full range of slowly-changing-dimension types, or handling data where facts and dimensions do not arrive together |
| [`shape-tradeoffs.md`](shape-tradeoffs.md) | Choosing between a star schema, one big table, a Data Vault, and normalised relations — with the costs stated per consumer, and what modern columnar engines changed about the calculus |
| [`additivity.md`](additivity.md) | Classifying a measure and it is not obviously fully additive — why the semi- and non-additive traps happen, and how to keep a measure's definition conformed across models |

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | What it decides here |
|---|---|
| `layers[]` | Which layers exist, so the design can name the one the model lands in — and whether the project even *has* an intermediate layer to put shared logic in |
| `layers[].terminal` | Whether the design may assume anything can read this model. A terminal-layer model must answer its questions alone; nothing downstream will extend it |
| `layers[].materialization` | Whether the chosen shape is affordable. A wide table in a view-only layer is a different design |
| `naming.model_pattern` | Whether the grain must be expressible as a name segment — a pattern with a `<grain>` slot forces the grain decision before the file exists |
| `naming.timestamp_column_suffix` | Whether the time grain column carries a timezone marker, which forces the timezone question in step 1 to be answered rather than deferred |
| `naming.surrogate_key_column` | The name of the key that will encode the grain |
| `project.timezone` | The default calendar the project reasons in — the *baseline* for the time-grain decision, not an answer to it |
| `schedules` | Which refresh cadences are actually available. Designing an hourly model into a project with only a daily cadence is a design that cannot ship |
| `bi.consumers[]` | Which BI layers to search in step 2, since a semantically duplicate model often already exists there rather than in the DAG |
| `testing` | Which tests will be expected on the key you are about to declare — a grain you cannot test is a grain you have not really chosen |

**Without a contract, or with these fields absent**, every decision in this skill still has to be made — none of them is a convention, all of them are semantics. What degrades is only your ability to place and name the result. Say so, and use generic guidance:

- **No `layers`**: infer the layer set from the directory structure under `models/` and place the model with its closest siblings. Do not invent a layer. If the project has no intermediate layer, shared logic goes in the consumer until a second consumer exists.
- **No `naming.timestamp_column_suffix` and no `project.timezone`**: you must still ask which timezone the day boundary uses. The absence of a project default makes this *more* important, not less — it means no prior decision protects you.
- **No `schedules`**: derive the actual cadences from the orchestrator rather than assuming one exists. Never design a refresh frequency the project has no job to deliver.
- **No `bi.consumers`**: you cannot enumerate the BI layers to search for an existing equivalent, and the duplicate-check in step 2 is therefore incomplete. State that in the design note.

---

## 1. Elicit the real question

The request you receive is almost never a description of a dataset. It is a description of an artifact — a dashboard, a spreadsheet, an export, a chart someone saw elsewhere. The dataset is an inference you are about to make on the requester's behalf, silently, and they will not review it.

Eight questions. Ask them together, per `dbt-gathering-context` step 5 — batched, with your own answers filled in wherever you could derive them.

The order below is load-bearing rather than cosmetic. The decision constrains the grain; the grain constrains which dimensions are even admissible; the metric definition determines whether the requested measure is computable from the sources at all. Asking them in the other order produces a specification that has to be rewritten when the first answer arrives.

**What decision does this drive?** The single most useful question, because it constrains everything else and nobody volunteers the answer. A number that decides whether to renew a contract needs a different grain, a different latency, and a different tolerance for restatement than a number someone watches out of curiosity. It also frequently reveals that the requested output is not the one that answers the decision.

**What does one row mean?** Ask it in exactly those words. The answer is the grain, in the requester's vocabulary, before you translate it into columns. If they cannot answer, they have described a report layout rather than a dataset, and step 3 will fail — go back and find the decision.

**Which grain do they actually need, versus the one they described?** People ask for the finest grain they can imagine because finer feels safer. Finer is not free: it multiplies cost, it can push a model past a materialization boundary, and it can make a metric non-additive that would have been additive one level up. Ask what the coarsest grain that still answers the question is. Conversely, a request stated at a summary grain often needs a finer one underneath because the *next* question will be a breakdown — build the fine grain and let the BI layer aggregate, when it is affordable.

**Which timezone, and which calendar?** Two separate questions with independent answers, and both change every number in the output. A "day" is a timezone decision: the same events assigned to different days shift daily totals and, at month boundaries, monthly ones. A "month" or a "week" is a calendar decision: a fiscal calendar, a broadcast calendar, and an ISO calendar disagree about which week a date belongs to, and about when a quarter starts. `project.timezone` is the project's default, not this model's answer — a model serving a function that reasons in a different zone needs that zone, and the mismatch is invisible once the column is named without a marker. Get both in writing.

**What does the metric actually mean, here, specifically?** "Revenue", "active", "session", "customer", "transaction" — every one of these is a family of definitions, and the requester is holding exactly one of them without knowing the others exist. Gross or net of something. Recognized when, on which event. Active over what window, by which action. A session ends after what idle gap. A customer at which level of a corporate hierarchy. This is the `semantics` ask-class in `dbt-gathering-context` step 4: no query answers it, and a plausible guess is indistinguishable from the right answer until reconciliation fails. If two existing models compute the same-named metric differently, **that is a finding to report**, not an ambiguity for you to resolve by picking one.

**Which source is authoritative?** When two systems both know a fact and disagree, choosing between them is a business decision with an owner. Never resolve it by preferring the one that is easier to query, and never resolve it by averaging.

**What must this number reconcile against, and to what tolerance?** The most useful question nobody asks. If a number has to tie to a figure produced by another system, that system's definition — its timezone, its boundary rule, its inclusion criteria — is a constraint on this design, not a nice-to-have, and it is far cheaper to learn now than during a reconciliation. "It does not have to tie to anything" is a valid and useful answer: it tells you restatement is cheap here.

**Is it acceptable for a number reported last month to change?** This is the same question as the historical-stability requirement in the slowly-changing-dimension step, asked early enough to shape the whole design. If the answer is no, then attributes used for slicing need as-of-event resolution, late-arriving data cannot be silently re-attributed, and a full refresh becomes a destructive operation. If the answer is yes — and for many models it genuinely is — a large amount of machinery is unnecessary and should not be built.

Answers to these eight become the specification. A request is specified when someone other than you could build the same model from it and get the same numbers.

### The requested shape is usually not the model shape

The most common structural mistake in this whole skill is implementing the *output* as the *model*.

| What was asked for | What the model should be | Why |
|---|---|---|
| A pivot — periods or categories as columns | Long: one row per entity per period per category | Every new period or category otherwise requires a schema change, and no BI tool can filter on a column name |
| A single chart's exact series | The grain underneath the chart | The second chart request arrives within a week and needs a different cut of the same rows |
| A percentage or a rate | The numerator and the denominator, as separate additive columns | See step 6. A stored ratio cannot be re-aggregated correctly, ever |
| A report with subtotal and total rows mixed in | Detail rows only | Mixed grains in one relation double-count on every unfiltered `sum` |
| A "top N" list | The full ranked population, or the rank as a column | N changes, and the filter belongs to the consumer |
| One row per entity with "current" values | The event grain, plus a separate current-state view | Collapsing to current at the model level destroys the ability to ask anything historical |

The through-line: **build the grain, let the consumer reshape.** Pivoting, ranking, limiting, and formatting are presentation. Every one of them is cheap in the BI layer and permanent in the warehouse.

**Stop and confirm before proceeding** when the answers to "what does one row mean" and "what did you ask for" describe different things. That divergence is the whole value of this step, and it is the moment the requester can still correct you for free.

---

## 2. Check whether something already answers it

The cheapest possible outcome of a model request is that no model is needed. In a project of any age this is a live possibility on most requests, and checking costs a few minutes.

```bash
# Names: the entity, its plausible synonyms, and the metric
find models -iname '*<entity>*'
grep -rln "<metric_column>" models/ --include=*.sql --include=*.yml

# What already exists at the terminal layer, which is where a duplicate would live
dbt ls --select <terminal_layer_path> --resource-type model

# Documented grain and purpose of the near-misses, without opening each file
grep -rn -A3 "name: <candidate_model>" models/ --include=*.yml
```

Search the **metric name as a column** as well as the entity name. A model serving this request may be named for a different entity entirely and already carry the measure you were asked to produce. Also search each `bi.consumers[].repo_path` — a semantically identical dataset frequently exists as a BI-layer derived table or saved query rather than as a dbt model, which is a different finding with a different remedy.

Four outcomes, in decreasing order of preference:

| What you found | Do this |
|---|---|
| A model at the same grain with the measure | Nothing. Point the requester at it. This is a success, report it as one |
| A model at the same grain, missing the measure | Add the column to it — `dbt-adding-columns`. One model, one grain, no new surface |
| A model at a **coarser** grain, from a source with the detail | Build the finer grain and derive the coarser one from it, or leave the coarse model alone and add one sibling. Do not build a third grain in parallel |
| A model at a **finer** grain | Aggregate from it. A new model reading the same sources at a coarser grain is a second implementation of the same logic, and the two will disagree |
| Genuinely nothing comparable | Proceed to step 3 |

**A near-duplicate at a slightly different grain is the most expensive avoidable object in a mature project.** It does not fail. It sits beside the original producing numbers that are almost the same, and the difference is never small enough to ignore or large enough to be obviously a bug. Every consumer then has to know which one to use, that knowledge lives nowhere, and both must be maintained forever because nobody can prove which dashboards depend on which. Two models that differ only in grain should be one model plus an aggregation.

### Reuse is a spectrum, and "new model" is not the opposite of "reuse"

The table above compresses a judgment that deserves to be made explicitly, because the common failure is not choosing wrongly between two options — it is not noticing that the middle options exist.

Rank these by cost and take the cheapest one that actually answers the question:

| Mode | What it looks like | Choose it when |
|---|---|---|
| 1. **Use as-is** | No change. Point at the existing model | It already answers the question, even if the requester did not know the name |
| 2. **Extend in place** | Add a column to an existing model | The grain and purpose are unchanged and the new field belongs to the same entity — `dbt-adding-columns` |
| 3. **Compose on top** | A new, thin model that `ref()`s the existing one and aggregates, filters, or reshapes | The grain or shape differs but the underlying logic does not |
| 4. **Extract and share** | Pull the common logic into one upstream model, then have both consumers read it | Two models need the same logic and it currently exists once or is about to exist twice — `dbt-restructuring-dags` |
| 5. **Build new from sources** | A genuinely new pipeline reading sources directly | Nothing existing carries the needed logic, or reusing would mean recomputing what the existing model deliberately excludes |

**Mode 3 is the one that gets skipped, and skipping it is what causes both failure modes at once.** A different grain does not mean a new pipeline. It usually means a small model that reads the existing one — which is reuse, not duplication, because the logic still lives in exactly one place. The instinct to avoid a new *file* is what drives people to overload an existing model with flags and unions until it serves three purposes badly; the instinct to avoid a new *dependency* is what drives them to re-derive from sources and create the near-duplicate. Composition avoids both.

**One model feeding two downstream models is the desired outcome, not a smell.** Shared upstream logic with divergent downstream shapes is exactly what a DAG is for. Do not consolidate two legitimately different consumers into one wide model to reduce the node count — see the anti-pattern table in `dbt-restructuring-dags`, which covers the opposite error of over-consolidation and the giant-table shape it produces.

Two tests distinguish composition from duplication, and they are the whole decision:

1. **Where does the logic live after this change?** If the business rule, the join, or the filter would exist in two places, you are duplicating regardless of how the files are arranged. One definition, referenced twice, is reuse. Two definitions that agree today is a future discrepancy.
2. **Would fixing a bug in that logic require editing more than one model?** If yes, extract it (mode 4). This is the test that survives disagreement about taste, because it names a concrete future cost rather than an aesthetic.

Two cases where building new genuinely is right, and reusing is the mistake:

- **The existing model deliberately excludes what you need.** A model that filters to valid or billable rows cannot serve a question about the excluded rows. Reading it and trying to add them back is worse than reading the source — you would be undoing a rule the model exists to apply.
- **Reuse would force a full-detail rebuild of an aggregate.** If composing on top means re-reading detail that the existing model already collapsed, the dependency buys nothing and costs a scan.

Report the mode you chose and why, in one line, naming the mode you rejected. "Composing on top of `<model>` rather than extending it, because the grain differs and extending would mix two grains in one relation" is a design decision a reviewer can check. "Created a new model" is not.

### Should this be a model at all?

Three alternatives are cheaper than a model and each is correct in a recognisable situation. Check them before proceeding, because a model is a permanent maintenance commitment: it must build on a schedule, keep passing its tests, survive upstream changes, and be understood by whoever inherits it.

| Signal | What it should be instead | Why |
|---|---|---|
| The request is a formula over an existing fact, at a grain that fact already supports | A governed **metric definition** — in a semantic layer if the project has one, otherwise a single named, version-controlled expression with one owner | A model hard-codes the formula at one grain and cannot be re-aggregated. A metric definition is computed at whatever grain the consumer asks for, and there is exactly one of it |
| The question is asked once, to decide one thing | An analysis or a query, not a relation | Nothing needs to be scheduled, tested, or inherited. An exploratory model is the hardest kind to retire, because nobody can prove it is unused |
| An existing model answers it but nobody can find it | Documentation, or an exposure, or a rename | Building a second model does not fix discoverability; it halves it |

The cheapest possible outcome of a model request is still a clear "no, and here is what to use instead", delivered with the evidence. Report it as a result, not as a refusal.

**When the answer is genuinely "a new model", say which of the four questions in step 1 it answers that nothing existing does.** If you cannot name one, you are about to build the near-duplicate.

Per `dbt-gathering-context` step 3: a clean `grep` is not proof that nothing exists. It is evidence bounded by the names you thought to search. Say which names you searched.

---

## 3. Write the grain down, as a column list, before any SQL

This is the step the library exists to have. Everything else here is elaboration.

> **Grain:** one row per `<grain_column_1>` per `<grain_column_2>` per `<grain_column_3>`.

Write it as a **column list**, not a sentence. A sentence permits vagueness — "daily performance by account" hides whether a currency, a channel, or a status dimension is also in the key, and each of those changes the row count and every metric. A column list cannot hide it, which is the point.

**If you cannot write the list, you do not yet understand the request.** Return to step 1. Do not write SQL and read the grain off the result; that produces a model whose grain is whatever the joins happened to do, documented after the fact as though it were intended.

The grain outranks every other decision here: it is what makes a candidate dimension or measure admissible, what the key is built from, and the one rule — two grains never share a relation — that prevents the most common wrong total. It must also be turned into an actual test (uniqueness over the exact column set, `not_null` on every grain column, a row-count magnitude) in the same change as the model, checked against four known multi-grain traps (header measures on line rows, subtotals mixed with detail, sources unioned at different grains, a multi-valued attribute joined directly), and sanity-checked against the source with a `having count(*) > 1` query before any SQL is written. All of this — the reasoning, the test shapes, the trap table, and the source-check queries — is in [`grain-deep-dive.md`](grain-deep-dive.md). Read it now if this is the first time through; treat it as reference on a repeat pass.

Then confirm every grain column exists somewhere upstream at the required granularity. A grain that requires a dimension the sources do not carry is not a grain, it is a request for a new source.

## 4. Choose the shape

Four shapes. Pick one deliberately; the default of "whatever the joins produced" is not one of them.

| Shape | One row is | Choose when |
|---|---|---|
| **Fact** | A measurement or event at a point in time | The subject is something that *happened* and has measures. Keys plus numbers, narrow |
| **Dimension** | A thing that exists and has attributes | The subject is something that *is*, described rather than counted, joined to by facts |
| **Wide denormalized** | A fact with its dimension attributes already attached | A BI or analyst-serving terminal model where join cost, or the requirement that consumers write joins correctly, is the binding constraint |
| **Bridge** | One row per pair, resolving a many-to-many | An entity relates to many of another and vice versa |

### If it is a fact, which of the four kinds of fact?

"Fact" is not a shape decision, it is a family. Transaction, periodic snapshot, accumulating snapshot, and factless are genuinely different structures with different row lifecycles, and the request rarely names one. The clearest signal: **if the request contains the words "how long", "still open", or "stuck at", it needs an accumulating snapshot** — a row per process instance that is updated as milestones land — and implementing it as a transaction fact produces a model that can count events and cannot measure duration.

The recognition table, the update-in-place consequences for incremental builds, and the lag-storage rule that stops the design growing quadratically are in [`fact-and-dimension-patterns.md`](fact-and-dimension-patterns.md).

### If it is a dimension, which techniques apply?

Degenerate dimensions (an identifier with no attributes left, which needs no table), junk dimensions (many trivial flags collapsed into one), role-playing dimensions (one dimension referenced several times under different meanings, which must be named per role or every filter is ambiguous), outriggers (and why a versioned one multiplies the base dimension), and hierarchies (fixed, slightly ragged, and genuinely ragged, which need three different techniques) are all in [`fact-and-dimension-patterns.md`](fact-and-dimension-patterns.md), along with the surrogate-key argument and why a versioned dimension needs both a version key and a durable key.

### Wide tables are frequently correct, and the purist answer frequently is not

A strict star schema is a good default for a modelling *layer*. It is not automatically right for the model a BI tool or an analyst actually reads.

Choose wide when: consumers are humans or a BI semantic layer rather than other models; the join keys are stable; the warehouse is columnar, so unread columns cost little to store and nothing to scan; and the alternative is every consumer independently writing the same five joins — which is the real risk, because they will not all write them identically, and a `left join` written as an `inner` by one consumer is a silently different number.

Choose narrow with dimensions when: the attributes change and history matters (step 5 — a wide table freezes the attribute as of build time, which is a Type 1 decision made by accident); the same fact is consumed by many models that need different attributes; or the dimension is genuinely large and duplicating it multiplies real cost.

The honest cost of wide, so it is a choice and not a drift: an attribute change means rebuilding the fact rather than updating one dimension row; the column list grows monotonically because removing a column from a BI-facing table is a breaking change (`dbt-breaking-changes`); and the grain becomes easier to break, because every added attribute is an opportunity for a fan-out.

**Do not build a wide table by adding joins to an existing fact until its grain is uncertain.** Wide is a decision made at design time with the grain fixed. Wide-by-accretion is how a fact ends up at a grain nobody can state.

### Zooming out: star schema, one big table, Data Vault, or normalised

The choice above is a within-project one, made per model. The larger architectural question — and the one where published advice is most often dogma in either direction — is which family of shape the project should be in at all.

**Modern columnar engines removed the performance argument in both directions**, which is why the argument reopened: repeated low-cardinality values compress to nearly nothing, so "normalise to save space" is largely obsolete, and joins are no longer the dominant query cost, so "denormalise for speed" is real but smaller than advertised. What survives are arguments about correctness, reuse, and the cost of change — not speed. Cite a figure only if you measured it on this project's own workload, never a published headline; a benchmark that favours the wide table typically measures only queries that were already correct, which is exactly the property a wide table makes harder to guarantee. The full comparison — per consumer, per cost, with what each shape commits you to permanently, and the two cases where the answer is not a model at all — is in [`shape-tradeoffs.md`](shape-tradeoffs.md). Read it before recommending any of the four, and especially before recommending against one.

### Bridges, and the trap of skipping one

When entity A relates to many B and B to many A, joining them directly fans out and every measure on the A side inflates by the number of matching B rows. No test fails, because the key of the joined result is still unique over the columns anyone thought to test.

Model it explicitly: a bridge at the grain `one row per <a_key> per <b_key>`. Then decide, in writing, **which side the measures live on** and whether a weighting factor is required to make them sum correctly across the bridge. Skipping the bridge and hoping is the single most reliable way to overstate a total.

Three follow-on decisions — whether the bridge carries allocation weights that must sum to 1, whether a bridge over versioned entities needs its own validity windows, and whether to use a group-key bridge that protects a naive consumer instead of a pair bridge that does not — are in [`fact-and-dimension-patterns.md`](fact-and-dimension-patterns.md).

### Model the absence when absence is the question

If the question is about something that did *not* happen — an entity with no activity in a period, a period with no rows — the shape must contain a row for the non-event. A relation built only from events cannot express absence: the row simply is not there, and a BI tool renders a missing row identically to a zero when it is aggregated and invisibly differently when it is filtered or ranked.

The construction is a complete key space (every entity crossed with every period in scope) left-joined to the events, with `coalesce` on the measures. Decide at design time whether the cell is `0` or `null`. They mean different things — zero is "we know it was none", null is "we do not know" — and no consumer can recover the distinction later.

The cost is real: the row count becomes the product of the key space, not the count of events. Decide it deliberately, and bound the key space to the periods and entities in scope.

### Time grain

State the time grain as a column, with its timezone, using `naming.timestamp_column_suffix` if the contract sets one. Two decisions people skip:

- **A finer time grain multiplies row count and can change additivity.** A daily grain sums cleanly to a month; an hourly grain summed to a month is the same number but a much larger scan. A grain finer than the source's own resolution is fabricated precision.
- **A period grain requires the period's boundary rule in writing.** Which day a midnight-boundary event belongs to, and whether a partial current period is included. Both are questions someone will ask about a number that looks wrong, and both are unanswerable after the fact.

## 5. Choose the slowly-changing-dimension type — at design time

Every descriptive attribute in the design is implicitly one of several types. Choosing by default means choosing Type 1, because overwriting is what happens when nobody decides.

**One criterion decides most of it: does anyone need to ask a question "as of" a past date?**

| Type | Behavior | Choose when | Cost of choosing it |
|---|---|---|---|
| **Type 0** — retain original | The value is set once and never changes, deliberately | The attribute means "original": the value at acquisition, the first classification, a durable identifier | You must actively exclude it from update logic, or Type 1 happens to it silently |
| **Type 1** — overwrite | Only the current value exists | Corrections to values that were always wrong; attributes nobody reports historically | Every historical report silently restates the moment the attribute changes |
| **Type 2** — versioned history | A row per version, with validity windows | The attribute changes for real business reasons **and** past reporting must stay stable | Every consumer must now join on a point in time, not just a key. Grain of the dimension is no longer one row per entity |
| **Type 3** — previous-value column | Current value plus one prior value | Exactly one prior value is ever needed, usually across a single known reorganization | Silently lossy on the second change. Almost never the right answer; when it is, it is obviously so |

Three decisions that resolve most real cases:

- **A correction is not a change.** If the old value was simply wrong — a typo, a bad load — overwrite it. Type 2 preserving a value that was never true pollutes history with a version that means nothing, and it cannot be removed later without rewriting validity windows. The data cannot tell you which case you are in: only a person knows whether an old value was wrong or merely old.
- **"Historical reporting must be stable" is a business requirement, not a preference.** If a report run today for last quarter must match the one run last quarter, the attribute is Type 2 and there is no cheaper option. If restatement is acceptable — and for many attributes it genuinely is, because the current classification is the one people want — Type 1 is correct and far simpler.
- **Wanting both is common, and it is not a contradiction.** "Show me last quarter as it was reported, and also show me all history grouped by today's classification" is a routine request, and it is satisfiable — by keeping type 1 copies of the attributes alongside the versioned ones, or by exposing two relations over the same dimension. The mistake is treating it as a choice and picking one, then discovering the other requirement six months later when the versioned history has already been captured in a form that cannot answer it.

The remaining types exist because that last requirement, plus the case of a fast-changing attribute group inside a large dimension, are not satisfiable by types 0 through 3. The full range with the decision procedure — including which of types 4 through 7 to use and how each is actually built in dbt — is in [`fact-and-dimension-patterns.md`](fact-and-dimension-patterns.md).

Two consequences to write into the design rather than discover:

- **Type 2 changes the dimension's grain**, from one row per entity to one row per entity per version. Every fact joining to it must join on the validity window as well as the key, or it fans out. This is the bridge trap in a different costume, and it inflates measures identically.
- **A wide denormalized table is a Type 1 decision for every attribute it absorbs**, made implicitly, and it freezes the attribute as of the build rather than as of the event. If any absorbed attribute needs Type 2 semantics, either resolve the attribute *as of the event* when building the wide table and say so, or keep that attribute in a separate dimension.

### Data that does not arrive together

Two failure modes that this step must anticipate, because both are unrecoverable after the fact:

- **A fact arrives before its dimension context exists.** Dropping it understates every total invisibly. The conventional remedy is a placeholder dimension member overwritten when the real context lands — which means any report run in between attributed the fact to "unknown" and will restate. If restatement is unacceptable, the placeholder approach is unacceptable, and the design needs a quarantine with a visible backlog count instead.
- **A fact arrives after the period it belongs to.** Resolving it against the *current* dimension version back-dates today's attributes onto an old event. Correct resolution is by event timestamp against the validity window — which requires Type 2, and is one of the strongest practical arguments for it. Separately, if the model is incremental and its boundary filter uses the event timestamp, the late row falls outside the window and is never loaded at all.

Both, plus what a retroactive change to an already-versioned attribute costs, are in [`fact-and-dimension-patterns.md`](fact-and-dimension-patterns.md).

Mechanics — strategies, `check_cols`, hard deletes, what a full refresh destroys — are in `dbt-snapshots`, and the mechanics matter because history captured wrongly cannot be recovered. Do not start there. Decide the type here first; a snapshot built before anyone decided whether history was needed is an expensive object with no consumer.

---

## 6. Classify the additivity of every measure

For each measure in the design, write one of three words next to it. This takes a minute and it is the difference between a dashboard that is right and one that is wrong in a way nobody can see.

| Class | Sums across | Examples | Design obligation |
|---|---|---|---|
| **Fully additive** | Every dimension, including time | Counts, amounts, quantities, durations | None. Store it and let anything aggregate it |
| **Semi-additive** | Every dimension **except** time | Balances, inventory levels, headcount, any point-in-time state | Store the snapshot value. Document that time aggregation is `last` or `average`, never `sum` |
| **Non-additive** | Nothing | Ratios, rates, percentages, averages, per-unit values | **Do not store the computed value as the model's answer.** Store the numerator and the denominator |

**Fully additive** is the only class that is safe by default. A semi-additive measure looks identical to an additive one by inspection — the only signal is the semantics, which is why the classification has to be written down. A non-additive measure stored as a computed rate gives every row equal weight regardless of its denominator and can be off by a large factor while looking entirely plausible; store the numerator and denominator instead and let the consumer compute the ratio. The reasoning behind each class, the traps that recur across them (distinct counts, running totals, multi-currency measures, null-vs-zero), and how to keep a same-named measure conformed across models are in [`additivity.md`](additivity.md).

---

## 7. Write the design down

Short. A paragraph and two lists — this is a design note, not a document, and a document does not get read or written.

```markdown
## <model_name>

**Question it answers:** <the decision from step 1, in one sentence>
**Grain:** one row per <grain_column_1> per <grain_column_2> per <grain_column_3>
**Key:** <surrogate_key_column>, built from exactly the grain columns
**Time grain and timezone:** <time_column> — <granularity>, <timezone>, <calendar>
**Shape:** <fact | dimension | wide | bridge>; <layer> layer
**SCD type per changing attribute:** <attribute> = type <1|2|3>, because <reason>

**Measures**
| Column | Additivity | Aggregation across time | Notes |
|---|---|---|---|
| <measure_a> | fully additive | sum | |
| <measure_b> | semi-additive | last | point-in-time state |
| <numerator>, <denominator> | fully additive | sum | rate = sum/sum, never average |

**Dimensions:** <dimension_column_1>, <dimension_column_2>
**Refresh cadence:** <cadence, from the contract's available schedules>
**Absence handled:** <yes — key space is X; or no — event rows only>
**Reconciles against:** <the external figure this must tie to, and the tolerance — or "nothing">
**Restatement acceptable:** <yes | no — and what that forced in the design>
**Explicitly out of scope:** <the adjacent questions this does not answer>

**Rejected alternatives:** <the shape or grain that was considered and why it lost>
**Open decisions:** <anything asked and not yet answered>
```

Four things earn their place and are the ones usually omitted:

- **Out of scope.** It is the record that a question was considered and declined, which is what prevents the near-duplicate model in step 2 from being built by the next person.
- **Rejected alternatives.** One line each. This is the entry that stops the design being relitigated: the next person's first instinct will be one of the alternatives, and without a record they will assume nobody thought of it. "Considered a wide table; rejected because the segment attribute needs as-of-event history" ends that conversation before it starts.
- **Open decisions.** A design note with an unresolved semantic question is honest and reviewable. One that resolved it silently is neither.
- **The grain as a column list.** This is the line `dbt-verification` reconciles against in its row-count check. Without it, that check confirms the grain the SQL produced, which is not a check.

### Name it so the design is legible from the name

The name is the only part of the design that every consumer sees, so it should carry the two facts people most need: what the entity is, and — where the project's pattern has a slot for it — the grain. A name that states the grain makes a grain mismatch visible at the call site, before anyone opens the file. `dbt-project-conventions` owns the pattern; the design-time obligation is to have a grain worth encoding.

Two naming decisions that are really design decisions:

- **A name that describes an output rather than an entity is a warning.** If the only honest name for the model is the name of a dashboard, step 1 has not been finished: the model is the artifact, not the dataset.
- **Do not encode a filter in the name unless the filter is permanent.** A name asserting a subset — a status, a region, a single segment — becomes a lie the first time someone wants the model without that filter, and the usual response is a second model with a slightly different name. Filters belong to the consumer for exactly the reason top-N does.

Put the grain statement and the additivity notes into the model's YAML description as well, so they live with the model rather than in a note nobody finds — see `dbt-authoring-schema-yaml`. A grain documented only in a plan is a grain that will be contradicted by the third change to the model.

**Confirm the design note with the requester before implementing.** This is the last cheap correction point in the entire lifecycle; after this, being wrong costs a rebuild and, for anything with history, may cost data.

---

## 8. Know when to stop and ask

Three classes here are un-derivable, and all three are load-bearing. Per `dbt-gathering-context` step 4, no volume of querying resolves them, and a plausible guess is indistinguishable from a correct answer right up until reconciliation.

| Un-derivable | Why no query answers it | What a wrong guess costs |
|---|---|---|
| **The grain** | Whether a duplicate is a legitimate second event or a defect is intent. The data shows the duplicate; only a person knows what it means | Every measure in the model, permanently, and a rebuild to fix |
| **Metric semantics** | SQL shows what is computed, never what the business means, nor which of two rival definitions is canonical | A number that reconciles against nothing, discovered by a stakeholder rather than by you |
| **Which source is authoritative** | Two systems disagree; choosing between them is a business decision with an owner | The wrong system becomes the definition, and the decision is invisible in the SQL |

Five more that recur specifically in design, each of which forecloses a later option if answered wrongly by default:

| Un-derivable | What a default answer silently commits you to |
|---|---|
| Whether historical reporting must be stable | The Type 1 default. The first attribute change restates every past report, and the prior values are gone — the only one here that costs **unrecoverable** history |
| Whether a partial current period is included | Whichever the SQL happened to do, and a chart whose newest point is not comparable to the others |
| The boundary rule for a fiscal or broadcast calendar | A quarter that starts on a different day from the one finance uses, discovered at a quarter boundary |
| Whether a **restatement** of already-published figures is acceptable | Either a backfill nobody sanctioned, or a step change nobody documented — see the corresponding decision in `dbt-unifying-sources` |
| The allocation rule for pushing a parent measure to a child grain | An arithmetic convenience standing in for a business rule, which changes who the numbers favour |

Note what is *not* on either list. Whether a duplicate exists is derivable; whether it is legitimate is not. Whether two models compute a measure differently is derivable; which one is canonical is not. The pattern holds throughout: **the data tells you what is, and a person tells you what it means.** Reporting the derived fact alongside the question is what makes the question answerable in seconds.

Everything else in this skill is derivable and must not be asked. Whether the proposed key is unique, what the source's real granularity is, whether a comparable model already exists, what the row-count magnitude will be, which cadences the orchestrator actually runs — all of these are one query or one command away, and asking them spends the credibility you need for the three that matter.

Ask once, batched, with the derived facts attached and a recommendation for each. "The source has 2.3 rows per `<proposed_key>`; the extra rows are status transitions. Should the model be one row per transition, or one row per entity at its latest status? I would take the transition grain and let the consumer filter — it answers both questions." That is answerable in seconds. "What grain do you want?" is not.

---

## Hand off to implementation

The design note is the input, not the output. Next:

- `dbt-authoring-sql-models` — its step 1 asks for the grain and the layer, which you now have in writing. Its surrogate-key section takes the grain column list directly, and the "exactly the grain columns and no others" rule is only checkable because the list exists.
- `dbt-project-conventions` — turn the shape, layer, and grain into a name that satisfies `naming.model_pattern`.
- `dbt-authoring-schema-yaml` — the grain statement becomes the model description; the additivity classification becomes the measure descriptions; the key becomes the tested column.
- `dbt-incremental-models` — if the design implies incremental materialization, the grain is the `unique_key` and the time grain is the boundary column.
- `dbt-snapshots` — only if step 5 chose Type 2.
- `dbt-verification` — its row-count reconciliation now has a written grain to test against rather than one inferred from the output it is checking.
- `dbt-unifying-sources` — if the design draws the same concept from more than one system, the conforming and identity-stitching decisions belong there, and they are design decisions rather than implementation details.

## Completion checklist

- [ ] Contract read; `layers`, `naming`, `schedules`, and `project.timezone` consulted, or their absence stated and guidance labelled generic
- [ ] The decision this model drives is written down in one sentence
- [ ] "What does one row mean" asked in those words and answered by the requester, not inferred
- [ ] Requested **output shape** distinguished from the model shape; any pivot, top-N, ranking, or subtotal recognized as presentation
- [ ] Timezone **and** calendar both stated for every date and period column
- [ ] Every ambiguous metric term defined specifically, in writing, by whoever owns the definition
- [ ] Authoritative source named where two sources could answer, and named by a person
- [ ] What the output must reconcile against, and to what tolerance, established — or "nothing" stated deliberately
- [ ] Whether previously-reported numbers may change asked and answered, and its consequences carried into the design
- [ ] Existing models searched by entity name, synonym, **and** metric column name; every `bi.consumers[].repo_path` searched too
- [ ] Search scope stated — no clean grep reported as proof nothing exists
- [ ] Extend-or-aggregate preferred over a new model wherever an existing grain matched
- [ ] The metric-definition, one-off-analysis, and discoverability alternatives to building a model considered and ruled out
- [ ] Grain written as a **column list**, not a sentence, before any SQL
- [ ] Grain converted into an actual test: uniqueness over the exact column set, `not_null` on every grain column, and a row-count magnitude expectation
- [ ] Proposed grain tested against source data with a `having count(*) > 1` query
- [ ] Rows-vs-distinct-keys discrepancy classified as missing column, legitimate multiplicity, or defect — never silently deduplicated
- [ ] Checked for the four multi-grain traps: header measures on line rows, subtotals mixed with detail, sources unioned at different grains, a multi-valued attribute joined directly
- [ ] Any allocation of a parent measure down to a child grain uses a rule supplied by the business, recorded in the description
- [ ] Every grain column confirmed to exist upstream at the required granularity
- [ ] Shape chosen deliberately: fact, dimension, wide, or bridge — with the reason
- [ ] If a fact: which of transaction, periodic snapshot, accumulating snapshot, or factless — and why that one
- [ ] Accumulating snapshots: boundary filter based on modification time, not process start, and lags stored against the start point
- [ ] Star / wide / vault / normalised choice made against consumer and cost, not against a methodology preference
- [ ] Many-to-many relationships modelled with an explicit bridge, and the measure side plus any weighting decided
- [ ] Absence modelled where the question is about non-events, with `0` versus `null` chosen deliberately
- [ ] Null dimension keys resolved to an explicit unknown member rather than left null
- [ ] SCD type chosen per changing attribute against the "as of a past date" criterion
- [ ] "As-was and as-is both needed" recognised as satisfiable rather than treated as a choice between them
- [ ] Correction-versus-change distinguished by a person before any history is captured
- [ ] Type 2's effect on the dimension's grain, and on every fact joining to it, stated
- [ ] Wide-table shape recognized as an implicit Type 1 decision for every absorbed attribute
- [ ] Late-arriving dimension context and late-arriving facts both considered; placeholder-versus-quarantine decided against the restatement answer
- [ ] Incremental boundary checked against late arrival — event-time boundary would lose a late row silently
- [ ] Every measure classified fully additive, semi-additive, or non-additive
- [ ] Non-additive measures stored as numerator and denominator, not only as a computed rate
- [ ] Weighted averages and averages-of-averages recognised as ratios
- [ ] Semi-additive measures carry their time-aggregation rule — `last`, `first`, or `average` — and whose requirement it is
- [ ] Distinct counts flagged as non-additive over the dimension they are distinct across
- [ ] Running and period-to-date totals kept out of the model, or their recomputation cost accepted deliberately
- [ ] Multi-unit or multi-currency measures store the original value and unit alongside the standard one
- [ ] A measure sharing a name with a measure in another model confirmed to be computed identically, or renamed on purpose
- [ ] Design note written: question, grain, key, shape, measures with additivity, dimensions, cadence, reconciliation target, out of scope, rejected alternatives, open decisions
- [ ] Rejected alternatives recorded, one line each, so the design is not relitigated
- [ ] Name describes an entity and its grain, not an output, and encodes no impermanent filter
- [ ] Grain and additivity carried into the model's YAML description, not left only in the note
- [ ] Design note confirmed with the requester **before** implementation began
- [ ] Un-derivable questions asked once, batched, with derived facts and a recommendation
- [ ] Nothing asked that a query would have answered

## Common failure modes

1. **Implementing the requested artifact instead of the underlying dataset.** A pivot becomes columns, a chart becomes a model, a top-N becomes a filter baked into SQL. Each one requires a schema change the first time the requester's question changes slightly — which is always, and soon. Build the grain; let the consumer reshape.

2. **Writing SQL first and reading the grain off the result.** The grain becomes whatever the joins happened to produce, and it is then documented as though it were intended. Every subsequent change is made against a grain nobody chose, and the model's `unique` test passes because the key was derived from the same accident.

3. **Stating the grain as a sentence instead of a column list.** "Daily performance by account" is compatible with four different keys. The one that ends up in the surrogate key is decided by whoever writes the SQL, silently, and the difference is a fan-out.

4. **Never testing the proposed grain against the source.** The design assumes the key is unique, the source disagrees, and the discrepancy is resolved during implementation by adding a `distinct` or a `qualify` — which discards real rows and looks like tidying up in the diff.

5. **Building a near-duplicate at a slightly different grain.** The most expensive avoidable object in a mature project. Nothing errors. Two models produce almost-equal numbers forever, no consumer knows which is right, both must be maintained, and neither can be retired because nobody can prove what depends on it.

6. **Storing a ratio and letting a dashboard average it.** Every row gets equal weight regardless of its denominator, so a row covering ten events counts as much as one covering ten thousand. The number is wrong by an arbitrary factor, moves in the wrong direction, and looks entirely plausible. No test detects it, because the stored value is correct at its own grain.

7. **Summing a semi-additive measure across time.** A daily balance summed to a month is roughly thirty times too large. Correct across every other dimension, which is why the measure looks innocent and why the aggregation rule has to be written into the description at design time.

8. **Choosing Type 1 by default, by not choosing.** Overwriting is what happens when nobody decides. The first time an attribute changes, every historical report restates, and the only evidence of the decision is that history was never captured — which cannot be fixed retroactively, because the prior values are gone.

9. **Absorbing a Type 2 attribute into a wide table.** The attribute freezes as of build time rather than as of the event, so the wide table answers "what was the total for accounts currently in that segment" while everyone reads it as "what was the total for accounts in that segment then." Two different questions, one column, no error.

10. **Joining a many-to-many directly instead of building a bridge.** Measures inflate by the number of matching rows on the other side. The result's key is still unique over the columns anyone thought to test, so every test passes and every total is overstated.

11. **Modelling only events when the question is about absence.** An entity with no activity has no row. It is indistinguishable from a zero once aggregated and behaves differently under filtering and ranking, so the same dashboard reports different populations depending on which tile you read.

12. **Leaving the timezone or the calendar unstated.** Both change every number. Once a date column exists without a timezone marker, the ambiguity is undetectable, and reconciling against a system on a different boundary becomes an investigation rather than a lookup.

13. **Guessing a metric definition because the guess is plausible.** "Revenue" has a dozen definitions and SQL cannot tell you which one is canonical. A wrong guess produces a model that reconciles against nothing, and the discovery happens in front of a stakeholder rather than in review.

14. **Skipping the design note because the model seems obvious.** The note takes five minutes and is the only artifact that makes the grain reviewable, the additivity communicable, and `dbt-verification`'s row-count check non-circular. Models that seem obvious at design time are exactly the ones whose grain nobody can state six months later.

15. **Carrying a parent-level measure onto child-level rows.** A charge or total belonging to the parent repeats identically across every child row, and any `sum` returns a multiple of the truth. The multiple is not an integer, because the number of children varies, so the total is wrong by an amount that looks like a plausible number rather than like a doubling.

16. **Implementing a duration question as a transaction fact.** Rows per event can count events and cannot measure how long a process takes or how many instances are still open, because those require one row per process instance updated in place. Discovered when the second question arrives, and the fix is a different model, not a new column.

17. **An accumulating snapshot whose incremental boundary filters on the process start date.** Rows for processes that began before the window are never revisited, so milestones reached today for an order placed last month are silently never recorded. The model keeps building successfully and the in-flight population is permanently wrong.

18. **Resolving a late-arriving fact against the current dimension version.** Today's attributes get back-dated onto an old event, so historical breakdowns change every time a dimension changes. Looks like data drift, is actually a join written against the key alone.

19. **A null dimension key silently dropping rows.** An inner join loses them and a grouping omits them, so the total is short by an amount nothing explains. An explicit unknown member makes the same rows visible as an "unknown" bucket someone can ask about.

20. **Choosing a shape by methodology rather than by consumer.** Both directions fail: a strict star schema handed to analysts who then each write the joins differently, and a wide table per dashboard that duplicates measure logic four times. The performance case for wide tables rests on queries that were already correct, which is exactly the property a wide table makes hard to guarantee.

21. **Storing a period-to-date or running total in the model.** It is not additive, so summing two rows double-counts the overlap, and it has to be recomputed whenever the period definition changes — at which point every consumer that copied the old boundary disagrees with the new one.

22. **Building a model when the answer was a metric definition.** The formula gets fixed at one grain and cannot be re-aggregated, so the next grain request produces a second model with the same formula. Two implementations, and they diverge at the first definition change.

23. **Two models with a same-named measure computed differently.** Every consumer has already assumed the names match, so the divergence surfaces as a reconciliation failure rather than as an error. Preventable only at design time, by conforming the definition or deliberately choosing a different name.
