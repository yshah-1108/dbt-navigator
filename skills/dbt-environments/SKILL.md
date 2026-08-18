---
name: dbt-environments
description: Use when building or querying models outside production, writing a validation query, debugging why dev results differ from production, or when ref() returns data you did not expect. Covers environment detection, ref() resolution and its silent production fallback, per-developer schemas, and why validation queries must name the database and schema explicitly.
metadata:
  phase: ship
---

# Environments

Almost every "the numbers are wrong in dev" report is not a logic bug. It is a model reading from a different place than the engineer believes it is reading from.

Two facts cause most of it:

1. `ref()` is **environment-dependent**. The same model file points at different relations depending on the target and on whether deferral is active.
2. Nothing tells you when it resolves somewhere unexpected. Under deferral, an unbuilt model silently reads production and the run goes green.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

Fields that matter here:

| Field | Use |
|---|---|
| `environments.detection.strategy` | Which signal identifies dev — `database_name`, `schema_name`, `target_name`, `env_var`, or `macro` |
| `environments.detection.expression` | The literal Jinja to use in model code. Copy it verbatim; do not paraphrase it |
| `environments.dev.database` / `.schema` | Where your builds land. `<username>` in the schema means per-developer schemas |
| `environments.prod.database` / `.schema` | The comparison baseline for validation queries |

**Absent field → generic guidance, labelled as generic.** If `environments.detection` is missing, do not guess an expression and do not copy one from another project. Determine the actual values from the connection profile (below), state that the project has not declared a detection convention, and prefer a database- or schema-based check over a target-name check for the reason given in the next section.

## 1. Establish where you are, before acting

Do this before any build, any query, and certainly before anything destructive.

```bash
dbt debug                                    # prints the resolved target: database, schema, user, role
dbt show --inline "select '{{ target.database }}' as db, '{{ target.schema }}' as sch, '{{ target.name }}' as target_name" --limit 1
```

The second command is the one that matters, because it shows what Jinja in your models will actually see.

State the answer out loud before proceeding. "Building into `<dev_database>.<dev_schema>`" is a one-line sentence that prevents the entire class of accidents below.

## 2. Detection in model code

When a model needs to behave differently outside production — smaller date range, relaxed assertion, different source — the condition must be reliable.

| Strategy | Reliability | Why |
|---|---|---|
| `macro` | Best, where one exists | The project wraps the condition in its own macro. Call it; do not reproduce its logic. This is the only correct form when the real condition is compound |
| `database_name` | Good | The database is set by the connection, not by a per-developer preference |
| `schema_name` | Good | Reliable, but needs care with per-developer schema prefixes |
| `target_name` | **Fragile** | A target is a local, renameable label. Two developers on the same project routinely have different target names for the same environment, and CI often uses a third |
| `env_var` | Situational | Reliable only if the variable is guaranteed set everywhere, including CI |

```sql
-- fragile: target names vary per developer and per CI configuration
{% if target.name in ('dev', 'ci') %}

-- reliable: derived from the connection
{% if target.database | lower == '<dev_database>' %}

-- best, where the project provides one: call the macro, do not inline its logic
{% if <project_dev_detection_macro>() %}
```

**Where a detection macro exists, calling it is not a style preference.** The real condition is frequently compound — a dev database *or* a production database under a non-production schema, which is what a CI pull-request build usually looks like. Every inline copy of a compound condition is a chance to drop the second clause, and dropping it classifies CI as production. That is the expensive direction: a dev-only guard that does not fire in CI lets CI scan full history, and a production branch that fires there writes production logic against a temporary schema.

If the contract names a macro, read the macro once to understand what it covers, then cite the call and move on.

Substitute the contract's `environments.detection.expression` for the second form. If the contract has no expression, use the database or schema name you observed in step 1 and say which you used.

The failure mode of target-name detection is asymmetric and nasty: it does not error, it just fails to trigger. A dev-only guard that silently does not fire in CI means CI scans full history; a prod-only branch that fires in dev means dev writes production logic against dev data.

### Never pass environment behavior on the command line

A date limit or environment switch supplied as a CLI variable is not reproducible: it lives in one engineer's shell history, not in the repo. It will be absent in CI, absent for the next person, and absent when you re-run the same command tomorrow. Put the condition in the SQL, guarded by the detection expression — a macro is the usual home for it.

## 3. How `ref()` actually resolves

`ref()` does not search for a table. It looks the model up in the manifest and constructs a fully-qualified name from the **current target**, via the project's database and schema resolution macros. Two mechanisms then decide whether you read your own build or production:

| Mechanism | Behavior when the model is not built in your environment |
|---|---|
| No deferral | `ref()` points at your relation, which does not exist. The query **errors**. Loud, and therefore safe |
| Deferral (`--defer --state <path>`) | `ref()` falls back to the state manifest's relation — usually production. The query **succeeds against production data**. Silent |
| Custom database/schema macros | Whatever the project's macros implement. Read them before assuming |

Deferral is genuinely useful — it is what lets you build one model without building its twenty ancestors. It is also on by default in some hosted development and CI environments. Check whether it is active before trusting a result.

### The class of bug this produces

You build a mart in your own schema. Three of its five ancestors exist there from a build two weeks ago; two were never built. The result is a table assembled from:

- two-week-old dev data for three ancestors,
- current production data for the other two,
- and joins across the two, silently, at whatever grain happens to line up.

Every test passes. The row count looks plausible. The numbers are wrong in a way no assertion catches, because nothing in the project asserts "all of my inputs came from the same place at the same time."

This is why "it worked in dev" carries less evidence than it feels like it does.

### Before building, check your ancestors

```bash
dbt ls --select +<model> --resource-type model      # everything this depends on
```

For each ancestor, answer: does it exist in my environment, and how old is it? Anything missing will come from production (with deferral) or fail (without). Anything stale is worse than missing, because it does not announce itself.

If ancestors are missing or stale, choose deliberately and say which you chose:

- build the ancestors first — `dbt build --select +<model>` — accurate, slower;
- accept production ancestors via deferral — fast, and fine when your change is confined to the leaf model;
- rebuild only the stale ones.

The one unacceptable option is not knowing.

## 4. Validation queries: name the database and schema

This is the rule that follows from everything above, and it is one of the universal rules in `AGENTS.md`: a validation query names its database and schema explicitly.

```sql
-- correct: unambiguous, and comparable across environments
select count(*) from <dev_database>.<dev_schema>.<model>;
select count(*) from <prod_database>.<prod_schema>.<model>;

-- wrong: resolves per-environment and per-deferral-state
select count(*) from {{ ref('<model>') }}
```

A validation query exists to answer "what is in this specific table." Using `ref()` reintroduces exactly the ambiguity you are trying to remove — and when it silently resolves to production, you validate production and report it as your change working.

The same applies to inline previews:

```bash
dbt show --inline "select count(*) from <dev_database>.<dev_schema>.<model>" --limit 1
```

### The relation is named by its alias, not its filename

`ref('<model>')` looks up the model by filename, but the physical table it resolves to is named by the model's `alias` config when one is set — and by the filename only when it is not. So the filename and the table name are not guaranteed to be the same string. An agent that constructs a validation query by pasting the filename into `<database>.<schema>.<filename>` will, on any aliased model, query a relation that does not exist — a "does not exist" error if you are lucky, and a stale table from a prior name if you are not.

Before naming a relation directly, confirm the physical name rather than assuming it equals the filename:

```bash
# the compiled SQL shows the fully-qualified name ref() actually produced
dbt compile --select <model> && grep -A2 'from' target/compiled/**/<model>.sql
```

The same gap appears with custom schema-generation macros (a model in a `staging/` folder may materialize into a schema named by config, not by folder) and with `database` overrides. The compiled relation name is the authority; the filename is a hint.

## 5. Per-developer schemas

Most projects give each developer their own schema, commonly derived from a username, so concurrent work does not collide. Read the pattern from `environments.dev.schema`; a `<username>` placeholder is the signal.

Consequences worth holding in mind:

- **Never hardcode a developer's schema** in committed code, a test, or a macro. It will be wrong for everyone else and for CI.
- Ad-hoc validation SQL is the exception: there, the concrete name is the point. Keep it out of the repo.
- Two developers can see different results for the same model and both be correct. Compare against the same baseline before concluding one is broken.
- A schema you have not built into is empty, not broken.

## 6. Incremental models in a fresh environment

An incremental model has no history in a schema where it has never been built. The first build must be a full refresh, and a full refresh over full history can be very expensive.

Practical sequence:

1. First build: `dbt build --full-refresh --select <model>` (note the flag position — see `dbt-command-reference`).
2. Constrain the range with the project's dev-guard condition, not a CLI variable.
3. Subsequent builds without `--full-refresh` exercise the incremental branch — which is different code, and is the code that runs in production.

Both branches need testing. A model can pass its first full-refresh build and be broken on every subsequent run, because the `is_incremental()` branch is only exercised the second time.

Corollary: a dev table constrained to a small window will look nearly empty. That is the guard working, not a bug — do not "fix" it by removing the guard.

## 7. Temporary filters are a commit hazard

Hardcoding a narrow date range to iterate quickly is legitimate. Leaving it in is a production incident that passes review, because the diff looks like a `where` clause.

If you add one, mark it unmistakably:

```sql
-- TEMPORARY DEV FILTER — REMOVE BEFORE COMMIT
where <date_column> >= <literal>
```

Before committing: remove it, confirm the permanent guard is in place, compile, and read `git diff` specifically looking for literal dates. See `dbt-verification`.

## Completion checklist

- [ ] Current database and schema established and stated before acting
- [ ] Detection expression taken from the contract, or its absence stated and a database/schema-based check used
- [ ] Deferral status known — you know whether an unbuilt `ref()` errors or reads production
- [ ] Ancestors enumerated; missing or stale ones handled by an explicit decision
- [ ] Validation queries use explicit database and schema, never `ref()`
- [ ] No developer-specific schema hardcoded in committed code
- [ ] Incremental models exercised on both the full-refresh and the incremental branch
- [ ] Temporary dev filters removed, verified in `git diff`

## The failure modes to watch for

1. **Mixed lineage** — some ancestors from your environment, some from production, joined without complaint. Tests pass, numbers are wrong. Enumerate ancestors first.
2. **Detection that never fires** — `target.name` based, and the name differs in the environment that mattered. Verify the guard is active, do not assume it.
3. **Validating the wrong table** — `ref()` in a validation query resolved to production; the report says the change works and the change was never measured.
4. **Stale dev data presented as current** — a table built weeks ago still exists and still answers queries. Age is invisible in a `select`.
5. **A temporary filter shipped** — the model runs in production against three days of data and nothing errors.
