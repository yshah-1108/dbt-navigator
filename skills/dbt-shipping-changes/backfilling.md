# Backfilling an incremental model

Referenced from [SKILL.md](SKILL.md). Read that first — this document covers only the mechanics of moving history, once the decision to move it has been made.

A backfill rewrites rows that already exist. Every hazard follows from that: it can duplicate rows, it can leave gaps between batches, and on a `full_refresh=false` model it can destroy data that no longer exists upstream.

The single most important property: **a backfill must be repeatable.** If running the same backfill twice produces a different table than running it once, the procedure is broken — and that will be discovered during an incident.

- `merge` on a correct `unique_key`, and delete-then-insert, are idempotent over a range. Re-running is safe.
- `append` is **not**. Re-running appends the range again. An `append` model has no safe backfill procedure; that is a consequence of the strategy, not something the backfill can fix.

## Before the first batch

Answer all five. A backfill that starts before these are settled is a backfill that gets interrupted halfway, leaving a table that is partly old logic and partly new — the one state worse than not having started.

1. **Is the history reproducible?** Check the model config for `full_refresh=false`. If present, stop and read the corresponding section of [SKILL.md](SKILL.md). If absent, confirm the *source* still holds the range you intend to rebuild — a source with a rolling retention window will happily produce zero rows for a period, and a merge strategy will not notice.

   ```sql
   select min(<date_column>), max(<date_column>), count(*)
   from <source_relation>
   where <date_column> >= '<range_start>' and <date_column> < '<range_end>'
   ```

2. **What is the exact range?** Narrower than "all of it". If only a known period is wrong, backfill only that period; a full-history rebuild costs more and risks more.

3. **What is the strategy?**

   | Strategy | Backfill behavior on a rerun of the same range |
   |---|---|
   | `merge` with a correct `unique_key` | Existing rows updated in place. Safe and idempotent. |
   | `merge` with an incomplete `unique_key` | **Duplicates.** The key does not identify a row, so the merge inserts instead of updating. |
   | delete-then-insert | Range deleted, then reinserted. Correct where rows can disappear upstream. |
   | `append` | **Duplicates, every time.** Append has no notion of an existing row. Never backfill an append model without deleting the range first. |

   Establish which one applies before running anything. A backfill on an append-strategy model is the fastest way to double a table.

4. **Is the range bounded on both sides?** A start bound alone will process from the start date to now, which for an old start date is a full-history rebuild wearing the costume of a targeted fix.

5. **Capture the pre-backfill state.** Row count and a key metric per period, so the effect is measurable rather than assumed.

   ```sql
   select date_trunc('month', <date_column>) as period,
          count(*) as rows,
          sum(<measure>) as total
   from <database>.<schema>.<table>
   where <date_column> >= '<range_start>' and <date_column> < '<range_end>'
   group by 1 order by 1
   ```

## Bounded batches

Rebuilding years of history in one statement fails in the least useful way possible: it runs for hours, times out, and leaves an indeterminate amount of work done.

Batch it. The parameters come from the model — most incremental models take their boundary from a variable or an environment-specific default, so a backfill passes explicit bounds for one window at a time:

```bash
dbt run --select <model> \
  --vars '{"<start_var>": "<range_start>", "<end_var>": "<range_end>"}'
```

Conventions that make batching safe:

- **Start inclusive, end exclusive** (`>= start and < end`). Contiguous batches then tile the range with no gap and no overlap. Inclusive-on-both-ends batches double-count every boundary row on an append strategy and merely waste work on a merge.
- **One month per batch** is a reasonable default. Drop to a week when a month does not complete comfortably. There is no benefit to a batch that barely fits.
- **Run batches sequentially, not in parallel.** Concurrent writes to the same table produce lock contention at best and interleaved partial results at worst.
- **Verify after each batch, not only at the end.** A batch that produced zero rows or double rows should stop the sequence immediately.
- **Record which batches completed.** A backfill will be interrupted; the resume point must be knowable without guessing.
- **Confirm `incremental_predicates` reach the range.** This is the one that produces a green run and a doubled table. A predicate pinned to a recent lookback window cannot reach a range from two years ago, so the delete-or-match phase finds nothing and `merge` inserts instead of updating. The model must already handle the range you intend to backfill — if it does not, fix the model first, as its own commit, before backfilling anything.

```
range_start          batch boundary        batch boundary          range_end
     |------- batch 1 -------|--- batch 2 ---|--- batch 3 ---|
   >= start,  < b1        >= b1, < b2     >= b2, < b3     >= b3, < end
```

### Why not one big statement

The instinct to do it in one pass is worth arguing against explicitly, because "fewer statements" sounds safer and is not:

| One statement over the whole range | Bounded batches |
|---|---|
| Fails after hours with an indeterminate amount done | Fails after one batch, with a known resume point |
| No progress signal until it finishes or does not | Progress is countable |
| Holds locks or a large write for its whole duration | Each batch releases between windows, so scheduled jobs can interleave |
| Peak resource usage sized for the whole range | Sized for one window, so a smaller compute suffices |
| Cannot be verified until complete | Verified incrementally, so an error is caught on batch one rather than batch forty |
| Aborting means losing everything done | Aborting means stopping after the current batch |

The last row is the decisive one. A batched backfill has a safe stopping point every window; a single statement has exactly two states, and one of them is "unknown".

Note what dbt does and does not give you here. dbt does not wrap a run in a transaction spanning models, and whether an individual model's write is atomic is adapter-dependent — some platforms replace a table atomically, others do not, and an incremental merge is a single statement whose atomicity is the platform's business. **Do not design a backfill on the assumption that a failure mid-statement leaves nothing written.** Verify what the platform guarantees, and if you cannot, treat every batch as potentially partially applied and make the procedure idempotent so re-running the batch is harmless. Idempotency is the property that makes the uncertainty survivable.

## Monitoring progress

A backfill that nobody is watching is a backfill discovered to have stopped four hours ago. Three things are worth having in front of you, and none of them requires tooling:

1. **A batch ledger.** A list of the windows, marked as they complete, kept outside the terminal — a scratch file or the ticket. Terminal scrollback is not a record, and the resume point after an interruption is the one fact that must not be guessed. Write down which batches completed, not which you believe completed.
2. **Elapsed time per batch, compared to the first.** The first verified batch is the yardstick for the remainder. A batch taking materially longer than its predecessors is a signal — a denser period, a warehouse under contention, or a predicate that stopped pruning. Any of those changes the cost estimate for what remains.
3. **Row count per batch, against expectation.** A batch that wrote roughly zero rows or roughly double is the failure worth catching immediately. This is why verification belongs after each batch rather than at the end: the difference between fixing one batch and unpicking forty.

Set a rough expectation before starting — total cost, total duration, rows per period — and compare against it as you go rather than only at the end. A backfill running at three times the estimated cost is a decision to make at batch three, not a number to discover afterwards.

## Aborting safely

Assume you will have to stop one: a cost overrun, a wrong result on batch two, a production incident that needs the warehouse, or someone with authority saying stop. The whole point of bounded batches is that this is survivable — but only if the abort is done deliberately.

**Stop between batches, not inside one.** Let the current batch finish if it is close, then stop. Killing a statement mid-flight leaves the table in a state whose contents depend on platform atomicity you may not have verified, which converts a clean pause into an investigation.

When you must kill a running batch:

1. Cancel the query at the warehouse as well as the client. Killing the dbt process does not necessarily cancel the statement it submitted, and an orphaned write that continues after you think you stopped is the worst outcome available.
2. Treat that batch's window as unknown, not as failed. Verify it directly — row count and duplicate check for that window alone — before deciding whether to re-run it.
3. Re-run the whole batch rather than trying to resume part of it, which is only safe because the procedure is idempotent. This is the payoff for insisting on that property up front.

Then, whether the stop was clean or not:

| Question | Why it matters |
|---|---|
| Which windows are done, which are not, and which is unknown? | The ledger answers this. Without one, the answer is a query per window |
| Is the table now internally inconsistent? | Part of the range has new logic and part has old. That is a real state consumers can read |
| Do downstream models hold a mix? | If any downstream ran during the backfill, it holds partly-new values. Note it |
| Will the next scheduled run make it worse? | If the schedule writes to this table, a partial backfill plus a normal run can compound. Consider pausing the job |

**Say the state out loud, in writing, before walking away.** "Backfill of the range paused after batch 12 of 40; periods through that boundary are on the new logic, later periods are on the old; downstream not yet rebuilt." A partially-backfilled table that nobody has documented is indistinguishable from a data quality incident, and someone will investigate it as one.

Two decisions to make at the point of aborting rather than later:

- **Finish forward or roll back?** Finishing is usually cheaper and usually right, since the remaining batches are already understood. Rolling back means restoring the range to the old logic, which requires the old code and the same batching effort in reverse — worth it only when the new logic is wrong rather than merely slow or expensive.
- **Does the mixed state need announcing now?** If consumers read the affected periods, yes, immediately, with the boundary named. Delaying that is how a paused backfill becomes someone else's incident.

## Coordinating with consumers

A backfill rewrites rows that people are reading. Three coordination points, in time order:

**Before.** Tell anyone who reads the affected periods that the values will change, roughly when, and roughly by how much. A number moving without warning is indistinguishable from a bug, and the ensuing investigation costs more than the notice would have. If a report is used for anything committed externally, its owner needs to know before the numbers move, not after.

**During.** The table is mid-rewrite, so anything reading it gets a mix of old and new. Where that is unacceptable, the options are to pause the consumer, backfill into a copy and swap it in, or accept a defined window of inconsistency and publish the window. Pick one deliberately; drifting into the third by accident is the common path.

**After.** Say which periods changed, by how much, and what to do about outputs already produced. Scheduled reports and exports that ran during the window contain values from a table being rewritten — name them and the period, so their output can be discarded rather than reconciled.

One case deserves separate handling: **a number someone has already reported externally.** Correcting it silently is worse than not correcting it, because two versions of a published figure now exist with nothing distinguishing them. That is a conversation before the backfill, not a note after.

## If the model does not accept bounds

Some incremental models compute their boundary internally with no override. Three options, in order of preference:

1. **Add the override.** A model that cannot be backfilled in bounded batches is a model that can only be fixed by full refresh, which for a large table is a real operational limitation. Adding a bounded override is a small change with lasting value — ship it as its own commit.
2. **Full refresh**, if the table is small enough for one statement to complete reliably.
3. **Delete the range and let the incremental boundary re-fill it**, where the model's boundary is derived from the maximum date already present. This works only when the boundary is computed that way; verify by reading the compiled SQL, not the model file:

   ```bash
   dbt compile --select <model>
   # read target/run/... and confirm how the boundary is actually derived
   ```

## Full refresh instead of a backfill

Sometimes a full refresh is simpler and safer than a batched backfill — but **only when history is reproducible from the source.**

```bash
dbt build --full-refresh --select <model>
```

Do **not** do this when:

- The model sets `full_refresh=false`. That flag exists because someone determined history is irreplaceable. Removing it to unblock yourself is how history is lost permanently.
- The source retains less history than the model holds. The rebuild silently truncates everything older than the source's retention window.
- The rebuild duration would itself be the outage — the table is unavailable or partial while it runs.

If any of those apply, a batched backfill is the only correct route.

## After the backfill

In this order. The order is the point — stopping after the first item is the standard mistake.

1. **Duplicate check on the primary key**, restricted to the backfilled range:

   ```sql
   select <key_columns>, count(*) as occurrences
   from <database>.<schema>.<table>
   where <date_column> >= '<range_start>' and <date_column> < '<range_end>'
   group by <key_columns>
   having count(*) > 1
   limit 20
   ```

   Any rows here means the strategy or the `unique_key` was wrong, and the table is now worse than before. Fix it before proceeding — an extra day of a wrong table is cheaper than a downstream chain built on duplicates.

2. **Gap check.** Every expected period present, with a plausible row count:

   ```sql
   select date_trunc('day', <date_column>) as day, count(*) as rows
   from <database>.<schema>.<table>
   where <date_column> >= '<range_start>' and <date_column> < '<range_end>'
   group by 1 order by 1
   ```

   Read the whole series, not the total. A missing day is invisible in a sum and obvious in a per-day listing — and a missing day is exactly what an off-by-one batch boundary produces.

3. **Compare against the pre-backfill capture.** Differences are expected — that was the point — but each one should be explainable by the change you made. An unexplained difference means something other than your change moved.

   For column-level evidence rather than aggregate comparison, `audit_helper.compare_relations` against a saved copy of the table is stronger proof — see `dbt-refactoring-safely` for the mechanics and `dbt-verification` for what counts as proof.

4. **Boundary continuity.** Confirm the periods immediately before and after the backfilled range have not changed and that no gap opened at either edge. Batch boundaries are where off-by-one errors live.

5. **Rebuild downstream.**

   ```bash
   dbt build --select <model>+
   ```

   Non-optional. A backfilled model whose consumers were not rebuilt leaves the consumers holding pre-backfill values, and the two now contradict each other. This is a worse state than the one you started from, because there are now two answers instead of one wrong one.

6. **Rerun the incremental build normally, twice.** Row counts must not move on the second run. If they do, the boundary and the backfilled range overlap in a way that duplicates rows on every subsequent run — a permanent daily defect introduced by a one-time fix.

7. **Notify consumers.** Anyone who pulled numbers from this model during the backfill window read a table mid-rewrite. If reports or exports run on a schedule, say which ones and for which period their output should be discarded.

## Cost and contention

- A backfill reads and writes the same volume as the original build of that period. Estimate before starting; the cost of the whole range is roughly the cost of one batch multiplied by the number of batches.
- Run outside the window in which scheduled jobs write to the same table. A backfill batch and a scheduled incremental run overlapping on one table is a race, and the loser's rows are the ones nobody notices are missing.
- A larger compute size shortens a large batch but does not make an incorrect batch correct. Size up only after one batch has been verified.

## Checklist

- [ ] `full_refresh=false` checked before anything else
- [ ] Source confirmed to still hold the range
- [ ] Range bounded on both sides, start inclusive and end exclusive
- [ ] Incremental strategy and `unique_key` confirmed adequate for a rerun
- [ ] Append-strategy models: range deleted before insert
- [ ] Pre-backfill row counts and metrics captured per period
- [ ] Batched, sequential, one month or narrower
- [ ] `incremental_predicates` confirmed to reach the backfilled range
- [ ] `full_refresh=false` respected, not removed
- [ ] Cost and duration estimated from the first verified batch, and compared against as you go
- [ ] Batch ledger kept outside the terminal, recording completed windows
- [ ] Abort procedure known before starting, including how to cancel at the warehouse
- [ ] Consumers of the affected periods told before values move
- [ ] Completed batches recorded so the sequence can resume
- [ ] Duplicate check on the primary key over the backfilled range
- [ ] Per-period gap check read as a series, not as a total
- [ ] Boundary periods on both edges confirmed unchanged
- [ ] Downstream rebuilt
- [ ] Normal incremental run repeated twice with stable row counts
- [ ] Consumers notified for the window the table was mid-rewrite
- [ ] If paused, the mixed state written down with the boundary named
