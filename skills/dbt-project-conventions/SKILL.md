---
name: dbt-project-conventions
description: Use when naming a new dbt model, choosing a prefix or layer, reviewing whether a name follows project convention, or authoring, validating, or refreshing the conventions.yml contract itself — including the first install into a project that has none, and any conflict between what the contract claims and what the repository does. Owns the contract artifact; reads the project's own taxonomy rather than imposing a fixed scheme.
metadata:
  phase: orient
---

# dbt Project Conventions

## The contract, not the convention

This skill enforces **your** taxonomy. It does not ship one.

Read `conventions.yml` from the project root (or `.dbt-agent/conventions.yml`). Everything below operates on what that file declares. If the file is missing or a field is absent, say so and fall back to the generic principles at the bottom — never substitute another team's prefixes.

```bash
# Locate the contract before answering any naming question
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

**If there is no contract:** tell the user, offer to generate one by inference (see [inferring-conventions.md](inferring-conventions.md)), and answer using generic principles only. Do not guess at prefixes.

### An absent field is not a default

This is the organizing principle of the whole artifact, and it is the thing most often got wrong. Every field in the schema is optional, so a contract has three states per field, not two:

| State | What it means | Correct behavior |
|---|---|---|
| Field present, with a value | The project has declared a rule | Enforce it, and cite the field when you do |
| Field absent | **This project has no convention here** | Fall back to generic guidance and *label it generic* |
| No contract at all | Nothing has been declared about anything | Generic guidance throughout, and offer to derive one |

The failure mode is filling an absent field with a plausible value — from another project, from a blog post, from the majority of dbt repositories. That produces confident enforcement of a rule nobody chose, and it is worse than silence, because the user cannot tell your invention from their policy. **When a field you need is absent, name the field, say what you cannot decide because of it, and give the generic answer with that label attached.**

The inverse also holds and is easier to miss: an absent field is not permission to do whatever you like either. `sql_style.allowed_join_types` being unset does not license a `right join` — it means the project has not ruled on joins, and generic guidance still applies.

## Naming a new model

1. **Determine the layer** from where the model sits in the DAG, then read that layer's `prefixes` from the contract.

   ```
   layers:
     - name: staging
       prefixes: ["stg_"]
   ```
   One prefix → use it. Multiple → pick by the distinction the contract documents in `model_pattern`, and if that is ambiguous, ask rather than guess.

2. **Build the name** from `naming.model_pattern`. A common shape is:
   ```
   <prefix><source_or_domain>__<entity>[_<grain>][_<timezone>]
   ```
   The middle segment is the part teams most often get wrong. The rule that generalizes: **the segment must add information the folder path does not already carry.** If the model lives in `models/marts/finance/`, then `finance` in the name is redundant — use the business category or entity instead.

3. **Apply the suffix rules.** If `naming.timestamp_column_suffix` is set, it applies to timestamp *columns*. Only append a timezone token to a model name when `model_pattern` includes a `<timezone>` segment.

4. **Check `naming.banned_prefixes`.** These are disallowed on new models but expected on existing ones. Never rename an existing model to satisfy this rule without an explicit request — a rename breaks every downstream `ref()` and BI reference.

## Reviewing an existing name

Compare against the contract and report only real divergences. Before flagging anything, check whether the name predates the convention:

```bash
git log --diff-filter=A --format=%ad --date=short -- <path> | tail -1
```

A model added before the convention existed is legacy, not a violation. Say which it is — reporting grandfathered names as errors is how teams learn to ignore a linter.

## Deviations are data, not errors

A taxonomy with zero exceptions is a taxonomy nobody uses. When a name diverges:

1. Check whether the deviation is **documented** — first in the contract's `deviations[]`, then in a comment, the model's YAML, or the PR that introduced it. A match in `deviations[]` closes the question: it is intentional and not a finding. If it carries an `expires` date that has passed, that is worth raising as debt now due, not as a naming violation.
2. Check whether it is **widespread** — if 30% of models share a "deviation," the contract is stale, not the models. Report that the contract needs updating rather than flagging 200 files.
3. Only flag a name as wrong when it is both undocumented and rare.

Measure before asserting:

```bash
# Distribution of actual prefixes, to compare against the contract
find models -name '*.sql' -exec basename {} \; | sed -E 's/^([a-z]+_).*/\1/' | sort | uniq -c | sort -rn
```

If the dominant prefixes are not in the contract, the contract is wrong. Fix it first.

> **Two fields exist for exactly this.** `deviations[]` records accepted departures — a `pattern`, a `reason`, and an optional `expires` that separates temporary debt from a permanent decision. An entry there means *we know, it is intentional, stop reporting it*, and a name matching one is never a finding. `verified_at` records when the contract was last checked against the repository, which is what lets you weigh it: a contract verified last week and one verified two years ago make identical claims with very different reliability. Both are hand-entered assertions and can themselves be stale, so treat a distant `verified_at` as a warning and a recent one as no guarantee about any specific field. Observed reality still wins on a conflict.

## Generic principles (used when no contract exists)

These hold regardless of taxonomy and are safe to apply without configuration:

- **A name states what the row is, at what grain.** `orders_daily` beats `orders_agg_v2`.
- **Prefix encodes layer; the rest encodes meaning.** Readers should infer the DAG position from the prefix alone.
- **Do not repeat the folder path in the name.** The path already carries the domain.
- **No version suffixes on models.** `_v2` means the DAG has two truths; migrate and delete instead.
- **No abbreviations that are not already project vocabulary.** `cust` saves four characters and costs every future reader a lookup.
- **Timezone belongs in the name only when the same entity exists in multiple zones.** Otherwise it is noise.
- **Grain suffixes only when multiple grains coexist.** If there is one `orders` table, do not call it `orders_detail`.

## Renaming

A rename is a breaking change. Before proposing one:

1. Find every reference: `grep -rn "ref('<old_name>')" models/`
2. Check BI consumers listed under `bi.consumers` in the contract.
3. Prefer an alias or a view over a rename when downstream breakage is wide.

Never bundle a rename with a logic change — if the rename is reverted, the logic goes with it.

---

## The sections, by consequence

The schema documents what each field *is*. What follows is what **breaks** when it is absent, wrong, or stale — which is the part that decides whether filling it in is worth anyone's afternoon.

If a team fills in nothing else, fill in these three:

1. **`project.warehouse`** — decides which SQL is *legal*, not merely idiomatic. Wrong or absent, and every dialect-gated recommendation in the library is a coin flip.
2. **`environments.detection`** — decides whether an agent can tell dev from prod. Wrong, and a command intended for dev runs against production.
3. **`layers[]`** — decides which folder is which layer, which prefix belongs where, and which models nothing may reference. Absent, and naming, layering and blast-radius reasoning all degrade at once.

Everything below is ordered by that logic rather than by the schema's key order.

| Section | Absent | Wrong or stale |
|---|---|---|
| `project.warehouse` | Dialect-specific guidance must be withheld and the withholding stated. The adapter can be read from the profile instead — see `dbt-onboarding-to-a-project` | Advice is either rejected by the engine (recoverable) or **accepted and inert** (not recoverable). The adapter dbt actually runs always wins over the declared value |
| `verified_at` | You cannot tell a contract measured last week from one measured two years ago, so every field has to be re-derived to be trusted | Reads as freshly checked when it is not. A recent date is not evidence about any particular field — it is a hand-entered claim, and observed reality still wins |
| `deviations[]` | Every grandfathered name looks identical to a fresh violation, so the same handful get re-litigated each session | Worse than absent: a deviation removed from the codebase but left here permanently silences a check that should now fire. An `expires` date in the past is debt now due, not a naming error |
| `project.dbt_project_name` | Every metadata call needing a node ID has to derive it from `dbt_project.yml` first | Node IDs resolve to nothing, and the API returns a well-formed empty result that reads like "this model has no consumers" |
| `project.timezone` | Date-boundary reasoning is unanchored; a "daily" grain has no defined day | Reported day boundaries are silently offset. Nothing errors; totals just disagree with the business's |
| `project.dbt_version` | Version-gated features cannot be recommended without shelling out. Cheap to fix — one command | Rots **detectably**: `dbt --version` contradicts it immediately. The low-danger stale field |
| `project.query_history_relation` | Dead-model and consumer claims run on two of their three tests. That must be stated as a limitation, not smoothed over | Queries error against a missing relation, or worse, succeed against a similarly-named one that means something else |
| `project.query_history_retention_days` | No lookback window is defensible, because you cannot know the log reaches back that far | **The dangerous one.** A 30-day claim drawn from a 7-day log is false, not merely weak, and it is the sentence that gets a live model deleted |
| `environments.detection` | An agent cannot reliably tell dev from prod at runtime | A dev command runs against production. Highest-consequence single field in the contract |
| `environments.dev` / `prod` | Target schemas cannot be verified before a build; a shared schema must be assumed | A build lands somewhere nobody expected, in a schema whose grants nobody checked — see `dbt-handling-sensitive-data` |
| `layers[]` | No layer can be determined from a path, so naming, layering and materialization guidance all degrade together | Models get named for the wrong layer and materialized against the project's actual practice |
| `layers[].terminal` | A model with zero children is ambiguous: correctly-designed endpoint, or abandoned. Do not classify it | A genuinely terminal layer left unmarked turns every report mart into a false dead-model candidate |
| `layers[].may_reference` | The no-layer-skipping rule cannot be checked. dbt compiles illegal edges happily | Legal edges get flagged, or illegal ones pass. Either teaches people to ignore the check |
| `naming.*` | Generic naming principles only. Never substitute another project's prefixes | Every new model is named to a taxonomy the codebase does not use, and the divergence compounds file by file |
| `naming.banned_prefixes` | New models can reuse a prefix the team abandoned. **Not inferable** — a deprecated prefix looks identical to a current one | Grandfathered models get reported as violations, which is how a convention check loses its audience |
| `schedules.default_tag` | A globally-inherited tag reads as a per-model decision, and agents add it redundantly | Models are tagged for a cadence nobody honors, or the genuinely non-default ones stop standing out |
| `schedules.tags[]` | Cadence intent is unreadable from the repo | **Rots fastest.** A `cron` recorded here is a hand-maintained copy of live state; the orchestrator is authoritative |
| `testing.*` | Test recommendations are generic rather than this project's policy — acceptable, and honest if labelled | Recommended tests conflict with what the project runs; on incremental models the wrong policy is also expensive |
| `bi.consumers[]` | BI repositories cannot be enumerated. Report BI impact as an explicit **gap**, never as "no consumers" | A rename is cleared against a stale repo list, and a dashboard breaks the morning after the merge |
| `bi.use_exposures` | Cannot tell a missing exposure from a project that never adopted them | `true` on a project that abandoned exposures makes a clean exposure sweep read as proof of no consumers. It never was |
| `sensitivity.*` | An agent cannot tell an unclassified column from a project that classifies nothing, so it must ask on every sensitive-looking column. Absence of a tag is never evidence a column is safe | `levels` out of order breaks the `max_classification` comparison; `warehouse_policy_is_authoritative: true` where the warehouse enforces nothing turns a YAML claim into a false guarantee |
| `layers[].max_classification` | Sensitive-column sprawl is checkable only when someone chooses to look | Set too permissively, it certifies the sprawl it was meant to catch. Interpreted against `sensitivity.levels`, so a stale level list breaks it silently. See `dbt-handling-sensitive-data` |
| `sql_style.*` | Genuinely contested style is left alone. This is the safest section to omit | `group_by_style: all` on a warehouse without the auto-inference form produces SQL the engine rejects — or, on Trino, silently groups differently |
| `orchestrator.type` | "Nothing schedules this" and "scheduling lives in a repo you cannot see" look identical from inside the repo, and have opposite implications | `none` recorded where orchestration is merely external is the worst value in the enum: it converts an unknown into a confident wrong answer |
| `orchestrator.config_path` | Discovery greps run again every session | Points at a path that moved; the grep returns nothing and reads as absence |

Two structural notes. **`version` is fixed** at `1` — it identifies the contract's shape, not the project's, so do not bump it to signal a project change. And the schema deliberately has **no field for crons as the source of truth**: `schedules.tags[].cron` is optional and marked as non-authoritative precisely because a hand-maintained duplicate of scheduling state rots silently. Read `orchestrator` as "enough to stop a discovery grep," nothing more.

## Authoring a contract for a project that has none

**For a first install, [`dbt-deriving-project-context`](../dbt-deriving-project-context/SKILL.md) is the fuller procedure** — it wraps this section with the machinery sweep, the git-history pass, and the appraisal step that decides whether each measured practice is best practice, a better local variant, or a defect. Use the steps below when you only need the contract's fields filled in; use that skill when installing into a project for the first time.

The default order, cheapest and highest-value first. Stop and ship after step 3 if that is all the time available — a three-field contract that is true beats a complete one that is aspirational.

| # | Fill in | How you get it |
|---|---|---|
| 1 | `version`, `project.dbt_project_name`, `project.warehouse`, `project.dbt_version` | Measured. `dbt_project.yml`, the profile, `dbt --version` |
| 2 | `environments.dev` / `prod` / `detection` | Measured from the profile plus how the codebase *already* detects dev |
| 3 | `layers[]` — `name`, `path`, `prefixes`, `materialization` | Measured. Prefix distribution per folder |
| 4 | `naming.model_pattern`, `separator`, `timestamp_column_suffix`, `surrogate_key_column`, `yaml_file_pattern` | Measured, with an adherence threshold |
| 5 | `testing.*` | Measured from the manifest where possible |
| 6 | `bi.consumers[]`, `bi.use_exposures`, `orchestrator.*`, `schedules.*` | **Mostly asked.** Not visible from inside the repo |
| 7 | `layers[].terminal`, `layers[].may_reference`, `layers[].max_classification`, `naming.banned_prefixes`, `sensitivity.*`, `sql_style.*` | **Asked.** These are decisions, not observations |

**Mechanics of the measurement live in [inferring-conventions.md](inferring-conventions.md).** Run it, present the draft, and let the user correct it. Do not hand anyone an empty YAML file, and do not present an inferred value as confirmed.

### Which fields are decisions, not observations

Four cannot be measured even in principle, and guessing at them is how a contract acquires its first lie:

- **`naming.banned_prefixes`** — a legacy prefix and a current one are byte-identical in a filename. Only the team knows which are closed. Present the distribution and ask.
- **`layers[].terminal`** — zero children is a fact; *should have* zero children is a design intent.
- **`layers[].may_reference`** — the DAG shows which edges exist, never which are permitted. An existing edge may be the violation.
- **`orchestrator.type: none`** — never infer this from the absence of config files in this repo. Absence of evidence in one repository is not evidence of absence. Use `unknown` until confirmed; that is what the value is for.
- **`sensitivity.levels` and `layers[].max_classification`** — a classification vocabulary and a per-layer ceiling are policy, and a wrong ceiling certifies exactly the sprawl it was meant to catch. Grep for how the project already expresses sensitivity before proposing a vocabulary, and route the ceiling to whoever owns the data. `dbt-handling-sensitive-data` covers the consequences.

`sql_style` is a near-fifth: the codebase shows a tendency, and the team decides whether the tendency is a rule. Gate `group_by_style: all` on `project.warehouse` — `GROUP BY ALL` meaning "group by every non-aggregated column in the select list" exists on Snowflake, BigQuery, Databricks, DuckDB, and Redshift. Postgres does not have it. Trino is the dangerous case: `GROUP BY ALL` is valid syntax there but `ALL` is a grouping-set duplicate modifier, not column inference — the auto-inference keyword on Trino is `GROUP BY AUTO`, so recording `group_by_style: all` against Trino produces SQL that runs and silently groups differently than intended. Confirm the keyword against the warehouse's own docs before recording it; an engine that lacks the auto-inference form either rejects the statement or, worse, accepts it with a different meaning.

### Record what the project does, not what someone wishes it did

This is the tension that decides whether the contract helps or hurts.

A contract stating an aspiration is not merely inaccurate — it makes **every skill that reads it wrong in a confident way**. Declare a taxonomy the codebase does not follow, and the naming check flags most of the project, the layer check reports legal edges as violations, and a reviewer learns within a day that this tool's output needs discarding. A missing field degrades to labelled-generic guidance. An aspirational field degrades to confident noise, which is strictly worse.

So when the team's intent and the repository's behavior disagree, **record the behavior** and note the intent beside it in a comment. Two specific cases worth naming:

- A project detecting dev via a target name has a fragile setup. Record `strategy: target_name` anyway, note the fragility once, and do not silently write a database-name expression that the codebase does not implement.
- A pattern the team is migrating *toward* is not the convention until the codebase mostly follows it. Record the current state; put the target in a comment. Below roughly 70% adherence it is a tendency, not a rule.

Adopting a genuinely new convention is a real thing teams do. It is a decision made explicitly, applied to new work, and recorded in the contract *with* the grandfathering acknowledged — not smuggled in by describing the codebase inaccurately.

## Validating a contract

The repository ships `schema/conventions.schema.json`. Validate against it after any edit:

```bash
python3 - conventions.yml schema/conventions.schema.json <<'PY'
import json, sys, yaml
from jsonschema import Draft202012Validator
contract, schema_path = sys.argv[1], sys.argv[2]
schema = json.load(open(schema_path))
doc = yaml.safe_load(open(contract)) or {}
errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(e.path))
for e in errors:
    print("invalid:", "/".join(str(p) for p in e.path) or "<root>", "->", e.message)
unknown = sorted(set(doc) - set(schema["properties"]))
for k in unknown:
    print("unrecognized top-level key:", k)
print(f"{len(errors)} schema error(s), {len(unknown)} unrecognized key(s)")
sys.exit(1 if errors else 0)
PY
```

Needs `pyyaml` and `jsonschema`. Any equivalent validator works; the schema is standard JSON Schema 2020-12.

The unrecognized-key check is separate on purpose. The schema does not set `additionalProperties: false`, so a **misspelled section name validates cleanly** and is then read by nobody — the field looks filled in and behaves as absent, which is the quietest way for a contract to be useless. That check is the only thing that catches it.

### Validation checks shape, not truth

A schema-valid contract can be entirely wrong, and this is the limit to be honest about. The validator confirms that `warehouse` is one of eight strings. It cannot confirm it is *your* warehouse. Everything below passes validation and is false:

- A `warehouse` naming an engine the project does not run on.
- `layers[].prefixes` listing prefixes that appear nowhere in `models/`.
- A `environments.detection.expression` that is syntactically fine Jinja and never evaluates true.
- `bi.consumers[].repo_path` pointing at a directory that no longer exists.
- `query_history_retention_days` larger than actual retention — which turns every lookback claim built on it into a false one.
- A `default_tag` for a job that was disabled two quarters ago.
- A `sensitivity.levels` list whose order does not match the project's actual severity ordering, which silently inverts every `max_classification` comparison built on it.

So run two checks, not one. **Shape** is the command above. **Truth** is cross-checking the values against the repository — the same measurements that produced the contract, re-run:

```bash
# Do the declared prefixes actually occur?
find models -name '*.sql' -exec basename {} \; \
  | sed -E 's/^([a-z]+_).*/\1/' | sort | uniq -c | sort -rn

# Do the declared layer paths exist?
ls -d models/*/

# Does each declared BI repo path exist?
# (run per bi.consumers[].repo_path)
ls -d <repo_path> 2>/dev/null || echo "MISSING: <repo_path>"
```

Cheapest truth check of all, and worth doing every time: `dbt --version` against `project.dbt_version`, and the adapter it reports against `project.warehouse`.

## Contract staleness

**A stale contract is more dangerous than a missing one**, and the reason is structural: skills degrade gracefully around an absent field and trust a present one completely. Absence produces labelled-generic guidance. Staleness produces confident enforcement of something that is no longer true, and nothing in the system is looking for it.

### What rots fast, what rots detectably

| Rots | Fields | Why |
|---|---|---|
| **Fast, and silently** | `schedules.tags[].cron`, `schedules.default_tag`, `orchestrator.*`, `environments.*`, `bi.consumers[].repo_path`, `bi.consumers[].status` | Hand-maintained copies of state that lives somewhere else and changes without touching this repo. Nothing here errors when it drifts |
| **Slowly, and detectably** | `project.dbt_version` | `dbt --version` contradicts it the moment you look. Cheap to catch, cheap to fix |
| **Slowly, and semi-detectably** | `layers[]`, `naming.*`, `testing.*`, `sensitivity.*` | Drift as the codebase drifts, and the prefix distribution shows it. Visible to anyone who measures |
| **Barely** | `project.dbt_project_name`, `project.warehouse`, `sql_style.*` | Change only on a migration, and a migration is loud |

The pattern: **anything describing schedules or environments rots fast**, because it describes a live system that this repository does not control. Everything describing the repository itself rots at the speed of the repository, which is slow and observable. This is precisely why the schema records cadence tags rather than crons, and why `orchestrator` records a type and a path rather than a schedule.

Deployment and orchestration change is `dbt-shipping-changes`; measuring an unfamiliar project is `dbt-onboarding-to-a-project`. What belongs here is the **artifact**: when a measurement contradicts the contract, the contract is the thing to fix.

### The resolution rule

> **Observed reality wins. Report the conflict rather than silently following either one.**

Concretely, on a conflict:

1. **Act on the observed value** for the task in front of you. The adapter dbt runs decides which SQL is legal; the profile decides where a build lands. The contract's opinion does not change either.
2. **Say so, in the summary, naming the field.** "The contract records `project.warehouse` as X; the adapter is Y, so I used Y." One sentence.
3. **Offer to update the field.** Do not update it as a side effect of an unrelated task — a contract edit buried in a modelling diff gets no review, and the contract is the file that most needs review.
4. **Never edit the codebase to match a stale contract.** That is the inverted failure: the contract is a record of the project, so making the project wrong to make the record right gets the direction of authority exactly backwards.

The one exception to acting on observation: where the conflict is about **intent** rather than fact, observation cannot settle it. If `layers[].terminal` says a layer is terminal and something references it, you have found either a stale field or a real violation, and the DAG cannot tell you which. Report both readings and ask. Same for `may_reference` — an edge existing has never meant an edge is permitted.

### Triggers to re-derive

Not on a calendar, on events: a dbt upgrade, an adapter or warehouse migration, a new BI tool or a retired one, an orchestrator change, a layer added or renamed, a deliberate convention change, and any session where a measurement contradicted the contract. Re-run [inferring-conventions.md](inferring-conventions.md), diff the draft against the committed file, and take the difference to a human.

Cheap habit worth adopting: when a session reads the contract and observes something that contradicts it, **say so in the summary even if the task did not need it.** Staleness surfaces only when someone reports it, and the agent that noticed is the one holding the evidence.

## Completion checklist

- [ ] Contract located, or its absence stated to the user
- [ ] Layer determined from DAG position, not from guesswork
- [ ] Name built from the contract's `model_pattern`
- [ ] Middle segment adds information the folder path does not already carry
- [ ] Checked against `naming.banned_prefixes` (new models only)
- [ ] For a review: checked whether the name predates the convention before flagging it
- [ ] For a deviation: confirmed it is both undocumented and rare before calling it wrong
- [ ] For a rename: every `ref()` and BI consumer found, and the rename kept separate from any logic change

Authoring, validating or refreshing the contract:

- [ ] Absent fields treated as "no convention here" — named to the user, with generic guidance labelled as generic
- [ ] Every value recorded is what the project **does**, not what someone wishes it did; intent kept in a comment beside it
- [ ] Fields that are decisions — `banned_prefixes`, `terminal`, `may_reference`, `orchestrator.type` — asked, never inferred
- [ ] `orchestrator.type` left `unknown` rather than set to `none` on the strength of absent config in this repo
- [ ] Anything dialect-specific gated on `project.warehouse`, `group_by_style: all` included
- [ ] Contract validated against `schema/conventions.schema.json`, and unrecognized top-level keys checked separately
- [ ] Values cross-checked against the repository, since validation confirms shape and not truth
- [ ] `project.dbt_version` and `project.warehouse` checked against `dbt --version` and the adapter
- [ ] Any lookback window stated no longer than `project.query_history_retention_days`
- [ ] On a contract-versus-reality conflict: acted on the observed value, named the field in the summary, and offered the update rather than making it silently
- [ ] No codebase edited to match a stale contract
- [ ] Contract edits kept out of unrelated modelling diffs

## Common failure modes

1. **Imposing a taxonomy the project does not use.** If the contract is missing, generic principles apply — never substitute another project's prefixes.
2. **Flagging grandfathered names as violations.** A model added before the rule existed is legacy. Reporting it as an error teaches people to ignore the tool.
3. **Treating a widespread deviation as 200 individual errors.** If a third of models disagree with the contract, the contract is stale. Fix it first.
4. **Repeating the folder path in the model name.** The path already encodes the domain; the name should add something.
5. **Renaming without tracing consumers.** A rename is a breaking change — see `dbt-breaking-changes`.
6. **Reading an absent field as a default.** Absence means the project has no convention there, not that a common convention applies. Filling the gap with a plausible value produces confident enforcement of a rule nobody chose, and the user cannot tell it from their own policy.
7. **Recording an aspiration instead of the behavior.** A contract asserting a taxonomy the codebase does not follow makes every skill that reads it wrong in a confident way — the naming check flags most of the project, and its output is discarded within a day.
8. **Inferring a decision.** `banned_prefixes`, `terminal`, `may_reference` and `orchestrator.type: none` are not observable. A deprecated prefix looks identical to a current one, an existing edge is not a permitted edge, and no config in this repo does not mean nothing schedules the project.
9. **Treating schema validation as verification.** The validator confirms `warehouse` is one of eight strings, not that it is your warehouse. Shape and truth are two checks.
10. **A misspelled section that validates cleanly.** The schema does not forbid extra properties, so a mistyped top-level key passes and is read by nobody. It looks filled in and behaves as absent.
11. **Trusting a stale contract over an observation.** Skills degrade gracefully around a missing field and trust a present one completely. Observed reality wins, and the conflict gets reported rather than either side silently followed.
12. **Editing the codebase to match a stale contract.** The inverted failure. The contract records the project; making the project wrong to vindicate the record inverts the direction of authority.
13. **Copying live crons into the contract.** Schedules and environments rot fastest because they describe a system this repository does not control. A stale cron reads as authoritative, which is why the schema records cadence tags instead.
14. **Stating a lookback longer than retention.** A 30-day claim from a log retaining 7 days is false, not weak, and it is the sentence that gets a live model deleted. Check `project.query_history_retention_days` first.
15. **Refreshing the contract inside an unrelated diff.** The contract is the file most in need of review and the one most easily waved through when it arrives attached to a modelling change.

