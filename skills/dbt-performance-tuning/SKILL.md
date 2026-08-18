---
name: dbt-performance-tuning
description: Use when a model is slow to build, warehouse compute cost is rising, build times are regressing, or a query scans far more data than it needs. Covers diagnosing the actual slow node from run artifacts, reducing scanned bytes, predicate form and pushdown, join and aggregation rewrites, spill and memory, engine-specific clustering and partitioning, caching effects on benchmarks, and converting a table to incremental.
metadata:
  phase: diagnose
---

# Performance tuning

Three rules govern everything below.

**Diagnose before optimizing.** The model an engineer believes is slow is frequently not the slow one. Guessing wastes effort on a model that contributes little to total runtime and leaves the real one untouched.

**Measure before and after, with numbers.** An optimization that was not measured is a claim, not a result. "This should be faster" is not an outcome. Some optimizations make things worse — clustering a small table, adding a predicate that defeats a join — and only a measurement tells you which happened.

**Say whether you are optimizing time or money.** They are not the same objective and the same change can improve one while worsening the other. Doubling compute usually buys time at flat or higher cost; reducing bytes scanned usually buys both. Naming the objective up front prevents delivering the one nobody asked for.

| Sub-document | Read it when |
|---|---|
| [scan-reduction.md](scan-reduction.md) | You are working Step 2 — predicate form, pushdown, types, semi-structured data, aggregation, or window functions |
| [joins.md](joins.md) | You are working Step 3 — join explosion, range/interval joins, semi- and anti-joins, broadcast vs shuffle, skew |
| [warehouse-layout.md](warehouse-layout.md) | You need the per-engine instrument, config syntax, or mechanism detail for Step 1 or Step 4 |

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

The field that matters here is `project.warehouse`. **Most physical-layout advice, and every instrument for reading a query plan, is warehouse-specific and wrong elsewhere.** If the field is absent, do not assume a warehouse. Determine it from `profiles.yml` or the adapter package in `packages.yml`, state which you found, and if you cannot determine it, apply only the warehouse-neutral steps — scan reduction, joins, aggregation, materialization, dbt-level levers and measurement — and say the physical-layout and instrumentation guidance was skipped for lack of a declared warehouse.

The steps are ordered by expected return, and the order matters: a physical-layout change on a query that reads columns it does not need is polish on the wrong surface, and enlarging compute before diagnosing hides whatever the real cause was.

| Step | Covers |
|---|---|
| 1 | Finding the actually-slow node, and reading a plan with a question in mind |
| 2 | Scan reduction: predicate form, pushdown, types, semi-structured data, aggregation, windows |
| 3 | Joins: explosion, range joins, anti-joins, broadcast versus shuffle, skew |
| 4 | Physical layout and the skipping mechanisms, gated on the engine |
| 5 | Incremental conversion and its correctness obligations |
| 6 | dbt-level levers: parallelism, deferral, batching |
| 7 | Materialization, including engine-maintained variants |
| 8 | Memory, spill, compute sizing, and cost as distinct from speed |
| 9 | Caching, and why it invalidates benchmarks |
| 10 | Anti-patterns worth recognizing on sight |
| 11 | Measurement and proof of output-neutrality |

---

## Step 1 — Find the actual slow model

dbt writes timing for every node to `target/run_results.json` after each invocation. That file is the ground truth for what is slow, and it is more reliable than intuition and cheaper than warehouse profiling.

```bash
# slowest nodes from the last run, descending
python3 -c "
import json
r = json.load(open('target/run_results.json'))
rows = sorted(((x['execution_time'], x['unique_id']) for x in r['results']), reverse=True)
for t, n in rows[:20]:
    print(f'{t:8.1f}s  {n}')
"
```

Read that list before forming a hypothesis. Three things it tells you that guessing does not:

- **Total versus per-model.** A model taking 90 seconds does not matter if the run is 40 minutes and one model takes 25 of them. Optimize the top of the list.
- **Whether the slow node is even a model.** A test that scans full history, or a snapshot, is often the real cost. Optimizing a model when a test is the bottleneck changes nothing.
- **Whether it regressed or was always slow.** Compare against an earlier `run_results.json` if you have one. A regression points at a specific recent change; a chronically slow model is a design question.

Then narrow to *why* the slow model is slow. In order of usefulness:

1. **Read the compiled SQL** — `target/compiled/...`. Not the model file. Jinja can expand into something quite different from what the source suggests, and a macro or an `is_incremental()` branch may be producing a full-history scan you did not intend.
2. **Get the query plan.** Every warehouse offers one (`explain`, or a query profile in the console). Look for the largest scan and whether any filter was applied to it, and for a join that produces far more rows than either input.
3. **Check the scan size.** Most warehouses report bytes scanned per query. That number, before and after, is the cleanest evidence an optimization worked.

State which of these you actually did. "The model looks like it scans a lot" is not a diagnosis.

### What to look for in the plan

Every engine's plan viewer has different names for the same handful of facts. Ask these five questions in this order; each one points at a different fix, and reading the plan without a question in mind produces no decision.

| Question | What the answer means | Section that fixes it |
|---|---|---|
| Which operator holds most of the time? | A scan, a join, an aggregate, a sort and a data-transfer step each have unrelated remedies. Time in a parent operator usually includes its children, so read for which subtree dominates rather than summing to 100%. | — |
| On the largest scan, what fraction of the table was read? | A selective filter that still reads everything means data skipping did not happen. This is the highest-value single number in the whole plan. | Step 2, Step 4 |
| Does any join emit far more rows than its inputs? | Join explosion: a duplicated key, a missing predicate, or an inequality condition the engine can only satisfy by producing a product and filtering. | Step 3 |
| Did anything spill to disk, or is a sort the dominant operator? | The working set did not fit in memory. Spilling is often the difference between a query taking minutes and the same query taking hours. | Step 8 |
| Is one worker or partition taking far longer than the average? | Skew on a join or group key, not overall volume. Adding compute does not help; the slow worker is still one worker. | Step 3 |

The per-engine instrument for each of these — column names, system views, plan operator labels — is in [warehouse-layout.md](warehouse-layout.md) under the engine's heading. Report the numbers you read, not the conclusion alone.

---

## Step 2 — Reduce what you scan

Warehouse-neutral, and where nearly all real wins come from. Physical layout only helps a query that already gives the engine something to skip on. Full technique — worked SQL, the four blockers that defeat pushdown, the CTE-materialization table by engine, and the aggregate/window guidance — is in [scan-reduction.md](scan-reduction.md). The headlines:

- **Filter as early as possible**, in the import CTE, naming columns rather than `select *`.
- **Do not wrap the filtered column in a function** — `date_trunc(col)` or `cast(col)` defeats every skipping mechanism because statistics live on the raw column.
- **Skipping needs a bound the planner can evaluate before execution starts.** A subquery bound (`= (select max(...) ...)`) is invisible to static skipping on Snowflake and BigQuery; resolve it at compile time instead.
- **Match join key types on both sides** — a cast on the large input costs real, measured multiples.
- **Extract semi-structured fields once**, into typed columns, not per consumer.
- **Do the work once**: dedupe with `qualify` where supported, aggregate before joining, and know whether your engine actually reuses a CTE referenced more than once.
- **`count(distinct)` is the expensive aggregate** — approximate it, with the error rate stated, where the consumer can tolerate it.
- **A window function's cost is the sort beneath it** — consolidate specifications, and never `order by` in an intermediate CTE.

## Step 3 — Joins

Joins are where a query stops being proportional to its inputs. A scan that reads too much costs what it costs; a join that emits a multiple of its inputs turns a large query into an unfinishable one. Diagnose the row multiple before anything else here: for each join, compare output rows against the larger input. Anything above 1 needs an explanation. Full technique — the four causes of explosion, range/interval join fixes, the three semi-/anti-join forms and their traps, per-engine broadcast-versus-shuffle behavior, and skew fixes — is in [joins.md](joins.md). The headlines:

- **A duplicated key on one side is the most common cause of explosion**, and it is a correctness bug wearing a performance costume — deduplicate first and the join gets faster as a side effect.
- **`not in (subquery)` is a trap**: a single null in the subquery makes it return no rows at all, silently. Write `not exists` for anti-joins.
- **Broadcast versus shuffle is mostly controlled through the inputs**, not a direct switch — filter and aggregate before the join so a side becomes small enough to broadcast.
- **Skew is a distribution problem, not a volume problem.** More compute does not help; the slow worker is still one worker. Look for a placeholder value dominating the key.

---

## Step 4 — Physical layout and skipping, gated on `project.warehouse`

Every engine's skipping mechanism reduces to the same idea — keep statistics per block of storage, compare the predicate against them, skip whatever cannot match — but the granularity, the statistics kept, and the maintenance cost differ enough that they are genuinely different tools.

| Mechanism | What it stores | What defeats it |
|---|---|---|
| Min/max statistics per block (micro-partitions, row groups, zone maps) | The lowest and highest value per column per block | Overlapping ranges. If every block spans the whole value range — the normal outcome of organizing on a near-unique column — no block can be excluded. Also defeated by a function around the column and by a bound that is not known at planning time. |
| Partitioning (an explicit directory or partition per key value) | An exact mapping from key value to storage | Omitting the partition column from the filter, deriving it in the query rather than filtering it directly, or too many partitions: past a certain count the per-partition overhead and small-file effects overwhelm the saving. |
| Bloom-filter-style structures and search access paths | A probabilistic membership test per block | Range predicates. These accelerate equality and set membership, not `between`. They answer "might this block contain value X", never "which of these blocks are in range". |
| Runtime join filters (built from one join input, applied to the other's scan) | A key set or range discovered during execution | Eligibility rules that vary by engine — a build side too large, a probe side too small, an unsupported join type, an unsupported operator. When it does not fire, the plan simply shows a full scan. |

Two consequences worth internalizing. First, **the mechanism has to match the predicate shape**: organizing a table for range scans does nothing for point lookups on a different column, and a membership structure does nothing for a date range. Second, **column pruning is a separate mechanism that compounds with all of them** — on a columnar engine, naming five columns instead of `select *` reduces bytes read regardless of how well blocks are skipped, and the two multiply rather than add.

Only apply the row for the project's actual warehouse. These mechanisms are not interchangeable and advice from one warehouse is often actively harmful on another.

The table below is the summary. For the per-warehouse detail — exact config syntax, when each mechanism stops paying for itself, and the specific mistake each engine invites — see [warehouse-layout.md](warehouse-layout.md).

| `project.warehouse` | Mechanism | Notes |
|---|---|---|
| `snowflake` | `cluster_by` on a table or incremental model | Reorganizes micro-partitions; maintained by a background service that costs credits. Worth it on large, repeatedly-filtered tables; wasted on small ones. |
| `bigquery` | `partition_by` **and** `cluster_by` | Partitioning is the primary win — it bounds the scan and can be enforced with `require_partition_filter`. Clustering refines within a partition. Roughly 4 clustering columns max, order matters. |
| `databricks` | `liquid_clustering` on recent Delta versions, else Z-ordering; plus `partition_by` for very large tables | Prefer liquid clustering where available: no manual re-ordering step. Do not partition on a high-cardinality column — it produces many small files and gets slower. |
| `redshift` | `sort` and `dist` keys | A sort key on the common range filter, a dist key on the common join key. `dist: all` for small dimensions replicated to every node. |
| `postgres` | Indexes via the `indexes` config | Composite index matching the actual filter and join columns. Indexes cost write throughput; on a full-table analytical scan they may not be used at all. |
| `duckdb` | None generally needed | Single-node; column pruning and filter pushdown carry the load. |
| `trino` | Depends on the underlying connector | Layout is a property of the source table format, not the query engine. |
| absent / `other` | Skip this section | Say the warehouse was not declared and that layout guidance was omitted. |

Three things that hold across all of them:

- **Choose the key from the actual query pattern**, not from intuition. The columns in `where` and `join` on the queries that actually run — check the compiled SQL of the consumers, not the model in isolation.
- **Lower cardinality first** in a composite key, so the leading column groups usefully.
- **Never organize on a near-unique column.** A key on a surrogate id, a UUID, or a timestamp with sub-second precision gives every partition an overlapping range and eliminates the skipping it was supposed to enable. This is the single most common clustering mistake.

Physical reorganization is not free, and on most warehouses it requires a full rebuild to take effect. If the model is large, that rebuild is itself a cost to plan for.

---

## Step 5 — Converting a table to incremental

The largest available win on a model that reprocesses history every run, and the change most likely to introduce a correctness bug. It trades a build-time cost for a permanent, ongoing correctness obligation.

Worth doing when: the model is genuinely slow, the source is append-only or has a reliable updated-at, history does not change retroactively, and full history is not needed on every run.

Not worth doing when: the model is small, the source restates history unpredictably, or the logic depends on a window function over the entire dataset. A window over all history cannot be computed from an increment alone, and forcing it produces wrong values at the boundary of every run.

### The costs, stated honestly

| Cost | Detail |
|---|---|
| Late-arriving data | Rows arriving after the boundary has moved past their timestamp are never picked up. Use `>=` and a lookback window wider than the observed lateness. |
| Drift | The incremental result and a full rebuild diverge over time. Periodic full refresh is the only way to detect it. |
| Strategy correctness | `merge` leaves stale rows when a row disappears upstream. Where the source reprocesses, `delete+insert` is required — see `dbt-incremental-models`. |
| Schema changes | A new column is null for all history until a backfill. See `dbt-adding-columns`. |
| Full-refresh cost | The rebuild you avoided daily still exists, and it now happens rarely and at inconvenient times. |

Full treatment of strategies, unique keys, and boundary predicates is in `dbt-incremental-models`. The point here is that incremental is a performance *tradeoff*, not a free improvement, and the tradeoff has to be stated.

### `incremental_predicates`

An incremental run using a merge or delete strategy must locate the target rows to modify. Without a bound, that means scanning the whole target table on every run — often the dominant cost of an otherwise cheap incremental build.

```sql
{{ config(
    materialized='incremental',
    unique_key='<key_column>',
    incremental_predicates=["<target_alias>.<date_column> >= '<lower_bound>'"]
) }}
```

Three things to get right:

1. **The alias.** Adapters expose the target relation under a specific alias inside the generated statement — check the adapter's documentation and confirm against the compiled SQL. Using the model name or `this` here silently does not do what you want.
2. **The predicate must be at least as wide as the incoming data.** Narrower, and rows that should have been updated are missed entirely — data loss with no error.
3. **The bound must be a literal or a computed date, not a subquery** on the source, which reintroduces the scan it was meant to avoid.

Always confirm the predicate landed in `target/compiled/` or in the run log. This config fails quietly when written wrong.

---

## Step 6 — dbt-level levers

Some of the largest wins are not in any single model's SQL. They are in how much dbt runs, and how much of it runs at once.

### Parallelism

`threads` is the number of concurrent connections dbt opens, so it caps how many independent DAG paths run at the same time. dbt Labs suggests starting at 4; Snowflake's own dbt guidance suggests 8 as compatible with most warehouse sizes. Both are starting points to be measured, not settings to be adopted.

- **Raising threads only helps if the DAG is wide.** A long serial chain has no independent paths to run, and threads change nothing. Check the shape before touching the number: if the critical path through the DAG is most of the total runtime, the fix is DAG shape or one slow model, not concurrency.
- **The limit past which it hurts is queuing, not the thread count itself.** Beyond what the compute can serve, queries queue and total runtime stops improving while contention and cost continue. Some warehouses reject connections past a hard limit. Raise it in steps and compare total wall-clock time.
- **More threads against fixed compute can make individual models slower** by dividing the same memory and CPU across more concurrent work, which can turn a query that fit in memory into one that spills.

### Build less

- **`--defer` with `--state`** resolves `ref()` for unselected models to a previously-built environment instead of requiring them to exist locally. Combined with `--select state:modified+` this is the standard mechanism for building only what changed plus its children, and it is usually the single largest reduction available in CI. It depends on a manifest from a successful prior run and on models being idempotent — the same inputs producing the same output. Where that does not hold, deferral produces confusing results rather than an error.
- **Note what `state:modified+` does not cover.** It selects changed nodes and their descendants. A change in behaviour that dbt cannot see as a code change — a macro's runtime output, a source's data — is not detected. State selection is a cost optimization with a blind spot, not a correctness check.
- **Select deliberately in development.** `--select <model>+` or `+<model>` builds a slice. Building the project to test one model is the most common avoidable cost in day-to-day work.

### Batching a large increment

Where an adapter supports it, dbt's microbatch strategy splits a run into one query per time window rather than one query over the whole increment. Two distinct benefits: each batch is small enough to stay in memory, avoiding the spill that a single wide window would cause, and a failed batch can be retried without redoing the rest.

Batches run in parallel when dbt determines they are independent. They run sequentially when the model references `{{ this }}`, since ordering then matters, and `concurrent_batches` overrides that detection in either direction. Set it to sequential for anything cumulative — a running total computed out of order is wrong, and nothing will fail to tell you so.

### Things that cost less than people assume

- **Model contracts are checked against metadata before SQL is submitted.** The comparison is not a warehouse query, so the compute cost is effectively zero. Contracts do change the generated DDL — column order and any platform-enforced constraints — and a declared type that forces a cast is a real cost, but the check itself is not one. Do not remove contracts to speed up a build.
- **Tests are frequently the actual bottleneck.** A uniqueness or referential test scanning full history can cost more than the model it protects. `run_results.json` covers tests too; if a test is at the top of the list, bound its scan rather than deleting it.

---

## Step 7 — Materialization choice

Materialization is a performance decision as much as a modeling one.

| Change | When it helps | What it costs |
|---|---|---|
| view → table | The logic is expensive and read by several consumers | Storage, and staleness between builds |
| table → view | Small result, or freshness matters more than read speed | Every consumer recomputes it |
| view → ephemeral | One or two consumers, simple logic | Inlined into each consumer; a heavy ephemeral referenced twice is computed twice |
| table → incremental | Large and slow, history stable | The correctness obligations above |
| incremental → table | The incremental logic has correctness problems | Full rebuild cost every run — sometimes the right trade |
| any → materialized view or equivalent | Warehouse supports it and freshness must be continuous | Support and semantics vary a great deal by warehouse; verify before relying on it |

A chain of views is a single large query at the far end. If a chain is slow, materializing one model in the middle — the point where the data first becomes much smaller — often fixes the whole chain at once. Find that point rather than materializing everything.

### Ephemeral is not a performance optimization

An ephemeral model is inlined as a CTE into every consumer. It creates no object, costs no storage, and cannot be queried, tested in isolation, or granted on. Its cost profile follows the CTE-reuse table in Step 2: on an engine that inlines CTEs, an ephemeral model referenced by five consumers is executed five times. Use it for cheap building-block logic with one or two consumers. Where the logic is expensive or widely shared, a table is cheaper and visible in the run artifacts, which an ephemeral model is not. Deep ephemeral-on-ephemeral chains also produce very large nested queries that are hard to plan and harder to debug.

### Engine-maintained materializations

Several engines offer something that keeps a derived result current without dbt orchestrating it. The names are similar, the semantics are not, and the differences decide whether one is usable at all:

- **Restrictions on the defining query vary enormously.** Snowflake's materialized views are limited to a single base table with no joins; its dynamic tables accept much broader SQL, including joins and window functions, and are refreshed to a declared target lag with a documented floor of one minute. Materialized views on other engines are frequently more permissive than Snowflake's. Never carry an assumption about "materialized views" from one engine to another.
- **Incremental refresh is conditional, and falling off it is silent.** Where a construct in the query is unsupported for incremental maintenance, the refresh degrades to a full recompute and the cost changes by an order of magnitude with no error. Snowflake documents both unsupported constructs and a change-volume threshold above which a full refresh happens instead.
- **Automatic query rewrite is a distinct feature from precomputation.** Some materialized-view implementations let the optimizer redirect a query against the base table to the view; a dynamic-table-style construct generally does not, so consumers must reference it explicitly. This changes whether existing consumers benefit without being edited.
- **Refresh cost is continuous and easy to overlook.** These are maintained by a background service that bills. Compare that against the incremental model it would replace, and remember that a declared freshness target is a cost decision as much as a latency one.
- **What you give up** is control of the reprocessing window and dbt's visibility into it. Where the exact lookback is a business rule, an incremental model remains the right tool.

---

## Step 8 — Memory, spill, and compute sizing

### Spill

When an operator's working set exceeds available memory, the engine writes intermediate data to disk instead of failing. That is a feature — the query completes — but the slowdown is severe and it is the single most common reason a query is slow for a reason invisible in the SQL.

Almost every engine distinguishes two tiers, and the distinction drives the decision:

- **Spill to local disk** is a noticeable slowdown, often tolerable.
- **Spill to remote or network storage** is one to two orders of magnitude worse and effectively pathological. This is the case where enlarging compute can reduce total cost as well as time.

Four operators produce nearly all spill: **sort**, **hash join** (building the hash table), **hash aggregation** (`group by`, `distinct`, `count(distinct)`), and **window functions** (the sort beneath them). If the plan shows spill, identify which of the four, because each has a different SQL-level fix.

Fixes, in the order to try them:

1. **Remove the operator if it should not exist.** An `order by` in an intermediate CTE, a `distinct` used to mask a duplicate join key, a `count(distinct a, b)` over pairs that are already unique, or a `group by` including a column nobody needs. This is the only fix that is free.
2. **Reduce the working set.** Filter earlier, project fewer columns, narrow the join key type, aggregate before joining. Less data in the operator means less to hold.
3. **Split the work into batches.** Processing a year one month at a time keeps each operator's state small. In dbt this is what a microbatch strategy or a bounded backfill does, and it often removes spill that no amount of rewriting would.
4. **Then, and only then, enlarge compute or raise the memory setting.** For remote spill this is frequently the correct answer rather than a last resort — but it is the fourth thing to try, because it makes the first three invisible.

Whether memory is a per-operation setting you control, a property of the compute size, or both is engine-specific, as is where the spill is reported. See [warehouse-layout.md](warehouse-layout.md).

Two cautions. Some engines write a small, benign amount to remote storage as normal behaviour when certain acceleration features are enabled, so a nonzero value is not automatically a problem — check the magnitude against the query's data volume. And on engines where the planner chooses between memory-hungry and memory-frugal plans from estimated row counts, stale statistics can cause spill by themselves; refreshing statistics is then the fix, not resizing.

### Compute sizing

Compute size is the fastest lever and the one most likely to hide a problem.

- **Larger compute helps when the query spills to disk**, because it did not fit in memory. That case can improve super-linearly, and cost may even fall since the query finishes disproportionately sooner.
- **It does not help a query that scans too much data.** Doubling compute to scan a table you should have filtered doubles the bill for roughly half the time. Net cost is flat and the underlying problem is now hidden.
- **It does not help a serial dependency chain.** If the run is long because models run one after another, the fix is parallelism or DAG shape, not size.
- **Never leave a temporary size increase in place.** If a size was raised to get a backfill through, restore it, and say so.

Diagnose spilling first — most warehouses report it. Resize because of that signal, not because the query is slow.

### Faster and cheaper are different objectives

Say which one you are optimizing, because the same change can improve one and worsen the other. Two billing models dominate, and they reward opposite behaviour:

| Billing model | What you are charged for | What this rewards |
|---|---|---|
| Provisioned compute, billed by time and size | Wall-clock seconds × size, while compute is running, regardless of query efficiency | Finishing quickly, not idling, and not over-provisioning. A query twice as fast on the same size is half the cost. |
| Per-byte-scanned | Bytes read by the query; elapsed time is not billed directly | Reducing scanned bytes. A faster query that reads the same bytes costs exactly the same. |

Under time-and-size billing, the doubling rule is what makes spill-driven resizing pay: each size step roughly doubles both throughput and rate, so a query that finishes in less than half the time on the next size up is cheaper as well as faster. The published example most worth remembering is a heavy sort that spilled to remote storage on a small warehouse and took hours, versus tens of minutes on a much larger one at a *lower* total credit cost. That arithmetic only works because of the spill; without one, doubling size roughly halves time and leaves cost flat.

Three cost items that are invisible in query-level tuning and frequently larger than any single query:

- **Idle compute.** Time between queries with compute still running is billed at full rate. Suspend timeouts are typically the highest-leverage cost setting on an engine that bills by time, and a default left in place is a common source of waste.
- **Restart minimums.** Where an engine bills a minimum interval on each resume, a suspend timeout shorter than that minimum can cost more than a longer one by causing repeated restarts. It also discards the warm cache. There is a floor below which aggressive suspension backfires; see [warehouse-layout.md](warehouse-layout.md) for the engine's specifics.
- **Background services.** Automatic clustering, search structures, materialized-view and dynamic-table refresh all bill continuously and none of them appear in a query's own cost. A layout config added and forgotten bills indefinitely, whether or not anything filters that way. Check the maintenance cost of anything you add here, and remove what is not earning it.

Separating workloads onto different compute so a large job does not force everything else onto oversized compute is usually a bigger win than tuning any individual query. It is also configuration rather than SQL, so it is easy to overlook from inside a model.

---

## Step 9 — Caching, and why it invalidates benchmarks

Nearly every engine has more than one cache, they invalidate on different events, and mistaking one for a real improvement is the most common false result in this whole document.

| Layer | Serves | Typically invalidated by |
|---|---|---|
| Result cache | The finished result of an identical query, usually without engaging compute at all | The underlying data changing, the query text changing, or a time limit. Non-deterministic functions such as a current-timestamp call generally prevent reuse. |
| Warm compute / local disk cache | Blocks previously read from remote storage, held on the compute nodes | Suspending, resizing or restarting the compute. It is a property of the running compute, not of the data. |
| Metadata cache | Object-level facts — row counts, per-block min/max, null counts — often answering some queries with no scan at all | Schema or object definition changes, not ordinary data changes. |

What follows for measurement:

- **A second identical run measuring the result cache is not evidence.** A "90% improvement" that is entirely cache is a false claim. Where the engine allows disabling result reuse for a session, do so while benchmarking, and say that you did.
- **Distinguish cold, warm and hot.** Cold is fresh compute with nothing local; warm reuses local blocks; hot returns the stored result. Published Snowflake measurements of the same query across those three states differ by orders of magnitude — around twenty seconds, roughly a second, and milliseconds. All three are correct measurements of different things. Compare like with like, and if only one state is available, name it.
- **A rebuild in between invalidates the comparison in the other direction.** Rebuilding a model discards the warm cache for it, so the first read after a build is a cold read and looks worse than steady state.
- **Aggregate cost over a period is the honest metric for a cost claim.** Any single query's timing is contaminated by cache state; a day of runs before and after is not.
- **A metadata-only answer is not a scan.** A bare `count(*)` returning instantly may never have read the table, so it proves nothing about scan performance.

---

## Step 10 — Anti-patterns worth recognizing on sight

Each of these has a stated failure mode, and each is common enough to check for before reading a plan.

| Pattern | Why it costs | What to do |
|---|---|---|
| `select *` in an import CTE | On a columnar engine, reads every column of the table whether or not anything downstream uses them. Also propagates upstream schema changes into the model silently. | Name the columns. This is the cheapest scan reduction available and it never has a downside. |
| `count(distinct ...)` over a very large set | Memory grows with distinct values, so it is a leading cause of spill. Several of them in one query multiply that state. | Approximate where a stated error is acceptable, pre-aggregate to a coarser grain, or store mergeable sketches. Never approximate a reported figure silently. |
| `distinct` used to remove duplicates a join created | Hides a wrong join. Costs a full deduplication pass and leaves the join producing garbage that happens to collapse. Sometimes it does not fully collapse, and the totals are wrong. | Find why the key duplicates and fix the join grain. `distinct` is legitimate on a genuinely multi-valued set, not as a repair. |
| A cross join without an aggregation | Output is the product of the inputs. Both sides only need to be moderately large for this to be unfinishable. | Add the missing predicate, or pre-aggregate one side. Where a cross join is genuinely required — a date spine, a small parameter set — bound it and say so. |
| `order by` in an intermediate model or CTE | Forces a full sort of an intermediate result that nothing is required to preserve. A frequent cause of spill and pure waste when it spills. | Sort only in the outermost query, and only if a consumer needs order. |
| A nested-subquery pyramid | Not inherently slower, but it hides what the query does, so predicates end up applied above things that block pushdown and nobody notices. | Flatten into named CTEs in the project's conventional order. Readability here is a performance property, not a style preference. |
| `where` on the null-producing side of a left join | Silently converts the outer join to an inner one, changing results, and reads as an optimization. | Put the predicate in `on` if it should apply before the join. Decide which you mean. |
| A function around a filtered or joined column | Defeats every skipping mechanism, because statistics exist for the column, not for the expression. | Transform the literal side. See Step 2. |
| `limit` used as a performance fix | Bounds what is returned, not what is read. The scan, join and aggregation all still happen; on per-byte billing the cost is unchanged. | Filter, do not limit. `limit` is for inspecting output. |
| Views stacked on views | The far end is a single enormous query, and one blocked predicate anywhere in the stack defeats filtering for the whole chain. | Materialize at the point where the data first gets much smaller. See Step 7. |

---

## Step 11 — Measure

An optimization with no before-and-after is not finished.

```bash
# before
dbt build --select <model>          # note execution_time from run_results.json

# after the change
dbt build --select <model>          # note it again
```

Report, at minimum: build time before and after, and bytes scanned before and after if the warehouse exposes it. Where the change was made for cost rather than speed, report cost as well — and note that they are different claims. Run each more than once where possible, and control for cache state as described in Step 9: a single pair of measurements is unreliable, and a "90% improvement" that is entirely cache is a false claim.

Report the mechanism too, not just the delta. "Build time fell from 14 minutes to 90 seconds, and partitions scanned fell from 100% to 3%, because the filter now compares the bare column to a literal" is a result. "Now 9x faster" is a number that might be cache, might be a warmer warehouse, and might be a changed result set.

Then confirm you did not change the output. A performance change is by definition output-neutral, so it needs the same proof as any refactor: see `dbt-refactoring-safely` and `dbt-verification`. An optimization that changed results is a bug that happens to be fast.

## Completion checklist

- [ ] Slow node identified from `run_results.json`, not guessed
- [ ] Its share of total runtime stated, so the work is known to be worth doing
- [ ] Compiled SQL read, not just the model file
- [ ] Query plan or scan-size evidence gathered, or its unavailability stated
- [ ] Dominant operator named — scan, join, aggregate, sort or data movement — and the fix matched to it
- [ ] `project.warehouse` determined; layout and instrumentation advice applied only for that warehouse
- [ ] No function wrapping a filtered or joined column
- [ ] No subquery supplying a bound that skipping was expected to use
- [ ] Import CTEs name their columns and filter at the source
- [ ] Join row multiples checked; any join emitting more rows than its inputs explained
- [ ] Anti-joins written as `not exists`, not `not in`, unless the column is provably not nullable
- [ ] Join key types match on both sides, with no implicit cast on the large input
- [ ] Spill checked; if present, the SQL-level fixes tried before enlarging compute
- [ ] Approximate aggregates, if introduced, have their error rate stated and consumer acceptance confirmed
- [ ] Organization key chosen from real query patterns, not a near-unique column
- [ ] Any added background-maintained structure — clustering, search structure, engine-maintained materialization — has its ongoing cost stated
- [ ] Incremental conversion, if any, states its correctness costs explicitly
- [ ] `incremental_predicates` verified in the compiled output, and at least as wide as the incoming data
- [ ] Whether the objective was speed or cost stated explicitly
- [ ] Before and after measured, with numbers, more than once, with cache state controlled or named
- [ ] Output proven unchanged
- [ ] Any temporary compute size increase reverted

## The failure modes that actually happen

1. **Optimizing the wrong model.** A plausible-looking model gets tuned; the run time barely moves because it was never the bottleneck. `run_results.json` would have said so in one command.
2. **Clustering or partitioning on a near-unique column.** Every partition's range overlaps every other, no skipping is possible, and the maintenance cost is paid for nothing.
3. **Cast in the `where` clause.** One `cast` or `date_trunc` around the filtered column turns a bounded scan into a full one. It is invisible in review and shows up as a slow query with a correct-looking predicate.
4. **A subquery supplying the bound.** The predicate looks perfectly selective and the engine still reads everything, because the value was not known when the scan was planned. Nothing in the SQL looks wrong.
5. **`not in` against a nullable column.** A single null makes the whole predicate return nothing. The query succeeds, the model builds, and the output is empty or silently short.
6. **A duplicated join key treated as a performance problem.** The row multiple is fixed by adding compute or a `distinct`, and the underlying double-counting ships to consumers.
7. **`incremental_predicates` narrower than the data.** Rows that should have been updated are silently skipped. No error, no failing test, wrong data.
8. **Measuring a cache hit.** The second run is fast because the result was cached, not because the change worked. Control for cache state and run more than once.
9. **Resizing compute instead of fixing the scan.** Faster, same or higher cost, and the real problem is now harder to see.
10. **Resizing when the problem is skew.** One worker holds most of the rows; more workers do not help, and the extra capacity idles while the same worker finishes.
11. **Leaving a background-maintained structure in place that nothing benefits from.** Clustering, a search structure or an engine-maintained view added during an investigation bills forever and appears in no query's cost.
12. **Silently swapping an exact aggregate for an approximate one.** A metric moves by a couple of percent, no test fails, and someone reconciles against it weeks later.
13. **Faster and wrong.** Output changed and nobody compared. This is the worst outcome available here, and the whole point of proving output-neutrality.
