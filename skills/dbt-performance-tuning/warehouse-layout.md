# Physical layout and engine instruments, per warehouse

Referenced from [SKILL.md](SKILL.md). **Apply only the section matching the contract's `project.warehouse`.** If that field is absent and the adapter could not be determined, apply none of this and say why — a layout config on an engine that has no such mechanism is accepted or ignored silently, and leaves an engineer believing the problem is addressed.

Read section 1 and 2 of [SKILL.md](SKILL.md) first. Physical layout is the second-order win; scan reduction in the SQL is the first-order one, and layout tuning on a query that reads columns it does not need is polish on the wrong surface.

Each section carries three things: **layout** (how to organize the data), **instruments** (where this engine reports the five facts SKILL.md Step 1 asks for), and **engine-specific mechanisms** that have no equivalent elsewhere. The instruments matter as much as the layout: an engineer who cannot read pruning and spill on their own engine is guessing, whatever they configure.

One rule holds on every engine: **layout is worth configuring only when queries filter consistently on the same columns.** Layout accelerates a predictable access pattern. It does nothing for an unpredictable one, and it bills either way.

Version-dependence is the standing hazard in this document. Every engine here has changed its recommended mechanism at least once, and older mechanisms usually keep working well enough to look correct. Where something below is stated as version-conditional, confirm it against the running version rather than assuming.

---

## `snowflake`

Clustering keys. Tables only — a clustering key on a view is inert.

```sql
{{ config(
    materialized='incremental',
    cluster_by=['<low_cardinality_column>', '<date_column>'],
) }}
```

- Worth considering above roughly a million rows, and only for columns that appear in `where` or `join` on most queries.
- **Order matters: lower cardinality first.** The key is a prefix ordering; a high-cardinality leading column makes the rest of the key irrelevant.
- **Never cluster on a unique or near-unique column.** Every storage unit then spans a distinct range, pruning gains nothing, and automatic reclustering bills continuously for it. Clustering on a surrogate key is the canonical version of this mistake.
- Three or four columns at most. Beyond that no single ordering can serve them all.
- Clustering is maintained in the background and **costs credits**. A clustered table nobody filters that way is pure expense with no counterpart benefit.
- Existing data does not reorganize on the spot; the benefit accrues as data is rewritten. Judge the result after a few builds, not immediately.

Check whether clustering is actually helping before adding more of it — the engine exposes clustering-depth information per key, and a table whose depth is already near optimal has nothing left to gain from tuning.

### Instruments

The query profile in the console, and `snowflake.account_usage.query_history` for the same facts in SQL. What to read, in the order SKILL.md Step 1 asks for:

| Fact | Where |
|---|---|
| Dominant operator | The profile's most-expensive-nodes callout. Time in a node includes its children, so read for which subtree dominates. |
| Pruning | On the `TableScan`: **partitions scanned versus partitions total**. This is the single most useful number the profile carries. In SQL, `partitions_scanned` and `partitions_total` in `query_history`. A selective date filter scanning 100% of partitions is a pruning failure — a type mismatch, a function around the column, a subquery bound, or the wrong key. |
| Join explosion | Output rows against input rows on each `Join` node. `get_query_operator_stats(<query_id>)` exposes the same programmatically, and the ratio of `output_rows` to `input_rows` per join operator finds it without clicking through a plan. |
| Spill | `bytes_spilled_to_local_storage` and `bytes_spilled_to_remote_storage`, on the operator in the profile and as columns in `query_history`. |
| Skew | Compare per-node time against row counts across the plan; the profile does not expose a per-worker breakdown as directly as some engines. |

Two profile details worth knowing. A `JoinFilter` operator is the engine's runtime semi-join reduction — evidence that a join-derived predicate did reduce the scan. And `warehouse_size` being null in `query_history` means the query was answered from metadata or the result cache and never engaged compute.

### Engine-specific mechanisms

- **Hash joins, and "lower left".** Joins are predominantly hash joins: one input builds a hash table, the other probes it. The build side is the expensive one, and the profile conventionally shows it on the left, so the smaller row count belongs on the left. You have little direct influence over the choice — but a flipped join is visible in the profile and explains a spill that the SQL does not.
- **Only equality predicates build the hash table.** A range or function condition in the join, alone or combined with an equality, is satisfied by producing candidate pairs and filtering. This is the mechanism behind the range-join advice in SKILL.md Step 3.
- **Subqueries do not prune.** Documented explicitly: micro-partitions are not pruned on a predicate containing a subquery, even when it returns a constant. Resolve the bound at compile time instead.
- **Search optimization service** builds a separate access path for highly selective lookups — equality, `IN`, substring and regex matching, fields inside semi-structured columns, geospatial predicates. It is for queries returning few rows. It does not help a range scan or a wide aggregate, and it is a maintained structure that bills. Clustering and search optimization solve different problems and can coexist; choosing one because the other was already tried is a common waste.
- **Query acceleration service** offloads part of a large scan-and-filter workload to shared compute, aimed at outlier queries within a warehouse. It is not a substitute for pruning. Note the interaction with diagnosis: when it is enabled the engine writes a small amount to remote storage for eligible queries regardless, so a small nonzero remote-spill figure is expected and not a finding.
- **Dynamic tables versus materialized views.** Materialized views are restricted to a single base table with no joins, are maintained continuously, and can be used by the optimizer to rewrite queries against the base table. Dynamic tables accept far broader SQL — joins, unions, window functions — and refresh to a declared target lag with a floor of one minute, but do not get automatic query rewrite. Incremental refresh on a dynamic table degrades to a full refresh for unsupported constructs and above a documented proportion of changed data; that degradation is silent and changes the cost profile entirely. Verify from the refresh history rather than assuming.
- **Named `window` clauses are not supported.** Repeat the full `over (...)`, or generate it from a Jinja variable so one definition produces byte-identical text.
- **Billing floors.** Compute is billed per second with a one-minute minimum on each resume, and resuming twice inside a minute is billed twice. That produces a floor below which shortening the suspend timeout costs more than it saves, and also discards the local cache each time. Sources disagree on the right setting — the vendor's own guidance suggests a few minutes, while cost-focused practitioners commonly recommend the 60-second minimum for everything except latency-sensitive interactive use. Both agree the platform default is too long for most batch workloads. Measure against your own gap pattern rather than adopting a number.
- **Background services bill separately from queries.** Automatic clustering, search optimization maintenance, and dynamic-table or materialized-view refresh all consume credits that appear in no query's cost. Anything added here needs its ongoing cost checked and removed if it is not earning it.

---

## `bigquery`

Partitioning first, clustering second. They are different mechanisms and partitioning does most of the work.

```sql
{{ config(
    materialized='incremental',
    partition_by={'field': '<date_column>', 'data_type': 'date', 'granularity': 'day'},
    cluster_by=['<column_a>', '<column_b>'],
) }}
```

- Partition on the column queries filter by date on. An unpartitioned scan of a large table is billed in full, every time.
- **Mind the partition limit.** Daily partitioning of many years of data can exceed the per-table maximum; use monthly granularity for long histories, or an integer-range partition where the natural key is numeric.
- Clustering operates *within* a partition, up to four columns, order significant — same prefix logic as elsewhere.
- `require_partition_filter` rejects a query that omits the partition filter. It is an ergonomic cost that repays itself the first time it prevents a full-table scan on a large table.
- A dry run reports bytes that would be scanned without executing. Use it as the measurement for a layout change — it is exact, immediate, and free.

Incremental strategies that replace whole partitions align naturally with a partitioned table, and are usually cheaper than a row-level merge. Confirm the partition column and the incremental boundary use the same column, or the strategy replaces partitions the boundary never selected.

### Instruments

- **A dry run reports bytes that would be scanned without executing.** Free, exact, immediate, and the correct measurement for any layout or predicate change on this engine. Use it as the before-and-after number.
- **The execution details / query plan** gives per-stage timing and slot time. Join strategy appears in the step details: broadcast shows as a join of each with all, hash-partitioned as each with each. Skew shows as **max slot time far above average slot time** for a stage — that comparison is the engine's clearest skew signal.
- **`INFORMATION_SCHEMA.JOBS`** carries `total_bytes_processed`, `total_bytes_billed` and `total_slot_ms` per job. Bytes billed is the cost fact under on-demand pricing; slot time is the cost fact under capacity pricing. They lead to different optimizations — see SKILL.md Step 8.
- **Set a byte ceiling as a guardrail.** A maximum-bytes-billed limit on the connection turns an accidental full scan of a very large table into a failed query rather than a large invoice. This is a safety mechanism, not a tuning one.

### Engine-specific mechanisms

- **Partition elimination requires a value known at planning time.** A subquery in the partition filter causes a full scan, documented and long-standing. Resolve it into a scripting variable, or resolve it at dbt compile time as shown in SKILL.md Step 2. Reports exist of limited dynamic elimination under narrow conditions in recent versions; the cost preview cannot reflect it either way, so verify against bytes actually billed rather than the estimate.
- **Keep the partition column isolated on one side of the comparison.** Mixing it with another column or wrapping it in a function — a `DATE()` call around a timestamp partition column being the classic — prevents matching the filter to the partition scheme. Ingestion-time pseudocolumns behave the same way, and are documented to prune better with the pseudocolumn on the left of the comparison.
- **`require_partition_filter` is the enforcement mechanism** and repays its ergonomic cost the first time it stops a full scan.
- **A merge on a partitioned table still scans the whole destination** unless a constant predicate on the partition column appears in the merge condition or `incremental_predicates`. A `when not matched by source` clause needs that predicate repeated in its own condition; without it, that clause alone scans every partition while everything else is bounded.
- **The join-order convention here is largest table first**, then decreasing size. This is the opposite of the mental model Snowflake's profile encourages, which is exactly why it belongs per-engine. Runtime re-planning means it is a hint, not a guarantee: the engine may begin with a hash-partitioned join and switch to broadcast when one side finishes small enough.
- **Skew is partly handled at runtime** by dynamic repartitioning of overloaded shuffle partitions, and partly not. Where a few key values dominate — nulls, an "unknown" sentinel — pre-filter or split the query. A hash-bucket salt on both sides of the join condition works and costs readability.
- **CTEs are not materialized.** A non-recursive CTE referenced twice scans its source twice, and on on-demand pricing that is twice the bytes billed. This is the strongest per-engine case for promoting a reused CTE to a model, and the strongest case against ephemeral models with several consumers.
- **Named `window` clauses are supported**, including partial specifications combined with an inline frame clause, and they let several window functions share one specification.
- **Materialized views here are incremental and automatically maintained,** and the optimizer can rewrite a query against the base table to use one. That makes them a genuine option for a reused aggregate, unlike on engines where the equivalent must be referenced explicitly. Restrictions on the defining query still apply — check them rather than porting an assumption.
- **Nested and repeated fields can remove a join entirely.** Storing children inside the parent row is the engine-native alternative to a fact-dimension join, and it trades join cost for a less conventional model shape. Worth considering only where the nesting matches how the data is queried.

---

## `databricks`

Depends on table format and runtime version. This is the engine where stale advice does the most damage, because the recommended approach has changed and the older mechanisms still work well enough to look correct.

```sql
{{ config(
    materialized='incremental',
    file_format='delta',
    liquid_clustering=true,
    cluster_by=['<column_a>'],
) }}
```

- On recent Delta versions, **liquid clustering** supersedes both partitioning and `zorder`, and the clustering columns can be changed later without rewriting the table. Prefer it where the runtime supports it. Confirm support rather than assuming it — the config is otherwise ignored.
- Where it is not available: `zorder` for multi-column locality, and partition only on a genuinely low-cardinality column.
- **Partitioning on a high-cardinality column produces the small-files problem**, which is slower than not partitioning at all. This is the most common layout mistake on this engine, and it presents as gradually worsening query time rather than as an error.
- `optimize` and `vacuum` are ongoing maintenance, not one-time setup. Confirm they are scheduled; without them the file layout degrades back to where it started, and the config still looks correct.

### Instruments

- **The Spark UI / query profile** gives per-stage task timing. Skew reads as a stage where the maximum task duration is far above the median — the per-task distribution makes this the most legible skew signal of any engine here.
- **Files pruned versus files read** on the scan, plus bytes read, is the pruning fact. `describe detail <table>` reports `numFiles` and `sizeInBytes`, which is what the dynamic-pruning thresholds below are compared against.
- **Spill** appears as spill columns on the stage. Sort, aggregate and join stages are where it shows.
- Small-file counts are worth checking on any table that has been partitioned: thousands of tiny files defeats every optimizer above them.

### Engine-specific mechanisms

- **Dynamic file pruning** applies a filter derived from one side of a join to the other side's scan at runtime, so a selective filter on a dimension prunes files in the fact table without the fact table being partitioned on that column. This is the engine's answer to the pre-filter pattern in SKILL.md Step 2, and it has real eligibility conditions: it is thresholded on the probe-side table's size and file count, so it does not fire on small tables, and its effect depends on how well clustered the data is. For `merge`, `update` and `delete` it requires the vectorized engine; for `select` that engine makes it broader and more reliable. If the plan shows no pruning, check the thresholds before rewriting the query.
- **Adaptive query execution re-plans at runtime** from observed statistics: converting a shuffle join to a broadcast when one side turns out small, coalescing shuffle partitions, and splitting skewed partitions into balanced ones. Where it is active it is usually a better answer to skew than a hand-written salt. It is not a reason to skip filtering — it optimizes the plan, not the volume.
- **Range join optimization** bins the value domain so a point-in-interval or interval-overlap join stops being a filtered product. Automatic in Databricks SQL; elsewhere it needs a hint naming a relation and a bin size, or the equivalent session setting. Conditions are specific: numeric, `DATE` or `TIMESTAMP` values, the same type — and for decimals the same precision and scale — on both sides, and an inner join or a point-in-interval outer join with the point on the outer side. Bin size units differ by type: days for `DATE`, seconds for `TIMESTAMP`, with fractions allowed. **Bin size has to approximate the typical interval length.** A bin much smaller than the intervals makes each interval overlap many bins and is worse than no optimization; the documented example is a bin of 10 against intervals a million wide, overlapping a hundred thousand bins.
- **Bucketing on a join key** removes the shuffle for every subsequent join on that key, paid for once at write time. Worth it for two large tables joined repeatedly on a schedule; not worth it for ad-hoc work.
- **Deletion vectors** record row-level deletes in metadata rather than rewriting files, which makes `update`, `delete` and `merge` much cheaper — at the cost of reads applying the vectors until the next compaction. Relevant when an incremental model's merge cost is the complaint.
- **Named `window` clauses are supported**, letting several window functions share one specification and one sort.
- The vectorized execution engine falls back to the row-based engine for unsupported operations, transparently and for that portion only. A query that seems to have lost its acceleration for no reason usually contains one unsupported expression.

---

## `redshift`

Sort keys and distribution style. Distribution dominates and is the one most often left at its default.

```sql
{{ config(
    materialized='table',
    sort='<date_column>',
    dist='<join_key_column>',
) }}
```

- **Sort key** enables zone-map pruning — the analogue of clustering. Choose the column ranges are filtered on, typically a date.
- **Distribution** decides whether a join redistributes data across nodes. Distributing both sides of a frequent join on that join's key removes the shuffle entirely, and no amount of sort-key tuning substitutes for getting it right.
- `dist='all'` replicates the table to every node: excellent for a small dimension joined constantly, ruinous for anything large.
- `dist='even'` is the safe default when no join key dominates. Choosing a distribution key that skews — a column where a few values hold most rows — is worse than even distribution, because one node does most of the work while the others idle.
- **`vacuum` and `analyze` matter more here than on any other engine listed.** Unsorted regions defeat the sort key, and stale statistics produce bad plans. Both degrade quietly, with no error and no config change to point at.

### Instruments

This engine exposes more per-slice detail than any other here, which makes skew and spill unusually easy to prove.

- **`svl_query_summary`** for a per-step summary of a query: `is_diskbased` marks steps that spilled — only hash, aggregate and sort steps can — `workmem` shows the memory granted, `is_rrscan` shows whether a range-restricted scan happened, which is this engine's name for zone-map pruning, and `rows_pre_filter` indicates sort-key selectivity.
- **`svl_query_report`** for the same broken out **by slice**. All slices processing roughly equal rows in roughly equal time is healthy; a large discrepancy is distribution skew, and points at the distribution key rather than the query.
- **`svv_table_info`** flags tables whose layout is the problem: skew, unsorted fraction, and the estimated benefit of vacuuming. A high vacuum-sort-benefit value is a concrete signal, unlike a general instinct to vacuum.
- **`svl_auto_worker_action`** shows what automatic table optimization has changed, which matters because layout may not be what your dbt config says any more.
- **Measure the second or third execution.** Both the result cache and the compiled-plan cache are cold after a restart or maintenance, so a first run overstates the cost of everything.

### Engine-specific mechanisms

- **Automatic table optimization** will choose distribution and sort keys itself when they are set to auto, and it revises them over time from observed workload. That is often better than a one-off manual choice, and it means a manual key competes with the engine's own decisions. Choose one approach deliberately rather than mixing them.
- **Automatic workload management** profiles queries and allocates concurrency and memory, and is the main lever for short queries stuck behind long ones. Since spill here is a function of the memory a query is granted, queue configuration is a memory-tuning decision, not only a scheduling one.
- **Concurrency scaling** adds transient capacity for bursts of reads. It addresses queueing, not a slow individual query.
- **`qualify` does not exist**, so deduplication needs the two-CTE rank-then-filter form. **Named `window` clauses are not supported** either; repeat the `over (...)` or generate it from a Jinja variable.
- Large `varchar` declarations are a real cost here in a way they are not everywhere: over-wide character columns inflate the memory a step is granted and make spill more likely.

---

## `postgres`

Not a columnar analytics engine, so the advice inverts.

- **Indexes are the primary tool**, not clustering. dbt supports them through the `indexes` config; add them for the columns queries filter and join on.

  ```sql
  {{ config(
      materialized='table',
      indexes=[
        {'columns': ['<filter_column>'], 'type': 'btree'},
        {'columns': ['<join_key>'], 'unique': False},
      ],
  ) }}
  ```

- An index that is never used still costs write time on every build. Check usage before adding a third and fourth.
- Native partitioning helps large time-series tables but is genuine schema work, not a config flag. Consider it only when the table is large enough that indexes alone have stopped helping.
- `explain (analyze, buffers)` reports **actual** timings and buffer reads alongside the estimates. That is better feedback than any other engine here provides — use it, and compare estimated against actual row counts. A large divergence means statistics are stale, which is a different fix from layout.
- Confirm `autovacuum` is keeping up. Bloat presents as query time degrading gradually with no change to the query or the data volume.

### Instruments, and the memory settings that decide spill

This is the one engine here where the memory budget is a setting you control directly per operation, which makes spill both easier to diagnose and easier to cause.

- **`explain (analyze, buffers)`** is the best feedback any engine in this document provides. Read three things:
  - **`Sort Method`.** `quicksort` or `top-N heapsort` means the sort stayed in memory. **`external merge` with a `Disk:` figure means it spilled**, and the reported size is a lower bound on the additional memory it needed.
  - **`Batches:` on a hash node.** Greater than 1 means the hash join or hash aggregate spilled.
  - **Estimated versus actual row counts.** A large divergence means statistics are stale, which is a different fix from layout — and a consequential one here, because the planner chooses between memory-hungry and memory-frugal plans from those estimates. A bad estimate can cause a spill by itself.
- **`work_mem` is per operation, per connection, not global.** A query with two sorts and a hash join can hold several times `work_mem` at once, and dozens of concurrent sessions multiply that again. Raising it globally because one query spilled is the standard way to turn one slow query into an out-of-memory incident. Set it for the session that needs it: `set local work_mem = '...'`, then confirm from the plan that the method changed.
- **`hash_mem_multiplier`** scales the budget for hash operations specifically, with a default of 2.0 in current versions. Where the spill is on a hash node, raising this leaves sort behaviour untouched — sometimes the better trade, since a spilled sort is often acceptable and a spilled hash join rarely is.
- **`log_temp_files`** records spills across a real workload, which is how to choose a setting from evidence rather than from one query.
- **`pg_stat_database`** carries `temp_files` and `temp_bytes`; growth there means spilling is routine rather than exceptional.
- Note the version dependence: before version 13 a hash aggregate chosen on a bad row estimate had no disk fallback at all and could exhaust server memory. From 13 onward it spills instead. If you are diagnosing an out-of-memory rather than a slowdown, the version matters.

**Try the index before the memory setting.** An index matching the sort or grouping columns can remove the operator entirely, which is strictly better than giving it more memory. The memory increase is a stopgap; often the index makes it unnecessary.

### Engine-specific mechanisms

- **CTE materialization is explicit and version-dependent.** Before version 12 a CTE was always materialized — an optimization fence, blocking predicate pushdown into it. From 12 onward a CTE referenced once and free of side effects is inlined by default. `with ... as materialized` and `as not materialized` state the intent, and both are useful: `materialized` to compute an expensive CTE once, `not materialized` to let a filter push into it.
- **`qualify` does not exist.** Deduplication uses the two-CTE rank-then-filter form. Recent versions can optimize `where rn = 1` over a `row_number()` by stopping once the condition can no longer hold, which narrows the gap — check the plan for evidence rather than assuming either way.
- **Named `window` clauses are supported**, and window functions sharing a syntactically identical specification are guaranteed to be evaluated in one pass, which is a stronger guarantee than most engines make.
- **Native partitioning** helps large time-series tables but is genuine schema work, not a config flag. Consider it only when the table is large enough that indexes alone have stopped helping.
- Materialized views here must be refreshed explicitly; they do not maintain themselves. Any freshness expectation has to be met by whatever runs the refresh.

---

## `duckdb`

- Usually single-process and fast enough that SQL-level scan reduction is the entire story. There is no clustering mechanism to configure.
- Where data lives in Parquet, the wins are projection pushdown (select fewer columns) and predicate pushdown into the file — both of which are SQL-level, not layout-level.
- If the constraint is memory rather than time, materialize an intermediate step instead of holding the whole pipeline in one query. That is the closest thing to a layout decision this engine offers.
- Partitioned Parquet output — a directory layout keyed by a low-cardinality column — is the one real physical option, and it pays off only if queries filter on that column.

### Instruments and memory settings

Here the constraint is almost always memory on one machine, so the settings are the tuning surface.

- **`explain analyze`** gives per-operator timing and row counts. Out-of-core support means grouping, joining, sorting and windowing spill to disk rather than failing, so a query that "works" may be spilling throughout.
- **`memory_limit`** defaults to a fraction of system RAM. Published guidance is roughly 1–2 GB per thread for aggregation-heavy work and 3–4 GB per thread for join-heavy work, with a documented minimum around 125 MB per thread. Those figures make the tradeoff concrete: **more threads is not free speed**, because each thread builds its own intermediates and peak memory rises with the thread count. Reducing `threads` is a legitimate fix for a query that will not fit.
- **`temp_directory`** is where spill goes; point it at fast local storage and leave real headroom, since a large sort can write out something comparable to the whole input. `max_temp_directory_size` caps it. If the limit is hit, or spilling is unavailable, the query fails rather than degrading.
- **`preserve_insertion_order = false`** lets the engine reorder results that have no `order by`, which reduces peak memory on large reads and writes. It is the standard first move for an out-of-memory on a bulk import or export. Only safe where nothing downstream depends on incidental ordering — which nothing should, but sometimes does.
- Parallelism is organized around row groups, so a file written as one enormous row group parallelizes poorly regardless of thread count.

### Other notes

- **`qualify` is supported**, as are **named `window` clauses**, so deduplication and multi-window queries can be written in their compact form.
- Where the constraint is memory rather than time, materializing an intermediate step instead of holding the whole pipeline in one query is the closest thing to a layout decision this engine offers.
- This engine is a reasonable local proxy for testing SQL logic, and a poor proxy for another engine's *performance*. Nothing measured here transfers to a distributed engine's cost.

---

## `trino`

- Trino is a query engine, not storage. **Physical layout belongs to the underlying tables**, and must be configured there: Iceberg, Delta, or Hive.
- Iceberg supports partitioning and sort orders, and partition evolution without rewriting history. That is the right place for layout decisions.
- What Trino controls is **pushdown**: whether the filter and the column projection reach the connector rather than being applied after reading. `explain analyze` shows whether it happened. If it did not, the shape of the predicate is usually the reason — a function wrapping the column is the most common cause, and it is engine-independent.
- A dbt config that looks like a layout setting may be silently unsupported by the connector in use. Verify against the connector's documentation before reporting a layout change as applied.

### Instruments

- **`explain analyze`** reports per-operator rows, time and whether pushdown happened. Because this engine's whole performance story is how much work it delegates versus how much it drags across the network, the pushdown question is the first one to ask.
- Look for the physical operations the connector performed against those the engine performed after reading. A filter or aggregate that appears in the engine's plan rather than being pushed down is data crossing the network for no reason.
- The most common cause of a failed pushdown is the shape of the predicate — a function wrapping the column being the usual one, and that cause is engine-independent. A type mismatch between the query and the source column is the second.

### Other notes

- **`qualify` and named `window` clauses are supported.**
- Cross-catalog joins are the case with no equivalent elsewhere: joining two connectors means one side is read across the network in full, since neither source can filter on the other's keys. Where that join is on a hot path, landing one side into the same catalog first is usually the only real fix.
- Memory and spill behaviour is a property of the cluster configuration rather than of a per-query setting. If a query fails or spills, that configuration is where the answer is, and it is usually outside dbt's reach.

---

## `other`, or unknown

Apply nothing from this document. Say that physical-layout guidance was withheld because the engine could not be established, and ask the user which engine the project runs on. Then apply only the sections of [SKILL.md](SKILL.md) that are engine-independent: scan reduction, predicate form, incremental conversion, and measurement.

Recommending a layout mechanism the engine does not have is worse than recommending nothing, because it terminates the investigation with a config that cannot help.

---

## Cross-engine quick reference

Two tables to stop a technique from one engine being written as though it were universal. Verify against the running version before relying on any row: several of these have changed and will change again.

### SQL features that affect how a rewrite must be written

| Feature | Snowflake | BigQuery | Databricks | Redshift | Postgres | DuckDB | Trino |
|---|---|---|---|---|---|---|---|
| `qualify` | yes | yes | yes | no | no | yes | yes |
| Named `window` clause | **no** | yes | yes | **no** | yes | yes | yes |
| `grouping sets` / `rollup` / `cube` | yes | yes | yes | yes | yes | yes | yes |
| Approximate distinct count | yes | yes | yes | yes | via extension | yes | yes |
| Mergeable distinct-count sketches | yes | yes | yes | yes | via extension | limited | limited |
| CTE referenced twice is reused | optimizer's choice | **no, re-scanned** | optimizer's choice | optimizer's choice | explicit `materialized` keyword | optimizer's choice | optimizer's choice |

Where a row says "optimizer's choice", do not build a cost argument on reuse. Promote the CTE to a model or verify from the plan.

### What each engine gives you for the five diagnostic questions

| Question | Snowflake | BigQuery | Databricks | Redshift | Postgres | DuckDB | Trino |
|---|---|---|---|---|---|---|---|
| Scan efficiency | partitions scanned / total | dry-run bytes; bytes billed | files pruned / read | `is_rrscan`, `rows_pre_filter` | buffers read; index usage | operator row counts | pushdown into connector |
| Join explosion | operator output vs input rows | per-stage row counts | per-stage row counts | per-step rows | actual vs estimated rows | operator row counts | operator row counts |
| Spill | local vs remote spill bytes | not directly exposed | stage spill metrics | `is_diskbased`, `workmem` | `Sort Method: external merge`, `Batches` | memory limit and temp directory usage | cluster-level metrics |
| Skew | per-node time vs rows | max vs average slot time | max vs median task time | **per-slice rows and time** | not applicable | not applicable | per-stage task distribution |
| Cost | credits; background service cost | bytes billed or slot time | DBU by compute | cluster time; queue metrics | server resources | local resources | cluster time |

The engine with the weakest direct spill signal is BigQuery, and the strongest per-worker signals are Redshift's per-slice views and Databricks' per-task distribution. Where an instrument is missing, say the evidence was unavailable rather than reporting a conclusion as though it were measured.
