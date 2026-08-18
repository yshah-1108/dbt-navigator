---
name: dbt-python-models
description: Use when creating or modifying a Python model, deciding whether logic belongs in Python or SQL, debugging a Python model build failure, managing Python package dependencies, or making a Python model incremental. Covers sharply differing warehouse support and why Python models are harder to test.
metadata:
  phase: build
---

# Python models

A Python model is a file whose `model(dbt, session)` function returns a dataframe that dbt materializes as a table. It exists for logic SQL cannot express.

The bar is high, deliberately. A Python model is slower to build, harder to debug, harder to test, less portable, and unavailable on several warehouses. It is right for a narrow set of problems and wrong for everything adjacent to them.

Two facts to have in hand before anything else, because both are absolute rather than matters of degree:

- **dbt unit tests do not support Python models**, and neither do contracts. The model's *output* is testable like any table; its *logic* is not reachable by any dbt testing tool.
- **All code runs remotely.** Nothing executes locally, there is no compiled artefact to read, and `print()` does not reach dbt's logs.

## Sub-documents

- [platform-reference.md](platform-reference.md) — per-platform execution models, dependency mechanisms, submission methods, required profile fields, and the adapters that support **nothing**. Read before writing any platform-specific config.
- [cost-and-decision.md](cost-and-decision.md) — where the cost actually accrues, pandas versus distributed dataframes, when to push work back to SQL, what genuinely needs Python, and the long list of things that look like Python problems and are not.
- [testing-and-debugging.md](testing-and-debugging.md) — exactly which dbt testing tools apply, the pure-function pattern, output assertions worth writing, where logs live per platform, and a table of opaque failures with their usual causes.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

**`project.warehouse` gates this entire skill.** Check it before writing anything — the answer may be that Python models are not available at all.

| `project.warehouse` | Python model support |
|---|---|
| `snowflake` | Yes — compiled to a Python stored procedure and run with Snowpark inside the warehouse |
| `bigquery` | Yes — submitted to Dataproc (serverless or a cluster) as PySpark, or run via BigQuery DataFrames. Needs profile fields SQL models do not |
| `databricks` | Yes — PySpark, the most native fit, with several submission targets |
| `athena` | Yes, via Spark, with substantial documented restrictions — see [platform-reference.md](platform-reference.md) |
| `redshift`, `postgres`, `duckdb`, `trino` | **No.** There is no equivalent. Solve it in SQL, or outside dbt |

The boundary is not arbitrary and knowing why helps: the supported platforms are the ones that provide a mechanism for Python code to run on the platform's own compute. Redshift and Postgres have none, and the feature request for them was closed as not planned — so on those engines the answer is not "not yet" and there is no roadmap to wait for.

If the contract has no `warehouse` field, determine it from `profiles.yml` or ask. Do not write a Python model on an assumption — the runtime differences are not cosmetic, and code is not portable between the platforms that do support it. Support is also version-dependent in both directions; verify against the adapter version the project runs rather than asserting from memory.

`naming` fields apply as they do to SQL models. If the project marks Python models with a distinct prefix, follow it; if the contract is silent, follow observed convention and say so.

## When a Python model is justified

| Python | SQL |
|---|---|
| Statistical modeling — regression, hypothesis tests, influence measures | Aggregation, joins, window functions |
| Machine learning — training, scoring, feature scaling | Business rules, `case` logic, filtering |
| Iterative algorithms — graph traversal, simulation, convergence loops | Set-based operations |
| Parsing beyond the dialect's regex support | Standard string manipulation |
| A library with no SQL equivalent | Anything the warehouse does natively |
| API enrichment, where the platform supports outbound calls | Anything answerable from data already in the warehouse |

**The test: can the logic be written in SQL at all?** If yes, write SQL — even if the SQL is longer. Python's cost is not typing time, it is the debugging and testing tax paid on every future change, by whoever is on call.

API enrichment deserves a caveat in its own right, because it is the justified case with the largest hidden cost. It requires platform support for outbound network access, and it makes the model's success depend on a third party's availability, rate limits and schema. dbt's own commentary on the feature is that SQL is resistant to external entropy and this deliberately opens the pipeline to it. Often the right call — and it should be a deliberate one with a stated failure plan, not a convenience.

Things that look like Python problems and are not:

| Instinct | Actually |
|---|---|
| Pivot or unpivot | SQL, or a utilities-package macro |
| Ranking, running totals, lag/lead | window functions, and far faster |
| Percentiles, correlation, stddev, regression slope and intercept | most warehouses have these built in — check first |
| Approximate distinct counts and quantiles | built-in on most warehouses, and dramatically cheaper |
| Date arithmetic and bucketing | SQL date functions |
| Deduplication | `row_number()` in a subquery, or a qualifying clause where the dialect has one |
| Fuzzy matching, edit distance, phonetic keys | often a warehouse function |
| JSON or semi-structured parsing | native on most modern warehouses |
| Generating a date spine | SQL, or a utilities-package macro |
| Recursive hierarchy flattening | a recursive CTE where the dialect supports it |

Check the warehouse's function list before concluding SQL cannot do it. Statistical aggregates in particular are more widely supported than people expect, and a built-in aggregate beats pulling a table into a Python runtime by a wide margin.

**Avoid making a Python model a mid-DAG dependency.** Every downstream model then waits on the slowest, most fragile node in the graph, and a Python failure becomes a failure of everything behind it. Push Python toward the leaves: aggregate in SQL, run the Python step on the small result. There is a second reason less often stated — a Python model can carry no contract and no unit test, so a mid-DAG Python model is an untestable, unconstrained interface that many models depend on. That is the position in a DAG where you least want those properties.

Full treatment of the decision, the cost breakdown, and how to write the justification into a PR: [cost-and-decision.md](cost-and-decision.md).

## Structure

```python
def model(dbt, session):
    dbt.config(
        materialized="table",
        packages=["pandas", "statsmodels"],
    )

    df = dbt.ref("upstream_model").to_pandas()

    return compute(df)
```

Four required properties: a function named exactly `model`, taking `dbt` and `session`, returning a dataframe. There is no other entry point, and the return value is the only output — a Python model cannot write a relation as a side effect and have dbt know about it.

| Call | Purpose | The part people get wrong |
|---|---|---|
| `dbt.config(...)` | Configuration | Must be inside `model()`. Accepts only literals and simple types — it cannot compute a value from the data, because it is read to build the DAG before anything runs |
| `dbt.ref("<model>")` | Upstream model as a dataframe | Only registers a dependency when called inside `model()` |
| `dbt.source("<src>", "<tbl>")` | Source table as a dataframe | Same |
| `dbt.this` | The current model's relation | Valid on a first full build too, where the relation may not exist yet |
| `dbt.is_incremental` | True when running incrementally | **A property, not a method** — `dbt.is_incremental()` is truthy always, so the guard silently never triggers a full rebuild path |

`dbt.ref()` is what builds the DAG, and it only works inside `model()`. Calling it from a helper function or at module level does not register the dependency, so dbt will not know to build the upstream model first. That failure is intermittent by nature: it works whenever the upstream happens to be fresh.

`dbt.is_incremental` deserves the emphasis above. It is exactly the mistake that produces a model which appears to work — the incremental branch runs on every build, including the first — and it produces wrong results rather than an error. It is also all it gives you: **it tells you which mode dbt is in and nothing about what the target relation contains.** There is no equivalent of SQL's `is_incremental()` combined with a compiled `where` clause; you have to query `dbt.this` yourself, and handle the case where it is empty.

There is no Jinja in a Python model — no `{{ ref() }}`, no `{{ config() }}`, no macros, no `var()`. Anything a macro did for your SQL models must be reimplemented in Python, and **code cannot be imported from another dbt Python model**: each is self-contained. Sharing logic across Python models means packaging it and installing it in the remote runtime, which is real infrastructure work rather than a refactor.

### The `session` object

The platform-specific session object — Snowpark on one platform, a Spark session on others — available for operations the `dbt` object does not cover: issuing SQL directly, reading a relation dbt does not know about, or looking up an incremental boundary.

**Using it is the least portable thing in the file.** It must be rewritten if the platform changes, and it bypasses the DAG: a table read through `session.sql()` is not a dependency, so dbt may build this model before that table is refreshed — a failure that appears only when timing is unlucky. Prefer `dbt.ref()` and `dbt.source()`. The one legitimate use is querying `dbt.this` for an incremental boundary, where there is no alternative.

### Dataframe flavors

Every supported warehouse offers a lazy dataframe whose operations push down to the engine, plus a conversion to a local pandas dataframe. The choice is consequential.

| | Native (pushed down) | pandas |
|---|---|---|
| Executes | in the warehouse or cluster engine | in a single Python process |
| Data volume | large | bounded by that process's memory |
| API | narrower | complete |
| Fails by | unsupported operation | out-of-memory, often near a deadline |

`.to_pandas()` is a materialization boundary: **every row crosses into one process's memory.** Correct for a library that requires a local dataframe — most statistical and ML libraries do — and wrong for reshaping data at scale.

The asymmetry in the last row is the argument for preferring native operations: a native dataframe fails **early and loudly** when it cannot express something, and pandas fails **late and situationally**, at whatever future volume exceeds the node. Prefer the failure you find while writing the code.

Reduce before converting. Aggregate, filter, and select columns upstream in SQL or with native dataframe operations, so the conversion handles the smallest dataframe that still answers the question. A Python model that pulls a wide fact table and immediately groups it should have grouped it in SQL.

One platform-specific nuance worth checking rather than assuming: a pandas-on-Spark API exists that covers most of the pandas surface while still executing in parallel, so on some platforms pandas *syntax* need not mean single-node execution. Establish which API is in use before advising either way — see [cost-and-decision.md](cost-and-decision.md).

## Configuration

```python
dbt.config(
    materialized="table",
    packages=["pandas", "numpy", "scikit-learn"],
)
```

`view` is not available — a Python model is `table` or `incremental` only. Config may also live in `dbt_project.yml` or a schema YAML entry, which is preferable for anything a reader of the project structure should see without opening the Python file.

Also unavailable, and worth knowing because each removes a mechanism another skill recommends:

| Not supported on a Python model | Consequence |
|---|---|
| `materialized: view` and `ephemeral` | The cheap materializations are off the table; every Python model is a physical relation |
| Contracts and `constraints` | The shape-enforcement mechanism `dbt-breaking-changes` relies on for an interface is unavailable. Data tests plus review are the substitute — say so rather than let a reader assume otherwise |
| dbt unit tests | See [testing-and-debugging.md](testing-and-debugging.md) |
| `--empty` | Documented as ignored for Python models, so the zero-row dry run that answers "does this build" for SQL models buys nothing here |
| Snapshots in Python | Snapshots are SQL-only |

### Packages

Dependency resolution differs by platform, and this is the most common source of "works locally, fails in the warehouse". Full detail in [platform-reference.md](platform-reference.md); the shape:

| Platform | Mechanism | Watch for |
|---|---|---|
| Snowflake | Resolved from the curated Anaconda channel via a `packages` list | An account-level terms acknowledgement is required before **any** third-party package can be used — and without it `to_pandas()` is unavailable, with an error that does not say why. A package outside the channel requires uploading it to a stage and referencing it through `imports` |
| BigQuery | The Spark runtime's environment | Third-party packages on serverless Dataproc mean a **custom container image** in a registry, which is infrastructure with its own release cycle — not a line in `packages` |
| Databricks | Cluster libraries or in-job installation | Library sets and Python versions drift between clusters, so the same model can produce different numbers on different compute with no code change |

Two rules hold everywhere:

- **Verify the package is available in the target runtime before writing code against it.** On some platforms an unavailable package is not an inconvenience but a hard blocker whose only remedy is infrastructure work or a redesign. Establishing availability costs one query; discovering it after the model is written costs the model.
- **Pin versions where the mechanism allows it.** A statistical library that changes a default between versions changes your outputs with no code change and no signal — the failure that looks like a data problem and is not.

There is no arbitrary `pip install` at runtime on any of these platforms. Where an environment appears to allow one, it is the compute's own configuration doing it, not the model.

## Column naming

The returned dataframe's column names become the table's column names, and warehouses differ on case folding — some uppercase unquoted identifiers, Spark-based runtimes generally preserve what you give them. The consequence is that a Python model's columns can arrive in a different case from every SQL model in the project, and downstream SQL then fails or needs quoting.

Normalize explicitly before returning, to whatever the surrounding project uses:

```python
df.columns = [c.lower() for c in df.columns]
return df
```

Also ensure every column is a simple scalar type. A column holding a list, dict, missing-date sentinel, or library-specific object either fails to serialize or lands as an unusable string. Nullable integer and categorical dtypes are frequent offenders — cast them before returning.

## Incremental Python models

Supported, with a caveat worth stating plainly: **the incremental machinery is thinner than for SQL models.** dbt does not build a `where` clause for you and there is no `is_incremental()` macro rendering into your query — the filtering is code you write, against the target relation you query yourself. Available strategies also vary by adapter, and at least one platform does **not** support `insert_overwrite` for Python models even though it supports it for SQL. Do not assume `merge` semantics; verify what your adapter does.

```python
def model(dbt, session):
    dbt.config(materialized="incremental", unique_key="<key_column>")

    df = dbt.ref("<upstream_model>")

    if dbt.is_incremental:
        max_loaded = session.sql(
            f"select max(<timestamp_column>) from {dbt.this}"
        ).collect()[0][0]
        if max_loaded is not None:
            df = df.filter(df["<timestamp_column>"] >= max_loaded)

    return df.to_pandas()
```

Five details, in order of how often each one bites:

- **`dbt.is_incremental` is a property, not a method.** `if dbt.is_incremental():` is truthy on every run including the first, so the full-load path never executes and the model queries a relation that may not exist. It produces a confusing error on a fresh build and, worse, no error at all where `dbt.this` happens to exist.
- **`>=` not `>`** on an incremental boundary — rows arriving at the boundary timestamp are otherwise dropped. Universal rule; see `dbt-incremental-models`.
- **Handle the null maximum.** On the first incremental run after a refresh the table may be empty, and comparing against null filters everything out. The model succeeds and writes nothing, which looks like an upstream problem.
- **Querying `dbt.this` is a second round trip, and it is not a dependency.** It reads the relation dbt is about to write. That is legitimate here and it is the one place `session.sql()` is hard to avoid — but keep it to the boundary lookup, not to reading data.
- **Verify what the adapter actually did.** Count rows before and after, and confirm whether existing rows were replaced or duplicated. The config declaring a `unique_key` is not evidence that a merge occurred.

Note also that deleting rows from an incremental Python model, or any incremental model, can move the boundary computed from `max(...)` — see the deletion mechanics in `dbt-handling-sensitive-data`.

Where the incremental logic is the hard part, a defensible split is a Python model computing on a bounded window as a `table`, plus a thin SQL incremental model downstream handling the merge. SQL's incremental behavior is better specified, and it keeps the fragile node out of the business of reasoning about history.

## Why testing and debugging are harder

Data tests apply normally — a Python model is a table like any other, so generic tests, singular tests, and test packages all work against it. That is the easy half.

The hard half is that **the logic itself is not reachable by any dbt testing tool.** dbt unit tests support SQL models only; contracts are unsupported; `--empty` is ignored; there is no compiled artifact to read, so the technique that resolves most SQL model bugs is unavailable. And the code runs in a remote runtime you do not fully control, so a stack trace may reflect a version difference rather than your logic.

Full treatment — the exact support matrix, output assertions worth writing, per-platform log destinations, and a table of opaque failures with their usual causes — in [testing-and-debugging.md](testing-and-debugging.md). The four techniques that substitute for what is missing:

1. **Keep `model()` thin.** Configure, read, delegate — nothing else. Pure functions taking and returning dataframes are testable with an ordinary Python test runner, locally, in seconds: the only fast feedback loop available here. Note that dbt does not run those tests; something in CI has to invoke them.
2. **Assert hard on the output.** Since the computation is unreachable, test its results: value ranges on every computed numeric column, expected null rates, row count relative to the input, the grain, and the cardinality of any classification produced. A model that flags outliers should be tested to flag neither zero rows nor most of them — assert both directions, because "not zero" passes when it flags everything.

```yaml
models:
  - name: <python_model>
    columns:
      - name: <key_column>
        data_tests:
          - unique
          - not_null
      - name: <category_column>
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['<value_a>', '<value_b>']
      - name: <score_column>
        data_tests:
          - dbt_expectations.expect_column_values_to_be_between:
              min_value: 0
              max_value: 1
```

3. **Guard the degenerate input case in code, with a reason.** Statistical functions on too few rows return garbage or raise, and which one is a library implementation detail. Carry a reason column so a null is a documented outcome rather than an indistinguishable bug:

```python
MIN_ROWS = 20

def flag_outliers(group):
    if len(group) < MIN_ROWS:
        group["is_outlier"] = False
        group["outlier_reason"] = "insufficient_rows"
        return group
    ...
```

This case is more common than it sounds because of *when* it appears: development runs on a filtered window where groups are small and production runs on full history where they are not, or a new low-volume segment appears months later.

4. **Reproduce outside dbt when a build fails.** A standalone script connecting to the same platform and running the same helper functions on the same input gives real tracebacks and an interactive debugger — usually faster than iterating through `dbt build`, and the only way to get a breakpoint.

```bash
dbt --debug build --select <python_model>   # full remote error output
```

**`print()` does not reach dbt's logs.** The platform runs the code without dbt's oversight, so standard output is not captured. dbt's documented workaround is to write the value into a column of the returned dataframe — which works, and requires the table to build, so it fails exactly when you most need it. Otherwise expect output in the platform's own job logs.

## Governance and reviewability

Two costs that appear in no log and belong in the PR rather than being discovered later:

- **A Python model cannot carry a contract**, so it cannot participate in the shape-enforcement mechanism `dbt-breaking-changes` recommends for a published interface. If downstream consumers depend on its columns, the guarantee is data tests plus review — state that rather than let a reader assume a contract covers it.
- **A Python model is reviewable by fewer people.** dbt's own guidance is that where a transformation could be written equally well in either language, well-written SQL is preferable because it is accessible to more colleagues. A Python model only its author can review is a model that stops being maintained when that person moves on — which is a stronger argument for thin `model()` functions than any testing consideration.

Two more, where the model touches regulated data or an external service:

- **The compute may be in a different region from the data.** On the platforms that submit to a separate compute service, the region is configured independently, so a Python model can move data across a boundary every SQL model in the project respects. And on one platform dbt uploads the compiled code to object storage, making that bucket part of the exposure surface. See `dbt-handling-sensitive-data`.
- **A scored output that drives a decision about a person** can attract obligations of its own. Recognise it and route it; do not adjudicate it.

## Completion checklist

- [ ] `project.warehouse` checked and confirmed to support Python models, at the adapter version in use
- [ ] SQL alternative considered explicitly, including built-in warehouse functions and any utilities package, and rejected with a stated reason in the PR
- [ ] Model is `table` or `incremental`, never `view` or `ephemeral`
- [ ] Platform-specific config (submission method, compute, required profile fields) verified against the adapter's own documentation
- [ ] `dbt.ref()` / `dbt.source()` used for every dependency, called inside `model()`; nothing read via `session.sql()` that should be a dependency
- [ ] `dbt.is_incremental` used as a property, not called as a method
- [ ] Data volume reduced before `.to_pandas()`
- [ ] Packages verified available in the target runtime, versions pinned where the mechanism allows it
- [ ] Any account-level or image-level prerequisite for third-party packages confirmed, not assumed
- [ ] Column names normalized to the project's case convention
- [ ] All returned columns are simple scalar types
- [ ] Degenerate-input case handled in code, with a reason recorded rather than a bare null
- [ ] Computation extracted into pure functions covered by local Python tests, and something in CI actually runs them
- [ ] Output assertions on ranges, nulls, grain, and row count relative to input in schema YAML
- [ ] Incremental behavior verified with row counts against the adapter, not assumed from the config
- [ ] Positioned near a DAG leaf, not as a dependency of many models
- [ ] Absence of contract support stated where the model is a consumed interface
- [ ] Region and any external network dependency named where the model touches sensitive data or an outside service

## The failure modes that cost the most

1. **Python chosen for something SQL does natively.** Usually a statistical aggregate the warehouse already has. Slower, unreviewable by half the team, and it will be rewritten by someone annoyed about it.
2. **Writing a Python model for a platform that has none.** On Redshift, Postgres, DuckDB and Trino there is no mechanism and no roadmap. Time spent designing one is wasted before the first line.
3. **`.to_pandas()` on a large relation.** Works in dev on a filtered window, out-of-memory in production on full history. Fails intermittently as data grows — the worst kind of failure to schedule around.
4. **Calling `dbt.is_incremental()` instead of reading the property.** Always truthy, so the full-load path never runs. Produces wrong results rather than an error.
5. **A package that exists locally and not in the runtime.** Or a different version, which is worse: no error, different numbers. On some platforms the remedy is infrastructure work, so this is a design blocker discovered after the design.
6. **Assuming a platform prerequisite is in place.** An unaccepted account-level package term, a container image that was never built, a cluster that no longer exists. The error names the compute, not the missing prerequisite.
7. **Column case mismatch.** The model builds, and every downstream SQL reference fails or silently reads nothing.
8. **Assumed incremental semantics.** Expecting a merge, getting a rebuild or duplicates, or reaching for a strategy the platform does not support for Python. Verify with row counts rather than trusting the config.
9. **Untestable logic in `model()`.** All computation inline, so testing a one-line change means a full remote build. This is what makes people stop maintaining a Python model.
10. **Debugging the logic when the environment is wrong.** A missing attribute or a type error is frequently a version difference in the remote runtime. Confirm installed versions before reading the code.
11. **Putting a Python model mid-DAG.** Everything downstream inherits its fragility and its build time, and it becomes an untestable, contract-free interface that many models depend on.
12. **Treating an API call inside a model as a transformation.** It is an integration: the build now fails for reasons outside the warehouse, on a schedule, at someone else's discretion.
