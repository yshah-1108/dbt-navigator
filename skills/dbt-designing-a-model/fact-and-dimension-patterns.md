# Fact and dimension patterns

The vocabulary in this document is Kimball's, because it is the only vocabulary for these structures that two engineers who have never met will both recognise. The reason to know it is not fidelity to a methodology. It is that each named pattern encodes a failure someone already had, and the name is the cheapest way to recognise you are about to have it too.

Read this when the design note is being written — the choices here are grain-level choices, and every one of them is expensive to change after the first consumer exists.

## 1. Which kind of fact table the request actually needs

There are four shapes, and the request almost never names one. It describes an output, and the shape is an inference. Getting it wrong is not a style error: a request that needs an accumulating snapshot, implemented as a transaction fact, produces a model that can answer "how many orders reached this step" and cannot answer "how long does the process take", which was the actual question.

| Type | One row is | Rows are | Recognise it by |
|---|---|---|---|
| **Transaction** | One measurement event at a point in time | Inserted once, never updated | The request describes something that *happened*: a payment, a signup, a shipment. Sparse — no event, no row |
| **Periodic snapshot** | The state of one entity over one standard period | One row per entity per period, whether or not anything happened | The request describes a *level* or *balance* at regular intervals. Dense — a row exists even for a quiet period |
| **Accumulating snapshot** | One instance of a process with a defined start and end | Inserted at process start, then **updated** as milestones are reached | The request contains the words "how long", "still open", "stuck at", "conversion between steps" |
| **Factless** | A relationship or an eligibility, with no measure | Inserted when the entities coincide | The request counts occurrences, or asks what did *not* happen |

### Transaction

The default and the most flexible. At its atomic grain it carries the most dimensions and supports the widest range of questions, including ones nobody has asked yet — which is the argument for starting at the atomic grain rather than a summary grain. A summary grain presupposes the questions.

It cannot answer state questions cheaply. "How many subscriptions were active on the 14th" from a transaction fact of subscription events requires a window over all history per entity; from a periodic snapshot it is a filter.

### Periodic snapshot

The distinguishing property is **density**: a row exists for every entity in every period regardless of activity, so an absent row means "not in scope", not "no activity". That is exactly the absence-modelling problem, and it is why a periodic snapshot is the right shape when the question is about levels rather than events.

Two things to decide in writing:

- **The measures are usually semi-additive.** A balance, a level, a headcount. See the additivity section of the main skill — summing across time is the specific bug this shape invites.
- **Cost is the product of the key space and the number of periods**, not the count of events. A daily snapshot of a large entity population is a large table forever. Bound the key space, and consider whether the period needs to be daily.

### Accumulating snapshot

The only fact shape whose rows are **updated in place**, which makes it the only one that is awkward in an append-only incremental model. One row per process instance, with a date column per milestone, plus lag measures between milestones.

Design consequences that are not obvious:

- **Milestone dates that have not happened yet are null**, and the null means "not yet", not "unknown". A consumer filtering on a milestone date silently excludes in-flight instances, which is often the population they wanted. Consider a status column alongside the dates so "still open" is expressible without reasoning about nulls.
- **Store the lag from the start point to each milestone, not every pairwise lag.** With *n* milestones there are *n*(*n*−1)/2 pairwise durations; storing each one against the start makes any pair a subtraction of two stored columns. This is the difference between a design that survives a new milestone and one that grows quadratically.
- **In dbt this is a `merge` incremental keyed on the process instance**, not an append. The rows that change are old rows, and the boundary filter must be based on when the row was last *modified*, not when the process started — a filter on the start date will never see an update to a process that began outside the window. That is the most common way an accumulating snapshot silently stops updating.
- **A lag is only meaningful once the process is complete.** An average duration computed over a population that includes in-flight instances is biased downward, because the slow ones have not finished yet and contribute nothing. Decide whether the model excludes incomplete instances from duration measures, or exposes the completion flag so the consumer can.

### Factless

Two genuinely different uses, and conflating them produces a model that answers neither.

- **Event with no measure.** Something happened and the interesting content is which entities coincided: a login, an eligibility, an assignment. The measure is `count(*)`, and adding a literal `1` column adds nothing except a column that can be summed incorrectly after a fan-out.
- **Coverage, for answering what did not happen.** A relation containing every combination that *could* have occurred. The absence answer is the coverage relation minus the activity relation. This is the same construction as the absence-modelling section of the main skill, and the reason it needs its own model is that the coverage relation is a real design artifact with a real cost — you must decide what "could have occurred" means, and that is a business decision, not a cross join.

### Consolidating two processes into one fact

When two processes are genuinely at the same grain — actuals and forecast, requests and fulfilments — they can share a fact table, which makes comparison a subtraction rather than a multi-query stitch. This is worth it when the comparison is the main question being asked.

The cost is that the model now fails when either upstream process fails, its grain must be the *intersection* of both processes' dimensionality, and a measure that only one process reports needs the null-versus-zero decision made explicitly. If the two processes are at different grains, do not consolidate; allocate or aggregate one of them first, deliberately, and say which.

**Never join two fact tables directly on their shared dimension keys.** The cardinality of that join is uncontrollable and the result is arbitrary multiplication. Aggregate each fact to the common grain separately and join the aggregates, or let the consumer align them on shared dimension values. This is the fan-out trap wearing a costume that looks like normal SQL.

## 2. Dimension design

### Surrogate keys, and the honest version of the argument

The classic argument for an integer surrogate key on every dimension is that the natural key cannot serve as the primary key once history is versioned (there are then several rows per natural key), that natural keys from different systems are incompatible, and that the warehouse should control its own key space.

The first point is structurally true and is the one that matters. The others are weaker on a modern columnar engine: sequential integer assignment requires coordinated state that distributed builds do not have, and a deterministic hash of the natural key plus the source system achieves the identity goal without the coordination. Most dbt projects therefore use a hashed surrogate key, and that is a reasonable deviation from the classic prescription — see the surrogate-key mechanics in `dbt-authoring-sql-models`.

What does *not* survive as a preference:

- **A versioned dimension needs a key that is unique per version, and a separate durable key that is stable across versions.** Two columns, two jobs. The version key is what facts join on to get as-of-then attributes; the durable key is what identifies the entity across its whole life and is what you group by to ask "everything this entity ever did". A design with only one of them cannot express one of those two questions.
- **A date dimension is the standard exception.** A meaningful key such as an integer `yyyymmdd` is conventional, readable in a query result, and supports partitioning. Do not hash it.
- **Nulls do not belong in a dimension key on the fact side.** A null foreign key cannot join, so the row silently drops from any inner join and from any grouping by that dimension. Use an explicit "unknown" or "not applicable" member in the dimension and point at it, so the row survives and is visibly attributed to the unknown bucket. This single decision is the difference between a total that is short by an unexplained amount and one that shows an "unknown" row someone will ask about.

### Degenerate dimensions

A dimension whose only content is its key — an order number on a model at order-line grain, once every descriptive attribute of the order has been inherited by the line. There is no dimension table to build. Leave the identifier on the fact and say in the description that it is deliberate.

The failure mode this prevents: building a one-column dimension table for it, joining to it forever, and implying to future readers that some attribute lives there.

### Junk dimensions

A process produces a handful of low-cardinality flags and indicators. Modelling each as its own dimension gives a fact table with a dozen foreign keys to trivial tables. Combining them into one dimension of the combinations that **actually occur** — not the full Cartesian product — collapses that.

Worth it when the flags are numerous and independently uninteresting. Not worth it when there are two of them, or when a flag is a genuine analytical axis people filter on constantly. The honest tradeoff: a junk dimension makes the fact narrower and adds an indirection that every consumer must learn. On a wide-table serving model, the flags belong inline and there is no junk dimension at all.

### Role-playing dimensions

One physical dimension referenced several times from the same fact under different meanings — an order date, a ship date, and a cancel date all pointing at the calendar. Expose each role as a separate view or aliased reference with role-prefixed column names.

The failure mode: if all three point at the same relation with the same column names, a consumer writing `where month_name = 'March'` has no way to know which date they filtered on, and the query is ambiguous in a way that returns a plausible number. Naming the roles is what makes the ambiguity impossible to express.

### Outriggers, and why to be sparing

A dimension holding a foreign key to another dimension. Legitimate — a rate table referenced by an entity dimension — but it has a specific expensive failure: if the outrigger is versioned, a change there forces version processing in the base dimension too, and the base dimension's row count multiplies for a reason that has nothing to do with the base entity.

The usual remedy is to demote the relationship to the fact: put both dimension keys on the fact instead. The correlation between the two dimensions is then only discoverable by traversing the fact, which is an acceptable cost when the alternative is uncontrolled growth.

### Bridges for many-to-many

The main skill states the trap. Three additions that decide real designs:

- **Decide whether the bridge carries a weighting factor.** If a measure on the A side must be attributed across matching B rows, the bridge needs an allocation weight and the weights per group must sum to 1, or every total across the bridge is inflated. If instead the correct behaviour is that the measure is reported in full for each B — an "impact" style question — then the total is deliberately greater than the source total, and that must be stated in the description or someone will report it as a bug and someone else will "fix" it.
- **A bridge over versioned entities needs validity windows of its own**, and the consumer must constrain it to a moment in time. Without that, an entity is linked to every group it was ever part of, simultaneously.
- **A group-key bridge is a different shape from a pair bridge.** One row per (group, member) with a group key on the fact keeps the fact at its original grain, at the cost of an extra indirection. One row per (A, B) is simpler and forces every consumer to think about fan-out. Choose deliberately; the group-key form is the one that protects a naive consumer.

### Hierarchies

| Shape | Recognise it by | Model it as |
|---|---|---|
| **Fixed depth** | Every level has an agreed name, and each level is many-to-one on the next | Positional attributes on the dimension: one column per level. Easiest to query, predictable performance |
| **Slightly ragged** | Depth varies within a narrow known range | Still positional, to the maximum depth, with a stated rule for filling the unused levels — repeat the parent, or use an explicit "not applicable" member. Say which |
| **Ragged / unbounded** | Depth is genuinely unknown, or the structure is recursive | A path-enumeration column, or a bridge with one row per ancestor-descendant path |

The two ragged techniques are not equivalent, and the difference matters:

- **A path string** on each dimension row handles most descendant queries with an ordinary prefix match and needs no extra relation. It cannot express alternative hierarchies over the same entities, or shared ownership, and any restructuring higher up relabels every descendant.
- **A path bridge** — one row per (ancestor, descendant, distance) — handles alternative hierarchies, shared ownership, and time-varying structure, and costs a relation whose row count grows with depth as well as breadth. It also fans out by construction, so every measure summed through it needs the bridge discipline above.

The decision that must be made in writing: **whether the hierarchy is time-varying.** If reports about last quarter must use last quarter's structure, the hierarchy is versioned and neither technique is cheap. If everyone wants today's structure applied to all history, it is not, and the whole problem is much smaller. Nobody volunteers this; ask.

### Flags, codes, and numbers that might be attributes

- **Expand cryptic codes into readable attributes** rather than requiring every consumer to memorise the encoding or write the same `case` statement. A code with embedded meaning — where each character position means something — should be decomposed into one attribute per position, and kept as the raw code as well.
- **A numeric value is a measure if it is aggregated, and an attribute if it is filtered and grouped by.** A list price used in calculations is a measure; the same price used to band entities into ranges is an attribute. It is legitimately both, and modelling it as both is not duplication, it is two different uses. What is not legitimate is putting a value that consumers sum into a dimension, where it will be summed once per joined fact row.
- **Prefer a descriptive member over a null in a dimension attribute.** Engines differ in how they group and filter nulls, and a null renders as blank in every consumer, which is indistinguishable from a rendering bug.

## 3. Slowly changing dimensions: the full range

The main skill covers the three types that account for most decisions. The extended set exists because two real requirements — "past reports must not move" and "I want to see all history grouped by the current classification" — are both legitimate and are not satisfiable by the same structure.

| Type | Behaviour | Genuinely warranted when | In dbt |
|---|---|---|---|
| **0** | Value is captured once and never changes, by design | The attribute means "original" — the value at acquisition, the first classification, a durable identifier. Also most calendar attributes | Set it in the first load and exclude it from any update logic. On a `merge` incremental, list update columns explicitly so this one is never overwritten |
| **1** | Overwrite | Corrections; attributes nobody reports historically | An ordinary model, or `merge` with the attribute in the update set. The default, including by accident |
| **2** | New row per version, with validity window | The attribute changes for real business reasons **and** past reporting must stay stable | A snapshot, or a hand-built incremental that closes the prior row. See `dbt-snapshots` |
| **3** | Extra column holding one prior value | Exactly one prior value is ever needed, usually across a single known restructuring, and both views must be available side by side | Two columns in an ordinary model. Lossy on the second change |
| **4** | Split the fast-changing attributes into their own small dimension; the fact carries both keys | One group of attributes changes far faster than the rest of a large dimension, so type 2 on the whole dimension would multiply a large table | Two models. The fact gets a second foreign key — which means it is a change to the fact's own design, not only the dimension's |
| **5** | Type 4, plus a current-value reference to the small dimension from the base dimension | You need type 4's history *and* the ability to group all history by the current assignment without traversing the fact | Type 4 plus a type 1 column, refreshed every build |
| **6** | Type 2, plus type 1 copies of the same attributes on every version row | The single most common real requirement: "as of then" *and* "as it is now", from one relation | A model on top of a snapshot that adds `current_*` columns via a window over the durable key |
| **7** | Both a type 2 and a type 1 view over the same dimension; the fact carries both the version key and the durable key | Same requirement as 6, but you want the two perspectives to be separate objects so a consumer cannot mix them by accident | The snapshot plus a current-only view, and both keys on the fact |

Reading this table as a menu is a mistake. The decision procedure is short:

1. **Does any question need the attribute as it was at event time?** No → type 1, and stop. This is most attributes, and the simplicity is worth defending.
2. **Yes. Does any question *also* need all history grouped by the current value?** No → type 2. Yes → type 6 or 7. The distinction between 6 and 7 is whether you would rather risk a consumer picking the wrong column (6) or the wrong relation (7).
3. **Is the dimension large and does one attribute group change much faster than the rest?** Then type 4 or 5, because type 2 over the whole dimension multiplies rows for changes nobody asked to track.
4. **Type 3 only when the requirement is literally "current and the one before, side by side"**, which is rare and obvious when it is real.

Two properties that are easy to get wrong regardless of type:

- **Correction versus change** decides whether history should be preserved at all. A value that was never true does not deserve a version. Preserving it pollutes history with a version that means nothing, and removing it later requires rewriting validity windows. This is a question for a human: the data cannot tell you whether an old value was wrong or merely old.
- **Type 2 changes the dimension's grain**, so every fact joining to it must join on the validity window too, and a fact joining only on the natural key fans out by the number of versions. On a wide serving table this shows up as a total that grew when a dimension was "just documented".

### Late-arriving dimensions and early-arriving facts

Two named problems, one underlying situation: fact and dimension data do not arrive together.

**A fact arrives before its dimension row exists.** Dropping it understates every total, and the drop is invisible because the row simply is not there. Holding it in a quarantine relation is honest but means the total is short until someone processes the quarantine. The conventional answer is a placeholder dimension row carrying the unresolved natural key and explicit unknown values for everything else, overwritten in place when the real context arrives.

What to write down when you choose the placeholder approach:

- The placeholder is a **type 1 overwrite**, so any report run between arrival and resolution attributed the fact to "unknown" and will restate once resolved. If restatement is unacceptable, the placeholder approach is not acceptable either, and the requirement is quarantine plus a visible backlog count.
- **A count of unresolved placeholders is a monitoring signal**, not a curiosity. If it trends up, an upstream feed has stopped and every affected total is quietly mis-attributed rather than short. Give it a test with a threshold.

**A fact arrives after the period it belongs to.** The current dimension version is not the one that was in effect at event time, so resolving the fact against "current" back-dates today's attributes onto an old event. The correct behaviour is to resolve the version key by the event timestamp against the validity window — which is only possible if the dimension is type 2, and is one of the strongest practical arguments for type 2 on an attribute that is used to slice history.

The related trap on the fact side: if the model is incremental and filtered on the event timestamp, a late-arriving row for an old period falls outside the boundary and is never loaded at all. The boundary must be based on load or modification time while the *grain* stays the event time, or the late row is lost silently. See `dbt-incremental-models`.

**A retroactive change to a versioned attribute** is the expensive case: a new version has to be inserted *in the middle* of an existing history, and every fact row in that window must have its version key restated. Decide whether the project accepts restatement of already-reported history before promising type 2 semantics on an attribute whose source is known to make backdated corrections.

## 4. Conformed dimensions, from the design side

A dimension is conformed when the same attribute name means the same thing, with the same domain of values and the same keys, everywhere it appears. That is what makes it possible to compare two facts by querying each separately at a common attribute and aligning the results — which is the only safe way to combine two facts, since joining them directly is uncontrollable.

The design-time obligation is small and usually skipped: **before creating a dimension, check whether the concept already has one, and if it does, extend it rather than building a parallel one.** A second dimension for the same concept does not error. It drifts, and the first symptom is two reports that disagree about a grouping everyone assumed was shared.

The planning tool is a grid of processes against dimensions, and the multi-source version of this technique — including how to detect that two dimensions are "almost conformed", which is the dangerous state — is in `dbt-unifying-sources`.
