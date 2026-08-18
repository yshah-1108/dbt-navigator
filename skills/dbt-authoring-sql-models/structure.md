# Structure, Jinja, and readability

The parent SKILL.md gives the import/logical/final pattern and the rule about `select *`. This document covers the decisions that pattern does not settle: when a CTE has outgrown the file, whether to make it ephemeral, how much Jinja is too much, and which readability rules are worth a review comment.

The last question deserves a direct answer up front. **Most SQL formatting is preference, and a review that spends its attention on preference has none left for correctness.** Automate what a linter can decide, and reserve human review for the rules below, each of which has a failure mode attached.

## CTE structure, in more depth

### Naming

An import CTE takes the name of what it imports. A logical CTE takes the name of what it did.

| Good | Poor | Why |
|---|---|---|
| `filtered_to_active` | `step_2` | A number tells the reader nothing and renumbers when a step is inserted |
| `aggregated_to_daily` | `agg` | Abbreviation saves four characters and costs a lookup |
| `joined_to_customers` | `final_2` | `final_2` means an earlier `final` was demoted and nobody renamed anything |
| `deduplicated` | `clean` | "Clean" describes an intention, not an operation. Two readers will assume different operations |
| `orders` (import CTE) | `src1`, `a`, `o` | A single letter forces the reader to scroll to find out what it is |

**A CTE you cannot name in one or two words is doing more than one thing.** That is the actual test, and it is more useful than a line count. Splitting it costs one `with` clause entry and makes each half independently readable and independently inspectable during development.

The counter-pressure is real and worth naming: a CTE per micro-step produces a file where every step is trivially clear and the whole is impossible to follow. dbt Labs' own guidance is "a single logical unit of work, where performance permits" — a unit of work, not a single expression.

### Are CTEs a performance problem?

Sometimes, and the answer is engine-specific rather than a matter of style.

- **Some engines materialise a CTE**, so a CTE referenced three times is computed once (good) or is a fence the optimiser will not push predicates through (bad).
- **Some inline CTEs**, so a CTE referenced three times is computed three times.
- Postgres's behaviour changed across major versions and now depends on the presence of `materialized` / `not materialized` hints.

The practical rule: **write for readability first, and if a model is slow, read the query plan rather than guessing at the CTE structure.** A CTE referenced more than once by an expensive computation is the case worth checking. See `dbt-performance-tuning`.

### Filters belong as high as possible

Row filters and column selection in the import CTE reduce what the warehouse reads, which is the largest cheap win available in a model. A `where` clause three CTEs down usually still gets pushed up by the optimiser — but "usually" depends on what sits between, and an aggregation or a window function in between can block it entirely.

Put the filter where it is provably applied rather than where it reads most naturally.

## When a CTE should become its own model

The threshold is not size. It is **whether the intermediate result has more than one consumer, or needs to be inspected, tested, or documented on its own.**

Extract it when:

| Signal | Why the CTE is no longer the right home |
|---|---|
| The same logic appears in two or more models | Two copies drift. One gets a filter the other does not, and the two numbers disagree with no diff to explain it |
| The intermediate result needs a test | A CTE cannot carry a test. If uniqueness or referential integrity matters at that step, it needs to be a node |
| The result is a distinct business concept | A concept a person would name in a meeting deserves a name in the DAG |
| Debugging it requires repeatedly editing the final `select` | That is the mechanism telling you it wants to be materialised |
| The file has grown past the point where a reviewer reads it all | A reviewer who skims is a reviewer who approves |

Keep it inline when:

- Exactly one model will ever use it. A separate model adds a node, a file, a YAML entry, a build step, and a name to maintain — in exchange for nothing, until a second consumer appears.
- It is a mechanical step — a rename, a cast, a filter — with no independent meaning.

The asymmetry is worth stating: **duplicating logic across models is the more common and more expensive error.** Two consumers is the threshold worth extracting for. One is not.

Restructuring an existing DAG this way — splitting a model, inserting a layer, repointing refs — has its own procedure and its own hazards. See `dbt-restructuring-dags`.

## Ephemeral materialisation

`ephemeral` compiles the model into its consumers as a CTE. No relation is created.

| Ephemeral gives you | Ephemeral costs you |
|---|---|
| A named, `ref()`-able node in the DAG | **No queryable object.** You cannot `select` from it to debug |
| No warehouse object and no storage | **No data tests.** A test needs a relation to query |
| Logic defined once, reused by several models | **The SQL is duplicated into every consumer** and recomputed by each one |
| A tidy warehouse — internal steps stay out of it | **Unit tests require `format: sql`** for an ephemeral input |
| | **Compilation errors surface in the consumer**, not in the ephemeral model, so the error message names the wrong file |
| | Deep ephemeral chains compile into deeply nested SQL that is hard to read and can hit engine limits |

**The debugging cost is the one that decides it.** The first time a number in a downstream model looks wrong and you cannot query the intermediate step, the storage saved stops being worth it.

Reasonable defaults:

- **A view** for a cheap intermediate step: negligible storage, queryable, testable, always current.
- **Ephemeral** for a genuinely trivial step reused by several models, where nothing about it will ever need testing or inspection — and where its own model file is short enough to read as the definition.
- **A table** when the computation is expensive and has more than one consumer, so it is computed once rather than once per consumer.
- **Never ephemeral** for anything whose correctness anyone will need to verify independently.

Read `layers[].materialization` first; the contract may have decided this already.

## Jinja discipline

Jinja is a code generator. Every use trades a readable source file for a compiled file that says something else, and the compiled file is what runs.

### Where Jinja earns its place

- **`ref()` and `source()`.** The entire dependency graph.
- **`is_incremental()`** and boundary predicates.
- **Lifting magic values to the top of the file** where a reader will find them.
- **A genuinely repeated fragment**, extracted into a macro — three or more call sites, or one that is difficult to get right. See `dbt-macros`.
- **A loop over a list that is stable and short** — a fixed set of columns to unpivot, a fixed set of currencies.

### Where it costs more than it saves

- **A loop generating a `select` list from a variable list.** The model's schema is now a function of the variable, and a reader cannot see the output columns without compiling. If the list is short and stable, write the columns out.
- **Nested conditionals assembling SQL.** Once there are two nested `if`s, the number of possible compiled outputs exceeds what anyone will check, and only one of them has been tested.
- **Anything that makes the schema depend on warehouse state.** Introspective macros — `dbt_utils.star`, `union_relations`, `get_column_values` for a pivot — mean the same commit compiles differently at different times, the model cannot be compiled without a connection, and **the model cannot be unit tested**. See `dbt-unit-tests`.
- **A macro used once.** Indirection with no reuse. The reader now opens two files to understand one model.

The reliable test: **would a reader who has not seen this file be able to predict the compiled output?** If not, either simplify it or accept that this model must be read in its compiled form, and say so in a comment.

Whichever way that goes, compile it and read the result. `dbt-verification` covers reading compiled SQL properly.

### Lifting magic values

```sql
{% set in_scope_statuses = ['shipped', 'delivered'] %}
{% set lookback_days = 30 %}
```

- A bare date literal five CTEs down is a maintenance trap: nobody finds it, and it silently ages.
- An unexplained identifier literal needs a comment saying what it is. `where record_type = '0Ab12'` is unreadable; the same line with `-- record_type 0Ab12 = wholesale` is fine.
- A value that changes per environment does not belong in a `set` at all — it belongs in a var or a config, so it is not edited per deployment.

### Whitespace control

`{%- -%}` strips surrounding whitespace. It has one legitimate use — keeping compiled SQL readable — and two ways to go wrong:

- **Stripping too aggressively welds tokens together.** `{%- if x -%}and{%- endif -%}` next to another keyword can compile to `...whereand...`. The error message points at a syntax error in generated SQL that does not appear in the source.
- **Stripping nothing leaves blank lines** that make compiled output hard to read, which matters because compiled output is what you debug.

Neither is a correctness question until it is a syntax error. Compile and read the output; that settles it in one command.

### `{# #}` versus `--`

`{# Jinja comment #}` is removed at compile time. `-- SQL comment` survives into the warehouse. Use the Jinja form for notes about the Jinja itself, and the SQL form for anything a person reading the compiled query or the warehouse's query history should see.

## Readability rules worth enforcing

Each of these has a failure mode. Anything not on this list is a linter's business, not a reviewer's.

| Rule | Failure mode it prevents |
|---|---|
| Explicit column lists in the `final` CTE | An upstream rename changes this model's output schema with no diff here |
| Every column qualified when more than one relation is in scope | A column silently resolves to the wrong side after someone adds a same-named column upstream |
| No single-letter or initialism aliases | Ambiguity in exactly the qualification the rule above depends on |
| Explicit join types, never a comma join | A dropped predicate turns an implicit join into a cross product with no syntax error |
| Explicit `group by` columns rather than positional | Inserting a column into the select list silently regroups the query |
| `as` used explicitly when aliasing | A missing comma between two columns turns them into one aliased column. `a b` is valid SQL and means something you did not write |
| Trailing commas, consistently | The same missing-comma class of error, made visible by consistent placement |
| One expression per line in a `case` | A multi-branch `case` on one line hides a branch, and the hidden branch is the one that is wrong |
| Line length bounded (dbt Labs uses 80) | A long line hides its tail in review, and the tail is where the filter is |
| Comments say why, never what | `-- join customers` above a join is noise that trains readers to skip comments, including the one that mattered |

**Automate the mechanical ones.** SQLFluff is what dbt Labs and the GitLab data team both use, and its value is not the formatting — it is that formatting stops being discussed, so review attention goes to the join cardinality instead.

### Column naming

The name is read by people who will never open the SQL, and in a consumer-facing model it is a contract with them.

- **Booleans get `is_` / `has_` / `was_`.** A boolean called `active_status` reads wrongly under negation; `not_deleted` produces double negatives at every call site.
- **Timestamps and dates carry the contract's suffix** if it sets one, and the timezone is stated in the description regardless.
- **Measures name the unit** where it is not obvious — or state it in the description, which is the better place. Two columns whose names differ only in an implied unit will be added together by somebody.
- **Rates and ratios state their scale in the description**: is `0.05` five percent or five basis points? A column whose scale is ambiguous will be formatted wrongly in a dashboard, and the dashboard is where people will see it.
- **Spell out abbreviations.** `qty`, `amt`, `cust` save characters and cost the reader a guess. The exception is an abbreviation that is genuinely the business's own vocabulary — then it belongs in a description that expands it.
- **Do not encode the source system** in a consumer-facing column name. It leaks an implementation detail into a name that is expensive to change later.
- **Ambiguous bare names — `id`, `name`, `type`, `status`, `date`, `value`** — should be qualified with the entity. After a join, `id` is a question. The GitLab handbook makes this rule explicit and it is worth copying.

### Ordering columns in the `final` CTE

Not a correctness matter, but it makes review cheaper, and a consistent order across a layer means a reviewer can spot a missing column by shape:

1. Key (surrogate or natural)
2. Foreign keys
3. Dates and timestamps
4. Dimensional attributes
5. Measures
6. Load metadata

## Checklist

- [ ] Every CTE named for what it imports or what it did; nothing numbered, abbreviated, or called `clean`
- [ ] No CTE doing two things that could be named separately
- [ ] Filters and column selection pushed into the import CTEs
- [ ] Repeated logic extracted to a model where it has two or more consumers; left inline where it has one
- [ ] Ephemeral used only where nothing will need to be inspected or tested — otherwise a view
- [ ] Jinja simple enough that the compiled output is predictable from the source
- [ ] No introspective macro in a model that must be unit tested or contracted
- [ ] Magic values lifted to the top of the file; identifier literals explained in a comment
- [ ] Compiled output read after any non-trivial Jinja change
- [ ] Explicit column list in `final`; every column qualified; explicit join types; explicit `group by`
- [ ] `as` used on every alias; trailing commas consistent
- [ ] Booleans prefixed; ambiguous bare names qualified with the entity; abbreviations spelled out
- [ ] Units and scales stated in descriptions
- [ ] Comments record decisions, not narration

## Failure modes

1. **Logic duplicated in two models rather than extracted.** They drift, one gains a filter, and the two numbers disagree with no diff that explains it.
2. **A model extracted for a single consumer.** A node, a file, and a build step maintained forever for no reuse — and the DAG is harder to read for it.
3. **Ephemeral chosen for a model that later needed debugging.** There is nothing to query, so the investigation moves to the consumer, where the intermediate state is no longer visible.
4. **A Jinja loop generating the select list.** The output schema is not visible in the source, and the model cannot be unit tested. The next person adds a column by editing a variable and does not know what else moved.
5. **An introspective macro in a contracted model.** The compiled schema changes when the warehouse changes, and the contract fails on a commit that changed nothing.
6. **A missing comma between two columns.** `amount total_amount` is valid SQL, produces one column, and silently drops the other. Explicit `as` makes it a syntax error instead.
7. **Positional `group by` after a column was inserted.** The query regroups silently and every aggregate is wrong.
8. **Aggressive whitespace stripping welding two keywords together.** The syntax error is in generated SQL, so the message points at code nobody wrote.
