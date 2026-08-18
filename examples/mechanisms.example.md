# Project mechanisms

Bespoke machinery this project provides that a general skill cannot know exists.
This file has one job: **stop an agent solving a problem by hand when the project
already has a sanctioned mechanism for it.**

That is the whole inclusion test, and it is worth applying strictly:

- **Include** a mechanism where a skill's sensible generic default would be
  *wrong* here.
- **Exclude** anything that merely restates advice a skill already gives. A
  duplicate drifts out of sync with the skill and still gets believed.

Some of what belongs here is invisible to a careful reader of the models. An
overridden `generate_schema_name` appears in no model file, and grepping for its
usage returns nothing, because dbt calls it automatically. That invisibility is
exactly why the file is needed.

Delete every section that does not apply. An empty heading is worse than a
missing one — it reads as "this project has no such mechanism," which is a
different and stronger claim than "nobody wrote this down."

---

## Limiting data volume in dev and CI

State the sanctioned mechanism, with a copyable example, and say plainly what
*not* to do instead.

```sql
where 1 = 1
  {{ <your_dev_filter_macro>('<timestamp_column>', 'timestamp') }}
```

Say what it returns in dev versus production, and what its arguments mean. If the
project forbids an obvious alternative — `--vars`, a hand-written date filter, a
hardcoded limit — say so here and say why, because the reason is what makes the
rule stick.

## Detecting dev or CI

If the project has its own detection macro, name it and instruct agents to
**call it rather than reproduce its condition inline.**

Write out the real condition once, so a reader understands what an inline copy
would omit. Compound conditions are the usual case and the usual source of bugs:
a CI pull-request build often runs against the *production* database under a
temporary schema, so a database-only check classifies CI as production.

## Overridden dbt built-ins

List any macro in `macros/` that shadows a dbt built-in — commonly
`generate_schema_name`, `generate_alias_name`, `generate_database_name`.

| Macro | Effect on the target relation |
|---|---|
| `<macro>` | `<what changes, and under what condition>` |

These matter more than their line count suggests: they mean a model's real
target relation is not what a naive reading of `target.schema` predicts.

## Deployment or backfill procedure

If rebuilding production tables follows a specific procedure — blue/green, a
staging swap, a shadow schema — describe the *sequence and its safety defaults*,
not just the macro names. Note especially any environment that looks like a
sandbox but is production-grade, since that misreading is expensive.

## Exposures, tests, or docs that are generated

If any artifact is machine-generated, say so and say **not to hand-edit it**,
naming the generator and where its output lands. Also state the generator's
*coverage*, because a generated record is only authoritative for what it covers —
consumers outside its scope remain invisible.

## Installed packages worth knowing about

Skills often recommend packages conditionally. Listing what is already installed
resolves the conditional.

| Package | State and what it is used for here |
|---|---|
| `<package>` | `<installed; the sanctioned way to do X>` |

## Custom generic tests

| Test | Why it exists rather than the built-in |
|---|---|
| `<test>` | `<the constraint that made the built-in unsuitable>` |

## Linting and formatting

Name the config file if one exists. A committed config, not a general style
preference, is the authority on formatting.

## CI checks that will run on a PR

Knowing these exist changes what is worth doing by hand.

| Workflow | What it catches, or what it automates |
|---|---|
| `<workflow>` | `<the check, and any label or convention that triggers it>` |

Label conventions belong here. A label that triggers an automated handoff is a
mechanism, and one nobody discovers by reading models.

---

## Maintaining this file

Add an entry when a new macro becomes the sanctioned way to do something a skill
would otherwise do generically. Remove one when it stops existing — a documented
mechanism that is gone is worse than an undocumented one, because an agent will
confidently call it.

Include usage counts where they make the case that something is the convention
rather than an option, and record how they were measured so the next reader can
re-check rather than trust:

```bash
ls macros/
grep -rl "<macro_name>" models --include="*.sql" | wc -l
```
