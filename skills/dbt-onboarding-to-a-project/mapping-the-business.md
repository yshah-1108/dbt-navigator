# Mapping the business behind the models

A DAG tells you which model feeds which. It does not tell you that `stg_salesforce__accounts` and `stg_stripe__customers` describe the *same companies* under different identifiers, that the CRM's "customer" is a signed contract while the product database's is a login, or that `fct_revenue` is what Finance closes the books on while `fct_bookings` is what Sales forecasts from. That is the layer where an agent's mistakes become expensive, because the SQL compiles, the tests pass, and the number is wrong in a way only a person who knows the business can see.

This pass builds that layer. It is the difference between an agent that can edit a model and one that understands what the model is *about*.

## The organizing idea: events flowing through systems

A business is a **sequence of events** — things that happen, in order — and a set of **objects** those events reference. That is the frame to build, because it is the one the warehouse is a recording of.

The events come in a chain, and the chain is the business:

| Business | The event chain |
|---|---|
| Ride-hailing | request → match → pickup → trip → payment → rating |
| Subscription software | signup → trial → subscription → invoice → payment → renewal or churn |
| Commerce | cart → order → payment → fulfilment → delivery → return |
| Lending | application → decision → origination → payment → delinquency → payoff |

Once you have the chain and the objects, the warehouse becomes readable, because the structure maps onto it directly:

- **Fact tables are events**, at a grain. `fct_orders` is the order event, one row per order or per order-line.
- **Dimensions are the objects** the events reference — customer, merchant, product, campaign, account.
- **Marts are questions asked of the events**, usually joined to the objects and aggregated to a period.

Miss the chain and you are pattern-matching on table names, which is how an agent concludes two tables are redundant when one records every request and the other only the matched ones. **The most important thing to learn is where one event becomes the next**, because that is where the volume drops by orders of magnitude, where money enters, and where the joins get hard.

## Read this as a first pass, and say so in the output

**This procedure produces a first draft, not a finished understanding.** It will miss things, and some of what it records will be an assumption that looked like a fact. That is the expected outcome, not a failure of the pass — a business's data model is the accumulated result of years of decisions, and no single session derives all of it from the outside.

So two obligations. **Mark every inference as one**, so the reader can tell measurement from guess at a glance. And **say plainly, in the summary and in the artifact, that this is a first pass to be corrected and extended** — invite the specific corrections rather than presenting a complete-looking document. A file that reads as authoritative gets quoted back; a file that reads as a draft gets fixed. Expect to run this again as the project teaches you more, and treat the second pass as normal rather than as a sign the first was wrong.

## Plan it before starting

This is the largest single context-gathering job in the library and it goes wrong by wandering — sampling an interesting table for twenty minutes while the event chain stays unmapped. Work in the order below, because each step makes the next cheaper, and track it as state per `AGENTS.md` § *Carrying state across a session*:

1. **The event spine** — what happens, in what order.
2. **Which system records which event**, and what each source powers.
3. **What sits upstream of dbt**, outside its horizon.
4. **The important fact tables**, read as events.
5. **The objects**, and which dataset is authoritative for each.
6. **How objects link across systems.**
7. **What the central marts are for.**

## 1. Establish the event spine

Ask for the chain in business words first — it takes one question and it makes every table name interpretable:

> "Walk me through what happens from the start of a customer interaction to the point we recognize revenue. What are the steps, in order, and which ones do we record?"

Then derive candidates and check them against the answer, because fact and source table names are usually named after the events:

```bash
# fact tables name the events; source tables name the raw ones
ls models/marts/**/fct_*.sql models/marts/**/*fact*.sql 2>/dev/null
grep -rh '^\s*- name:' models --include='*.yml' | sort -u | head -40
```

Per event, four things worth establishing, and only the first is derivable:

| What | Why it matters |
|---|---|
| Its **grain** — what one row is | Everything downstream depends on it, and it is the single most common source of double-counting |
| Whether it is **immutable or revised** | A revised event (a trip later disputed, an order later refunded) means the source reprocesses, which decides `merge` versus `delete+insert` |
| Which events are **money events** | These get the reconciliation requirements and the strictest tests |
| The **drop-off to the next event** | Orders of magnitude between request and conversion. This tells you which tables are large, and a drop-off that changes is a real business signal or a real bug |

## 2. Which system records which event

Now map events to systems, because the answer to "where does this event live" is what makes the source list meaningful.

Start from the declared sources, then trace what each powers and whether it is still alive — both derivable, and both come before asking anybody anything:

```bash
grep -rh -A2 '^\s*- name:' models --include='*.yml' | head -60

# what depends on this source: importance is dependents, not table count
dbt ls --select "source:<source_name>+" --resource-type model | wc -l
dbt ls --select "source:<source_name>+,resource_type:exposure"
```

**A source inventory that stops at "apparent subject, inferred from the name" is not a business map** — it is the file listing with a guess attached, and it reads as knowledge. Rank sources by what depends on them, so a person's attention goes to the ones that matter.

Then sample recent rows, always under a bounded date predicate:

```sql
select * from <database>.<schema>.<table>
where <timestamp_column> >= dateadd(day, -7, current_date)
limit 20;

select max(<timestamp_column>) as latest, count(*) as rows_last_7d
from <database>.<schema>.<table>
where <timestamp_column> >= dateadd(day, -7, current_date);
```

The date predicate is not politeness — on a large event table an unbounded scan is expensive and may time out, and **a `limit` alone does not bound the scan**. The timeout then gets reported as "could not establish," turning a derivable fact into a fake open question. **A source with no rows in the last week is a finding**: a broken pipeline nobody noticed, or a system already retired, and both change what you should build on.

Then ask what no query answers:

| Question | Why it cannot be derived |
|---|---|
| Which event(s) does this system record, and what is it *for* in the business? | A schema shows tables, never the business process that produces them. |
| What is it the **system of record** for? | Authority is an organizational decision. Two systems holding overlapping data is normal; which one wins is a policy. |
| Who owns it, and who changes its schema? | Upstream ownership decides whether a breaking change is negotiable or an event you absorb. |
| How does data arrive — batch, stream, CDC, manual upload? | Decides whether late-arriving rows and mutable history are expected. Changes incremental strategy. |
| Is it being migrated, deprecated, or replaced? | The highest-value fact here, and it exists only in someone's head. Building on a system being retired next quarter is wasted work. |

A project routinely contains two generations of the same source — the old one still landing data, the new one partially built — and nothing marks which is which. If your freshness check found one stale, lead with it: "this source last landed data in March — is it retired?" answers itself in one reply.

## 3. Look upstream of dbt's horizon

**A dbt source is the entry point to dbt, not the entry point to the data.** This is the step most often skipped, and the one that most often explains behavior that otherwise looks inexplicable.

Before a dbt source there is usually a chain nobody in the dbt project can see: raw event logs, an ingestion or CDC pipeline, and — frequently — **a transformation another team already performed in another repository**. A "source" that is actually a pre-aggregate or a rebuilt table has semantics you must know, and none of them are visible from `sources.yml`:

| What the upstream is | What it changes for you |
|---|---|
| **Raw log or event stream** | Detail is available if you need it; the dbt source may be a lossy subset |
| **A pre-aggregate built elsewhere** | You *cannot* recover finer grain from it, and any request needing detail has to go upstream |
| **A table rebuilt on a schedule, with lag** | Freshness is bounded by that rebuild, not by your run. Reading it early gets partial data with no error |
| **A source that reprocesses a window** | `merge` silently leaves stale rows. This is the `delete+insert` case, and the upstream is the only place the fact lives |
| **A reverse-ETL loop back into the warehouse** | Circularity: a table you build may feed a system that feeds a source you read |

How to find it, in increasing order of effort:

```bash
# the source yml is the first place someone would have written it down
grep -rn -A8 'name: <source_name>' models --include='*.yml'

# the project's own tests and comments often name the upstream system
grep -rn '<source_name>' tests/ macros/ --include='*.sql' | head
```

Then look outside this repository: the organization's other repos (an ingestion service, a Spark or Flink job, another dbt project), and the ingestion tool's own configuration. If a git host API is connected, search the org for the source or table name — the pipeline that produces it is frequently a named repository.

**Where it cannot be found, ask specifically rather than generally.** "What builds `<table>`, and on what schedule?" is answerable in one line. "Tell me about our data pipeline" is not. The facts worth having: what produces it, whether it is raw or aggregated, whether it reprocesses history, and what its own lag is. Record these in `context.mechanisms` — a source that rebuilds with lag is bespoke machinery, and a skill's generic freshness advice is wrong without it.

## 4. Read the important fact tables as events

Fact tables are where the event model and the warehouse meet, so this is where understanding converts into correct SQL. Take the highest-fan-out ones from the DAG survey — not all of them.

Per fact table:

- **Which event does it record**, and at what grain. State it in one sentence: "one row per order line per day." If you cannot, you do not yet understand the table.
- **Is it the raw event, or already aggregated?** An hourly rollup cannot answer a per-event question, and the difference is rarely in the name.
- **Which objects does it reference**, and via which keys. These are its dimensions and they are the join surface.
- **Which measures are additive**, and which are not. Rates, ratios and distinct counts do not sum, and summing them is a silent error. `dbt-designing-a-model/additivity.md` has the reasoning.
- **Where does it sit in the chain**, and what is its relationship to the fact table before and after it. Two fact tables recording adjacent events are not redundant even when their columns look similar.

Sample it under a date filter, and read whole rows rather than per-column statistics — what columns mean *together* is the thing a schema cannot show.

## 5. Objects: the business nouns and which dataset is authoritative

This is the section that prevents the most expensive class of error, because the same word means different things in different systems and the difference is never in a column name.

The objects are what the events reference — the dimensions the fact tables join to. For each core noun the business runs on (customer, account, order, subscription, product, campaign, merchant, user), establish:

- **What it means here**, in business terms rather than table shape. Not "a row in `dim_customer`" but "an organization with at least one paid contract."
- **Which datasets represent it**, across systems. Usually several.
- **Which one is authoritative**, and for which questions. A CRM may be authoritative for the commercial relationship while the product database is authoritative for usage.
- **The trap** — the thing a newcomer assumes that is wrong.

The traps are the payload. The canonical example, and a version of it exists in almost every company: *a "customer" in the CRM is a signed contract, while a "customer" in the product database is a login. The two counts have never matched and are not supposed to.* An agent that does not know this will "fix" the discrepancy, and the fix is a bug.

Where the same object appears in two systems, ask the question that decides every future join: **is one a subset of the other, or do they overlap partially?** A subset means a left join is safe. Partial overlap means every join loses rows on both sides, and whether that loss is acceptable is a business call.

## 6. How objects link across systems

Cross-system joins are where business meaning and technical mechanics meet, and where the failures are quiet.

For each link between systems, establish and record:

| Fact | Why it matters |
|---|---|
| The join key, and whether it is a natural or a mapped key | A mapped key means a crosswalk table exists — find it, because hand-rolling a second one is a classic duplication |
| Whether the link is 1:1, 1:many, or many:many | Determines fan-out. A join assumed 1:1 that is 1:many silently multiplies every measure downstream |
| The match rate, and whether unmatched rows are expected | 100% is suspicious, 60% may be entirely normal — but only a person knows which |
| What unmatched rows *mean* | Trials with no CRM record, self-serve signups outside the sales process, test accounts. Each implies a different filter |
| Whether identity resolution happens, and where | If a model reconciles identities across systems, that model is load-bearing and its logic is business policy, not code |

`dbt-unifying-sources` covers the mechanics of building these joins. This pass establishes the *facts* they must respect. Do the map first: a union written without knowing that two sources overlap partially is a union that either drops or duplicates real business events.

## 7. Per mart: what it is for

For the top 10–20 models by fan-out — the ranking the DAG survey produced — establish the three facts that no tool holds:

1. **What decision it drives.** "Finance closes the month on this" and "an analyst browses it occasionally" call for very different care with the same one-line change.
2. **Who reads it, and how.** A dashboard, a scheduled export, a reverse-ETL sync into an operational tool, a notebook. The DAG and query log tell you *that* something reads it; only a person tells you what breaks in their week when it is wrong.
3. **Which of two similar models is canonical.** Nearly every mature project has `fct_revenue` and `fct_revenue_v2`, or a mart and its "daily" sibling. The names never settle it and the wrong choice is invisible.

An exposure with an empty description gives you the link and not the criticality — the link is derivable, the importance is a question. That gap is exactly what this step closes.

## Sampling: the instrument, used throughout

Reading actual values is how you *check* the map at every step above — it is not a step of its own, and it is not the goal. A thousand row counts will not tell you which system is the system of record.

Preconditions, because a profile of the wrong relation is worse than none — it reads as fact. Read **production** with explicit database and schema (`ref()` in a dev target may resolve to a partial build or fall back silently — see `dbt-environments`), and take the relation name from **compiled SQL, not the filename**, since an `alias` or a custom schema macro breaks that assumption (`dbt-gathering-context` §7). Bound every query on an event table with a date predicate.

```sql
-- Categorical domains: the live vocabulary, which the docs rarely match
select <column>, count(*) as n
from <database>.<schema>.<relation>
where <timestamp_column> >= dateadd(day, -7, current_date)
group by 1 order by n desc limit 30;

-- Cross-system match rate: whether a business link actually holds
select count(*) as total,
       count(<foreign_key>) as has_key,
       count(distinct <foreign_key>) as distinct_keys
from <database>.<schema>.<relation>
where <timestamp_column> >= dateadd(day, -7, current_date);
```

What to look for, and what each finding means in business terms:

- **The enum has four live values, not the eleven in the vendor docs.** The other seven are either historical or belong to a product line this company does not use. Which is a question, and the answer is business knowledge.
- **A sentinel appears** — `'UNKNOWN'`, `-1`, `'9999-12-31'`. These are nulls in disguise: they pass every not-null test and break every aggregate. Record the *convention*, since it is usually project-wide.
- **One value is 94% of rows.** That is the default path; the rest are edge cases someone will forget. Worth knowing which are real business cases and which are legacy.
- **Units are wrong from the range.** Amounts averaging 4,000 on a consumer product are cents. A "rate" above 1.0 is not a rate. No documentation states this and every downstream calculation depends on it.
- **History starts later than expected.** A migration discarded what came before. Any year-over-year calculation is wrong before it is written, and *why* the boundary exists is knowledge only a person has.
- **A match rate that surprises you.** The fastest way to discover that two systems do not describe the same population.
- **A drop-off between adjacent events that does not match the business's expectation.** Either your understanding of the chain is wrong or something is broken, and both are worth knowing.

**Measured distributions are not thresholds.** Two years at 3% null tells you 3% is *normal*, never that it is *acceptable*. That line is a business tolerance: bring the number and ask.

## Recording it

This goes in `context.domain_notes`, whose template (`examples/domain.example.md`) has the sections — the event chain, source systems, core objects with a trap column, how they link, canonical metric definitions, timezone rules, known traps, closed decisions. `dbt-deriving-project-context` owns the artifact and its rules.

Three constraints, each preventing a specific failure:

- **Prefer the most specific home.** A fact about one column belongs in that column's dbt `description`, where it versions next to the model and reaches the catalog and the docs site. A reader of the YAML will never think to look in a prose file. `domain.md` is for what spans models or has no node to attach to.
- **Record interpretation, never measurement.** That `amount` is in cents, not its current average. That history starts in 2023 *because of the billing migration*, not `min(date)`. A recorded measurement is a second source of truth that starts disagreeing with the warehouse immediately, and it is the copy that gets believed.
- **Never write a real data value.** A sentinel convention is a fact about the schema; a customer name or an actual revenue figure is data, and copying it into a repository file exports it past whatever grants and masking protected it. See `dbt-handling-sensitive-data`.

**Leave what you could not confirm visibly empty, marked as a question.** An empty "canonical definitions" section is a visible gap someone will fill. A plausible guess is an invisible error that will be cited as fact — and unlike a stale tool reading, a wrong note gives no sign that it is wrong.

**Say in the file that it is a first pass, and date it.** The next reader needs to know whether they are looking at a draft to extend or a document to trust, and only the file can tell them. Name what was not covered — the sources you did not trace, the events you could not confirm — so the gaps are visible work items rather than silent absences.

## Failure modes

1. **Mapping tables instead of the business.** A list of sources and marts with no event chain is an inventory, not an understanding. If you cannot say what happens first, what happens next, and where money enters, you have not done this pass.
2. **A source inventory that is the file listing with a guess attached.** Twenty rows of "appears to be, inferred from the name" is not a business map. Every one of those rows had a derivable downstream blast radius, a measurable freshness, and twenty sample rows available, and none of that was gathered. This is the most likely way this pass produces something that looks thorough and teaches nothing.
3. **Stopping at the dbt source.** A source is dbt's entry point, not the data's. A pre-aggregate, a lagged rebuild, or a reprocessing window upstream explains behavior that is otherwise inexplicable, and none of it is visible in `sources.yml`.
4. **Declaring something a question for the human that a connected tool answers.** See §0 of `dbt-deriving-project-context`. Asking someone to tell you their warehouse is reachable is the inversion of deriving.
5. **Presenting a first pass as complete.** The output will contain assumptions. Unmarked, they get quoted back as facts, and the person who could have corrected them in ten seconds never sees that there was anything to correct.
6. **Profiling instead of understanding.** Row counts, max dates and null rates feel like progress and answer none of the questions above. Metadata is the instrument; the business map is the deliverable.
7. **Inventing business meaning from names.** `dim_customer` does not tell you what a customer is here. A definition assembled from naming conventions reads authoritative and is a guess.
8. **Assuming one system is authoritative because it has more rows.** Authority is a policy, not a row count.
9. **Treating two systems' objects as the same population.** The most expensive error available here. Ask whether one is a subset or the overlap is partial, before writing the join.
10. **Treating two adjacent event tables as redundant.** All requests and matched requests look similar and are not the same event. The chain is what distinguishes them.
11. **Reading a discrepancy as a bug.** Two customer counts that never matched may be correct by definition. Classify with whoever owns the definition before "fixing" it.
12. **Profiling dev and believing it.** A partial or 100-row dev copy makes every conclusion worthless, and the numbers look just as real.
13. **Querying by filename on an aliased model.** You read a stale relation or nothing, and "no rows" reads as a finding.
14. **Scanning an event table without a date predicate.** A `limit` does not bound the scan. The query is expensive, may time out, and the timeout then gets reported as "could not establish."
15. **Copying real data into a repository file.** The one irreversible mistake available in this pass.
16. **Doing this for every model and every source equally.** Rank by what depends on them. Twenty central models is a day well spent; three hundred is a week that teaches less, because nobody reads the result.
