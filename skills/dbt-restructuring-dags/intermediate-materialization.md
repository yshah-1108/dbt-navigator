# Choosing a materialization for an intermediate node

Read [SKILL.md](SKILL.md) first — restructuring constantly creates new intermediate models, and the operations there tell you when. This document is the choice between `ephemeral`, `view`, and `table` for those nodes: how they differ, two claims about ephemeral that are commonly repeated and should not be, a practical default, and the guardrails on deep ephemeral chains.


Restructuring constantly creates new intermediate models, and the materialization chosen for them is the decision most often made by copying the neighbour. The three candidates behave very differently.

| | `ephemeral` | `view` | `table` |
|---|---|---|---|
| Object in the warehouse | None | View | Table |
| Storage | None | None | Full copy |
| Compute | Re-executed inside **every** consumer, every time | Re-executed on every read | Once per build |
| Queryable by hand | **No** | Yes | Yes |
| Inspectable when debugging | Only by reading compiled SQL | Yes | Yes |
| Tests on it | Not in the usual sense — no relation to test | Yes | Yes |
| Grants, hooks, contracts | **None** | Grants and hooks yes; contracts partially — names and types, not constraints | Yes |
| Reachable from `dbt run-operation` | No | Yes | Yes |
| Stale? | Never | Never | Until the next build |

The mechanic that drives the whole comparison: an ephemeral model is compiled into a CTE and **inlined into each consumer separately**. Five consumers means the SQL is compiled into five statements and executed five times. There is no shared computation. That is fine for a thin filter or rename and expensive for anything that scans or aggregates.

Two claims about ephemeral models that are commonly repeated and should not be:

- **"Ephemeral is faster because the optimizer sees the whole query."** Sometimes true, sometimes false, and entirely dependent on how the engine handles CTEs — some materialize a CTE as an optimization fence, some inline it. The reliable benefits of ephemeral are *fewer objects* and *zero storage*, not a better plan. Do not claim a performance win you have not measured on the project's own warehouse.
- **"Ephemeral keeps the warehouse clean, so prefer it for intermediates."** Materializing intermediates as views in a separate schema achieves the same tidiness while keeping them queryable and testable. The published guidance offers both, and the view option is the more robust one as the number of models grows.

A practical default: **ephemeral for a light transformation early in the DAG with one or two consumers that nobody needs to query; view for anything you might have to debug or test; table when the computation is expensive enough that recomputing it per read or per consumer costs more than storing it.**

Two guardrails on deep ephemeral chains specifically. Five levels of ephemeral-on-ephemeral compile into a deeply nested statement that is hard to read, hard to debug, and on some engines hard to plan — break the chain with a materialized link. And a unit test on a model with an ephemeral parent must supply that input as raw SQL rather than as a dictionary or CSV fixture, because there is no relation to introspect for column types; if the project unit-tests heavily, that alone argues against ephemeral parents. See `dbt-unit-tests`.
