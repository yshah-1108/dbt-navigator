# Failure taxonomy

Reference detail for each failure class. Read [SKILL.md](SKILL.md) first — it holds the diagnostic method, and the method matters more than the catalogue. Use this file to identify a class from the message you are holding, and to know the first command for that class.

The organising fact: **dbt executes in stages, and the stage that failed tells you where the cause can possibly be.** dbt's own error naming follows those stages — a `Runtime Error` at startup cannot be a SQL bug, and a `Database Error` cannot be a YAML bug. Every minute spent looking in the wrong stage is wasted by construction.

| Stage | dbt is doing | Error dbt names it |
|---|---|---|
| Initialise | Finding the project, reading the profile, opening a connection | `Runtime Error` |
| Parse | Reading `.sql` and `.yml` files, rendering first-pass Jinja, resolving `ref()` and `source()` | `Compilation Error` |
| Graph | Building the DAG and checking it is acyclic | `Dependency Error` |
| Execute | Sending statements to the warehouse | `Database Error` |

Two corollaries worth stating, because both are commonly got wrong:

- A failure at Parse means **no SQL ran at all**. Nothing in the warehouse changed; there is nothing to clean up.
- A failure at Execute means the warehouse rejected or aborted a statement dbt built. The message text after dbt's prefix is the warehouse's, not dbt's, and it is the specific part.

## Message fragment → class → first move

Scan for the fragment, not for the wording; adapters phrase these differently.

| Fragment in the message | Class | First move |
|---|---|---|
| "Not a dbt project", "Missing dbt_project.yml" | Initialise | `pwd`; you are in the wrong directory |
| "Could not find profile named" | Initialise | Compare `profile:` in `dbt_project.yml` against the keys in the profile file; `dbt debug --config-dir` locates the file |
| "Failed to connect", "Incorrect username or password", "authentication has expired" | Connection / auth | `dbt debug`. Never a model fix |
| "Additional properties are not allowed ('x' was unexpected)" | Parse — unrecognised key | The key is not in the resource's schema for this dbt version. Check spelling, then `dbt --version` against the docs for that key |
| "mapping values are not allowed in this context", "while scanning for" | Parse — malformed YAML | Indentation, almost always. The line number is accurate |
| "depends on a node named ... which was not found" | Parse — bad `ref()` | The named model file does not exist. Typo, rename, or a deleted model still referenced |
| "Reached EOF without finding a close tag" | Parse — unclosed Jinja | An `{% endif %}`, `{% endfor %}` or `{% endmacro %}` is missing, or the loop and the conditional are closed in the wrong order |
| "'x' is undefined" | Parse — undefined Jinja name | A `var()`, `env_var()`, or macro that does not exist in this target. Often an environment problem, not code |
| "unsupported operand type(s)" | Parse — Jinja type error | Everything from `var()` and `env_var()` arrives as a string. Cast before arithmetic |
| "Found a cycle" | Graph | See *Cycles* below. The message names the loop |
| "depends on ... which is disabled" | Graph / environment | The target is `enabled: false`, frequently per-target. A config problem dressed as a code error |
| "syntax error at or near", "unexpected 'from'" | Execute — SQL syntax | Read the compiled SQL, run it directly |
| "Invalid identifier", "column ... does not exist", "Unrecognized name" | Execute — schema mismatch | Either a typo, or the upstream relation's shape changed. Confirm against `information_schema` before editing |
| "Object ... does not exist or not authorized", "Table not found", "relation ... does not exist" | Execute — missing relation **or** permission | Ambiguous by design on some engines: the same message covers "absent" and "not visible to this role". Resolve which before acting |
| "Insufficient privileges", "Access Denied", "permission denied for" | Execute — grant | Never fixed in the model |
| "column ... is ambiguous" | Execute — ambiguous reference | Two joined relations expose the same column name. Qualify it |
| "Numeric value 'x' is not recognized", "Invalid digit", "could not convert" | Execute — cast failure on real data | A value the cast does not tolerate arrived. Data, not code, unless the cast was always wrong |
| "Statement reached its statement or query timeout", "Query exceeded ... limit", "canceled" | Timeout | See *Timeouts* below |
| "Resources exceeded", "Out of memory", "Disk full", "exceeded ... memory limit" | Resource exhaustion | See *Memory and spill* below |
| "run exceeded ... memory limits" (the dbt process, not the warehouse) | dbt-side memory | A macro pulling a large result back into dbt, or catalog generation over a schema with very many objects |
| "Invalid config version", "Could not find package" | Package / version | `dbt deps`, then check the package's supported dbt range |
| "Compilation Error in macro" | Parse — in the macro, not the model | Compile a second model that calls the same macro. If both fail, the macro is the defect |

## Cycles and ref loops

`Found a cycle` prints the loop, in order, with the closing node repeated:

```text
Found a cycle: model.my_project.orders --> model.my_project.customers --> model.my_project.orders
```

That output is the whole diagnosis: those are the only files that can be at fault. Cycles arise four ways, and the fix differs:

| Cause | Fix |
|---|---|
| Two models `ref()` each other | Extract the logic they share into a third model both reference. Do not "solve" it by pointing one at a `source()` unless the relation genuinely is a source — that hides a real dependency from the DAG, and the build order then depends on luck |
| A long chain closes on itself, usually after a refactor moved one `ref()` | Read the printed chain and find the single edge that is new. `git log -p` on those files finds it faster than reasoning about the graph |
| A model references itself for incremental logic | Use `{{ this }}`, which is not a graph edge. `ref()` to the model's own name is a self-cycle |
| A hook or macro embeds a hard-coded relation name to dodge a cycle | This compiles and is worse than the cycle: the dependency is invisible, so dbt may build the two in either order, and the output silently depends on which ran first. Restructure instead — see `dbt-restructuring-dags` |

Ephemeral models do not escape this. They are inlined as CTEs, but they are still graph nodes, so a cycle through an ephemeral model is still a cycle.

## Test failures

A test failure is not an error in the sense the other classes are: nothing broke. A query ran successfully and returned rows it was asserted would not exist. The count in the message is the number of such rows.

```text
Failure in test unique_orders_order_id (models/marts/_marts.yml)
  Got 639,057 results, configured to fail if != 0
  compiled code at target/compiled/my_project/models/marts/_marts.yml/unique_orders_order_id.sql
```

Two things in that message are load-bearing. **"configured to fail if != 0"** is the test's threshold, which can be configured — so a failure may reflect a threshold rather than an absolute assertion. And the compiled path is a runnable query; the rows are one `select` away. SKILL.md covers deciding whether the test or the data is wrong.

Configuration that changes what a failure means, and is worth reading before interpreting one:

| Config | Effect on interpretation |
|---|---|
| `severity: warn` | The run stays green. A warning is a failure that chose not to stop anything |
| `error_if` / `warn_if` | The failure threshold is not zero. "Got 5 results" may be a pass |
| `fail_calc` | The number reported is not necessarily a row count; it is whatever aggregate was configured |
| `where` | The test only examined a subset. A pass says nothing about the rows excluded |
| `limit` | The stored failures are truncated. The count is real; the stored sample is partial |
| `store_failures` / `store_failures_as` | Whether the failing rows are inspectable after the fact, and as a table or a view |

`store_failures` writes to a separate schema — by convention the target schema with a `_dbt_test__audit` suffix, configurable per test. It requires permission to create that schema, which is a common reason it works locally and not in an automated environment.

## Timeouts

Separate the two, because they have different owners:

- **Warehouse-side**: the engine aborted the statement at a configured limit. The statement is gone; the model is not partially written on engines with atomic DDL, but check on engines without it.
- **Client-side**: dbt or the driver stopped waiting. On some adapters the underlying job keeps running after dbt gives up, so a retry can collide with the still-running original. Confirm in the engine's query history before re-running.

Adapters expose their own settings for both — statement timeouts, job execution timeouts, connect timeouts, connect retries. They live in the connection profile, not in the model. Raising a timeout is a legitimate fix only when the runtime is understood and acceptable; otherwise it converts a fast failure into a slow one. Diagnose the runtime first, with `dbt-performance-tuning`.

A timeout that appears suddenly, with no code change, is usually data volume or contention, not the query. Check the run's concurrency and the engine's queueing before touching SQL.

## Memory and spill

An engine that runs out of working memory does one of three things, and each has a different signature:

| Behaviour | Signature | Engines that do this |
|---|---|---|
| Spill to disk and continue, slowly | No error at all — only a large runtime increase, and spill bytes in query metadata | Snowflake (local then remote spill), Spark and Databricks (shuffle spill) |
| Abort the query | An explicit resource error | BigQuery ("Resources exceeded"), Redshift (disk full) |
| Abort the process | An out-of-memory error naming the process, not a query | Any engine executing in-process, and the dbt process itself |

The first is the dangerous one, because it is not a failure: the model still succeeds, and the only symptom is cost and duration. It is diagnosed from query metadata, not from logs. Where the engine exposes spill bytes per query, a non-trivial spill on a model that used to be fast is the finding.

Neutral first moves, in order, before any engine-specific tuning:

1. Find the operator that needs the memory. Sorts, wide aggregations, window functions over large partitions, and joins that fan out are the candidates — almost never the scan.
2. Ask whether it needs to exist. An `order by` inside a CTE that nothing depends on is a full sort of the dataset for nothing.
3. Reduce what reaches it — filter earlier, aggregate earlier, or process a narrower window.
4. Only then consider giving it more memory. Sizing up hides an accidental cross join rather than fixing it.

Engine-specific diagnostics belong to `dbt-performance-tuning`; gate anything you recommend on the project's declared warehouse.

## Concurrency and lock failures

Symptom: an error about a conflicting write, a lock, a deadlock, or a relation being modified — and it does not reproduce when the model is run alone.

This is an environment class, not a code class. Causes worth checking in order: two runs overlapping (a schedule that outlives its interval, or a person running the same model as the scheduler); two models writing the same relation; a thread count high enough that several nodes contend for the same target table; or an external process writing the table dbt is replacing.

The diagnostic is the run's own timing. `target/run_results.json` records `thread_id` and per-node timing, which is enough to see whether two nodes overlapped. Lowering thread count is a test, not a fix — if it resolves the failure, the real finding is which two nodes contend.

## Which classes are never fixed in the model

Worth stating flatly, because each of these gets "fixed" in SQL regularly, and each such fix is permanent damage:

| Class | Fixed by |
|---|---|
| Connection, auth, profile | The profile or credential store |
| Grant and permission | The role's grants, by whoever owns them |
| Missing relation because it was never built here | Building the ancestor |
| A disabled dependency | The config that disabled it |
| Package or version incompatibility | The package pin |
| Concurrency conflict | Scheduling or thread configuration |
| Warehouse resource limits | Query shape or warehouse sizing — and only after the shape is understood |

If a model edit is being considered for any row in that table, the class was misidentified.
