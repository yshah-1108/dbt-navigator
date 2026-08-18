---
name: dbt-debugging-failures
description: Use when a dbt test fails, a scheduled job fails, a model will not compile, Jinja throws an error, a build times out or runs out of memory, a run hits a permission or connection failure, or the same code behaves differently in dev, CI and prod. Covers reading the real error, classifying the failure, bisecting the DAG to the first failing node, and separating a code problem from a data problem from an environment problem.
metadata:
  phase: diagnose
---

# Debugging failures

Something errored. That is the good case — an error is a signal with a location attached. The expensive mistakes here are not technical, they are diagnostic: guessing at the cause instead of reading it, and fixing the wrong class of problem.

Three classes, three different fixes:

| Class | Means | Fixed by |
|---|---|---|
| **Code** | The SQL or Jinja is wrong | Editing the model |
| **Data** | The code is correct; the input is not what it assumes | Fixing upstream, or making the assumption explicit |
| **Environment** | The code and data are fine somewhere, and this is not that somewhere | Building a dependency, fixing config, or correcting the target |

Conflating these wastes more time than any other debugging error. A data problem "fixed" in code becomes a permanent workaround for a transient upstream gap. An environment problem "fixed" in code breaks production. **Name the class before proposing a fix.**

If nothing errored but the numbers look wrong, this is the wrong skill — see `dbt-data-quality-triage`.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

Relevant fields: `environments.detection` (which environment am I in), `environments.dev` / `environments.prod` (database and schema for direct queries), `project.dbt_project_name` (path to compiled artifacts), `project.warehouse` (which query-history and metadata tools exist).

Absent field → generic guidance, labelled as generic. Do not guess at a database or schema name; get it from `dbt debug` or the profile instead.

## Step 0: read the actual error

Not the summary line dbt prints at the end. The actual error.

```bash
dbt build --select <model> 2>&1 | tee /tmp/dbt-error.log
```

Then read the full text, including the database's own message. dbt's wrapper is generic; the warehouse's message is specific and usually names the column, table, or type.

| What to look for | What it tells you |
|---|---|
| A column name | Almost always code, or an upstream schema change |
| A type or cast message | Code — usually a cast missing at staging |
| "does not exist" / "not found" | Environment — a dependency was never built here |
| "permission" / "access denied" | Environment — role or grant, never fixed in the model |
| A row count in a test failure | Data, until proven otherwise |
| A Jinja traceback | Code, and the line number in the traceback is real |

For more detail, `logs/dbt.log` holds the full compiled statement and the driver response. `--debug` adds the connection and macro-resolution trace, which is verbose but decisive for Jinja and package problems.

**Before proposing any cause, quote the error text.** If the error has not been read, no diagnosis is possible — only guessing.

### Anatomy of the message: which half is dbt's

A dbt error is two messages concatenated, and they have different authors. Splitting them is the single highest-value reading skill in this file, because engineers routinely search for dbt's generic half and find nothing.

```text
Completed with 1 error and 0 warnings:

Database Error in model orders (models/marts/orders.sql)     <- dbt: class, node, file
  001003 (42000): SQL compilation error:                     <- warehouse: code and class
  syntax error line 14 at position 4 unexpected 'from'.      <- warehouse: the actual cause
  compiled SQL at target/run/my_project/models/marts/orders.sql   <- dbt: where to look
```

| Part | Author | What it is good for |
|---|---|---|
| `<Class> Error in <resource> (<path>)` | dbt | Which stage failed and which file to open. Nothing about the cause |
| The indented body | The warehouse or Jinja | The cause. Line and column numbers here refer to the **compiled** SQL, not the model file |
| `compiled SQL at <path>` | dbt | The artifact to read and run. The line number above indexes into this file |
| `Completed with N errors` | dbt | A count, not a cause. The first error in execution order is the one to investigate |

Two traps this splits open. A line number in a `Database Error` almost never matches the model file, because Jinja expanded — count lines in the compiled file instead. And an error code such as `42000` or a driver-specific number belongs to the engine's own documentation; searching for it there beats searching for dbt's wrapper text.

## Step 1: classify the failure

dbt fails in stages, and the stage bounds where the cause can be. A parse failure cannot be caused by data; a database error cannot be caused by YAML indentation. Naming the stage eliminates most of the search space for free.

| Class | Distinguishing symptom | Cause lives in |
|---|---|---|
| **Initialisation / connection** | Fails before any node is listed; no `Found N models` line | Profile, credentials, network, or working directory |
| **Parse (YAML)** | Names a `.yml` file and a line; often "mapping values are not allowed" or an unexpected key | The YAML file |
| **Parse (Jinja)** | A traceback, an unclosed tag, an undefined name, or a Python type error | The model or the macro |
| **Parse (`ref`)** | "depends on a node named ... which was not found" | A missing, renamed, or disabled model |
| **Graph** | "Found a cycle", and the loop is printed | The `ref()` edges named in the loop |
| **Database — syntax** | Engine-specific syntax error, with a position | The compiled SQL |
| **Database — schema** | A named column or relation "does not exist" | Either a typo, or an upstream shape change |
| **Database — permission** | "insufficient privileges", "access denied", "not authorized" | Grants. Never the model |
| **Test failure** | "Got N results, configured to fail if" | The data, or the assertion. Nothing broke |
| **Timeout** | The statement was cancelled at a limit | Runtime, contention, or an unbounded scan |
| **Resource exhaustion / spill** | A memory or disk error — or *no error at all*, only a large slowdown | Query shape, then warehouse sizing |
| **Adapter / package** | An invalid config version, a missing dispatch package, a manifest schema mismatch | Package pins and dbt version |
| **Concurrency** | A lock or write conflict that does not reproduce when run alone | Scheduling or thread count |

Message fragments for each class, the cycle-fixing procedure, timeout and spill detail, and the classes that may never be fixed in a model are in [failure-taxonomy.md](failure-taxonomy.md).

One check that saves the most time: **the spill row has no error.** A model that succeeded but took ten times longer has failed in a way no log line reports, and it is only visible in query metadata.

## Step 2: did anything change?

This one question separates code from data faster than any other.

```bash
# Has the model or its ancestors changed since this last worked?
git log --oneline -10 -- models/path/to/model.sql
git log --oneline --since="<date of last success>" -- models/
```

- **Code changed, then it broke** → code problem. Start from the diff.
- **Nothing changed, and it broke** → data or environment. The SQL that ran yesterday is byte-identical; something it reads is not.

That second case is where engineers lose the most time, because the instinct is to re-read the model. The model is not the problem. Move to the input.

## Where the answers live

Each artifact answers a different question. Reaching for the wrong one is why a debugging session stalls.

| Source | Answers | Does not answer |
|---|---|---|
| `dbt debug` | Can dbt find the project and the profile, and open a connection? Which files is it using? | Anything about a model |
| `dbt debug --config-dir` | Where the profile file dbt is reading actually lives | Whether its contents are right |
| `dbt parse` | Is the project structurally valid — YAML, Jinja, `ref()` targets, the graph? | Whether any SQL is valid |
| `dbt ls --select +<model>` | Does the ancestor graph resolve, and in what order? | Whether the ancestors are built |
| `target/compiled/...` | What `select` statement your Jinja produced | What dbt wrapped around it |
| `target/run/...` | The full DDL/DML — the `create`, `merge`, or `insert`, including incremental predicates | The result |
| `logs/dbt.log` | Every statement dbt issued, including introspective queries and hooks, plus the driver's reply. Most recent at the bottom | Anything about a run whose logs were overwritten |
| `--log-level debug` (or `--debug`) | Connection setup, macro resolution, cache behaviour, the full statement stream to the console | Concise anything. Use it on one node |
| `target/run_results.json` | Every node's status, `thread_id`, timing, `rows_affected`, and the order things ran | Why — only what and when |
| `target/manifest.json` | The resolved configuration and dependencies of every node, including disabled ones | Runtime behaviour |
| The exit code | `0` success; `1` the run finished with at least one handled failure; `2` the run did not finish | Which node |

Two practical notes. `logs/dbt.log` appends across runs, so for a confusing database error, empty the file and re-run the single failing node — the log then contains only the relevant statements. And `--log-format json` makes the log machine-readable when you need to search a long run rather than read it.

Two claims that look identical and are not: **the exit code says whether the run finished, not whether it did anything.** A `0` on a run whose nodes were all skipped by a selector that matched nothing is a successful run that built nothing. Check the node count and the statuses, not the code.

## A failing test: is the test wrong, or the data wrong?

Ask this **first, always**. Everything else in this section depends on the answer, and the two have opposite fixes.

### 1. See the failing rows

```bash
dbt test --select <model> --store-failures
```

Then query the stored failures table directly, with explicit database and schema — not `ref()` (see `dbt-environments` for why). If `--store-failures` is not configured, run the compiled test SQL instead:

```bash
dbt compile --select <model>
# the test's compiled SQL lands under target/compiled/<project>/models/...
```

A dbt test is a query that returns failing rows. Run it and look at them. Everything after this is interpretation of actual rows, not speculation.

`--store-failures` writes them to a dedicated audit schema, which needs create permission on that schema — a frequent reason it works locally and not in automation. `store_failures_as` chooses a table or a view; a view re-evaluates on read, which is either convenient or misleading depending on whether the underlying data has since changed.

### 2. Read the test's configuration before reading the number

The count in a failure message means different things under different configuration, and interpreting it without checking is how a real defect gets dismissed or a non-defect gets escalated.

| Config present | What it does to the number |
|---|---|
| `where` | The test only looked at a subset. A pass proves nothing about the excluded rows |
| `error_if` / `warn_if` | The failure threshold is not zero, so a non-zero count may be a deliberate pass |
| `fail_calc` | The reported figure is a configured aggregate, not necessarily a row count |
| `limit` | The stored rows are truncated; the count is real but the sample is partial |
| `severity: warn` | A failure that does not stop the run and does not appear in the summary line |

A `where` clause on a test is the one to look for hardest. A uniqueness test scoped to a recent window passes happily while duplicates accumulate outside it — the test is green, the assertion is narrower than its name implies, and nothing in the run output says so.

### 3. Decide which is wrong

| Evidence | Reading |
|---|---|
| The failing rows are genuine, valid business records | **Test is wrong** — its assumption never held, or no longer holds |
| The failing rows are duplicates that should not exist | **Data or code** — check the grain and the join |
| The failing rows are all recent | Likely a data or upstream problem, not the test |
| The failing rows span all history and the test is new | Test is newly-added and its assumption was never true |
| A `not_null` failure on a column that is legitimately optional | Test is wrong |
| A `relationships` failure where the parent row was deleted upstream | Data — and the test is doing its job |
| An `accepted_values` failure with a new, real category | Test is stale — the value set needs extending, deliberately |

The test being wrong is a legitimate and common outcome. It is not a face-saving conclusion. But it must be **argued from the rows**, not assumed because fixing the test is easier.

### 4. Fix the right thing

- **Test wrong** → change the test to state the true invariant. Say in the summary what the old assertion claimed, why it was false, and what the new one claims. Never simply delete it.
- **Data wrong, upstream** → the model is correct. Report the upstream defect; do not paper over it in the model unless the user asks for a defensive fix, and if they do, comment why.
- **Data wrong, caused by this model** → a duplicate, a fan-out, a broken key. See `dbt-data-quality-triage` for the diagnostic shapes.

## A compile error

Compile errors are pure code, with one exception worth checking first.

```bash
dbt parse                       # project-wide: syntax, refs, config
dbt compile --select <model>    # this model and its dependencies
```

`parse` and `compile` are not interchangeable, and picking the wrong one wastes a cycle:

| | `dbt parse` | `dbt compile` |
|---|---|---|
| Scope | The whole project, always | Selectable |
| Renders Jinja | First pass only — enough to find `ref`, `source`, `config` | Fully, to final SQL text |
| Writes compiled SQL | No | Yes, to `target/compiled/` |
| Needs a warehouse connection | No | Sometimes — a macro that runs an introspective query will connect |
| Best for | "Is the project structurally sound?" after a broad edit | "What SQL did this model actually produce?" |

So: `parse` to answer whether anything in the project is broken, `compile` to answer what one model does. A project that parses can still fail to compile, and a model that compiles can still be rejected by the warehouse.

| Message | Cause |
|---|---|
| "depends on a node named ... which was not found" | A `ref()` names a model that does not exist — typo, or a renamed or deleted model |
| "Found a cycle" | Two models `ref()` each other, directly or through a chain |
| "Compilation Error in macro" | The macro, not the model. Compile a second model that uses it to confirm |
| "Model X depends on ... disabled" | The target is `enabled: false`, often per-environment — this is the exception, an environment problem wearing a code error's clothes |

`dbt ls --select +<model>` prints the resolved ancestor list and fails on a broken `ref()`, which is a fast way to confirm the graph is intact before looking at SQL.

## A Jinja error

Jinja fails at compile time, before the warehouse sees anything. The traceback line number refers to the **model file**, and it is accurate — read it.

Common causes:

1. **Undefined variable** — a `var()` or `env_var()` without a default, absent in this target. Environment problem, not code. Give it a default or set it.
2. **Jinja evaluated where SQL was intended** — a `{{ }}` inside a string literal, or a `%` in a `like` pattern colliding with a `{%` tag.
3. **Whitespace control removing a needed keyword** — `{%-` and `-%}` eat surrounding whitespace, which can concatenate two tokens. The compiled SQL shows this instantly.
4. **A macro called with the wrong arity or argument names** — the traceback names the macro; read its signature.
5. **Type confusion in Jinja** — everything from `var()` is a string until cast. `{% if var('x') %}` is true for the string `"false"`.

The single most useful move: **stop reading Jinja and read the SQL it produced.**

### Instrumenting Jinja

When the compiled SQL is wrong but it is not obvious which branch or value produced it, print from inside the template rather than reasoning about it.

```sql
-- writes to the log file only
{{ log("lookback resolved to: " ~ lookback_days) }}

-- writes to the log file AND the console
{{ log("lookback resolved to: " ~ lookback_days, info=True) }}

-- prints to stdout; suppressible with --no-print
{% do print("columns: " ~ column_list) %}
```

Two behaviours that mislead people who have not met them:

- **These fire during parsing, not only during execution.** dbt renders a first pass over every file to find `ref`, `source` and `config`, and logging runs in that pass — so a message can appear twice, or appear during `dbt ls` when nothing is being built. It is not evidence that your model ran.
- **Anything that touches the warehouse must be guarded.** During parsing, introspective helpers return nothing, so unguarded code raises on `dbt parse`, `dbt ls`, and documentation generation while `dbt run` works. Wrap it: `{% if execute %} ... {% endif %}`. This is the most common cause of "it builds but the project will not parse."

`{{ log() }}` at debug level is invisible unless the log level allows it — pass `info=True` when you want to see the message without turning on debug logging for everything.

Rendering surprises worth checking in the compiled file specifically, because none of them raise an error:

| Construct | Silent effect |
|---|---|
| `{%- ... -%}` around a keyword | Two tokens concatenate: `where x = 1and y = 2` |
| A comma emitted by a loop with no trailing-comma guard | A dangling comma before `from`, or a missing one between columns |
| `{# ... #}` used where `--` was intended | The text vanishes from the compiled SQL rather than surviving as a comment |
| A conditional whose test is a non-empty string | Always true. `{% if var('enabled') %}` fires for `"false"` |
| A `{% set %}` inside an `{% if %}` in a different scope | The value is lost outside the block, and the later reference resolves to nothing |
| An undefined variable used with a filter that tolerates it | Renders as an empty string, producing valid SQL with a missing predicate |

The last row is the dangerous one: a filter predicate that renders to nothing produces a query that runs, returns more rows than intended, and reports no problem at all.

## Read the compiled SQL

The model file is not what ran. The compiled SQL is.

```bash
dbt compile --select <model>
# target/compiled/<project_name>/models/<path>/<model>.sql
```

Read that file. It resolves every `ref()`, macro, and conditional to literal text, which answers questions the model file cannot:

- Which database and schema did `ref()` actually resolve to?
- Did the incremental branch or the full-refresh branch compile?
- Did a conditional filter appear, or silently not appear?
- Is the boundary predicate `>=` or `>`?

Then **run the compiled SQL directly against the warehouse**, unmodified, in a console or via `dbt show --inline`. This is the highest-value single step in this skill, because it splits the problem cleanly:

- **Compiled SQL fails the same way** → the problem is in the SQL. dbt is irrelevant. Debug it as a query: comment out CTEs, narrow the select list, add `where` clauses until it succeeds.
- **Compiled SQL succeeds** → the problem is in dbt's execution wrapper: materialization, incremental merge, schema change handling, grants, or hooks. Look at the `create`/`merge` statement in `logs/dbt.log`, not at the select.

## Bisect the DAG

When several models fail, only the first one matters. The rest are consequences.

```bash
dbt build --select +<model> --fail-fast
```

`--fail-fast` stops at the first failure instead of producing a wall of downstream errors. That first node is the only one to investigate.

If the run already happened, `target/run_results.json` records every node's status and timing; the earliest `error` in execution order is the origin.

To narrow further, walk the ancestor chain outward from the failing node:

```bash
dbt ls --select +<model>          # every ancestor, in dependency order
dbt build --select <ancestor>     # one at a time, upstream first
```

Build upstream-first. A downstream failure caused by an unbuilt or stale upstream model is an environment problem, and building the ancestor resolves it without any edit.

### Narrowing to a minimal reproduction

Once the first failing node is known, shrink the failure until only the cause is left. Each step below halves the search space, and each is cheap.

1. **Narrow the selector.** One node, no ancestors, no descendants: `dbt build --select <model>`. If it now passes, the failure was about the graph — ordering, staleness, or a sibling — not about this model.
2. **Split dbt from SQL.** Run the compiled statement directly. If it fails identically, dbt is irrelevant from here on. If it succeeds, the fault is in the materialisation wrapper, and `target/run/` is the file to read.
3. **Bisect the query.** Comment out CTEs from the bottom up, replacing the final `select` with the last surviving CTE. The first CTE whose removal makes the error disappear contains the cause. On a wide model, bisect the select list the same way.
4. **Bound the data.** Add a restrictive `where` on the partition or date column. A failure that survives on one day's data is reproducible in seconds instead of minutes; a failure that vanishes is data-dependent, which is itself the finding — some specific rows cause it, and they can be found.
5. **Bisect history.** When the code is suspected but the offending change is not, `git bisect` over the model's commits, with the reproduction from step 3 as the test:

```bash
git bisect start <known-bad-commit> <known-good-commit>
# at each step:
dbt compile --select <model> && dbt build --select <model>
git bisect good    # or: git bisect bad
```

`git bisect` needs a test that is deterministic and fast, which is why steps 3 and 4 come first. Bisecting with a fifteen-minute full build as the test is how an afternoon disappears. If no commit is a known-good, the change may be upstream rather than in the repo — go back to "did anything change?" and check the source instead.

**When the failure will not reproduce, stop bisecting.** An intermittent failure is a concurrency, timeout, or late-arriving-data problem, and none of those are found by narrowing the query. Go to the run history and compare a failing run against a passing one: same node, different duration, different concurrency, different input volume.

## Was the model ever built in this environment?

Check this early. It explains a large share of confusing failures and costs one query.

A `ref()` in a development target typically resolves to the development location **if the model was built there**, and otherwise may fall back to another location or fail outright, depending on the project's setup (see `dbt-environments`). Either way, a model you have never built locally is not reading what you assume.

```sql
-- explicit database and schema, from the contract's environments.dev — never ref()
select count(*) as rows, max(<timestamp_column>) as latest
from <dev_database>.<dev_schema>.<model>
```

Three outcomes:

| Result | Meaning |
|---|---|
| Table does not exist | Never built here. Build the ancestors; the failure may vanish |
| Exists but empty | Built, but a filter excluded everything — often a development date limit |
| Exists with stale data | Built once, long ago. Downstream results are computed from a snapshot of the past |

## Dev and prod behave differently with the same code

Same SQL, different result, is always an **environment** problem. The code is a constant; something around it varies. Check in this order, cheapest first:

| # | Difference | How to confirm |
|---|---|---|
| 1 | `ref()` resolved to different physical tables | Read `target/compiled/` in both environments and compare the fully-qualified names |
| 2 | A development date or row limit is active | Grep the model for environment-conditional filters; check the compiled SQL for a `where` clause present in one and absent in the other |
| 3 | An environment-conditional config: `enabled`, `materialized`, `full_refresh` | Compare the compiled config, not the source |
| 4 | Incremental vs full build | A development environment often full-refreshes what production loads incrementally. Different code path, so different bugs |
| 5 | Upstream data differs | The development copy may be a partial or older clone of production |
| 6 | Different `var()` or `env_var()` values | `dbt debug` and the target definition |
| 7 | Role or grant differences | Permission errors and, on some warehouses, silently-filtered rows under row-level policies |
| 8 | Package or dbt version differences | Compare `package-lock.yml` / `packages.yml` resolution and `dbt --version` |

Check cause 1 first regardless of suspicion: reading both compiled files settles it in a minute and costs nothing. Theorize afterwards.

### It works locally and fails in automation

A distinct case, with its own causes. An automated environment is not a smaller development environment — it is usually a fresh, empty, differently-permissioned one that has never built anything, and most of these failures come from assuming otherwise.

| Symptom in the automated run | Cause | Confirmation |
|---|---|---|
| "relation does not exist" for a model you never touched | The run built only a subset and nothing supplied the rest. Either state-based deferral is not configured, or it is and the stored manifest is missing | Whether the run resolves unbuilt references to another environment at all, and whether a prior manifest was actually fetched |
| "Could not find manifest.json at path", "requires a --state path" | The comparison or deferral baseline was never downloaded into the job, or the path points at the file rather than the directory containing it | The job's artifact-fetch step, and whether the very first run has any baseline to compare against |
| Everything rebuilds when few files changed | State comparison silently fell back to selecting everything, usually because the baseline was absent or its schema version does not match the running dbt version | Compare the dbt version in the job against the one that produced the baseline |
| A seed-dependent model fails | Seeds exist locally and were never loaded here | Whether the job runs a seed step, or `build` rather than `run` |
| "Undefined" on a variable that works locally | An environment variable is set on your machine and not in the job, and the call site has no default | Grep for `env_var(` without a second argument |
| A permission error only in automation | The automated role is not your role. Frequently it can read but cannot create a schema | The specific object named in the error, checked against that role's grants |
| A test that stores failures fails only here | Storing failures needs permission to create its audit schema | Whether that schema exists and the role may create it |
| Passes locally, fails intermittently in the job | Different thread count, so nodes that never overlapped locally now do | `thread_id` and timings in the run artifact |
| A model is empty in the job | A development or CI-only row or date limit is active there and not locally, or the deferred source is an environment with different coverage | The compiled SQL from the job, not from your machine |

The general rule: **compare the two runs' artifacts, not the two environments' descriptions.** The compiled SQL and the run results from the failing job are the evidence; a description of how the job is configured is a claim about it. If the job's artifacts are not retrievable, say that the divergence could not be confirmed rather than asserting a cause.

Two failure modes specific to this class. Re-running the job "to see" is not a diagnosis when the first run's artifacts were never read — they are usually still available, and they contain the answer. And a fix that makes the job pass by widening its selection, disabling a step, or granting broader permissions has removed the signal rather than the defect; say which one you did and why it was the right level to fix it at.

## Warehouse-specific tools

Gate this on `project.warehouse`; do not assume a platform.

| Warehouse | Useful for debugging |
|---|---|
| snowflake, bigquery, databricks, redshift | A query-history or job-history view exposing the exact executed statement, error, and bytes scanned |
| postgres, duckdb | Server log and `explain`; no persistent query history by default |
| trino | Per-query UI/API with stage-level failure detail |

All of them support `information_schema` for confirming a table's existence, columns, and types — which is how to verify a schema assumption rather than asserting one.

Three questions the engine can answer that dbt cannot, whatever the platform:

| Question | Where the answer is |
|---|---|
| Did the statement dbt claims to have run actually reach the engine, and what did the engine say? | Query or job history, matched on time and text |
| Did this query spill, queue, or get throttled? | Per-query execution metadata — spill bytes, queue time, slot or slot-time consumption |
| Does the relation exist, and does this role see it? | `information_schema`, queried as the failing role. Existence and visibility are different facts, and several engines return the same message for both |

That last distinction is worth care: on more than one engine, "does not exist or not authorized" is a single message covering two causes. Treating it as "missing" and building the ancestor, when the real cause is a grant, produces a build that succeeds locally and fails for everyone else.

Tagging dbt's statements — most adapters support attaching an identifier to the session or query — makes them findable in query history later. Whether the project does this is a project decision, not something to introduce mid-incident.

If `project.warehouse` is absent, say that the advice is generic and confirm the platform before recommending a platform-specific tool.


## What not to do

These are the four moves that turn a debuggable failure into a silent, permanent defect.

1. **Do not re-run hoping it passes.** A re-run is a diagnostic only when there is a stated reason to expect a different outcome — a transient connection error, a concurrency conflict, or an upstream load that has since completed. Say the reason. A test that passes on the second run without explanation has not been fixed; it has become intermittent, which is worse.

2. **Do not widen a test's tolerance to make it green.** Changing a bound from 100 to 10,000 does not fix anything; it deletes the test while leaving its name in the repo, which is strictly worse than removing it, because the next engineer believes the invariant is checked. A threshold may only move when the *business* reason for the old value is understood and no longer applies — and the summary must say what that reason was.

3. **Do not add `severity: warn` to silence a real failure.** `warn` is for assertions that are genuinely advisory — measurement drift between two sources, expected seasonal variance. It is not a mute button. Downgrading an error-level failure without diagnosing it converts a blocking signal into log noise nobody reads, and the data stays wrong.

4. **Do not fix a data problem in code without saying so.** Wrapping a defect in `coalesce` or filtering out the offending rows makes the symptom disappear and the cause permanent. If a defensive fix is genuinely wanted, add it deliberately, comment why, and report the upstream defect anyway.

The common thread: each of these makes the signal quieter without making the data more correct. Every one of them is discoverable in a diff, and every one damages trust in the test suite far beyond the model it touched.

## Reporting the finding

A diagnosis is only useful if it is falsifiable. State:

1. **The class** — code, data, or environment.
2. **The evidence** — the error text, the failing rows, the compiled SQL line, the row count.
3. **The fix, and what it does not fix** — especially if the underlying cause is upstream.
4. **What remains unverified**, explicitly.

Evidence standards are in `dbt-verification`. "This should work now" is not a resolution.

## Completion checklist

- [ ] Full error text read, not just the summary line
- [ ] dbt's half of the message separated from the warehouse's half, and the warehouse's half quoted
- [ ] Class named: code, data, or environment — and the failure stage named
- [ ] "Did anything change?" answered from `git log`
- [ ] For a failing test: failing rows inspected, and test-wrong vs data-wrong decided from the rows
- [ ] Test configuration checked — `severity`, `where`, `error_if`, `limit` — before interpreting the count
- [ ] Compiled SQL read, and run directly against the warehouse where relevant
- [ ] First failing node identified by bisection, not the last error in the log
- [ ] Failure narrowed to a minimal reproduction, or its non-reproducibility stated as the finding
- [ ] Confirmed whether the model and its ancestors were ever built in this environment
- [ ] For dev/prod or local/automation divergence: compiled SQL and run artifacts compared across both
- [ ] For a "does not exist" error: absence distinguished from lack of visibility to the role
- [ ] For a slow-but-successful build: query metadata checked for spill, queueing, or scan growth
- [ ] Fix addresses the class of problem identified, not the symptom
- [ ] Fix applied at the right level — not a model edit for a grant, connection, or scheduling problem
- [ ] No re-run, widened tolerance, or severity downgrade used in place of a diagnosis

## The most common failure modes

1. **Fixing the last error instead of the first.** One broken upstream node produces a screen of downstream failures. Without `--fail-fast` or `run_results.json`, the natural instinct is to debug the most recent message, which is a consequence, not a cause.
2. **Debugging the model file instead of the compiled SQL.** The model file contains Jinja that may not have produced what you assume. Failures that appear impossible frequently become obvious on reading `target/compiled/`.
3. **Searching dbt's half of the error message.** The generic wrapper is the same for thousands of unrelated failures; the engine's message, and its error code, is the part with an answer behind it.
4. **Trusting a line number across the Jinja boundary.** A position reported in a database error indexes into the compiled file. Counting to that line in the model file lands somewhere arbitrary and sends the investigation to the wrong CTE.
5. **Calling a data problem a code problem.** Nothing changed in the repo, yet the model is edited to accommodate a transient upstream gap. The workaround outlives the gap and quietly corrupts results afterwards.
6. **Silencing a test instead of diagnosing it.** Widening a bound or downgrading severity produces a green run and leaves the data wrong, with the added cost that the assertion now looks checked.
7. **Assuming the environment.** A model never built locally, a stale local copy, or a `ref()` resolving elsewhere explains a large share of failures that appear to defy the code.
8. **Fixing an automation failure by weakening the automation.** Widening the selection, skipping a step, or broadening a grant makes the job green and deletes the check. If that is genuinely the right fix, say which check was removed.
9. **Bisecting with a slow, non-deterministic test.** `git bisect` and CTE-elimination both require a reproduction that is fast and reliable. Establishing one first is not a detour; it is what makes the rest possible.
10. **Treating a successful-but-slow model as a non-failure.** Spill, queueing, and a quietly growing scan produce no error line. The only signal is in query metadata, and nobody looks there unless prompted.
