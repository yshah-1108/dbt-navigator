---
name: dbt-breaking-changes
description: Use when renaming a model or column, removing a column, changing a column's type or grain, deleting a model, or changing a model's contract, access or version. Covers the blast-radius check that must come first, the expand/migrate/contract pattern, deprecation windows and shims, BI and cross-project consumer impact, and why a rename must never be bundled with a logic change.
metadata:
  phase: ship
---

# Breaking changes

A breaking change is any change that can make a *consumer* wrong — a downstream model, a test, a BI report, or a query someone runs by hand. The defining property is that the failure surfaces somewhere you are not looking.

Some breaking changes fail loudly. A downstream model referencing a removed column will not compile, and you will know immediately. The dangerous ones fail quietly: a BI report continues to render, showing a number that is now wrong, and nobody re-checks it for months.

**The blast-radius check comes first, before any edit.** Not after the change is written, not as part of the PR. The blast radius decides whether the change is a one-line edit or a multi-week coordinated migration, and you cannot know which you are doing until you have looked.

Two sub-documents hold the depth:

- [blast-radius.md](blast-radius.md) — the full consumer-discovery procedure: graph versus grep, cross-project and `access: public` consumers, BI repositories, warehouse query logs per platform, and what each method structurally cannot see.
- [governance-mechanisms.md](governance-mechanisms.md) — contracts, `access` and `groups`, `versions`, and `deprecation_date` in depth, with the version and platform dependencies of each.

## Expand, migrate, contract — the pattern behind every safe change

Every ordering in this document is one application of a single pattern from software and database practice, known as **parallel change** or **expand/contract**. A backward-incompatible change is split into three phases that ship separately:

1. **Expand** — add the new thing. The old thing is untouched, so nothing can break. Shippable on its own.
2. **Migrate** — move consumers to the new thing, one at a time, each verified. Both shapes exist during this window, deliberately.
3. **Contract** — remove the old thing, only after evidence that nothing reads it.

| Phase | In a dbt project |
|---|---|
| Expand | New column alongside the old; new model alongside the old; a shim aliasing the old name to the new; a new model version |
| Migrate | Repoint dbt consumers; ship the BI change; notify external consumers; let the deprecation window run |
| Contract | Drop the shim, delete the old model, drop the abandoned relation |

Three properties are what make it worth the extra ships:

- **Every phase is independently deployable and independently revertible.** A problem in the migrate phase is fixed by reverting one consumer, not by unwinding a schema change.
- **There is no interval in which any consumer is broken.** A single-commit rename has one, however short: between the deploy and the consumer's next release.
- **The old path is the rollback.** This is the reason the contract phase must be a separate change. Deleting the old column in the same pull request that moves reads to the new one buys tidiness and spends the only recovery route you had.

The failure mode of the pattern is real and worth naming: **an expand phase with no contract phase leaves the project worse than before it started** — two ways to express the same thing, and no way for the next reader to tell which is current. That is why every shim in this document carries a date and a tracked task. Discipline in phase three is what separates this from accumulating debt.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | What it decides |
|---|---|
| `bi.consumers` | Which BI repositories to grep, their local paths, and whether each is primary, legacy, or deprecated |
| `bi.use_exposures` | Whether BI dependencies are modelled as exposures and therefore visible in the DAG |
| `project.query_history_relation` | Where to look for consumers that exist in no repository |
| `project.query_history_retention_days` | The longest window any query-log claim may cover |
| `project.dbt_version` | Whether contracts, versions, `deprecation_date` and per-warning error promotion are available at all |
| `project.warehouse` | Which constraints are enforced rather than decorative, and which query-log facility exists |
| `naming.banned_prefixes` | Prefixes a replacement model must not use |
| `naming` | The name of any replacement model |
| `naming.yaml_file_pattern` | What the YAML file must be renamed to when a model is renamed |
| `layers[].terminal` | Whether the model should have dbt consumers at all — a terminal model with none is not evidence of disuse |

Without `bi.consumers`, **state plainly that the project has not declared its BI consumers, so BI impact could not be checked**, and ask which tools read from the warehouse. Do not conclude "no BI impact" from an unpopulated contract — absence of a declaration is not absence of consumers, and that inference is how a dashboard breaks.

---

## Step 1 — Blast radius, before editing anything

The full procedure, including what each method cannot see, is in [blast-radius.md](blast-radius.md). Read it before deleting a relation, renaming anything a person may have typed by hand, or changing a grain. The condensed version:

```bash
# dbt consumers: grep finds the line to edit, the graph proves the set is complete
grep -rn "ref('<model>')" models/ tests/ macros/ analyses/ snapshots/
grep -rn 'ref("<model>")' models/ tests/ macros/ analyses/ snapshots/
dbt list --select <model>+
dbt list --select <model>+ --resource-type test
dbt list --select <model>+ --resource-type exposure

# for a column: every file that mentions the name
grep -rn "<column_name>" models/ tests/ macros/ --include="*.sql" --include="*.yml"

# is this model declared as an interface to consumers you cannot see?
dbt list --select "access:public" --output name
```

Run **both** the grep and the graph query. The grep cannot see a `ref()` built from a variable inside a macro, or a versioned `ref('<model>', v=2)`; the graph is built from the parsed manifest and can. The graph, in turn, does not tell you which file and line to edit.

The column grep is noisy — a common name matches unrelated models — and that is the correct trade-off. A false positive costs a few seconds of reading; a false negative ships a break.

Four classes of consumer, in ascending order of how badly each is served by the tools available:

| Class | Best available check | What it cannot see |
|---|---|---|
| dbt nodes in this project | `dbt list --select <model>+` plus greps | Nothing significant, if both are run |
| Other dbt projects | `access: public` on the model; the platform's lineage service | The set of dependent projects; your manifest does not contain it |
| BI tools | Grep each `repo_path` in `bi.consumers` for the model name, the **physical relation name**, and the column | Anything a user built in the tool's UI rather than in code |
| Ad-hoc queries, notebooks, exports, reverse ETL | Warehouse query log, within its retention window | Everything beyond retention, and — with a text-searchable log only — any consumption that goes through a view |

The physical relation name is the one that matters in a BI repository and the one most often skipped: a model with `alias` set, or a versioned model, has a warehouse name that is not its dbt name. Grepping only the dbt name returns clean and means nothing.

Without `bi.consumers`, **state plainly that the project has not declared its BI consumers, so BI impact could not be checked**, and ask which tools read from the warehouse. Do not conclude "no BI impact" from an unpopulated contract — absence of a declaration is not absence of consumers, and that inference is how a dashboard breaks.

BI-layer risk by change type:

| Change | Typical BI risk |
|---|---|
| Add a column | Low — additive. Watch for a collision with a field the BI layer already defines |
| Rename a column | High — every reference breaks or silently drops |
| Remove a column | High — same, plus derived fields built on it |
| Change a type | Medium to high — comparisons, formatting and sort order can change without erroring |
| Change grain | **High and quiet** — reports keep rendering, aggregates are wrong |
| Rename a model | High where the relation name is hardcoded; lower where a semantic layer indirects it |

The grain row is the one to take seriously. Every other row eventually produces an error someone notices. A grain change produces plausible numbers.

### Classify before proceeding

| Finding | Risk | Required approach |
|---|---|---|
| No consumers anywhere, query history checked over an adequate window | Low | Direct change |
| dbt consumers only, all in this project | Medium | Update consumers in the same change, upstream-first ordering |
| BI consumers found | High | Expand/migrate/contract with a dated window, coordinated with the BI change |
| `access: public`, cross-project, or unknown consumers | Critical | Versioned or deprecated interface; notify before the window opens |
| Any of the checks above was not possible | **One tier higher than what you found** | Name the missing check and say the risk was rounded up |

That last row is the one that gets skipped. An unavailable check does not lower the risk of the change; it lowers your knowledge of it.

---

## Step 2 — Prefer deprecation to deletion

Deletion is instant and irreversible from the consumer's point of view. Deprecation converts a break into a scheduled migration, and it costs one extra release cycle.

The mechanics of `deprecation_date`, contracts, `access`, `groups`, and `versions` — including which dbt version each requires and what each one does *not* protect — are in [governance-mechanisms.md](governance-mechanisms.md). Read it before recommending any of them, and read it before *introducing* any of them: governance features raise the permanent maintenance cost of a model in exchange for safety at one moment, and adopting one mid-migration is usually the wrong trade.

### `deprecation_date`

```yaml
models:
  - name: <old_model>
    deprecation_date: 2026-06-30
    description: "Deprecated. Replaced by <new_model>. Removed after 2026-06-30."
```

Available from dbt Core 1.6. It makes dbt warn on every `ref()` to the model, which turns a silent dependency into a visible one at parse time, and those warnings can be promoted to errors by name once the window closes. Four things it does **not** do, each of which has caught someone:

1. It does not stop the model being built — a deprecated model keeps running and keeps costing.
2. It does not drop the relation. The warehouse object survives the model.
3. Non-dbt consumers see nothing. It is a message to the dbt project, not to the warehouse.
4. On a contracted or versioned model it binds *you*: dbt refuses to delete such a model before its date.

### Column shims

A removed or renamed column can be kept alive for one window with an alias:

```sql
select
    <new_column_name>,
    <new_column_name> as <old_column_name>  -- shim: remove after <date>
from <upstream>
```

For a removal with no replacement, a typed null holds the shape:

```sql
cast(null as varchar) as <old_column_name>  -- shim: remove after <date>
```

Every shim needs a removal date in the comment and a tracked task. A shim without an expiry is not a migration aid, it is permanent debt that the next engineer cannot tell apart from real logic.

Two properties of a shim column worth stating in the PR, because a reviewer will not infer them: it is **not** covered by any test unless you write one, so nothing detects it silently going null; and on a **contracted** model it must appear in `columns:` with a `data_type` like any other column, which means the shim is part of the declared interface and removing it later is a contract-breaking change on its own schedule.

### Model shims

To rename a model while keeping the physical relation name stable for BI, an alias decouples the two:

```yaml
models:
  - name: <new_model_name>
    config:
      alias: <old_relation_name>
```

dbt consumers use the new model name; the warehouse object keeps the old name, so BI keeps working. Rename the physical object in a later, separate change coordinated with the BI update. This is usually the cheapest path when BI hardcodes relation names.

Alternatively, a passthrough view under the old name:

```sql
-- <old_model>.sql — passthrough during migration, delete after <date>
select * from {{ ref('<new_model>') }}
```

This is the one place `select *` is appropriate: the point is to be a faithful mirror. Set a `deprecation_date` on it so the shim itself is on a clock. Two costs to weigh: it is an extra node that builds on every run, and if it is materialized as a `table` rather than a `view` it is also a full copy of the data with its own refresh lag — which turns a naming shim into a staleness bug. Make it a view.

### Versioning

Where a model has an enforced contract or consumers outside your control, dbt model versions let old and new shapes coexist, with consumers pinning a version and migrating on their own schedule. This is the heaviest option and it is only worth it when consumers are genuinely outside your control.

One mechanical detail that surprises people and matters here: **a versioned model's default relation name gains a version suffix**, so making an existing model versioned changes its physical name unless you set `alias` on the version that existing consumers read. Details, plus the choice between a version and simply building a new model, are in [governance-mechanisms.md](governance-mechanisms.md).

If the project does not already use versions, do not introduce them for a single rename.

---

## Step 3 — Ordering, per change type

Every ordering below is the expand/migrate/contract pattern applied to one kind of change. If you find yourself compressing the phases, the question to answer first is which consumer class you have proven does not exist.

### Rename a column

1. **Expand.** Add the new name **alongside** the old one, as a shim. Ship it. Nothing breaks.
2. **Migrate dbt consumers** to the new name. Compile, build, verify.
3. **Migrate BI consumers** — a separate change in the BI repository, coordinated in time.
4. **Contract.** Remove the shim in a third change, after confirming nothing still reads the old name.

Three ships instead of one, and no window where any consumer is broken. Where BI is not involved and consumers are few, steps 1 and 2 can collapse into one change — but only after the blast radius has shown that BI is genuinely uninvolved.

Before the contract phase, the evidence to require is boring and specific: no dbt reference to the old name (grep plus a full-project compile), no BI reference in any declared `repo_path`, at least one full refresh cycle elapsed since the migrate phase, and — for anything with unknown consumers — a clean query-log check over a window longer than any plausible consumer period. Missing any of those, hold the shim and say which one is missing. Impatience in the contract phase is the single most common way this pattern fails.

**If the column carries a masking policy or is referenced by a row filter, the rename is a two-part change** — the model and the policy. The two failure directions are asymmetric: the policy can be stranded on a column that no longer exists, which on some platforms makes *subsequent* queries fail after a perfectly green build, or the new column can arrive with no policy attached, which fails silently and leaves sensitive data exposed while everything appears to work. The second is the one to plan around. Confirm the policy moved before treating the migrate phase as done; see `dbt-handling-sensitive-data` for how the mechanism behaves per engine.

### Rename a model

A model rename is really two changes that a single-commit rename bundles by accident: the **dbt-level name** used by `ref()`, and the **physical relation name** in the warehouse.

| What you change | Who notices | How to decouple it |
|---|---|---|
| The `.sql` filename and every `ref()` | dbt consumers, at compile time — loudly | Nothing to decouple; fix them in the same change |
| The relation name in the warehouse | BI, ad-hoc queries, exports, reverse ETL — silently | Set `alias: <old_relation_name>` on the renamed model, so the physical name does not move yet |

That is why `alias` is the workhorse here. Rename the model and pin the relation name in change one; move the relation name in change two, coordinated with the consumers that hardcode it. Bundling them means the loud failure and the silent failure land together, and the loud one absorbs all the attention.

Two things that also move with a model rename and get forgotten:

- **The schema YAML entry and, by convention, the YAML filename.** Check `naming.yaml_file_pattern`.
- **The schema itself, if the project assigns schemas by path and the file moved directories.** Then the fully-qualified name changes even if the identifier did not, and every external consumer breaks. Covered under moving a model between layers in `dbt-restructuring-dags`.

### Remove a column

1. Remove references from downstream models first.
2. Remove tests on the column, then its YAML entry.
3. Remove it from the model SQL.
4. Compile the whole project.

The order is deliberate: the reverse leaves the project uncompilable between steps. If BI referenced the column, insert a null-typed shim window before step 3.

On an **enforced-contract** model, steps 2 and 3 are not separable: the contract is checked against the SQL at build time, so removing the column from one side alone fails the build. Change the `columns:` entry and the `select` in one edit — and note that on a contracted model this is a **breaking change to the contract**, so if the model is versioned it needs a new version rather than an in-place edit. On a contracted **incremental** model, also check `on_schema_change`: it must be `append_new_columns` or `fail`, which means a removed column is not dropped from the existing relation and the stale column persists there until a full refresh. `sync_all_columns` would drop it, which is exactly why contracts disallow that setting.

### Change a type

Type changes break in ways that do not raise errors. Before changing one, check every downstream site where the type is load-bearing:

- join conditions between the column and something of the old type
- comparisons to literals — `'0'` versus `0`, string date comparisons
- `case` branches matching on string values
- arithmetic that silently truncated under integer division and now returns a fraction, or the reverse
- sort order — lexicographic on a string, numeric on a number, and `'10' < '9'`
- aggregate overflow and precision — widening or narrowing a numeric changes what `sum()` can hold and how it rounds
- a boolean rendered as `true`/`false` versus `0`/`1`, which every consumer formats differently

Cast once in staging, so downstream inherits it, and rebuild everything downstream. Compare the affected columns before and after, not just row counts: a type change usually preserves row count and changes values.

Two mechanical notes. On an **enforced-contract** model, a `data_type` change is one of dbt's defined breaking changes and CI will fail the comparison against the previous state — that is the feature working, and the fix is a new version, not a relaxed contract. On an **incremental** model, changing a column's type does not retype the existing relation: `on_schema_change` handles added and removed columns, not type changes. The model will either fail on the type mismatch or, on adapters that coerce, quietly write coerced values into the old type. A type change on an incremental model requires a full refresh, and if the model is `full_refresh=false` then it requires a rebuild strategy from `dbt-incremental-models` instead.

### Change grain

**This is the most dangerous change in the list and it cannot be done in place.** Changing what a row means changes row counts, breaks every join on the old key, and changes every aggregate computed downstream — while the model still builds, still passes most tests, and still populates every report.

Dimensional modelling practice has been explicit about this for decades: the grain declaration is a **binding contract** on the design, it must be declared before any dimension or measure is chosen, and every column in the model has to be true to it. Two consequences follow directly, and they are the reason an in-place edit does not work:

- **Different grains belong in different relations.** The Kimball rule is that each proposed grain yields a separate physical table and grains must not be mixed in one. A model edited from one grain to another is not an evolved model, it is a different model wearing the old one's name — and the name is what every consumer holds.
- **Every downstream measure was chosen to be true to the old grain.** A sum that was correct per order is not correct per order line. Nothing in the model records that dependency, so nothing can check it for you.

The mechanism of failure is worth stating precisely, because it explains why tests do not save you. Consumers break in two different ways: those that **join** on the old key now fan out or drop rows, and those that **aggregate** now aggregate over a different number of rows. Both produce valid-looking output. A `unique` test on a surrogate key that was regenerated from the new grain columns will **pass**, because the new key is genuinely unique at the new grain — it is testing the new design, not detecting the change.

**Build the new grain as a new model.** This is the expand phase, and here it is not merely preferable, it is the only version of this change that can be verified:

1. **Enumerate every consumer** and record, for each, whether it joins on the key or aggregates. Both break, differently. This list is the migration plan.
2. **Create the new model at the new grain**, named for what a row now is, with its own surrogate key including all new grain columns and a `unique` test on it. State the grain in the YAML description — the next person cannot infer it and neither can a test.
3. **Verify the new model's grain with numbers**: `count(*)`, `count(distinct <new_key>)`, and the relationship between the old and new row counts, which should be explainable rather than merely observed. If you cannot say why the ratio is what it is, you do not yet understand the new model.
4. **Reconcile the measures.** For every additive measure, the total over the new model must equal the total over the old one. That is the single strongest check available on a re-graining, and a mismatch localises the bug immediately. If a measure's total *should* change, say why before you run it.
5. **Migrate consumers one at a time**, each verified against its own baseline. This is what the new-model approach buys you and what an in-place edit destroys.
6. **Deprecate the old model** with a date, then delete it and drop its relation.

Two things that must be said in the PR rather than discovered afterwards. **An incremental model whose key changed must be fully refreshed**, and downstream incrementals that joined on the old key usually must too — this is a required post-merge action, not a detail; see `dbt-shipping-changes`. And **a coarser grain destroys information**: if the new model aggregates away a column, the old detail is only recoverable by rebuilding from source. Where the source cannot reproduce history, aggregating in place is irreversible, and the old model must be retained rather than deprecated.

If you are editing in place regardless — because the model is genuinely new, has no consumers, and the blast radius proved it — then update the SQL, the surrogate key, the `unique_key` config, and the tests together, and say in the summary that grain-change safety was traded away deliberately because the consumer set was empty.

### Delete a model

Leaf-first, always:

```
1. remove singular tests referencing it
2. remove its YAML entry (or the whole YAML file if it documented only this model)
3. update or remove every downstream ref()
4. delete the .sql file
5. dbt compile  (whole project)
6. after merge and deploy: drop the warehouse object
```

#### The dead-relation problem

Step 6 is the one that gets dropped, and it is the more serious half. **dbt does not drop the warehouse table or view when a model is deleted.** The same is true of a model that is merely deprecated, or disabled. So the object persists — holding storage, and, far worse, *serving stale data to anyone still querying it*.

A deleted model with a surviving relation looks alive and stops updating. There is no error anywhere: the relation exists, queries succeed, the numbers are simply frozen at the last build. This is strictly more dangerous than the loud alternative, because a dropped relation produces an immediate error that someone fixes, and a stale one produces a wrong answer that someone acts on.

Put the drop in the post-merge actions explicitly, with the fully-qualified name, and confirm it afterwards.

#### The dead-model test, and why "no dbt descendants" is not "unused"

Before deleting, be honest about what the evidence covers:

| Evidence | Supports the claim |
|---|---|
| `dbt list --select <model>+` is empty | No dbt node in **this project** references it |
| Grep of `models/ tests/ macros/` is clean | No textual reference, including ones the graph might miss |
| Model is not `access: public`, and the project uses `access` | No other dbt project can reference it |
| Each `bi.consumers` `repo_path` greps clean for the **physical** name | No version-controlled BI reference |
| Query log clean over a window exceeding any plausible consumer period | No warehouse consumer *that this log can see* |

Only the conjunction of all five approaches "unused", and even then only for the window measured. **A terminal model has no dbt descendants by design** — check `layers[].terminal` before treating an empty descendant list as evidence of anything. The whole purpose of a mart is to be consumed from outside the DAG.

The honest wording when checks are missing is a sentence, not a hedge: "No references in the dbt project or in the one declared BI repository; query history was not available, so external consumption could not be checked." That sentence is the deliverable. "Unused" is not.

Two lower-risk alternatives to deletion, both of which convert a silent failure into a loud one:

- **Disable it** (`enabled: false`) or set a past `deprecation_date`, leave the relation in place, and wait. dbt stops building it; anything reading the relation gets progressively staler data — still silent, so pair it with a monitored freshness check if one exists.
- **Replace the relation with a view that fails loudly**, or drop it and watch for errors, in a period when someone is available to respond. A loud break during a window you chose is much cheaper than a quiet one at a time you did not.

---

## Step 4 — Never bundle a rename with a logic change

This is worth its own section because the reasoning is not obvious and the cost is high.

When a rename and a logic change ship together:

- **The diff is unreviewable.** A reviewer cannot tell which lines are mechanical and which are substantive, so both get skimmed.
- **Verification proves nothing.** A comparison shows a difference. Was it the intended logic change, or a mistake in the rename? Two candidate causes, no way to separate them.
- **Revert is all-or-nothing.** Rolling back the logic problem also rolls back the rename, re-breaking every consumer that just migrated.
- **The safe half becomes unsafe.** A rename is mechanically verifiable — compiled SQL should be identical except for identifiers. Bundling destroys that property.

Rename first, verified as output-neutral per `dbt-refactoring-safely`. Then change the logic, verified against the renamed baseline. Two changes, each with a clean proof. This is one of the universal rules; the point here is *why* it holds.

---

## Step 5 — Communicating and coordinating the change

For anything classified High or Critical, the technical work is the smaller half. The migrate phase is the longest phase of expand/contract precisely because it is not under your control.

- **Tell consumers before the window opens, not when it closes.** A deprecation notice that arrives with the removal is not a notice.
- **Name the replacement concretely.** "Use the new model" is not a migration path. State which model, which column, and what changes on their side.
- **Give the window a date and hold it.** A window with no date never closes, and the shim becomes permanent. A date that slips twice teaches consumers to ignore the next one.
- **Ship the BI change and the dbt change in a known order.** Expand first (new column or shim exists), then the consumer migrates, then the old thing is removed. Any other order has a broken interval.
- **State the coordination in the PR.** Which BI repository, which change, and who is doing it. A reviewer cannot verify a cross-repository dependency that is not written down.

### What a migration guide has to contain

A notice that does not let a consumer act is a notice they will ignore. The minimum:

| Element | Why it is not optional |
|---|---|
| Old name and new name, exactly as they appear in the warehouse | The consumer is searching their own code for a string, not reading your DAG |
| What changes semantically, not only syntactically | A renamed column whose definition also changed is two migrations, and they need to know |
| The removal date, with the time zone | `deprecation_date` without an offset is interpreted in the system time zone of whichever machine runs dbt |
| The one-line mechanical fix, where there is one | Most consumers need a find-and-replace, not an explanation |
| Who to ask, and where the change is tracked | An unattributed deprecation notice gets no replies and no migrations |

For anything with unknown consumers, add a **grace signal**: keep the shim producing correct data past the announced date, but make the deprecation warning an error in CI on that date. Consumers inside dbt are then forced to migrate while consumers you cannot see keep working — which is the whole reason the window exists.

### Enforcing a timeline rather than hoping for one

Announcements do not migrate anyone. Two mechanisms do:

```yaml
# dbt_project.yml — turn advisory warnings into a CI failure once the window closes
flags:
  warn_error_options:
    error:
      - DeprecatedModel
      - DeprecatedReference
      - UpcomingReferenceDeprecation
```

Promote these **in CI first**, never straight to production: a scheduled production build that starts failing at midnight on a date somebody typed six months earlier is a self-inflicted incident. The other mechanism is the periodic sweep — list what is deprecated and past due, and act on it on a cadence:

```bash
dbt list --quiet --output json --output-keys name deprecation_date
```

There is no selector for deprecation state, so a manifest read is the only way to enumerate it. A project with three years of undated shims and no sweep is the normal outcome of skipping this.

## Verification

The verification here is not "does it build" — a breaking change usually builds fine. It is "did the things I did not intend to change stay the same."

- Full-project `dbt compile` — catches every reference you did not grep.
- `dbt build --select <model>+` — the model and everything downstream.
- For a rename meant to be output-neutral: compiled SQL diff, plus a row-level comparison per `dbt-refactoring-safely`.
- For a grain change: row count, distinct key count, uniqueness of the new key, **and every additive measure's total reconciled between old and new** — with numbers.
- For a type change: value-level comparison on the affected columns, not just counts.
- For a contract change, in CI: `dbt build --select "state:modified+" --state <prod_artifacts>`, so dbt's own breaking-change detection runs. Without a stored previous manifest this check does not fire at all — say so rather than implying the contract was validated.
- Before a contract *phase*: the evidence list from the rename section — no dbt references, no BI references, a full cycle elapsed, and a query-log check where one is possible.

Evidence standards are in `dbt-verification`. Post-merge actions, including required full refreshes and warehouse cleanup, are in `dbt-shipping-changes`.

## Completion checklist

- [ ] Blast radius checked **before** any edit
- [ ] dbt consumers found with **both** grep (both quote styles, across `models/`, `tests/`, `macros/`, `analyses/`) and `dbt list --select <model>+`
- [ ] Exposures, singular tests, selectors, semantic models, and macro-hardcoded relations checked
- [ ] `access` and `groups` inspected; a `public` model treated as having consumers the DAG cannot see
- [ ] Cross-project consumption addressed, or stated as not checkable from this repository
- [ ] `bi.consumers` read; each declared path grepped for the **physical** relation name as well as the model name — or absence of the field stated explicitly
- [ ] Query history checked over a window exceeding any plausible consumer period, or its unavailability stated
- [ ] Change classified Low / Medium / High / Critical, rounded **up** for every check that could not be run
- [ ] Expand / migrate / contract phases identified and shipped separately for anything above Medium
- [ ] Deprecation chosen over deletion wherever a consumer exists
- [ ] Every shim carries a removal date, a tracked task, and a `data_type` entry if the model is contracted
- [ ] Ordering followed: additive first, removal last
- [ ] Nothing bundled — no logic change riding along with a rename
- [ ] Grain change built as a new model, or in-place editing justified by an empty consumer set
- [ ] Grain change: key uniqueness and additive-measure totals reconciled with numbers
- [ ] Contract implications stated: breaking-change class, `on_schema_change` setting, whether a new version is warranted
- [ ] dbt version and platform dependence stated for any governance feature recommended
- [ ] Full-refresh requirement identified and written into the PR
- [ ] Warehouse relation drop listed in post-merge actions, with the fully-qualified name
- [ ] Downstream consumers notified before the window, with a migration guide that names the replacement and the date
- [ ] Timeline enforcement chosen — CI warning promotion, a sweep cadence, or an explicit decision to rely on neither

## The failure modes that actually happen

1. **BI break found by a user.** The contract's consumers were never grepped, or only the dbt model name was searched and not the physical relation name. The report renders, the field is empty or wrong, and the first signal is someone asking why a number moved.
2. **Grain change that nothing catches.** Row counts move, downstream aggregates change, every test passes — including the uniqueness test, which now validates the *new* design. Then a total disagrees with another system and the investigation starts weeks later.
3. **Deleted model, surviving relation.** The dbt model is gone, the warehouse object is not. It stops refreshing and keeps serving. Consumers see stale data with no error — quieter and worse than a dropped relation.
4. **"No dbt descendants, therefore unused."** A terminal model has no dbt descendants by design. The claim that was provable was much narrower than the claim that was made.
5. **Shim that never expires.** No date, no task, no sweep. Two years later a column exists that is documented as temporary and cannot be removed because nobody can prove it is unused.
6. **Contract phase shipped with the migrate phase.** The old column is dropped in the same pull request that moves reads to the new one, so the rollback path is gone at exactly the moment it is needed.
7. **Type change with no error.** A join between the changed column and an unchanged one starts returning fewer rows, or a boolean starts rendering as `0`/`1`. The build is green.
8. **Bundled rename.** A comparison shows a diff with two possible causes. The verification cannot distinguish them, so it gets rationalized instead of investigated.
9. **Concluding "no BI impact" from a missing contract field.** Absence of a declaration is not absence of consumers. Say what you could not check.
10. **A governance feature introduced to solve one rename.** Contracts, versions and `access` are permanent maintenance in exchange for one moment of safety. Adopting one mid-migration usually adds a second problem to the first.
11. **Deprecation warnings promoted to errors in production.** A scheduled build fails at midnight on a date typed six months earlier. Promote in CI; leave production warning.
12. **Query-log clean on a table consumed through a view.** A text-searchable log never mentions the base relation. The check returned nothing and was reported as evidence of nothing being there.
