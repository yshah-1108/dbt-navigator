# Additivity, in depth

Read this once every measure in the design has been classified fully additive, semi-additive, or non-additive (main skill, step 6). This covers why each class fails the way it does, the traps that recur across all three, and how to keep a measure's definition conformed across models.

**Fully additive** is the only class that is safe by default, and it is why the numerator-and-denominator rule below exists for the non-additive class.

## Semi-additive measures are the quiet ones

Summing a daily balance across a month produces a number roughly thirty times too large — large enough that someone notices, but only if they know what the right magnitude is. Summing across accounts on a single day is correct. The measure is therefore not wrong, it is *conditionally* wrong, and no test can distinguish the two cases because both are just `sum`. The design obligation is to name the time-aggregation rule in the measure's description, so the consumer that reads it does not have to guess.

Two things make the semi-additive bug more likely than its simplicity suggests, and both are worth pre-empting in the design:

- **A semi-additive measure is indistinguishable from an additive one by inspection.** A column of numbers named for a level looks exactly like a column of numbers named for a flow. The only signal is the semantics, which is why the classification has to be written down rather than inferred by the next reader.
- **The correct time aggregation is a choice, not a fact.** `last` (the closing value), `first` (the opening value), and `average` (the mean over the period) are all defensible and give different answers, and finance and operations frequently want different ones. Name which one, and whose requirement it is. A period-end balance and a period-average balance are two measures, not one measure aggregated two ways, if both are needed regularly.

## Non-additive measures are the expensive ones

The mistake here is structural rather than arithmetic. A stored rate averaged across rows gives every row equal weight regardless of its denominator, so a row covering ten events and a row covering ten thousand contribute equally. The result is not a rounding difference; it can be off by a large factor and it moves in the wrong direction when the data changes.

```sql
-- Store these
sum(<numerator_column>)   as <numerator_column>,
sum(<denominator_column>) as <denominator_column>
-- and let the consumer compute sum(numerator) / sum(denominator)

-- Not this, as the model's only answer
<numerator_column> / <denominator_column> as <rate_column>
```

Storing the rate *in addition* is acceptable and often convenient at the model's own grain — it is correct at that grain, and only at that grain. When you do, name it so its grain is unmistakable and state in its description that it must not be averaged. The numerator and denominator must still be present, because they are the only things that make the rate recomputable at any other grain.

**A weighted average is a ratio wearing a disguise.** Store the weighted numerator and the weight, not the average. An average of averages is wrong for exactly the same reason and by the same mechanism, and it is harder to spot because the column is called an average and averaging it looks like the obvious thing to do.

## Four related traps worth deciding now

- **A count of a distinct thing is not additive across the dimension it is distinct over.** Distinct entities per day cannot be summed to a month; the same entity appears on several days. Either store the finer grain and let the consumer count distinct, or state the aggregation window in the column name so nobody sums it.
- **`null` and `0` are different for every class.** Decide which one an absent measure is, in the design's key space, and be consistent. A `null` that should be `0` breaks a sum's neighbors after arithmetic; a `0` that should be `null` invents data.
- **A running or period-to-date total is not additive at all** — it already contains its own history, so summing two of them double-counts the overlap. Store the periodic measure and let the consumer accumulate. A stored period-to-date column also has to be recomputed whenever the period boundary definition changes, and every consumer that hard-coded it silently disagrees with the new one.
- **A measure expressed in two units, or two currencies, is two columns.** Store the value in its original unit and the value in the standard unit, plus the identifier of the original unit — or store the standard value plus the conversion factor, so the original is recoverable. Storing only the converted value destroys the ability to reconcile against the source system, which will report in the original. The conversion rate is itself an as-of decision; see the conforming discussion in `dbt-unifying-sources`.

## Conform the measure's definition, not just its name

Two facts that both carry a column with the same name must compute it identically, or the name is a lie that survives every test. If they are compatible, name them identically so they can be compared. If they are not, **name them differently on purpose** — an awkward, specific name that forces a reader to notice is cheaper than a shared name that lets two definitions be summed together.

This is a design-time obligation because it is unenforceable later: once two models both have a column called by the same name, every consumer has already assumed they match.
