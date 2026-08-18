# Joins

Detailed technique for Step 3 of [SKILL.md](SKILL.md). Joins are where a query stops being proportional to its inputs. A scan that reads too much costs what it costs; a join that emits a multiple of its inputs turns a large query into an unfinishable one. Diagnose the row multiple before anything else here: for each join, compare output rows against the larger input. Anything above 1 needs an explanation.

## Join explosion

Output rows greater than input rows means one of four things, in rough order of frequency:

1. **The key is not unique on the side you assumed it was.** Ten duplicates on one side multiply matching rows by ten. This is a correctness bug that presents as a performance problem — the totals downstream are also wrong. Check uniqueness before optimizing anything: if the key is duplicated, deduplicate that side first and the join gets faster as a side effect.
2. **A missing predicate,** producing a full or partial cross product. Some engines label this in the plan; others simply report a large output.
3. **A many-to-many relationship that genuinely exists.** Then the fix is to aggregate one side to the grain the other side needs *before* joining, so the join is many-to-one.
4. **An inequality or function in the join condition.** Hash joins require equality. A condition the engine cannot hash — a range comparison, `like`, a function over the key, or `or` between conditions — must be satisfied by generating candidate pairs and filtering them. That is why a `between` join over a large table can be orders of magnitude slower than an equality join on the same data.

The `or` case is worth naming on its own. `on a.k1 = b.k1 or a.k2 = b.k2` cannot be evaluated as a single hash join. Two `union all`-ed joins, each with one equality, is frequently dramatically faster and returns the same rows once duplicates from rows matching both branches are handled.

## Range and interval joins

Joining a point to an interval — an event timestamp falling inside a validity window, the standard slowly-changing-dimension lookup — has no hash key, so the naive plan is a product filtered afterward.

Three fixes, in order of preference:

1. **Add an equality component.** Nearly every range join has a natural partition: an entity id, a day. `on a.entity_id = b.entity_id and a.ts >= b.valid_from and a.ts < b.valid_to` gives the engine something to hash and reduces the range comparison to within-group work. This is warehouse-neutral and usually sufficient.
2. **Bucket both sides on a coarse grain** and join on the bucket plus the range: derive a date or hour key on each side, join on it, then apply the range predicate. This trades exactness of the bucket boundary for a hashable key, so intervals spanning a boundary need a row per bucket they overlap. Test the row count before and after — done carelessly this reintroduces the explosion it was meant to prevent.
3. **Use engine support where it exists.** Databricks has an explicit range-join optimization that bins the value domain, automatic in Databricks SQL and hint-driven elsewhere; the bin size has to approximate the typical interval length or it makes things worse. Other engines have no equivalent. See [warehouse-layout.md](warehouse-layout.md).

Also: an interval join where `valid_to` is null for current rows silently drops rows unless the null is handled. Correctness first.

## Semi-joins and anti-joins

"Rows that have a match" and "rows that have no match" should not be written as full joins. Three forms exist and they are not interchangeable.

| Form | Semantics | Notes |
|---|---|---|
| `exists (select 1 from ...)` | Two-valued: true or false. Stops at the first match. | The safe default for both semi- and anti-joins. Optimizers on every major engine recognize it and plan a semi/anti-join. |
| `in (subquery)` / `not in (subquery)` | Three-valued. **A single null in the subquery makes `not in` return no rows at all.** | `in` is usually fine. `not in` is a trap: the failure is total and silent, and it looks like correct SQL. Only use it where the column is provably not nullable, and prefer `not exists` regardless. |
| `left join ... where b.key is null` | Two-valued, equivalent to `not exists`. | Correct, and readable to many people. But it materializes matches before discarding them, and it duplicates left rows when the right side has duplicate keys — an anti-join that accidentally becomes an explosion. |

Measured performance among the correct forms differs by engine and by version, and the published comparisons disagree with each other. Do not port a ranking from another engine's blog post. Write `not exists` for anti-joins because its semantics are unambiguous, and check the plan if it is the dominant operator.

## Join order, broadcast, and shuffle

Two large tables joined across a distributed engine require the matching keys to meet on the same worker. There are two ways to arrange that, and which one the engine picks dominates the cost:

- **Broadcast:** one side is small enough to copy to every worker, and the large side is not moved at all. Much cheaper when it applies.
- **Shuffle / hash-partitioned:** both sides are redistributed by the join key. Correct at any size, and the expensive option, because it moves data across the network.

What you control is mostly the *inputs*, not the choice: filter and aggregate before the join so a side becomes small enough to broadcast. Explicit control varies sharply and the guidance is contradictory across engines, which is exactly why it belongs per-engine rather than as a rule:

- BigQuery documents a *largest table first* ordering convention and detects broadcast eligibility at runtime.
- Snowflake's plan generally shows the build (hash-table) side on the left and benefits from the *smaller* input being there, and users have little direct influence over it.
- Spark-based engines re-plan at runtime from observed statistics and can convert a shuffle into a broadcast mid-query.

Writing tables in one engine's recommended order because another engine's documentation said so is a real and common mistake. Check [warehouse-layout.md](warehouse-layout.md) for your engine, and treat the ordering convention as a hint to the optimizer, never as a guarantee.

## Skew

If one worker takes far longer than the average on a join or aggregation, the problem is distribution, not volume, and a bigger warehouse buys nothing — the slow worker is still one worker.

- **The usual culprit is a placeholder value**: a null, an empty string, `-1`, or an "unknown" sentinel holding a large share of rows. All of them hash to one destination.
- **Exclude non-matching keys before the join.** Nulls never match an equality condition, so filtering them out of the join input changes nothing but the work.
- **Split the query** where a few known values dominate: join the skewed values separately from the rest and `union all` the results.
- **Salting** — adding a bucket derived from a hash of the key to both sides of the join condition — spreads a hot key across workers. It works, and it makes the query considerably harder to read, so use it only when the plan proves the skew and the simpler options are exhausted.
- Some engines detect and split skewed partitions at runtime; where that exists it is usually the best answer, and it is engine-specific.
