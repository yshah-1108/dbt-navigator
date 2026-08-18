---
name: dbt-snapshots
description: Use when creating or modifying a snapshot for SCD2 history tracking, choosing between the check and timestamp strategies, selecting check_cols, deciding whether to invalidate hard deletes, debugging a snapshot that captures no history or too much, or recovering a corrupted snapshot. Covers why a snapshot decision is effectively irreversible.
metadata:
  phase: build
---

# Snapshots

A snapshot records the history of a mutable table as slowly-changing-dimension type 2 (SCD2): each version of a row gets a validity window, so you can ask what a record looked like on a given date.

Snapshots are the one dbt artifact you cannot fix later. Every other model can be dropped and rebuilt from its source. **A snapshot's value is history the source no longer contains** — if you configured it wrong six months ago, the changes you needed were never captured, and no amount of rerunning produces them. Treat the initial configuration as a one-way decision and verify it before the first production run.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

Relevant fields: `naming.model_pattern` and `naming.separator` for the snapshot name, `naming.timestamp_column_suffix` if the snapshot query casts timestamps.

Absent contract → name the snapshot consistently with its siblings and say plainly that you are following observed convention rather than a declared one.

## Establish the dbt version before writing config

Snapshot configuration changed substantially in dbt Core 1.9, and the two styles are not interchangeable. Writing the newer form against an older engine fails; writing it against an older *snapshot table* produces mixed data, which is worse.

| Capability | Availability |
|---|---|
| SQL file with a `{% snapshot %}` block | All versions |
| YAML snapshot definition with `relation:` and `config:` | 1.9+ |
| `target_schema` required | Through 1.8. Optional from 1.9, defaulting to the environment's schema |
| Standard `schema` / `database` configs | 1.9+ |
| `hard_deletes: ignore \| invalidate \| new_record` | 1.9+ |
| `invalidate_hard_deletes: true` | All versions; **legacy** from 1.9 |
| `dbt_valid_to_current` | 1.9+ |
| `snapshot_meta_column_names` | 1.9+ |
| `--empty` on `dbt snapshot` | 1.9+ |
| `materialized: snapshot` enforced | 1.4+ raises a parse error otherwise |

`hard_deletes` support is adapter-dependent as well as version-dependent — it was introduced for a specific set of adapters, so verify support for the adapter in use rather than assuming. Where you cannot confirm, say the behaviour is version- and adapter-dependent and check it before relying on it.

**The `target_schema` change has an operational edge.** Through 1.8 it was required and environment-independent, so a developer running `dbt snapshot` locally wrote into the same table production uses. From 1.9 the default is the environment's schema, which is safer — but it also means a project upgrading and dropping `target_schema` moves where the snapshot lands. Moving a snapshot's location is not a rename; the history is in the old table. Decide deliberately.

### The YAML form

```yaml
snapshots:
  - name: <snapshot_name>
    relation: source('<source_name>', '<table_name>')
    config:
      unique_key: <key_column>
      strategy: timestamp
      updated_at: <updated_at_column>
      hard_deletes: ignore
```

Cleaner than the SQL block form and the recommended shape from 1.9 onward. The SQL block form remains valid and is required wherever the snapshot query needs any SQL at all — but see below on keeping that query minimal.

### Migrating an existing snapshot to the newer configs

This is the sharp edge in the 1.9 changes and it deserves its own warning: **dbt does not migrate snapshot data for you.** Enabling `dbt_valid_to_current`, `snapshot_meta_column_names`, or switching `invalidate_hard_deletes` to `hard_deletes` on a snapshot that already holds history produces a table where old rows follow the old convention and new rows follow the new one. Every downstream point-in-time query then has two cases to handle, and nothing marks the boundary.

Concretely: with `dbt_valid_to_current: '<sentinel date>'`, pre-existing current rows keep `null` in `dbt_valid_to` while new ones get the sentinel. A `where dbt_valid_to is null` filter now returns half the current rows, and a sentinel comparison returns the other half.

The safe sequence:

1. Copy the snapshot table to a backup relation. Not optional.
2. Decide whether to adopt the new configs at all on this snapshot — for an existing one, "no" is often correct.
3. If yes: alter the table so its columns match the new configuration, and update the pre-existing rows to the new convention in the same change.
4. Run `dbt snapshot` in a non-production environment and read the resulting rows before running it anywhere else.
5. Prefer adopting the new configs on **new** snapshots only, and leaving existing ones alone.

`invalidate_hard_deletes` and `hard_deletes` cannot both be set; that is a config error, not a precedence question.

## When a snapshot is the right answer

| Snapshot | Do not snapshot |
|---|---|
| A mutable dimension whose source overwrites in place | Events or facts — already immutable and timestamped |
| Attribute changes you need to reconstruct historically | Data you own the schema of — add audit columns at the source |
| An audit trail for a table with no built-in history | A table the upstream system already versions |
| Slowly changing, small-to-moderate row counts | Large, high-churn tables — cost grows with every change captured |

Two questions decide it: **does the source overwrite history**, and **will anyone need the prior value?** If either answer is no, a snapshot is cost with no return.

## Alternatives to a snapshot

A snapshot is one of four ways to get history, and it is the right one less often than its convenience suggests. Compare before committing, because the decision is close to irreversible.

| Approach | How it works | Better than a snapshot when | Worse when |
|---|---|---|---|
| **Snapshot** | dbt polls the table on a schedule and records what changed since last time | The source overwrites in place and offers nothing else. Small to moderate row counts | Changes matter at sub-run resolution, or volume is high |
| **CDC from the source** | The source system or a log-based tool emits every change with its own timestamp | You need every intermediate state, or true event-time validity windows, or the table is large and high-churn | It does not exist and getting it means a project. CDC streams are also harder to reason about and can deliver out of order |
| **SCD2 built incrementally** | An incremental model closes and opens validity windows itself | The source already carries a reliable `updated_at` **and** append-only change rows, or you need control over the window semantics | You have to implement and test window-closing logic that a snapshot gives you correctly for free |
| **Source-side audit columns** | Add `valid_from` / `valid_to` where the data is produced | You own the source schema. It is the cheapest correct answer and nobody reaches for it | You do not own the source, which is the usual case |
| **Warehouse time travel** | Query the table as of a past timestamp | A one-off investigation, or recovery | It is a retention window, not history. It expires, and it cannot answer questions older than the window |

Two decision rules worth stating:

- **If you own the source schema, add the audit columns instead.** A snapshot exists to compensate for not controlling the source. Snapshotting a table you could have timestamped is choosing a polling loop over a fact.
- **If sub-run resolution matters, no snapshot will do.** A snapshot sees only the state at run time, so a value that changes twice between runs records once, and a change that reverts records not at all. That is a property of polling, not a configuration mistake — the fix is CDC.

Time travel deserves a specific caution: it is genuinely useful for recovering a snapshot you damaged, and it is not a substitute for one. The window is finite, and by the time anyone asks a historical question the relevant period is usually outside it.

## The trap: snapshotting a model instead of a source

A snapshot can point at a `source()` or a `ref()`. Pointing it at a model is the most consequential mistake in this skill, because the snapshot inherits every property of that model — including the parts you will want to change.

- **A filter in the model becomes a delete in the snapshot.** If the staging model has `where status != 'archived'`, rows leaving that filter look identical to rows leaving the source. With hard-delete invalidation on, they get closed as deleted.
- **Refactoring the model rewrites your history's meaning.** Change a `case` expression in staging and the snapshot starts recording different values for the same underlying data. The history is now a mix of two definitions with nothing marking the boundary.
- **A column dropped from the model stops being tracked.** Silently, with no error.

Prefer `source()` and keep the snapshot query as close to raw as possible. If you must snapshot a model, snapshot the thinnest possible one — casting and renaming only, no filtering, no derived columns, no joins — and treat it as frozen. Note that freeze in its description so the next engineer does not refactor it.

## Strategy: check vs timestamp

The other irreversible decision. Both detect change; they fail differently.

| | `timestamp` | `check` |
|---|---|---|
| Detects change by | comparing an `updated_at` column | comparing column values row by row |
| Requires | a timestamp the source updates on **every** field change | nothing |
| Cost | low | higher — full or wide row comparison |
| Fails by | **missing changes silently** when the source forgets to bump the timestamp | capturing too many versions when volatile columns are included |
| Scales to large tables | yes | poorly |

```
Does the source have an updated_at column?
├─ No  → check
└─ Yes → Is it updated on EVERY field change, guaranteed by the source system?
         ├─ Yes, verified → timestamp
         └─ Not sure      → check
```

"Not sure" resolves to `check`. The asymmetry matters: `check` costs compute, `timestamp` costs history you will never get back. Verify the guarantee before choosing `timestamp` — look for rows where a business column differs from a prior known value while `updated_at` did not move. One counterexample disqualifies it.

**Note the tension with dbt Labs' own guidance**, which recommends `timestamp` as the default. Their reasoning is sound and worth understanding: `timestamp` tracks one column, so it absorbs source schema changes without any config update, whereas a `check_cols` list has to be maintained as the source evolves and is a standing source of drift. That is a real maintenance argument.

The reason to still resolve uncertainty toward `check` is the failure asymmetry, not a disagreement about maintenance. An unmaintained `check_cols` list produces a **visible** gap — a column you can see is not in the list, fixable going forward. An `updated_at` column that does not always move produces an **invisible** gap in history that no later change recovers. Where the guarantee is verified, take `timestamp` and its maintenance advantage. Where it is merely likely, `check` is the safer default and the cost is compute.

Neither strategy detects a change that happens and reverts between two runs. Both see only the state at run time. If intra-run changes matter, a snapshot is the wrong tool — you need change-data-capture at the source.

### `check` with an `updated_at` column

A less-known combination worth knowing: the `check` strategy accepts an `updated_at` config as well. Change detection still comes from comparing `check_cols`, but when a change is found, the validity timestamps are taken from the `updated_at` value rather than from the run's clock — falling back to the current timestamp when that value is null.

This is the right configuration for the common middle case: an `updated_at` column that is *approximately* trustworthy — good enough to timestamp a change accurately, not good enough to be the sole detector of one. It buys better `dbt_valid_from` precision without betting history on the column moving.

Verify support on the version in use before relying on it; the behaviour is documented for recent versions and this is not a configuration to assume.

### check_cols

`check_cols: all` compares every column. Right for a genuinely narrow dimension; wrong the moment the source carries a load timestamp, row hash, or ETL batch id — those change every load, so every load produces a new version and the snapshot becomes a log of pipeline runs rather than of business change.

An explicit list is usually better:

```yaml
check_cols: [<name_column>, <status_column>, <tier_column>, <owner_column>]
```

Include exactly the columns whose change you would want to see in a report. Exclude anything the pipeline touches.

**What `check_cols: all` actually costs**, since "higher" is not specific enough to decide with:

| Cost | Detail |
|---|---|
| Comparison width | Every column is compared on every row, every run. On a wide table that is a large scan and a large hash |
| Storage growth | A new version per changed row. With a volatile column included, that is a new version per row per run — the table grows by its own row count each run, without bound |
| Downstream cost | Every model reading the snapshot now reads that growth, and every point-in-time join has more versions to range over |
| Irreversibility | The spurious versions are history now. Removing the volatile column from the list stops new ones and does not remove the old ones |

The compounding case is the one that bites: `check_cols: all` on a table whose loader adds a batch id produces a table that doubles in a week and cannot be pruned without destroying the real history mixed into it. The check is cheap: run the snapshot twice with no source change and confirm zero new rows. If the second run inserted rows, a mechanical column is in scope.

A middle option worth knowing: hash the business columns into one surrogate column in a thin upstream view and check that single column. It gets the "no list to maintain" property of `all` while excluding mechanical columns explicitly — at the cost of not being able to tell *which* column changed from the snapshot alone.

The cost of an explicit list: **a column not in `check_cols` is not tracked at all.** Its value is whatever it happened to be when a tracked column last changed. Adding it later starts tracking from that day forward, not retroactively — so the list is also close to irreversible. Err toward including a business column you are unsure about, and toward excluding anything mechanical.

## Configuration

| Config | Purpose |
|---|---|
| `unique_key` | Column or list identifying a record across versions — required |
| `strategy` | `check` or `timestamp` — required |
| `check_cols` | Required for `check`: `all` or an explicit list |
| `updated_at` | Required for `timestamp`; optional with `check` |
| `hard_deletes` | `ignore` (default), `invalidate`, or `new_record` (1.9+, adapter-dependent) |
| `invalidate_hard_deletes` | Legacy equivalent of `hard_deletes: invalidate`. Cannot be combined with `hard_deletes` |
| `schema` / `database` | Where the snapshot lands (1.9+) |
| `target_schema` | Required through 1.8; optional from 1.9 |
| `dbt_valid_to_current` | Value written for the current version's end of validity; default null (1.9+) |
| `snapshot_meta_column_names` | Rename the meta columns to local convention (1.9+) |

Two configs deserve comment rather than a table row.

`dbt_valid_to_current` set to a far-future sentinel makes range predicates simpler — `event_at >= dbt_valid_from and event_at < dbt_valid_to` with no `or ... is null` branch, which removes the most common consumption bug in this skill. Worth adopting on a **new** snapshot. On an existing one it splits the table into two conventions; see the migration warning above.

`snapshot_meta_column_names` is a naming convenience with a real cost: every piece of documentation, every example, and every colleague's mental model uses the default names. Renaming them means anyone reading a downstream model has to learn the mapping. Rename only where a project-wide convention genuinely requires it, and document the mapping next to the snapshot.

**`unique_key` must actually be unique.** Duplicates produce undefined SCD2 behaviour — overlapping windows, versions attributed to the wrong record — and the damage accumulates run over run. Verify before the first run:

```sql
select <unique_key>, count(*)
from <source_relation>
group by <unique_key>
having count(*) > 1
```

Zero rows, or fix the key. If uniqueness needs multiple columns, declare the list rather than concatenating into a string — a separator that appears in the data creates collisions.

### Meta columns dbt adds

| Column | Meaning |
|---|---|
| `dbt_scd_id` | Identity of this specific version |
| `dbt_updated_at` | The source change timestamp recorded when this version was inserted |
| `dbt_valid_from` | Start of this version's validity |
| `dbt_valid_to` | End of validity; null (or the `dbt_valid_to_current` value) marks the current version |
| `dbt_is_deleted` | Present only with `hard_deletes: new_record` (1.9+). True on the row recording a deletion |

**Which clock populates them depends on the strategy**, and this is the detail that decides whether validity windows mean anything precise:

| Strategy | `dbt_valid_from`, `dbt_valid_to`, `dbt_updated_at` come from |
|---|---|
| `timestamp` | The source's `updated_at` value. The run's own clock is **not** used |
| `check` | The run's clock at execution time |
| `check` with `updated_at` set | The source's `updated_at`, falling back to the run clock when null |

The consequence is a real difference in what the history means. With `timestamp`, a window boundary is when the source says the record changed — accurate, and it lands correctly even if dbt ran hours later. With plain `check`, the boundary is when **dbt noticed**, so with daily runs windows are accurate to a day at best, and a change made shortly after a run is stamped a full cycle late.

Neither is wrong; they answer different questions. What is wrong is a downstream model that treats a `check`-strategy `dbt_valid_from` as the time the business event occurred. If downstream logic needs true event time, the source must supply it and the strategy must be one that uses it.

On insert, `dbt_valid_from` and `dbt_updated_at` are set from the same value — they mean validity start and recorded change time respectively, and they diverge for later versions of the same record.

## Hard deletes

By default a row that vanishes from the source keeps its snapshot row open — asserting the record is still current, which is wrong for a source that truly deletes.

From 1.9 there are three options rather than two, and the third is materially better than the one it supplements:

| Setting | Behaviour |
|---|---|
| `hard_deletes: ignore` (default) | Nothing happens. A deleted record stays open forever |
| `hard_deletes: invalidate` | `dbt_valid_to` is set to the run time for any `unique_key` absent from the source. Equivalent to the legacy `invalidate_hard_deletes: true` |
| `hard_deletes: new_record` | A new row is inserted with `dbt_is_deleted` true, and the previous version is closed |

`invalidate` and `new_record` differ in a way that matters for querying:

- With `invalidate`, a deleted record's history simply stops. There is a gap: no row is valid after the deletion, so a point-in-time query for a later date returns nothing and cannot distinguish "deleted" from "never existed".
- With `new_record`, the timeline is continuous. A row states explicitly that the record was deleted as of that moment, and if it is later restored a further row states that too. Deletion becomes a queryable fact rather than an absence.

Prefer `new_record` for a new snapshot where deletions are meaningful; it costs one column and answers questions `invalidate` cannot. Prefer `invalidate` only where gaps are acceptable and the extra column is not wanted. Note the migration warning above: switching an existing snapshot between these is a data migration, not a config change.

| Enable deletion tracking when | Leave it off when |
|---|---|
| The source hard-deletes and deletion means "no longer valid" | The source soft-deletes with a status column |
| You need accurate "currently active" queries | Extraction can partially fail, so absence may be a pipeline artifact |
| The snapshot query is unfiltered | The snapshot query has a `where` clause |

The last row is the sharp edge. **A filter plus deletion tracking records every row that stops matching the filter as deleted.** Combining the two is almost always a bug — pick one.

A worse variant: an extraction that silently returns a partial result closes every missing record at once. The snapshot cannot distinguish that from a mass deletion, and reopening those windows correctly is manual work. If the pipeline is not reliably complete, leave deletion tracking off.

Worth stating the defence, since the failure is common: a row-count assertion on the source, or a threshold on how many records may be closed in a single run, catches the mass-deletion case before it is history. There is no built-in guard.

## Schema changes to the snapshot query

Changing the columns your snapshot query selects does not fail the run. dbt reconciles the difference against the destination table by creating the new columns there, and by widening string types where the adapter requires it (for example `varchar` on Redshift).

That reconciliation is deliberately conservative in two directions, and both are permanent:

| Change to the query | What happens to the snapshot table |
|---|---|
| Column added | Created in the destination. Historical rows are null for it — the addition is **not** retroactive, and nothing re-derives what the value was when those versions were captured |
| Column removed | **Not dropped.** It stays, permanently null going forward, and no error says so |
| Type changed (string → date) | **Not changed.** The destination keeps the old type. Only string *widening* happens |

The consequence that matters: because none of this errors, a snapshot's schema drifts quietly, and the historical rows never gain the new information. If you need a column's history, it must be in the snapshot before the history you care about accumulates — which is the general case of the rule in this skill's first section, that a snapshot only ever captures what it was watching at the time.

Adding a column to `check_cols` is the sharper case, because it changes what counts as a change: the next run compares a stored null against a real source value and registers a new version for effectively every row. See `dbt-adding-columns` for the pre-change sequence.

## Where snapshots sit in the DAG

```
source  ──►  snapshot  ──►  staging / intermediate  ──►  mart
```

The snapshot sits as close to raw as possible and transformation happens downstream of it. This inverts the usual instinct deliberately: transformation you can redo, capture you cannot.

Downstream models `ref()` the snapshot like any other model. Two access patterns cover nearly everything — current state (`where dbt_valid_to is null`) and the point-in-time join:

```sql
select
    fact.event_at,
    fact.entity_id,
    dim.entity_name,
    fact.amount
from {{ ref('<fact_model>') }} as fact
left join {{ ref('<snapshot_name>') }} as dim
    on fact.entity_id = dim.entity_id
    and fact.event_at >= dim.dbt_valid_from
    and (fact.event_at < dim.dbt_valid_to or dim.dbt_valid_to is null)
```

The `is null` branch is not optional. Omit it and every fact joined to a currently-valid dimension row drops out — a `left join` quietly returning nulls, or an `inner join` quietly losing rows. This is the most common snapshot-consumption bug.

If the snapshot sets `dbt_valid_to_current` to a far-future sentinel, the predicate simplifies to a plain range with no `or` branch — which is the main practical reason to adopt it on a new snapshot. Do not assume it: read the config, because a wrong assumption in either direction silently changes the row count rather than erroring.

Run snapshots as their own step, **after** the source loads and **before** the models that read them:

```bash
dbt snapshot --select <snapshot_name>
```

A snapshot that runs before its source refreshes captures the previous state and stamps it with today's timestamp. That is not a missing row; it is a wrong row, and it looks fine.

### Cadence, and `dbt build`

Cadence is a modelling decision, not a scheduling detail: **the snapshot's run frequency is the resolution of the history, permanently.** Daily runs cannot answer an hourly question later, and no backfill fixes it because the intermediate states were never observable. Choose the interval against the fastest question anyone will plausibly ask, not against the interval the source changes at.

Two operational points that follow:

- `dbt build` includes snapshots in DAG order. That is usually correct in production and often wrong elsewhere — a build in a development or CI environment executes the snapshot too, writing history into whatever target that environment points at. Where snapshots are expensive or their target is shared, exclude them (`--exclude resource_type:snapshot`) outside production, and be certain a CI target cannot write to a production snapshot. See `dbt-environments`.
- A snapshot that fails is more urgent than a model that fails. A failed model rebuilds successfully next run with no lasting harm; a snapshot that fails to run has an unrecoverable gap for that interval. Alert on snapshot failures separately from model failures, and prefer retrying a failed snapshot promptly over waiting for the next scheduled run.

There is one thing worse than a snapshot running too rarely: two schedules snapshotting the same source into the same table. Concurrent snapshot runs against one relation can interleave their window-closing writes, and the resulting overlaps are corrupted history rather than a failed run. One snapshot, one schedule.

## Testing a snapshot

Column tests apply as they do to models, but a snapshot's failure modes are structural, so test the structure. `not_null` on the business key and on `dbt_valid_from` is the baseline.

`unique` on the business key is wrong here — multiple versions per key is the entire point. What must be true is that validity windows do not overlap:

```sql
-- fails if two versions of the same record are valid at the same time
select
    older.<unique_key>,
    count(*) as overlapping_versions
from {{ ref('<snapshot_name>') }} as older
inner join {{ ref('<snapshot_name>') }} as newer
    on older.<unique_key> = newer.<unique_key>
    and older.dbt_scd_id != newer.dbt_scd_id
    and older.dbt_valid_from < coalesce(newer.dbt_valid_to, '9999-12-31')
    and newer.dbt_valid_from < coalesce(older.dbt_valid_to, '9999-12-31')
group by 1
```

Worth adding alongside it: exactly one open version per key — group by the key `where dbt_valid_to is null` and fail on a count above one.

Also assert the snapshot is still running. A snapshot that stops executing produces no error — it just stops accruing history, and the gap is discovered when someone queries a period that was never captured. A recency assertion on `dbt_updated_at`, thresholded to the snapshot's cadence, is the cheapest protection available.

Two further assertions earn their place, both catching failures that are otherwise silent:

- **No gaps in a record's timeline.** For each key, the next version's `dbt_valid_from` should equal the current version's `dbt_valid_to`. A discrepancy means a window was closed without a successor — the signature of a deletion recorded with `invalidate`, or of a repair applied by hand. Expected where deletions occur; investigate where they should not.
- **A bound on how many rows a single run may close.** Compare closures stamped with the latest run against the table's key count. A run that closes an implausible fraction is the mass-deletion failure in progress, and catching it as a test failure is the difference between a fix and an archaeology project.

Unit tests do not apply to snapshots — they are supported for models, and a snapshot's behaviour depends on the destination table's existing state, which is exactly what a unit test replaces. Test snapshots with data tests on the output, as above. See `dbt-unit-tests` for what unit tests do cover.

For a genuinely high-stakes snapshot, the strongest available check is a rehearsal rather than a test: run it against a copy of the source in a non-production target, mutate the copy deliberately (change a tracked column, change an untracked one, delete a row, re-insert it), run again, and confirm the version history matches what you predicted. This is the only way to verify strategy and `check_cols` behaviour before the choices become irreversible history.

## Debugging

| Symptom | Likely cause | Check |
|---|---|---|
| Source changed, no new version | `timestamp` strategy, source did not bump `updated_at` | Compare the business column against `updated_at` movement for a known-changed row |
| Source changed, no new version | changed column not in `check_cols` | Read the config; the column is untracked |
| Versions multiplying every run | `check_cols: all` with a volatile pipeline column | Diff two consecutive versions and see which column moved |
| Overlapping validity windows | `unique_key` is not unique | The duplicate query above |
| Everything closed at once | hard-delete invalidation plus an incomplete extraction, or a filter | Source row count for that run |
| Rows lost in a point-in-time join | missing `or dbt_valid_to is null`, or a `dbt_valid_to_current` sentinel the query does not account for | Read the join predicate and the snapshot config together |
| `dbt_valid_from` later than the real change | `check` strategy stamps the run clock, not source time | Check the strategy; consider `check` with `updated_at` |
| Old rows null in a column added to the query | schema reconciliation is not retroactive | Expected. History for that column starts now |
| Snapshot ran but history has a gap for a period | the run failed or was skipped for that interval | Run history for the snapshot; the gap is not recoverable |

To see what dbt thought changed, select all versions of one key ordered by `dbt_valid_from`, querying with an explicit database and schema rather than `ref()` — see `dbt-environments`.

Two things worth knowing before debugging in the dark:

- `dbt snapshot --empty` is supported from 1.9 and validates that the snapshot's SQL compiles and runs without processing rows. It proves the query is valid; it proves nothing about change detection.
- Inspecting a snapshot's compiled SQL shows the merge or insert/update statement dbt generated, including which columns it compares. That is the fastest way to settle a "why did this not register a change" argument, because the comparison is right there in the SQL rather than inferred from config.

## Recovery, and why to avoid needing it

`--full-refresh` on a snapshot **drops the table and rebuilds it from current source state.** All history is destroyed. It is not a repair; it is a reset to one version per record.

```bash
# destructive: history is gone and cannot be recovered
dbt snapshot --full-refresh --select <snapshot_name>
```

Before ever running that, copy the table to a backup relation.

Changing `unique_key` or the strategy on a running snapshot requires a full refresh to be coherent — which is the same as saying **those fields cannot be changed without losing history.** When history matters more than tidiness, stand up a second snapshot with the corrected config alongside the first, let it accumulate, and union the two downstream with a documented cutover date. Ugly, and it preserves the asset.

Never run a full refresh against production unless the user named production explicitly.

### What is and is not recoverable

| Situation | Recoverable? |
|---|---|
| Full refresh run by mistake, backup taken first | Yes — restore the backup, or use warehouse time travel if still inside the retention window |
| Full refresh, no backup, outside the time-travel window | No. The prior history is gone |
| Spurious versions from a volatile column in `check_cols` | Partly. They can be deleted with a careful predicate, but distinguishing them from real changes is manual and error-prone |
| Mass false closures from an incomplete extraction | Partly. Reopening windows is hand-written DML, and it must be done before further runs build on the wrong state |
| A period where the snapshot never ran | No. The states were never observed |
| A column added later | No, for the past. Historical rows stay null |

The pattern in that table is the argument for the discipline: two of the six are recoverable, and both of those only because someone took a copy first. Take the backup, and prefer standing up a parallel snapshot over mutating one that holds history you cannot reconstruct.

## Completion checklist

- [ ] A snapshot is the right tool — alternatives compared, and the source genuinely overwrites history
- [ ] Config form matches the project's dbt version, and version-gated configs verified as available
- [ ] Points at a source, or at a deliberately frozen thin model with that stated in its description
- [ ] `unique_key` verified unique with a query, before the first run
- [ ] Strategy chosen with the reason stated; `timestamp` only where the guarantee was verified
- [ ] `check_cols` excludes pipeline-mechanical columns; inclusions justified
- [ ] Two consecutive runs with no source change insert zero rows
- [ ] Deletion handling decided deliberately, and not combined with a filtered query
- [ ] Cadence chosen against the finest question anyone will ask, not the source's change rate
- [ ] One snapshot, one schedule — no concurrent runs against the same table
- [ ] Ordered after source load and before consuming models; excluded from non-production builds where appropriate
- [ ] Overlap test and single-open-version test written
- [ ] Recency assertion on `dbt_updated_at` in place
- [ ] Point-in-time joins downstream include the open-version branch, matching the snapshot's `dbt_valid_to_current` setting
- [ ] Backup taken before any full refresh, config migration, or repair DML
- [ ] Naming follows the contract, or the observed convention is stated

## The failure modes that cost the most

1. **Wrong strategy, discovered late.** `timestamp` on a source that does not reliably bump it. Nothing errors; the history is simply incomplete, and the gap is invisible until someone audits a specific change and finds it absent.
2. **Snapshotting a model that later gets refactored.** History spans two definitions of the same column with no marker. Every trend crossing the change date is wrong and looks plausible.
3. **Deletion tracking plus a filter or a flaky extraction.** Mass false deletions that must be repaired by hand, and each further run builds on the wrong state.
4. **A full refresh run to "fix" a problem.** The problem is gone because the evidence is gone. Irreversible, and the reason to back the table up first.
5. **Point-in-time join missing the open-version branch.** Silently drops current records. Row counts look low, nobody knows why, no test fails.
6. **`check_cols: all` over a mechanical column.** The table grows by its own row count every run, and the spurious versions cannot be separated from the real ones afterwards.
7. **A config migration applied to a snapshot holding history.** Old rows follow one convention, new rows another, nothing marks the boundary, and every downstream query silently handles one case.
8. **A snapshot that quietly stopped running.** No failure, no alert, and an unrecoverable hole in the history discovered months later by someone asking about that period.
9. **Cadence chosen by convenience.** The resolution of the history is fixed the day the schedule is set, and no later change can add detail the runs never observed.
