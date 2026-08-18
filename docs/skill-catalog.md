# Skill catalog

What each skill covers and which `conventions.yml` fields it reads. Use this to check whether a task has coverage before writing a new skill, and to see what a skill will consult in your contract before you fill it in.

Organized by tier. See [`skill-architecture.md`](skill-architecture.md) for why the tiers exist and how the lifecycle phases order them.

A 27th skill, `dbt-navigating-skills`, is not listed here: it is the router that decides which of the others to read for a given task, so it is described in [`skill-architecture.md`](skill-architecture.md) rather than catalogued as a work type of its own.

---

## Tier A — Foundations (7)

### `dbt-onboarding-to-a-project`
Reads: `project.*`, `orchestrator.*`, `bi.*`

The cold-start measurement pass: DAG shape, terminal versus dead nodes and the three-way dead-model test, orchestrator and BI discovery, dbt version and adapter detection, test-coverage concentration, git activity, and which patterns are grandfathered rather than exemplary. Owns version detection so five other skills can read a resolved version instead of each re-deriving it. Defers naming inference to `dbt-project-conventions` rather than duplicating it. Ends with a do-not-do-on-arrival section, because the most expensive thing an agent does in a new repository is "clean up" a pattern it has not yet understood.

### `dbt-deriving-project-context`
Reads: writes all of it — `conventions.yml`, `context.mechanisms`, `context.domain_notes`, `context.references`

The install-time pass that produces what every other skill consumes, which makes it the one place where a guess is most expensive: a wrong contract is schema-valid, reads as authoritative, and silently misinforms every task after it. Measures the taxonomy with counts and records how each was measured, so the next reader can re-verify instead of trusting. Finds the bespoke machinery an agent would otherwise hand-roll — including dbt built-in overrides, which are invisible to grep because dbt calls them automatically, and CI-generated artifacts, which invert a skill's advice from *write this* to *do not hand-edit this*. Reads git and PR history for intent, since present state shows a pattern exists while history shows whether it was chosen.

Owns the **appraisal axis** nothing else in the library covers: every finding is assigned best practice, deliberate variant, or defect, using a four-question test rather than an opinion. Prioritises best practice by default but prefers a demonstrably better local variant — a team that solved a problem you have not hit is better informed about their project than a general rule is. Complements `dbt-onboarding-to-a-project`, which dates a deviation to decide whether it is *deliberate*; this skill judges whether it is *good*. Enforces unset-over-guessed on the fields that cause the worst errors when wrong (dbt version, query-log retention, sensitivity classification, where a missing value means unclassified and never safe), and separates schema validity from factual truth.

### `dbt-designing-a-model`
Reads: `layers`, `naming`, `project.warehouse`

Everything that must happen before any SQL is written: eliciting the real question when a request arrives as an output rather than a dataset ("I need a dashboard that…"), checking whether an existing model already answers it, fixing the grain in writing, choosing between fact / dimension / wide table / bridge, picking the SCD type, and classifying measure additivity. Prevents the most expensive class of error in this library — a technically flawless model at the wrong grain.

### `dbt-gathering-context`
Reads: `project.warehouse`, `bi.consumers`, `environments`

The derive-versus-ask discipline that precedes every other skill: a derivation ladder from repo to warehouse to orchestrator to human, a matrix classifying the 15 recurring questions as derivable / partial / must-ask, and the six question classes no tool can answer (intent, threshold, semantics, tradeoff, scope, consequence tolerance).

Requires proving an instrument can detect a presence before treating an absence as evidence — a catalog integrated with one BI tool and blind to another returns an identical, confident empty list for both. Deliberately does *not* let the contract enumerate connected tools: a hand-maintained integration inventory rots into false confidence, so capability is discovered at runtime.

### `dbt-project-conventions`
Reads: all of `conventions.yml`

Naming, prefixes, layer rules, and which layer may reference which. Also owns contract inference — deriving the contract from a repository that has none — which makes it the entry point for a new install.

### `dbt-environments`
Reads: `environments.*`

Dev versus prod detection, `ref()` resolution and its silent production fallback, and why validation queries must not use `ref()`.

### `dbt-verification`
Reads: nothing

How to prove a change did what you claim: compile, row-count reconciliation, `audit_helper` comparison, and checking the compiled SQL rather than trusting the model file. Owns the evidence ladder every operation skill ends by referencing, so the standard for "done" is defined once.

---

## Tier B — Artifacts (9)

### `dbt-authoring-sql-models`
Reads: `layers`, `naming`, `sql_style`, `project.warehouse`

CTE structure, the import / logical / final pattern, where `select *` is acceptable, and join and null handling. Layer-specific guidance lives in sub-documents (`staging.md`, `intermediate.md`, `marts.md`) so a task loads only the layer it concerns.

### `dbt-authoring-schema-yaml`
Reads: `naming.yaml_file_pattern`, `testing`

Descriptions that state entity and grain, column documentation, and model contracts and constraints. Sub-documents cover data tests, exposures, and governance (`access`, `group`, versions).

### `dbt-unit-tests`
Reads: `naming.yaml_file_pattern`, `testing`

Unit tests (dbt 1.8+) — fixture-based verification of model logic on constructed inputs, as distinct from data tests that assert on real rows. Carries the incremental case, where overriding `is_incremental` is the only reliable way to test a boundary predicate against a constructed edge case.

### `dbt-incremental-models`
Reads: `project.warehouse`, `testing.primary_key_incremental`

`merge` versus `delete+insert` versus `append`, unique keys, boundary predicates, and `full_refresh=false`. The highest-consequence skill in the library: this is where teams silently lose or duplicate data. Sub-documents cover strategy selection, boundary patterns, lateness, mutable sources, schema evolution, microbatch, backfilling, and testing.

### `dbt-snapshots`
Reads: `naming`

SCD2 history capture, `check` versus `timestamp` strategies, and why a snapshot should point at a source rather than a model.

### `dbt-macros`
Reads: nothing

Authoring, Jinja mechanics, dispatch and adapter resolution, and when *not* to write a macro.

### `dbt-python-models`
Reads: `project.warehouse`

When a Python model earns its cost, platform differences across warehouses, and the testing and debugging story. Sub-documents cover the cost decision and platform reference.

### `dbt-sources-and-seeds`
Reads: `naming`

Source YAML, freshness configuration and monitoring, and when a seed is the right answer versus a source.

### `dbt-handling-sensitive-data`
Reads: `project.warehouse`, `bi.consumers`

Classification propagation through the DAG, why a masking policy does not follow a `select` into a downstream model, where sensitive values leak outside the warehouse (test-failure tables, seeds committed to git history, PR bodies, agent summaries), and grants.

Owns the honest resolution of the deletion conflict: an incremental model does not drop a row on rebuild, `full_refresh=false` disables the very operation deletion requires, a snapshot retains history by design, and warehouse time-travel can hold rows past the purge. Marks legal and policy decisions and routes them to a human rather than making them.

---

## Tier C — Operations (7)

The tier that justifies the library. Authoring an artifact from scratch is well covered by dbt's own documentation; safely changing a DAG that already has consumers is not.

### `dbt-adding-columns`
Reads: `naming`, `bi.consumers`

The most common task in analytics engineering. Trace the column from source to mart, decide which layers must carry it, update YAML at each, handle the incremental case (a new column is null for existing rows until backfill), and check BI consumers. Includes the decision about how far upstream a calculation belongs, which is a tradeoff with no free option.

### `dbt-unifying-sources`
Reads: `layers`, `naming`, `project.warehouse`

Combining the same business concept from several source systems: union-versus-join shape, conforming each source at its own boundary, vocabulary reconciliation, entity resolution, source drift, and per-source verification.

*Evidence caveat, stated because this library asks the same of its users:* the measurement that prompted this skill — 32 of 320 merged pull requests in one year, 10%, the single largest identifiable task category — is **n=1**, one project over one year. It is the strongest evidence in this repository and it is still a single sample. Multi-vendor unification is inherently general: two systems reporting the same concept with different column names, grains, and timezones is not one team's problem, so the *content* stands on its own. What remains unproven is the *10% frequency*, which likely tracks how many source systems a project has. A project with two sources will hit this rarely; a project with twelve will hit it constantly. Re-measure against a second project before quoting the number as an industry figure.

### `dbt-restructuring-dags`
Reads: `layers`

Decompose one model into several · combine several into one · insert a layer · reroute a `ref()` · flatten a linear DAG. Each with the ordering that avoids a broken intermediate state, and each delegating the equivalence proof to `dbt-refactoring-safely`.

### `dbt-refactoring-safely`
Reads: `sql_style`

Output-neutral change, **proven** rather than asserted. Separates behaviour-preserving from behaviour-changing edits, covers migrating SQL from outside dbt into it, and treats linting and formatting as a step with its own contract dependency.

### `dbt-breaking-changes`
Reads: `bi.consumers`, `naming.banned_prefixes`

Rename, remove a column, change a type or grain, delete a model. Everything with downstream blast radius, including BI consumers and enforced contracts, plus the expand / migrate / contract sequence that makes a rename survivable. Sub-documents cover blast radius and governance mechanisms.

### `dbt-performance-tuning`
Reads: `project.warehouse`

Diagnose before optimizing, clustering and partitioning, converting a table to incremental, and predicate pushdown. Warehouse-specific advice is gated on `project.warehouse` rather than assuming one engine. Sub-documents cover scan reduction, joins, and warehouse layout.

### `dbt-shipping-changes`
Reads: `bi.consumers`, `schedules`

Branch, pull request, merge, then what happens in production — which scheduled jobs pick the change up, whether a backfill is needed, and in what order.

---

## Tier D — Diagnostics (2)

### `dbt-debugging-failures`
Reads: `environments`

Failing test, failing job, compile error, dev/prod divergence. Bisect the DAG, read the compiled SQL, and distinguish a data problem from a code problem. Includes a failure taxonomy for classifying a symptom before acting on it.

### `dbt-data-quality-triage`
Reads: `bi.consumers`

The build is green and the numbers are wrong. Source freshness, incremental boundary gaps, late-arriving data, and timezone mismatches — the failure modes that produce *plausible but wrong* output, which is the expensive kind because nothing alerts.

---

## Tier E — Reference (1)

### `dbt-command-reference`
Reads: `project.dbt_project_name`

Selection syntax, flag ordering, `dbt show`, state comparison and CI selectors, and the gotchas that cost real time.

---

## What is deliberately not a skill

Two categories are absent by design rather than by oversight.

**Testing is not a topic.** There is no standalone testing skill, because "testing" is not a task anyone performs. Test authoring belongs with the YAML you are already writing (`dbt-authoring-schema-yaml`), adding tests to an untested model belongs with the change that prompted it (`dbt-adding-columns`), fixing a failing test belongs with diagnosis (`dbt-debugging-failures`), and logic verification on constructed inputs is its own primitive (`dbt-unit-tests`). A standalone testing skill would force an agent to load it when the actual task was authoring YAML.

**BI tooling is not a skill.** Vendor-specific BI guidance would not survive contact with a different stack. What generalises is the *question* — who consumes this, and will my change break them — so it lives as the BI-impact section of `dbt-breaking-changes` and reads `bi.consumers` from your contract.

---

## Works with or without a contract

Every skill states its contract dependency explicitly, and the failure mode is uniform: **absent field → generic guidance, clearly labelled as generic.**

A team that installs this and writes nothing gets a competent dbt agent. A team that adds `conventions.yml` gets one that enforces *their* rules. A team that runs `dbt-deriving-project-context` gets the contract written for them from their own repository, with the count behind each value cited so it can be checked rather than trusted.
