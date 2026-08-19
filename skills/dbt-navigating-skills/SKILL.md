---
name: dbt-navigating-skills
description: Use when starting any dbt task, before reading any other skill, to decide what to read and in what order. Classifies the request into a task archetype, names the minimum read set for each phase, and states the conditions under which to escalate to a further skill. Use when you do not already know exactly which skills a task needs, when a task spans several skills, or when you are unsure whether you have read enough to act.
metadata:
  phase: orient
---

# Navigating the skill set

This library holds 27 skills across 78 files and roughly 19,500 lines. A single ordinary task — "add a column to an incremental mart model" — has plausible description matches totalling over 8,000 lines. **Reading everything that might be relevant is not an option, and reading whatever matched first is how a task gets done wrongly with confidence.**

This skill is the entry point. It is 1.5% of the library and its whole job is to tell you which few hundred lines to read next.

## How to use this file

1. Find your task in the **archetype table**. If two rows fit, read both minimum sets — they are small by design.
2. Read the **minimum read set** for the phase you are in. Not the whole skill unless the row says so.
3. Check the **escalation triggers**. Each names a condition, not a topic: read the extra skill only when its condition is true of the task in front of you.
4. When you move to the next phase, come back here rather than following whatever link you last saw.

**The stopping rule:** you have read enough when you can state the grain, the verification you will run, and the failure mode you are guarding against. If you cannot state all three, you are missing a read. If you can, further reading is procrastination.

**Then write those three down before editing anything.** What you just read is in the transcript, and the transcript is what a long session loses first — so the answers to the stopping rule are exactly what should become tracked state, along with the file list and the verification items from the checklist of whichever skill you read. `AGENTS.md` § *Carrying state across a session* has the rule and the three entry kinds. This is also the moment to note which skills you read: after a compaction, that list is the difference between re-reading two files and starting the orientation over.

---

## The phase spine

Every dbt change moves through the same phases. Skills attach to phases, which is what makes the order predictable.

| Phase | Question it answers | Skills that own it |
|---|---|---|
| **Orient** | What is true of this project, and what am I actually being asked? | `dbt-navigating-skills`, `dbt-gathering-context`, `dbt-project-conventions`, `dbt-onboarding-to-a-project`, `dbt-deriving-project-context` |
| **Decide** | Should this be built at all, and as what? | `dbt-designing-a-model`, `dbt-restructuring-dags` |
| **Build** | Write the thing | `dbt-authoring-sql-models`, `dbt-authoring-schema-yaml`, `dbt-incremental-models`, `dbt-snapshots`, `dbt-sources-and-seeds`, `dbt-macros`, `dbt-python-models`, `dbt-adding-columns`, `dbt-unifying-sources` |
| **Prove** | Did it do what I claim? | `dbt-verification`, `dbt-unit-tests` |
| **Ship** | Get it to production safely | `dbt-shipping-changes`, `dbt-breaking-changes`, `dbt-environments` |
| **Diagnose** | Something is wrong — off-spine, entered directly | `dbt-debugging-failures`, `dbt-data-quality-triage`, `dbt-performance-tuning` |
| **Reference** | Looked up mid-phase, never read start to finish | `dbt-command-reference`, `dbt-handling-sensitive-data`, `dbt-refactoring-safely` |

Two rules about the spine:

- **Never skip Orient.** Reading `conventions.yml` is cheap and every later decision depends on it. Guidance given without the contract is generic guidance and must be labelled as such.
- **Prove is not optional and not last.** Decide the verification *while* building, because "how would I know if this were wrong?" changes what you build.

### Delegating a phase, if your harness supports it

Four jobs read very wide and report narrow, so they are worth handing to a fresh context when one is available. If it is not, do the reading yourself in the main thread — the content lives in the skills, and nothing is lost but headroom.

**These four names are agent definitions in `agents/`, not skills** — there is no `skills/dbt-impact-scout/`. They exist for harnesses that support subagents; each one's job is to read a set of skills and instruments on your behalf and hand back evidence. When your harness has no subagent mechanism, "delegate" simply means "read those same skills inline": for a breaking change, that is `dbt-breaking-changes` and `blast-radius.md`; for onboarding, `dbt-onboarding-to-a-project`; for review, `dbt-shipping-changes` and `dbt-verification`; for a slow model, `dbt-performance-tuning`.

| Delegate (an agent, not a skill) | On archetype | Instead of reading, inline |
|---|---|---|
| `dbt-context-deriver` | H | the whole repository |
| `dbt-impact-scout` | A, C, E, G, L — before a breaking change | manifest, exposures, BI catalog, query log |
| `dbt-change-reviewer` | F | your own diff, which you cannot review independently |
| `dbt-perf-profiler` | D, when slow | query profiles and plans |

**Read what comes back as evidence, not as a verdict.** Each returns a slot for what it could not verify, and that slot is the most important part: a delegate that queried four blind instruments and found nothing has found a gap, not an absence. Do not delegate debugging, authoring, or design — those need the working memory of the main thread, and a cold delegate re-derives from a summary that cannot contain the hypothesis you just formed.

---

## Archetypes: the minimum read set

Classify the request here first. This table is the root of the tree: pick one row, then read only that archetype's section.

| If the request is… | Archetype |
|---|---|
| Add a field/column to something that exists | **A** |
| Create a model that does not exist yet | **B** |
| Change what an existing model computes | **C** |
| An error, wrong numbers, or something slow | **D** |
| Split, combine, relayer, or re-point models | **E** |
| Commit, PR, review, or release finished work | **F** |
| Change materialization — including table → incremental | **G** |
| Get oriented in an unfamiliar project | **H** |
| Add or improve tests | **I** |
| Factor repeated SQL into a macro | **J** |
| Do it in Python / Snowpark | **K** |
| Retire, delete, or replace a model | **L** |
| Reprocess history — no code change | **M** |
| Explain, answer, or review — no edit | **N** |

If two rows fit, read both — they are small by design. If none fits, say so rather than forcing the nearest match; an unclassified task is a signal the request is ambiguous, and that is worth one clarifying question.

`§` means a named section, not the whole file. Read the section; the surrounding file is available if the section tells you to go deeper.

**Two rules apply to every archetype below, so they are stated once rather than repeated in each table.**

1. **Every path that changes a file exits through archetype F (ship).** The tables below stop at Prove because that is where the *thinking* specific to the task ends, not where the work ends. A change that is verified and unshipped is unfinished, and a change shipped without the review and CI questions in F is where correct work still causes an incident.
2. **Every path begins by reading the contract, and reading it is not the same as having the facts.** `conventions.yml` and `.dbt-agent/` tell you what the project decided. When a task depends on a fact neither one states — who consumes this, what an acceptable threshold is, whether a value is a bug — that is `dbt-gathering-context`, and the rule there is the important one: exhaust what a tool can derive before asking a person, and never present an unverified assumption as a finding.

### A. Add a column to an existing model

| Phase | Read |
|---|---|
| Orient | `conventions.yml`; `.dbt-agent/mechanisms.md` if present |
| Decide | `dbt-adding-columns` § the classification table — it routes the rest of that file |
| Build | The one section of `dbt-adding-columns` your row points to |
| Prove | `dbt-verification` § row-count reconciliation, and the three-value invariance check in `dbt-adding-columns` |

Escalate only if: the column needs a **new join** (`dbt-authoring-sql-models/joins.md`) · the model is **incremental** and history must be filled (`dbt-incremental-models` § backfill, plus `dbt-shipping-changes/backfilling.md`) · the column is **personal or regulated** (`dbt-handling-sensitive-data`) · the model has an **enforced contract** (`dbt-adding-columns` § contracts).

### B. Build a new model

| Phase | Read |
|---|---|
| Orient | `conventions.yml`; `dbt-project-conventions` § naming if the name is not obvious |
| Decide | `dbt-designing-a-model` §§ *check whether something already answers it* **and** *reuse is a spectrum* — **before writing any SQL**; then § grain |
| Build | `dbt-authoring-sql-models` § the layer you are writing; `dbt-authoring-schema-yaml` § descriptions and tests |
| Prove | `dbt-verification` § the evidence ladder |
| Ship | `dbt-shipping-changes` § part 1 |

Escalate only if: it is **incremental** (`dbt-incremental-models`, and `strategy-reference.md` before choosing a strategy) · it **combines several source systems** (`dbt-unifying-sources`) · it is a **snapshot** (`dbt-snapshots`) · it needs a **new source or seed** (`dbt-sources-and-seeds`) · the logic is **non-obvious enough to deserve a unit test** (`dbt-unit-tests`).

**The reuse gate is mandatory here.** Most "build a new model" requests are better served by extending or composing on an existing one. If you have not searched for candidates and named the reuse mode you rejected, you have skipped the highest-value step in this archetype.

### C. Change the logic of an existing model

| Phase | Read |
|---|---|
| Orient | `conventions.yml` |
| Decide | `dbt-refactoring-safely` § *is this the right skill?* — routes between refactoring, restructuring, and breaking changes |
| Build | Whichever that row names |
| Prove | `dbt-verification` § equivalence proof; `comparison-techniques.md` if the change should be output-neutral |

Escalate only if: output **should not** change (`dbt-refactoring-safely` in full — the behaviour-preserving table is the point) · a **column or grain changes** (`dbt-breaking-changes` **before editing**, because ordering matters) · the model is **incremental** (`dbt-incremental-models` § refactoring: two bodies of SQL, two proofs).

### D. Something is broken

| Symptom | Enter at |
|---|---|
| A command or build **errored** | `dbt-debugging-failures`; `failure-taxonomy.md` to map the message |
| It **ran fine but the numbers are wrong** | `dbt-data-quality-triage`; `silent-corruption.md` |
| It is **slow or expensive** | `dbt-performance-tuning` § diagnose first — never skip to the fix |

Do not read the other two. These are different problems with different procedures, and the wrong one wastes the whole session. If a build error turns out to be a data problem, come back here and switch.

**Diagnosis is not the end of the task.** Once the cause is known, the fix re-enters the spine at the archetype matching the *edit* — a logic change is C, a materialization change is G — and then leaves through F like anything else. Two additional obligations belong to this path specifically:

| After diagnosing | Do this |
|---|---|
| Prove | `dbt-verification` — reproduce the wrong result, then show the same query returning the right one. A fix asserted without the before-and-after is a guess that happened to stop the symptom |
| Communicate | If wrong numbers were **published**, `dbt-data-quality-triage` § whose problem it is and the incident-communication guidance. Silently correcting figures someone already acted on is the failure people remember |

The cause is frequently upstream and not yours to patch. Deciding that before writing SQL is the point of the triage skill.

### E. Restructure the DAG

| Phase | Read |
|---|---|
| Orient | `conventions.yml` § `layers[].may_reference` and § `layers[].materialization` — these define which edges are legal, so a restructure that ignores them can produce a DAG the contract forbids |
| Decide | `dbt-restructuring-dags` § the anti-pattern diagnosis table — a restructure needs a **named defect**, not a feeling |
| Build | The operation that table points to |
| Prove | `dbt-verification`; `dbt-refactoring-safely` § what a zero-diff does not prove |

Escalate only if: consumers are affected (`dbt-breaking-changes`) · **materialization** changes (`dbt-restructuring-dags` § operation 7).

### F. Ship / open a PR

| Phase | Read |
|---|---|
| Ship | `dbt-shipping-changes` § part 1 (review) and § part 2 (CI) |
| | `dbt-verification` § what "verified" cannot mean — before writing the PR description |

Escalate only if: anything **breaking** is included (`dbt-breaking-changes` § communicating and timeline) · a **backfill** is required (`backfilling.md`) · **production deployment** carries risk (`dbt-shipping-changes` § part 3).

### G. Change a model's materialization (including table → incremental)

The DAG does not change, no SQL needs to change, and the diff is often three lines — which is exactly why this one gets under-reviewed. Do not route it as a restructure just because the operation lives in that skill.

| Phase | Read |
|---|---|
| Orient | `conventions.yml` § `layers[].materialization` — departing from the layer's declared materialization needs a recorded reason |
| Decide | `dbt-restructuring-dags` § operation 7 — the direction table, then the consequences-by-category section |
| Build | For **→ `incremental`**: `dbt-incremental-models` § boundary and § unique key, and `strategy-reference.md` **before** choosing a strategy. Nothing else in this library covers the adapter differences that decide correctness here |
| Build | **Mandatory when the model gains a range or backfill parameter:** `dbt-incremental-models/backfilling.md` § design the range parameter in before you need it, and `dbt-shipping-changes/backfilling.md` § is the range bounded on both sides. This is a gate, not an escalation — see below |
| Prove | `dbt-verification` § row-count reconciliation and § equivalence — output is supposed to be identical, which is precisely when a regression hides |
| Prove | **Reruns do not prove a boundary is safe.** If the model gained `incremental_predicates`, check that the delete window cannot become narrower than the source window when the table is stale — `dbt-incremental-models/boundary-patterns.md` § the two windows must not be able to drift apart. Two clean consecutive runs look identical whether or not this bug is present |

**`table` → `incremental` is a rewrite wearing a config change.** The model acquires a boundary predicate, a `unique_key` decision, and a strategy whose meaning differs by adapter — plus the possibility of a first run that behaves unlike every later run. Treat the config edit as the smallest part.

**`incremental` → `table` destroys accumulated history.** If the source cannot reproduce the past, that is irreversible. Establish reproducibility *before* editing, not after.

**Why the backfill read is a gate rather than a condition.** A model becoming incremental almost always acquires a way to reprocess history, and the two decisions that go wrong are made *at that moment*, not later:

- **Is the reprocessing window bounded on both sides?** A start bound alone reprocesses from that date to now. That is a full-history rebuild wearing the costume of a targeted fix, and it is the same statement whether the intent was one day or two years.
- **Is the default deliberately open-ended?** For the routine incremental run it usually should be — the point is to capture everything available upstream. That makes an *optional* upper bound the right shape: no end for the scheduled run, an end available for a targeted backfill. Do not conclude from a project having no upper bound anywhere that none is wanted. **Absence of an end-date parameter in the existing models is not evidence that the team does not want one** — it is frequently just the thing nobody needed until the first bad day. If the project has no precedent, that is a question to ask, not a convention to copy.

Escalate only if: the model is consumed by BI or has grants applied out of band (`dbt-breaking-changes` § blast radius — a replace can silently drop grants) · the target is `materialized_view` (contract support and refresh semantics differ per adapter).

### H. New to the project

Read `dbt-onboarding-to-a-project` in full — it is the one skill designed to be read start to finish. If `conventions.yml` is missing, `dbt-deriving-project-context` comes first, because everything else depends on the contract existing.

Whichever you run, **start with the instrument sweep** (onboarding §0, or deriving §0 for the full table). What is answerable at all depends on it, and skipping it is how a brief ends up full of open questions that a connected tool would have closed.

Its business-map pass (§6b, `mapping-the-business.md`) is the one section that is conditional rather than routine: run it when `context.domain_notes` is missing or stale, or when the task turns on business meaning — a metric definition, a cross-system join, which of two similar marts is canonical. It is mostly structured asking, so batch the questions. Where the artifact already exists, reading it is the step. **When deriving the contract, that pass comes before writing the artifacts** (deriving §4b), not after — the business sections cannot be written from the repository alone.

**Hand the result back as a first pass.** Say what is covered, what is thin, and what could not be established, as a ranked list. An empty question list on a mature project is evidence something was inferred rather than asked.

### I. Add or improve tests on an existing model

| Phase | Read |
|---|---|
| Orient | `conventions.yml` § `tests` — the project's required tests and its custom generic tests, so you add what is missing rather than a second version of what exists |
| Decide | `dbt-authoring-schema-yaml/data-tests.md` § which tests earn their compute — the goal is coverage of the claims that matter, not a higher test count |
| Build | `dbt-authoring-schema-yaml/data-tests.md` for data tests; `dbt-unit-tests` **only if** the logic has branches or arithmetic worth pinning, plus `fixtures.md` before writing fixture rows |
| Prove | A test that has never failed proves nothing. Break the input deliberately, or reason explicitly about what would have to be true for it to fire |

Escalate only if: adding tests reveals the model's grain is not what was documented (`dbt-designing-a-model` § grain) · a test fails immediately, which is a data-quality finding and not a test bug (`dbt-data-quality-triage`).

### J. Extract or write a macro

| Phase | Read |
|---|---|
| Decide | `dbt-macros` § the readability cost, and the test for whether a macro earns its place. Two occurrences is usually not enough |
| Build | `dbt-macros` § dispatch mechanics if it must work across adapters; § operational-macro safety if it writes anything |
| Prove | `dbt-macros` § testing a macro. Compile at least two call sites and diff the generated SQL against what they produced before |

Escalate only if: the macro is introspective — it queries the warehouse at compile time — which makes every model using it untestable (`dbt-unit-tests` § what cannot be tested) · you are replacing SQL in many models at once, making it a refactor (`dbt-refactoring-safely`).

### K. Write a Python model

| Phase | Read |
|---|---|
| Decide | `dbt-python-models/cost-and-decision.md` **first** — the question is whether this needs to be Python at all, and the honest answer is usually no |
| Orient | `dbt-python-models/platform-reference.md` — several adapters support no Python models whatsoever, so this can be settled before any design work |
| Build | `dbt-python-models` § the model contract and returned dataframe |
| Prove | `dbt-python-models/testing-and-debugging.md` — the tooling is weaker here than for SQL, which is itself an argument against choosing Python |

### L. Deprecate or remove a model

| Phase | Read |
|---|---|
| Decide | `dbt-breaking-changes` § prefer deprecation to deletion, and `blast-radius.md` to establish who reads it |
| Build | `dbt-breaking-changes` § the expand/migrate/contract ordering; `governance-mechanisms.md` for `deprecation_date` mechanics |
| Prove | The evidence table for "unused" — **"no references found" and "unused" are different claims**, and only one of them is provable |

The trap specific to this archetype: **dbt does not drop the relation.** A deleted model leaves a table serving frozen data with no error, which is worse than a dropped one because nothing signals it. Removing the object belongs in the post-merge actions.

### M. Run a backfill, with no code change

| Phase | Read |
|---|---|
| Orient | `.dbt-agent/mechanisms.md` — **if the project has a sanctioned backfill mechanism, use it.** Blue/green swaps, range vars, and handoff labels are project machinery; re-inventing a path around them is how a live table gets overwritten |
| Decide | `dbt-shipping-changes/backfilling.md` § is the range bounded on both sides, and the per-strategy rerun table — whether a rerun is *safe* is decided by the model's strategy, not by the operator |
| Build | `dbt-incremental-models/backfilling.md` if the model has no range parameter, because then one must be added before any backfill is possible |
| Prove | Per-batch verification, not one check at the end. A partially-completed backfill is the state you must be able to reason about |

Escalate only if: the model is `full_refresh=false` (history may be unreconstructable — stop and confirm the source still holds the range) · the strategy is `append` (**no safe backfill exists** until the range is deleted by hand).

### N. Answer a question, explain a model, or review someone's change

No files are edited, so most of the spine does not apply — but two obligations still do, and they are the ones that make an answer trustworthy rather than plausible.

| Phase | Read |
|---|---|
| Orient | `conventions.yml` and `.dbt-agent/domain.md` — an explanation that contradicts the project's own documented meaning is wrong even when the SQL reading is right |
| Derive | `dbt-gathering-context` § the derivability matrix — answer from the warehouse, the manifest, or the graph rather than from inference. **Say which of the three you used** |

Read-only does not mean unverified. If the question is "does anything use this model," the answer requires the evidence table in `dbt-breaking-changes`, not a `grep`. If it is "why is this number wrong," that is archetype D. State plainly what you could not check.

---

## Escalation triggers, as conditions

Read the skill when the condition is true. Do not read it because the topic came up.

| Read this | Only when |
|---|---|
| `dbt-incremental-models` | The model is or will be incremental. Not "might be large one day" |
| `dbt-performance-tuning` | Something is measurably slow or expensive, or you are about to write a join against a table you know is large. Not as a general polish pass |
| `dbt-handling-sensitive-data` | A column is personal, regulated, or already classified. Absent classification means *unclassified*, never *safe* |
| `dbt-breaking-changes` | An existing name, type, or grain changes, or something is being removed |
| `dbt-unit-tests` | The logic has branches, edge cases, or arithmetic worth pinning. Not for a passthrough column |
| `dbt-macros` | You are about to write the same SQL a third time, or editing an existing macro |
| `dbt-environments` | `ref()` resolution, dev-vs-prod behaviour, or deferral is in question |
| `dbt-command-reference` | You need exact selector, state, or defer syntax. Look up the section; do not read the file |
| `dbt-gathering-context` | You are about to ask the user something, or about to assume something you could measure |

**The last row is the one that most changes behaviour.** Before asking a question, check whether a tool can answer it. Before assuming, check whether a query can settle it.

---

## Anti-patterns in using this library

| Anti-pattern | Why it fails |
|---|---|
| Loading every skill whose description matched | 8,000+ lines for one task. Everything is diluted and the specific guidance is lost in the general |
| Following cross-links depth-first | The skills reference each other densely on purpose. Depth-first traversal never terminates. Return here between phases |
| Reading Build before Decide | The most expensive errors are design errors. Writing SQL first makes them expensive to undo |
| Skipping Orient because the task is small | The contract is what makes advice specific to this project. Without it you are guessing at naming, layers, and environments |
| Treating Prove as a final step | Verification chosen after the fact tends to be verification the change happens to pass |
| Reading a whole file when a section was named | The sections were chosen because the rest is not relevant to that archetype |

## Completion checklist

- [ ] Task classified into an archetype; both rows read if two applied
- [ ] Contract read, or its absence stated
- [ ] For a new model: reuse modes considered, and the rejected mode named
- [ ] Every true escalation condition followed; conditions that were false, not read
- [ ] Can state the grain, the verification, and the failure mode being guarded against
- [ ] Verification decided during Build, not after
