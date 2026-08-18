# Skill architecture

How the library is organized, and why it is organized that way. Read this if you want to understand the shape before adopting it, or if you are contributing a skill and need to know where it belongs.

## Organized by task, not by topic

Most skill libraries are organized by **topic** — a skill per dbt feature. That shape has two failure modes, and both are structural rather than a matter of writing quality.

**It produces bloat and gaps at the same time.** A topic like "linting" yields a 60-line note that is really a reference card, while a topic like "building models" yields a 1,300-line file covering four different layers — four skills wearing one name. Meanwhile the tasks an engineer actually performs most often fall between topics and get no coverage at all: adding a column and propagating it downstream, decomposing a model, proving a refactor changed nothing, backfilling.

**It makes the agent guess.** Topic organization requires classifying a request into a topic before finding guidance. Task organization removes that step, because the trigger *is* the task. "I need to rename a column" maps to a skill about renaming columns, not to a judgement call about whether that is a "modeling" or "governance" or "testing" concern.

So skills are named after **what the engineer is doing**, in five tiers:

| Tier | Answers | Loaded |
|---|---|---|
| **A — Foundations** | "Where am I, and what are this project's rules?" | Always, cheaply |
| **B — Artifacts** | "How do I author a *thing* correctly?" | When creating or editing that artifact |
| **C — Operations** | "How do I make this *change* safely?" | When performing that change |
| **D — Diagnostics** | "Why is this wrong?" | When something failed |
| **E — Reference** | "What is the exact syntax?" | On lookup |

**Tier C is the one that justifies the library.** Authoring an artifact from scratch is well covered by dbt's own documentation. Safely reshaping a DAG that already has consumers is not — and that is where data gets silently lost or duplicated.

## The lifecycle spine

The tiers say what a skill *is*. The phases say **when it is read**, and that ordering is what the router enforces:

| Phase | The question it answers |
|---|---|
| **Orient** | What is true of this project, and what am I actually being asked? |
| **Decide** | Should this be built at all, and as what? |
| **Build** | Write the thing |
| **Prove** | Did it do what I claim? |
| **Ship** | Get it to production safely — and what happens after the merge |
| **Diagnose** | Something is wrong. Entered directly, off the spine |

Each `SKILL.md` declares its phase in frontmatter. Two properties of the spine do most of the work:

- **Orient is never skipped.** Every later decision depends on the project's contract. Guidance given without it is generic guidance, and should say so rather than inventing a convention.
- **Prove is not last.** The verification gets chosen while building, because "how would I know if this were wrong?" changes what you build. Deferring it to the end produces work that compiles and cannot be defended.

## The task surface

The enumeration below is the coverage target — the actual work an analytics engineer does, listed independently of what happens to be written. It is the reference for deciding whether a proposed skill fills a real gap, and for noticing when something common has no home.

### Artifacts an engineer authors

SQL model (staging / intermediate / mart) · Python model · source definition · seed · snapshot · macro · generic test · singular test · unit test · schema YAML (descriptions, contracts, constraints) · exposure · semantic model and metric · analysis · materialization and incremental config

### Operations an engineer performs

**Additive**
- Add a column to an existing model and propagate it downstream
- Add a new model to an existing DAG
- Add a test to an untested model
- Add a source

**Reshaping**
- Decompose one model into several
- Combine several models into one
- Insert a layer between two existing models
- Reroute a `ref()` — change what feeds what
- Extract repeated SQL into a macro
- Flatten a deep or linear DAG

**Destructive / breaking**
- Rename a model or a column
- Remove a column
- Deprecate and delete a model
- Change a column's type or grain

**Neutral by intent**
- Refactor SQL with no output change (must be *proven*, not asserted)
- Reformat and lint

**Performance**
- Reduce runtime or cost
- Convert a table to incremental
- Change incremental strategy
- Add clustering or partitioning

**Lifecycle**
- Backfill or full-refresh an incremental model
- Ship a change: branch → PR → merge → verify in production
- Upgrade dbt or a package
- Onboard to an unfamiliar project

### Diagnostics

Failing test · failing production job · stale or missing data · wrong numbers · slow build · unexpected DAG behavior · dev/prod divergence

## Why the router exists

27 skills across 77 files is more than can be read per task. An ordinary request — "add a column to an incremental mart model" — has plausible description matches totalling over 8,000 lines. Reading all of it is not an option, and reading whichever matched first is how a task gets done wrongly with confidence.

`dbt-navigating-skills` resolves that by classifying a request into one of 14 task archetypes and naming the minimum set of sections to read at each phase, plus the conditions for escalating further. It exists so the rest of the library can be deep without being unusable.

This is a real tradeoff, worth stating rather than hiding: a router adds a layer of indirection, and if it misclassifies, it confidently sends the agent to the wrong place. The alternative — a flat library where the agent picks by description match — fails more often and less visibly, because nothing is accountable for the choice.

## Adding a skill

Three questions, in order:

1. **Which task on the surface above does it cover, and does something already cover it?** If an existing skill covers it thinly, extend that skill or add a sub-document. A new top-level skill competes for description-match attention with every existing one.
2. **Which tier and phase?** If it does not fit a tier, that usually means it is a sub-document of an existing skill rather than a skill.
3. **Is it reachable?** A skill the router never names is a skill that never loads. Add it to an archetype in `dbt-navigating-skills`, or it is dead weight — CI fails a skill that nothing references.

See `CONTRIBUTING.md` for the mechanical requirements.
