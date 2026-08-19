# Domain notes

Copy to the path you set in `context.domain_notes` and replace every section.

**The test for whether something belongs here: could a connected tool compute it?** If a tool can, delete it — a copy of a derivable fact rots while the real answer moves on. Column types belong in the warehouse, dependencies in the DAG, run times in the orchestrator, schedules in the orchestrator's config. What belongs here is what no query returns: decisions, meanings, and the reasons behind them.

Keep this short. A file nobody maintains is worse than no file, because it is believed. If a section goes stale, delete it rather than leaving it to mislead.

**Mark the first version as a first pass, and date it.** Whoever derived this got a useful fraction of the picture, not all of it, and some entries will be assumptions that read like facts. Mark every unconfirmed claim (`NEEDS CONFIRMATION`), name what was not covered, and leave sections empty rather than plausibly filled. Extend it as the project teaches more — the second pass is normal.

---

## What this company does

Two or three sentences. What the business sells, to whom, and how it makes money. This is the context that makes every column name interpretable — an agent that knows whether you bill per seat or per transaction reads `quantity` correctly without asking.

## The event chain

What happens, in order, and where each step is recorded. This is the spine: fact tables are events, dimensions are the objects those events reference, and marts are questions asked of them. An agent that has this reads the warehouse; one that does not is pattern-matching on table names.

| # | Event | What one row means | Recorded in | Fact table |
|---|---|---|---|---|
| 1 | `<event>` | `<the grain, in business terms>` | `<source system>` | `<model>` |

Then the three things about the chain that are worth more than the chain itself:

- **Where money enters.** Which event is the first one with revenue attached, and which is the one Finance treats as final.
- **Where the volume drops.** Orders of magnitude between steps is normal; knowing where tells you which tables are large and which joins are expensive. A drop-off that *changes* is either a real business shift or a real bug.
- **Which events get revised after the fact.** A trip later disputed, an order later refunded, a session later stitched. A revised event means the source reprocesses, which decides `merge` versus `delete+insert` downstream.

## Source systems

The operational systems that feed this warehouse, and what each one is the **system of record** for. Overlapping data between systems is normal; which system wins is a policy decision that only lives here.

| System | Records which event(s) | System of record for | Arrives by | Note |
|---|---|---|---|---|
| `<system>` | `<step numbers above>` | `<the questions it is authoritative for>` | `<batch / stream / CDC / manual>` | `<migrating, deprecated, or partially replaced>` |

The "note" column earns its place on the migration case. A project often carries two generations of the same source — the old one still landing data, the new one partially built — and both look live in the DAG. Record which is which, because building on the retiring one is wasted work.

## What sits upstream of dbt

A dbt source is dbt's entry point, not the data's. Record what produces each important source, because none of this is visible in `sources.yml` and all of it changes what correct code looks like.

| Source | Produced by | Raw or pre-aggregated? | Reprocesses? | Own lag |
|---|---|---|---|---|
| `<source>` | `<pipeline, repo, or team>` | `<raw events / rollup — a rollup cannot be un-aggregated>` | `<does it rebuild history?>` | `<how stale can it be when you read it>` |

Two of these columns decide correctness rather than convenience: a source that **reprocesses a window** makes `merge` silently leave stale rows behind, and a source with its **own rebuild lag** bounds your freshness no matter when your job runs.

## Core objects

The nouns the business runs on and what each actually means. Include the distinctions outsiders get wrong.

| Object | What it means here | Authoritative dataset | The trap |
|---|---|---|---|
| `<object>` | `<the business definition, not the table shape>` | `<which model wins when two disagree>` | `<the thing people assume that is wrong>` |

Example of the kind of trap worth recording: *a "customer" in the CRM is a signed contract, while a "customer" in the product database is a login. The two counts have never matched and are not supposed to.*

## How objects link across systems

The cross-system joins, and the business facts they must respect. This is where a technically valid join produces a wrong answer.

| From → to | Key | Cardinality | Unmatched rows mean |
|---|---|---|---|
| `<system.object>` → `<system.object>` | `<natural key, or the crosswalk model>` | `<1:1, 1:many, many:many>` | `<trials, self-serve signups, test accounts — and whether to filter them>` |

Two things to be explicit about, because both are invisible in the schema and both silently corrupt results: whether one population is a **subset** of the other or the two **overlap partially** (a subset makes a left join safe; partial overlap loses rows on both sides), and whether a match rate well below 100% is **expected**. A rate only a person can tell you is normal is a rate an agent will otherwise treat as a defect and "fix."

## Canonical metric definitions

Only metrics where **two plausible definitions exist and one is correct**. This is the highest-value section in the file, because SQL can show what is computed but never which rival definition is canonical, and getting it wrong produces a number that looks right and is not.

**Search the wiki and the ticket tracker for the metric name before leaving this empty.** A data dictionary page, a finance definition doc, or the ticket behind the model often has the answer already written down — in which case the job is confirming it, not sourcing it. Mark anything found that way as `documented, unconfirmed` with a link, because a wiki page can be years stale.

### `<metric name>`

- **Definition:** `<the arithmetic, in words>`
- **Canonical source:** `<the model that is the agreed answer>`
- **Commonly confused with:** `<the near-miss definition and why it differs>`
- **Do not:** `<the specific mistake made before>`

## Timezone and calendar rules

State the business day boundary, since a "daily" grain is meaningless without one. Note any fiscal calendar that disagrees with the Gregorian one, and any reporting that must stay in a specific zone regardless of the warehouse default.

## What the main marts are for

Only the central ones — the handful with the most downstream dependents. The DAG shows *that* something reads a model; this records what breaks in someone's week when it is wrong, which is what tells an agent how much care a one-line change deserves.

| Mart | Decision it drives | Read by | Canonical vs. |
|---|---|---|---|
| `<model>` | `<what someone decides using it>` | `<dashboard, export, reverse-ETL, notebook>` | `<the similar model it should not be confused with>` |

The last column is the one that prevents silent errors. Nearly every mature project has a `fct_revenue` and a `fct_revenue_v2`, or a mart and its "daily" sibling; the names never settle which is authoritative and the wrong choice looks identical to the right one.

## Known traps

The mistakes that have actually happened. One line each, phrased so an agent can act on it. This section earns its keep after the first incident it prevents.

- `<the trap>` — `<what to do instead>`

## Decisions we have already made

Closed questions, with reasons. Without this, every agent re-opens the same debate and some of them reach a different answer.

| Decision | Reason | Date |
|---|---|---|
| `<what was decided>` | `<why>` | `<YYYY-MM-DD>` |

## Who to ask

Only where it is not already in `CODEOWNERS` or model `meta`. Areas, not a full roster — a stale roster is worse than none.

| Area | Ask |
|---|---|
| `<domain>` | `<team or channel>` |
