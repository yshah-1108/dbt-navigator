# Testing and debugging Python models

The feedback loop is the whole problem. A SQL model can be compiled locally in under a second and read as text; a Python model can only be run remotely, produces no readable compiled artefact, and reports failure through the platform's own error channel. Everything below exists to buy back some of that loop.

---

## What dbt's testing tools do and do not cover

| Tool | Works on a Python model? | Why |
|---|---|---|
| Generic data tests (`unique`, `not_null`, `accepted_values`, and package tests) | **Yes, fully** | They run as SQL against the resulting relation, which is an ordinary table |
| Singular data tests | **Yes** | Same reason |
| **Unit tests** | **No** | dbt unit tests support SQL models only. Not a version gap to wait out |
| Contracts and `constraints` | **No** | Contracts are not supported on Python models |
| `--empty` for a schema-only dry run | **No** | The flag is documented as ignored for Python models |
| `dbt compile` producing readable SQL to inspect | **No** | There is no compiled query. The technique that resolves most SQL model bugs is unavailable |

So the split is clean and worth stating to anyone who asks whether a Python model is tested: **its output is testable exactly like any other table; its logic is not reachable by any dbt testing tool.** That asymmetry is what makes the two techniques below not optional extras but the entire strategy.

Note also that a Python model does not accept a contract, which means it cannot participate in the shape-enforcement mechanism `dbt-breaking-changes` recommends for interfaces. If a Python model is a published interface, the shape guarantee has to come from data tests plus review, and you should say so rather than let a reader assume a contract covers it.

---

## 1. Keep `model()` thin, and test the pure functions

This is the highest-leverage habit in this skill, and the reason is arithmetic: a change tested through `dbt build` costs a remote submission, cluster or warehouse start-up, and a full data read — minutes, and money. A change tested through a local test runner costs milliseconds.

The shape:

```python
# The computation. No dbt, no session, no I/O. Ordinary Python.
MIN_ROWS = 20

def flag_outliers(df, value_column):
    if len(df) < MIN_ROWS:
        df["is_outlier"] = False
        return df
    ...
    return df


def model(dbt, session):
    dbt.config(materialized="table", packages=["pandas"])
    df = dbt.ref("<upstream_model>").to_pandas()
    return flag_outliers(df, "<value_column>")
```

`model()` does three things and no more: configure, read, delegate. Everything else lives in functions that take and return dataframes.

Then test those functions with the project's ordinary Python test runner, on small hand-built frames, locally, in seconds. Cover the cases that actually break: an empty frame, a single row, all-identical values, nulls in the value column, and a group below whatever minimum the statistics require.

Two caveats to state honestly rather than discover:

- **dbt does not run these tests.** They are Python tests in the repository, and something in CI has to invoke them. A pure function with no test runner wired up is untested code that merely looks testable.
- **A function cannot be imported from another dbt Python model.** Code reuse across Python models is not supported; each model is self-contained. So "extract it into a shared module" works only if that module is importable in the *remote* runtime, which usually means packaging and installing it — real work. Within a single model, extraction costs nothing and is always right.

---

## 2. Assert hard on the output

Since the computation is unreachable, test its results. This is where a Python model's schema YAML should be *denser* than a SQL model's, not sparser:

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

The assertions that catch real Python-model failures, as opposed to generic data problems:

- **Range bounds on every computed numeric column.** A statistical function on degenerate input returns something — often infinity, a null, or zero — and a bound is what turns that into a failure instead of a number in a report.
- **Null rate.** A library that silently drops or nulls rows it cannot handle produces a plausible-looking result with a changed null rate and nothing else different.
- **Cardinality of a classification.** A model that flags outliers should flag neither zero rows nor most of them. Assert both directions; "not zero" alone passes when the model flags everything.
- **Row count relative to the input.** A dataframe operation that fans out or drops rows is one of the easiest Python mistakes to make and one of the hardest to see, because the output is still a plausible table.
- **The grain.** A `unique` test on the key is the single most valuable test on a Python model, because a groupby or a merge that changed the grain is silent otherwise.

---

## 3. Guard degenerate input in code

Statistical functions on too few rows return garbage or raise. Which one they do is a library implementation detail, and neither is a good outcome in a scheduled build. Handle it explicitly:

```python
MIN_ROWS = 20

def score_group(group):
    if len(group) < MIN_ROWS:
        group["score"] = None
        group["score_reason"] = "insufficient_rows"
        return group
    ...
```

The second column is the part people skip and shouldn't. A null with no reason is indistinguishable from a bug; a null with a reason is a documented outcome someone can test and a consumer can filter on. It also converts a support question into a lookup.

This case is more common than it sounds because of *when* it appears: development runs on a filtered window, where groups are small, and production runs on full history, where they are not — or the reverse, where a new low-volume segment appears months later and the model has never seen a group that size.

---

## 4. Debugging: where the information actually is

| Platform | Where output and errors go | How to get more |
|---|---|---|
| Snowflake | The stored procedure's error surfaces through dbt; deeper detail is in the platform's query and procedure history | `dbt --debug build --select <model>` for the full remote error |
| BigQuery / Dataproc | The batch or cluster job's own logs, in the platform's logging service. The compiled PySpark also sits in the configured storage bucket | Read the submitted code from the bucket; inspect the batch's logs by its id |
| Databricks | The job run's output. With `create_notebook: true`, the uploaded notebook is a readable artefact you can open and run | Open the notebook dbt uploaded — it is exactly what was executed |

Three things to know before starting:

- **`print()` does not reach dbt's logs.** The platform runs the code without dbt's oversight, so standard output is not captured into the run output. dbt's own documented workaround is to write the message into a column of the returned dataframe — which works, is visible, and fails exactly when you need it most, because it requires the table to build. Use it for a value you need to see, not as a general logging strategy.
- **A stack trace may reflect the environment rather than your logic.** A missing attribute, a changed default, or a type error can all be a version difference in the remote runtime rather than a bug in the code. Before debugging the logic, confirm the package versions actually installed.
- **Reproduce outside dbt.** A standalone script that connects to the same platform and calls the same helper functions on the same input gives a real traceback and an interactive debugger. For anything more than a one-line fix this is faster than iterating through `dbt build`, and it is the only way to get a breakpoint.

### Opaque failures and what they usually mean

The failures that waste the most time, and their usual causes:

| Symptom | Usual cause |
|---|---|
| A serialisation or type error when returning | A column holding a list, dict, missing-date sentinel, or library object. Nullable-integer and categorical dtypes are frequent offenders |
| Out-of-memory, only sometimes | `.to_pandas()` on a relation that has grown. Passed in development on a filtered window |
| The model builds; downstream SQL finds no such column | Column case mismatch. See `SKILL.md` |
| A package import fails at build time | Not available in the remote runtime, whatever the local environment has |
| Numbers changed with no code change | A package version changed. The reason to pin |
| `to_pandas()` unavailable on Snowflake | The account has not accepted the third-party package terms. See [platform-reference.md](platform-reference.md) |
| An empty result from a governed upstream table on Databricks | Row filters or column masks on compute that fails securely rather than erroring. See `dbt-handling-sensitive-data` |
| The model builds fine and rows duplicated | Assumed incremental semantics. Verify with counts, per `SKILL.md` |
| A permission or configuration error naming the compute rather than the data | A missing profile field, a cluster that no longer exists, or a service account without access to the code bucket |

The pattern across most of that table: **the error names the layer it failed in, which is usually not the layer that is wrong.** A submission error names the compute; a serialisation error names a type; neither names the logic. Read the error for *where* execution stopped, then look one step upstream of it.

---

## 5. Reviewability

Worth stating because it is a real cost that does not appear in any log.

A SQL model can be reviewed by anyone on a data team. A Python model can be reviewed by whoever reads Python — usually fewer people, and sometimes only its author. dbt's own guidance is unambiguous: where a transformation could be written equally well in either, well-written SQL is preferable because it is accessible to more colleagues.

That has two practical consequences for a change:

- **Explain the choice in the PR.** Not "this needs Python" but what specifically cannot be expressed in SQL, and what was checked before concluding that — including the warehouse's own function list. See the decision guidance in `SKILL.md`.
- **A Python model that only its author can review is a model that stops being maintained** when that person moves on. That is the outcome behind most abandoned Python models, and it is a stronger argument for keeping `model()` thin than any testing consideration: pure functions with tests are readable by someone who does not know the pipeline.
