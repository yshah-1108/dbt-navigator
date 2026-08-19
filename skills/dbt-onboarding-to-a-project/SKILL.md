---
name: dbt-onboarding-to-a-project
description: Use when starting work in a dbt project you have not measured this session — a fresh install, a new repository, the first task after a context reset, or before touching a model whose consumers you have not enumerated. Covers DAG shape, terminal and dead nodes, orchestration and CI discovery including what each workflow is for, BI consumers, the business map of source systems and how entities link across them, dbt version and adapter detection, test coverage, activity, and grandfathered patterns.
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/survey-project.sh *) Bash(${CLAUDE_SKILL_DIR}/survey-project.sh)
metadata:
  phase: orient
---

# Onboarding to a project

You start cold every session. The project has years of accumulated decisions in it and you have none of them, and nothing about the repository announces which of the 600 files is the one that 40 other models read. That asymmetry is the whole problem: **the cheapest possible mistake is editing a model you believe is a leaf.** It compiles, it builds, your tests pass, and forty downstream models are now producing different numbers.

The second failure is quieter. You find a model that breaks the project's own conventions, "fix" it, and discover it was deliberately frozen — grandfathered, load-bearing, and left alone on purpose by people who knew more than the filename told you.

Both are prevented by the same twenty minutes of measurement. Everything below is roughly one command, because the library's position is that a project fact you can measure is never a project fact you should assume. Run these, report the numbers, then start work.

> **Fast start (optional).** `${CLAUDE_SKILL_DIR}/survey-project.sh` runs the mechanical, read-only sections — 1 (version and adapter), 2 (DAG shape and child counts), 3 (entry points), 4's two in-repo dead-model signals, 7 (test coverage), and 8 (activity) — in one pass, and refreshes the manifest once instead of per section. Pass a model name (`${CLAUDE_SKILL_DIR}/survey-project.sh <model>`) to also get that model's transitive lineage and history. The skill pre-approves that exact command in `allowed-tools`, so it runs without a prompt; if your harness does not honor `allowed-tools`, `bash survey-project.sh` from the skill's folder is equivalent. It **dumps facts; it does not interpret them.** Every judgment below is still yours: splitting zero-child models against `layers[].terminal`, the orchestrator sweep (5), BI-consumer discovery (6), and the warehouse query-log tests all need a decision or a connection the script does not have, and stay inline. Use it to skip the typing of the deterministic parts, not the thinking. The inline commands remain the measure-one-thing-then-read path when you would rather go section by section.

**This is the most common application of `dbt-gathering-context`.** That skill states the general discipline — derive silently, ask only what no tool can answer, never read a well-formed empty result as knowledge. This one is the specific battery of measurements for arriving somewhere new. Read it if you have not; do not re-derive its reasoning here.

**Naming conventions are out of scope.** Prefix distribution, separator, timestamp suffixes, surrogate key naming and materialization-per-layer are measured by [inferring-conventions.md](../dbt-project-conventions/inferring-conventions.md), which produces a contract draft. Run that once per project and commit the result. Onboarding covers everything the taxonomy does not: shape, consumers, orchestration, version, coverage, and age.

**On a first install, or when the contract and context files are missing or stale, run [`dbt-deriving-project-context`](../dbt-deriving-project-context/SKILL.md) instead of this skill.** It owns the full install-time pass — measuring the taxonomy, finding the project's bespoke machinery, and appraising each practice as best practice, a better local variant, or a defect. The two skills divide cleanly: onboarding tells you **what is here and what depends on it**; that skill tells you **how this team works and whether that way is good**. Section 9 below dates a deviation to decide whether it is *deliberate*; the appraisal step there decides whether it is *good*.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | What it decides here |
|---|---|
| `project.dbt_project_name` | The node-ID prefix every manifest query and metadata API call needs — `model.<dbt_project_name>.<model>` |
| `project.warehouse` | Whether a query log exists at all, and which relation holds it. Gates every warehouse-specific command below |
| `layers[]` | Which folders are which layer, so node counts can be grouped meaningfully instead of by directory name |
| `layers[].terminal` | Which models are *supposed* to have zero children — so a childless model is expected rather than suspicious |
| `bi.consumers[].repo_path` | The repositories to grep. The DAG cannot see these and no amount of `dbt ls` will find them |
| `bi.consumers[].status` | Whether a discovered consumer blocks work or merely needs notice |
| `schedules.default_tag` | The tag inherited globally — models carrying it are not thereby non-default |
| `testing` | The coverage bar to measure against, rather than a bar you invented |

**Without a contract:** offer to generate one, and say plainly that everything you report is measurement rather than policy. You can still count nodes, trace children, and read git history — none of that needs configuration. What you cannot do is call any pattern a *violation*, because nothing has declared the rule.

**Without `project.warehouse`:** do not guess at a query-history relation. Every warehouse names it differently and several do not have one. Read the adapter from the profile instead (step 1), and if you still cannot establish it, state that dead-model detection is running on two of its three tests and is therefore capable of false positives. Say which two.

**Without `layers[].terminal`:** a model with zero children is ambiguous — it may be a report that nothing should read, or genuinely abandoned. Do not classify it. Report the list and ask which layers are terminal by design.

**Without `bi.consumers`:** you have no way to enumerate BI repositories and must not invent them. Ask which tools read this project, fall back to exposures and the query log, and mark BI coverage as **unverified** in your summary. An unverified consumer list reported as "no consumers" is worse than reporting the gap, because it will be believed.

---

## 1. dbt version and adapter

**This skill owns version detection.** Six other skills gate advice on a minimum version — unit tests need 1.8+, the microbatch incremental strategy and YAML snapshots need 1.9+, `arguments:` in test definitions needs 1.10.5+ — and none of them should be re-deriving it. Establish it once, on arrival, and carry it for the session.

```bash
dbt --version
```

That reports the installed core version *and* the installed adapters, which is two of the three facts you need in one command. The third is what the project will *accept*:

```bash
grep -n 'require-dbt-version' dbt_project.yml
grep -rn 'dbt-version\|dbt_version' packages.yml package-lock.yml 2>/dev/null
```

`require-dbt-version` is a floor, a ceiling, or both, and it is the project's declared position rather than your machine's accident. When the two disagree, the constraint is what matters: a project pinned below the version you have installed will refuse to run, and a project pinned *above* it means your local build is not the build CI performs. Neither is a detail to discover halfway through a task.

The adapter matters as much as the version, and for a different reason:

```bash
grep -A6 -E '^\s*(outputs|target):' ~/.dbt/profiles.yml 2>/dev/null
python3 -c "import json;m=json.load(open('target/manifest.json'));print(m['metadata']['adapter_type'], m['metadata']['dbt_version'])" 2>/dev/null
```

The manifest records both, authoritatively, for the last parse — prefer it when `target/` is current, and run `dbt parse` first when it is not. The adapter decides which SQL is *legal*, not merely idiomatic: `group by all` does not exist on several warehouses, `qualify` exists on some, merge semantics differ, and the query-history relation is named differently or absent entirely. Cross-check it against `project.warehouse`; if they disagree, the contract is stale and the adapter wins.

Why this is worth a section rather than a footnote:

- **Version-gated features fail differently than syntax errors.** A YAML key an older version does not understand is frequently *ignored*, not rejected. You write a unit test, the parse succeeds, nothing runs it, and you report coverage you do not have.
- **Deprecation behavior is version-specific.** Behavior-change flags introduced in one minor version become defaults in a later one, so identical project code produces different results across two versions that are both "recent". Read the flags block before assuming a default:
  ```bash
  sed -n '/^flags:/,/^[a-z]/p' dbt_project.yml
  ```
- **Recommending a feature the project cannot use is worse than recommending nothing.** It costs a round trip and it teaches the user your advice needs checking.

**Record the version and adapter in your summary once, and cite them when gating any recommendation.** Then recommend adding `project.dbt_version` to the contract, so the next session reads it instead of shelling out.

---

## 2. DAG shape

The point of measuring shape is not orientation, it is danger. You are looking for the handful of models where a small edit has a large blast radius, before you edit one of them by accident.

```bash
dbt parse                                    # refresh the manifest before reading it
dbt ls --resource-type model | wc -l
dbt ls --resource-type source | wc -l
dbt ls --resource-type test | wc -l
dbt ls --select "config.materialized:incremental" | wc -l
```

Counts by layer, using the paths the contract declares:

```bash
for p in $(ls -d models/*/); do echo "$(dbt ls --select "path:$p" --resource-type model 2>/dev/null | wc -l) $p"; done
```

Then the part that matters — **child counts per model**, from the manifest. This is a fixed manifest parse with nothing to adapt, so `${CLAUDE_SKILL_DIR}/survey-project.sh` runs it (and refreshes the manifest first): it prints the model count, the zero-child count, and the top 15 models by direct child count. Read that output the same way whether the script or a hand-run parse produced it:

The top of that list is the project's load-bearing structure. **Treat any model with a double-digit child count as a change that needs `dbt-breaking-changes` before it needs an editor**, whatever the task sounded like. Direct children understate it, too — use `dbt ls --select "<model>+" | wc -l` for the transitive count on anything you intend to touch.

The survey counts an exposure as a child, deliberately: a model consumed only by an exposure has no dbt descendants and is emphatically not unused. So its zero-child list is already the narrower, more interesting one — models nothing in the project references and no exposure claims.

Depth is the other half:

```bash
dbt ls --select "<model>+" --resource-type model | wc -l   # transitive descendants
dbt ls --select "+<model>" --resource-type model | wc -l   # transitive ancestors
```

A long chain means a rebuild is slow and a mistake propagates far before anyone sees it. A wide fan-out means many owners are affected at once. They call for different caution: depth wants verification at each level, width wants coordination.

---

## 3. Entry points and terminal nodes

```bash
dbt ls --resource-type source
dbt ls --select "source:*+1" --resource-type model     # models reading a source directly
```

Sources are where assumptions about the outside world live, and a project with a hundred sources has a hundred ways to be wrong for reasons that are not its fault. Note any model reading a source from outside the layer the contract says may do so — not to fix it, but because it means the layering rule has exceptions you will meet again.

Terminal nodes come out of the same manifest pass as step 2: every model with a zero child count. Split them immediately against `layers[].terminal`:

| Zero children, and… | Reading |
|---|---|
| In a layer the contract marks `terminal` | Expected. This is the layer's design. |
| Referenced by an exposure | Consumed outside the DAG. Not a leaf in any meaningful sense. |
| In a non-terminal layer, no exposure | **Suspicious.** Candidate for step 4, not a conclusion. |

The distinction is the entire value of the measurement. Without it, a report mart correctly designed to have no children looks identical to a model somebody abandoned eighteen months ago.

---

## 4. Dead or dying models

A model is a deletion candidate when **all three** of these hold, and finding one or two of them proves nothing:

1. **Zero children in the DAG.** `dbt ls` sees `ref()` edges and nothing else.
2. **Not referenced by an exposure.** Exposures are the only in-repo record of an external consumer, and only where someone bothered to write one.
3. **Not read in the warehouse query log.** The one test that sees notebooks, scheduled exports, reverse ETL, another team's script, and a saved query someone runs every Monday.

```bash
dbt ls --resource-type exposure
python3 -c "
import json; m=json.load(open('target/manifest.json'))
for e in m.get('exposures', {}).values():
    print(e['name'], e.get('type'), '->', [n.split('.')[-1] for n in e['depends_on']['nodes']])
"
```

The third test needs the warehouse, and is gated on `project.warehouse`:

```sql
-- Adapt the relation name to the warehouse the contract declares.
-- The shape is what generalizes: who read this relation, how recently, how often.
select <user_column>, count(*) as query_count, max(<start_time_column>) as last_read
from <query_history_relation>
where <query_text_column> ilike '%<table_name>%'
  and <start_time_column> >= current_date - 30
group by 1
order by query_count desc
```

**Why any single test gives false positives, individually:**

- Zero children alone flags every correctly-designed terminal model in the project. On a report-heavy project that is most of the marts.
- No exposure alone means only that nobody wrote one. Exposure coverage is voluntary and usually partial; treating its absence as evidence is the textbook case of an unpopulated field read as an answer.
- A quiet query log alone can mean the reader runs quarterly and your window was thirty days, or that retention is shorter than the reading pattern, or that the consumer queries a view built on top of the model and never names it.

**Never state a lookback longer than retention.** Check `project.query_history_retention_days` before writing the window into a finding. A thirty-day claim drawn from a log that only keeps seven days is not a weak claim, it is a false one — and "no reads in 30 days" is the sentence that gets a live model deleted. If retention is shorter than the window you need, say what you actually measured ("no reads in the 7 days the log retains") and treat the rest as unmeasured. If the field is unset, the honest report is that the query log was unavailable, which is a different finding from a quiet one.

So the honest output of this step is a **candidate list with the evidence attached**, not a deletion plan:

> `<model>`: no `ref()` in the DAG, no exposure, no reads in the 30-day query log. Retention on that log is <N> days, so a quarterly consumer would not appear.

Then stop. **Deleting a model is `dbt-breaking-changes`, and it is a decision the user makes, not a conclusion you reach.** Onboarding produces the list. A model that is genuinely dead has been dead for a year and can wait for a sentence of confirmation.

---

## 5. Orchestrator and CI discovery

Nothing in the repository tells you what runs. **Tags express intent; only the scheduler knows truth.** A model tagged for an hourly cadence is a model somebody once intended to run hourly, and the job that would have done it may have been disabled two quarters ago.

Find the orchestrator by looking for its configuration, in decreasing order of how conclusive a hit is:

```bash
ls -d .github/workflows .gitlab-ci.yml .circleci dagster* airflow* dags 2>/dev/null
ls .pre-commit-config.yaml 2>/dev/null                # not an orchestrator; changes what to do by hand
grep -rln 'dbt build\|dbt run\|dbt-core\|dbt_cloud\|dbt seed' \
  .github .gitlab-ci.yml .circleci ci 2>/dev/null
grep -rn 'dbt build\|dbt run' Makefile* justfile* scripts/ 2>/dev/null | head -20
```

Then read the *selectors*, because the selector is the mapping from job to models:

```bash
cat selectors.yml 2>/dev/null                       # named selectors, if the project uses them
grep -rhoE '\-\-select[= ]+"[^"]+"' .github ci scripts 2>/dev/null | sort | uniq -c | sort -rn
```

Four outcomes, and each has a different honest report:

| What you find | What to conclude |
|---|---|
| CI/CD config invoking dbt with selectors | The selectors are the job definitions. Resolve one against the DAG with `dbt ls --select "<selector>"` to learn which models a job actually builds. |
| A managed platform, no scheduling in the repo | The API is authoritative. Query jobs and their selectors; do not reconstruct the schedule from tags. |
| An external scheduler with its own repository | You cannot see it. Say so, and ask where it lives. |
| Nothing at all | Either scheduling is genuinely elsewhere or it is manual. **Do not conclude "unscheduled."** Ask. |

Whatever you find, do not restate crons into the contract. A hand-maintained copy of live scheduling state rots silently and is then worse than nothing, which is exactly why the schema records tags and cadences rather than crons. `dbt-shipping-changes` covers what to do with this once you have a change to merge; the relevant fact on arrival is simply **which models are built by an automated job at all**, because a model nothing builds is a model whose freshness is nobody's responsibility.

One trap specific to `schedules.default_tag`: a tag set globally in the project file is *inherited*, and it shows up on individual models in `dbt ls --select "tag:<tag>"` exactly as though it were declared there. Do not read an inherited tag as a per-model decision, and never add it to a model file.

### Enumerating jobs on a managed platform

When scheduling lives in a platform rather than the repo, the API is the only authority, and "read the API" is not a step until you know what to read. Three calls, in order, and the third is the one people skip:

1. **List the jobs.** You want, per job: its name, whether it is *enabled*, its trigger (schedule, PR, API-only), and its cadence. A disabled job is the single most common reason a model that looks scheduled is not.
2. **Read each job's steps** to get the actual dbt command and its selector. A job named for one domain routinely builds three.
3. **Resolve every selector against the DAG** — `dbt ls --select "<selector>"` — and take the union. That union, not the tag list, is the set of models something builds. Anything outside it is unscheduled no matter what it is tagged.

Where an MCP server or CLI for the platform is connected, these are three tool calls; `dbt-command-reference` covers the call shapes and the traps, including that metadata tools read the *deployed* manifest rather than your branch. Where nothing is connected, this is a question for the team rather than a guess — see below.

Two failure modes worth naming because both produce a confident wrong answer. **Reading job names as scope**: names drift from selectors immediately and nobody notices, because the job still runs. And **ignoring the enabled flag**: a disabled job appears in the list, has a cadence, and builds nothing.

### What each CI workflow is *for*

Finding `.github/workflows/` tells you CI exists. It does not tell you what CI *does*, and the difference changes what you should do by hand. Read each workflow and classify it — the classification is the deliverable, not the file list:

| Class | Tells you |
|---|---|
| **Builds or deploys dbt** | This is orchestration, not just a check. Its selector belongs in the union above. |
| **PR quality gate** | What is already enforced mechanically — lint, compile, contract checks, a leak scan. Do not hand-check what a gate catches, and do not fight a gate you did not know existed. |
| **Generates a committed artifact** | The highest-value class to spot: where exposures, docs, or a schema file are machine-written, hand-editing them is work that the next run silently reverts. This inverts a skill's advice from *write this* to *do not touch this*. |
| **Unrelated to dbt** | Note and move on, so it is not re-read every session. |

Also read `.pre-commit-config.yaml` if present. It is not in the `ls -d` sweep above because it is not an orchestrator, but it changes the same calculus: a formatter that runs on commit means formatting is not your job, and a hook that will reject your commit is worth knowing before you write it rather than after.

For each workflow, the durable facts are its **purpose** and **what it enforces or generates** — not its YAML, which is one `cat` away. Those belong in `context.mechanisms`, whose template already has sections for CI checks that run on a PR and for generated artifacts.

### When discovery runs out, ask — carrying what you already measured

Two of the four outcomes above end in a question, and asking it well is the difference between a two-minute reply and an investigation you caused. You have just measured the DAG, the selectors and the workflows; put that in the ask. Batch these rather than asking serially:

| Ask | Why no tool answers it |
|---|---|
| Which tool runs production builds, and where does its config live? | If it is in another repository or a UI, nothing here can see it. Absence of config is not absence of scheduling. |
| Which job builds *the models this task touches*, and how often? | You can resolve selectors only for jobs you can enumerate. Name the models; do not ask them to explain the whole platform. |
| What happens when a build fails — who is paged, does it retry, is there a backfill ritual? | Failure handling is a procedure someone chose. It is invisible in config and it determines whether a late model is an emergency. |
| Does anything run *outside* that tool — a manual script, a periodic backfill, a reverse-ETL job? | The orchestrator cannot report what does not run in it, and this class is where surprise consumers live. |
| Which of these jobs must not be allowed to fail? | Criticality is a business ranking. Every job in a list looks equally important. |

Record the answers where they will not rot: `orchestrator.type` and `orchestrator.config_path` in the contract, and the procedural half — failure handling, the backfill ritual, what runs outside the orchestrator — in `context.mechanisms`. **Do not record the cadences you were just told.** A cron you transcribed is a cron that will be wrong within a quarter and will still read as authoritative; the tool that owns it is one call away. Record *which tool to ask*, not its current answer.

---

## 6. BI consumer discovery

The DAG's consumer list is systematically incomplete, and the direction of the error is always the dangerous one: it undercounts. Three sources, in decreasing reliability.

**From the contract.** For each entry in `bi.consumers`, grep its `repo_path` for the relation name. BI repositories reference warehouse *tables*, not dbt model names — usually identical, but check for an `alias` config before treating a clean search as a clean result:

```bash
grep -rn "alias" models/ --include=*.yml --include=*.sql | grep -v 'aliases' | head
# then, per contract entry:
grep -rn "<table_name>" <bi_repo_path>/
```

**From exposures.** Already enumerated in step 4. Exposures are the in-repo, DAG-visible record of external consumption, and their coverage is only as good as the discipline that maintained them. An exposure with an empty description gives you the link and not the criticality — the link is derivable, the importance is a question.

**From the query log.** The same query as step 4, aggregated the other way: not "is anything reading this model" but "which service accounts and users read this project's tables at all." That gives you the consumer classes nobody wrote down, and it is the only instrument that sees them.

```sql
select <user_column>, count(*) as query_count
from <query_history_relation>
where <query_text_column> ilike '%<prod_schema>%'
  and <start_time_column> >= current_date - 7
group by 1
order by query_count desc
limit 25
```

**Before trusting any tool's empty answer here, prove it can see the class you asked about.** A catalog or lineage tool integrated with one BI product and blind to another returns the same empty list for both, in the same confident language. Query it for *any* asset of that type first: zero assets means the instrument is blind and its negative is worth nothing. The technique is in `dbt-gathering-context`; this is where it is most often skipped, and reporting "no BI impact" on the strength of a blind tool is how a dashboard breaks the morning after a merge.

Report BI coverage as a *scope*, never as an absolute: "no references in the two repositories the contract lists, and no reads in the 7-day query log" is a claim you can defend. "No BI consumers" is not.

---

## 6b. Map the business behind the models

Everything so far reads the repository, the manifest and the logs — so everything so far describes the project's *shape*. None of it tells you what the data is **about**: which operational systems feed this warehouse and what each is the system of record for, that the CRM's "customer" is a signed contract while the product database's is a login, that two sources describe overlapping but non-identical populations, or which of `fct_revenue` and `fct_bookings` Finance actually closes the books on. That is the layer where an agent's errors get expensive, because the SQL compiles, the tests pass, and the number is wrong in a way only someone who knows the business can see.

Three things to build, each making the next cheaper: a **source-system inventory** (what feeds this, what each system is authoritative for, what is being migrated), an **entity map** (the business nouns, which datasets represent each, which one wins when they disagree), and the **join fabric** (how entities link across systems, on which keys, at what match rate, and what unmatched rows mean). Then per central mart: what decision it drives and who breaks if it is wrong.

Almost none of this is derivable. The source list is in the repo; what a source is *for* is not. The join key is measurable; whether the two populations are supposed to match is a business fact. So this pass is mostly **structured asking**, which is why it belongs on arrival where the questions can be batched — and why the questions must carry what you already measured. Sampling actual values is one instrument here rather than the goal: it checks the map and surfaces the vocabulary nobody documented, but no volume of row counts will tell you which system is the system of record.

The question sets, the sampling queries that reveal meaning rather than metadata, and the recording rules are in [`mapping-the-business.md`](./mapping-the-business.md). What you learn goes in `context.domain_notes`; `dbt-deriving-project-context` owns that artifact and the rule that keeps it honest — record interpretation, never measurement, and leave what nobody confirmed visibly empty rather than filling it with a plausible guess.

---

## 7. Test coverage map

You need this before you change anything, because coverage tells you what your build will and will not catch. A green build on an untested model is not evidence of anything.

`${CLAUDE_SKILL_DIR}/survey-project.sh` computes this from the same manifest — it prints the model count, the untested count and percentage, and the untested models grouped by folder. It is another fixed parse with nothing to adapt. Read its coverage section this way:

Two things to read out of it, and one not to.

**Read the concentration, not the average.** A project at 80% coverage with the missing 20% spread evenly is healthy. The same number with every untested model in one mart folder means one area is unguarded, and if your task is in that folder your build will pass no matter what you break.

**Read coverage against the *shape*.** Cross the untested list against the child counts from step 2. An untested model with thirty children is the single highest-risk object in the project; an untested leaf is nearly harmless. The intersection of those two lists is the most useful sentence in your onboarding report.

**Do not read low coverage as a task.** Adding tests to a project you arrived at twenty minutes ago is out of scope, generates a large diff nobody asked for, and will surface pre-existing failures that are now attributed to you. Report the gap. `dbt-authoring-schema-yaml` covers the tests themselves when someone asks for them.

---

## 8. Activity and staleness

Git tells you which parts of the codebase are alive. This is what separates "this model looks wrong" from "this model has been deliberately untouched for two years."

```bash
# Most recently modified models -- where the team is actually working
git log --since='90 days ago' --name-only --pretty=format: -- models/ \
  | grep '\.sql$' | sort | uniq -c | sort -rn | head -20

# Age distribution of last modification, by year
git ls-files models/ | grep '\.sql$' | while read -r f; do
  git log -1 --format=%ad --date=format:%Y -- "$f"
done | sort | uniq -c
```

```bash
# For one model: when it was created, when it last changed, how often
git log --diff-filter=A --format='added   %ad by %an' --date=short -- <path> | tail -1
git log -1 --format='last    %ad by %an' --date=short -- <path>
git log --oneline -- <path> | wc -l
```

Three inferences this licenses, and they change how you behave:

- **A hot area** — many commits in ninety days — has current owners, current conventions, and a real chance someone else is editing it right now. Coordinate, and match what the recent commits do rather than what the oldest files do.
- **A cold area** — untouched for years but built daily — is load-bearing and unowned. This is the dangerous quadrant. Nobody will review your change well because nobody remembers it, and the tests are whatever was normal when it was written.
- **A single-commit model** never revised since creation is either perfect or unexamined. Check whether anything downstream consumes it before assuming the former.

Two limits worth stating rather than discovering. `git log` on a repository cloned with `--depth` has no history to report, and it will say so by returning nothing rather than by failing — check `git rev-parse --is-shallow-repository` before trusting an empty answer. And a bulk reformat or a directory move rewrites every date in one commit; if the age distribution is implausibly uniform, look for that commit before drawing conclusions from it.

Git names who *typed*, never who is *accountable*. Check `CODEOWNERS` and any `meta.owner` in YAML for the recorded answer, and if neither exists, ownership is a question — see `dbt-gathering-context`.

---

## 9. Grandfathered versus current patterns

The most expensive thing a new agent does is mistake history for error. Projects accumulate layers of practice, and the older layer is frequently *deliberate*: preserved because changing it breaks consumers, because the numbers were reconciled against it, or because a migration was costed and declined.

Detect the seam by dating the pattern rather than judging it:

```bash
# For a pattern you think is wrong -- when was the newest file that uses it added?
for f in $(grep -rl '<pattern>' models/ --include=*.sql); do
  git log --diff-filter=A --format='%ad' --date=short -1 -- "$f"
done | sort | tail -3

# ...and the newest file that uses the pattern you think is current
for f in $(grep -rl '<current_pattern>' models/ --include=*.sql); do
  git log --diff-filter=A --format='%ad' --date=short -1 -- "$f"
done | sort | tail -3
```

A pattern whose newest instance is two years old, next to a pattern whose newest instance is last week, is a **migration boundary**. Read it as a boundary, not as a bug: the old side is grandfathered, and the rule is that new work uses the current pattern while existing files keep theirs.

The signals that a deviation is deliberate rather than accidental:

- It is **widespread**. Thirty percent of models sharing a "deviation" means the convention is stale, not that 200 files are wrong. That principle belongs to `dbt-project-conventions` and applies here unchanged.
- It is **clustered by age**, cleanly separated by a date.
- It is **commented**, or explained in the PR that introduced it. Read the PR body before deciding.
- It is **load-bearing** — a column name a BI tool depends on, a grain a reconciliation was performed against, a materialization chosen for a cost reason nobody wrote down.

**Finding a deviation is not licence to fix it.** The rule is absolute: report it, attribute it to its era, and change it only when someone asks. An agent that arrives and normalizes the old layer produces a diff of hundreds of files, a blast radius nobody sized, and a reviewer who now distrusts everything else in the change. If a deviation genuinely blocks the task at hand, say which one and why, and ask.

---

## 10. What not to do on arrival

Every item here is something an agent does out of helpfulness, and every one of them costs more than it returns.

- **Do not reformat.** Not whitespace, not keyword case, not CTE ordering, not YAML key order. A formatting diff buries the substantive change and makes `git blame` useless for everyone afterwards.
- **Do not rename anything.** A rename is a breaking change with a blast radius you have not measured yet, spent on something no consumer can perceive.
- **Do not "clean up."** Not the model you noticed on the way past, not the unused CTE, not the commented-out block that is probably somebody's rollback path.
- **Do not add tests, descriptions, or tags nobody asked for.** They surface pre-existing failures attributed to your change, and they enlarge a diff whose reviewability is the only thing keeping the real edit safe.
- **Do not delete a model on the strength of the step-4 candidate list.** The list is evidence for a conversation.
- **Do not open with a critique of the project's conventions.** An unsolicited audit as a first act reads as posturing, is usually half wrong because you have twenty minutes of context, and spends credibility you need for the one real finding later.
- **Do not run anything destructive or unbounded to learn the shape.** A full refresh, a `+` selector on a staging model, a drop — none of these are exploration. `dbt ls` answers every structural question and executes nothing.
- **Do not narrate the measurement.** Report the facts, not the commands you ran to get them. A wall of exploration output buries the two numbers that mattered.

Onboarding output is a short brief, not a report: node counts, the version and adapter, the highest-fan-out models, what schedules this project, what consumes it and how confidently you know, where coverage is thin, and any stated gap. Then the task.

**Not every arrival needs every section.** Steps 1–8 are the graph and its guarantees — cheap, mechanical, and worth running whenever you arrive cold. The business map in 6b is different in kind: it is mostly asking people, its answers change on the timescale of reorganizations rather than commits, and it is written down once and then read. Run it when the project has no `context.domain_notes`, when what is there is stale enough to mislead, or when the task turns on business meaning — a metric definition, a cross-system join, which of two similar marts is canonical. Do not re-interview the team on a Tuesday to fix a typo. Where the artifact already exists, reading it *is* this step.

## Completion checklist

- [ ] Contract read; absent fields named, with the specific degradation stated rather than guessed around
- [ ] `dbt --version` run; core version, adapter, and `require-dbt-version` all recorded, and any disagreement between them resolved
- [ ] Behavior-change `flags:` block read before assuming any version-dependent default
- [ ] `dbt parse` run before reading the manifest, so the graph measured is the graph on disk
- [ ] Node counts captured by resource type and by layer
- [ ] Child counts per model computed; the highest-fan-out models named as the dangerous ones
- [ ] Transitive descendant count checked for any model the task will touch, not just direct children
- [ ] Sources enumerated; models reading a source directly identified
- [ ] Zero-child models split against `layers[].terminal` rather than reported as one list
- [ ] Dead-model candidates required all three tests — no children, no exposure, no reads — with the evidence attached and no deletion proposed
- [ ] Query-log window and retention stated, so a quiet log is not read as an absent consumer
- [ ] Orchestrator located, or its absence stated as a question; selectors resolved against the DAG rather than inferred from tags
- [ ] Platform jobs enumerated with their *enabled* state, not just their names; every selector resolved and unioned
- [ ] CI workflows read and classified by purpose, not merely listed; generated artifacts identified as do-not-hand-edit
- [ ] `.pre-commit-config.yaml` checked, so hooks are known before a commit is rejected rather than after
- [ ] Orchestration questions no config answers — failure handling, what runs outside the orchestrator, which jobs must not fail — asked in one batch, carrying the selectors already measured
- [ ] Cadences not transcribed into any file; the tool that owns them recorded instead
- [ ] Inherited `schedules.default_tag` not mistaken for a per-model declaration
- [ ] BI consumers traced from the contract, exposures, and the query log; instrument coverage proven before any negative was reported
- [ ] BI findings reported as a scope, never as "no consumers"
- [ ] Source systems inventoried, each with what it is the system of record for, and any migration-in-progress named
- [ ] Core entities defined in business terms, with the authoritative dataset per entity and the trap a newcomer falls into
- [ ] Cross-system links recorded with cardinality and whether a sub-100% match rate is expected — before any join was written
- [ ] Purpose established for the central marts: what decision each drives, and which similar model it must not be confused with
- [ ] Data sampled against **production** with explicit database and schema, using compiled relation names rather than filenames
- [ ] Interpretation recorded rather than measurement; nothing confirmed by nobody left as a plausible guess, and no real data values written to any file
- [ ] Untested models counted, and the count crossed against child counts to locate real risk
- [ ] Activity measured from git; shallow-clone and bulk-reformat caveats checked before trusting dates
- [ ] Ownership taken from `CODEOWNERS` or `meta`, not from the last committer
- [ ] Any pattern deviation dated and attributed to its era, not flagged as an error
- [ ] Nothing reformatted, renamed, cleaned up, deleted, or tested that the task did not ask for
- [ ] Brief reported as facts and numbers, with the derivation not narrated

## Common failure modes

1. **Editing a model believed to be a leaf.** The defining failure of a cold start. It compiles, it builds, the tests pass, and every consumer now reports different numbers. One manifest pass for child counts prevents it, and nothing else does.
2. **Reading a terminal model as an abandoned one.** A report mart designed to have zero children looks exactly like a dead model. Without `layers[].terminal` the two are indistinguishable, so the honest move is to ask rather than to classify.
3. **Calling a model dead on one test instead of three.** No `ref()` flags every terminal model. No exposure flags every project that never adopted exposures. A quiet query log flags every consumer that runs monthly. Each alone is a false-positive generator.
4. **Inferring the schedule from tags.** Tags are intent. The job that honored a tag may have been disabled two quarters ago, and the model is now stale on a cadence nobody is watching.
5. **Re-deriving the dbt version in every skill that gates on it.** Establish it once on arrival. The cost of not doing so is not the extra command — it is recommending a feature the project cannot parse, on a version you assumed.
6. **Assuming a version-gated YAML key fails loudly.** Older versions frequently *ignore* what they do not understand. The parse succeeds, the feature does nothing, and you report coverage that does not exist.
7. **Trusting an empty answer from a lineage or catalog tool.** A blind integration and a genuine absence return the same well-formed empty list, in the same confident language. Establish that the tool indexes the asset class before its negative means anything.
8. **Reporting "no BI consumers" instead of a scope.** The first is a claim you cannot defend and will be believed anyway. Name the repositories searched and the window queried.
9. **Measuring coverage as an average.** 80% coverage with the entire gap in one folder is not 80% healthy. The concentration is the finding; the average hides it.
10. **Treating low coverage as an invitation.** Adding tests on arrival produces an unrequested diff and surfaces pre-existing failures that will be attributed to your change.
11. **Reading git dates from a shallow clone or across a bulk reformat.** Both produce plausible, uniform, wrong answers — and the shallow case returns nothing rather than erroring.
12. **Confusing a cold model with an unimportant one.** Untouched for two years and built every night is the highest-risk quadrant in the project, not the lowest.
13. **"Fixing" a grandfathered pattern.** It was preserved for a reason that predates you, the diff spans hundreds of files, and the blast radius was never sized. Report the seam; change one side of it only when asked.
14. **Opening with an audit of the project's conventions.** Twenty minutes of context is not enough to be right, and being wrong in the first message costs the credibility you need for the finding that matters.
15. **Narrating the exploration.** Fifteen commands and their output, with the two numbers that mattered somewhere in the middle. Report the facts.
