# Platform reference for Python models

`project.warehouse` gates everything here, and the first possible answer is that Python models are not available at all. Nothing in this file is portable: the runtime, the dataframe API, the dependency mechanism, the submission model and the log destination all differ, and code written against one platform does not run on another.

**Support and configuration change frequently.** Every table below is a starting point to verify against the adapter version the project actually runs, not a fact to assert. Where a config name matters, `dbt --version` and the adapter's own configuration page are the authority — not this document and not a memory of how it worked.

## Which platforms support Python models

| Adapter | Support | Execution model |
|---|---|---|
| Snowflake | Yes | Compiled to a Python **stored procedure** and executed with Snowpark inside the warehouse |
| BigQuery | Yes | Submitted to Dataproc (Serverless or an existing cluster) as PySpark, or executed via BigQuery DataFrames |
| Databricks | Yes | PySpark, submitted to one of several compute targets |
| Athena | Yes, via Spark, with substantial documented caveats | Spark on Athena |
| **Redshift** | **No** | — |
| **Postgres** | **No** | — |
| **DuckDB** | **No** | — |
| **Trino** | **No** | — |
| Some Spark-based community adapters | Varies; at least one explicitly declines to support Python models | — |

dbt Labs has stated the reason for the boundary, and it is the useful way to remember it: the supported platforms are the ones that provide **a mechanism for Python code to use the platform's own compute**. Redshift and Postgres have no such mechanism, and the request to add Python models to them was closed as not planned. If someone asks for a Python model on one of those, the answer is not "not yet" — it is **solve it in SQL, or solve it outside dbt**, and there is no roadmap to wait for.

Where a project runs an adapter not listed here, check its configuration page before answering. An adapter that supports Python models advertises it prominently; silence means no.

---

## Snowflake

**Execution model.** dbt wraps the model into a Python stored procedure and calls it. Everything runs in the warehouse; nothing runs locally. The practical consequences: the code is subject to the warehouse's Python version support rather than the local interpreter's, and the failure surface is a stored-procedure error rather than a local traceback.

**Dependencies come from a curated channel, not from PyPI at runtime.** Packages resolve from the Anaconda channel Snowflake integrates. Three things follow:

- **An account-level term must be acknowledged before any third-party package can be used.** This is an administrator action in the account's terms settings, not a code change. Without it: no third-party packages at all; Snowpark itself can be named but not version-pinned; and — the one that catches people — **`to_pandas()` is unavailable**. A model that converts to pandas fails on an account where nobody accepted the terms, and the error does not say "accept the terms".
- **A package outside the channel is a hard blocker, not an inconvenience.** There is no arbitrary `pip install` at runtime. The documented route for an unavailable package is to upload it to a stage and reference it through the `imports` config, which is a real piece of infrastructure work. **Establish availability before writing code against a library**, because the alternative to availability is a redesign.
- Pin versions in `packages`. dbt tracks them in project metadata, and a statistical library that changes a default between versions changes your outputs with no code change and no signal.

**Configs worth knowing:**

| Config | What it does |
|---|---|
| `packages` | The Anaconda packages to install for this model. Different models may have different sets |
| `imports` | References a staged file, which is the route for a package the channel does not carry |
| `snowflake_warehouse` | Overrides the warehouse for this model. Snowflake's own recommendation is a **dedicated warehouse** for Python models using third-party packages, rather than one shared with many concurrent users |
| `external_access_integrations` and `secrets` | Permit outbound network calls from inside the model, and supply credentials for them. Requires a network rule and an integration created in Snowflake first |

**External access is the sharpest edge here.** It is what makes API enrichment inside a model possible, and dbt's own commentary on the feature is worth repeating: SQL and dbt are resistant to external entropy, and the moment a model depends on an API it inherits that API's availability, rate limits, schema changes and latency. A model that calls an API is a model that fails for reasons outside your warehouse, on a schedule. Treat it as an integration with an SLA, not as a transformation.

**One documented limitation to plan around:** complex *named* UDFs cannot be registered inside a stored procedure, so they cannot be registered inside a dbt Python model. Anonymous UDFs work. For a vectorised UDF the documented workarounds are creating the function in a SQL macro run as a hook or operation, or registering from a staged file.

---

## BigQuery

**Three submission methods**, and which one is in play changes almost everything about the model:

| `submission_method` | What runs it | What it needs |
|---|---|---|
| `serverless` | Dataproc Serverless — PySpark, no cluster to manage | `gcs_bucket` and `dataproc_region` in the profile |
| `cluster` | An existing Dataproc cluster | `dataproc_cluster_name`, plus the bucket and region |
| `bigframes` | BigQuery DataFrames — no Spark setup at all | Newer; verify availability in the adapter version |

`gcs_bucket` and `dataproc_region` are **required in the profile** for the Dataproc methods — the adapter raises an error naming the missing one rather than failing mysteriously, which is the one friendly failure in this area. Note what that bucket is for: dbt uploads the model's compiled PySpark code to it. **The compiled code lands in object storage**, so its access controls are part of the model's exposure surface.

Three consequences people get wrong:

- **The Dataproc methods are PySpark, not pandas.** Code written against pandas idioms on a large relation will work in development on a filtered window and behave very differently at volume. The `bigframes` method changes this picture substantially; establish which method the project uses before advising on dataframe style.
- **`insert_overwrite` is not supported for incremental Python models**, though `merge` is. A project standardised on `insert_overwrite` for its partitioned SQL models cannot use that strategy here, which is a design constraint rather than a config detail.
- **Third-party packages on Dataproc Serverless are an image problem.** Google's recommendation is a custom container image hosted in a registry, referenced through `dataproc_batch.runtime_config.container_image`. That is infrastructure with its own build and release cycle — not a line in `packages`.

`dataproc_batch` accepts arbitrary batch configuration passed straight through to the platform's batch object: service account, subnetwork, labels, executor instances, driver memory. It is powerful and unvalidated by dbt, so a structural mistake surfaces at submission time. A `timeout` config exists and matters for long-running work, since without one the model inherits the execution environment's default.

**Region is a governance concern, not just a config.** The compute region is configured separately from the dataset, so a Python model can move data across a boundary that every SQL model in the project respects. See `dbt-handling-sensitive-data`.

---

## Databricks

The most native fit — PySpark on a Spark platform — and the one with the most submission choices:

| `submission_method` | Compute | When it fits |
|---|---|---|
| `all_purpose_cluster` | An existing interactive cluster | Development, and shared-cluster environments. Needs `http_path` or `cluster_id` |
| `job_cluster` | An ephemeral cluster created and torn down per model | Longer-running production models. Needs `job_cluster_config`. Slower to start and stop, cheaper to run |
| `serverless_cluster` | Serverless compute | Fast start, nothing to manage |
| `workflow_job` | A persistent, reusable workflow | Scheduling, access-control lists, multi-task orchestration with pre- and post-hook tasks |

`create_notebook` applies to `all_purpose_cluster` only and is worth knowing for debugging: `false` (the default) submits through the command API, while `true` uploads the compiled code as a notebook in a shared namespace and runs it as a one-off job. **The notebook is the artefact you can open and read**, which makes it the fastest route to understanding what dbt actually submitted. Note that it lands in a shared location by default — a consideration if the model touches sensitive data.

Two properties that decide whether a Python model works here at all, both about the compute rather than the code:

- **Access mode and runtime version interact with governance features.** Reading a table protected by Unity Catalog row filters or column masks has real runtime and access-mode requirements, and older runtimes fail *securely* by returning no data rather than erroring. A Python model reading a governed table on the wrong compute can produce an empty result that looks like an upstream problem. Details in `dbt-handling-sensitive-data`.
- **Cluster configuration drift is a correctness risk.** Library sets and Python versions differ between clusters, so the same model can produce different numbers on a different cluster with no code change. Pin what the platform lets you pin, and prefer a submission method that fixes the environment (`job_cluster` with an explicit config, or serverless) over one that inherits whatever a shared cluster currently has.

---

## Athena

Supported via Spark, with caveats worth reading before committing:

- Python models **cannot reference the platform's SQL views**.
- Third-party libraries must be in the pre-installed set or imported manually.
- Referenced and written table names must match `^[0-9a-zA-Z_]+$` — no dashes or special characters, even where the SQL engine tolerates them. A project whose naming convention includes a dash cannot be referenced from a Python model at all.
- Incremental models do not fully use Spark; they depend partly on SQL-based logic running on the query engine.
- Snapshots are not supported.
- Spark can only reference tables in the same catalog.

Treat this as a working but constrained implementation. Verify each of the above against the current adapter release rather than assuming the list is either complete or still accurate.

---

## Cross-platform comparison

The table that matters when someone asks whether a Python model will behave the same way somewhere else. The answer is no, and here is where:

| Dimension | Snowflake | BigQuery | Databricks |
|---|---|---|---|
| Runtime | Warehouse Python via a stored procedure | Dataproc Spark, or BigQuery DataFrames | Spark |
| Dataframe returned | Snowpark or pandas | BigFrames, pandas, or Spark | Spark, pandas, or pandas-on-Spark |
| Dependencies | Curated channel; staged files for the rest | Custom container image, or the cluster's environment | Cluster libraries or in-job installation |
| Arbitrary `pip` at runtime | No | Via a custom image, which is not runtime | Depends on the compute |
| Compute chosen per model | `snowflake_warehouse` | `submission_method` and batch config | `submission_method` and cluster config |
| Outbound network calls | External access integration plus secrets | The compute's own networking | The cluster's own networking |
| Identifier case of returned columns | Typically upper-cased if unquoted | Varies by method | Generally preserved as given |
| Incremental strategies | Adapter-dependent | `merge`; **not** `insert_overwrite` | Adapter-dependent, more strategies available |

The case row is the one that produces the most confusing failure: a Python model's columns can arrive in a different case from every SQL model in the project, so downstream SQL fails or, worse, needs quoting that nobody else in the project uses. Normalise explicitly before returning, and see the column-naming section in `SKILL.md`.

---

## What to do when the platform is unknown

Establish it before writing anything:

```bash
grep -rn "type:" profiles.yml ~/.dbt/profiles.yml 2>/dev/null
grep -rn "dbt-" requirements.txt pyproject.toml 2>/dev/null
dbt debug 2>&1 | grep -i adapter
```

If it cannot be established, **say you are withholding platform-specific guidance and ask which platform the project runs on.** A Python model written for the wrong platform is not a portability annoyance: the dataframe API, the dependency mechanism and the session object are all different, so the model does not run at all. That is at least an honest failure — the dishonest one is confidently recommending a `packages` list for a platform that resolves packages from a container image.
