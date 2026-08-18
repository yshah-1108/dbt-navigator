---
name: dbt-macros
description: Use when writing or editing a macro, deciding whether repeated SQL justifies one, debugging a Jinja compilation error, working with run_query or statement blocks, or reasoning about parse-time vs execution-time evaluation. Covers when a macro is the wrong answer.
metadata:
  phase: build
---

# Macros

A macro trades a duplicated fragment for an indirection. The fragment is visible in the model; the macro is not. Most of this skill is about when that trade is worth making, because the default failure in dbt projects is not too little abstraction — it is a macro layer nobody can read.

| Sub-document | Read it when |
|---|---|
| [jinja-mechanics.md](jinja-mechanics.md) | You need delimiter syntax, context variables (`target`, `this`, `graph`, `var()`/`env_var()`), parse-vs-execution-time rules, or `statement`/`run_query` details |

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

The only field macros routinely need is `environments.detection.expression` — the literal Jinja that identifies dev. Use it verbatim. **If it is absent, do not invent a detection expression.** Ask which one the project uses, or take the condition as a macro argument. A wrong detection expression is how a dev-only filter ends up applied in production, or a production branch runs in dev against real data.

`project.warehouse` matters for any macro emitting dialect-specific SQL.

## When not to write a macro

Ask what breaks if you copy the fragment instead. If the answer is "nothing, it is four lines in two models," copy it.

| Do not macro | Because |
|---|---|
| Two call sites | Indirection costs more than the duplication, and two occurrences are weak evidence of a pattern. They may diverge next quarter, and then you are adding parameters to keep them together. |
| A simple `case` or `coalesce` | The inline version is self-documenting; the macro requires opening another file. |
| Logic used once, however complex | A CTE with a clear name is better. |
| Wrapping an existing macro | Adds a layer, no capability. |
| Something a package or the adapter already does | Check `dbt_utils`, `dbt-expectations`, and dbt's cross-database macros first. |

| Write a macro | Because |
|---|---|
| Three or more identical call sites | The pattern is established and drift between copies is a real risk. |
| A business rule that must change everywhere at once | Divergence produces two numbers for one metric. |
| Environment-conditional behavior | Getting the condition wrong is dangerous, so it belongs in exactly one place. |
| Generated SQL — a loop over a column list | Hand-writing it is error-prone and unreviewable. |
| A fragment that is genuinely easy to get wrong | Correct once, reused. Worth indirection even at two sites. |

The strongest signal is not occurrence count — it is **whether a change must land in every copy simultaneously.** If it must, macro it at two sites. If copies can legitimately diverge, leave them.

Abstracting early costs more than abstracting late. Two call sites forced into one macro tend to acquire a boolean parameter, then a second, until the macro expresses both variants worse than either did alone.

### The readability cost, stated plainly

Worth being explicit about what a macro takes away, because the cost is real and usually unpriced:

| Cost | Consequence |
|---|---|
| The model no longer states what it does | A reader must open a second file, and a third if that macro calls another |
| Reviewers stop reading the SQL | A diff showing a changed macro argument does not show the SQL it produces. Approving it is trust, not review |
| Search stops working | Grepping for a column or a business rule no longer finds the models that apply it |
| Warehouse query logs get harder to read | Everyone debugging a slow query is reading generated SQL |
| The compiled output becomes the only truth | Which means it must actually be read, every time |

The asymmetry that decides most cases: **duplicated SQL is visible and a leaky abstraction is not.** A duplicated fragment can be found and fixed by anyone. A macro producing subtly wrong SQL under one argument combination is found by whoever notices the number is wrong, months later.

A useful test before writing one: could a competent colleague who has never seen this project read the *call site* and correctly predict the SQL? If not, the macro needs a better name, fewer arguments, or not to exist.

## Jinja mechanics

Delimiter syntax (`{{ }}`, `{% %}`, `{# #}`), whitespace control, `set`/loop scope, the context variables (`target`, `this`, `model`, `execute`, `flags`, `graph`, `var()`/`env_var()`), and the parse-time-vs-execution-time split that produces most "works on rerun, fails on clean build" bugs are in [jinja-mechanics.md](jinja-mechanics.md).

The one rule worth stating here because it causes real incidents: **`execute` is true whenever dbt has a warehouse connection, including `dbt compile` and `dbt docs generate` — not only `run` and `build`.** A `run_query` containing `delete`/`insert`/`create` guarded only by `{% if execute %}` performs it during documentation generation too. Scope side-effecting calls by `flags.WHICH` as well, or — better — keep side effects out of model code entirely and put them in an operational macro invoked through `run-operation`. Details, the `this`-vs-`ref()` distinction, and `statement`/`run_query` mechanics are in [jinja-mechanics.md](jinja-mechanics.md).

## Cross-warehouse macros and dispatch

Before writing dialect SQL, check whether a cross-database macro already covers it. dbt ships a set — `dbt.dateadd()`, `dbt.datediff()`, `dbt.date_trunc()`, `dbt.safe_cast()`, `dbt.type_timestamp()` and others — and using them is how a macro stays portable without an `if` chain on `target.type`.

Where genuinely different SQL is needed per warehouse, `adapter.dispatch` is the mechanism:

```sql
{% macro <macro_name>(<args>) %}
    {{ return(adapter.dispatch('<macro_name>')(<args>)) }}
{% endmacro %}

{% macro default__<macro_name>(<args>) %}
    -- portable implementation
{% endmacro %}

{% macro <adapter_name>__<macro_name>(<args>) %}
    -- warehouse-specific implementation
{% endmacro %}
```

How resolution works, and the parts that surprise people:

| Rule | Detail |
|---|---|
| Naming is rigid | The dispatching macro takes the plain name; implementations are `<adapter>__<name>` and `default__<name>`. A typo produces "macro not found", not a fallback |
| Falls back to `default__` | When no adapter-specific implementation exists |
| Child adapters inherit | An adapter derived from another searches the parent's prefix too, before `default__` |
| Package macros must pass a namespace | `adapter.dispatch('<name>', '<package_name>')`, or dispatch cannot find the package's own candidates |
| Package specificity beats adapter specificity | With a `dispatch` search order configured, `my_project.default__x` is preferred over `some_package.<adapter>__x` |

The override mechanism is what makes dispatch worth knowing even in a single-warehouse project. A `dispatch` config in the root `dbt_project.yml` redirects a package's internal calls through your implementations:

```yaml
dispatch:
  - macro_namespace: <package_name>
    search_order: ['<your_project>', '<package_name>']
```

That is how you change the behaviour of a package's higher-level macro by reimplementing one building block it calls, without forking the package. Two constraints: the config is only read from the **root** project, so putting it in a package has no effect; and `ref`, `source` and `config` are context properties rather than dispatched macros, so they cannot be overridden this way.

The failure mode: overriding a macro in your project, seeing your version used at your own call sites, and assuming the package now uses it too. It does not — not without the `dispatch` config — so a fix applied in one place holds in half the project. Verify by compiling a model that reaches the macro through the package, and reading the output.

Dispatch is only worth the indirection in a package or a genuinely multi-warehouse project. In a single-warehouse project it is three files where one would do.

## Patterns worth having

### Cheap dev builds via a date filter

The highest-value macro most projects write. Full-history builds in a personal schema are slow and expensive for no benefit, so restrict the window outside production. Centralizing it means the condition is correct in one place instead of approximately correct in forty models.

```sql
{% macro limit_dev_window(column, days=7, column_type='timestamp') %}
    {#-
        Restricts a build to a recent window outside production; emits nothing in production.
        Requires a preceding predicate — emits a leading `and`.
    -#}
    {%- if <environments.detection.expression from the contract> -%}
        {%- if column_type == 'date_integer' -%}
            and {{ column }} >= cast(
                to_char({{ dbt.dateadd('day', -days, 'current_date') }}, 'YYYYMMDD') as integer
            )
        {%- else -%}
            and {{ column }} >= {{ dbt.dateadd('day', -days, 'current_date') }}
        {%- endif -%}
    {%- endif -%}
{% endmacro %}
```

Four things make it work:

1. **It emits nothing in production.** The production compiled SQL is byte-identical to having no macro. Verify by compiling against a production target and reading the output.
2. **It leads with `and`,** so call sites sit under an existing `where`. Document that — a call site with no preceding predicate is a syntax error.
3. **The condition comes from the contract.** Never hardcode a database name.
4. **A type parameter** handles integer date keys, which are common and cannot take date arithmetic directly.

Date arithmetic syntax differs by warehouse, so route it through a cross-database macro like `dbt.dateadd()` rather than writing dialect SQL, or gate on `project.warehouse`.

Do not pass a dev window through `--vars`. It is invisible in the model file, absent from the compiled artifact under review, and forgotten exactly once — during the run that matters.

### A row-deletion helper with a safety interlock

Operational macros run destructive statements against a real warehouse. The pattern that keeps them survivable is **dry-run by default with an explicit confirmation token**:

```sql
{% macro remove_rows(relation, where_clause, confirm=none) %}
    {%- set count_query -%}
        select count(*) from {{ relation }} where {{ where_clause }}
    {%- endset -%}

    {% if execute %}
        {% set affected = run_query(count_query).columns[0].values()[0] %}
        {{ log("Rows matching predicate: " ~ affected, info=true) }}

        {% if confirm != 'DELETE' %}
            {{ log("Dry run. Re-invoke with confirm='DELETE' to execute.", info=true) }}
        {% else %}
            {% do run_query('delete from ' ~ relation ~ ' where ' ~ where_clause) %}
            {{ log("Deleted " ~ affected ~ " rows.", info=true) }}
        {% endif %}
    {% endif %}
{% endmacro %}
```

Invoked with `dbt run-operation`. Three properties are the point: it reports the blast radius before acting, it requires a second deliberate step, and it logs what it did. Take the relation as an explicit fully-qualified argument rather than deriving it from `target`, so the environment is visible in shell history. Do not point one of these at production unless the user named production explicitly — a destructive operation is never inferred from an ambiguous request.

### Operational macros in general

`dbt run-operation` has no dry-run mode, no confirmation prompt, and no undo. Whatever safety exists is what the macro itself provides, so these properties are the macro's job:

| Property | Implementation |
|---|---|
| **Blast radius reported first** | Count or list what will be affected, log it, before touching anything |
| **Confirmation required** | A second invocation with an explicit token. A boolean `dry_run=false` is weaker — it is one word to get wrong |
| **Idempotent** | Running it twice must be indistinguishable from running it once. Where that is impossible, say so in the docstring |
| **Logged** | `{{ log(..., info=true) }}` at each step, so the terminal output is a record of what happened |
| **Explicit target** | The relation or database as an argument, never derived silently from `target` |
| **Bounded** | A predicate the macro requires, rather than defaulting to everything |

On idempotency: a `delete` over a bounded range is idempotent; an `insert` is not. A macro that appends should either be a merge or should delete its range first — otherwise the second invocation, which will happen during an incident, doubles the rows.

The `--sql` form (1.12+) skips the macro entirely:

```bash
dbt run-operation --sql "<statement>"
```

Convenient for a true one-off, and unreviewable by construction: nothing records it but shell history, and it has none of the safety properties above. Anything that will run twice, or that anyone else will run, belongs in a macro under version control.

## Custom generic tests

A generic test is a macro in a `test` block, and writing one is often the right answer where a business rule needs asserting in more than one place. They live in `tests/generic/` or in `macros/`.

```sql
{% test <test_name>(model, column_name, <extra_arg>) %}
    {{ config(severity = 'warn') }}

    select <column_name>
    from {{ model }}
    where <the condition that should never be true>
{% endtest %}
```

The contract: **the test passes when the query returns zero rows.** Anything else is a failure, and the returned rows are the evidence.

| Argument | Meaning |
|---|---|
| `model` | The relation the test is defined on — always named `model`, even for a source, seed or snapshot |
| `column_name` | The column, for column-level tests. Omit for model-level tests |
| Anything else | Passed from the YAML `arguments` mapping |

Three things worth knowing:

- A `config()` block inside the test definition sets defaults for every instance of it — `severity` most usefully — overridable per instance in YAML.
- Defining a test named the same as a built-in (`unique`, `not_null`) **overrides the built-in project-wide**, silently. That is occasionally what you want and is otherwise a very confusing bug: every `unique` test in the project now runs your SQL. Name new tests distinctively unless the override is the goal.
- To document a generic test in YAML, list it under `macros:` with a `test_` prefix on the name.

Check `dbt_utils` and `dbt-expectations` before writing one; the common assertions already exist there. See `dbt-authoring-schema-yaml` for where tests belong and `dbt-unit-tests` for testing model logic rather than data.

## Custom materializations, and why not to write one

A materialization is a macro in a `materialization` block that owns the full lifecycle of building a relation: inspect existing state, run pre-hooks, execute DDL/DML, run post-hooks, clean up, commit, and return the relations it created so dbt's cache stays correct.

**Treat writing one as a last resort.** The reasons are concrete:

| Cost | Detail |
|---|---|
| You own the whole lifecycle | Miss the `adapter.commit()` and the work is rolled back. Miss `run_hooks` and every hook on every model using it silently stops firing |
| The relation cache desynchronises | Failing to return created relations, or renaming outside `adapter.rename_relation`, leaves dbt's cache disagreeing with the warehouse |
| `--full-refresh` and other flags are your problem | Nothing handles them for you |
| No upgrade path | Built-in materializations gain capabilities each release; yours does not |
| Every reader is now an expert | A model using a custom materialization cannot be understood from the model file |

Before writing one, check whether a config on an existing materialization does it, whether the adapter offers a materialization already, and whether a post-hook covers it. Most "we need a custom materialization" cases are an incremental model with a strategy config, or a post-hook.

If one is genuinely required, do not name it after a built-in: dbt resolves materializations by precedence, and shadowing `table` or `incremental` changes the behaviour of every model in the project including those in packages. Prefix or suffix the name so it is visibly a variant. Two packages may not define the same materialization name — that is an error, and it is a real hazard when adding a package to a project that has one.

### Environment-conditional logic in general

Any branch on environment reads its condition from `environments.detection.expression`. Two rules: **default to the safe branch when detection is uncertain** — an unrecognized environment should behave like production — and **compile against both targets and read both outputs.** A branch you have not compiled is a branch you have not tested.

Where a macro must emit different SQL per warehouse, use `adapter.dispatch` with `default__` and per-adapter implementations rather than an `if` chain on `target.type`. Only worth it in a package or a genuinely multi-warehouse project.

## Testing a macro

dbt's unit-test framework covers **models, not macros** — that is a documented limitation, not an oversight to work around. So macro correctness is established by other means. Four options, increasing in strength.

**1. Compile a call site and read the output.**

```bash
dbt compile --select <model_using_the_macro>
# target/compiled/<project_name>/models/<path>/<model>.sql
```

This is the only way to know what the macro produced. Reasoning about Jinja without reading the compiled result is guessing — whitespace, comma placement, and empty conditional branches all behave differently than they look.

**2. A scratch model asserting known input/output.** Rows are cases, each with the macro's output beside the expected value and a test asserting they match. Cover null input, empty string, and every type variant the macro accepts.

```sql
-- a fixture model: one row per case
with cases as (
    select '<input_1>' as input, '<expected_1>' as expected
    union all
    select '<input_2>' as input, '<expected_2>' as expected
)
select input, expected, {{ <macro_name>('input') }} as actual
from cases
```

A singular test then fails on `actual != expected`. This is the strongest option that stays inside dbt, and unlike option 1 it does not silently rot: it runs on every build.

**3. Unit-test the model, not the macro.** Where a macro's whole job is to produce part of a model's SQL, a model unit test with static inputs asserts the *outcome* of the macro. That is often the more useful assertion, and it uses supported machinery. Note two things: a unit test's `overrides.macros` can stub a macro's output, which is how to test the surrounding logic independently; and a model containing an **introspective** query cannot be unit-tested at all — which is one more reason to keep introspection out of models. See `dbt-unit-tests`.

**4. Byte-comparison for an extraction.** When pulling a macro out of existing SQL, the target is compiled output identical to what it replaced — proof the change cannot alter results, and stronger than any data comparison. See `dbt-refactoring-safely`.

Testing macros in Python with a Jinja harness is possible, and there are community packages for it; it buys speed and the ability to assert on error paths, at the cost of not exercising dbt's actual rendering. Reasonable for a shared macro library, overkill for a project's own macros — and if you go there, say plainly that the harness is an approximation of dbt's environment rather than dbt itself.

For an environment-conditional macro, compile both branches. Where it should emit nothing, confirm it emits *nothing* — not a stray newline inside a `where` clause.

## Debugging

Read the compiled SQL. Nearly every macro bug is visible there and invisible in the source. Run that compiled SQL directly in a warehouse client to separate a Jinja problem from a SQL problem: if it fails standalone, the macro produced wrong SQL. `{{ log(value, info=true) }}` prints during execution, for values that only exist at run time.

| Symptom | Cause | Fix |
|---|---|---|
| `'None' has no attribute` | introspection at parse time | guard with `{% if execute %}` and return a sane parse-time value |
| Trailing comma before `from` | loop without `loop.last` | `{% if not loop.last %},{% endif %}` |
| Next line vanished | stripping merged it into a `--` comment | use `{# #}`, or stop stripping there |
| Macro renders blank | no `{{ return(...) }}` on a value-returning macro | add it |
| `syntax error at "and"` | conditional fragment leading with `and` had no preceding predicate | add `where 1 = 1`, or document the requirement |
| Comparison never true | `=` instead of `==` | `==` |
| Type error concatenating | `+` on strings | `~` |
| `&&`, `\|\|`, `!` rejected | Python/JS operators | `and`, `or`, `not` |
| Works on rerun, fails on clean build | introspects a relation that does not exist yet | handle the `none` relation case |
| Quoting errors around `var()` | missing quotes | `'{{ var("x") }}'` for a string literal |
| Indexing a `run_query` result raises | the query returned no rows, or was DDL/DML and returned `none` | check `results\|length > 0` first |
| `dbt docs generate` changed warehouse state | side-effecting `run_query` reached during compilation | scope with `flags.WHICH`, or move it out of model code |
| Macro not found, despite being defined | dispatch naming — the implementation needs the `<adapter>__` or `default__` prefix | rename to the exact prefix form |
| Your override works locally but the package ignores it | no `dispatch` search order configured in the **root** project | add the `dispatch` config |
| Every `unique` test in the project behaves oddly | a locally-defined generic test shadowed the built-in | rename it |
| A macro fails at parse on v2 and compiled fine on 1.x | v2 fails parse on nonexistent macros, adapter methods, undefined vars and undefined generic tests | fix the reference; the earlier failure is the point |
| `return(x) + y` silently ignores `y` | `return` is final; surrounding expressions do not apply. v2 warns about it | compute the value first, then return it |

## Documenting a macro

A macro is read at its definition, not its call site, so the docstring is where the contract lives. State the call-site requirements — a leading `and`, expected argument types, environment behavior — because those are exactly what a caller gets wrong. Add a YAML entry under `macros:` if the project publishes docs; a custom generic test is documented the same way, with a `test_` prefix on the name.

## Completion checklist

- [ ] Justified against the "must change everywhere at once" test, not occurrence count alone
- [ ] Call site is readable on its own — a colleague could predict the SQL from it
- [ ] Checked whether `dbt_utils`, `dbt-expectations`, a cross-database macro, or the adapter already provides it
- [ ] Environment condition read from the contract, never hardcoded
- [ ] Warehouse-specific SQL gated on `project.warehouse`, routed through a cross-database macro, or dispatched
- [ ] `{% if execute %}` guard on every introspective call, with a defined parse-time return
- [ ] Any side-effecting statement additionally scoped by `flags.WHICH`, or moved out of model code entirely
- [ ] `run_query` results checked for emptiness before indexing
- [ ] `var()` / `env_var()` given defaults wherever the value is optional
- [ ] Compiled at a real call site and the compiled SQL **read**
- [ ] Both branches compiled where the macro is environment-conditional
- [ ] A fixture model or unit test asserts the macro's output, if it will be maintained by anyone else
- [ ] Destructive macros report blast radius, require explicit confirmation, log what they did, and are idempotent
- [ ] Generic tests named distinctively unless a built-in override is the deliberate goal
- [ ] Custom materialization rejected in favour of a config or hook, unless genuinely impossible
- [ ] Dispatch: `default__` present, and a root-project `dispatch` config added if a package's calls must route through the override
- [ ] Docstring states call-site requirements and environment behavior
- [ ] Compiled output diffed against the original, if this was an extraction

## The failure modes that cost the most

1. **Abstracted at two call sites, then parameterized to death.** The macro grows flags to keep divergent callers together and ends up less readable than the duplication it replaced.
2. **Introspection without an `execute` guard.** Fails at parse time, or worse, returns `none` and the model compiles to something subtly wrong.
3. **A side-effecting `run_query` guarded only by `execute`.** It runs during `dbt compile` and `dbt docs generate` too, so a scheduled docs job is quietly executing DML. `flags.WHICH` scopes it; keeping side effects out of model code prevents it.
4. **Hardcoded environment detection.** Nobody notices until a dev-only filter is silently active in production, or a production branch runs in dev against real data.
5. **Never reading the compiled SQL.** Stripping ate a line, a comma is misplaced, a conditional emitted a stray `and`. All obvious in `target/compiled/`, all invisible in the macro file.
6. **A destructive operational macro with no dry-run.** One typo in a `where` clause and the rows are gone. The interlock exists because the mistake is cheap to make and expensive to undo — and `run-operation` provides none of it for you.
7. **An override that only half applies.** A macro reimplemented in the project, used at your call sites and ignored by the package that calls its own copy, because no `dispatch` search order was configured. Half the project has the fix.
8. **A custom materialization missing a lifecycle step.** Hooks stop firing, or the relation cache disagrees with the warehouse, and the symptom appears in an unrelated model.
9. **A generic test that shadowed a built-in.** Every `unique` test in the project silently runs different SQL, including tests in packages.
