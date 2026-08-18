# Jinja mechanics

The syntax, context variables, and parse/execution-time behavior a macro author needs. Read [SKILL.md](SKILL.md) first for when to write a macro at all; this document is the mechanics once you are writing one.

## Table of contents

- [Delimiters](#delimiters)
- [Whitespace control](#whitespace-control)
- [`set`, loops, and scope](#set-loops-and-scope)
- [Context variables](#context-variables)
- [Parse time vs execution time](#parse-time-vs-execution-time)
- [`execute` is not "only during a run"](#execute-is-not-only-during-a-run)
- [`statement` blocks](#statement-blocks)

## Delimiters

```sql
{{ ... }}   -- expression: renders into the compiled SQL
{% ... %}   -- statement: logic, renders nothing
{# ... #}   -- comment: removed at compile time
```

Use `{# #}` for anything the reader of the compiled SQL does not need. A `--` comment survives into the warehouse and into every query log.

## Whitespace control

`-` on either side of a delimiter strips adjacent whitespace: `{%- if x -%}`, `{{- expr }}`. Mostly cosmetic — Jinja otherwise leaves a blank line per statement block — but two cases change behavior:

- A stripped newline can join a `--` comment to the following line, commenting it out.
- Over-stripping can concatenate two identifiers into one token.

Read the compiled output rather than reasoning about whitespace in your head.

## `set`, loops, and scope

```sql
{% set metrics = ['order_count', 'refund_count', 'gross_amount'] %}

{% for metric in metrics %}
    sum({{ metric }}) as total_{{ metric }}{% if not loop.last %},{% endif %}
{% endfor %}
```

`loop.last` handles trailing commas; also available are `loop.first`, `loop.index` (1-based), `loop.index0`, `loop.length`. A `set` inside a loop does not persist after it — use `namespace()` when you need accumulation across iterations. Building a list and joining is often cleaner than comma bookkeeping:

```sql
{% set exprs = [] %}
{% for metric in metrics %}
    {% do exprs.append('sum(' ~ metric ~ ') as total_' ~ metric) %}
{% endfor %}
{{ exprs | join(',\n    ') }}
```

## Context variables

| Variable | Meaning |
|---|---|
| `target` | The resolved connection: `target.name`, `target.database`, `target.schema`, `target.type` |
| `this` | The current model's relation |
| `model` | The current node's metadata dict |
| `execute` | `false` during parsing, `true` during execution **and during any compilation with a connection** |
| `flags` | Invocation flags, including `flags.FULL_REFRESH` and `flags.WHICH` |
| `graph` | The parsed project: every node, source, and exposure |
| `modules` | Python `datetime`, `re`, `pytz` |
| `var()` / `env_var()` | Project and environment variables |
| `invocation_id` | A unique id for this run — useful in an audit column |
| `adapter` | Warehouse introspection and dispatch |

`target.name` is the least reliable for environment detection, because a target name is set per developer in a local profile and can be anything. Prefer database or schema, and take the expression from the contract rather than choosing one.

### `this`, and why it is not a substitute for `ref()`

`this` is the relation the current model writes to, so it resolves per environment automatically — which is exactly what an incremental model's `is_incremental()` branch needs. What it is not: a dependency. Using `this` creates no DAG edge, so it is correct for self-reference and wrong for anything else. Referencing another model by constructing its name from `target` rather than calling `ref()` produces a model dbt does not know is a parent, and it will be built in the wrong order — intermittently, depending on threading.

### `var()` and `env_var()`, with defaults

```sql
{{ var('<name>', <default>) }}          -- default when the var is not set
{{ env_var('<NAME>', '<default>') }}    -- default when the environment variable is absent
```

Differences that decide which to use:

| | `var()` | `env_var()` |
|---|---|---|
| Set in | `dbt_project.yml`, or `--vars` | The process environment |
| Visible in the repository | Yes, when project-level | No |
| Missing with no default | 1.x: fails at compile. v2: fails at **parse** | Fails at compile |
| Right for | A project-wide constant reviewed in a diff | A secret, or a value that genuinely differs per deployment |
| Traced by `state:modified` | **No** — a changed *value* is invisible to state comparison | No |

Two consequences. Always supply a default for anything optional, or a run in an environment that does not set it fails on a message that does not name the missing variable clearly — and on v2 it fails earlier, at parse. And because a changed variable value is invisible to state comparison, a behavioural switch driven by a variable can change every model's output while CI reports nothing modified. That is a reason to keep behaviour in code, and it is covered in `dbt-environments`.

Note that `env_var` is available at parse time and can be used in `dbt_project.yml` and `profiles.yml`, which `var` cannot.

### `graph`

`graph.nodes` and `graph.sources` expose the whole parsed project, which makes project-wide operations possible — grant every relation with a given config, report on every model missing a description. Two constraints: it is only fully populated at execution, so it needs the `execute` guard, and iterating it in a model creates no dependencies, so anything derived from it is not reflected in the DAG. Reserve it for operations, not for models.

## Parse time vs execution time

dbt reads every file twice. First it **parses**, building the DAG from `ref()` and `source()`, with `execute` false and no warehouse connection. Then it **executes**, with `execute` true.

Consequences that produce real errors:

- Anything touching the warehouse — `run_query`, `adapter.get_columns_in_relation`, `adapter.get_relation` — returns nothing useful at parse time. Unguarded, it raises or silently yields `none`.
- A `ref()` inside a conditional is still registered as a dependency. Dependencies are not conditional.
- Introspecting a relation that does not exist yet is the classic first-run failure: works on the second run, fails on a clean warehouse.

Guard it, and define the parse-time return so the macro is coherent both times:

```sql
{% macro get_distinct_values(relation, column) %}
    {% set query %}
        select distinct {{ column }} from {{ relation }} order by 1
    {% endset %}

    {% if execute %}
        {% set results = run_query(query) %}
        {{ return(results.columns[0].values()) }}
    {% else %}
        {{ return([]) }}
    {% endif %}
{% endmacro %}
```

Returning `[]` keeps the model parseable. Returning `none` makes every downstream `for` loop fail with an unhelpful error.

Introspection also means the compiled SQL depends on warehouse state, so the same commit can compile differently on different days. Prefer explicit lists where you can.

## `execute` is not "only during a run"

This is the gotcha that surprises people who thought the guard was enough, and it has caused real incidents.

`execute` is true whenever dbt compiles **with a warehouse connection**. That includes `dbt compile` and `dbt docs generate` — not only `dbt run` and `dbt build`. So `{% if execute %}` around a `run_query` does not mean "only when materializing"; it means "whenever dbt has a connection", which is most commands.

The consequence: **a macro containing a `delete`, `insert`, `create` or `grant` inside a `run_query` performs it during documentation generation.** Nothing warns. A team that runs `dbt docs generate` on a schedule is running that statement on a schedule.

Scope by command, not only by `execute`:

```sql
{% if execute and flags.WHICH in ['run', 'build'] %}
    {% do run_query('<side-effecting statement>') %}
{% endif %}
```

`flags.WHICH` holds the active command. Adjust the list to the commands where the effect is intended — `run-operation` for an operational macro, `run` and `build` for something a materialization needs.

The design rule that avoids the problem entirely: **keep side effects out of model code.** A statement that changes warehouse state belongs in a macro invoked through `dbt run-operation`, or in a hook, where the invocation is explicit and visible in the command. A `run_query` inside a model should only ever read.

Two further notes on the same theme. `run_query` does not open a transaction — issue `begin` and `commit` yourself if you need one. And `--no-compile` on `dbt docs generate` skips the compilation step, which is the mitigation when a project already has this problem and cannot be fixed immediately; it is not a substitute for fixing it.

## `statement` blocks

Block-form `set` captures multi-line SQL as a string; `run_query` executes it and returns an agate table:

```sql
{% set query %}
    select count(*) from {{ this }}
{% endset %}

{% set row_count = run_query(query).columns[0].values()[0] %}
```

`statement` is the lower-level form, needed when you want `fetch_result=false` or named results:

```sql
{% call statement('<result_name>', fetch_result=true) %}
    select max(<column>) from {{ this }}
{% endcall %}

{% set result = load_result('<result_name>') %}
```

Use `run_query` otherwise.

Details that matter when reading the result:

| Fact | Consequence |
|---|---|
| The return is an agate table | Index by column: `.columns[0].values()`, or `.rows[0][0]` for a single scalar |
| A statement returning no rows returns `none` | A DDL or DML statement gives you `none`, not an empty table. Guard before indexing |
| Use `results\|length > 0` before indexing | An empty result indexed at `[0]` raises an error whose message names Jinja, not your query |
| `fetch_result=false` skips retrieval | Right for a DDL statement where you do not need the output |
| No transaction is opened | Issue `begin` / `commit` explicitly if the operation needs atomicity |
