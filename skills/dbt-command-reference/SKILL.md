---
name: dbt-command-reference
description: Use when constructing a dbt CLI invocation, unsure of node selection syntax, hitting an unexpected CLI error, selecting multiple models, placing --full-refresh, using dbt show, deferring to a previous state, or calling dbt Cloud through an API or MCP tool. Reference skill — look up the syntax, do not read it end to end.
metadata:
  phase: reference
---

# Command reference

Lookup, not narrative. Every entry is the form that works; where a form silently misbehaves, it is named.

| Sub-document | Read it when |
|---|---|
| [selection-syntax.md](selection-syntax.md) | You need graph operators, wildcards, set operations, selector files, or a method-selector reference |
| [state-and-ci.md](state-and-ci.md) | You are building `state:`/`--defer` logic or a Slim CI pipeline |

## Contract

Only one field is needed here: `project.dbt_project_name`, used to construct node `unique_id`s for API and MCP calls.

**Absent → read `name:` from `dbt_project.yml`.** Never guess a project name; a wrong one produces a not-found error that looks like a missing model.

## Establish the version before quoting syntax

More of this surface is version-gated than people expect, and the gates are silent: an unsupported selector matches nothing and exits zero. Check first, then construct the command.

```bash
dbt --version                # engine and adapter versions
```

Version markers used below are dbt Core minor versions unless stated. Anything unmarked has been stable for long enough that it is safe to assume — but if a selector unexpectedly matches nothing, version support is the first hypothesis, not the last.

Two distinct lineages now exist, and they differ in flag handling as well as speed:

| Lineage | What it is |
|---|---|
| dbt Core 1.x (Python) | The long-standing implementation. Everything in this document was written against it |
| dbt v2 / the Fusion engine (Rust) | A reimplementation sharing the authoring language. Stricter validation, several flags removed |

The cross-lineage facts worth knowing before writing a command:

| Change | Consequence |
|---|---|
| `--models` / `--model` / `-m` | Long-deprecated aliases for `--select`. **Error** in v2. Use `--select` / `-s` everywhere |
| `--resource-type` / `--exclude-resource-type` | Renamed to the plural `--resource-types` / `--exclude-resource-types` in v2 |
| `--partial-parse` / `--no-partial-parse` | No longer honoured on Fusion job runs; it warns and does nothing |
| Failures at parse rather than compile | v2 fails `dbt parse` on a call to a macro that does not exist, an undefined `var()`, or a generic test that is not defined. On 1.x those pass parse and fail compile |
| Manifest compatibility | Both lineages read each other's manifest for `state:` and `--defer`, so a mixed estate can share artifacts |
| `dbt parse --use-v2-parser` (1.12) | Runs the newer parser without changing anything else — the cheapest compatibility check available |

Where a project's lineage is unknown, prefer the forms that work on both: `--select` over `-m`, and an explicit selector confirmed with `dbt ls`.

## Commands

| Command | Does | Warehouse contact | Use for |
|---|---|---|---|
| `dbt deps` | Installs packages | No | After changing `packages.yml` |
| `dbt parse` | Builds the manifest, validates structure | Minimal | Fast syntax/ref check; refresh manifest for tooling |
| `dbt compile` | Renders Jinja to SQL in `target/compiled/` | Yes, if a macro is introspective | Mandatory after every SQL edit |
| `dbt run` | Materializes models | Yes | Models only, no tests |
| `dbt test` | Runs tests against existing relations | Yes | Re-testing without rebuilding |
| `dbt build` | Models, tests, seeds, snapshots in DAG order | Yes | **Default choice.** Stops a downstream model when an upstream test fails |
| `dbt seed` | Loads CSVs | Yes | Seed changes |
| `dbt snapshot` | Runs snapshots | Yes | Snapshots only |
| `dbt source freshness` | Checks source freshness, writes `sources.json` | Yes | Staleness triage. **Exits non-zero on a stale source** |
| `dbt show` | Recompiles and runs a query, prints rows | Yes | Ad-hoc inspection. Does not read the materialized relation |
| `dbt ls` / `dbt list` | Lists nodes a selector matches | No | Confirm a selector before running it |
| `dbt retry` | Re-runs from the last failure using the previous `run_results.json` | Yes | Resume a partially failed run without redoing successful nodes |
| `dbt clone` | Clones nodes from a state manifest | Yes | CI setup; making a modifiable copy of production |
| `dbt run-operation` | Executes a macro, or `--sql` directly (1.12+) | Yes | Maintenance macros, codegen. No dry-run, no undo |
| `dbt docs generate` | Builds the catalog | Yes | Docs; `--no-compile` to skip recompiling |
| `dbt debug` | Prints resolved connection and target | Yes | Confirm which environment you are in |
| `dbt clean` | Removes generated directories | No | Recovering from a corrupt `target/` or stale `dbt_packages/` |

`dbt build` over `dbt run` + `dbt test`: build interleaves tests into the DAG, so a failing upstream test prevents downstream models from running on bad data. Run-then-test builds everything first and tells you afterwards.

### What `dbt build` actually guarantees

The ordering guarantee is more specific than "tests run in order", and the specifics decide whether a bad row reaches a mart.

| Situation | Behaviour |
|---|---|
| Test on a parent fails at `error` severity | Every child of that parent is **skipped**, not run |
| Test configured `severity: warn` | Nothing is skipped. A warning does not block |
| Test with two parents, one upstream of the other | Blocks and skips children of the **most downstream** parent only |
| Test with two independent parents | A child is skipped only if it depends on **all** of those parents |
| Unit tests and data tests on one model | Unit tests, then materialize, then data tests — so a model with failing unit tests is never built |

Two consequences. First, `severity: warn` on a test is a decision to let bad data through; it is legitimate for advisory checks and wrong for a key uniqueness test. Second, in v2 all unit tests run before the rest of the DAG rather than in lineage order, so a unit test failure surfaces earlier and no models are built.

`dbt build` writes one `manifest.json` and one `run_results.json` covering every resource type it selected — which is why it is also the command whose artifacts are usable for `result:` selection across models and tests together.

### `compile` vs `parse` vs `ls`

Routinely confused, and the difference is what each one can prove.

| Command | Proves | Does not prove |
|---|---|---|
| `dbt parse` | The project's structure is valid and the DAG resolves | That the SQL is renderable, or that it is valid SQL |
| `dbt compile` | Jinja rendered to concrete SQL, written to `target/` | That the SQL runs — nothing is submitted for models |
| `dbt ls` | Exactly which nodes a selector matches | Anything about their contents |
| `dbt show` | The query runs and returns these rows | Anything about the materialized relation (see below) |
| `dbt build --empty` | The SQL is accepted by the warehouse against a real schema | That it produces correct rows — inputs were empty |

`dbt compile` does contact the warehouse when the project contains an introspective macro, because rendering requires the query result. That is also why compilation can fail on a clean warehouse and succeed on a populated one.

A caution that follows from it and belongs here rather than in a macro document: **compilation runs introspective queries, including during `dbt docs generate`.** A macro containing a side-effecting statement will execute it during documentation generation. See `dbt-macros`.

## Selection: operators, wildcards, sets, and methods

Node selection has its own layered grammar — graph operators (`+`, `@`, depths), Unix-style wildcards, set operations (union, intersection, `--exclude`), selector files for anything too complex to keep on a command line, and the full table of method selectors (`tag:`, `path:`, `config:`, `state:`, and the rest). It also governs which tests get pulled in when you select a model — the indirect-selection mode, which is the setting most likely to fail a CI run on something unrelated to the change under review.

**Quote every multi-value selector** — how an unquoted list fails differs by CLI, and quoting is the one form that behaves identically everywhere. Confirm any selector with `dbt ls` before an expensive run.

The full reference — every operator, every method, the selector-file YAML shape, and the indirect-selection table — is in [selection-syntax.md](selection-syntax.md).

## State and deferral, and Slim CI

`--state` and `--defer` are the highest-leverage and most misunderstood part of the CLI. In short: `state:modified` (and its narrower forms — `.body`, `.configs`, `.macros`, and more) selects what changed against a previous manifest; `--defer` resolves unselected `ref()`s to that manifest's relations instead of rebuilding them. Two traps dominate: pointing `--state` at the same directory dbt writes to (the comparison becomes a no-op), and forgetting that some CLIs — notably the dbt Cloud CLI — defer to production **by default**, so an unbuilt model can read production data with no flag at all.

Slim CI — `dbt build --select "state:modified+" --state ./prod-artifacts --defer` — is the pattern that makes this affordable at scale, with refinements for retrying failures, combining change- and failure-based selection, and avoiding cross-environment test noise.

The full mechanics — what `state:modified` does and does not catch, the artifact trap in detail, exactly what `--defer` rewrites, and the Slim CI refinement table — are in [state-and-ci.md](state-and-ci.md).



One convention, and one thing that is not a rule.

```bash
# boolean flag first, selector quoted and last — a convention, not a requirement
dbt build --full-refresh --select <model>

# also valid: --full-refresh works before or after --select. Ordering is not fragile here
dbt build --select <model> --full-refresh

# dbt show: --limit after --inline keeps values next to their flags
dbt show --inline "select * from <database>.<schema>.<model>" --limit 20
```

`--full-refresh` is a boolean flag and functions on either side of `--select`; **verified empirically, and the ordering is not a source of bugs.** Likewise `dbt show --limit` before `--inline` parses fine — putting `--limit` last is a readability convention that keeps value-taking flags adjacent to their values, not a parse requirement.

What genuinely matters is not ordering but quoting: an unquoted multi-value selector is where selection changes unexpectedly. Put boolean flags first if you like the habit, and quote every selector because that one is load-bearing.

## `--full-refresh`

| Fact | Consequence |
|---|---|
| Only meaningful on incremental models | On a view or table it is a no-op — and a signal that the author misunderstood the materialization |
| Ignores the `is_incremental()` branch | Rebuilds from scratch, at full-history cost |
| Blocked by `full_refresh=false` in the model config | Deliberate: the source cannot reproduce history. Do not remove the config to get past it |
| Required for an incremental model's first build in a fresh schema | There is no existing relation to merge into |

Check the materialization before reaching for it.

## `dbt show`

| Need | Command |
|---|---|
| Preview a model | `dbt show --select <model>` |
| Ad-hoc SQL | `dbt show --inline "<sql>" --limit 20` |
| All rows | `dbt show --inline "<sql>" --limit -1` |
| Validation against a specific relation | `dbt show --inline "select count(*) from <database>.<schema>.<model>" --limit 1` |
| Machine-readable output | `dbt show --inline "<sql>" --output json` |
| Inspect a failing test's rows | `dbt show --select <generated_test_name>` |

Default limit is 5 rows — easy to mistake for "the table only has 5 rows." For validation, always name the database and schema rather than using `ref()`.

Writing `--limit` after `--inline` is a readability convention that keeps the value-taking flags adjacent to their values; it is not a parse requirement, and the reverse order is accepted.

Four limits worth knowing before trusting the output:

| Limit | Consequence |
|---|---|
| **It re-runs the query; it does not read the table** | Previewing a model you just built shows the result of recompiling and re-executing its SQL. If the materialized relation differs from what the SQL now produces, `dbt show` shows the SQL, not the table. To inspect the relation, query it explicitly |
| Single node only | Selector methods and graph operators are not honoured; multi-node selection does not work |
| `--limit` modifies the SQL | It is pushed down as a `LIMIT`, so the warehouse returns fewer rows — cheap, but it means an aggregate over "all rows" needs `--limit -1` or an explicit aggregate in the query |
| `--inline` runs arbitrary SQL | dbt cannot tell a `select` from a `delete`. Point it at a read-only role for ad-hoc work |

No Python model support.

## Retrying a failed run

```bash
dbt retry
```

Re-executes the previous invocation from its point of failure, reading `run_results.json` to know where that was. Semantics worth knowing before relying on it:

| Situation | What `dbt retry` does |
|---|---|
| Some nodes ran, then one failed | Resumes from the failure. Successful nodes are not rebuilt |
| Nothing ran — connection or permission error before execution | Nothing to retry. Fix the cause and re-run the original command |
| Previous command fully succeeded | No-op |
| The underlying problem was not fixed | Fails again, identically. `retry` is idempotent, not corrective |

Because it depends on `run_results.json`, `dbt retry` only works where that artifact survived — the same directory, not a fresh container. In CI, either persist `target/` between steps or use `result:error+` with an explicit `--state` instead.

## `dbt clone`

```bash
dbt clone --select "<model>" --state ./prod-artifacts
```

Creates relations in the target schema from the state manifest's relations. On warehouses with zero-copy cloning it is a metadata operation and effectively free; elsewhere dbt falls back to a pointer view (`select * from <source>`). Pre-existing relations are not replaced unless `--full-refresh` is passed. Available from 1.6.

Clone versus defer — they solve overlapping problems and the choice is not stylistic:

| | `--defer` | `dbt clone` |
|---|---|---|
| Creates warehouse objects | No | Yes |
| Visible to tools outside dbt, e.g. a BI tool | No | Yes |
| Safe to modify the result | No — it is production | Yes, it is a copy |
| Drifts from the source | No, always current | Yes, it is a point in time |
| Can mix several source environments | Yes, per `ref()` | No, one source to one target |

Rule of thumb: defer for CI, clone for CD and for anything a human or a BI tool must be able to open. The specific CI use worth knowing: cloning the modified incremental models before building them gives the incremental branch an existing relation to merge into, which both exercises the real code path and avoids paying for a full-history rebuild.

## Frequently useful flags

| Flag | Effect |
|---|---|
| `--empty` | Builds with inputs limited to zero rows. Validates SQL against the warehouse cheaply (1.8+ for run/build/compile/snapshot; seed support added later) |
| `--fail-fast` / `-x` | Stop on the first failure. Global — stops on test failures as well as run errors, and cancels in-flight queries |
| `--warn-error` | Treat every warning as an error |
| `--warn-error-options '{"error": ["<WarningName>"]}'` | Selective promotion of warnings |
| `--threads <n>` | Override profile concurrency |
| `--target <name>` / `-t` | Choose a target from `profiles.yml` |
| `--indirect-selection <mode>` | `eager`, `buildable`, `cautious`, `empty` |
| `--quiet` | Suppress everything but errors and `show` output |
| `--log-format json` | Structured logs; also how to discover warning event names |
| `--no-partial-parse` | Force a full re-parse when the manifest seems stale (1.x; ignored on Fusion job runs) |
| `--vars '{"k": "v"}'` | Project variables. **Not** the place for environment-dependent behavior — see `dbt-environments` |

### `--empty` and what it proves

`--empty` limits every `ref()` and `source()` to zero rows, then executes the model SQL for real. So it proves the SQL is valid against actual warehouse types and that dependencies resolve — a much stronger claim than `compile` makes, at close to no scan cost.

Its main uses: validating a large model without paying for it, and creating the empty relations that `dbt test --select "test_type:unit"` needs to exist before it can run. Unit tests require their model's parents to exist in the warehouse, and `--empty` is the cheap way to satisfy that.

Two caveats. Python models ignore the flag. And when `--empty` skips processing a `ref()` or `source()` for optimization, a hook that needs the resolved relation string must call `.render()` on it explicitly, or compilation fails.

## Exit codes and what CI should treat as failure

| Code | Meaning |
|---|---|
| 0 | Completed with no error |
| 1 | Completed with at least one handled error — a model failed, a test failed at error severity, permissions were wrong. The run finished; some nodes may have been skipped |
| 2 | Unhandled error — interrupt, network failure. The run did not finish |

A zero exit code always means success and a non-zero one always means failure, so CI logic should test for non-zero rather than for a specific code.

Three things a bare exit code will not tell you, each of which has let a broken change through:

1. **A warning exits 0.** A test at `severity: warn`, a selector matching nothing, and a deprecation all exit successfully. If the pipeline should fail on any of those, they must be promoted explicitly.
2. **Skipped is not failed.** Exit 1 says something failed; it does not say how much was skipped downstream. `run_results.json` does.
3. **`dbt source freshness` exits non-zero on a stale source**, so placing it in the middle of a job stops the steps after it. Put it first if models must not run on stale data, last or in a separate job if they should run anyway.

`--warn-error` promotes every warning — which is a real hazard in production, because a warning introduced by a future dbt version will start failing a job that has not changed. Prefer naming the ones you mean:

```bash
dbt build --warn-error-options '{"error": ["NoNodesForSelectionCriteria"]}'
```

`NoNodesForSelectionCriteria` is the one most worth promoting: it is what fires when a selector matches nothing, which is exactly the case where a CI job passes without having built anything. Warning names come from the structured log output (`--log-format json`), and `error`, `warn` and `silence` all take arrays. `--warn-error` and `--warn-error-options` are mutually exclusive; setting both is a usage error.

## Artifacts (`target/`)

| File | Contains |
|---|---|
| `manifest.json` | Every node, its config, dependencies, compiled SQL |
| `run_results.json` | Per-node status, timing, `rows_affected` |
| `catalog.json` | Warehouse columns and types — needs `docs generate` |
| `sources.json` | Freshness results |
| `compiled/<project_name>/...` | Rendered SQL |
| `run/<project_name>/...` | The full DDL/DML wrapper, including merge predicates |

Reading these rather than the console is covered in `dbt-verification`.

## `--threads`

`--threads` caps how many nodes dbt executes concurrently, bounded by what the DAG actually allows — a linear chain of five models cannot use five threads. Raising it shortens wall-clock time and raises peak warehouse concurrency, so the ceiling is the warehouse's, not dbt's.

Behaviour differs by lineage, and this is worth checking rather than assuming:

| Lineage | Behaviour |
|---|---|
| dbt Core 1.x | Honours the configured value; the profile default is low, so most projects raise it |
| Fusion, on some adapters | Manages parallelism itself with backpressure; `threads` acts as a cap. Lowering it is the remedy for rate-limit or timeout errors, not raising it |

Two practical notes. More threads does not make a slow model faster — it only overlaps independent models, so a single dominating model is unaffected. And a backfill or any operation writing to one table should run with low concurrency regardless; parallel writes to the same relation produce lock contention at best. See `dbt-shipping-changes`.

## `run-operation` and code generation

```bash
dbt run-operation <macro_name> --args '{"key": "value"}'
```

`--args` is parsed as YAML, so a value needing to stay a string should be quoted, and a boolean written as `true` arrives as a boolean.

From 1.12 there is also a macro-less form:

```bash
dbt run-operation --sql "<statement>"
```

It runs the statement through the full Jinja pipeline, so `ref()`, `source()`, `var()` and `target` are available, and it cannot be combined with a macro name or `--args`. Convenient for a genuine one-off — a grant, a drop of a known-dead table. It is also unreviewable and unrepeatable: nothing records what was run except shell history. Anything that will be run more than once, or that anyone else will need to run, belongs in a macro under version control.

**`run-operation` has no dry-run, no confirmation, and no undo.** dbt does not inspect the statement, so a `delete` with a wrong predicate executes at full speed. The safety pattern — count first, require an explicit confirmation token, log what happened — is in `dbt-macros`, and it belongs in every destructive operational macro. Never point one at production unless the request named production.

If the project installs `dbt-labs/codegen`, three macros save real time. Confirm the package is in `packages.yml` before suggesting them — they are not built in.

| Macro | Produces |
|---|---|
| `generate_source` | Source YAML for a schema |
| `generate_base_model` | A staging model selecting every source column |
| `generate_model_yaml` | Schema YAML skeleton for an existing model |

## `dbt docs generate`

Builds `catalog.json` by querying the warehouse's information schema for every relation, then compiles the project.

```bash
dbt docs generate --no-compile     # skip recompiling; reuse the existing manifest
```

`--no-compile` is the flag to reach for when the manifest is already current and you only want a refreshed catalog. The reason it matters is not speed: **compilation executes introspective macros**, so a project containing a macro with a side effect performs that side effect during documentation generation. If `dbt docs generate` has ever done something surprising to the warehouse, that is the mechanism. See `dbt-macros`.

The catalog also queries metadata for the whole project, which on a large warehouse is itself a non-trivial cost — a reason not to attach docs generation to every CI run.

## dbt Cloud API and MCP tools

Generic guidance; exact tool names vary by client. What does not vary:

### `unique_id` construction

| Node | Format |
|---|---|
| Model | `model.<project_name>.<model_name>` |
| Source | `source.<project_name>.<source_name>.<table_name>` |
| Seed | `seed.<project_name>.<seed_name>` |
| Snapshot | `snapshot.<project_name>.<snapshot_name>` |
| Exposure | `exposure.<project_name>.<exposure_name>` |
| Macro | `macro.<package_name>.<macro_name>` — package, not project |

`<project_name>` comes from `project.dbt_project_name`, or `dbt_project.yml` if the contract is absent.

### Behaviors that surprise people

| Behavior | Consequence |
|---|---|
| Metadata tools read the **production** manifest | They describe what is deployed, not your working copy. Uncommitted changes are invisible |
| Local-manifest tools read `target/manifest.json` | Stale until you `dbt parse` |
| Remote compile is usually project-wide, with no selector | For one model, use the CLI: `dbt compile --select <model>` |
| Remote query tools take a SQL string plus a separate row limit | Do not also write `limit` inside the SQL; the two interact badly |
| Parent/child tools return `unique_id`s, not names | Strip the prefix before showing them to a human |
| Job runs are asynchronous | Trigger returns a run id; poll for status, then fetch the error artifact on failure |

### Investigating a failed scheduled run

1. List recent runs for the job, filtered to failed status.
2. Fetch the run's error output — the compiled SQL and the warehouse message are what matter.
3. Fetch `run_results.json` from that run's artifacts to see which nodes were `error` and which were `skipped`.
4. Reproduce locally with the same selector before changing anything.

## Gotchas

| Symptom | Cause |
|---|---|
| Unexpected node count from a bare multi-value `--select` | An adjacent flag or argument absorbed as a selector value. Quote the list |
| `dbt ls` matched more nodes than `dbt run` | Resource-type filtering at the end of selection. Not a bug |
| `dbt show` returned 5 rows | Default limit; pass `--limit` |
| `dbt show` output disagrees with the table | It re-ran the SQL; it did not read the relation |
| `--full-refresh` did nothing | The model is not incremental |
| `--full-refresh` raised a compiler error | `full_refresh=false`, or the project requires an explicit backfill range |
| `state:` selector matched nothing | Missing or stale `--state` artifacts — or `--state` pointing at `target/`, which was overwritten during parsing |
| CI passed and built nothing | Selector matched no nodes and that is only a warning. Promote `NoNodesForSelectionCriteria` |
| A variable change produced no CI diff | `state:modified` cannot see `var` or `env_var` values |
| A large seed edit produced no CI diff | Seeds ≥ 1 MiB are compared by path, not content |
| Rebuilt model still reads old upstream data | Deferral resolved the ref to the state manifest |
| A stale local table was preferred over production | Deferral only applies when the relation is absent. Use `--favor-state` |
| A `relationships` test failed on an unrelated change | Eager indirect selection pulled in a test whose other parent was never built |
| `dbt retry` said there was nothing to do | No nodes had executed before the failure, or `run_results.json` is gone |
| A `-m` flag stopped working | `--models` / `-m` errors on v2. Use `--select` |
| `--resource-type` stopped working | Renamed to the plural form on v2 |
| Node not found via API | Wrong project name in the `unique_id` |
| Local-manifest tool disagrees with your files | Manifest not re-parsed |
| Re-running everything after one failure | `dbt retry` exists |
| `dbt docs generate` altered warehouse state | A macro with a side effect ran during compilation |

## Completion checklist

- [ ] Version and lineage established before relying on any version-gated flag or selector
- [ ] Selector confirmed with `dbt ls` before an expensive run
- [ ] Multi-value selectors quoted
- [ ] `run-operation` checked for a dry-run or confirmation path before it touches anything that matters
- [ ] `--full-refresh` used only on incremental models, and only deliberately
- [ ] `build` rather than `run` unless there is a reason
- [ ] `--state` present when using `state:` or `--defer`, its provenance known, and **not** pointing at `target/`
- [ ] Deferral behaviour understood for this CLI — including whether it is on by default
- [ ] Compiled SQL checked for which relations it names, when deferral was in play
- [ ] Indirect selection mode chosen deliberately where the build is a DAG subset
- [ ] CI treats non-zero as failure, and promotes the warnings that should fail
- [ ] `unique_id` project segment taken from the contract or `dbt_project.yml`
- [ ] `run_results.json` checked rather than the console summary

## The failure modes to watch for

1. **Wrong selection from an unquoted selector** — an adjacent flag or argument absorbed into the list. The dbt Cloud CLI rejects the bare form with a quoting hint and exits non-zero; dbt Core accepts space-separated values, so there the change in selection is silent. `dbt ls` with the same selector is the check, and it is the only check that works on both.
2. **`run` where `build` was needed** — models materialized, tests never executed, failure surfaces in production.
3. **Deferral misread as a local build** — the numbers came from the state manifest's relations, not yours. Where the CLI defers by default, this needs no mistake on your part; it is what happens unless you built the ancestors.
4. **`--state` pointing at `target/`** — dbt overwrote the comparison manifest during parsing, so the diff is empty and CI concludes nothing changed.
5. **A green CI run that built nothing** — the selector matched no nodes, which is a warning, and warnings exit zero.
6. **`--full-refresh` on the wrong materialization** — a no-op that reads as a completed rebuild, or a full-history scan nobody budgeted for.
7. **`run-operation` against production from an ambiguous request** — no dry run, no confirmation, no undo.
8. **Wrong project segment in a `unique_id`** — indistinguishable from a deleted model in the API response.
