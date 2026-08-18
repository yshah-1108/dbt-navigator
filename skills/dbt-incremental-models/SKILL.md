---
name: dbt-incremental-models
description: Use when creating or modifying an incremental dbt model — choosing between merge, delete+insert, append, insert_overwrite, and microbatch, setting unique_key, writing the boundary predicate, handling on_schema_change, adding incremental_predicates or clustering, protecting irreplaceable history with full_refresh=false, or planning a backfill. Also use when an incremental model produces duplicates, drops rows, goes stale, or scans the full table every run.
metadata:
  phase: build
---

# Incremental models

This is where teams lose data quietly.

An incremental model that is wrong does not usually fail. It builds green, produces a table of the expected shape, and is off by some rows — duplicated, missing, or stale. The error is discovered weeks later by someone reconciling a total, and by then the cause is buried in history.

Every rule below exists to prevent a specific silent failure. None of them are stylistic.

| Sub-document | Read it when |
|---|---|
| [strategy-selection.md](strategy-selection.md) | You need the narrative behind a strategy — when it is right and how it loses or duplicates data when wrong |
| [strategy-reference.md](strategy-reference.md) | You need the exact DML a strategy generates, per-adapter support, `merge_update_columns`, or the `incremental_predicates` alias rules |
| [schema-evolution.md](schema-evolution.md) | You are setting `on_schema_change`, or a column change must propagate into an incremental table |
| [microbatch.md](microbatch.md) | The model is a large time-series table and the project is on dbt 1.9+ |
| [boundary-patterns.md](boundary-patterns.md) | You are hand-writing the boundary filter |
| [lateness.md](lateness.md) | You are sizing a lookback window, making dedup deterministic, or need a reconciliation path |
| [mutable-sources.md](mutable-sources.md) | Rows can be updated or deleted upstream — CDC, soft deletes, tombstones, restatement |
| [backfilling.md](backfilling.md) | You need the model-side range mechanism; general procedure is in `dbt-shipping-changes` |
| [testing-incrementals.md](testing-incrementals.md) | You are about to claim it works |

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | What it decides |
|---|---|
| `project.warehouse` | Which strategies exist, and what `insert_overwrite` does |
| `project.dbt_version` | Whether `microbatch` and unit tests are available at all |
| `testing.primary_key_incremental` | PK tests for an incremental model |
| `naming.surrogate_key_column` | The key column name |
| `environments.detection` | How to tell dev from prod before running anything destructive |

**Absent field → generic guidance, labelled as generic.** In particular: do not recommend a strategy without knowing the warehouse. Strategy availability and semantics genuinely differ between adapters — `delete+insert` does not exist on every one, and `insert_overwrite` replaces a partition on some and the entire table on others.


## First question: should this be incremental at all?

Incremental is a performance optimization with a correctness cost. It is worth paying only when the cost of a full rebuild is real.

| Signal | Incremental? |
|---|---|
| Rebuild takes minutes and costs little | **No.** A table is simpler and always correct. |
| Rebuild is slow or expensive, data is append-mostly | Yes |
| Source no longer holds the full history | Yes, and `full_refresh=false` |
| Small dimension or lookup | No |
| Grain and logic still changing weekly | Not yet — the churn will cost more than the rebuilds |
| Rows change arbitrarily far back in time | Rarely worth it — every run must reach the whole table anyway |
| You need SCD2 history of a mutable dimension | No — that is a snapshot, see below |

The default should be a table. Reach for incremental when there is a measured reason, and say what the measurement was. An incremental model that did not need to be is a permanent source of subtle bugs bought for nothing.

Two costs that are easy to leave out of the comparison:

- **A full rebuild is provably complete.** An incremental table drifts from its source over time, because no lookback window covers every late arrival. dbt's own guidance is to reset that drift with a periodic full refresh where the table is small enough to allow it. If it is not, the drift is permanent and someone should know that.
- **Incremental logic is a second code path** that CI usually does not exercise, and that every future editor of the model has to reason about. That cost is paid on every change for the life of the model.

### Incremental or snapshot?

Both handle "the source changes". They answer different questions, and choosing the wrong one is expensive in opposite directions.

| | Incremental model | Snapshot |
|---|---|---|
| Question it answers | What is the current state, cheaply? | What did this row look like on a given date? |
| History of changes | Not kept — an update overwrites | Kept as validity windows (SCD2) |
| Cost model | Proportional to new/changed rows | Proportional to changed rows, plus a full source scan to detect them |
| If you configured it wrong | Rebuild it | **History that was never captured cannot be recovered** |
| Right for | Facts, events, aggregates over time | Mutable dimensions whose prior values matter |

If anyone will ever ask "what was this record's value last quarter", the answer must be captured as it happens, and an incremental model does not do that. See `dbt-snapshots` — and note that its decisions are close to irreversible, which is the opposite of the position an incremental model leaves you in.

## Choosing a strategy

This is the decision that determines whether you lose data. Get it right before writing any SQL.

**Check the warehouse first.** Strategy availability and semantics genuinely differ between adapters, defaults differ, and the same config can mean "replace yesterday's partition" on one platform and "replace the entire table" on another. The support matrix and exact generated DML are in [strategy-reference.md](strategy-reference.md).

### The one question that decides it

**Can a row that was previously loaded disappear or change in the source?**

| Source behavior | Strategy | What happens if you choose wrong |
|---|---|---|
| Append-only; rows never change or vanish | `append` | Nothing — but any rerun duplicates |
| Rows can be updated; keys never disappear | `merge` | — |
| Source **reprocesses** a window: rows can vanish | `delete+insert` | `merge` leaves the vanished rows in the target **forever** |
| Whole time partitions are rewritten atomically | `insert_overwrite` | — |
| Large time-series, dbt 1.9+, reliable event timestamp | `microbatch` | — |

### Do not leave the strategy unset

`incremental_strategy` has a default, and **the default is not the same on every adapter.** On some it is `merge`; on others it is `append` when no `unique_key` is set and `delete+insert` when one is. So the same model file, unchanged, upserts on one warehouse and delete-inserts on another — and those differ precisely in what happens to a row that vanished upstream.

Set it explicitly on every incremental model, even when the value matches the adapter's default. The config costs one line and removes an entire class of "it behaved differently in the other environment".

### The four keyed strategies in brief

Each strategy's narrative — what it does, when it is right, and the specific way it loses or duplicates data when it is wrong — is in [strategy-selection.md](strategy-selection.md). The one-line version of each:

- **`merge`** — updates matched keys, **never deletes**. Right when rows update but keys persist; wrong on any reprocessing source, where a vanished row stays in the target forever.
- **`delete+insert`** — deletes the incoming keys (and whatever the predicate covers), then inserts. Right when the source reprocesses a window; unavailable on BigQuery and Spark.
- **`append`** — inserts, full stop. The only correct choice for genuinely immutable events; any rerun of an overlapping range duplicates.
- **`insert_overwrite`** — replaces whole partitions, and **its scope differs by warehouse** (whole table on Snowflake, partitions on BigQuery). Verify what it means on your adapter first.

### `microbatch`

dbt 1.9+. dbt splits the run into one bounded query per time window, derived from `event_time` and `batch_size`, and replaces each window independently using whatever atomic mechanism the adapter provides.

Worth evaluating before hand-writing a boundary on any large time-series model, because it removes four things you would otherwise have to get right: the boundary expression, the lookback, the choice of reprocessing-safe strategy, and the backfill mechanism. Failed batches also retry individually.

It is not a general replacement for `merge`. It needs a trustworthy event timestamp and a time-series shape; a keyed dimension whose rows change arbitrarily far back is not a microbatch model. Details, adapter requirements, the parent-`event_time` cost trap, the UTC assumption, and parallelism rules are in [microbatch.md](microbatch.md).

### Decision summary

```
Is this a large time-series table, on dbt 1.9+, with a reliable event timestamp?
├─ Yes → microbatch (see microbatch.md), unless you need column-level update control
└─ No ↓

Can a previously-loaded row change or disappear in the source?
├─ No, never (immutable events)
│   └─ Is a rerun of the same window possible? (retries, manual reruns)
│       ├─ Yes → merge (dedup protection is worth the cost)
│       └─ Truly no → append
├─ Rows update, keys persist
│   └─ merge, with a non-null unique_key and dedup in the model body
├─ Source reprocesses a window; rows can vanish
│   └─ delete+insert, with incremental_predicates covering the window
│       (unavailable on BigQuery/Spark — use insert_overwrite or replace_where)
└─ Whole time partitions rewritten atomically, warehouse supports it
    └─ insert_overwrite — verify what it means on your adapter first
```

## `unique_key`

```sql
-- single column
unique_key = 'order_id'

-- composite grain: a list, not a concatenated string
unique_key = ['order_date', 'region', 'product_id']

-- surrogate key over the grain columns
unique_key = '<naming.surrogate_key_column>'
```

- **Use a list for a composite grain.** dbt builds the match or delete predicate from the list, column by column. A string containing a comma is not a list and will not do what you want.
- **No `unique_key` column may be null.** Matching is equality, and `null = null` is not true, so a row with a null key component is never matched — it is inserted again on every run. The duplicates accumulate slowly and each one looks like a legitimate row. Either enforce non-null at the source, coalesce to an explicit sentinel, or use a surrogate-key macro that hashes nulls deterministically. Add a `not_null` test on every key column; it is the cheap version of this paragraph.
- **A list of natural grain columns is usually better than a surrogate key** for `delete+insert`: the delete predicate can use them directly, and clustering on the leading column makes it cheap. Matching on a hashed surrogate key forces the warehouse to compare an unindexed hash.
- **The key must exactly match the model's grain.** Missing a grain column means the strategy treats distinct rows as the same row: `merge` overwrites one with the other or errors on the ambiguity, `delete+insert` deletes both and inserts one. Rows are lost, quietly.
- **When the grain changes, the key changes with it.** This is the most common origin of corruption in a previously-working incremental model. Adding a column to the `group by` without adding it to `unique_key` silently starts collapsing rows.
- **The key must be unique in the incoming batch too, not only in the target.** A source that legitimately emits two versions of the same key in one window is a duplicate as far as the strategy is concerned. Deduplicate in the model body — outside `is_incremental()`, so a full refresh gets the same treatment — keeping the latest version per key by the source's own update timestamp.

Verify after building, do not assume:

```sql
select <key_columns>, count(*)
from <explicit_database>.<explicit_schema>.<model>
group by <key_columns>
having count(*) > 1
```

Zero rows, or the key is wrong. Use an explicit database and schema — never `ref()` — for validation queries.

## The boundary predicate: `>=`, not `>`

```sql
{% if is_incremental() %}
    where order_date >= (select max(order_date) from {{ this }})
{% endif %}
```

**`>` drops late-arriving rows at the boundary.** If the target's max is `2024-03-01` and more rows for `2024-03-01` arrive after the run, `> '2024-03-01'` excludes them and they are never loaded. Not delayed — never. The next run's max is higher still, so the window that contained them is never revisited.

`>=` reprocesses the boundary period. The strategy handles the resulting overlap: `merge` updates, `delete+insert` replaces. With `append` it duplicates — which is one more reason `append` is rarely right.

The one narrow exception: on a **load-time** watermark, where the timestamp is assigned by your own pipeline rather than by the business event, `>` is defensible because re-reading the last batch is merely wasteful and nothing can arrive "before" a load that already happened. On an **event-time** watermark it is never defensible. If you are not certain which of the two you have, use `>=` — the cost of the safe choice is compute, and the cost of the wrong choice is data that is never loaded.

### A wider overlap for slower sources

When data can arrive days late, one boundary period is not enough:

```sql
{% if is_incremental() %}
    where order_date >= (
        select dateadd(day, -{{ var('lookback_days', 3) }}, coalesce(max(order_date), '1900-01-01'))
        from {{ this }}
    )
{% endif %}
```

Two things this gets right:

- **Size the lookback to the source's actual observed lateness**, not to a guess. If you do not know it, measure it before choosing a number, and say the number is provisional.
- **`coalesce` on `max()`.** On an empty table — first run, or a table just recreated by a schema change — `max()` returns null, every comparison against it is unknown, and the model loads **zero rows** while succeeding. This is a genuinely nasty failure: a model that builds successfully and stays permanently empty.

Adapter date functions vary. `dateadd` is not universal; use the cross-database macro (`dbt.dateadd()`) or what the warehouse accepts.

### Watermark on the event time or on the load time?

The boundary column choice is a genuine fork, not a detail, and both options have a named failure.

| | Event time (`ordered_at`) | Load time (`loaded_at`) |
|---|---|---|
| Late arrivals | **Missed** unless the lookback covers them | Caught — a late row gets a fresh load timestamp |
| Watermark direction | Can move backwards relative to the source's clock | Owned by your pipeline; always moves forward |
| Duplicates | Prevented by the key, since the same row reprocesses in the same window | **Possible**: the same event can arrive twice with two load timestamps |
| Backfill semantics | A date range means what a person means by it | A date range means "when we loaded it", which nobody thinks in |
| Breaks when | The source's lateness exceeds the lookback | The extraction tool is resynced and re-stamps everything |

Choosing:

- **Event time plus a lookback sized to measured lateness** is the default. It keeps the boundary meaningful and pairs naturally with a partition- or cluster-aligned predicate.
- **Load time** is right when lateness is unbounded or unmeasurable — you cannot pick a lookback that is wide enough. It requires an idempotent strategy and a correct row-level key, because the key is now the only thing preventing duplicates.
- **Both, with an `or`**, catches more and costs more, and the predicate on the target must then cover both windows. It is a legitimate choice; it is not free, and the cost is a wider target scan every run.

Two failure modes specific to this choice:

- **A watermark taken from a source column that multiple systems write** can move backwards. If two upstream servers stamp rows from their own clocks and one is behind, its rows can carry a timestamp earlier than a maximum you have already recorded, and they fall outside the window permanently. This is clock skew, and no lookback smaller than the skew fixes it.
- **A resync of the extraction tool rewrites load timestamps**, so a load-time watermark reprocesses data it already has. With a correct key that is wasted compute; without one it is duplication.

### Timezone mismatch between boundary and source

The boundary compares a value from `{{ this }}` against a value from the source. If those are in different timezones — or if one is a session-dependent `timestamp` and the other is stored as UTC — the comparison is off by the offset.

The symptom is not an error. It is a window shifted by some hours: rows at the edge are excluded from the run that should have loaded them, and a daily aggregate blends two partial days. Every number is plausible.

Normalise timezone and type in staging so downstream models inherit one convention, and make the boundary column's timezone explicit in its name or its description. If the model mixes a date and a timestamp in the same comparison, be explicit about how the date is being promoted — implicit casts are where the offset hides.

### Guard against a boundary that cannot advance

Two related failures, both of which produce a permanently stuck model with green runs:

- **A future-dated row poisons the watermark.** One bad row with a timestamp in 2099 makes `max()` 2099, and every subsequent run loads nothing. Bound the boundary read to exclude implausible futures, or test that no row exceeds the current period.
- **A boundary computed from the target when the target is empty** loads zero rows, as above — and stays empty, because the table is still empty on the next run. The `coalesce` is what prevents it.

Neither of these fails. Both are caught by a recency test, which is the cheapest protection available and the one most often missing — see [testing-incrementals.md](testing-incrementals.md).

## `on_schema_change`

| Setting | Behavior when the model's columns change |
|---|---|
| `fail` | Build errors. Loudest, and the right default. |
| `append_new_columns` | New columns added; existing rows null for them. Removed columns retained. |
| `sync_all_columns` | New columns added, removed columns **dropped**, including their data. Also applies type changes. |
| `ignore` | New columns **never appear**. Build succeeds. Nothing signals anything. |

**`ignore` is the default when the config is unset**, and it is the trap. Add a column to an unconfigured incremental model and the build succeeds while the column silently does not exist. Downstream reads null, or errors on a missing column far from the cause.

Set it explicitly on every incremental model. `fail` forces the decision that actually matters — whether history needs backfilling — to be made by a person rather than by a default. `sync_all_columns` drops data on a rename, since a rename looks like one removal plus one addition; use it only where you are certain that is acceptable.

What each setting does to **existing rows** (no setting backfills a new column — historical rows stay null), the behaviours that surprise people (`ignore` is asymmetric; detection is top-level only; only `sync_all_columns` handles a type change), and the rule for a contracted model are in [schema-evolution.md](schema-evolution.md).

## `incremental_predicates`

`merge` and `delete+insert` both need to locate rows in the target. Without a hint, that means scanning the whole target table on every run — which grows without bound while the incoming data does not.

```sql
incremental_predicates = [
    "DBT_INTERNAL_DEST.order_date >= dateadd(day, -30, current_date)"
]
```

- **`DBT_INTERNAL_DEST` is the target-table alias** dbt uses in the generated **merge** statement, and `DBT_INTERNAL_SOURCE` the incoming one. The column named must exist in the **target's** schema, i.e. the model's own output columns — not a CTE alias and not a source column name.
- **The aliases are not reliably in scope outside `merge`.** With `delete+insert` the predicate lands in a plain `delete from <target>`, where whether an alias is present has varied between adapter versions and where some warehouses reject an alias outright. This has broken real projects on an adapter upgrade with no model change. Read the generated statement in `target/run/` rather than assuming, and re-read it after upgrading an adapter.
- **The predicate must cover everything you intend to change.** A row outside the predicate range cannot be updated or deleted this run. Too narrow, and corrections outside the window silently do not apply. This is also the mechanism by which `delete+insert` removes rows that vanished from the source — the predicate defines the window being made to match.
- **Widen it for a backfill.** A predicate hardcoded to 30 days makes a backfill of last year a no-op. Parameterize it.
- On `insert_overwrite` the predicate *is* the delete scope rather than a performance hint, so an over-wide one destroys data and an empty one destroys all of it.
- The config is also accepted under the older name `predicates`. Grep for both when auditing a project.

Add predicates when the target is large. "Large" is a measurement, not a feeling — check the row count and the query profile. See `dbt-performance-tuning`.

## Clustering and partitioning

Clustering (or partitioning, depending on the warehouse) on the incremental time column makes the merge or delete scan a fraction of the table instead of all of it.

```sql
-- syntax and concept both vary by warehouse
cluster_by = ['order_date']
```

- **Cluster on the leading `unique_key` column**, normally the date or time column the predicate filters on.
- **Align three things: the boundary column, the predicate column, and the clustering or partitioning column.** They should be the same column. A predicate on a column the table is not organised by cannot prune anything, so the scan is full-table regardless of how narrow the predicate reads. This is the most common reason a predicate is added and the run gets no faster.
- The concept is not portable: some warehouses cluster, some partition, some rely on sort keys or distribution keys. Check the adapter.
- **On partition-replacement strategies the alignment is a correctness requirement, not an optimisation.** The replaced unit is the partition, so a partition grain coarser than the reprocessed window replaces more than intended, and finer replaces less.
- Clustered-`merge` versus partition-replacement is data-dependent: one tends to win at small volumes and the other scales better as volume grows, with a crossover that shifts by workload. Measure on your own data rather than porting a conclusion. It is not free — there is a write-time maintenance cost — so justify it with a measurement.

### Avoiding a full target scan

Three separate scans happen on an incremental run, and they need separate treatment. Conflating them is why "I made it incremental and it is still slow" is such a common report.

| Scan | Reduced by |
|---|---|
| Reading the upstream source | The `is_incremental()` boundary filter in the model body |
| Reading the target to find the boundary | Clustering/partitioning on the boundary column; the `max()` is then a metadata-ish read |
| Locating rows in the target to update or delete | `incremental_predicates`, aligned with the physical layout |

An incremental model with a boundary filter and no predicate still reads the whole target on every merge. The build got faster because the source scan shrank, and then stopped getting faster because the target scan grows forever. See `dbt-performance-tuning` for how to measure which one you are paying for.

## `full_refresh = false`

```sql
{{ config(
    materialized = 'incremental',
    full_refresh = false,
) }}
```

This makes `--full-refresh` a no-op for this model. Add it whenever **the source cannot reproduce the history the table holds**:

- The source has a retention window shorter than the table's history
- The table spans a migration and an older source no longer exists
- Upstream data was corrected in place, and the table holds the pre-correction record deliberately
- The table is the only remaining record of something

Without it, one `--full-refresh` — typed by someone debugging something unrelated, or matched by a wildcard selector — destroys data that cannot be recovered. There is no undo.

For extra protection, a compile-time guard fails before any data is touched:

```sql
{% if not is_incremental() and <environments.detection expression for prod> %}
    {% if adapter.get_relation(this.database, this.schema, this.identifier) is not none %}
        {{ exceptions.raise_compiler_error(
            "Full refresh of " ~ this.identifier ~ " is blocked: this table holds history "
            ~ "the source cannot reproduce. Use a targeted backfill instead."
        ) }}
    {% endif %}
{% endif %}
```

Use the project's own dev/prod detection expression from `environments.detection`. With no contract, ask which expression the project uses rather than guessing — a wrong guess here either blocks dev work or fails to protect production.

Whenever `full_refresh = false` is set, **say why in a comment.** A future engineer hitting the no-op needs to know it is deliberate, or they will remove it.

### Backfilling a model that cannot be full-refreshed

Setting the flag removes the blunt instrument, so the targeted one has to exist. A `full_refresh = false` model with no range parameter cannot be corrected at all — the only available operation is the daily incremental run, and if history is wrong it stays wrong.

- **Build the range override in at the same time as the flag.** Not later. This is the single most important sequencing point in this section.
- **Verify the source still holds the range before running anything.** On a normal model a mistaken backfill is repaired by a full refresh. Here there is no repair: if upstream retention has aged the data out, the backfill replaces real rows with nothing, permanently.
- **Take a copy of the affected range first.** A table whose history cannot be reproduced from the source deserves a backup relation before it is modified, and that copy is also the only baseline you will have for verification.
- **`--full-refresh` is respected as a no-op, which is a mercy and a trap.** Someone debugging an unrelated failure runs a wildcard full refresh; this model is skipped and everything else is rebuilt. That is the flag working. But it also means a genuinely needed rebuild silently does not happen and the run reports success.

The general backfill procedure — chunking, ordering, per-chunk verification, downstream reruns, consumer notification — is in `dbt-shipping-changes`, and it applies unchanged. What is different here is only that the safety net is gone.

### Zero-downtime rebuilds

Where a rebuild is affordable but the table cannot be unavailable while it runs, the shape is: build the new version into a separate relation, verify it, then swap. Some projects have a deployment mechanism for this already; some warehouses offer a zero-copy clone that makes the baseline copy nearly free.

Three things decide whether it is worth it:

- **The swap must be atomic**, or consumers see a missing or half-built table — which is the outage you were avoiding.
- **Verify before the swap, not after.** The whole point is that the old table is still serving traffic while you check. Compare the shadow relation against the live one row-by-row; the criteria are in [testing-incrementals.md](testing-incrementals.md).
- **It does not help on a `full_refresh = false` model**, because the reason for that flag is that the source cannot reproduce history — so a shadow build has nothing to build from. The two techniques address different problems and do not compose.

Do not invent this mechanism inside a model. It is deployment machinery; check whether the project already has it, and if it does, use theirs.

## Idempotency and late-arriving data

The property to hold onto: **running the model twice over the same source data must produce the same table as running it once.** Every rule in this section follows from it, and a model that fails it has a defect that shows up as a mystery months later, on a day when a job was retried.

Three defenses against lateness, and you need to choose consciously among them:

1. **A `>=` boundary with a lookback window** sized to the source's observed lateness.
2. **A strategy that tolerates reprocessing** — `merge` or `delete+insert`. With `append`, late data becomes a duplicate.
3. **A predicate wide enough** that the late rows' target range is actually modifiable.

All three must agree. A 7-day lookback with a 3-day predicate can read the late rows and then fail to write them, because the target rows they should replace are outside the modifiable window. The build succeeds and the correction does not land — the most confusing variant of this bug, because the model appears to have processed the data.

The how of each defense — sizing the lookback from the source's real lateness distribution, making deduplication deterministic, and the reconciliation path a high-water mark cannot replace — is in [lateness.md](lateness.md).

## Mutable sources, deletes, and restatement

If rows can be updated or deleted upstream, or if the source restates a period after the fact, the boundary is only half the problem: a deleted row has no timestamp to be late with, and a restated period may not move any watermark at all.

That family of problems — change-data-capture feeds, soft deletes, hard-delete detection, tombstones, full-snapshot sources, and source restatement — is in [mutable-sources.md](mutable-sources.md).

## Backfilling

Any incremental model that will ever need a correction needs a way to reprocess a specific range without a full refresh. Design that in from the start — retrofitting it under pressure, during an incident, is how ranges get mistyped.

The detailed procedure — batching, sequencing, baseline capture, per-batch verification, ordering downstream reruns, notifying consumers — is in `dbt-shipping-changes`, which owns post-merge operations, and its [backfilling.md](../dbt-shipping-changes/backfilling.md) sub-document. The model-side range mechanism and the incremental-specific ways a backfill silently does nothing are in [backfilling.md](backfilling.md). For the boundary-filter shapes themselves, see [boundary-patterns.md](boundary-patterns.md).

Four points that are specific to incremental models rather than to backfills in general, and that decide whether the procedure can work at all:

- **The strategy determines whether a backfill is repeatable.** `merge` on a correct key and `delete+insert` are idempotent over a range. `append` is not: rerunning appends the range again, so an `append` model has no safe backfill procedure until the range is deleted by hand.
- **`incremental_predicates` must reach the range**, or the run is green and nothing changes. This is the most common way a backfill is declared done while the numbers are unchanged.
- **Downstream incremental models will not revisit the corrected period on their own.** Their boundaries have already moved past it. The corrected numbers sit in one model and every consumer still reports the old ones — which is worse than the original problem, because now two tables disagree.
- **On a microbatch model the mechanism is built in**: `--event-time-start` and `--event-time-end`, no range parameter to design and no predicate to widen. That is a real argument for microbatch on any model likely to need corrections. See [microbatch.md](microbatch.md).

Run backfill chunks sequentially, not in parallel. Concurrent writes to one table produce lock contention at best; on strategies that stage through a deterministically-named temporary relation, two overlapping runs of the same model can also overwrite each other's staging data and produce interleaved results. That risk is real for concurrent scheduled runs too, not only for manual backfills.

## Verify, twice

An incremental model has two code paths and they must both be exercised. Most incremental bugs live in exactly the difference between them.

```bash
dbt compile --select <model>              # full-refresh path compiles
dbt build --full-refresh --select <model> # builds from scratch
dbt build --select <model>                # now exercises the incremental path
dbt build --select <model>                # and again — counts must not move
```

The second `build` proves the incremental branch runs. **The third proves it is idempotent**, and that is the one people skip: if row counts move on a run with no new source data, the model duplicates on every retry, permanently.

Then check, by query against an explicit database and schema:

- **Duplicate keys**: the `group by ... having count(*) > 1` query above returns zero rows.
- **Row count after the second and third runs.** If a run added rows for a period that was already complete, the strategy or the boundary is wrong.
- **Boundary period totals.** Compare the overlap period's totals before and after. They should match, or differ only by genuinely new data.
- **The compiled SQL of the incremental path, and the generated DML.** Read both. A Jinja boundary expression that renders to something unintended is invisible in the model file and obvious in `target/compiled/`; the merge or delete predicate is only visible in `target/run/`.

The stronger evidence — proving an incremental build equals a full rebuild, unit tests on both branches, and the recency test that catches a model which silently stopped loading — is in [testing-incrementals.md](testing-incrementals.md). See also `dbt-verification`.

## Completion checklist

- [ ] Incremental justified by a measurement, not assumed; snapshot ruled out if history of changes is needed
- [ ] Grain stated; `unique_key` exactly matches it
- [ ] Strategy chosen by answering whether source rows can change or disappear
- [ ] `incremental_strategy` set explicitly — never left to an adapter-specific default
- [ ] Strategy confirmed available on this warehouse, and its semantics there confirmed
- [ ] `microbatch` evaluated first for a large time-series model on dbt 1.9+
- [ ] `delete+insert` used if the source reprocesses; not `merge`
- [ ] `append` used only for genuinely immutable data with no rerun path
- [ ] `insert_overwrite` semantics verified for this adapter, not assumed portable
- [ ] `unique_key` is a list for a composite grain, and every column in it is non-null
- [ ] Deduplication in the model body, outside `is_incremental()`, with a deterministic tiebreak
- [ ] Boundary uses `>=`, with a lookback sized to measured source lateness
- [ ] Watermark column chosen deliberately between event time and load time, with the trade-off stated
- [ ] Boundary and source timezones confirmed to match; no implicit cast hiding an offset
- [ ] `max()` wrapped in `coalesce` so an empty target does not load zero rows
- [ ] Boundary cannot be poisoned by a future-dated row
- [ ] `on_schema_change` set explicitly — never left to default to `ignore`
- [ ] `append_new_columns` or `fail` if the model has an enforced contract
- [ ] `incremental_predicates` present on a large target, with the generated DML read to confirm the alias is valid on this adapter
- [ ] Predicate range covers everything the run intends to change, including backfills
- [ ] Boundary column, predicate column, and clustering/partitioning column are the same column
- [ ] `full_refresh = false` on irreplaceable history, with a comment saying why and a range override already in place
- [ ] Lookback, strategy, and predicate all agree on the late-arrival window
- [ ] A reconciliation path named — periodic full refresh, bounded backfill, or source comparison
- [ ] Deletes and restatement handled if the source is mutable
- [ ] PK tests per `testing.primary_key_incremental`; uniqueness on the full key and a recency test present
- [ ] Built three times; duplicate-key query returns zero rows; counts stable on the third run
- [ ] Compiled incremental SQL and generated DML read, not assumed

## The failure modes that lose data

1. **`merge` on a reprocessing source.** A key disappears upstream and the target keeps the old row forever. Permanent, self-perpetuating, and invisible to every test — the row is individually valid. The single most expensive mistake in this document.
2. **`>` on the boundary.** Late rows at the boundary timestamp are never loaded. Not delayed. The window is never revisited.
3. **`unique_key` narrower than the grain.** Distinct rows are treated as the same row, and one silently overwrites or deletes the other. Row counts drop with no error.
4. **A null in a `unique_key` column.** Matching is equality, `null = null` is not true, so the row is never matched and is inserted again on every run. Duplicates accumulate slowly and each looks legitimate.
5. **`on_schema_change` left unset.** A new column silently never appears. The build is green, downstream reads null, and the diff looks correct. On a contracted model the contract is now false and nothing detected it.
6. **`max()` on an empty target with no `coalesce`.** Every boundary comparison is unknown, zero rows load, the run succeeds. The model stays empty indefinitely and nothing complains.
7. **A future-dated row poisoning the watermark.** One bad timestamp and the boundary can never advance. Green runs, zero rows, forever, until someone asks why the table stopped.
8. **Missing `full_refresh = false` on irreplaceable history.** One wildcard `--full-refresh` and the data is gone with no recovery path. And the mirror image: `full_refresh = false` set with no range override, so the table cannot be corrected at all.
9. **A predicate narrower than the lookback.** Late or corrected rows are read but cannot be written, because their target rows sit outside the modifiable window. The run succeeds and the correction does not land.
10. **`append` on anything rerunnable.** Every retry adds another copy of the same rows, and no uniqueness test exists to notice.
11. **`insert_overwrite` ported between warehouses.** The config that replaced one partition on the origin platform replaces the entire table on the destination one. If the run's window was one day, the table is now one day of history.
12. **A timezone or type mismatch in the boundary comparison.** The window shifts by the offset, edge rows are excluded, and daily aggregates blend two partial days. Every number is plausible.
13. **Non-deterministic deduplication.** Ties in the ordering column make the surviving row arbitrary, so reprocessing changes values with no source change. Counts match and a value silently flipped.
14. **Deduplication written only inside `is_incremental()`.** The full refresh admits every duplicate, and the merge then maintains them faithfully forever.
15. **Clock skew between source systems.** A watermark can be ahead of a lagging server's rows, which then fall outside the window permanently. No lookback smaller than the skew recovers them.
16. **A parent without `event_time` on a microbatch model.** Each batch scans it in full, so a 90-batch backfill performs 90 full scans. Nothing errors; the only symptom is the bill.
17. **A source restatement that moves no watermark.** The source corrects a closed period in place without touching any timestamp the boundary reads. The correction is never picked up and only a reconciliation query finds it.
