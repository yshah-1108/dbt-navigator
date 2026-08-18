# Comparison techniques

How to compare two relations, what each method proves, and where each one lies to you. Read [SKILL.md](SKILL.md) first — it holds the evidence ladder, and a comparison is only meaningful once you know which rung your claim requires.

Everything here answers one question: **is B the same as A?** Nothing here answers whether A was right. That distinction is the most-violated boundary in verification work.

## Choosing a method

| Method | Proves | Cost | Fails silently when |
|---|---|---|---|
| Row count | Cardinality is unchanged | One query | Every value is wrong and the count is not |
| Count + one aggregate | Cardinality and one measure | One query | Any other column is wrong |
| Grain check (`count(*)` vs `count(distinct key)`) | The grain is what was declared | One query | The key was chosen by looking at the output |
| Column-set and type comparison | Schema is unchanged | Metadata only | Values differ within an unchanged schema |
| Whole-relation hash | Byte-level identity of the whole set | Full scan, both sides | Nothing — but it tells you *nothing about where* the difference is |
| Row-level comparison on a key | Which rows and which columns differ | Full outer join, both sides | The key is not unique or not populated |
| Per-column value comparison | Which column drives a mismatch, and how | One pass per column | Rows missing on one side are attributed to the column rather than to absence |
| Statistical comparison | Distributions agree | Cheap | Two different datasets share a mean |
| Deterministic sample | The sampled subset agrees | Proportional to the sample | The defect is outside the sample |

A practical progression: count → count + aggregate → hash if the engine supports one → row-level on a key → per-column on whatever the row-level comparison implicated. Each step is only worth taking if the previous one passed, because a failure at any step ends the investigation early and cheaply.

## `audit_helper`

The dbt-maintained package for relation comparison. It generates SQL; it does not execute anything itself, so its output is inspected by running the generated query — through `dbt show --inline`, as an analysis, or wrapped in a singular test.

### The macros, and what each is for

| Macro | Question it answers |
|---|---|
| `compare_row_counts` | Do the two relations have the same number of rows? The correct first call, because a mismatch here ends the comparison |
| `compare_relation_columns` | Do the two have the same columns, types, and ordinal positions? The right call before any value comparison, and the fastest explanation for a comparison that will not run |
| `quick_are_relations_identical` / `quick_are_queries_identical` | Are they byte-identical? A hash-based check, available on a subset of adapters. Returns one boolean and no location |
| `compare_and_classify_relation_rows` / `compare_and_classify_query_results` | Which rows are added, removed, modified, identical — with counts *and* samples in one pass |
| `compare_which_relation_columns_differ` / `compare_which_query_columns_differ` | Which columns contain any difference at all. Cheap triage before a per-column deep dive |
| `compare_column_values` | For one column: how many rows match, differ, are null on one side, or are missing entirely |
| `compare_all_columns` | The same breakdown for every column at once |
| `compare_relations` / `compare_queries` | Row-by-row comparison. Long-standing and widely used; the classify macros above supersede them for new work |

The `_relations` variants take two relations; the `_queries` variants take two SQL strings. Prefer the query variants whenever the two sides need aligning first — restricting to a common window, casting a type that differs, renaming a column, or rounding a float. Aligning inside the comparison is honest and visible; aligning by editing one of the models is not.

### Reading the output

`compare_column_values` and `compare_all_columns` classify each row into categories that must not be collapsed:

| Category | Means | Not the same as |
|---|---|---|
| perfect match | Same value both sides | — |
| both null | Absent on both sides, consistently | A match on a real value |
| values do not match | Present on both sides, different | Anything about absence |
| null in A only / null in B only | The row exists on both sides; the value is missing on one | The row being missing |
| missing from A / missing from B | The **key** is absent on that side | A value difference |

The distinction that matters: "missing from B" is a cardinality problem and "values do not match" is a logic problem, and they have different fixes. A summary that adds them together to produce a single match percentage has destroyed the only useful information in the output.

A row-level comparison with `summarize=false` returns the differing rows themselves — which is what you actually read. The summary tells you a difference exists; the rows tell you why.

### The limits, stated plainly

These are the conditions under which the tool returns a confident and wrong answer.

1. **A non-unique or nullable key invalidates the whole result.** Every key-joined macro assumes the key is unique and never null on both sides. Given duplicates, the join multiplies rows and the output is arithmetic on a fan-out — usually reported as a large number of mismatches that do not exist. **Establish uniqueness and non-nullness with a query before running any comparison**, not by assumption:

```sql
select count(*) as rows,
       count(<key>) as key_present,
       count(distinct <key>) as distinct_keys
from <database>.<schema>.<model>
```

All three must be equal, on both sides.

2. **No key at all means no row-level comparison.** Not a weaker one — none. Row-based comparison without a key cannot attribute a difference to a row. The honest fallback is aggregate comparison plus a hash, described as aggregate-level evidence.

3. **A composite key must be composed identically on both sides.** Concatenating columns to form a key is supported, and it is also where a separator-free concatenation makes two different rows collide. Use an explicit separator, and prefer a list of key columns where the macro accepts one.

4. **A full comparison is a full scan of both relations.** The join-based macros read everything on both sides. On a large relation this is expensive enough that people skip verification altogether — which is why the cheap rungs exist, and why bounding both sides to a common window is the standard move.

5. **Different coverage reads as missing rows.** If one side has ten days and the other has ninety, the output is dominated by rows-only-in-B that are not a defect. Restrict both sides to the same window before drawing any conclusion. This is the single most common misreading of a comparison result.

6. **Floating-point and type differences read as value mismatches.** Reordered arithmetic differs in the last decimal places; a `decimal` on one side and a `float` on the other differs on comparison. Round or cast inside the query variant, at a precision you can justify, and say in the summary that you did.

7. **Column-set differences stop the comparison before it starts.** Relations must expose the same column names. Metadata columns — load timestamps, batch identifiers, ingestion ids — differ by design and must be excluded explicitly. Excluding a *business* column to make a comparison pass is not a comparison; if you exclude one, say which and why.

8. **A comparison against a baseline proves equivalence, never correctness.** A zero-difference result against a wrong baseline proves you faithfully reproduced a defect. If the baseline was never itself validated, the strongest available claim is "unchanged from previous behaviour", and it should be written that way.

### As a standing test

A comparison can be a singular test, so a model's equivalence to a reference relation is enforced on every run rather than checked once. Two conditions make it worth doing: the model must have a key that reliably passes uniqueness and non-null tests, and the reference relation must be one that legitimately should never diverge. Cross-environment comparisons that are *expected* to differ — different data coverage, different freshness — produce a permanently failing test, which trains everyone to ignore it.

## Whole-relation hashing

Where an adapter provides it, hashing every row and aggregating to a single value answers "identical or not" in one cheap query. Its value is as a gate: identical means stop, no further work needed.

Its limits are worth knowing before relying on it:

- **A mismatch gives no location.** You learn only that something differs, and then need a row-level comparison anyway.
- **It is sensitive to representation, not just to meaning.** A different type, a different precision, trailing whitespace, or a different null encoding produces a different hash for data that is semantically identical. Cross-engine hashing in particular usually needs explicit normalisation on both sides to mean anything.
- **Aggregating row hashes requires order-independence.** An aggregation whose result depends on row order will differ between runs on the same data. If the method is not order-independent, its disagreement is not evidence.

Used as a gate rather than as a verdict, hashing is the best cost-to-evidence ratio available. Used as the only check, a mismatch leaves you exactly where you started.

## Bisecting a difference

When a row-level comparison is too expensive but a hash says the two differ, narrow by segment rather than sampling. Hash a key range or a date range at a time; recurse into whichever segment disagrees. Two or three rounds of halving isolate a difference to a small window, at a fraction of the cost of comparing everything — and unlike sampling, it cannot miss the defect, because every row is covered by some segment.

This is the right technique whenever differences are expected to be rare and localised, which describes most refactor regressions.

## Sampling

Sampling is a cost-control measure. It is not a weaker proof; for some defect classes it is not a proof at all, and knowing which is the entire skill.

### The rule that makes a sample comparable

**Both sides must return the same rows.** A random sample on each side draws different rows, and comparing two different sets of rows says nothing whatsoever — the differences you find are the sampling, not the data.

Sample deterministically, on the key:

```sql
-- both sides, same predicate, same rows
select * from <relation>
where mod(abs(<integer_key>), 100) = 0
```

For a non-integer key, hash it to an integer first and take the modulus of that — the same expression on both sides. Or take contiguous key ranges, which additionally lets you widen the range around whatever you find.

Whatever the method: the sample predicate must be **stated in the result**, so a reader knows what was and was not examined.

### What sampling finds, and what it cannot

| Defect class | Found by a sample? |
|---|---|
| A wrong cast, or a systematically wrong expression | Yes — it affects every row, so any sample catches it |
| A pervasive encoding, type, or precision difference | Yes, same reason |
| A join that fans out across the board | Yes, if the sample preserves the fan-out |
| A boundary defect affecting rows at a specific timestamp | **No** — those rows are almost certainly not in the sample |
| A defect confined to one segment, region, or source system | **No**, unless the sample is stratified to include it |
| A handful of wrong rows | **No.** This is what sampling structurally cannot do |
| An aggregate that must reconcile exactly | **No.** A sampled total is an estimate, and an estimate does not reconcile |

The pattern: **sampling finds systematic defects and misses local ones.** So a passing sample supports "no pervasive difference" and does not support "no difference". Those are different claims, and only one of them is usually what the reader wants.

Two consequences worth stating in the summary rather than leaving implied:

- A percentage match on a sample is not a mismatch count. "99.9% of sampled rows matched" and "all differences identified" are different statements, and only the second lets anyone sign anything off.
- For anything that must reconcile exactly — a financial total, a regulatory figure, a migration cutover — sampling is not sufficient at any confidence level. Full comparison, or an explicit acceptance that the figure is unverified.

### Choosing what to sample when you must

If a full comparison is genuinely out of proportion, narrow along the axis that costs the most and keep full coverage on the axis that matters:

- **Fewer columns, all rows.** The key plus the columns that must be right — monetary values, identifiers, dates — with wide serialized or free-text columns excluded. Usually the best trade, because most defects show up in the columns anyone cares about.
- **All columns, a bounded window.** Full-fidelity comparison over a recent or representative period, with the window named.
- **Key only, all rows.** Proves presence and absence — no rows lost, none added — and nothing about values. A legitimate and clearly-bounded claim.

Each of those is a defensible, stateable claim. "We sampled and it looked fine" is not one of them.
