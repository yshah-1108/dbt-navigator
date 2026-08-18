# Testing an incremental model

An incremental model has two code paths, and only one of them runs in CI unless someone made it run. Most incremental bugs live in the difference between them.

Read [SKILL.md](SKILL.md) for the design decisions. This document is how you prove the thing works, and what each kind of proof does and does not establish.

## The claim you are trying to support

**An incremental build should produce the same table as a full rebuild of the same data.** Everything below is in service of that one sentence. Where it cannot be true — a source with a retention window shorter than the table's history, a table deliberately holding a pre-correction record — say so explicitly, because then no test can establish equivalence and the reviewer needs to know that.

## The minimum sequence

Nothing here is optional, and the order matters.

```bash
dbt compile --select <model>                # the full-refresh path renders
dbt build --full-refresh --select <model>   # builds from scratch, runs tests
dbt build --select <model>                  # now exercises the incremental path
dbt build --select <model>                  # and again: the second run must be a no-op
```

The **third** build is the one people skip and the one that catches the most. A single incremental run after a full refresh tells you the incremental branch compiles and inserts something. Running it twice with no new source data tells you whether the model is idempotent — and if row counts move on that run, the model duplicates on every retry, forever.

Then, against an explicit database and schema rather than `ref()`:

```sql
-- 1. duplicate keys: must return zero rows
select <key_columns>, count(*) as occurrences
from <database>.<schema>.<model>
group by <key_columns>
having count(*) > 1
limit 20;

-- 2. per-period series across the boundary: read it as a series, not a total
select <date_column>, count(*) as rows, sum(<measure>) as total
from <database>.<schema>.<model>
group by 1
order by 1 desc
limit 30;
```

Read the second one across the boundary period specifically. The overlap period's totals should be unchanged by the second run, or changed only by genuinely new source data. A gap, or a period whose total doubled, localises the bug immediately.

Also read the compiled incremental SQL. The boundary is Jinja; the model file does not tell you what ran. `target/run/` holds the generated merge or delete — see `dbt-verification` for the paths and for what each rung of evidence proves.

## Proving increment equals full rebuild

The strongest available evidence, and worth the cost on any model whose numbers matter.

```
1. Full refresh into a copy, or note the current table as the baseline
2. Clone or save that relation under a second name
3. Run the model incrementally over the same period
4. Compare row-by-row
```

```sql
{{ audit_helper.compare_relations(
    a_relation = api.Relation.create(
        database='<db>', schema='<schema>', identifier='<model>__full_rebuild'
    ),
    b_relation = api.Relation.create(
        database='<db>', schema='<schema>', identifier='<model>'
    ),
    primary_key = '<key_columns_or_concatenation>',
    exclude_columns = ['<load_timestamp_column>'],
    summarize = true
) }}
```

Acceptance criterion: zero rows only in A, zero only in B, zero differing.

Practical notes that decide whether this works at all:

- **Exclude run-stamped columns.** A `current_timestamp()` audit column differs by construction between the two builds and will make every row differ. Excluding it is legitimate; excluding a business column to make the comparison pass is not.
- **Restrict both sides to the same window** if the incremental table has history the rebuild does not, or the difference reads as rows-only-in-A and means nothing.
- **Escalate progressively.** Compare row counts first — it is one cheap query and a mismatch ends the investigation. Row-level comparison is a full scan on both sides plus a join; on a large table that is a real cost, and it tells you nothing extra when the counts already disagree.
- **No usable primary key means no row-level comparison.** Fall back to aggregates and say plainly that the evidence is aggregate-level.

The mechanics of before/after comparison live in `dbt-refactoring-safely`; what counts as proof is in `dbt-verification`.

## Unit tests on both branches

Unit tests run against static fixtures and no warehouse data, which makes them the only cheap way to test the incremental branch's *logic* — and they are available from dbt 1.8.

Override `is_incremental` to test each path, and supply the current contents of the model as the `this` input:

```yaml
unit_tests:
  - name: orders_daily__full_refresh_path
    model: orders_daily
    overrides:
      macros:
        is_incremental: false
    given:
      - input: ref('stg_orders')
        rows:
          - {order_id: 1, ordered_at: '2024-01-01', order_total: 10}
    expect:
      rows:
        - {order_id: 1, ordered_at: '2024-01-01', order_total: 10}

  - name: orders_daily__incremental_path_reprocesses_boundary
    model: orders_daily
    overrides:
      macros:
        is_incremental: true
    given:
      - input: ref('stg_orders')
        rows:
          - {order_id: 1, ordered_at: '2024-01-01', order_total: 10}
          - {order_id: 2, ordered_at: '2024-01-02', order_total: 20}
      - input: this
        rows:
          - {order_id: 1, ordered_at: '2024-01-01', order_total: 10}
    expect:
      # what the materialization will merge, not the final table
      rows:
        - {order_id: 1, ordered_at: '2024-01-01', order_total: 10}
        - {order_id: 2, ordered_at: '2024-01-02', order_total: 20}
```

Two things to be precise about, because getting them wrong makes the test meaningless:

- **The expected output is what the materialization will insert or merge, not what the final table looks like afterwards.** A unit test on an incremental model asserts the contents of the incoming set.
- **dbt has no way to unit test the insert/merge step itself.** The DML the materialization generates is out of scope. Unit tests can prove your boundary reprocesses the right window; only a real build can prove the strategy then does the right thing with it.

The test worth writing above all others: **the boundary case.** Put a row in `this` at exactly the boundary timestamp and another in the input at the same timestamp, and assert the input row is present in the expected output. That test fails if anyone changes `>=` to `>`, which is the highest-value single assertion in this document.

Incremental models must already exist in the warehouse before unit tests run against them. `dbt run --select "config.materialized:incremental" --empty` creates them cheaply. Details in `dbt-unit-tests`.

## Data tests that catch incremental-specific rot

Ordinary column tests apply. These are the ones that exist because the model is incremental.

| Test | Catches |
|---|---|
| Uniqueness on the full `unique_key` | Duplicates from a rerun, a null key column, or a grain that outgrew the key |
| Recency on the boundary column | A model that silently stopped loading — green runs, no new rows |
| Row count per period, floor and ceiling | A period that loaded partially, or twice |
| No rows beyond the current period | A future-dated boundary that will freeze the model |
| Not-null on every `unique_key` column | The null-key duplication mechanism, before it happens |

**Uniqueness on the composite key is not optional on an incremental model.** It is the only automated protection against the entire class of duplication bugs, and on a wide fact table it is cheap relative to what it prevents. If the project declares expected tests per column role, follow that; if the declared policy does not cover an incremental key, this is the case for raising it rather than skipping it.

**Recency is the test nobody writes and everybody needs.** An incremental model that stops receiving data does not fail. Its boundary is satisfied by the existing maximum, it loads zero rows, and the run is green — potentially for weeks. A test asserting the newest row is within the model's cadence is the cheapest available protection, and the failure mode it catches is invisible to every other test in this table.

Two cautions on how these are applied:

- **A test that only ever examines recent rows can pass over a corrupt history.** If tests are restricted to a recent window for cost reasons, that is a legitimate trade — but record that historical rows are unverified rather than treating a green suite as whole-table evidence.
- **A uniqueness test added to an already-duplicated table fails on history you did not create.** Decide deliberately whether to clean the history first or to threshold the test; do not disable it.

## Testing that a backfill did what it claims

Backfill procedure belongs to `dbt-shipping-changes` and its backfilling document. What is specific to verifying an incremental backfill:

- **Capture the baseline before, not after.** Row count and one measure per period over the range being changed. Without it there is no way to demonstrate the backfill helped, or to notice it did not.
- **A backfill that changes nothing usually means the predicate did not reach the range.** Green run, right source rows read, no target rows modifiable. Check the generated DML, not the model file.
- **Confirm the periods immediately either side of the range are unchanged.** Batch boundaries are where off-by-one errors live, and a range one period too wide looks like a successful backfill.
- **Then run the normal incremental build twice** and confirm row counts are stable. A backfill that leaves the boundary and the backfilled range overlapping introduces a duplicate on every subsequent scheduled run — a permanent daily defect created by a one-time fix.

## What each kind of evidence actually proves

| Evidence | Proves | Does not prove |
|---|---|---|
| Compiles | Jinja renders; the boundary resolved to something | That the boundary is correct, or that the warehouse accepts the SQL |
| One incremental run succeeded | The branch executes | Idempotency, or that the right rows were reprocessed |
| Two consecutive incremental runs, stable counts | Idempotency over the current window | Anything about periods outside the window |
| Zero duplicate keys | The key holds **today** | That the key matches the grain |
| Row-level comparison against a full rebuild | Equivalence over the compared window | Correctness, if the rebuild is wrong too |
| Unit test on the incremental branch | The boundary logic selects the intended rows | That the strategy writes them correctly |
| Recency test passing | The model is still loading | That what it loaded is right |

## Checklist

- [ ] Full refresh, then incremental, then incremental again — row counts stable on the third
- [ ] Duplicate-key query returns zero rows, over the whole table not just recent data
- [ ] Per-period series read across the boundary, not a single total
- [ ] Compiled incremental SQL and generated DML read, not assumed
- [ ] Unit test on the full-refresh branch and on the incremental branch
- [ ] Unit test asserting a row exactly at the boundary is included
- [ ] Uniqueness test on the complete `unique_key`, and not-null on each of its columns
- [ ] Recency test sized to the model's cadence
- [ ] Row-level comparison against a full rebuild where the numbers matter, with excluded columns justified
- [ ] Anything not verifiable stated as such, with the reason
