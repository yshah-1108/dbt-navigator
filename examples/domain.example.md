# Domain notes

Copy to the path you set in `context.domain_notes` and replace every section.

**The test for whether something belongs here: could a connected tool compute it?** If a tool can, delete it — a copy of a derivable fact rots while the real answer moves on. Column types belong in the warehouse, dependencies in the DAG, run times in the orchestrator, schedules in the orchestrator's config. What belongs here is what no query returns: decisions, meanings, and the reasons behind them.

Keep this short. A file nobody maintains is worse than no file, because it is believed. If a section goes stale, delete it rather than leaving it to mislead.

---

## What this company does

Two or three sentences. What the business sells, to whom, and how it makes money. This is the context that makes every column name interpretable — an agent that knows whether you bill per seat or per transaction reads `quantity` correctly without asking.

## Core entities

The nouns the business runs on and what each actually means. Include the distinctions outsiders get wrong.

| Entity | What it means here | The trap |
|---|---|---|
| `<entity>` | `<the business definition, not the table shape>` | `<the thing people assume that is wrong>` |

Example of the kind of trap worth recording: *a "customer" in the CRM is a signed contract, while a "customer" in the product database is a login. The two counts have never matched and are not supposed to.*

## Canonical metric definitions

Only metrics where **two plausible definitions exist and one is correct**. This is the highest-value section in the file, because SQL can show what is computed but never which rival definition is canonical, and getting it wrong produces a number that looks right and is not.

### `<metric name>`

- **Definition:** `<the arithmetic, in words>`
- **Canonical source:** `<the model that is the agreed answer>`
- **Commonly confused with:** `<the near-miss definition and why it differs>`
- **Do not:** `<the specific mistake made before>`

## Timezone and calendar rules

State the business day boundary, since a "daily" grain is meaningless without one. Note any fiscal calendar that disagrees with the Gregorian one, and any reporting that must stay in a specific zone regardless of the warehouse default.

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
