---
name: dbt-refactoring-safely
description: Use when changing SQL without intending to change output — restructuring CTEs, simplifying logic, extracting a macro, reformatting, cleaning up a model, or bringing a legacy query or stored procedure into dbt. Covers proving output equivalence rather than assuming it, and separating behaviour-preserving from behaviour-changing edits.
metadata:
  phase: reference
---

# Refactoring safely

A refactor is defined by its **output being identical**. If the output changes, it was not a refactor — it was a change, and it needs the treatment in `dbt-breaking-changes`.

The entire discipline is: **prove equivalence, do not assume it.** "The logic looks the same" is not evidence. Reviewers cannot verify it either, which is why refactors are where silent regressions enter a project.

## Is this the right skill?

Several skills cover "change the code without changing the answer." They are not interchangeable, and loading the wrong one costs you the step ordering or the proof:

| If you are… | Use |
|---|---|
| Rewriting SQL *inside* a model — CTEs, logic, formatting, extracting a macro | **this skill** |
| Bringing a query, script, or stored procedure from outside dbt into dbt | **this skill** — the migration procedure below, then the equivalence proof |
| Changing the *shape of the DAG* — splitting, combining, inserting a layer, repointing a `ref()`, flattening a chain, or changing a materialization | `dbt-restructuring-dags` — the risk is step ordering, and it delegates the proof back here |
| Changing what the model *outputs* — a renamed or removed column, a new grain | `dbt-breaking-changes`. If output changes, it was never a refactor |
| Proving any change did what you claim | `dbt-verification` — owns the evidence ladder both of the above rely on |

A request like "split this model up" or "these three models should be one" is DAG work: go to `dbt-restructuring-dags` first and come back for the equivalence proof. Getting this backwards is how a restructure breaks the project for a commit — the ordering rules are the whole point of that skill.

One more routing case: **bringing SQL that lives outside dbt into dbt** — a stored procedure, a scheduled script, a query embedded in a BI tool. That is a migration followed by a refactor, and the migration half has its own procedure. See *Migrating SQL into dbt* below.

## Behaviour-preserving versus behaviour-changing

Every edit to a model's SQL is one of two things, and the whole discipline depends on knowing which before you start. The two need different evidence, and mixing them in one commit destroys the evidence for both.

| Edit | Class | Evidence required |
|---|---|---|
| Reformatting, reindenting, changing keyword case | Preserving | Compiled SQL diff, semantically empty |
| Renaming a CTE, reordering independent CTEs | Preserving | Compiled SQL diff, or row-level comparison |
| Flattening a subquery into a CTE, or the reverse | Preserving | Row-level comparison |
| Extracting a macro that generates identical SQL | Preserving | Compiled SQL diff, empty |
| Replacing a `left join` + `where ... is not null` with an `inner join` | **Changing** — unless proven equivalent on this data | Row-level comparison, and a reason |
| Replacing `distinct` with a window function, or the reverse | **Changing** — tie-breaking differs | Row-level comparison |
| Adding `coalesce` to a nullable expression | **Changing** — nulls become values | Row-level comparison, and a decision recorded |
| Changing `union` to `union all`, or the reverse | **Changing** — duplicates appear or disappear | Row count, and a reason |
| Adding or reordering a `qualify`/`row_number` predicate | **Changing** unless the ordering is total | Row-level comparison |
| Casting differently, or changing precision | **Changing** — values may round differently | Value-level comparison on the column |
| "Simplifying" a `case` expression | **Changing** if the branches were not mutually exclusive, or if the default differed | Row-level comparison |

The right-hand column is not the point. The point is the middle one: **several edits that feel like cleanups are behaviour changes**, and the tell is always that the old form and the new form disagree on an edge case — a null, a tie, a duplicate, a boundary value. Those edge cases may be absent from today's data, which means the comparison passes and the change is still wrong. When you make one of those edits, say which edge case the two forms differ on and whether the data currently contains it.

### Keeping them in separate commits

If a cleanup and a fix are both needed, the order is: **refactor first, prove it zero-diff, commit; then change the behaviour, prove the diff is exactly what you intended, commit.**

Reasons this matters more than it sounds:

- **A refactor commit has a mechanical acceptance criterion.** Zero difference. It can be reviewed in seconds and reverted without thought. Bundling turns it into a judgment call.
- **A behaviour change reviewed against a refactored baseline shows only the behaviour change.** That diff is the reviewable artifact. Against an unrefactored baseline it shows both, and the reviewer cannot separate them.
- **Reverting is asymmetric.** A logic problem found in production needs the logic reverted, not the formatting. A bundled commit forces you to revert both, re-breaking whatever the formatting fixed and re-triggering every downstream rebuild.
- **A non-zero diff on a bundled commit is unattributable.** Two candidate causes, no way to distinguish them, so it gets rationalized. This is the specific mechanism by which silent regressions enter projects.

If you have already bundled them, the fix is not to explain the diff. It is to split the commit: revert to the baseline, make the mechanical change alone, verify zero diff, then reapply the behaviour change.

## The sequence

### 1. Capture the "before" — while you still can

This is the one step that is unique to refactoring, and the one that cannot be recovered later.

```bash
dbt build --select <model>
```

Then clone the built table so it survives your rebuild:

```sql
create or replace table <dev_db>.<dev_schema>.<model>__before
as select * from <dev_db>.<dev_schema>.<model>
```

Order matters absolutely here. Skip it and the only baseline left is production, which differs from your dev output for reasons unrelated to your change — so every diff becomes unattributable and the refactor can no longer be proven at all.

### 2. Refactor

Change the SQL. Do not change anything else at the same time — no renames, no new columns, no config changes. A refactor bundled with a behavioral change cannot be verified, because any diff has two possible causes.

### 3. Rebuild and compare

```bash
dbt build --select <model>
```

Then prove equivalence against the baseline. **The mechanics live in `dbt-verification` — use its evidence ladder and state which rung you reached.** For a refactor the target is rung 1 or 2 and nothing lower:

- **Rung 1** — identical compiled SQL. Available whenever the refactor is meant to be textual, and it is the strongest evidence there is: it proves the change *cannot* alter output, without building anything.
- **Rung 2** — `audit_helper.compare_relations` against `<model>__before`, zero rows differing in either direction.

Anything weaker is a partial result, not a pass. If the model has no primary key, or diffs turn out to be floating-point noise from reordered arithmetic, `dbt-verification` covers both cases — apply them there rather than inventing a local variant.

**A refactor's acceptance criterion is zero difference.** Do not rationalize a small diff. A diff means one of two things: the refactor changed behavior, or it was never a pure refactor. Both need investigating before you proceed, and neither is a rounding concern.

### What a zero-diff result does not prove

`dbt-verification` owns the mechanics of the comparison; this is about the scope of its conclusion. A clean `compare_relations` proves the two versions agree **on the rows that were in both relations, in the environment you ran it in**. Four gaps survive that, and each has produced a real regression:

| Gap | Why the comparison misses it | What to do instead |
|---|---|---|
| The dev relation is filtered or sampled | Many projects limit data volume in development, so the comparison covers a slice. An edge case outside the slice is untested | Say which window and volume the comparison covered. Where the project has a mechanism for widening dev data, use it for the affected date range |
| The edge case is absent from current data | A `left join`-to-`inner join` change is only detectable if unmatched rows exist today | Name the edge case the two forms differ on, and check for it directly: count the rows that would behave differently |
| Only one branch of a conditional was compiled | `is_incremental()` and environment conditionals produce different SQL per branch. Comparing one branch says nothing about the other | Read the compiled SQL for both branches, and exercise both |
| Column order or type changed while values did not | Row-level comparison is value-based; a contracted model or a consumer using positional references still breaks | Compare the built relation's column list and types, not only its rows |

State the scope with the result. "Zero differing rows over the last 30 days in dev" is evidence. "Zero differing rows" is a claim that sounds larger than what was measured.

## Refactoring an incremental model

The riskiest kind of refactor, because the model has **two bodies of SQL** — the full-refresh branch and the incremental branch — and the default comparison exercises one of them.

Three rules specific to this case:

1. **Compile and read both branches.** `is_incremental()` is false on a first build in a fresh schema and true afterwards, so which branch you compiled depends on state you may not have checked. Read `target/compiled/` for the branch you got, and `target/run/` for the merge or insert statement the materialization generated — the predicate lives there and predicates are where incremental models go wrong.
2. **Prove equivalence on both paths.** Build once full-refresh and compare; then run incrementally over a period that includes the boundary and compare again. A refactor that is correct on a full refresh and wrong at the boundary is the normal failure, not an exotic one.
3. **Re-run without new data and confirm nothing changes.** A refactor that altered the merge key or the deduplication produces duplicates or overwrites on the second run, not the first.

If the model is `full_refresh=false` because its source cannot reproduce history, a full-refresh comparison is not available at all. That is a genuine limit: say that the equivalence proof covers the incremental path only, and use the backfill guidance in `dbt-incremental-models` rather than reaching for a rebuild.

## Refactoring Jinja and macro-heavy SQL

The model file and the SQL that runs can differ substantially, and refactoring the template while reasoning about the template is how an hour disappears.

- **Compile before and after, always, and diff the compiled output** — not the model file. This is the entire safety mechanism for Jinja work.
- **A macro loop that generates columns or `union` branches can change count silently.** Diff the compiled SQL and count the generated fragments, rather than trusting that the loop is equivalent.
- **A conditional that did not fire looks like it worked.** An environment guard whose branch was not taken produces no error and no effect. Check that the branch you intended is present in the compiled output.
- **Introspective macros need a warehouse connection at compile time**, so `dbt compile` is not purely offline for such models and can fail for reasons unrelated to your edit.
- **Whitespace control changes can alter generated SQL structurally**, not just cosmetically — a stripped newline that joins two clauses is a syntax error at best and a different statement at worst.

Where the refactor is a macro extraction, the target is a byte-identical compiled diff, which makes all of the above checkable in one step.

## Migrating SQL into dbt, then refactoring it

Legacy SQL — a stored procedure, a scheduled script, a query pasted into a BI tool — arrives as one large statement with hardcoded relation names and no tests. The temptation is to modernise it while porting it. Resist that, for one reason: **you will not be able to prove anything.** A rewritten port produces a diff against the original with dozens of candidate causes.

The published procedure separates migration from refactoring, and the separation is the whole value:

### Phase 1 — Get it running in dbt, unchanged

Copy the query into a `.sql` file under `models/` and run it. Nothing else. The goal is a dbt-built relation that matches the legacy output, so that everything after this has a baseline.

The only edits permitted in this phase are the ones without which it cannot run at all: dialect differences if the warehouse also changed — a function that does not exist here, different date arithmetic, different string handling. Anything you want to fix, write down and leave alone. **More changes means more auditing.**

Then capture the baseline: build it, clone the result, and — where the legacy job still runs — compare against the legacy output directly, which is a stronger baseline than your own first build.

### Phase 2 — Replace hardcoded relations with `source()`

Every `database.schema.table` becomes `{{ source(...) }}`, with the source declared in YAML. This is behaviour-preserving in the environment you are in, and it is what makes everything else possible: dbt can now see the lineage, freshness checks become available, and — crucially — the model reads the right relation per environment instead of always reading production. `dbt-sources-and-seeds` owns the YAML mechanics.

Compile after this step and diff the compiled SQL. In the target environment it should differ only in the relation names being resolved rather than literal, which is a strong signal that nothing else moved.

### Phase 3 — Choose alongside, not in place

Two strategies, and the published recommendation is the second:

| Strategy | What it means | Cost |
|---|---|---|
| In place | Edit the ported file directly | You overwrite your own audit baseline, so equivalence becomes unprovable; and anything already pointing at the model is exposed to every intermediate state |
| **Alongside** | Copy the model and work on the copy; the original stays until the replacement is proven | Two files exist for a while, and bulk migrations get cluttered |

Alongside wins because it preserves the comparison, allows small reviewable pull requests instead of one large one, and lets you decide when the old model is ready to be deprecated rather than being forced by the merge.

### Phase 4 — Impose CTE structure, without changing logic

A four-part layout, applied mechanically:

```sql
with

-- import CTEs: one per source or ref, select * with a filter only if it was already there
orders as (
    select * from {{ source('<source_name>', 'orders') }}
),

customers as (
    select * from {{ ref('stg_customers') }}
),

-- logical CTEs: one per subquery, lifted out in dependency order, keeping its original alias
orders_per_customer as (
    ...
),

-- final CTE: what was the outermost select
final as (
    ...
)

select * from final
```

The mechanics: pull the innermost subquery out first and work outward, name each CTE exactly what the subquery's alias was, and change nothing else. Renaming CTEs to better names is a second, separate pass — the first pass's value is that a reviewer can match each CTE to the subquery it came from.

This phase should be provably zero-diff. Restructuring nested subqueries into sequential CTEs does not change the result set, so a non-zero comparison here means something was lifted out of order or a correlated reference was flattened incorrectly. Investigate; do not accept it.

### Phase 5 — Split into layers

Now the import CTEs tell you what the staging models should be: each one is a source, and the light transformations applicable to that source alone belong in its own staging model. The remaining logical CTEs either stay as CTEs or become intermediate models, by the criteria in `dbt-restructuring-dags`. This phase is DAG work and it follows that skill's ordering, coming back here for each equivalence proof.

### Phase 6 — Audit against the legacy output

The comparison that matters is against the *original system's* output, not against your phase-1 build, for as long as the legacy job still runs. Same window, same filters, row-level. `dbt-verification` owns the mechanics.

Two things that will show up and are not bugs in your work: the legacy job and your model may have run at different times over changing data — restrict both to a closed window; and the legacy query may itself have been wrong, in which case a diff is a finding about the old system and needs a decision, not a fix. Say which one you concluded and on what basis.

### Phase 7 — Deprecate the original

Only after the audit passes. The legacy job stops, the old relation is dropped, and if anything consumed it directly that is a breaking change with a window — see `dbt-breaking-changes`.

## Extracting a macro

A common refactor with a specific trap.

1. Identify the repeated SQL. **Two occurrences is usually not enough** — a macro adds indirection, and two call sites rarely repay it. Three or more, or a fragment that is genuinely difficult to get right, justifies it.
2. Write the macro so the *generated SQL is byte-identical* to what it replaces, at first. Resist improving the logic in the same step.
3. Replace one call site, compile, and diff the compiled output:

```bash
dbt compile --select <model>
# then diff target/compiled/... against the pre-refactor compiled SQL
```

Identical compiled SQL is the strongest possible evidence — it means the change provably cannot alter output. This is faster and stronger than a data comparison, so prefer it whenever the refactor is meant to be textual.

4. Only after all call sites are migrated and verified, improve the macro if needed — as a separate, separately-verified change.

## Linting and formatting

Formatting is a refactor with a zero-risk verification path: the compiled SQL should be semantically identical, and reformatting alone cannot change results.

**Read the contract's `sql_style` before reformatting anything.** This is the one place a refactor reliably goes wrong without touching logic at all: an agent "cleaning up" a model applies its own defaults for keyword case, `group by` style, the terminal CTE's name, and which join types are allowed — and produces a file that is stylistically correct in the abstract and inconsistent with every other model in the project. The result is a large diff, no behaviour change, and a reviewer who cannot tell the two apart.

| Contract field | What it decides |
|---|---|
| `sql_style.keyword_case` | Whether keywords are lowercased or uppercased |
| `sql_style.group_by_style` | `all`, ordinals, or explicit columns — and `all` is dialect-gated, so check `project.warehouse` before introducing it |
| `sql_style.final_cte_name` | The terminal CTE's name, which governs where `select *` is acceptable |
| `sql_style.allowed_join_types` | Whether a rewrite may introduce the join type it wants |

If the contract does not declare a field, **match the surrounding file** rather than picking a default. A refactor is not the place to establish a convention the project has not adopted — that is a separate, explicit decision.

Run the project's linter if it has one:

```bash
sqlfluff lint models/path/to/model.sql
sqlfluff fix models/path/to/model.sql
```

`sqlfluff` is the common choice, but it is not the only one — use whatever the repo configures (`.sqlfluff`, a pre-commit hook, a CI step) rather than introducing a tool the project has not adopted.

Two cautions:

- `sqlfluff fix` can alter Jinja-heavy SQL in ways that change behavior. Always compile after fixing, and diff the compiled output.
- Never bundle a reformat with a logic change in one commit. The diff becomes unreviewable, and reverting the logic reverts the formatting with it.

If the project has no linter config, do not impose one. Match the surrounding file's existing style.

## What makes this different from "just refactor it"

The steps above are cheap. The reason they get skipped is that a refactor *feels* safe — the engineer can see the logic is equivalent. But equivalence of intent is not equivalence of output, and the failure mode is a number that is wrong by a small amount in a report nobody re-checks for months.

Refuse to describe a refactor as complete without a comparison result. If a comparison was not possible, say which weaker evidence was used instead.

## Completion checklist

- [ ] Edit classified as behaviour-preserving or behaviour-changing, before starting
- [ ] Baseline captured **before** editing
- [ ] Only the refactor changed — no bundled renames, columns, config, or logic fixes
- [ ] Equivalence proven at rung 1 or 2 of the `dbt-verification` evidence ladder, and the rung named
- [ ] Zero differing rows, or a documented floating-point tolerance
- [ ] The **scope** of the comparison stated — window, volume, environment — not just its result
- [ ] For any edit on the behaviour-changing list: the edge case the two forms differ on named, and checked for in the data
- [ ] Compiled SQL diffed, for macro extraction and for any Jinja change
- [ ] For a reformat: `sql_style` read from the contract, and any field the contract does not declare matched from the surrounding file rather than defaulted
- [ ] For an incremental model: both branches compiled and read; both paths compared; a repeat run produces no duplicates
- [ ] Downstream models rebuilt if the model is referenced
- [ ] For a migration from outside dbt: hardcoded relations replaced with `source()`, and the audit run against the legacy output rather than against your own first build
- [ ] Comparison output included in the summary, not just a claim of success

## Common failure modes

1. **Refactoring before capturing the baseline.** Unrecoverable. Production is not a substitute, because it differs from your dev output for unrelated reasons, and every subsequent diff is unattributable.
2. **Accepting a small diff.** A refactor's criterion is zero. A diff means the behavior changed or it was never a refactor — investigate rather than rationalize.
3. **A cleanup that was a behaviour change.** `left join` plus a null filter becomes an `inner join`; `distinct` becomes a window function; a `case` is "simplified". The two forms differ on an edge case, today's data happens not to contain it, and the comparison passes.
4. **Bundling a reformat or rename with logic.** The diff becomes unreviewable, and reverting the logic reverts the formatting with it.
5. **Extracting a macro on two call sites.** The indirection costs more than the duplication saved. Three or more, or a fragment genuinely hard to get right.
6. **Improving the logic during extraction.** Two changes at once, so a diff has two possible causes and neither can be isolated. Make the generated SQL byte-identical first.
7. **Trusting `sqlfluff fix` on Jinja-heavy SQL.** It can alter behavior. Always compile after fixing and diff the compiled output.
8. **Imposing a linter the project has not adopted.** Match the surrounding file's style instead.
9. **A zero-diff on a filtered dev relation reported as full equivalence.** The comparison covered a slice; the claim covered everything.
10. **Comparing one branch of an incremental model.** Correct on a full refresh, wrong at the boundary — or the reverse. Both paths need exercising.
11. **Rewriting legacy SQL while porting it into dbt.** Migration and refactor bundled, so the audit against the legacy output has dozens of candidate causes and proves nothing.
12. **Reporting a refactor complete with no comparison result.** "The logic looks equivalent" is explicitly not evidence — it is the bottom of the evidence ladder.
