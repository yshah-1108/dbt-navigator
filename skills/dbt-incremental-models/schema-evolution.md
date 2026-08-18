# Schema evolution on an incremental model

Read [SKILL.md](SKILL.md) first for the `on_schema_change` settings table and why `ignore` (the unset default) is the trap. This document is the precise behaviour each setting has on **existing rows**, the behaviours that surprise people, and the rule for a contracted model. For propagating a new column end to end, see `dbt-adding-columns`; for breaking changes on a contract, see `dbt-breaking-changes`.

Precision on what each does to **existing rows**, since that is the part that gets assumed:

| | New column's value in historical rows | Removed column's data |
|---|---|---|
| `fail` | n/a — nothing happens until a human acts | n/a |
| `append_new_columns` | Null, permanently, until a backfill | Retained, and now unmaintained |
| `sync_all_columns` | Null, permanently, until a backfill | **Dropped. Gone.** |
| `ignore` | The column does not exist at all | Retained |

**No setting backfills a new column for existing rows.** New rows get values, historical rows stay null. See `dbt-adding-columns` for the sequence that populates them.

Three further behaviours worth knowing before you rely on this config:

- **Removing a column from the model under `ignore` fails the run** rather than being ignored, because the insert's column list no longer matches. `ignore` is asymmetric: additions are silently dropped, removals error.
- **Detection is top-level only.** Changes inside a nested or struct-typed column are not detected, so on warehouses with nested types a schema change can pass every setting including `fail`.
- **A type change is only handled by `sync_all_columns`.** Under `append_new_columns` a column whose type changed keeps the old type in the target, and the values are coerced on insert — silently, and possibly lossily. Widening a string type is handled separately by the adapter; narrowing or changing the family is not.

## With an enforced contract

A contracted incremental model must set `on_schema_change` to `append_new_columns` or `fail`. The reason is specific: under `ignore`, the upsert is built from the columns that already exist in the target, so it succeeds while the new column never lands — leaving the YAML contract describing a table that does not exist in that shape. The contract is now false, and nothing detected it.

`sync_all_columns` is wrong on a contracted model for the opposite reason: dropping a column is a breaking change for consumers, and a contract exists to make breaking changes deliberate and versioned. See `dbt-breaking-changes`.
