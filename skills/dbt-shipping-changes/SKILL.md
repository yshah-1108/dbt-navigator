---
name: dbt-shipping-changes
description: Use when preparing a branch, writing a commit or PR description, designing CI, or merging a dbt change. Covers PR evidence rather than claims, CI design and cost control, linting and pre-commit, blue/green and canary deployment, data rollback — and what happens after merge, which scheduled jobs pick the change up, whether a backfill is needed and in what order, cross-frequency lag, and production verification.
metadata:
  phase: ship
---

# Shipping a change

Most guidance on shipping stops at merge. Merge is the midpoint. The interval between merge and the first successful scheduled production run is where a change either lands correctly or quietly does not, and it is almost always unaccounted for.

Four parts: getting the change reviewed on evidence, designing CI so it catches what matters without paying for what does not, choosing a deployment pattern, and knowing what production does with the change afterward. The last is the one usually missing.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | What it decides |
|---|---|
| `schedules.default_tag` | The globally-inherited tag — **must not** be added to individual models |
| `schedules.tags` | Which cadences exist and roughly when each runs |
| `bi.consumers` | BI repositories that may need a coordinated change |
| `environments.prod` | The relation to query when verifying in production |

**On `schedules`: the contract is a description, not the source of truth.** Crons change in the orchestrator, and a hand-maintained copy rots without any signal that it has. Use the contract to know which tags exist, then confirm actual timing from the orchestrator's API or UI before making a claim about when something runs. If you cannot reach the orchestrator, say the timing is from the contract and unverified. Never state a job name or a run time you have not seen.

---

## Part 1 — Getting it reviewed

### Branch

Never work on the default branch. Match the repository's existing branch naming — read `git branch -a` and follow it rather than imposing a convention.

One caution worth knowing: some orchestration tools handle slashes in branch names poorly, in job configuration or in the UI. If the project's existing branches all use a flat separator, that is likely why, and it is a reason to match rather than deviate.

### Commits

- One logical change per commit. A restructure, a rename, and a reformat are three commits even when they ship in one PR.
- The message says **why**, since the diff already says what.
- Never bundle a mechanical change with a substantive one. A reformat mixed into a logic commit makes the logic unreviewable.
- Do not commit on the user's behalf without being asked.

### PR description: evidence, not claims

The difference between a reviewable PR and an unreviewable one is whether the verification section contains **output**. "Validated in dev" is not verification; it is an assertion the reviewer must take on faith and cannot check.

```markdown
## What changed and why

<the change, and the reason. Link the ticket.>

- Models added / modified: <list>
- Downstream models affected: <list, or "none">
- Breaking: <yes/no — if yes, link to the coordination plan>

## Verification

### Build

<the actual dbt build output — model count, pass/fail, timing>

### Queries and results

<queries with explicit database and schema, never ref(), each with its result>

| Check | Dev | Prod | Delta |
|---|---|---|---|
| Row count | <n> | <n> | <n, explained> |
| <key metric> | <v> | <v> | <v, explained> |
| Duplicate keys | 0 | — | — |

## Post-merge actions

<the checklist from Part 2. Empty is a valid answer, but state it.>

## BI impact

<consumers checked, per bi.consumers, and what was found — or that the contract declares none>
```

Rules for the verification queries themselves:

1. **Explicit database and schema, never `ref()`.** `ref()` resolves per-environment; a query using it does not prove what the reviewer thinks it proves. This is one of the universal rules.
2. **Include the result, not just the query.** A query without output is not evidence.
3. **Explain every delta.** An unexplained difference between dev and production is either a finding or a gap in understanding. "Dev has one extra day of data" is an explanation; a bare number is not.
4. **Never state a count you did not run.** A number you did not query is a guess wearing a number's clothes.

Detailed comparison technique — `audit_helper`, row-level versus aggregate evidence — is in `dbt-verification` and `dbt-refactoring-safely`.

### What a reviewer of a data PR can and cannot check

A code reviewer can read logic and spot a bug. A data reviewer cannot verify a number without running it themselves, and will not. So the PR has to carry the evidence, and the evidence has to be of a kind the reviewer can judge without re-running anything.

| Evidence | What it lets a reviewer conclude | Limits |
|---|---|---|
| The diff | Whether the logic expresses the stated intent | Nothing about the data |
| Build output | It runs, tests pass, and roughly what it costs | Nothing about correctness |
| Row counts before and after | Whether the grain and volume are what was claimed | A matching count hides offsetting errors |
| Aggregate comparison per period | Whether totals moved where they should and stayed put where they should not | Aggregates cancel; two errors of opposite sign look clean |
| Row-level comparison on a key | Whether individual rows changed as intended | Expensive, and needs a sensible sample |
| A screenshot | Almost nothing verifiable | Cannot be re-run, queried, diffed, or trusted |

**Screenshots are the weakest evidence and the most commonly offered.** They are unqueryable, undated, uncheckable, and easy to produce from the wrong environment. Use a screenshot only for something inherently visual — a chart that renders wrong, a BI tool's field list — and even then pair it with the query. Anything numeric belongs in a table with the query that produced it.

The reviewer's actual questions are narrow, and a good PR answers all five before they are asked:

1. What did the numbers do, and is that what you expected?
2. What did you check that could have shown you were wrong?
3. What is downstream of this, and did you look?
4. What must happen after merge, and who does it?
5. If this is wrong in production, how does it get undone?

A PR that answers those is reviewable in ten minutes. One that asserts "tested and working" is not reviewable at all, and the review will either be a rubber stamp or a round-trip.

### Sizing the diff

Review quality falls off a cliff with size, and the cliff is not far along. A reviewer reads a 100-line diff and asks questions; the same reviewer approves a 1,000-line diff on the strength of the description. That approval carries no information, and the failure mode is not a bad review — it is a review that produced nothing while looking like a gate was passed.

Practical splits, in order of how much they help:

| Split | Why |
|---|---|
| Formatting or renaming from logic | A reformat makes a logic change invisible. This is the single highest-value split |
| Additive from destructive | Adding a column is safe and mergeable now. Dropping one needs coordination. Different PRs, different timelines |
| Per model, when models are independent | One can merge and be verified while another is still in question |
| Mechanical bulk edits from the change that motivated them | A 40-file update to a renamed reference reviews in a minute alone, and buries a real change if mixed in |

Some diffs are legitimately large — a generated file, a project-wide mechanical rename, a new model that is simply long. Say so in the description, and say which parts are mechanical and which need attention. A reviewer told "files 1–38 are the same one-line substitution, file 39 is the actual change" can do a real review of a large diff. Left to guess, they cannot.

The one thing not to do is split a change so it merges in a non-working state. Sequenced PRs are fine; a PR that leaves the default branch broken between merges is not. Where sequencing is required, write the order into both descriptions, and prefer additive-then-migrate-then-remove over anything that has a broken interval.

### Before requesting review

- [ ] Compiles: `dbt compile --select <models>`
- [ ] Builds with tests: `dbt build --select <models>+`
- [ ] Linter passes, if the project has one configured
- [ ] `git diff` shows only intended files — no stray edits, no debug filters left in
- [ ] No temporary date filter or hardcoded dev limit remaining in the SQL
- [ ] YAML documentation exists for new models and columns
- [ ] The globally-inherited schedule tag was not added to any individual model

---

## Part 2 — CI design

CI for dbt has a cost that CI for application code does not: every run touches a warehouse and bills for it. That constraint drives the design. The goal is not maximum coverage on every push — it is catching the failures that matter, in the cheapest place that can catch them.

### Split by cost and by what each stage can prove

| Stage | Runs | Cost | Catches |
|---|---|---|---|
| Pre-commit hook | On commit, locally | Free | Formatting, lint, missing documentation, obvious mistakes |
| PR check, no warehouse | On every push | Near-free | Parse errors, project-structure violations, lint |
| PR check, warehouse | On every push, or on request | Real | Compile against real schemas, build and test the changed subset |
| Merge / deploy | On merge to default | Real | The full DAG, or the promotion step |
| Scheduled | On cadence | Real | Freshness, and everything CI's narrow selection skipped |

Two principles worth stating because they are routinely violated in both directions:

- **Never move a check later than the cheapest stage that can catch it.** Linting in a warehouse-connected job wastes money on something a hook catches for free.
- **Never rely on a stage that cannot actually prove the thing.** A parse-only check does not prove SQL is valid; it proves the Jinja renders. See `dbt-command-reference` for what each command does and does not establish.

### PR versus merge

A PR check should build **only what changed and what depends on it**, deferring everything else to production — the Slim CI pattern. The mechanics of `state:modified`, `--defer`, artifact requirements and the pitfalls are in `dbt-command-reference`; what matters here is the shape:

```bash
# on a PR: build the changed subset, defer the rest to production
dbt build --select state:modified+ --defer --state <path/to/production/artifacts>
```

The two ways this goes wrong are worth naming:

- **The manifest is stale or wrong.** If the artifacts do not come from the current production state, `state:modified` selects the wrong set — too much, or worse, too little. A CI job that silently selects nothing passes in seconds and proves nothing. Fail the job when the selection is empty and unexpected.
- **CI builds nothing because everything deferred.** Deferral is what makes Slim CI affordable and also what lets an unbuilt model read production data without saying so. When CI passes suspiciously fast, check what it actually built.

On merge, the reasonable options are a full build, or a build of the changed subset followed by the normal schedule. A full build on every merge is the safest and the most expensive; on a large project it is usually reserved for a nightly job.

Two things belong on merge or on a schedule rather than on a PR, because a PR cannot do them meaningfully:

- **Source freshness.** It reflects the state of the pipeline, not the state of the branch. See `dbt-sources-and-seeds`.
- **Long-running full refreshes.** If a change requires one, that is a post-merge action with a plan, not a CI step.

### Cost control

| Technique | Effect | Cross-reference |
|---|---|---|
| Build only modified nodes and descendants | The single largest saving available | `dbt-command-reference` |
| Defer unmodified nodes to production | Avoids rebuilding parents to test a child | `dbt-command-reference` |
| Build with no rows to validate schema and SQL | Catches invalid SQL at near-zero warehouse cost | `dbt-command-reference` |
| Clone production tables into the CI schema | Lets incremental models run incrementally in CI instead of full-refreshing | `dbt-command-reference` |
| Stop at the first failure | Avoids paying for the rest of a doomed run | `dbt-command-reference` |
| A sampled or filtered development environment | Bounds cost for every developer, every run | `dbt-environments` |
| Drop CI schemas after the PR closes | Prevents an accumulating pile of abandoned relations | — |

The last one is a real cost that hides: CI schemas are cheap individually and expensive in aggregate after a year of pull requests. Some platforms clean up automatically; verify rather than assume, and if nothing does, schedule it.

Two cost traps specific to dbt CI:

- **Incremental models in CI full-refresh by default**, because the target relation does not exist in a fresh CI schema. On a large incremental model that is the most expensive thing in the pipeline, paid on every push. Cloning production tables into the CI schema first is the standard fix.
- **`state:modified` on a widely-referenced upstream model selects most of the project.** A one-line change to a base staging model can trigger a near-full build. That is correct behaviour and worth knowing before someone concludes CI is broken.

### Linting and formatting

Two distinct tools, routinely conflated:

| Tool | What it is | Notes |
|---|---|---|
| SQLFluff | A configurable linter **and** formatter — finds rule violations, can fix some | Needs configuration and a house style. Slower |
| sqlfmt | An opinionated formatter only | Not configurable by design, which is the point: no style debates. Does not lint |

For a dbt project, SQLFluff's templater choice is the decision that matters:

- The `jinja` templater is the default and does not need a database. Faster, and it does not understand dbt macros unless configured to approximate them.
- The `dbt` templater renders through dbt itself, so it lints what the warehouse will actually receive. It requires the adapter and the `sqlfluff-templater-dbt` package, is meaningfully slower, and if any model runs an introspective query at compile time it will need database access.

The documented guidance follows from that: `dbt` templater where accuracy matters and speed does not (CI), `jinja` templater where response time matters (editor, commit hook). One configuration gotcha that costs an afternoon: the templater can only be set in the config file in the directory SQLFluff is invoked from, not in a subdirectory — and pre-commit always runs from the repository root, so a config file inside a nested project directory is silently ignored and the `jinja` templater is used instead. The symptom is a lint pass that disagrees with CI.

Whatever the choice, two rules:

1. **Adopt formatting in its own commit.** A repository-wide reformat mixed with a logic change destroys reviewability, and it is the most common way a real bug ships unseen.
2. **Match the project's existing configuration rather than introducing your own.** If a config file exists, it is the house style whether or not you would have chosen it.

### Pre-commit hooks

Hooks are the cheapest stage, so put everything there that can live there: formatting, linting, YAML validity, trailing whitespace, large-file guards, and dbt-specific structural checks — a model has a description, a model has at least one test, SQL uses `ref()` rather than a hardcoded relation.

Ecosystem note, because the naming has changed: the widely-used dbt hook collection was renamed, with the older name now unmaintained. Check which one a project has pinned and what the current maintained package is before adding hooks, rather than copying a snippet from an old post.

Three cautions:

- **A hook that needs a warehouse connection is not a hook.** Anything that compiles or queries belongs in CI. A pre-commit that takes two minutes gets bypassed, and a bypassed hook is worse than no hook because everyone believes it ran.
- **Hooks are local and skippable.** They are a convenience for the author, never a guarantee for the repository. Anything that must be true has to be enforced in CI as well.
- **Introduce hooks to an existing repository incrementally.** Turning on a strict set at once produces a first run that fails on hundreds of pre-existing files, and the usual response is to disable the whole thing.

---

## Part 3 — Deployment patterns

Most projects deploy by running the models in place: the scheduled job overwrites production tables, and consumers read whatever exists at the moment they query. That is fine for most work, and it has two properties worth naming, because the patterns below exist to fix exactly these:

- **Consumers can read a half-built state.** Between the first model completing and the last, production is internally inconsistent. A dashboard refreshing mid-run sees a new fact table joined to an old dimension.
- **Tests run after publishing.** A `dbt build` tests each model after building it, so a failing test reports a problem that consumers can already see. The failure is a notification, not a gate.

### Blue/green, or write-audit-publish

The fix for both is to build somewhere consumers are not looking, test there, and promote only on success. The pattern is old and has two names — blue/green from application deployment, write-audit-publish from data engineering. Same idea.

| Step | What happens |
|---|---|
| Write | Build the full set into a staging location consumers do not read |
| Audit | Run the tests against staging. Consumers are still on the previous good state |
| Publish | Promote staging to production in one operation |

What "promote" means depends on the platform, and this is where the pattern becomes adapter-dependent:

| Mechanism | Requires | Notes |
|---|---|---|
| Zero-copy clone from staging to production | A platform with zero-copy cloning of tables | `dbt clone` implements this; on platforms without cloning it falls back to pointer views |
| Atomic swap of two databases or schemas | Platform support for a swap or rename primitive | Genuinely atomic where the platform provides it. Swaps privileges too on some platforms — check |
| Repointing a view layer | Nothing special | Consumers read views; publishing means redefining them to point at the new tables |

Verify the primitive on the actual platform before designing around it. "Swap" and "clone" are not universal, their semantics differ, and a plan that assumes an atomic swap on a platform offering only sequential renames has a window where production is broken.

The costs, stated plainly, because this pattern is often adopted without them:

- **Roughly double the storage**, unless the promotion is a zero-copy clone. Even then, the previous version retained for rollback occupies space.
- **A full build every time.** The staging environment starts without the incremental history unless it is cloned in first, so the pattern interacts awkwardly with large incremental models. Cloning production in before building is the usual answer, and it adds a step that can fail.
- **A promotion step that can fail on its own.** A failed swap can leave the environments in a state nobody designed for. It needs to be idempotent and logged. See `dbt-macros` for the safety properties an operational macro needs.
- **Hardcoded references break.** Anything outside dbt that names a database or schema explicitly — a BI connection, an external query, a reverse-ETL job — may follow the swap or may not, depending on whether it resolves the name at query time. Enumerate those consumers before the first swap, not after.

Use it where consumers genuinely cannot tolerate an inconsistent read or an untested publish. Skip it where a failing test caught twenty minutes later is an acceptable outcome, which is most projects. The pattern's cost is real and its benefit is narrow.

### Canary, and what it means for data

Canary deployment in application terms means routing a fraction of traffic to the new version. Data has no traffic to split, so the analogue is different and worth stating precisely: build the new version alongside the old, point **one** consumer or one query at it, compare, then cut everyone over.

That works when there is a consumer willing to be first and a comparison worth making. It does not work as a percentage — you cannot give 10% of a table's rows the new logic and call the result either version.

The practical form is a parallel model rather than a routed one: the new definition lands under a new name, runs on the same schedule, and gets compared against the old for a few cycles before the old one is retired. It costs double compute for the overlap period and it is the only way to observe a change's behaviour over real time — across a month boundary, a late-arriving batch, a source outage — none of which a single verification run can show. See `dbt-refactoring-safely` for the comparison technique.

### Rollback is not the same problem as in application code

Reverting application code restores previous behaviour completely, because behaviour is the code. Reverting a dbt model restores the previous **definition**, and the data stays exactly as the bad version left it. Every rollback for data is therefore two decisions: revert the code, and separately repair what it wrote.

| What the change did | Code revert alone is sufficient? |
|---|---|
| Nothing yet — not built since merge | Yes |
| Rebuilt a view | Yes. The next query uses the reverted definition |
| Rebuilt a table in full | Yes, once it rebuilds — assuming the source can still reproduce it |
| Appended or merged rows into an incremental model | **No.** The bad rows persist. Delete the affected range and rebuild it |
| Full-refreshed a model whose source cannot reproduce history | **No, and there may be no repair at all.** Restore from a backup or time travel if the window has not passed |
| Dropped a relation | **No.** The object is gone; restoring depends entirely on platform recovery features |

Three things make a data rollback survivable, and all three are decided before the merge:

1. **Know whether the model is reproducible from its source.** This single fact determines whether a mistake is an inconvenience or a permanent loss.
2. **Know the platform's recovery window.** Time travel and undrop features have retention limits and configuration. A window you have not verified is not a plan.
3. **Take a copy before anything destructive.** The cheapest insurance available, and skipped precisely when it matters most.

Prefer forward-fixing to rolling back where the data is already wrong: a corrected version and a targeted rebuild of the affected range usually reaches a correct state faster than a revert plus a repair, and it leaves a clearer history. Roll back when the change is actively producing more damage each run.

---

## Part 4 — What happens after merge

Merging changes the code. It does not change the data. Production data changes when a scheduled job next runs the model — and that run may be hours away, may not include your model, or may fail because of your change.

### Which jobs pick the change up

Determine, for each changed model:

1. **Its effective schedule.** An explicit tag in the model config wins. Otherwise it inherits the project-level default. A model in a layer with no scheduled job of its own is built only as a dependency of something that does have one.
2. **Which job selects it.** Read from the orchestrator, not from the contract. A model can be selected by tag, by path, by an explicit list, or transitively via `+`. Only the orchestrator knows.
3. **When that job next runs.** From the orchestrator. State the source of the time.

If the orchestrator cannot be reached, say so and describe the change's requirements in terms of tags — "this model carries the hourly tag, so it will be picked up by whichever job selects that tag" — rather than inventing a job name or a time.

### Classify the change by post-merge risk

| Class | What it is | Post-merge action |
|---|---|---|
| **1 — no-op** | Documentation, tests, or YAML only | None. No data changes. |
| **2 — auto** | Modified view; modified incremental with no schema change and no history correction | None. The next scheduled run applies it. |
| **3 — first run** | New model | Verify the first run succeeds. A new incremental model's first run builds all history — estimate that volume before merging. |
| **4 — schema** | Column added, removed, renamed, or retyped on an incremental model | Depends entirely on `on_schema_change`. May require a full refresh **before** the next scheduled run, or the run fails. |
| **5 — backfill** | A logic fix that corrupted or omitted historical rows | A targeted rebuild of the affected range. Never automatic. |
| **6 — irreversible** | Full refresh on a model whose source cannot reproduce history, or a warehouse object drop | **Stop.** History is lost and cannot be recovered. Requires an explicit human decision, named in the request. |

Class 4 is the most commonly missed. An incremental model configured to fail on schema change will **fail its next scheduled run** after your merge if you did not refresh it first — so the change lands as a broken production job rather than as new data. Configured permissively instead, it succeeds and leaves the new column null for all history, which is worse because nothing signals it. See `dbt-adding-columns`.

Class 6 deserves its own gate. Any operation that destroys history — a full refresh on a model marked as non-refreshable, or dropping a table that is the only copy of something — must be named explicitly by the person requesting it. Do not perform it on inference from an ambiguous request.

### Backfill: whether, and in what order

A backfill is required when history is wrong or incomplete, not merely when the code changed. Ask: *would a consumer reading last month's rows get the wrong answer?* If no, no backfill.

**Order matters, and the default is backfill after merge.** Running before merge means the production code that generated the backfilled rows is not the code in the repository — the data cannot be reproduced, and the next scheduled run may overwrite it with the old logic. Backfill after merge, and before the next scheduled run if the two would conflict.

For multi-model backfills, go in **DAG order, upstream first**, verifying each before starting the next. Backfilling a mart from an upstream model that has not yet been corrected propagates the bad data with more confidence attached to it.

Other constraints to state before starting:

- **Range.** Specific dates, derived from when the bug was introduced. "Rebuild everything" is rarely the right scope and is often expensive enough to matter.
- **Size.** A backfill that will not finish inside the window before the next scheduled run needs to be chunked, or the two collide.
- **Refreshability.** If the model cannot be full-refreshed because its source cannot reproduce history, a targeted delete-and-reinsert of the affected range is the only safe route — and that is a Class 6 operation.
- **Downstream.** Correcting an upstream model does not correct the marts built from it. List them.

For the mechanics — chunking a range that will not finish in one window, choosing between `--full-refresh` and a targeted delete-and-reinsert, and verifying each chunk before starting the next — see [backfilling.md](backfilling.md).

### Cross-frequency lag

The most under-appreciated post-merge issue, because it produces no error at all.

When a model on a frequent cadence depends on one that refreshes less often, or simply later in the day, the frequent model consumes whatever version of its parent currently exists. Concretely: a model running every hour that depends on a model refreshed once daily at midday reads yesterday's data for every run before midday. That is by design, and consumers reading the hourly model will see values that lag.

Check this whenever you change a model's schedule tag, add a dependency across cadences, or add a new model that something more frequent will reference:

```bash
# what depends on this model, and what cadence is each on
dbt list --select <model>+ --output name
# then inspect each dependent's tags
```

Two specific traps:

- **New model, existing downstream job.** If a downstream job fires before the new model has ever been built in production, it fails on a missing relation. Ensure the new model is built first — usually by triggering it manually right after merge.
- **A changed tag moves a model later in the day** than something that reads it. That is a new lag that did not exist before, and it will not raise an error. It will just make a number stale.

Say explicitly when a change introduces or worsens lag, and by roughly how much.

### Sequencing the merge itself

Some changes cannot be a single merge. When the work spans repositories or requires an action in a specific relation to the merge, write the sequence down before starting — including who performs each step.

| Situation | Sequence |
|---|---|
| New model that an existing frequent job will reference | Merge → build the new model manually → let the dependent job fire |
| Incremental schema change with a strict `on_schema_change` | Merge → full refresh → next scheduled run |
| Logic fix requiring a backfill | Merge → backfill the affected range → verify → next scheduled run |
| Column rename with a BI consumer | Merge the additive shim → migrate the BI repository → merge the shim removal |
| Change spanning two dbt projects or a package bump | Merge the upstream side first; the downstream side references it |

Two constraints worth stating explicitly in the PR:

- **Any action that must happen between the merge and the next scheduled run is time-boxed.** If that window is an hour and the action takes two, the run fails or produces wrong data. Check the window before choosing the sequence, and if it is too narrow, pause the job rather than racing it.
- **Whoever merges must know the post-merge actions.** A checklist that exists only in the author's head fails the moment someone else merges the PR. Put it in the description.

### What a merge does not do

Worth stating plainly, because each of these is assumed at some point:

- It does not build anything. No relation is created or updated by merging.
- It does not drop a deleted model's warehouse object. That object persists and serves stale data.
- It does not backfill, refresh, or correct any existing row.
- It does not update a BI tool's copy of the schema.
- It does not guarantee any job selects the changed model. Selection is orchestrator configuration, independent of the merge.

### Verify in production

The change is not shipped when it is merged. It is shipped when the first scheduled production run has completed and been checked.

1. **Watch the first run.** Not "assume it passed." Confirm it, and note the runtime — a large jump is itself a finding.
2. **Query production**, using the explicit production database and schema from `environments.prod`, never `ref()`.
3. **Compare against the dev numbers in the PR.** They should agree, allowing for the extra data production has. If they do not, that is a finding, not a rounding difference.
4. **Check the model's downstream dependents** ran, and ran after it rather than before.
5. **Check BI**, for anything with declared consumers. A BI break does not appear in a dbt run log; someone has to look.

### Rollback

Decide the rollback path **before** merging, because deciding it during an incident is how the wrong thing gets done. The general shape — and why reverting code does not revert data — is in Part 3. What belongs in the PR is the specific path for this change:

| Situation | Rollback |
|---|---|
| Code is wrong, data not yet rebuilt | Revert the commit. Clean. |
| Code is wrong, incremental has already appended bad rows | Revert, then delete the affected range, then rebuild it. The revert alone leaves bad rows in place. |
| Code is wrong, table was fully refreshed | Revert and full-refresh again — only if the source can still reproduce that history. |
| History was destroyed by a non-refreshable full refresh | **There is no rollback.** Restore from a warehouse time-travel or backup facility if one exists and the window has not passed. |
| A column was dropped that BI reads | Revert restores the column, but the BI consumer may have already been changed. Coordinate both directions. |

The last row generalises: **any change coordinated across two repositories has a rollback that must also be coordinated.** Reverting one side alone restores a mismatch rather than the previous working state, which is why the additive-then-migrate-then-remove sequence is worth the extra merges — each step is independently revertible.

One thing to write down explicitly for anything class 4 or higher: **how long the rollback takes.** A revert is a minute; a delete-and-rebuild of a month of an incremental model may be hours. If that number is longer than the tolerance of whoever reads the data, the plan needs a different shape — a parallel build rather than an in-place change.

## Completion checklist

- [ ] Branch created off the default branch, matching the repository's naming
- [ ] Commits separated by logical change; nothing mechanical bundled with anything substantive
- [ ] Diff sized for review, or the mechanical parts identified explicitly in the description
- [ ] PR contains verification **output**, not assertions — and not a screenshot standing in for a number
- [ ] Every query uses an explicit database and schema
- [ ] Every dev-versus-production delta explained
- [ ] The five reviewer questions answered before they are asked
- [ ] CI selection verified to have actually built something, not silently deferred everything
- [ ] Linter run with the project's existing configuration; any reformat in its own commit
- [ ] Change classified 1–6 by post-merge risk
- [ ] Effective schedule of each changed model determined from the orchestrator, or the uncertainty stated
- [ ] Full-refresh requirement identified for any incremental schema change, and sequenced before the next run
- [ ] Backfill decision made explicitly — including "none needed"
- [ ] Backfill ordered after merge and in DAG order, upstream first
- [ ] Cross-frequency lag checked for new or retagged dependencies
- [ ] Class 6 operations named explicitly by a human, never inferred
- [ ] Rollback path written down before merge, including how long it takes and whether the model is reproducible from its source
- [ ] Backup taken before anything destructive
- [ ] First production run watched and production queried after it
- [ ] BI consumers verified, per the contract

## The failure modes that actually happen

1. **Merged and considered done.** The next scheduled run fails on a schema change, or never selects the model. The change is in the repository and not in the data, and nobody notices until someone asks why a number did not move.
2. **Backfilled before merge.** The rows were produced by code that is not in the repository. The next scheduled run overwrites them with the old logic, and the correction silently disappears.
3. **Backfilled downstream-first.** The mart is rebuilt from an upstream model that is still wrong. The bad number now has a fresh timestamp and more apparent authority.
4. **Cross-frequency lag introduced silently.** A frequent model now reads a parent that refreshes later. No error, no failing test, just data that is a cycle behind and a consumer who cannot tell.
5. **Irreversible refresh from an ambiguous instruction.** A full refresh run against a model whose source cannot reproduce history, because the request said "rebuild it." That history is gone.
6. **Reverted the code, left the data.** The commit is reverted, the bad rows remain in the incremental table, and the model looks correct because the code now is.
7. **A job name or run time stated from a stale static file.** The contract said one thing, the orchestrator another, and the post-merge plan was built on the wrong timing.
8. **CI passed because it built nothing.** Stale artifacts meant the change-detection selector matched nothing, or everything deferred to production. The job was green in ninety seconds and proved not one thing about the change.
9. **A reformat shipped with a logic change.** The diff is a thousand lines of whitespace with one altered predicate in the middle. It was approved, and nobody read the predicate.
10. **A screenshot instead of a query.** The evidence cannot be re-run, its environment is unknown, and the number in it turns out to have come from a stale development build.
11. **A blue/green swap that broke everything naming the schema directly.** The dbt models followed the swap; the BI connections, external queries and reverse-ETL jobs did not, because nobody enumerated them first.
