# dbt Navigator

**Agent skills for the full dbt development lifecycle** — orient, decide, build, prove, ship.

A skills library that teaches coding agents how to work in a **dbt** project — and reads your conventions from a file you own, instead of imposing someone else's.

Installable as a plugin for **Claude Code**. Works in **Cursor** via `npx skills add`, and via `AGENTS.md` with **Codex**, **Gemini CLI**, and any agent that reads it.

## Why this exists

Generic coding-agent methodologies handle TDD and debugging well. None of them know that `ref()` silently falls back to production, that `merge` leaves stale rows when a source reprocesses, or that a globally-inherited schedule tag should never be repeated on an individual model.

This library is that body of dbt knowledge, distilled from operating a large production dbt project on a columnar warehouse and cross-checked against public dbt guidance. The distinguishing choice is not the knowledge itself — it is that the enforcement logic is separated from the values, so the same skills fit a project whose conventions look nothing like the one they were written against.

## What it looks like

Ask an agent to rename a column. Without this library, it edits the SQL and the YAML, compiles, and reports success — which is correct in the same way that unplugging a monitor fixes a warning light.

With it, the agent classifies the request as a breaking change and stops before editing:

```
This is a breaking change (column rename), so ordering matters. Before editing:

  Downstream refs      4 models — 2 marts, 1 intermediate, 1 exposure
  BI consumers         3 dashboards reference `revenue_net`
  Enforced contract    yes — fct_orders declares this column
  Grants               the mart carries a masking policy on 1 column

`grep` found 4 refs. That bounds what I searched, not what exists — the query
log is the only instrument that sees consumers in no catalog and no exposure,
and I could not reach it. Treat "4" as a floor.

Recommended: expand / migrate / contract, not a rename.
  1. Add `net_revenue` alongside `revenue_net`, both populated
  2. Migrate the 4 refs and 3 dashboards
  3. Drop `revenue_net` once a full refresh cycle confirms nothing reads it

A direct rename breaks the contract at build time and the dashboards silently.
Proceed with expand/migrate/contract, or do you want the direct rename?
```

Three things there are the whole point. It **refused to start with the edit**, because ordering is what makes a breaking change safe. It **named what it could not check** instead of presenting four as a complete answer. And it **ended with a decision for a human**, because which tradeoff to accept is not the agent's call.

## Who this is for

Worth being direct, because the value is not uniform.

**A good fit** if your project has roughly 100+ models, more than one source system feeding the same concepts, incremental models holding history the source cannot reproduce, and BI consumers who will notice a renamed column. The Operations tier assumes exactly that situation, and that is where it pays for itself.

**Not worth it yet** on a 30-model project with one source and no BI layer. Contract derivation has little to measure, and most of the Operations tier addresses failures you have not had. dbt's own docs plus dbt Labs' skills are a better fit until the DAG has consumers you cannot enumerate from memory.

**On the evidence:** this comes from one large project, cross-checked against public dbt guidance. Where a claim rests on a single project's measurement it is labelled as such rather than presented as an industry figure — [`docs/skill-catalog.md`](docs/skill-catalog.md) carries the caveats, including an explicit n=1 warning on the one frequency statistic quoted anywhere.

### How this relates to dbt Labs' own agent skills

dbt Labs publishes an [official agent-skills repository](https://github.com/dbt-labs/dbt-agent-skills), and it is the first thing to evaluate. Theirs is strongest where it wraps dbt's own surface: the semantic layer and MetricFlow, dbt Mesh governance, MCP setup, platform job troubleshooting, and Core-to-Fusion migration.

This library covers a different axis — the **operations** tier, where a DAG that already has consumers gets changed: propagating a column, proving a refactor changed nothing, renaming when BI depends on the old name, backfilling without losing history. There is almost no overlap in the task surface.

It also takes a different shape on conventions: one `conventions.yml` contract that every skill reads, so a team states its taxonomy, layer rules, dev detection, and test policy **once** rather than shadowing individual skills.

**Install both.** If you want dbt platform and semantic-layer coverage, theirs does it and this one deliberately does not.

## The design decision that matters

Most convention tooling ships one team's conventions and asks you to adopt them. This ships the **enforcement logic** and reads the **values** from your `conventions.yml`.

That means `dbt-project-conventions` works whether your prefixes are `stg_/int_/dim_` or `bronze_/silver_/gold_` or something nobody has thought of yet.

Three tiers:

| Tier | Configuration | Example |
|---|---|---|
| **Universal** | None. Works on install. | `= null` is never true · `>=` not `>` on incremental boundaries · never commit on `main` |
| **Contract-driven** | Reads `conventions.yml` | naming taxonomy · layer rules · dev detection · schedule tags · test policy |
| **Dialect-gated** | Reads `project.warehouse` | `group by all` — full support on Snowflake/BigQuery/Databricks/DuckDB/Redshift, absent on Postgres, and a silent trap on Trino where `ALL` means something else |

## Quickstart

You do not write `conventions.yml` from scratch. After installing (see [Installation](#installation)), ask your agent to derive it — this one prompt is the whole first-run step:

```
Read the dbt-deriving-project-context skill and derive this project's context.
Measure the repo, don't assume. Write conventions.yml plus the .dbt-agent/
prose files, cite the count behind each value, mark inferences NEEDS
CONFIRMATION, and list what you could not establish as questions for me.
```

The agent measures your repo — prefix distribution, separator, materializations, timestamp suffixes, surrogate key naming, schedule tags — and presents a draft with adherence percentages for you to confirm. Only genuinely unknowable fields become questions.

It also does two things a taxonomy pass alone does not. It finds the **bespoke machinery** your project already has, so an agent stops hand-rolling what you solved years ago — including overridden dbt built-ins, which appear in no model file because dbt calls them automatically. And it **appraises** what it finds: each practice is recorded as best practice, a deliberate variant that works better here, or a candidate defect. Findings are reported, not silently corrected.

Example of the kind of draft it produces:

```
stg_ 142 · dim_ 38 · fct_ 26 · int_ 24 · base_ 9
→ 239 of 252 models (95%) match a 4-layer taxonomy
→ separator `__` in 193/252 (77%)
→ surrogate key named `unique_id` in 61 of 64 models that declare one (95%)
→ legacy prefixes not inferable as deprecated — asked, confirmed
```

Then copy `examples/conventions.example.yml` as a reference and edit.

**On validation:** the skill validates the draft against `schema/conventions.schema.json`. The whole library ships with that file — a plugin install copies the entire repo to the plugin cache, and a clone has it at the repo root — so the schema is available either way; the skill just has to locate it (it checks both spots). Validation is a recommended check, not a gate: if the schema genuinely cannot be found, the agent records that the contract was not schema-validated rather than faking a pass. And a schema-valid contract can still be factually wrong, so the independent re-derivation the skill runs afterward matters more than the validation either way.

**No contract?** The skills still work. They fall back to generic principles and tell you they are doing so. They never guess at your prefixes.

## Bringing your own context

The contract records *conventions*. Two other things decide whether an agent asks you a question it should already know the answer to.

**Optional, and worth adding in this order.** Everything below is a fallback for knowledge that has no home in dbt itself — always prefer a `description`, `meta`, `exposure`, or source `freshness` block when one fits, because those live next to what they describe and ship to your catalog.

```yaml
# conventions.yml
context:
  domain_notes: .dbt-agent/domain.md      # business meaning, canonical metrics, traps
  references: .dbt-agent/references.md    # index of external docs — links, never copies
  meta_keys:                              # YOUR key names; values live in dbt `meta`
    sla: sla_hours
    criticality: criticality
    owner: owning_team
```

| File | Answers the question | Template |
|---|---|---|
| `domain.md` | "Which of these two revenue definitions is canonical?" — the things no query returns | `examples/domain.example.md` |
| `references.md` | "Where is the retention policy?" — an index stating which question each document answers | `examples/references.example.md` |
| `meta_keys` | "Is this table *late*?" — needs a declared SLA; staleness alone is not lateness | Set on models in `meta` |

The filter for what to write down is one question: **could a connected tool compute this?** If yes, leave it to the tool. A copy of a derivable fact goes stale while the real answer moves on, and the copy is what gets believed. Column types, lineage, run history, and schedules are all derivable — business meaning, closed decisions, SLAs, and criticality are not.

Start with three canonical metric definitions and stop. The failure mode is not a thin file; it is a thorough one nobody maintains, because a stale note gives no sign that it is stale.

`dbt-gathering-context` is the skill that reads all of this, and it enforces the ordering: **written context loses to observation on any fact a query can check.** Its authority covers intent and meaning, never current state.

**Rolling this out on a team?** [`docs/adoption.md`](docs/adoption.md) is the week-by-week path, with the checkpoint at each stage that tells you it is actually working.

**Optional speed layer.** `agents/` holds four subagents for the jobs that read wide and report narrow — deriving project context, tracing who consumes a model, reviewing a diff independently, and profiling a slow query. They contain no guidance of their own; they preload the same skills and return a structured finding. A plugin install brings them along; `npx skills add` installs skills only. [`docs/subagents.md`](docs/subagents.md) covers how they arrive per channel, why there are four rather than one per skill, and how to port them to a harness without a subagent mechanism — do not hand-copy them into Cursor, since the tool restriction and skill preloading live in fields Cursor's format lacks. Skip the whole layer and nothing breaks.

**Optional deterministic backstop.** The library enforces nothing through the harness — the skills are the enforcement. But a handful of mistakes are pure facts a hook can catch deterministically (a commit on `main`, a model that failed to compile, a destructive warehouse command). [`docs/hooks.md`](docs/hooks.md) is a shortlist of the checks worth wiring up, with minimal snippets — no harness-specific config, because hooks are not portable. If your harness has no hooks, the skills still carry every check.

## Installation

### Claude Code

```
/plugin marketplace add yshah-1108/dbt-navigator
/plugin install dbt-navigator@dbt-navigator-marketplace
```

The marketplace lives in the same repo as the plugin, so adding the marketplace and installing the plugin both come from one GitHub source. Updates: `/plugin marketplace update dbt-navigator-marketplace`.

### Cursor

Cursor's plugin marketplace is curated — plugins are submitted and manually reviewed before listing — and **there is no working path for installing an unlisted plugin by hand.** Dropping a clone into `~/.cursor/plugins/local/` does not load it: tested with a valid `.cursor-plugin/plugin.json`, all 27 skills, and the `agents/` directory in place, Cursor picked up none of it. Until it clears review, install the skills directly instead — this is the recommended route, not a workaround, and Cursor reads these paths natively:

```bash
npx skills add yshah-1108/dbt-navigator --global   # ~/.cursor/skills/, all projects
npx skills add yshah-1108/dbt-navigator            # this project only
```

Confirm under **Customize → Skills**. For a project-local install, committing `.cursor/skills/` gives your whole team the same skills at the same version, which is usually what you want for a shared dbt repo.

The subagents need one extra step, because Cursor's agent format lacks the two fields these rely on:

```bash
python3 scripts/port-agents.py cursor <dest-repo>
```

Do not hand-copy them — see [`docs/subagents.md`](docs/subagents.md) for why a blind copy produces an agent that never reads its skills and holds write access it was designed not to have.

### Any agent supporting the Agent Skills standard

```
npx skills add yshah-1108/dbt-navigator --global
```

Installs the skills into your agent's skills directory. Works with any agent that has adopted the [Agent Skills](https://agentskills.io/home) format. It reads this repository from GitHub directly — nothing is published to npm, so the version you get is whatever `main` holds.

**This route installs the 27 skills and their sub-documents, and nothing else** — not `AGENTS.md`, not `agents/`, not `schema/`. The skills are self-contained on technical matters, so the correctness rules that matter most (the `>=` incremental boundary, `delete+insert` on a reprocessing source, `full_refresh=false` on irreplaceable history) still reach you inside the skills that own them. What does not travel is the behavior contract — intellectual honesty, scope discipline, derive-versus-ask, never committing on `main` — plus the JSON schema that validates `conventions.yml`. If your agent reads an `AGENTS.md`, add it alongside as described below; if you want schema validation, clone instead.

### Codex / Gemini CLI / anything reading AGENTS.md

Clone anywhere and point your agent at it, or copy `AGENTS.md` and `skills/` into your project. `CLAUDE.md` is a symlink to `AGENTS.md` and `GEMINI.md` includes it, so there is exactly one copy of the rules and it cannot drift. The `schema/` and `examples/` directories come with the clone, so schema validation works out of the box this way.

## What's inside

**Rules** — `AGENTS.md`. 18 universal rules, 11 contract-driven ones, a session-state discipline for what a long conversation forgets, and a behavior contract covering intellectual honesty, scope discipline, the derive-versus-ask discipline, and adversarial self-review.

**Skills** — 27, loaded on demand by task.

Skills are named after **what you are doing**, not by topic — so the trigger is the task.

### The lifecycle

Every dbt change moves through the same phases, and skills attach to phases rather than to topics. That is what makes the order predictable instead of a matter of which description happened to match first.

| Phase | The question it answers |
|---|---|
| **Orient** | What is true of this project, and what am I actually being asked? |
| **Decide** | Should this be built at all, and as what? |
| **Build** | Write the thing |
| **Prove** | Did it do what I claim? |
| **Ship** | Get it to production safely — and what happens after the merge |
| **Diagnose** | Something is wrong. Entered directly, off the spine |

Two properties of that spine do most of the work. **Orient is never skipped**, because every later decision depends on the contract, and advice given without one is generic advice that should say so. And **Prove is not last** — the verification gets chosen while building, because "how would I know if this were wrong?" changes what you build.

`dbt-navigating-skills` is the router that walks the spine. It classifies a request into one of 14 task archetypes and names the minimum set of sections to read at each phase, which is what keeps a library this size usable: an ordinary task has plausible description matches totalling over 8,000 lines, and reading whichever matched first is how work gets done wrongly with confidence.

| Tier | Skill | Use when |
|---|---|---|
| Foundations | [`dbt-navigating-skills`](skills/dbt-navigating-skills/SKILL.md) | **Start here for any task.** Decides what to read and in what order — classifies the request, names the minimum read set per phase, and gives the conditions for escalating to a further skill |
| Foundations | [`dbt-onboarding-to-a-project`](skills/dbt-onboarding-to-a-project/SKILL.md) | Starting work in a project you have not measured this session — a fresh install, a new repo, or the first task after a context reset |
| Foundations | [`dbt-deriving-project-context`](skills/dbt-deriving-project-context/SKILL.md) | First install into a project, or the contract and context files are missing or stale — measure the conventions, find the bespoke machinery, and appraise whether each practice is best practice, a better local variant, or a defect |
| Foundations | [`dbt-gathering-context`](skills/dbt-gathering-context/SKILL.md) | A task depends on a fact you don't have — who consumes this, what the grain is, whether a value is a bug, what threshold is acceptable |
| Foundations | [`dbt-designing-a-model`](skills/dbt-designing-a-model/SKILL.md) | A request arrives as an output rather than a dataset ("I need a dashboard that…"), or the grain of a proposed model is unclear |
| Foundations | [`dbt-project-conventions`](skills/dbt-project-conventions/SKILL.md) | Naming a model, choosing a prefix or layer, or deriving the contract for a new install |
| Foundations | [`dbt-environments`](skills/dbt-environments/SKILL.md) | Building or querying in dev, writing a validation query, or unsure whether a result came from dev or prod |
| Foundations | [`dbt-verification`](skills/dbt-verification/SKILL.md) | About to report a change is complete, or asked whether it is safe |
| Artifacts | [`dbt-authoring-sql-models`](skills/dbt-authoring-sql-models/SKILL.md) | Writing or editing model SQL — CTE structure, joins, casting, layer choice |
| Artifacts | [`dbt-authoring-schema-yaml`](skills/dbt-authoring-schema-yaml/SKILL.md) | Documenting a model or column, choosing tests, adding a contract or constraint |
| Artifacts | [`dbt-unit-tests`](skills/dbt-unit-tests/SKILL.md) | Logic data tests cannot verify — regex, date math, window functions, edge cases not yet in the data |
| Artifacts | [`dbt-incremental-models`](skills/dbt-incremental-models/SKILL.md) | Creating or changing an incremental model, choosing a strategy, or setting a boundary predicate |
| Artifacts | [`dbt-snapshots`](skills/dbt-snapshots/SKILL.md) | Creating or modifying a snapshot, choosing timestamp vs check, handling hard deletes |
| Artifacts | [`dbt-macros`](skills/dbt-macros/SKILL.md) | Writing a macro, extracting repeated SQL, or debugging Jinja |
| Artifacts | [`dbt-python-models`](skills/dbt-python-models/SKILL.md) | Deciding whether logic needs Python, or debugging a Python model |
| Artifacts | [`dbt-sources-and-seeds`](skills/dbt-sources-and-seeds/SKILL.md) | Defining a source, configuring freshness, or choosing between a seed and a source |
| Artifacts | [`dbt-handling-sensitive-data`](skills/dbt-handling-sensitive-data/SKILL.md) | Selecting a column that may be personal or regulated data, setting grants, or handling a deletion or retention request |
| **Operations** | [`dbt-adding-columns`](skills/dbt-adding-columns/SKILL.md) | Adding a column and propagating it source → mart |
| **Operations** | [`dbt-unifying-sources`](skills/dbt-unifying-sources/SKILL.md) | Combining the same concept from several source systems into one model |
| **Operations** | [`dbt-restructuring-dags`](skills/dbt-restructuring-dags/SKILL.md) | Splitting or combining models, inserting a layer, repointing a `ref()` |
| **Operations** | [`dbt-refactoring-safely`](skills/dbt-refactoring-safely/SKILL.md) | Changing SQL with no intended change in output |
| **Operations** | [`dbt-breaking-changes`](skills/dbt-breaking-changes/SKILL.md) | Renaming or removing a column, changing a type or grain, deleting a model |
| **Operations** | [`dbt-performance-tuning`](skills/dbt-performance-tuning/SKILL.md) | A model is slow, scans too much, or costs are regressing |
| **Operations** | [`dbt-shipping-changes`](skills/dbt-shipping-changes/SKILL.md) | Branch, PR, merge — and what happens in production afterwards |
| Diagnostics | [`dbt-debugging-failures`](skills/dbt-debugging-failures/SKILL.md) | A test fails, a job fails, a model will not compile, or dev and prod disagree |
| Diagnostics | [`dbt-data-quality-triage`](skills/dbt-data-quality-triage/SKILL.md) | The build is green but the numbers are wrong or stale |
| Reference | [`dbt-command-reference`](skills/dbt-command-reference/SKILL.md) | Constructing a CLI command, selecting nodes, or unsure where a flag belongs |

Full catalog — what each skill covers and which contract fields it reads: [`docs/skill-catalog.md`](docs/skill-catalog.md) · architecture: [`docs/skill-architecture.md`](docs/skill-architecture.md)

**Operations is the tier that matters.** Authoring a model is well covered by dbt's own docs. Safely reshaping a DAG that already has consumers is not — and that is where data gets lost.

**Schema** — `schema/conventions.schema.json`. JSON Schema for editor autocomplete and validation.

## Philosophy

- **Measure, don't assert.** A prefix distribution takes one command. Report the number.
- **Deviations are data.** If 30% of models break a "convention," the convention is stale, not the models.
- **Grandfathered is not wrong.** A model that predates a rule is legacy. Flagging it teaches people to ignore the tool.
- **Absent config means generic advice, not invented advice.**
- **Verification over confidence.** Done requires an external signal.

## Roadmap

Shipped when proven, not when written. Deliberately **not** shipping yet:

- **Deterministic enforcement.** Blocking a destructive command requires harness-specific hooks with no portable contract. A working engine with 41 passing tests exists as a companion project and will be released separately as a CI/pre-commit linter, where determinism is portable because CI is not a harness.
- **A learning loop.** Having an agent capture its own corrections and fold them back into the skills is an appealing idea that does not survive measurement: automatic capture produces overwhelmingly noise, and the signal that remains is too sparse to justify the mechanism. It will ship if that changes, and not before.

## License

MIT
