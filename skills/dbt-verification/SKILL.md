---
name: dbt-verification
description: Use when about to claim a change works, is complete, or is output-neutral. Also use when deciding between compile, run, build and test, when reconciling row counts, when comparing two versions of a model with audit_helper, or when reading the compiled SQL to confirm what a model actually does. The single reference for what counts as evidence.
metadata:
  phase: prove
---

# Verification

Every other operation skill ends here, because every operation ends with the same question: **how do you know?**

The standard is stated in the universal rules of `AGENTS.md`, and it is short. Done requires an external signal — compiled output, a query result, a passing test. "This should work" is not done. Never state a row count, a schema, or a behavior you have not queried.

This skill is how to satisfy that standard, and how to say precisely what you did and did not prove.

## Evidence versus assertion

The distinction is not about confidence. It is about whether a reviewer can check you.

| Assertion | Evidence |
|---|---|
| "The SQL looks correct" | `dbt compile` succeeded and the compiled SQL was read |
| "The logic is unchanged" | `compare_relations` returned zero differing rows |
| "Row counts should match" | Both counts queried; both numbers reported |
| "The column is populated" | `count(*) - count(<column>)` queried and reported |
| "Tests pass" | `dbt build` output shows the test names and PASS |
| "It runs in production" | The production run for that model was inspected |

An assertion becomes evidence when it carries a number or a command output someone else could reproduce. If you cannot produce one, say so — see *When verification is not possible*.

## The ladder

Each rung costs more and proves more. Do not stop below the rung your claim requires.

| Rung | Command | Proves | Does **not** prove |
|---|---|---|---|
| 1. Parse | `dbt parse` | The project is structurally valid; refs resolve; the manifest builds | Nothing about the SQL |
| 2. Compile | `dbt compile --select <model>` | Jinja renders; refs resolve to real relations; the SQL text is what you intended | That the warehouse accepts the SQL, or that the output is correct |
| 3. Dry build | `dbt run --empty --select <model>` | The warehouse **parses and plans** the SQL — column names, types, function signatures, ambiguous references | Anything about the data |
| 4. Run | `dbt run --select <model>` | The model materializes over real data | That the output is correct |
| 5. Build | `dbt build --select <model>` | Materializes **and** runs its tests | Anything a test does not assert || 6. Query | `select ... from <db>.<schema>.<model>` | Concrete facts: counts, nulls, ranges, min and max | Equivalence to a previous version |
| 7. Grain check | `count(*)` against `count(distinct <declared_key>)` | The grain is the one that was declared | That the values within each row are right |
| 8. Aggregate comparison | Two aggregates on both sides, grouped by period | Cardinality and one measure agree, period by period | That any other column agrees |
| 9. Row-level comparison | `audit_helper` on a verified key | Which rows and which columns differ | Correctness, if the baseline was wrong |
| 10. Statistical comparison | Distribution of a column on both sides | The shape of the data is unchanged where a row-level match is impossible | Equality. Two different datasets can share a mean |

Rung 3 is the most underused. It catches almost every syntax and type error at near-zero cost, which makes it the right check on an expensive model before committing to a full build. (`--empty` requires dbt 1.8 or later; without it, rung 3 is unavailable and you go straight to rung 4.)

Rung 5 has an ordering worth knowing: where a model has unit tests, `dbt build` runs them **before** materializing it, then runs the data tests after. A failing unit test therefore costs nothing to discover — the model is never built. That makes a unit test on expensive logic cheaper than it looks, and it is the reason `dbt build` is preferable to `run` followed by `test` on anything costly.

Rung 7 is the cheapest rung that catches a whole class the rungs below it cannot see. Rungs 1 to 6 are all satisfied by a model that silently doubled its grain.

Rung 10 is a fallback, not a peer of rung 9. Reach for it when a row-level comparison is impossible — no key, a non-deterministic ordering, a genuinely different but supposedly equivalent computation — and say that you did, because a distribution match is materially weaker evidence than a row match and a reader must be told which they were given.

### The ladder is not a schedule

Two rungs on the same claim are worth more than five rungs in sequence on a claim that only needed two. Pick the rung the claim requires, then stop:

| The claim you are about to make | The rung that supports it |
|---|---|
| "It compiles" | 2 |
| "The SQL is valid for this warehouse" | 3 |
| "It builds and the project's own tests pass" | 5 |
| "The grain is one row per X" | 7, with X named from outside the query |
| "The totals are unchanged" | 8 |
| "The output is identical" | 9, zero differences |
| "The output is equivalent, and no row-level comparison was possible" | 10, with the limitation stated |
| "The numbers are correct" | **No rung on this ladder.** Correctness needs an independent source of truth — see `dbt-data-quality-triage` |

That last row is the important one. Every rung above compares against something inside the system or against a previous version of it. Not one of them establishes that the answer is right.

### Compile does not mean it runs

A common and costly conflation. `dbt compile` renders Jinja and resolves refs. It does not send the statement to the warehouse for validation. A model that compiles cleanly can still fail on a misspelled column, a type mismatch, an ambiguous reference, or a function that does not exist in this dialect.

Conversely, compile *can* fail for reasons unrelated to your SQL — a macro performing an introspective query needs a warehouse connection, so compile is not purely offline.

## Read the compiled SQL, not the model file

The model file is a template. The compiled artifact is what runs. Between them sit macros, variables, environment conditionals and incremental logic, any of which can make the two differ substantially.

```bash
dbt compile --select <model>
# then read:
target/compiled/<project_name>/models/<path>/<model>.sql
```

`<project_name>` is the `name` from `dbt_project.yml` (`project.dbt_project_name` in the contract, if the project has one).

Also useful: `target/run/<project_name>/models/...` holds the full DDL/DML wrapper — the `create`, `merge`, or `insert` that the materialization generates. For an incremental model this is where the merge predicate becomes visible, and predicates are where incremental models go wrong.

### What only the compiled SQL will tell you

| Construct | What can be hidden |
|---|---|
| `{{ ref() }}` | Which database and schema you are actually reading — see `dbt-environments` |
| Custom macros | Expanded logic differing from the macro's name or docstring |
| `{% if %}` on environment | A branch that did not fire, so a guard silently did nothing |
| `is_incremental()` | Which branch you are testing; the two branches are different code |
| `incremental_predicates` | The scan boundary, visible only in `target/run/` |
| `{{ var() }}` / `{{ env_var() }}` | The value actually substituted |
| A macro loop | The number of columns or `union` branches generated |

When a result is surprising, read the compiled SQL before forming a theory. Reasoning about the template while the warehouse executed something else is the most common way an hour disappears.

### Diffing compiled output

For a change intended to be textually neutral — extracting a macro, reformatting, reordering CTEs — comparing compiled SQL before and after is the strongest available evidence, and it is stronger than a data comparison: identical compiled SQL provably cannot produce different output.

```bash
dbt compile --select <model> && cp target/compiled/<project_name>/models/<path>/<model>.sql /tmp/<model>.before.sql
# make the change
dbt compile --select <model>
diff /tmp/<model>.before.sql target/compiled/<project_name>/models/<path>/<model>.sql
```

Empty diff, nothing further needed. Non-empty diff on a change you believed was textual means your belief was wrong.

## Row-count reconciliation

The cheapest check that catches the largest class of mistakes: an unintended change to the grain or to a join's cardinality.

```sql
select count(*) as rows,
       count(distinct <key_column>) as distinct_keys
from <database>.<schema>.<model>
```

Read it as three separate facts:

| Observation | Meaning |
|---|---|
| `rows = distinct_keys` | Grain is what you think it is |
| `rows > distinct_keys` | Duplication — usually a join fanning out |
| `rows` moved after an "additive" change | The grain changed. It was not additive |
| `rows` unchanged, a measure's total moved | Values changed, not cardinality — a different investigation |

**`<key_column>` must come from outside the query.** This check only means something if the intended grain was written down before the model ran — in the design note, the YAML description, or the `unique_key`. If you pick the key by looking at the output and choosing the columns that happen to be distinct, you have confirmed the grain the SQL produced, which is not a check, it is a restatement. When a model has no stated grain anywhere, that is the finding to report; establish the grain first, per `dbt-designing-a-model`.

Row count alone is weak evidence of correctness and strong evidence of *incorrectness*. A matching count does not mean the data matches; a changed count on a change that should not have changed it is close to proof of a bug.

Pair it with a sum over a numeric column. Two aggregates that both match are meaningfully better than one, and the pair costs one query.

### Group the comparison by period

An unqualified total hides the two most common shapes. A gain in one period offset by a loss in another produces a total that matches perfectly while the data is wrong in both:

```sql
select
    <date_column>,
    count(*)        as rows,
    sum(<measure>)  as total
from <database>.<schema>.<model>
group by 1
order by 1
```

Run the same on both sides and read the two series, not the two totals. **A matching grand total with differences that cancel is the single easiest verification result to misread as a pass.** Grouping costs nothing extra and removes the possibility.

Two more cheap facts worth including, because each catches a class the counts cannot:

| Query | Catches |
|---|---|
| `min(<date_column>)`, `max(<date_column>)` | Coverage silently shrinking or extending — a window changed, or history dropped |
| `count(*) - count(<column>)` per important column | A column that went null without the row count moving |

A null-rate that shifts while the row count holds is a real defect that every count-based check passes.

## Comparing two versions with `audit_helper`

When a baseline exists — the previous build, production, an old model being replaced — compare row by row rather than eyeballing.

```sql
{{ audit_helper.compare_relations(
    a_relation=api.Relation.create(
        database='<baseline_database>', schema='<baseline_schema>', identifier='<model>'
    ),
    b_relation=ref('<model>'),
    primary_key='<pk_column>'
) }}
```

Run it through `dbt show --inline "..."`. Acceptance criterion: **zero rows only in A, zero only in B, zero differing.** Anything else means behavior changed; investigate rather than rationalize a small diff.

| Helper | Use when |
|---|---|
| `compare_row_counts` | First pass. Cheap, and a mismatch ends the investigation early |
| `compare_relation_columns` | Before any value comparison — confirms both sides expose the same columns and types |
| `compare_relations` | The main test. Needs a primary key |
| `compare_all_columns` | Rows differ and you need to know which columns |
| `compare_column_values` | One column is suspect |

Caveats that matter in practice:

- **Verify the key before trusting the output.** Every key-joined comparison assumes the key is unique and non-null on *both* sides. Given duplicates, the join fans out and the tool reports a large number of differences that do not exist. One query settles it: `count(*)`, `count(<key>)` and `count(distinct <key>)` must all be equal, on both relations.
- **No primary key** → row-level comparison cannot run. Fall back to aggregate comparison and state plainly that the evidence is aggregate-level, not row-level.
- **Floating-point reordering** can produce differences in the final decimal places with no logic error. Compare with a tolerance, and say that you did.
- **Different date coverage** between the two relations will show as rows-only-in-A. Restrict both sides to the same window before concluding anything.
- **A full comparison scans both relations completely.** On a large model, bound both sides to a common window, or gate on a cheap identity check first.
- **Excluding a column changes what you proved.** Excluding load timestamps and batch identifiers is correct. Excluding a business column to make the comparison pass is not — if you do it, name the column and say why.
- The baseline can be wrong. Equivalence to a baseline proves you changed nothing, not that either version is correct.

The full macro inventory, how to read the match categories without collapsing "missing row" into "different value", whole-relation hashing, difference bisection, and deterministic sampling are in [comparison-techniques.md](comparison-techniques.md).

Details of the before/after workflow live in `dbt-refactoring-safely`.

## Two different burdens: neutrality and effect

Almost every change carries one of two claims, and they need opposite evidence. Confusing them is why verification often feels thorough and proves nothing.

| | **Neutrality** — "this changed nothing" | **Effect** — "this did what I said" |
|---|---|---|
| Typical change | Refactor, macro extraction, reformatting, relayering, materialization change | New column, fixed defect, new logic, corrected boundary |
| Strongest evidence | Identical compiled SQL — it proves the output *cannot* differ, without building anything | A measurement of the specific thing claimed, before and after |
| Next best | Row-level comparison against a baseline, zero differences | A query showing the previously-wrong rows are now right, and a count of how many changed |
| A difference means | The claim is false. Investigate; do not rationalize | Possibly nothing — unless the difference is larger or in a different place than predicted |
| The trap | Accepting a small diff | Proving the model built, and never measuring the effect at all |

**For a neutrality claim, the acceptance criterion is zero.** There is no small acceptable difference, because the claim was that there is none. The only legitimate exception is a floating-point tolerance, and it must be quantified and stated.

A refactor is the canonical neutrality claim, and it has only two acceptable outcomes: **an empty compiled-SQL diff**, or **a row-level comparison with zero differences in either direction**. Anything weaker is a partial result. The ordering discipline for capturing the baseline first is in `dbt-refactoring-safely`; the comparison mechanics are here.

**For an effect claim, "it builds and tests pass" is not evidence of the effect.** A build proves the model still works; it says nothing about whether the fix fixed anything. The evidence for an effect claim is nearly always a comparison of the same specific measurement before and after — and crucially, **predicted in advance**:

```
Claimed: the boundary fix recovers rows previously dropped at the run boundary.
Predicted: ~200-400 additional rows per day, in the most recent 30 days only,
           no change before that window.
Measured: +287 rows/day mean over 30 days; zero change in rows before the window.
```

Stating the prediction first is what makes the measurement a test rather than a description. A number produced after the fact can be made to sound like a confirmation of almost anything; a number that had to land in a stated range either does or does not.

A change that produces a difference *elsewhere* than predicted is a finding, not a success, even when the predicted difference also appeared. That is a second, unintended effect, and it is the one that will be discovered later by someone else.

## Match the verification to the change

| Change | Minimum sufficient evidence |
|---|---|
| Formatting or comments only | Compiled SQL diff is empty |
| Macro extraction | Compiled SQL diff is empty |
| Logic refactor, output intended identical | `compare_relations`, zero diffs |
| New column added | Column exists with expected type; null rate reported; row count unchanged |
| New model | Builds; tests pass; row count and grain reported; spot-check against the source of truth |
| Column removed or renamed | Downstream and BI references searched; full downstream build green |
| Grain change | Row count before and after, both reported and both expected |
| Incremental strategy or predicate change | Both branches exercised; a re-run produces no duplicates; boundary rows present |
| Performance change | Runtime or bytes-scanned before and after, both measured |
| Config-only change | Compile, plus the built object's properties inspected in the warehouse |
| Defect fix | The previously-wrong rows queried and shown correct; the number of rows changed, matched against a prediction made first |
| Backfill or rebuild | Row count and one aggregate per period across the backfilled window; the window's boundaries queried, not assumed |
| A model replacing an existing one | Row-level comparison over a common window on a verified key, plus the grain of both stated |
| Source or seed added | Row count and column types queried from the loaded relation, not from the file |

## Reading run results

The console scrolls. The artifact does not.

```bash
python3 -c "
import json
r = json.load(open('target/run_results.json'))
for n in r['results']:
    print(n['status'], n['unique_id'], n.get('execution_time'))
"
```

Statuses: `success`, `error`, `skipped`, `pass`, `fail`, `warn`. Two worth pausing on:

- **`skipped`** — an upstream node failed, so this one never ran. A run containing skips has verified less than its summary line implies.
- **`warn`** — a test failed at `warn` severity. It did not stop the run and it is easy to miss. Report warnings; do not let a green summary bury them.

Also check `rows_affected` where the adapter populates it. Zero rows affected on an incremental run you expected to load data is a finding, not a success.

## What "verified" cannot mean

The most useful section in this skill, because the failures it describes all look like successes.

### Passing tests is not proof of correctness

A test suite asserts what someone thought to assert. Its passing tells you those specific assertions hold, and nothing else. Concretely, a fully green run is compatible with all of the following:

| Green run, and yet | Why no test caught it |
|---|---|
| The grain doubled | Nothing asserts the grain unless a uniqueness test names the true business key |
| Every value in a measure is 10× too large | No test bounds a measure's magnitude by default |
| A column is null for all of history and populated recently | `not_null` fails on *any* null; a column that is null everywhere fails, but one that is null only in an untested period may not be tested at all |
| Half the expected rows never arrived | Nothing asserts volume unless someone wrote a row-count assertion |
| A category was silently rebucketed into an existing one | `accepted_values` passes — the value is in the list, it is just the wrong one |
| A timezone shifted every timestamp by hours | No test asserts what day a row belongs to |
| A join dropped rows on a `where` that became restrictive | Referential tests check the rows present, not the rows lost |

The general shape: **tests detect violations of stated invariants, and most correctness lives in unstated ones.** So "all tests pass" is an accurate and narrow claim. Reporting it as "verified" widens it into a false one.

Two second-order traps in test evidence specifically:

- A test with a `where` clause proves nothing about the rows it excluded, and the test name does not say so.
- A test at `warn` severity that failed still shows a green summary. Read the artifact.

### Absence of failure is not evidence

"No errors" and "it works" are different claims, and so are "nothing found" and "nothing there". The distinction is whether anything actually looked.

| What is often written | What was actually established |
|---|---|
| "No downstream references" | A search was run, over a specific set of places, with a specific pattern. Say which — and BI tools, notebooks, external queries and scheduled extracts are frequently outside it |
| "No duplicates" | A `group by ... having count(*) > 1` returned nothing, on a named key, over a named window |
| "The column is unused" | A search found no references in the places searched. Usage that does not appear in a repository is invisible to it |
| "It ran fine" | It exited zero. That is compatible with skipped nodes and failed warn-level tests |
| "No difference" | A comparison ran, on a verified key, over a common window, with the excluded columns named |

Rewriting each of these as what was actually done takes one clause and is the difference between evidence and a claim. Where a check was genuinely not possible, that belongs in the summary too — see *When verification is not possible*.

### An unbuilt, unqueried, or empty result is not a pass

Three specific ways a verification step returns "fine" without having examined anything:

- **A query over the wrong relation.** A validation query resolved to a different environment answers a question about a different table, confidently. This is why validation queries name the database and schema explicitly rather than using `ref()`.
- **A query over an empty table.** Every aggregate over zero rows is null or zero, and every comparison against an empty baseline shows no differences. Confirm both sides are non-empty before reading any comparison result.
- **A skipped node.** A run in which the model was skipped because an ancestor failed produces no failure for that model. Check statuses in the artifact, not the summary line.

### Verification you did not do

The completion statement is where these converge. Anything on the ladder that was skipped, any check that was scoped, any comparison whose baseline was itself unvalidated: name it. A summary that lists only what passed is not a partial account, it is a misleading one, because the reader cannot distinguish "checked and fine" from "not checked".

## When verification is not possible

Sometimes it genuinely is not: no baseline exists, the data is not reproducible, the warehouse is unreachable, the volume makes a comparison impractical.

The correct response is to say so, specifically. Not to soften the claim and move on.

```
Verified: compiles; builds; 4 tests pass; row count 1,204,331 unchanged from baseline.
Not verified: correctness of the new revenue attribution against finance's figures —
no independent source available in dev. Needs review by someone who can check the
attribution rule.
```

That paragraph is more useful than a confident summary, and it is the difference between an agent that can be trusted with production and one that cannot.

## How to report

Every completion statement should answer three things: what was run, what the numbers were, and what remains unproven. Include the actual output rather than a description of it. A reviewer who has to take your word for a count has not been given evidence.

Two properties make a report checkable rather than merely confident:

- **Every claim carries its scope.** Not "no duplicates" but "no duplicates on the order key over the last 90 days". Not "row-level comparison passed" but "row-level comparison on `order_id`, both sides restricted to the last 30 days, metadata columns excluded". The scope is what lets a reviewer find the gap you did not.
- **The unverified part is a section, not an omission.** A reader cannot distinguish something you checked and found fine from something you never checked, unless you say which.

Never write "should work", "looks correct", or "logic is equivalent" as a conclusion. Each is a signal that a rung on the ladder was skipped.

## Completion checklist

- [ ] The claim identified as a neutrality claim or an effect claim
- [ ] The rung of the ladder that claim requires named, and reached
- [ ] Compiled after every SQL edit
- [ ] Compiled SQL read, not just the model file
- [ ] Warehouse-level validity established — a real run, or `--empty`
- [ ] Tests run, not just the model — `build` rather than `run`
- [ ] Row count and one aggregate queried and reported as numbers, grouped by period rather than as a single total
- [ ] Grain checked against a key declared **outside** the query
- [ ] Both sides confirmed non-empty before reading any comparison result
- [ ] Comparison key verified unique and non-null on both sides before a row-level comparison
- [ ] Both sides bounded to the same window; excluded columns named
- [ ] Baseline comparison run where a baseline exists, with its acceptance criterion met — zero for a neutrality claim
- [ ] For an effect claim: the expected difference predicted before measuring, and the measurement matched against it
- [ ] Any sampling done deterministically, with the sample predicate stated
- [ ] `run_results.json` checked for `skipped` and `warn`
- [ ] Scope of every "no X found" claim stated — what was searched, and what was not
- [ ] Unverified aspects named explicitly, not omitted

## The failure modes to watch for

1. **Compile mistaken for correctness** — it renders Jinja; it does not ask the warehouse anything. Add `--empty` or a real run.
2. **Reasoning about the template** — the theory is built from the model file while the warehouse ran something else. Read `target/compiled/`.
3. **`run` instead of `build`** — the model materialized and no test was executed, so the checks the project already wrote were skipped.
4. **Passing tests reported as verified correctness** — tests assert what someone thought to assert. A green suite is compatible with a doubled grain, a 10× measure, and a shifted timezone.
5. **A green summary hiding skips and warnings** — read the artifact, not the last line.
6. **Row count matched, so it is fine** — matching counts are compatible with every value being wrong. Add an aggregate or a row-level comparison.
7. **An ungrouped total** — a gain in one period offset by a loss in another matches perfectly and is wrong twice. Group by period.
8. **Choosing the grain key from the output** — picking the columns that happen to be distinct restates what the SQL did. The key must be declared before the model ran.
9. **Comparing on an unverified key** — duplicates in the key make the comparison join fan out and report differences that do not exist. Check uniqueness and non-nullness on both sides first.
10. **Comparing different windows** — unequal date coverage dominates the output with rows-only-on-one-side that are not defects, and the result reads as a large regression.
11. **Random sampling on both sides** — two different samples are not comparable, and the differences found are the sampling. Sample deterministically on the key.
12. **A passing sample reported as no difference** — sampling finds systematic defects and structurally misses local ones. The supportable claim is "no pervasive difference".
13. **Proving a build instead of an effect** — the model built, so the fix is declared done, while the thing it was supposed to change was never measured.
14. **Baseline captured too late** — after the rebuild there is nothing to compare against, and production becomes the only baseline available, differing for unrelated reasons.
15. **Equivalence to a baseline reported as correctness** — a zero-difference result against a wrong baseline proves the defect was faithfully reproduced.
16. **"No X found" without a scope** — a search over one set of places is evidence about those places. Absence of a finding is not absence of the thing.
17. **Silence on what was not proven** — the most damaging one, because it is invisible to the reader.
