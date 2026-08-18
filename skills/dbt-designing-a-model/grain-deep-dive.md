# The grain statement, in depth

Read this once the grain is written as a column list (main skill, step 3) and before any SQL. It covers why the grain outranks every other design decision, how to turn the statement into a test, the four ways a relation ends up holding more than one grain, and how to check the proposed grain against the source before designing around it.

## Why this one decision outranks the others

The grain is the highest-leverage decision in the design because **every other decision is evaluated against it, and none of them can be evaluated without it.**

- A candidate dimension is admissible only if it is single-valued at the grain. Without a stated grain there is no test for admissibility, so dimensions get added because they were available.
- A candidate measure is admissible only if it is measured at the grain. A measure belonging to a coarser process — a charge levied per order, on a model at order-line grain — repeats on every row and sums to a multiple of the truth. The grain statement is the only thing that makes this visible before it ships.
- The key is exactly the grain columns, so an unstated grain means an unexamined key, and the uniqueness test then tests the accident rather than the intent.
- Two grains must never share one relation. This is the single rule that prevents the most common wrong total, and it is unenforceable if the grain is not written.

The grain is a binding claim about the data, not a description of it. That is why the next section tests it rather than trusting it.

## A grain statement is a testable assertion — make it one

The value of the column list is that it converts directly into a test that fails when the claim stops being true. Write the test in the same change as the model, not later:

- **Uniqueness over the exact grain column set.** Either a `unique` test on the surrogate key built from exactly those columns, or a combination-uniqueness test over the column list itself. The latter is usually the cheaper test on a large relation and it names the columns in the YAML, which makes the grain readable without opening the SQL.
- **`not_null` on every grain column, not only on the key.** A null in a grain column means the grain claim is false: two rows differing only in that one has a value and the other does not are, by the model's own definition, the same row. Note that a hashed surrogate key is non-null even when a component is null, so testing only the key does not catch this.
- **A row-count expectation, written as a magnitude.** "Roughly one row per active entity per day, so tens of thousands per day, not millions" is enough. It is what makes a fan-out visible on the first build instead of on the first reconciliation.

A grain that cannot be expressed as a passing test is a grain that has not really been chosen. If the honest statement is "one row per X per Y, except that a small number of Xs legitimately have two", then that exception is part of the design and belongs in the description — and the test needs to be written to accept it deliberately rather than being dropped.

## Multi-grain traps

Four ways a single relation ends up holding more than one grain. All four produce a relation that looks fine and double-counts on an unfiltered `sum`.

| Trap | How it happens | Recognise it by |
|---|---|---|
| **Header measures on line rows** | A charge, discount, or total that belongs to the parent is carried onto every child row | The measure has an identical value repeated across a group of rows. Summing it gives a multiple of the true total |
| **Subtotal rows mixed with detail** | The request was a report layout containing totals, and the layout was implemented literally | A relation where some rows are aggregates of others. Any `sum` without a row-type filter double-counts |
| **Two processes unioned at different grains** | Two feeds combined because they share column names, one at a finer grain than the other | Row counts per source in wildly different proportions to their true volumes |
| **A dimension attribute that is multi-valued at the grain** | An entity legitimately has several of something, and the join was written directly | Row count is a clean multiple of the expected count for the affected entities only |

The remedy for the first is **allocation**: push the parent measure down to the child grain using a rule the business supplies — proportional to a child measure, or evenly — and record the rule in the description. Allocation is a business decision, not an arithmetic convenience: choosing to allocate a charge proportionally to value rather than to quantity changes who looks profitable. Never invent the rule. If nobody will supply one, keep the parent measure in a separate model at the parent grain and let consumers align the two, and say why in the design note.

## Sanity-check the proposed grain against the source

A grain is a hypothesis about the source data. Test it before designing around it — this is derivable, so per `dbt-gathering-context` it must never be asked:

```sql
-- Does the proposed combination actually identify a row uniquely?
-- Any output at all means the grain is finer than you think, or a filter is missing.
select <grain_column_1>, <grain_column_2>, count(*) as n
from <source_relation>
group by <grain_column_1>, <grain_column_2>
having count(*) > 1
order by n desc
limit 20
```

```sql
-- The one-line version: how many rows per proposed key?
-- `||` is standard-ish but not universal: some engines need concat() or +.
-- Substitute the dialect's concatenation, and cast components so a null
-- component does not collapse the whole expression to null.
select
    count(*) as rows,
    count(distinct <grain_column_1> || '|' || <grain_column_2>) as distinct_keys
from <source_relation>
```

Rows greater than distinct keys means one of three things, and **which one it is decides the design**:

| Cause | Remedy |
|---|---|
| A grain column is missing from your list | Add it. The grain is finer than you stated |
| The source legitimately holds multiple events per key | Your grain requires aggregation, and you must decide which aggregate: sum, last, max |
| The source has duplicates it should not have | A data-quality problem, not a design input. Whether it is a bug is the `intent` ask-class — see `dbt-data-quality-triage`, and do not silently deduplicate around it |

Deduplicating to force a hypothesized grain, without establishing which of the three you are in, is how a model quietly discards real rows.

## Then check the grain is reachable

Confirm every grain column exists somewhere upstream at the required granularity before committing. A grain that requires a dimension the sources do not carry is not a grain, it is a request for a new source — a much larger piece of work, and one the requester needs to hear about now rather than after two days of implementation.
