---
name: dbt-restructuring-dags
description: Use when decomposing one model into several, combining several models into one, inserting a layer between two existing models, rerouting a ref() to change what feeds what, flattening a deep or linear DAG, extracting logic several models duplicate, moving a model between layers, changing a model's materialization, or splitting one project into several. Covers detecting structural defects and the ordering that keeps the project compiling at every step.
metadata:
  phase: decide
---

# Restructuring a DAG

Reshaping a DAG is a sequence of edits, and most of the risk lives in the *order* of those edits rather than in any single one. A project that does not compile is not a work in progress — it is a broken branch that nobody else can build on, and it hides whether your logic was right.

Every operation below follows the same shape:

> **build new alongside old → prove equivalence → cut over consumers → delete old**

Four phases, and the project compiles and builds at the end of every one. The tempting shortcut — edit in place and fix the fallout — produces a window where nothing compiles and no comparison is possible, because the "before" no longer exists.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

Relevant fields:

| Field | What it decides |
|---|---|
| `layers` | Which layers exist, their paths, and their default materialization |
| `layers[].may_reference` | Whether a proposed edge is legal — this is the no-layer-skipping rule |
| `layers[].terminal` | Whether a model may be referenced at all |
| `layers[].prefixes` | What a new model extracted into that layer must be named |
| `naming` | The name for any model you create |
| `project.warehouse` | Whether zero-copy cloning, materialized views, or a given constraint are available |
| `project.dbt_version` | Whether `dbt clone`, contracts, and version-aware selection exist |
| `bi.consumers` | Who breaks when a physical relation name or object type changes |

Without a contract, reason with generic layer concepts — a staging layer that is one-to-one with sources, an intermediate layer that holds business logic, and a mart layer that is consumed — and **say explicitly that this is generic guidance**, not the project's declared architecture. Do not infer a rule from a handful of files and then present it as policy. If layer rules matter to the decision, infer the contract properly first (`dbt-project-conventions`).

## Diagnose before you reshape

Restructuring without a named defect is rearranging. Before any operation, state which of these you found and how you detected it — a reviewer cannot evaluate "the DAG was messy".

| Anti-pattern | How to detect it | Why it is a defect | Operation |
|---|---|---|---|
| A mart or intermediate model selecting from a `source()` directly | `grep -rn "source(" models/marts/ models/intermediate/` | The staging layer is the one place a source's columns are renamed and recast. A model bypassing it duplicates that work, or skips it | 3 |
| One model referencing both a `ref()` and a `source()` | `grep -rln "source(" models/` then check for `ref(` in the same files | Half the model reads cleaned data, half reads raw. The inconsistency is invisible in the output | 3 |
| A source with more than one direct child | `dbt list --select source:<name>.<table>+ --output name` | Each source should have exactly one staging model. Multiple children means the light transformations are done more than once and can diverge | 3 |
| A staging model referencing another staging model | `grep -rn "ref('stg_" models/staging/` (adapt to `layers[].prefixes`) | Either the child is misnamed and belongs in a later layer, or it should read a source | 6 |
| A staging model referencing an intermediate or mart model | `dbt list --select +<staging_model> --output name` and read the parents | Almost always a misnamed file. It also creates a cross-layer edge that makes the DAG unreadable | 6 |
| A model with more than about three direct leaf children | `dbt list --select <model>+ --output name` and count | Transformation that should be shared upstream is being duplicated per consumer, or belongs in the BI layer | 8 |
| A model referencing more than about seven other models | `grep -oE "ref\('[^']+'\)" models/path/model.sql \| sort -u \| wc -l` | Too much happening in one place to review or test. Two intermediates that each hold part of the complexity are easier to verify | 1 |
| Two siblings, one of which feeds the other, both fed by the same parent, with no other consumers | Read the DAG around any short "loop" | The middle model buys no parallelism — nothing downstream can start until it finishes — and adds a hop | 2, 5 |
| A model with no parents at all | `dbt list --select <model> --output name` then check for `ref`/`source` in its SQL | It contains a hardcoded relation name, so dbt cannot order the build correctly and lineage is wrong | Fix the reference first, then reassess |
| A hardcoded `database.schema.table` in any model | `grep -rnE "from +[a-z_]+\.[a-z_]+\.[a-z_]+" models/` | Same as above, plus it reads the same relation in every environment, so dev silently reads production | Fix the reference first |
| Several stacked views doing real aggregation | `dbt list --select <model>+ --output config` and read materializations | Views compose into one deeply nested statement evaluated at read time | 7 |
| Deep chains of ephemeral models | `grep -rn "materialized='ephemeral'\|materialized: ephemeral" models/` | Each consumer recompiles and re-executes the whole chain; nothing is inspectable | 7 |

Some of this is mechanically checkable: the `dbt_project_evaluator` package encodes most of the rows above as tests against the manifest and will list every occurrence with names. Running it once at the start of a restructuring effort turns "the DAG feels wrong" into a ranked list. It classifies by layer using the project's own naming and paths, so its output is only as good as the prefixes it is configured with — reconcile them against `layers[].prefixes` before trusting the classification.

**Cycles are the one defect you cannot ship.** dbt refuses to parse a project containing a `ref()` cycle, so a reshape that accidentally creates one fails immediately at parse — loud, cheap, and the reason `dbt parse` belongs after every rerouting step. In a multi-project setup, cycles are checked across project boundaries too, but only at the node level and only when the depended-on project has produced its public models, which is why cross-project dependencies have to be established one direction at a time.

## Before any restructuring: map what exists

```bash
# full lineage, both directions
dbt list --select +<model>+

# direct consumers, which is what you must cut over
grep -rn "ref('<model>')" models/ tests/ macros/
grep -rn 'ref("<model>")' models/ tests/ macros/
```

Both quote styles. A grep for only single quotes is the most common way a consumer is missed.

Record four things before editing. All four are needed later and two of them are unrecoverable once you start:

1. **The consumer list** — every model, test, and macro that references the model.
2. **The baseline output** — materialize it and clone it, as in `dbt-refactoring-safely`. Once you rebuild, the "before" is gone.
3. **The test inventory** — which tests live on which columns, so you know where each must land.
4. **Whether the model is terminal.** If the contract marks its layer terminal, nothing should be referencing it, and anything that does is a pre-existing violation to report rather than to preserve.

On a wide reshape, baselining every affected model by hand is the step people skip because it is tedious. Where the platform supports zero-copy cloning, `dbt clone` does it in one command against a stored production manifest:

```bash
dbt clone --select <model>+ --state <path/to/prod/artifacts>
```

Two caveats before relying on it. It requires dbt 1.6 or later, and on platforms without zero-copy cloning it creates pointer views rather than independent copies — which means the "baseline" changes when the source does, and is therefore not a baseline at all. Confirm `project.warehouse` supports cloning; if it does not, copy the relations explicitly.

---

## Operation 1 — Decompose one model into several

**When it is justified:** a CTE inside the model is reusable by something else, deserves its own tests, or is the expensive part of a slow build that would benefit from being materialized once. **When it is not:** the model is simply long. Length alone is not a reason to split; splitting adds DAG hops, build overhead, and files to keep in sync.

Ask one question per candidate CTE: *would a second model reference this?* If no, it stays a CTE.

Three justifications, in descending order of how well they hold up:

| Justification | Holds up? |
|---|---|
| A second model already needs the same logic, or is about to | Yes — this is the extraction case, below |
| The logic is the expensive part of a slow build and is recomputed per consumer | Yes, if you can name the consumers and the cost |
| The CTE encodes a business concept that deserves its own tests and description | Usually — a named, tested relation is a real asset |
| The model is long | No |
| The model has many CTEs | No. Count of CTEs is not a defect; a project-wide convention that models are broken into layered CTEs is a readability pattern, not a symptom |

### Ordering

1. **Create the new upstream model(s)** with the extracted logic, named per the contract for their layer. Do not touch the original yet. The project compiles: you have added leaf nodes nothing depends on.
2. **Build and inspect the new models.** Row counts and grain should match what the CTE produced.
3. **Replace the CTE in the original with `ref()`** to the new model. This is the only edit to the original, and it should be textually minimal — the import CTE now selects from a `ref()` instead of being defined inline.
4. **Compile the original and diff the compiled SQL** against the pre-split compiled output. If the extraction was faithful, the compiled SQL differs only in the relation name substituted for the inline CTE. That is the strongest evidence available and it is cheaper than comparing data.
5. **Build the original and compare to baseline** with `audit_helper`, per `dbt-refactoring-safely`. Acceptance is zero differing rows.
6. **Migrate tests and YAML** to the model that now owns each column.

### Extracting logic that two or more models duplicate

The most valuable version of this operation, and the one with an extra trap. Given the same non-trivial expression in three models, extract it once and have all three read it.

1. **Prove the duplicates are actually identical.** Diff the fragments character by character, not by reading them. Duplicated logic drifts, and the differences are the interesting part: one copy has an extra filter, another casts differently, a third handles nulls. **If they differ, extraction is a behaviour change for at least two of the three consumers** — you are picking a winner. Decide which semantics is correct, say so, and expect a non-zero diff in exactly the models whose behaviour you corrected.
2. **Extract to a model, or to a macro?** A macro shares *text* and produces a separate computation per call site; a model shares *the computed relation*. If each consumer needs the fragment applied to its own rows, that is a macro (`dbt-macros`). If all consumers need the same rows, that is a model.
3. Then follow the ordering above, one consumer at a time, comparing each to its own baseline.

The seam risk is grain. The extracted model has a grain; each consumer joined to the inline version at some cardinality. If the extracted model is coarser or finer than what any one consumer's inline CTE produced, that consumer's join changes cardinality and the comparison for that consumer alone will fail. Compare counts at each seam, not only at the far end.

### Where each test goes

| Test | Lands on |
|---|---|
| `unique` on a column | The model that produces that column at that grain |
| `not_null` | The earliest model that introduces the column |
| `accepted_values` | The model that applies the mapping |
| `relationships` | The model holding the foreign key |
| Singular test | Update its `ref()`; the test itself usually does not move |

A split with tests left behind on the original will either fail (column no longer present) or silently stop testing the thing it was written for.

One caveat if the extracted model is **ephemeral**: it has no relation, so `unique` and `not_null` tests on it cannot run in the usual way, and a unit test on a consumer must supply the ephemeral input as raw SQL rather than as a dictionary or CSV fixture. If the extracted logic deserves tests, that is an argument for a view rather than ephemeral — see Operation 7.

---

## Operation 2 — Combine several models into one

**Precondition, and it is absolute:** every model being absorbed must have exactly one consumer — the target. Check, do not assume:

```bash
for m in <model_a> <model_b> <model_c>; do
  echo "== $m"; grep -rln "ref('$m')" models/ tests/
done
```

If any has a second consumer, it cannot be absorbed without that consumer also changing, which is a different and larger operation.

### Ordering

1. **Baseline the target model** before touching anything.
2. **Copy the absorbed models' logic into the target as CTEs**, in dependency order, and repoint the target's internal references at those CTEs. The absorbed models still exist. The project compiles; the absorbed models are now orphans, which is legal.
3. **Compile and diff compiled SQL.** An inlined CTE chain should produce compiled SQL equivalent to the original chain, since a chain of view-materialized models and a chain of CTEs are the same query.
4. **Build and compare to baseline.** Zero differing rows.
5. **Only now delete the absorbed models** — SQL file, YAML entry, tests. Deleting them at step 2 breaks compilation for as long as the target still references them.
6. **Migrate their tests** into the target, or state deliberately which ones no longer make sense on a CTE and are being dropped.

Note the cost: merging removes intermediate materializations that were debuggable inspection points. If the chain existed because someone needed to query the middle of it, merging removes that ability. Weigh it, and say so.

### What combining destroys, and what to check before you do

Everything in this list is a real capability the absorbed model had. A combine trades all of them for one fewer node.

| Lost | Check before combining |
|---|---|
| A queryable relation for debugging | Was anyone querying it by hand? Query history over the retention window is the only evidence |
| Its own tests | Which tests were on it, and do they still make sense inside a CTE? Some cannot be expressed there |
| Its own description in the docs | Move the description into the combined model's YAML, or the concept becomes undocumented |
| A parallelism boundary | Two independent absorbed models could build concurrently; one combined model cannot. On a wide DAG this can make the critical path longer, not shorter |
| An incremental boundary | **A `table` or `view` absorbing an `incremental` model is not a combine, it is a materialization change plus a history decision.** The incremental model's accumulated state disappears. Treat it as the `incremental` → `table` row in Operation 7 first, and only combine if that is acceptable |
| A grant or access boundary | Grants configured on the absorbed model no longer exist. If a consumer had select on it and not on the target, that access is gone |
| Its physical relation | dbt will not drop it. See the surviving-relation problem in `dbt-breaking-changes` and add the drop to post-merge actions |

The single-consumer precondition is about `ref()` only. **A model with exactly one dbt consumer can still have BI or ad-hoc consumers**, and `grep` over `models/` says nothing about them. If the absorbed model is in a layer the contract does not mark internal, check `bi.consumers` and query history before absorbing it.

---

## Operation 3 — Insert a layer between two existing models

Given `upstream → downstream`, you want `upstream → new_middle → downstream`. Typically because the transformation now needs a home of its own, or because several downstream models are about to duplicate the same logic.

First check legality against `layers[].may_reference`: the new model's layer must be allowed to reference `upstream`'s layer, and `downstream`'s layer must be allowed to reference the new one. Inserting a mart-layer model in the middle of a staging-to-intermediate edge usually violates the project's own rules even though dbt will happily compile it.

### Ordering

1. Baseline `downstream`.
2. **Create `new_middle`** referencing `upstream`. Nothing references it yet. Project compiles.
3. **Build `new_middle`** and verify it produces what `downstream` expects — same grain, same key columns, same types.
4. **Reroute `downstream`** to `ref('new_middle')` and remove the logic that moved.
5. Compile, build, compare `downstream` to baseline. Zero differing rows.

The failure mode specific to this operation is a **grain mismatch at the seam**: `new_middle` aggregates or deduplicates slightly differently than the inline logic did, `downstream` fans out on the join, and row counts move. Compare row counts at the seam explicitly, not just at the far end.

---

## Operation 4 — Reroute a `ref()`

The smallest structural change and the easiest to get wrong, because it looks like a one-line edit and is actually a claim that two relations are interchangeable.

Before rerouting `model_x` from `ref('old')` to `ref('new')`, verify all four:

- **Grain** — one row per what, in each? If they differ, the join in `model_x` will fan out or drop rows.
- **Columns** — every column `model_x` consumes exists in `new`, with a compatible type.
- **Coverage** — `new` covers the same entity set and the same history. A model that starts in a different month will silently truncate downstream history.
- **Legality** — the new edge satisfies `layers[].may_reference`, and `new` is not in a layer the contract marks terminal.

Two more that only bite in a governed project: **`access`** — if `new` is `private` to a group `model_x` is not in, the reroute is a parse-time error, which is the good case; and **freshness** — if `old` was a view and `new` is a table built on a different schedule, `model_x` now reads data as of a different moment, which is a correctness change nothing will flag.

```sql
-- run against both relations, using explicit database and schema, never ref()
select count(*) as rows,
       count(distinct <key_col>) as distinct_keys,
       min(<date_col>) as earliest,
       max(<date_col>) as latest
from <db>.<schema>.<relation>
```

Then reroute, compile, build, and compare `model_x` to its baseline. If the reroute is *supposed* to change output, it is not a reroute — it is a logic change, and it needs the treatment in `dbt-breaking-changes`.

---

## Operation 5 — Flatten a deep or linear DAG

A chain of single-consumer, single-CTE models costs a DAG hop each and buys nothing. Flattening is Operation 2 applied repeatedly, with one addition: **do it one link at a time, verifying after each.** Collapsing five models in one commit produces a diff nobody can review and a failure you cannot localize.

Find the candidates before deciding anything:

```bash
# models whose only consumer is a single other model
dbt list --select <model>+ --output name
```

A link is a flattening candidate when all of these hold: exactly one consumer, no tests that depend on it being materialized, and no independent value as an inspection point.

Two things that look like flattening candidates but are not:

- **A fan-in point.** Several models joining at one intermediate is good structure, not a deep chain. Do not collapse it into each consumer — that duplicates the join.
- **A materialized break in a long chain.** A table in the middle of a chain of views is often there deliberately, because the chain is expensive to recompute. Collapsing it makes every downstream build recompute the whole thing. Check the materialization before assuming the hop is waste.

### Chain depth is not the metric; what the depth costs is

A long chain is only a problem when the hops cost something. Establish which cost applies before flattening:

| The chain is made of | What the depth actually costs | Is flattening the fix? |
|---|---|---|
| Tables | One build and one full copy per hop — storage, build time, and refresh lag accumulating down the chain | Often yes |
| Views | Nothing at build time. At read time, the whole chain becomes one nested statement the optimizer must handle | Only if reads are slow — measure first |
| Ephemeral models | Each consumer re-executes the entire chain inline, with no shared computation and nothing inspectable | Usually yes, or materialize one link |
| A mix, with a table at the expensive point | Nothing — this is a deliberate design | No |

The direction of the fix differs too. A chain of tables is flattened by *combining*; a chain of views that is slow to read is fixed by *materializing one link as a table*, which makes the chain shorter from the optimizer's point of view without deleting any model. Reach for the second before the first: it is a config change with no diff to review, and it is reversible.

---

## Operation 6 — Move a model between layers

A model in the wrong layer misleads everyone who reads the DAG: its name implies a set of guarantees it does not provide. Moving it is mostly mechanical, but it is a **rename plus a relocation**, which means every consumer changes.

Check the contract first. The destination layer's `may_reference` must permit everything the model currently references, and the model's existing consumers must be permitted to reference the destination layer. A model promoted toward the mart layer frequently fails the second test — a layer marked `terminal` may not be referenced at all, so promoting a model that still has dbt consumers turns them into violations.

1. Confirm layer legality in both directions.
2. Rename the file to the destination layer's prefix convention and move it into that layer's path.
3. Update the config block if the destination's default materialization differs. Where materialization is set per-layer in `dbt_project.yml`, moving the file changes it implicitly — verify what it became rather than assuming.
4. Move the schema YAML alongside it, renamed per `naming.yaml_file_pattern`.
5. Update every consumer's `ref()`, including tests and macros.
6. Compile the whole project, then build and compare to baseline.

The schema also changes if the project assigns schemas per layer, which means the **physical relation moves**. Anything reading the old fully-qualified name — a BI tool, an external query — breaks even though dbt compiles cleanly. Treat that as a breaking change and handle it per `dbt-breaking-changes`.

Two further consequences of a layer move that the diff does not show:

- **The old relation survives in its old schema.** dbt does not drop it, so the project now has two relations with the same identifier in two schemas, one of them frozen. Anything reading the old one gets stale data with no error. Add the drop to post-merge actions with the fully-qualified name.
- **Grants and access change with the schema.** Grants configured per-schema out of band do not follow the model, and if the destination layer sets a different default `access` or `group` in `dbt_project.yml`, the model inherits it — potentially turning an internal model into a `public` interface, or breaking an existing reference by making it `private`. Confirm what the model's `access` and `group` became rather than assuming they were unchanged:

```bash
dbt list --select <model> --output json --output-keys name access group config
```

## Operation 7 — Change a model's materialization

No SQL changes, so the diff looks trivial and the review is usually cursory. The risks are entirely in what the object *becomes*, and two of them break consumers without failing a build.

Read `layers[].materialization` from the contract first. If the model's layer declares one and you are departing from it, that needs a reason recorded in the PR, not a silent config edit.

| Direction | Why it happens | What actually bites |
|---|---|---|
| `table` → `view` | The table was a pass-through adding nothing; a view removes a build step and a copy | Cost moves from build time to **every read**. A view over a large scan read by a dashboard on every load can cost more than the table did. Materialization is not free just because it is not built. |
| `view` → `table` | A view is re-scanned by many consumers, or its logic got expensive | It now needs a build and can be **stale**. Anything that relied on the view being live-as-of-query-time silently starts reading a snapshot. |
| `incremental` → `table` | The incremental logic was more risk than the runtime saved | **The history is gone.** If rows are unreconstructable from the source, this is irreversible — see the `full_refresh=false` guidance in `dbt-incremental-models`. |
| `table` → `incremental` | Full rebuilds outgrew the window | Everything in `dbt-incremental-models` now applies: boundary predicate, `unique_key`, strategy choice. This is not a config change; it is a rewrite. |

Three things to do regardless of direction:

1. **Drop the old relation.** dbt does not always replace a `view` with a `table` of the same name cleanly across adapters, and a leftover object of the wrong type causes errors that read as permission problems. On the first production run after the change, confirm the object type is what you intended.
2. **Check grants and downstream tooling.** Grants may not survive a replace, and some BI tools cache the object type. A dashboard can fail on a relation that queries fine in a worksheet.
3. **Verify the data is unchanged, not just the config.** The point of this change is that output is identical, which makes it exactly the case where a regression hides. Use the row-count and equivalence rungs in `dbt-verification`.

Going `table` → `view` in a chain deserves one extra thought: **views compose into a single query.** Three stacked views become one deeply nested statement the optimizer must handle at read time, and the cost is invisible until a consumer complains. Fine for thin pass-throughs, poor for anything doing real aggregation.

### Consequences by category, not just by direction

Four things change when a materialization changes, and only the first is in the diff.

**Grants.** dbt reapplies `grants` on every build, but only for nodes that *have* a `grants` config; if grants were applied out of band — by a hook, by a warehouse admin, by an `alter` someone ran once — a replace can drop them silently. The failure surfaces as a permission error for a consumer, at a time unrelated to your change. Confirm the grants on the new object rather than assuming they carried.

**Freshness and the meaning of a read.** A view is live as of query time; a table is as of its last build. Going `view` → `table` inserts a staleness window that no consumer asked for and none will be told about. Going the other way removes one, which sounds strictly good and is not: a downstream model that was reading a stable snapshot now sees rows appear mid-run, and two models reading the same view at different moments can disagree.

**What the object supports.** This is where a materialization change quietly removes a capability:

| Target materialization | Loses |
|---|---|
| `view` | Contract `constraints` (names and types are still checked, constraints are not); any performance benefit of precomputation |
| `ephemeral` | Everything — no relation, so no direct queries, no grants, no hooks, no contracts, no tests on it in the usual sense, and operations cannot `ref()` it |
| `materialized_view` | Contract support entirely; also a different refresh model, since most platforms refresh it on their own schedule rather than on `dbt run` |
| `incremental` | Nothing structurally, but it gains every failure mode in `dbt-incremental-models` |

**Platform behaviour.** Whether an existing relation of the wrong type is replaced cleanly, and whether a materialized view exists at all, are adapter-specific. Some platforms have no materialized-view materialization and use a different construct instead; the config name that works on one adapter is not portable. Read `project.warehouse` before recommending one, and say which adapter your statement is about.

## Operation 8 — Reduce extreme fan-out

Several leaf models hanging off one parent, each doing a small variation of the same thing. This is not a shape problem; it is duplicated logic wearing a DAG.

Diagnose before acting, because two very different causes look identical:

| Cause | Evidence | Fix |
|---|---|---|
| Every child applies the same transformation before its own variation | The same expression in each child | Push the shared transformation **up** into the parent, then remove it from each child. This is Operation 1's extraction case |
| Each child is a per-consumer or per-report reshape of the same rows | Children differ only by filters, column subsets, and labels | The reshaping belongs in the consuming layer, not in dbt. Delete the children and expose the parent, if the consuming tool can do the work |
| The children are genuinely different concepts that happen to share a parent | Little textual overlap; each has its own tests and grain | Not a defect. Leave it |

The second row is a judgment call that depends on the consuming tool, and it deserves an explicit statement rather than a preference. Tools that join and aggregate well can consume one wide parent; tools that prefer pre-shaped wide relations do better with the fan-out. **Whichever way the project has decided, the decision belongs in the contract, not in each PR** — otherwise the boundary moves per author and both layers accumulate half of the logic.

Fan-out counts are also a threshold judgment, not a law. The commonly cited trigger is more than about three direct leaf children, but the number is configurable in the tools that check it precisely because the right value depends on the project. Report the count you measured rather than asserting a limit.

## Operation 9 — Split a project in two

At some size, the reshape needed is not within the DAG but across it: two dbt projects with a declared interface between them, referencing each other's public models. This is the heaviest restructuring in this document and the one most often attempted too early.

**Availability first:** cross-project `ref()` is a feature of paid dbt platform tiers, not of dbt Core. A project on dbt Core alone can split into multiple repositories, but the projects then communicate through **sources** pointing at each other's warehouse relations — which loses lineage, loses the parse-time access checks, and loses the cycle detection. That is a materially worse arrangement than a monolith, and it is worth saying plainly before anyone starts.

### Signals that a split is warranted

Not "the project is big". The published signals are about the *cost of coordination*:

- Model count is degrading development performance — parse times, CI times, local iteration.
- Teams have diverged in workflow and cadence and are blocking each other.
- Communication overhead has started to show up as reliability problems in specific data products.
- Security or governance requirements would be served by isolation that folder conventions cannot provide.

If none of those are true, the answer is groups and `access` inside one project. They give you ownership and reference boundaries with none of the deployment coupling.

### Where to draw the line

Two shapes, and they compose:

| Split | Cut along | Typical reason |
|---|---|---|
| Vertical | DAG order — staging and shared foundations in one project, downstream domains in others | A tightly controlled shared base other teams build on but cannot edit; isolating models that carry sensitive columns so downstream consumers cannot reach them; fencing off expensive models |
| Horizontal | Source system or business domain | Team consumption patterns; independently shaped data; teams that need to move at different speeds |

Three constraints on any line you draw:

1. **The same rows must not be sourced into two projects.** Duplicating the underlying data, not just the code, is the failure this pattern is most prone to.
2. **The interface must be declared.** Models crossing the boundary need `access: public`, and — because you can no longer see who depends on them — a contract and probably versions. See `dbt-breaking-changes`.
3. **Dependencies are established one direction at a time.** Where two projects will depend on each other, the first must run and produce its public models before the second can take a dependency on it, and only then can the reverse dependency be added. Attempting both at once fails.

Monorepo or multi-repo is a separate decision from where the DAG splits. A single repository is simpler while the number of projects and contributors is small; separate repositories are what actually decouple release cadence and permissions. The friction of upstreaming a change across a boundary is the point of the boundary — but if a team needs a coordinated cross-project change every week, or for a fifth of their work, the line is in the wrong place.

**What this operation does to every other one in this document:** once a boundary exists, an operation whose consumers are on the far side is no longer a restructure. It is an interface change, and it goes through `dbt-breaking-changes` with a version and a deprecation window.

## Choosing a materialization for an intermediate node

Restructuring constantly creates new intermediate models, and their materialization is the decision most often made by copying the neighbour. The choice between `ephemeral`, `view`, and `table` — how they differ, the ephemeral claims to distrust, a practical default, and the deep-chain guardrails — is in [intermediate-materialization.md](intermediate-materialization.md).


## When not to restructure

Restructuring has a real cost: a diff to review, a rebuild, a window where consumers are in motion, and a risk of silent regression. Some structures that look wrong are not.

| Looks like a problem | Often is not |
|---|---|
| A long model | Length is not a defect. Split only when something else would reference the extracted part. |
| Many CTEs in one model | A layered import/logic/final CTE structure is a readability convention, not a symptom. |
| A wide mart | Wide final models exist so consumers do not join. That is the design working. |
| Fan-in at one intermediate | Several models joining at one place is good structure. Do not push the join into each consumer. |
| A single-consumer intermediate | It may be the documented, tested, inspectable definition of an entity. Check for tests before collapsing it. |
| An orphan model | Terminal models have no dbt consumers **by design** — check `layers[].terminal` before calling one unused. It may have BI consumers instead. |
| A `table` in the middle of a chain of views | Usually a deliberate performance decision. Read the config and, where possible, the run times before removing it. |
| A deep chain of views | Costs nothing at build time. Only a problem if reads are slow, and then materializing one link beats deleting models. |
| Duplicated-looking logic in two models | Diff it character by character first. If the copies differ, "deduplicating" them is a behaviour change for at least one consumer. |

Genuine problems, worth restructuring for:

- A mart or intermediate model referencing a source directly, skipping the staging layer entirely.
- A model referencing both a `ref()` and a `source()`, so half of it reads cleaned data and half reads raw.
- A source with more than one direct child, so the same light transformation happens twice and can diverge.
- A model referencing something its layer is not permitted to reference, per `may_reference`.
- A staging model referencing another staging model, or referencing something downstream of itself.
- The same non-trivial logic duplicated in three or more models, verified identical.
- A chain of single-CTE, single-consumer models adding hops and nothing else.
- A model referencing something in a `terminal` layer.
- A hardcoded `database.schema.table` anywhere in `models/` — it breaks lineage and reads the same relation in every environment.
- A model with no parents, which is the same defect seen from the other side.

If none of these apply, the strongest option is often to leave the DAG alone and say why.

## The one-model-at-a-time rule

Restructuring is compounding. Two simultaneous reshapes produce a diff where any regression has two possible causes and neither can be isolated. One operation, verified, committed. Then the next.

The same rule bans bundling a restructure with anything else: no renames, no new columns, no config changes, no reformatting. Each of those alone is defensible; combined with a reshape, the comparison that would have proven the reshape safe no longer means anything.

## Verifying the whole DAG, not just the model

A restructure can leave the target correct and the DAG wrong.

```bash
dbt parse                         # cheapest check that no cycle was introduced
dbt compile                       # whole project — catches refs you did not grep for
dbt build --select <model>+       # the model and everything downstream
dbt ls --select <model>+          # confirm the shape is what you intended
dbt ls --select <model>+ --output config   # confirm materializations are what you intended
```

Whole-project compile is the step most often skipped and the one that catches the reference you missed. It costs seconds. `dbt parse` is cheaper still and is the check that fails on a cycle, which is the one restructuring mistake that cannot be shipped.

Where CI compares against a stored production manifest, one more check is worth running before merge:

```bash
dbt ls --select "state:modified+" --state <path/to/prod/artifacts>
```

That is the set of nodes your reshape actually affects, as dbt computes it rather than as you believe it. A node in that list you did not expect is a finding. Note two limits: the comparison needs a previous manifest, and it will not flag a change that lives only in a `var` or `env_var` value, because dbt cannot trace that lineage.

Evidence standards, `audit_helper` invocation, and what counts as proof: see `dbt-verification` and `dbt-refactoring-safely`. Do not restate a comparison you did not run.

## Completion checklist

- [ ] The defect named, with the command or count that detected it — not "the DAG was messy"
- [ ] Contract read; layer legality of every new edge confirmed, or generic guidance labelled as generic
- [ ] Consumer list built with both `ref()` quote styles across `models/`, `tests/`, `macros/`, **and** with `dbt list --select <model>+`
- [ ] Non-dbt consumers considered for any model being absorbed or deleted, not just `ref()` consumers
- [ ] Baseline captured before the first edit
- [ ] New models created *before* old ones were modified or deleted
- [ ] `dbt parse` run after each rerouting step — no cycle introduced
- [ ] Project compiled at every intermediate step, not just at the end
- [ ] Compiled SQL diffed where the change was meant to be structural only
- [ ] Row-level comparison run; zero differing rows, or the difference explained
- [ ] Grain and row count verified at each new seam, not only at the far end
- [ ] Tests and YAML migrated to the model that now owns each column
- [ ] Materialization chosen deliberately for every new node, with the ephemeral/view/table trade-off stated
- [ ] For a materialization change: object type, grants, and freshness semantics all checked on the new object
- [ ] For a layer move: the physical relation change assessed as a breaking change
- [ ] For an absorbed incremental model: the history consequence stated before combining
- [ ] Old models deleted last, after all consumers were cut over — and their relations added to post-merge drops
- [ ] `dbt compile` on the full project passes
- [ ] Version and adapter dependence stated for anything that has it
- [ ] One operation in this change, nothing bundled

## The failure modes that actually happen

1. **Deleted before rerouted.** The old model's file is removed while a consumer still references it. Everything downstream fails to compile, and the branch is unusable until it is fixed. Always delete last.
2. **A consumer missed.** The grep used one quote style, or skipped `tests/` and `macros/`, or the reference was built inside a macro where no grep could see it. Whole-project compile catches this; a targeted compile does not.
3. **Grain drift at a new seam.** The extracted or inserted model aggregates marginally differently, a downstream join fans out, and row counts move a few percent. No test fails, because no test asserts the grain. Compare counts at the seam.
4. **Tests left on the wrong model.** After a split, a `unique` test sits on a model that no longer produces that column — or worse, still passes trivially while the column it was protecting is now untested elsewhere.
5. **Flattening a deliberate materialization.** A table in the middle of a view chain was a performance decision. Collapsing it makes every downstream build recompute the expensive part. Read the config before calling a hop unnecessary.
6. **"Deduplicating" logic that was not identical.** The three copies differed in a filter or a null handling. Extraction silently changed the answer for two consumers, and the comparison against baseline was rationalized as "the fix".
7. **An incremental model absorbed into a table.** Its accumulated history is gone, and if the source cannot reproduce the past it is unrecoverable. This is not a combine; it is a materialization change with a data-loss decision inside it.
8. **A restructure that deleted the only queryable copy.** The absorbed model was how someone debugged the pipeline, or was read directly by a notebook. `grep` over `models/` proved nothing about either.
9. **Ephemeral chosen for tidiness, then found to be untestable and unqueryable.** No relation means no direct query, no grants, no hooks, no contract constraints, and unit-test inputs that must be raw SQL.
10. **A materialization change that dropped grants.** The build was green, the object type was right, and a consumer lost access — surfacing later as a permission error unconnected to the change in anyone's mind.
11. **Bundling.** A reshape shipped with a rename or a logic tweak. The comparison proves nothing, review is impossible, and a revert undoes more than intended.
12. **A project split attempted on dbt Core.** Cross-project references need a paid platform tier; without one the projects talk through sources, losing lineage, access checks, and cycle detection. Worse than the monolith it replaced.
