# Domain notes

Copy to the path you set in `context.domain_notes` and replace every section.

**The test for whether something belongs here: could a connected tool compute it?** If a tool can, delete it — a copy of a derivable fact rots while the real answer moves on. Column types belong in the warehouse, dependencies in the DAG, run times in the orchestrator, schedules in the orchestrator's config. What belongs here is what no query returns: decisions, meanings, and the reasons behind them.

Keep this short. A file nobody maintains is worse than no file, because it is believed. If a section goes stale, delete it rather than leaving it to mislead.

---

## What this company does

Two or three sentences. What the business sells, to whom, and how it makes money. This is the context that makes every column name interpretable — an agent that knows whether you bill per seat or per transaction reads `quantity` correctly without asking.

## Source systems

The operational systems that feed this warehouse, and what each one is the **system of record** for. Overlapping data between systems is normal; which system wins is a policy decision that only lives here.

| System | System of record for | Arrives by | Note |
|---|---|---|---|
| `<system>` | `<the questions it is authoritative for>` | `<batch / stream / CDC / manual>` | `<migrating, deprecated, or partially replaced>` |

The "note" column earns its place on the migration case. A project often carries two generations of the same source — the old one still landing data, the new one partially built — and both look live in the DAG. Record which is which, because building on the retiring one is wasted work.

## Core entities

The nouns the business runs on and what each actually means. Include the distinctions outsiders get wrong.

| Entity | What it means here | Authoritative dataset | The trap |
|---|---|---|---|
| `<entity>` | `<the business definition, not the table shape>` | `<which model wins when two disagree>` | `<the thing people assume that is wrong>` |

Example of the kind of trap worth recording: *a "customer" in the CRM is a signed contract, while a "customer" in the product database is a login. The two counts have never matched and are not supposed to.*

## How entities link across systems

The cross-system joins, and the business facts they must respect. This is where a technically valid join produces a wrong answer.

| From → to | Key | Cardinality | Unmatched rows mean |
|---|---|---|---|
| `<system.entity>` → `<system.entity>` | `<natural key, or the crosswalk model>` | `<1:1, 1:many, many:many>` | `<trials, self-serve signups, test accounts — and whether to filter them>` |

Two things to be explicit about, because both are invisible in the schema and both silently corrupt results: whether one population is a **subset** of the other or the two **overlap partially** (a subset makes a left join safe; partial overlap loses rows on both sides), and whether a match rate well below 100% is **expected**. A rate only a person can tell you is normal is a rate an agent will otherwise treat as a defect and "fix."

## Canonical metric definitions

Only metrics where **two plausible definitions exist and one is correct**. This is the highest-value section in the file, because SQL can show what is computed but never which rival definition is canonical, and getting it wrong produces a number that looks right and is not.

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
