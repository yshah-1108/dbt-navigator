# Cost, performance, and deciding against Python

dbt states the trade plainly: Python models are slower to run than SQL models and the compute that runs them can be more expensive, because Python needs general-purpose compute that may live on a separate service from the SQL warehouse. This document is about where that cost actually accrues and how to decide whether it is worth paying.

---

## Where the cost comes from

Five sources, and they compound. Naming them separately matters because the mitigation differs per source.

### 1. Compute start-up

The SQL path submits a query to a running engine. The Python path may have to start something first: a stored procedure invocation, a serverless batch, or a cluster that must be provisioned and torn down. On an ephemeral cluster that is minutes before any work begins and minutes after it ends — per model, per run. A DAG with eight small Python models pays that eight times.

**Mitigation:** prefer a submission target with fast start-up for small models, and consolidate. Two Python models that each start a cluster to do thirty seconds of work should be one model.

### 2. Data movement into the runtime

A SQL model transforms data where it already lives. A Python model reads it into a runtime — sometimes the same engine, sometimes a separate service across a network boundary. That read is real work whether or not the transformation is.

**Mitigation:** reduce before reading. Aggregate, filter and project in SQL upstream so the Python model reads the smallest relation that still answers the question.

### 3. Serialisation

Crossing between the engine's representation and Python's costs CPU and memory on both sides. `.to_pandas()` is the obvious boundary, but so is every conversion between a native dataframe and pandas, and so is the write-back of the result. On BigQuery the intermediate format for writing records is itself configurable — meaning it is a real step with a real cost, not a detail.

**Mitigation:** cross the boundary once, as late as possible, with as few columns and rows as possible. Two conversions in one model is a design smell.

### 4. Single-node execution

The dominant one, and the one that produces the worst failure. A native dataframe — Snowpark, Spark, BigFrames — pushes operations down to a distributed engine. pandas runs in one process, on one node, with that node's memory as the hard ceiling.

`.to_pandas()` is a **materialisation boundary: every row crosses into one process's memory.** That is correct for a library that requires a local dataframe, and most statistical and ML libraries do. It is wrong for reshaping data at scale, and the failure is the nastiest kind: it works in development on a filtered window, works in production for months, and then fails intermittently as data grows — usually near a deadline, because volume and deadlines correlate.

| | Native, pushed down | pandas |
|---|---|---|
| Executes | In the warehouse or cluster engine | In a single Python process |
| Data volume | Large | Bounded by that process's memory |
| API surface | Narrower | Complete |
| Fails by | An unsupported operation, at development time | Out-of-memory, at an unpredictable future volume |

The asymmetry in the last row is the argument: a native dataframe fails **early and loudly** when it cannot do something, and pandas fails **late and situationally**. Prefer the failure you find while writing the code.

Note one platform-specific escape hatch: a pandas-on-Spark API covers most of the pandas surface while still executing in parallel, so on that platform pandas *syntax* need not mean single-node execution. Verify which API is actually in use before assuming either way.

### 5. The maintenance tax

Not a compute cost, and usually the largest one. Every future change to a Python model pays the debugging and testing overhead in `testing-and-debugging.md`: no compiled artefact to read, no unit tests, a remote runtime, and a smaller pool of reviewers. dbt's own framing is that the cost of Python is not typing time — and that is right, but it understates it. **The cost is paid on every future change, by whoever is on call.**

---

## When to push work back to SQL

A useful rule with a clean test: **push everything up to the last operation that SQL can do, and let Python do only the operations it must.**

Concretely, in a model that fits a regression per group:

| Step | Belongs in |
|---|---|
| Filtering to the relevant population | SQL |
| Joining in the attributes needed | SQL |
| Aggregating to the grain the model fits on | SQL |
| Selecting only the columns the fit uses | SQL |
| Fitting the model and extracting coefficients | Python |
| Joining the coefficients back to other facts | SQL, in a downstream model |

A Python model that pulls a wide fact table and immediately groups it has done its most expensive operation — the read — in the wrong place. If the first thing the Python code does is something SQL can do, move it.

### DAG position

**Avoid making a Python model a mid-DAG dependency.** Every downstream model then waits on the slowest, most fragile node in the graph, and a Python failure becomes a failure of everything behind it. Push Python toward the leaves: aggregate in SQL, run the Python step on the small result, and where something downstream needs the result, prefer a thin SQL model between them.

There is a second, less obvious reason. A Python model cannot carry a contract and cannot be unit tested, so a mid-DAG Python model is an untestable, unconstrained interface that many models depend on. That is the position in a DAG where you least want those properties.

---

## What genuinely needs Python

| Case | Why SQL cannot do it |
|---|---|
| **ML inference or training** | Loading a serialised model and applying it. No SQL analogue, and the library is the point |
| **Complex statistical work** | Model fitting, hypothesis testing, influence measures, distribution fitting — where the warehouse has no such aggregate |
| **Iterative algorithms** | Graph traversal, simulation, convergence loops. Expressible in recursive SQL sometimes, readably almost never |
| **A library with no SQL equivalent** | Domain parsing, specialised text processing, a scientific package |
| **API enrichment** | Calling an external service per row or per batch. Requires platform support for outbound network access — see [platform-reference.md](platform-reference.md) |
| **Parsing beyond the dialect's capability** | Where the dialect's own functions genuinely cannot express the parse |

Two notes on that list. **API enrichment is the entry with the highest hidden cost**: it makes the model's success depend on a third party's availability, rate limits and schema, so a scheduled build now fails for reasons outside the warehouse. dbt's own commentary is that this opens the pipeline to external entropy that SQL is resistant to. It is often right anyway — and it should be a deliberate choice with a stated failure plan, not a convenience.

And **ML inference is the case where a Python model is most clearly correct and also most likely to be a governance question**: a scored output that drives a decision about a person can attract obligations of its own. See `dbt-handling-sensitive-data`.

---

## What looks like Python and is not

Check the warehouse's function list before concluding SQL cannot do something. Statistical aggregates in particular are far more widely supported than people expect, and a built-in aggregate beats pulling a table into a Python runtime by an enormous margin.

| Instinct | Actually |
|---|---|
| Pivot or unpivot | SQL, or a macro from a utilities package |
| Ranking, running totals, lag or lead | Window functions, and far faster |
| Percentiles, correlation, standard deviation, regression slope, linear-regression intercept | Most warehouses have these built in — **check first** |
| Approximate distinct counts and quantiles | Built-in on most warehouses, and dramatically cheaper |
| Date arithmetic and bucketing | SQL date functions |
| Deduplication | `row_number()` in a subquery, or a qualifying clause where the dialect has one |
| Fuzzy matching, edit distance, phonetic keys | Often a warehouse function |
| JSON or semi-structured parsing | Native on most modern warehouses |
| String splitting and regex extraction | SQL, unless the dialect's regex genuinely cannot express it |
| Generating a date spine | SQL, or a utilities package macro |
| Recursive hierarchy flattening | A recursive CTE where the dialect supports it |

**The test: can the logic be written in SQL at all?** If yes, write SQL — even if the SQL is longer. Length is a one-time cost; the Python tax is recurring.

---

## Deciding, and writing the decision down

The decision is worth one paragraph in the PR, and the paragraph has four parts:

1. **What the model does**, in one sentence.
2. **The specific thing SQL cannot express**, named — not "this is complex logic" but the operation and why.
3. **What you checked before concluding that**, including the warehouse's function list and any utilities package installed. This is the part that gets skipped and the part a reviewer most needs.
4. **Where the cost lands**: which compute runs it, roughly what volume crosses into the runtime, and whether anything downstream depends on it.

Two failure modes this prevents, both common:

- **Python chosen out of habit**, by someone more fluent in pandas than in window functions. The model works, is slower, is reviewable by fewer people, and gets rewritten in SQL within a year by someone who is annoyed about it.
- **Python chosen correctly and abandoned anyway**, because nobody recorded what it does or why, so the next person cannot safely change it and works around it instead.

A defensible split worth naming, because it resolves a surprising number of cases: **a Python model computing on a bounded window as a `table`, plus a thin SQL incremental model downstream handling the merge.** SQL's incremental behaviour is better specified, the Python step stays simple and stateless, and the expensive, fragile node stops being the one that also has to reason about history.
