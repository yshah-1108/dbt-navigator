# Blast radius: finding every consumer

The blast-radius check decides whether a change is a one-line edit or a multi-week coordinated migration. This document is the full procedure. The summary in `SKILL.md` is enough for a low-risk change; come here before deleting a relation, renaming anything a person might have typed by hand, or changing a grain.

**The governing principle:** each method below has a class of consumer it cannot see. The output of this check is therefore two lists — what you found, and what you could not look for. A blast-radius report without the second list is not a finding, it is a guess with a table around it.

---

## 1. dbt consumers

### Text search

```bash
# both quote styles, across every directory that can contain a ref
grep -rn "ref('<model>')" models/ tests/ macros/ analyses/ snapshots/ seeds/
grep -rn 'ref("<model>")' models/ tests/ macros/ analyses/ snapshots/ seeds/

# for a column: every file that mentions the name
grep -rn "<column_name>" models/ tests/ macros/ --include="*.sql" --include="*.yml"
```

A single-quote-only grep is the most common way a consumer is missed. The column grep is noisy — a common name matches unrelated models — and that is the correct trade-off: a false positive costs seconds of reading, a false negative ships a break.

Text search misses two constructs that resolve at parse time:

- `ref(<variable>)` or a `ref()` built inside a macro from a string argument. The reference is real and the grep cannot see it.
- A versioned reference, `ref('<model>', v=2)`, which will not match a pattern ending in `')`.

### Graph search, which does not miss those

```bash
dbt list --select <model>+                        # every descendant, transitively
dbt list --select <model>+ --resource-type test    # tests that will start failing
dbt list --select <model>+ --resource-type exposure
dbt list --select +exposure:*  --output name       # everything any exposure depends on
```

The graph is built from the parsed manifest, so it includes references the grep cannot resolve. Run both: the grep finds the file and line you have to edit, the graph proves the set is complete.

Places both methods reach only if you point them there:

| Easy to miss | How to check |
|---|---|
| Singular tests in `tests/` | Included in the greps above; also `dbt list --select <model>+ --resource-type test` |
| Exposures | `--resource-type exposure`; only as complete as the project's exposure coverage |
| A macro that hardcodes a relation instead of using `ref()` | `grep -rn "<physical_table_name>" macros/` |
| `dbt_project.yml` config blocks and `selectors.yml` | `grep -rn "<model>" dbt_project.yml selectors.yml` |
| Orchestrator jobs naming the model in a selector | `orchestrator.config_path` from the contract |
| Semantic models, saved queries, or metrics built on the model | `dbt list --select +semantic_model:* --output name`, and the same for `saved_query` |

### Whether the project's own governance already answers the question

```bash
grep -rn -B2 -A6 "access:" models/ --include=*.yml
grep -rn "group:" models/ --include=*.yml dbt_project.yml
dbt list --select "access:public" --output name
```

A model with `access: public` is declared to be referenceable from outside its group and — in a multi-project setup — from outside the project entirely. **That declaration is itself a blast-radius finding**: it means consumers exist that this repository cannot enumerate. `access: private` is the opposite and much better news: only models in the same group may reference it, and dbt enforces that at parse time, so the graph search above is close to complete.

Absence of `access` anywhere means the project has not adopted the feature, in which case every model behaves as `protected` (referenceable anywhere in the project) and this check tells you nothing. Say that rather than reporting "no public models".

---

## 2. Cross-project consumers

If the project is one of several that reference each other, `dbt list` in this repository sees **only this project's** DAG. A downstream project's `ref()` to a public model here does not appear in your manifest at all.

What you can check locally:

```bash
# does this project expose models to others, or consume from others?
grep -rn "projects:" dependencies.yml 2>/dev/null || echo "no dependencies.yml"
dbt list --select "access:public" --output name
```

What you cannot: the set of projects that depend on yours. That lives in the platform's metadata, not in the repository. Cross-project references are a paid-tier dbt platform feature, so a project using them has a catalog or lineage service that can answer the question — name it as the next step and say you could not answer it from the repo.

The safe default: **treat every `access: public` model as having unknown external consumers** and give it the deprecation treatment in `governance-mechanisms.md` rather than a direct edit.

---

## 3. BI consumers

BI tools read the *warehouse relation*, not the dbt model, and they keep their own copy of the schema. Renaming a column in dbt does not update them; it makes them wrong.

Read `bi.consumers` from the contract, then for each declared `repo_path` grep three strings:

```bash
grep -rn "<model_name>"          <bi_repo_path>/   # dbt model name
grep -rn "<physical_table_name>" <bi_repo_path>/   # differs if the model sets an alias
grep -rn "<column_name>"         <bi_repo_path>/
```

The physical name is the one that matters and the one most often skipped. A model with `alias` set, or a versioned model (whose default relation name carries a version suffix), has a warehouse name that is not its dbt name — grepping only the dbt name in a BI repository returns clean and means nothing.

Three honest outcomes:

| Situation | What to report |
|---|---|
| `bi.consumers` declares a `repo_path`, grep is clean | "No references in `<tool>` as of `<commit>`" — a real finding, scoped to what is version-controlled |
| A consumer is declared with no `repo_path` | "Must be checked through that tool's API or UI; not checkable from here" |
| `bi.consumers` is absent | "The project has not declared its BI consumers, so BI impact could not be checked" — then ask which tools read the warehouse |

Never convert any of these into "no BI impact". Even a clean grep over a full BI repository misses content that is not in the repository: ad-hoc explorations, saved user queries, scheduled email exports, and anything a user built in the tool's UI rather than in code.

If `bi.use_exposures` is true, the project models BI dependencies as exposures, which puts them in the DAG and makes `--resource-type exposure` meaningful. That is strictly better than grepping — but only for the exposures someone remembered to write.

BI-layer risk by change type:

| Change | Typical BI risk |
|---|---|
| Add a column | Low — additive. Watch for a name collision with a field the BI layer already defines |
| Rename a column | High — every reference breaks or silently drops |
| Remove a column | High — same, plus derived fields built on it |
| Change a type | Medium to high — comparisons, formatting and sort order can change without erroring |
| Change grain | **High and quiet** — reports keep rendering, aggregates are wrong |
| Rename a model | High where the relation name is hardcoded; lower where a semantic layer indirects it |
| Change materialization | Low to medium, and non-obvious — some tools cache the object type, and grants may not survive a replace |

The grain row is the one to take seriously. Every other row eventually produces an error someone notices. A grain change produces plausible numbers.

---

## 4. Consumers in no repository at all

Ad-hoc queries, notebooks, scheduled exports, reverse-ETL syncs, and other teams' pipelines do not live anywhere you can grep. The warehouse query log is the only evidence available, and it is partial evidence.

Read `project.query_history_relation` and `project.query_history_retention_days` from the contract. Without those fields, ask rather than guess at a relation name.

```sql
-- shape of the check; the relation and column names are warehouse-specific
select <user_column>,
       count(*)                   as query_count,
       max(<start_time_column>)   as last_seen
from <query_history_relation>
where <query_text_column> ilike '%<table_name>%'
  and <start_time_column> >= current_date - <retention_days>
group by 1
order by last_seen desc
```

### What each platform offers, and what it costs you

| Platform | Where to look | The important limitation |
|---|---|---|
| Snowflake | An access-history view resolves each query to the objects it touched, including objects reached *through a view*. Requires a higher edition; on lower editions you are left with query-text search | Access history is large and slow to scan unfiltered |
| BigQuery | The jobs metadata view exposes a `referenced_tables` array per query job, which resolves views for you | Region-scoped, and not populated for cache hits |
| Redshift | System views over statement text | Text matching only, and statement text can be truncated or split across rows |
| Postgres and other engines | Statement statistics if the relevant extension is enabled; otherwise nothing | Frequently disabled, and normalises away the literals |

**The distinction that matters:** a log that records *resolved objects* (Snowflake access history, BigQuery `referenced_tables`) will show a table as used when a view over it was queried. A log you can only *text-search* will not — so a clean text search over query history proves nothing about a table that is consumed exclusively through a view, which is the normal case for a base table. State which of the two you had.

Four more limits worth stating out loud:

1. **Retention truncates the answer.** Match the window to `project.query_history_retention_days`. Thirty days is the example, not the answer: if the log retains seven, a thirty-day claim is false rather than merely thin. Report the window you actually measured.
2. **A window shorter than a consumer's period cannot clear the relation.** A monthly or quarterly job is invisible to a 30-day window by construction. For anything that might run on a period, the window has to exceed it or the check is void.
3. **Text search matches comments and unrelated names.** A table called `payments` matches every query mentioning payments in a comment or a string literal. Read the hits.
4. **Your own dbt runs are in the log.** Filter out the service account that runs dbt, or every model looks busy.

If the warehouse has no such view, say the check was not possible. **"No dbt or BI references found" and "unused" are different claims**, and only the first one is ever provable from a repository.

---

## 5. Classify, then choose the approach

| Finding | Risk | Required approach |
|---|---|---|
| No consumers anywhere, and query history was checked over a window longer than any plausible consumer period | Low | Direct change |
| dbt consumers only, all in this project | Medium | Update consumers in the same change, upstream-first ordering |
| BI consumers found | High | Expand/migrate/contract with a dated window, coordinated with the BI change |
| `access: public`, cross-project, or unknown consumers | Critical | Versioned or deprecated interface, notification before the window opens |
| Any check above was not possible | **Treat as the next tier up** | Say which check was missing and why the risk was rounded up |

That last row is the one that gets skipped. An unavailable check does not lower the risk of the change; it lowers your knowledge of it.
