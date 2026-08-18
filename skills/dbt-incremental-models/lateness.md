# Sizing the lookback, deterministic dedup, and reconciliation

Read [SKILL.md](SKILL.md) first for the idempotency property and the three defenses against late-arriving data that must agree. This document is the how of each: sizing the lookback window from the source's real lateness distribution, making deduplication deterministic so a reprocess does not flip values, and why a high-water mark is not a substitute for a reconciliation path.

## Sizing the lookback from evidence

A lookback is a number someone chose. Choose it from the source's actual lateness distribution, not from what feels safe:

```sql
select
    datediff('day', <event_timestamp>, <load_timestamp>) as days_late,
    count(*) as rows
from <database>.<schema>.<upstream_relation>
where <load_timestamp> >= dateadd('day', -90, current_date)
group by 1
order by 1
```

Read the tail, not the average. If 99.9% arrive within two days and the rest trail to thirty, a 3-day lookback is a decision to permanently miss a known fraction — which may be correct, and should be stated in the model's description rather than discovered. Date-function syntax varies by warehouse.

Then re-measure occasionally. A lookback sized correctly two years ago against a source that has since changed its schedule is a number nobody has questioned.

## Deduplication must be deterministic

If the model deduplicates — and on a `merge` model it usually must — the tiebreak has to be total. `row_number()` partitioned by the key and ordered by an update timestamp picks an arbitrary winner whenever two rows share that timestamp, and "arbitrary" means it can differ between runs.

The consequence is a table that changes on reprocessing without any source change: the same key, a different version each time. Row counts match, tests pass, and a value silently flips. Add a second ordering column that is unique — a sequence, an ingestion id, a file offset — so the order is total. If nothing unique exists, the tiebreak cannot be made deterministic and that limitation belongs in the model's description.

## A high-water mark is not a substitute for reconciliation

Every watermark strategy leaks. Late data beyond the lookback, clock skew, a source that restates an old period, a run that was skipped for a week — each leaves a gap that the next run's boundary steps straight over, because the boundary only ever moves forward.

So a long-lived incremental model needs a reconciliation path in addition to its boundary:

- **A periodic full refresh** where the table is small enough and history is reproducible. This is dbt's own recommendation and the simplest answer: it resets the accumulated drift completely.
- **A periodic bounded backfill** of a trailing window wider than the lookback, where a full refresh is too expensive.
- **A comparison against the source** — count and one measure per period, source versus target, over a trailing window — which is the only thing that *detects* drift rather than hoping the window covered it.

Pick one and say which. "It has a 3-day lookback" is a mitigation, not a reconciliation, and the difference matters on the day someone asks whether last quarter is complete.
