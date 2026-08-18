---
name: dbt-adding-columns
description: Use when adding a column to an existing model, propagating a new field from source to mart, exposing an existing source column downstream, adding a derived or calculated column, adding a column that requires a new join, adding a column to a model with an enforced contract, or adding a column to a snapshot. Covers layer propagation, fan-out risk, incremental backfill implications, and downstream BI impact.
metadata:
  phase: build
---

# Adding a column

The most common change in analytics engineering, and the one most often done incompletely. A column added to a mart but not to the staging model it reads from will compile, run, return null, and pass every test.

"Adding a column" also names several changes that are not the same change. A passthrough field is mechanical. A column that arrives through a new join can multiply every row in the model. A column added to a snapshot's `check_cols` alters what the snapshot records as history, and that history cannot be reconstructed later. Establish which change you are making before editing anything.

Two of the sections below are the ones people skip and should not: the **backfill decision** on an incremental model (section 9), because "the column is null for everything before today" is a decision whether or not anyone made it; and the **null and zero-denominator decisions** on a calculated column (section 5), because an unguarded division fails on the first row that reaches it, which may be a production run weeks after the change shipped.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | What it decides here |
|---|---|
| `layers` | Which models the column must traverse, and each layer's materialization |
| `naming.timestamp_column_suffix` | The suffix a timestamp or date column must carry |
| `naming.surrogate_key_column` | The key that may need the new column added to it |
| `testing` | Which tests the new column needs for its role |
| `sql_style.group_by_style` | The legal `group by` form if the column enters an aggregate |
| `sql_style.allowed_join_types` | Which join the project permits when the column arrives through one |
| `project.warehouse` | Whether a given `group by` form or contract `data_type` string is legal; whether a constraint is enforced or decorative; whether integer division truncates; whether an unguarded division errors or returns null |
| `project.dbt_version` | Whether contracts and the `on_schema_change` behaviours you are relying on exist |
| `bi.consumers` | Where to grep for a name collision or a `select *` that will pick the column up |
| `sensitivity.required_on_new_columns_in` | Whether the new column needs classification metadata before it may be added at all |

Without a contract, follow the generic sequence below and name the column consistently with its siblings in the same model.

## 1. Classify the change before editing

Seven kinds of column addition, in ascending order of what they can break. Read **every** row that matches and follow the sections it names — the sequence differs, not just the effort.

| Kind | The risk it carries | Section |
|---|---|---|
| Passthrough of a column already in the source YAML | A skipped layer, so null at the far end | 2–4 |
| Column present in the raw table but not in the source definition | Staging cannot reference it meaningfully | 2 |
| Calculated from columns already present | Wrong layer owns it; a stored ratio that cannot be re-aggregated; an unguarded null or zero denominator | 5 |
| Requires a new join | **Fan-out** — every row and every measure multiplies | 6 |
| Added to a model with an enforced contract | A hard build failure, and no ordering of two edits avoids it | 8 |
| Added to an incremental model | Silent no-op, or a column that is null for all history | 9 |
| Added to a snapshot | Irreversible change to what history records | 10 |

Two of these are not additive changes at all. A join that fans out and a column that enters a `group by` both change what a row means, which makes them breaking changes wearing the clothes of an addition — see `dbt-breaking-changes`.

More than one row can apply. A calculated column on a contracted incremental model is sections 5, 8, and 9 together, and the constraints interact: the contract forces the YAML and SQL into one edit, and `on_schema_change` is then constrained to `append_new_columns` or `fail`. Read every row that matches rather than the first.

## 2. Find where the column originates

```bash
# Is it already in staging?
grep -rn "<column_name>" models/staging/

# Is it declared in a source definition?
grep -rn "<column_name>" models/ --include=*.yml

# Does anything already expose it downstream?
grep -rln "<column_name>" models/
```

Four cases, and they need different work:

| Case | Work required |
|---|---|
| Column exists in source YAML and in staging | Propagate only |
| Column exists in source YAML, not in staging | Add to staging, then propagate |
| Column exists in the raw table, not in the source YAML | Add to the source definition first — below |
| Column does not exist in the raw table | Stop. It cannot be added until the upstream loader produces it |

For the last two, verify against the warehouse rather than assuming. Query the raw table directly — do not use `ref()`, which resolves per-environment and may read a different relation than you think.

### When the column is only in the raw table

A missing source-YAML entry does not stop `source()` from resolving: `source()` builds a relation name, and a `select` of an undeclared column from that relation succeeds as long as the warehouse has it. So the build works, and three things quietly do not:

- The column has no declared description, tests, or `data_type` anywhere, so nothing records what it is or asserts anything about it.
- Documentation and any tooling that reads the source's declared columns does not know it exists.
- The next person to read the source YAML concludes the column is not available.

Add it to the source definition in the same change, not afterward. `dbt-sources-and-seeds` owns the source-YAML mechanics — declared columns, types, and the `information_schema` query that confirms what the raw table actually holds. If the column is genuinely absent upstream, the change is blocked on the loader and saying so is the whole answer.

## 3. Determine the full path

The column must be added to **every model between its origin and its destination**. Skipping one produces a null column at the far end.

```bash
# What feeds the target model
grep -oE "ref\('[^']+'\)" models/path/to/target.sql | sort -u

# What consumes the target model
grep -rln "ref('<target_model>')" models/
```

Build the explicit list before editing anything, and edit **upstream first**. Editing the mart first leaves the project in a state where the mart references a column that does not yet exist.

One exception to upstream-first: a model with an enforced contract must have its SQL and its YAML changed together, in the same edit. Section 8.

## 4. Add it at each layer

**Staging** — this is where type casting belongs. Cast once, here, so every downstream model inherits a consistent type. Apply the contract's suffix conventions (for example a timestamp suffix such as `_utc`).

**Intermediate** — carry the column through. If the model aggregates, decide deliberately: is the new column a grain column (which changes the grain and therefore the surrogate key) or a measure (which needs an aggregate function)?

> A new column added to the `group by` of an aggregate model **changes its grain**. That is not "adding a column," it is changing what a row means, and it will silently change row counts. If the grain changes, stop and treat it as a breaking change — see `dbt-breaking-changes`.

If the column does enter the `group by`, write the grouping in the form the project already uses. `sql_style.group_by_style` names it, and the form is not universally legal: `group by all` requires `project.warehouse` to be `snowflake`, `bigquery`, `databricks`, or `duckdb`, and does not exist on `postgres`, `redshift`, or `trino`. On those adapters, list the grouping columns explicitly.

**Mart** — add to the select list and to any surrogate key that is supposed to include it.

## 5. A calculated column is a different change

A derived column — a ratio, a flag, a bucketing, a difference between two dates — raises two questions a passthrough does not.

**Which layer owns the calculation?** The answer follows the inputs, not convenience:

| The calculation | Belongs |
|---|---|
| Reshapes one column of one source (a cast, a trim, a rename) | Staging |
| Combines columns from more than one model | Intermediate |
| Is a presentation choice — a label, a threshold someone will want to move, a bucketing chosen for one chart | The consuming layer, or not stored at all |

Pushing a calculation upstream makes it a shared definition other models inherit whether or not they want it; pushing it downstream duplicates it the moment a second consumer needs the same number. Neither is free — state which cost you chose.

### The ownership test, in four questions

The table above answers the common cases. When it does not, these do, and they are worth asking explicitly because the wrong answer is expensive in a way that is not visible for months.

1. **What are the inputs, and what is the earliest model where all of them exist together?** The calculation cannot live upstream of that model. This is a hard floor, not a preference — if the inputs come from two sources, staging cannot own it.
2. **Would a second consumer need this exact number?** If yes, storing it once is what makes the definition shared. If no, storing it commits the project to maintaining a definition with one user.
3. **Is this a business rule or a display choice?** A business rule must be identical everywhere and belongs in the model. A threshold someone will want to move next quarter, a label, a rounding for a chart — those belong where they can be changed without a rebuild.
4. **Does adding it here change the model's grain or cardinality?** If the calculation requires a join or an aggregate, it is not a column addition. Go to section 6, or to `dbt-breaking-changes` for a grain change.

Two failure modes sit at opposite ends of this, and both are common:

- **Too far upstream.** A staging model gains a calculation that only one mart needed. Now every consumer of that staging model inherits it, the staging layer no longer maps one-to-one to the source, and changing the rule means rebuilding everything downstream. The tell is a business rule appearing in a model whose job was to rename and recast.
- **Too far downstream.** Each of four marts computes the same rule independently. Three of them get updated when the rule changes. Nobody notices the fourth, because it still produces a plausible number. The tell is the same expression appearing in models that do not reference each other.

**Should it be stored at all?** A calculated column a BI layer can compute from columns already present adds a maintained definition and a rebuild for every future adjustment. Store it when the calculation is a business rule that must be identical everywhere, or when the inputs are not all present in the model. Leave it to the consumer when it is a display choice.

### Passthrough versus calculated: what changes about the work

The two are not the same change and the checklist differs. Being explicit about which one you are making prevents applying the cheap procedure to the expensive case.

| | Passthrough | Calculated |
|---|---|---|
| Where the value comes from | Upstream, unchanged | An expression over columns present in the model |
| Layers to edit | Every model between origin and destination | Only the model that owns the calculation, plus any layer below it that must carry the result |
| Type decision | Cast once, at staging | Determined by the expression; check what the warehouse infers rather than assuming |
| Null behaviour | Inherited from the source | **A decision you are making**, and it must be recorded |
| Test that means something | `not_null` where the source guarantees it; `accepted_values` for an enum | A test that asserts the *rule*, not the column's existence — a range, a relationship between the inputs and the output, or a singular test |
| Most common defect | A skipped layer, so null at the far end | An unhandled null or zero denominator, discovered on the first row that reaches it |

**A ratio or percentage is the case with a hard rule.** Storing a computed rate prevents correct re-aggregation: a rate averaged across rows weights every row equally regardless of its denominator. Store the **numerator and the denominator** as their own columns, so any consumer can divide the sums at its own grain.

```sql
-- store both inputs
sum(<numerator_column>)   as <numerator_column>,
sum(<denominator_column>) as <denominator_column>
```

Why this is a rule rather than a preference: a rate is **non-additive**. Two rows with rates of 50% and 100% do not average to 75% unless their denominators are equal, and no consumer downstream can recover the denominators from the stored rate. The information needed to re-aggregate correctly is destroyed at the moment the division happens. Storing the inputs preserves it; storing the output does not.

The failure this prevents is specific and quiet: a report groups the model at a coarser grain than the model's own, averages the stored rate, and produces a number that is confidently wrong by an amount proportional to how uneven the denominators are. Nothing errors. Nothing is null. The total simply disagrees with the correctly computed one, and usually by a plausible-looking margin.

Storing the rate *as well* is acceptable at the model's own grain, where it is correct — and if you do, say in the YAML description that it is valid only at that grain and must not be averaged. `dbt-designing-a-model` owns the additivity classification; classify the new column there rather than deciding by feel, and carry the aggregation rule into its YAML description so a consumer does not have to infer it.

### Null and zero-denominator decisions

A calculated column needs an explicit decision on every input that can be null, and division needs one more. These are not edge cases; they are the normal content of production data.

| Situation | The options | What to say |
|---|---|---|
| An input is null | Propagate null, or `coalesce` to a default | Which one, and why. `coalesce(x, 0)` on a measure that is genuinely unknown converts "no data" into "zero", which is a different claim and will be summed as though it were measured |
| Denominator is zero | Null result, or zero, or fail | Null is usually right: the rate is undefined, not zero. Returning zero asserts something false |
| Denominator is null | Usually the same treatment as zero | State it, because the expression that guards one often does not guard the other |
| Both numerator and denominator are zero | Almost always null | `0/0` is undefined; a stored zero here is indistinguishable from a real zero rate |

```sql
-- the guard has to cover null as well as zero
case
    when coalesce(<denominator_column>, 0) = 0 then null
    else <numerator_column> / <denominator_column>
end as <rate_column>
```

Two platform notes. Integer division truncates on some engines and returns a fraction on others, so a rate computed from two integer columns can silently come back as 0 — cast one side. And an unguarded division behaves differently by platform: some raise a division-by-zero error, some return null. **A runtime error only appears on the first row that reaches a zero denominator**, which may be weeks after the change shipped and in a production run rather than your dev build. Read `project.warehouse` before asserting which behaviour applies, and guard regardless.

## 6. A column that requires a new join

This is not a column addition. It is a join addition that happens to deliver a column, and the risk lives entirely in the join.

**If the joined relation is not unique on the join key, the row count multiplies.** Every fact row matching two rows on the right becomes two rows, every measure in the model doubles, and nothing fails: each individual row is valid, and a uniqueness test on a surrogate key that includes the new column will pass because the duplicated rows differ.

### Pre-flight: prove the right side is unique on the join key

Run this **before** writing the join, against the relation you intend to join to, with an explicit database and schema:

```sql
select
    <join_key>,
    count(*) as occurrences
from <database>.<schema>.<joined_relation>
group by <join_key>
having count(*) > 1
```

Zero rows, or the join will fan out. There are only three honest responses to a non-empty result:

| Result | Response |
|---|---|
| Zero rows | Proceed. The join is safe on this key today |
| Duplicates, and the relation should be unique | **Fix the relation at its own layer.** Do not patch it at the join |
| Duplicates that are legitimate (a versioned or multi-valued relation) | The join needs a further predicate that selects one row — a validity window, a rank, a filter — and that predicate is a modelling decision to state, not to improvise |

Deduplicating inside the join with `distinct` or a window function hides a broken relation that every other consumer is also joining to. Fix it where the key is supposed to be unique, and add a uniqueness test there so the next occurrence fails loudly.

The legitimate-duplicates row deserves its own treatment, because it is the case people improvise. A relation with more than one row per key is usually one of a small number of shapes, and each has a correct predicate:

| Shape | The predicate that selects one row | What to state |
|---|---|---|
| Slowly-changing dimension with validity windows | Join on the key **and** the fact's event timestamp falling inside the validity window | Which timestamp you used, because "current" and "as of the event" give different answers and both are defensible |
| Versioned rows with a current flag | The flag, plus a check that exactly one row per key carries it | That you verified the flag is unique per key — a "current" flag is frequently not |
| Multiple rows by design (an entity with several tags, addresses, categories) | **None.** Selecting one row silently discards data | That the attribute is multi-valued, and either aggregate it into one value per key or accept the fan-out deliberately and change the model's grain per `dbt-breaking-changes` |
| Duplicates from an upstream load defect | None — fix upstream | That you found a data-quality problem and where; see `dbt-data-quality-triage` |

Picking the newest row with a window function is the improvisation to be most suspicious of. It is correct when the rows are versions of one fact and you want the latest; it is wrong when they are different facts, and it produces a plausible result either way.

Two more properties to settle before writing the join:

- **Use a `left join` unless dropping unmatched rows is the intent.** An `inner join` added to fetch one attribute silently deletes every row with no match, which reads in the diff as a one-line addition. Check `sql_style.allowed_join_types` for what the project permits.
- **A null in the new column after a `left join` is data, not a bug.** Decide whether unmatched means null or a default, and say which.

### The rule: row count and one measure must both be unchanged

**The row count must be identical before and after.** Capture it before the change and compare after:

```sql
select count(*)                       as row_count,
       count(distinct <key_column>)   as distinct_keys,
       sum(<a_measure_column>)        as measure_total
from <database>.<schema>.<model_name>
```

Run this against the built relation before the change and after, with an explicit database and schema. Three outcomes and only one of them is a pass:

| Observation | Meaning |
|---|---|
| All three unchanged | The addition was additive. This is the pass |
| Row count up | The join fanned out. This is a grain change, not a column addition |
| Row count down | The join was `inner` and dropped unmatched rows |
| Row count unchanged, measure total moved | Cardinality held and values changed — which a column addition should not do. Something else changed too |

**Row count alone is not sufficient**, which is why the measure is in the same query. Two ways a matching count hides a fan-out: a `distinct` or a `group by` somewhere in the same model can collapse duplicated rows back to the original count while the intermediate aggregation was already wrong; and a fan-out on one key can coincide with dropped rows on another, netting to zero. The measure total catches both, and it costs nothing extra to select.

Any difference goes back through `dbt-breaking-changes` rather than forward to a pull request. See `dbt-verification` for what counts as evidence.

### Also check the models downstream

A join that preserves the row count of the model you edited can still change a downstream model's cardinality, if that model joins to yours on something other than your key — for example on the newly added column. Run the same three-value query on each direct consumer, before and after. `dbt build --select <model>+` proves the models still build; it does not prove their row counts held.

## 7. Update schema YAML at every layer

A column without a description is a column the next engineer has to reverse-engineer. Add it wherever the model has a YAML entry, with the tests the contract's `testing` policy expects for that column's role. `dbt-authoring-schema-yaml` owns description and test authoring; the rule specific to a column addition is that the entry goes in at **every** layer the column now reaches, not only at the destination. A column documented in the mart and undocumented in staging tells the next reader that staging does not have it.

If the column came from a sensitive source, its classification metadata must propagate with it — see `dbt-handling-sensitive-data`. Copying a YAML entry without its `meta` is how classification is lost, and it happens in exactly this kind of mechanical edit.

## 8. If the model has an enforced contract

Check before editing. This is a hard build failure, not a subtlety:

```bash
grep -rn -A3 "contract:" models/ --include=*.yml
```

A model with `contract: {enforced: true}` has its output schema verified at build time against the `columns:` list in its YAML. dbt compares the two and **fails before writing any data** if they disagree — in either direction:

| The disagreement | What the build reports, conceptually |
|---|---|
| Column added to the SQL, absent from `columns:` | The model produced a column the contract does not declare |
| Column added to `columns:`, absent from the SQL | The contract declares a column the model did not produce |
| Declared with a type the SQL does not produce | A type mismatch on that column, named explicitly |

So **there is no ordering of two separate edits that works.** SQL first fails on the undeclared column; YAML first fails on the missing one. The YAML entry and the `select` must change together, in one edit, and be compiled together before anything else proceeds. This is the one place the upstream-first rule in section 3 needs qualifying: order the *models* upstream first, and inside a contracted model change both files at once.

The contract entry needs a `data_type`, and **type strings are dialect-specific.** `varchar`, `string`, `text`, `numeric`, `decimal(38,6)`, and the various timestamp spellings are not uniformly available across adapters. Read `project.warehouse` and use what that adapter accepts; with the field absent, copy the spelling used by the sibling columns in the same contract rather than guessing a dialect.

Two consequences worth stating in the change summary: the failure is loud and early, which is what the contract is for — a contracted model that fails on your column addition is the contract working, and `enforced: true` must never be removed to make a build pass. And if the model is versioned, adding a column may warrant a new version rather than an edit to the current one; additive changes are usually compatible for consumers, but that decision belongs with the consumers of the interface — see `dbt-breaking-changes`.

### Additive is the non-breaking direction, and that has a cost

dbt classifies adding a column to a contracted model as **not** a breaking change, so a new version is not required and CI's state comparison will not object. Removing a column or changing its type is breaking; adding one is not.

That asymmetry has a predictable consequence worth naming when you add a column to a long-lived contracted model: because every addition is free and every removal is not, the interface accumulates columns nobody reads. dbt's own recommendation is not to fight this per-change but to bump the version on a **predictable cadence, announced in advance, and remove the dead columns then**. If the model you are widening already carries columns that look abandoned, that is a finding to raise — not a thing to fix in the same pull request.

### Where contracts are not available at all

Check the materialization before promising the contract will catch anything:

| Materialization | Contract support |
|---|---|
| `table`, `incremental` | Full — column names, types, and platform-supported constraints |
| `view` | Names and types only. **`constraints` are not applied** |
| `ephemeral`, `materialized_view` | **Not supported** |
| Python models | **Not supported** |
| Models using recursive CTEs on BigQuery | Not supported |

Contracts also do not exist for sources, seeds, or snapshots — those are model-only features. And `constraints` themselves are unevenly enforced: `not_null` is enforced on most platforms, while `primary_key`, `unique`, and `foreign_key` are commonly *definable but not enforced* on cloud warehouses, where they are metadata. **A declared-but-unenforced `primary_key` will not stop duplicate rows**, so if the new column enters a key, add a uniqueness test as well rather than relying on the constraint.

If the contracted model is also incremental, set `on_schema_change` explicitly (section 9) rather than assuming the contract covers the target relation's shape. The contract governs what the model's SQL produces; `on_schema_change` governs what happens to the existing relation. This is not merely good practice — **a contracted incremental model is required to use `append_new_columns` or `fail`**, and the reason is exactly the trap in section 9: with `ignore`, dbt does not add the column to the existing relation while the merge still succeeds against the pre-existing destination columns, leaving the relation's real shape different from its own declared contract. The contract is then false and nothing errored. `sync_all_columns` is excluded because dropping a column is a breaking change to a contracted model.

## 9. Handle the incremental case

This is the step most often missed.

**Adding a column to an incremental model does not populate it for existing rows.** New rows get the value; historical rows are null. The column is added to the table by dbt's schema-change handling, but nothing rewrites history.

Check the model's config — this single setting decides whether your change lands, errors, or silently disappears:

| Setting | What happens to your new column |
|---|---|
| `fail` | The run **errors** on the schema change. Loudest, and usually the right default — it forces an explicit decision about backfill. |
| `append_new_columns` | Column is added; existing rows are null. |
| `sync_all_columns` | Column is added (and removed columns dropped); existing rows still null. |
| `ignore` | **The column never appears.** The build succeeds, downstream reads null or errors on a missing column, and nothing signals a problem. |

`ignore` is the default when the config is unset. That default is the trap: an unconfigured incremental model will accept your edit, run green, and not add the column.

Experienced teams tend to set `fail` explicitly rather than leave the default: it forces the schema-change decision to be made by a person, and teams that have been burned by a silently-missing column choose to be told. If the model you are editing has no `on_schema_change`, raise that as a finding rather than silently relying on the default.

### The backfill decision, made explicitly

Adding the column is step one. Deciding what its historical values are is step two, and omitting step two is how a column ships that is correct going forward and null for everything before the deploy — with nothing recording which date the boundary is.

Four options, and the decision belongs in the PR:

| Option | When it is right | What to state |
|---|---|---|
| Full refresh | The source can reproduce all history, and the rebuild fits the available window | The runtime and cost, and that downstream incrementals may need the same |
| Targeted backfill by period | History is reproducible but a full rebuild is too expensive or too long | The period range, the batch size, and that the boundary rows were checked. See `dbt-incremental-models` |
| Leave history null, deliberately | The value genuinely did not exist before now, or the column is only meaningful going forward | **The date from which the column is populated**, in the YAML description. Otherwise every future consumer has to rediscover it |
| Backfill from a different source | The current source cannot reproduce history but another relation can | Which relation, and whether its definition of the column matches the new one — it usually differs subtly |

The third option is legitimate and under-used, but only if it is documented. An undocumented null history is indistinguishable from a bug, and someone will eventually "fix" it by full-refreshing a model whose history cannot be reproduced.

If the model is `full_refresh=false`, that is deliberate — the source cannot reproduce history — and a full refresh is not available to you at all. Do not propose one; use the backfill guidance in `dbt-incremental-models`. See also `dbt-shipping-changes` for writing the required post-merge action into the PR.

Two mechanical notes that catch people:

- **The new column must be in the incremental branch's `select` too**, not only in the full-refresh branch. Compile and read `target/compiled/` for the branch you are actually running; a column added inside an `{% if is_incremental() %}` guard, or outside one when it should be inside, produces a mismatch that `on_schema_change` will not explain.
- **`append_new_columns` adds the column but does not populate it in the same run.** The first incremental run after the change writes values only for the rows it processes. Every row already in the relation stays null until a backfill.

Say explicitly which of these applies before claiming the change is complete.

## 10. Adding a column to a snapshot

Snapshots are the one place a column addition can damage an asset that cannot be rebuilt. Read `dbt-snapshots` before making the change — this section covers only what is specific to adding a column.

**No addition is retroactive.** Existing versions were written without the column and keep null for it. Whatever the column's value was during the periods already recorded, that history does not exist and cannot be recovered from the snapshot. A full refresh does not fix it either: on a snapshot, `--full-refresh` drops the table and rebuilds one version per record from current source state, destroying all prior history.

What happens next depends on the strategy:

| Strategy | Effect of adding the column to the snapshot query |
|---|---|
| `timestamp` | **Inert for change detection.** Versions are still triggered only by `updated_at`. The column starts being captured on the next version of each row, and stays null on every existing version |
| `check`, column **not** added to `check_cols` | Captured but untracked. Its value is whatever it was when a tracked column last changed, and later drift in it produces no version |
| `check`, column added to `check_cols` | **Changes what counts as a change**, with an immediate side effect below |

Adding a column to `check_cols` widens the comparison dbt makes between the source row and the stored current version. The stored version has null for a column that did not exist when it was written, and dbt's check comparison treats a null-versus-non-null difference as a change. So on the next run, **every row whose new column is non-null registers as changed and gets a new version**, timestamped to that run, recording a business change that did not happen.

That burst is a permanent artifact of the deployment, not a transient one. Anyone later reading the snapshot sees a mass change on that date. Before the change:

- **Say the burst will happen**, and give the run date, so the artifact is documented rather than discovered during an audit.
- **Back the snapshot table up to a separate relation first.** It is the only recovery path, and there is no undo.
- **Confirm the column is worth tracking**, because `check_cols` is close to irreversible in the other direction too: removing it later does not remove the versions it created.
- **Exclude anything the pipeline touches** — a load timestamp, a row hash, a batch identifier — or every load produces a version and the snapshot becomes a log of pipeline runs.

**The snapshot table does gain the column.** dbt reconciles a changed source query against the destination table by creating the new columns there, and by widening string types where an adapter needs it (for example `varchar` on Redshift). So the run does not fail on the schema difference — which means the risk here is never a loud error, it is the silent version burst above.

Two asymmetries in that reconciliation are worth knowing before you rely on it. dbt will **not** drop a column from the snapshot table when you remove it from the query, so a removal leaves a permanently null column behind. And it will **not** change a column's type beyond widening a string — if you change a column from a string to a date in the snapshot query, the destination keeps the old type. Neither is reversible on historical rows.

## 11. Verify

Do not assert the column works. Show it.

```bash
dbt compile --select <model_name>          # compiles
dbt build --select <model_name>+           # model and everything downstream
```

Then query the built relation with an explicit database and schema (never `ref()`) and confirm:

- the column exists with the expected type
- it is not null where it should not be — report the null rate as a number, not as "looks fine"
- **row count, distinct key count, and one measure total are all unchanged** — if any moved, you altered more than the column list
- for a new join, the same three values on each direct consumer as well
- for a calculated column, spot-check the arithmetic against the inputs on a handful of rows rather than trusting the expression, and check the rows where an input is null or a denominator is zero specifically — those are the rows the expression was written to handle and the ones a random sample will miss

```sql
select count(*)                                        as row_count,
       count(distinct <key_column>)                    as distinct_keys,
       sum(<a_measure_column>)                         as measure_total,
       count(*) - count(<new_column>)                  as new_column_nulls
from <database>.<schema>.<model_name>
```

For an incremental model, add one more check that nothing else in this document covers: **run it twice.** A column addition that altered the merge condition produces duplicates or overwrites on the second run, not the first, and the row-count comparison above will pass on the first.

See `dbt-verification` for what counts as evidence.

## 12. Check downstream consumers

A new column rarely breaks BI, but `select *` in a downstream view or a BI tool that materializes a schema can be surprised by it. A name that collides with an existing field in the BI layer is the more common problem, and it is invisible from inside the repository.

```bash
# BI repos from the contract's bi.consumers
grep -rn "<target_model>" <bi_repo_path>/
grep -rn "<column_name>" <bi_repo_path>/
```

With `bi.consumers` absent, say that BI exposure was not verified rather than that there is none.

## Completion checklist

- [ ] Change classified against **every** row that applies — passthrough, source-YAML gap, calculated, join-requiring, contracted, incremental, snapshot
- [ ] Column traced to its true origin, verified in the warehouse
- [ ] Source definition updated if the column was only in the raw table
- [ ] Added at every layer in the path, upstream first
- [ ] Cast once, at staging
- [ ] Naming follows the contract
- [ ] Calculated column: owning layer chosen against the four ownership questions, and the choice stated
- [ ] Ratio or percentage: numerator and denominator stored, not only the computed rate
- [ ] Null, zero-denominator **and** null-denominator behavior decided and guarded, not left to the platform
- [ ] Integer division checked for truncation on this warehouse
- [ ] New join: right side proven unique on the join key **before** the join was written
- [ ] New join: duplicates fixed at the relation's own layer, not patched at the join; where duplicates are legitimate, the selecting predicate named as a modelling decision
- [ ] Join type chosen deliberately; unmatched-row behavior stated
- [ ] `group by` form legal for `project.warehouse`
- [ ] Schema YAML updated at every layer, with tests that assert the rule and not merely the column's existence
- [ ] Classification metadata carried along if the column came from a sensitive source
- [ ] Enforced contract: `columns:` entry and `select` changed in the same edit, with a valid `data_type`
- [ ] Contract support confirmed for the materialization, and constraint enforcement confirmed for the platform
- [ ] Incremental `on_schema_change` behavior identified and stated; `append_new_columns` or `fail` on a contracted incremental model
- [ ] New column confirmed present in the branch actually being run, by reading the compiled SQL
- [ ] Backfill decision made explicitly from the four options — and a deliberately-null history documented with its start date
- [ ] Snapshot: strategy effect stated; `check_cols` version burst disclosed; relation backed up first
- [ ] Compiled and built, downstream included
- [ ] Row count, distinct key count, and one measure total confirmed unchanged — all three, in one query, before and after
- [ ] Direct consumers' row counts checked too, not only that they still build
- [ ] BI consumers grepped for the model and for a colliding column name, or non-verification stated

## The failure modes to watch for

1. **Null at the far end** — a layer was skipped. Grep the column name across `models/` and confirm it appears at every step.
2. **Silent no-op on an incremental model** — `on_schema_change` was unset or `ignore`. The build succeeds and the column never appears.
3. **A column that is null for all history and undocumented** — the backfill decision was made by omission. Nothing records the date from which the column is meaningful, so every future consumer rediscovers it as a bug.
4. **Grain change disguised as a column addition** — the column entered a `group by`. Row counts move, downstream aggregates change, and no test fails because no test asserts the grain.
5. **Fan-out from a new join** — the joined relation was not unique on the key. Every row and every measure multiplies, each individual row is valid, and a surrogate-key uniqueness test passes because the duplicates differ. The most expensive failure in this document.
6. **Fan-out masked by a matching row count** — a `distinct` or `group by` later in the same model collapsed the duplicates back, so the count held while the intermediate aggregation was already wrong. This is why the measure total belongs in the same query as the count.
7. **A window function used to pick "the newest row"** where the rows were different facts rather than versions of one. Plausible output, silently discarded data.
8. **An `inner join` added to fetch an attribute** — rows with no match are deleted. A row-count drop from a change that reads as purely additive.
9. **A stored ratio with no numerator and denominator** — correct at the model's grain and wrong at every other one, because averaging a rate ignores the denominators. Nothing errors and the number is confidently wrong.
10. **An unguarded division** — it fails on the first row that reaches a zero denominator, which may be a production run weeks later; or, on a platform that returns null instead, it never fails and the null is read as "no data".
11. **`coalesce(<measure>, 0)` on an unknown value** — "not measured" becomes "measured as zero", and every downstream sum treats it as a real observation.
12. **A calculation pushed too far upstream** — a staging model now carries a business rule only one mart needed, every other consumer inherits it, and changing the rule rebuilds the world.
13. **The same calculation duplicated across marts** — the rule changes in three of the four. The fourth keeps producing a plausible number, and nothing connects them.
14. **Enforced contract edited on one side only** — the build fails on an undeclared or an undelivered column. Recoverable, but only if the contract is honoured rather than disabled to get past it.
15. **Relying on a `primary_key` or `unique` constraint the platform does not enforce** — on most cloud warehouses the declaration is metadata. Duplicates arrive anyway, and the test that would have caught them was dropped as redundant.
16. **A column added to `check_cols`** — a burst of versions dated to the deployment, recording a change that never happened, permanently. Undocumented, it reads to a future auditor as a real mass change.
17. **A column selected from a raw table that the source YAML does not declare** — it works, and the column has no description, no tests, and no visibility to anyone reading the source definition.
