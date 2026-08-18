# Per-engine protection mechanisms

Everything here is gated on `project.warehouse`. Read it from the contract before using any of it, and if the contract has no `warehouse` field, establish the adapter first. A statement written for the wrong engine either errors — recoverable — or is accepted and does nothing, which ends the conversation with an engineer believing regulated data is protected when it is in plaintext.

**Version dependence is the rule, not the exception.** Every mechanism below has changed at least once, and several changed in ways that alter whether protection survives a rebuild. Where this document says "verify", it means run the check against the account and runtime the project actually uses. Do not substitute the documentation's current state for the deployment's current state.

## What exists where

Read the rows you need, not the whole table. The **Follows a derived table?** column is the one that decides whether a dbt model built from a protected column is itself protected.

| Engine | Column masking | Row filtering | Column tagging / classification | Follows a derived table? |
|---|---|---|---|---|
| Snowflake | Masking policies, applied directly or via a tag | Row access policies | Object tags; automated classification assigning system tags | **Only if tag propagation is deliberately configured** — see below |
| BigQuery | Dynamic data masking, via a data policy on a policy tag | Row-level access policies | Policy tags in a taxonomy | **No** for a query with a destination table. Yes for a same-region table copy job |
| Databricks (Unity Catalog) | Column masks — a SQL UDF bound to a column | Row filters — a SQL UDF bound to a table | Tags on catalogs, schemas, tables, columns | **No** for a new table. But **`REPLACE TABLE` retains masks and filters**, which matters for dbt |
| Redshift | Dynamic data masking policies | Row-level security policies | No first-class column classification | **No** |
| Postgres | **None.** Column-level `GRANT` and views are the only levers | Row security policies, per table | **None** | **No** — nothing to follow |
| DuckDB | **None** | **None** | **None** | Not applicable |
| Trino | Depends entirely on the connector and any access-control plugin | Same | Same | Unknown without knowing the plugin |
| Anything else | **Unknown** | **Unknown** | **Unknown** | Withhold guidance and ask |

Three engines have nothing to offer for column masking: **Postgres, DuckDB, and Redshift has masking but no classification vocabulary at all.** On Postgres and DuckDB, do not describe a view as "equivalent to a masking policy" — a view withholds a column from people who query the view, and does nothing about people who query the table.

---

## Snowflake

### The propagation feature, and its conditions

A masking policy attached to a column of one table is not attached to a column of a table built from it. That is the default. Snowflake does offer real propagation, and it is worth using, but it is a configured feature with conditions people get wrong:

- Propagation is a property of a **tag**, set with `CREATE TAG ... PROPAGATE` or `ALTER TAG`. The policy attaches to the tag; the tag propagates; the policy follows.
- It can be configured for **object dependencies** (a view over a tagged table), for **data movement** (`CREATE TABLE AS SELECT`, `CREATE DYNAMIC TABLE`, and `INSERT`, `MERGE`, `UPDATE`, `COPY INTO`), or both. A project that enabled dependency propagation only will not protect a dbt `table` model.
- **Tags applied through data movement are not continuously updated.** Change the tag on the source later and the derived relation keeps the tag value it was given at build time. A tag that reads as current may be a snapshot from months ago.
- **System tags do not propagate, and inherited tags do not propagate.** This is the trap for anyone relying on automated classification: classification applies *system* tags, so classification alone does not propagate. Map the system tag to a user-defined tag and propagate that one.
- A tag-based policy set on a **database or schema** is inherited by tables in it. Clone or move the table elsewhere and it is governed by the *target* schema's policy, not the source's. A personal development schema with no policy therefore strips the protection — see the cloning section in `SKILL.md`.
- `CREATE TABLE ... CLONE` and `CREATE TABLE ... LIKE` **always copy tags**, regardless of the propagate property. Zero-copy clone is the one operation where tags come along by default.

Verify, per relation, rather than assuming the account setting covers you:

```sql
-- what policies are actually attached to this relation
select * from table(
  <governance_db>.information_schema.policy_references(
    ref_entity_domain => 'TABLE',
    ref_entity_name   => '<db>.<schema>.<relation>'
  )
);
```

An empty result here means no policy is attached to that relation. It does not mean the mechanism is broken — but see the instrument-coverage rule in `SKILL.md` before reading any empty governance result as good news.

### Applying protection at create time

Snowflake accepts a masking policy inside a `CREATE TABLE ... AS SELECT`, when the column list is declared:

```sql
create table <target> (
  <column> string with masking policy <policy_name>
) as select <column> from <source>;
```

This closes the post-hook window entirely, because the relation is never committed unprotected. Two conditions: the role needs `APPLY` on the masking policy, and dbt's `table` materialisation does not emit column definitions, so getting this requires either a custom materialisation or the tag-based route. **Prefer the tag route** — it is the supported path and it does not fork the materialisation.

### Materialized views are a dead end here

This surprises people who reach for `materialized_view` to get a "cheap protected copy":

- A masking policy **cannot** be set on a column of a materialized view once the rewrite exists, and a materialized view **cannot** be created including a column that has a masking policy on the base table. It fails with an unsupported-feature error.
- A masking policy cannot be added to a base-table column if a materialized view already exists over that table. You have to drop the view first.
- A row access policy cannot be added to a table that has a materialized view over it, and a materialized view cannot be created from a table that already has one.
- **Dynamic tables do support masking policies, row access policies and tags.** This matters in dbt because `dbt-snowflake` implements `materialized_view` as a dynamic table rather than a Snowflake materialized view — so the config name and the underlying object are not what a reader of the Snowflake docs would expect. Confirm which object your adapter version creates before reasoning about policy support.

### Secure views, and the inference leak

A non-secure view can leak the rows it filters out. Snowflake documents the mechanism: the optimizer may push a predicate from the outer query *underneath* the view's own security filter, and a predicate crafted to raise an error on a specific value then reveals whether such a row exists. The classic form divides by zero on the matching row — the querying role sees no rows it is not entitled to, and learns that a matching row exists.

`secure: true` on a Snowflake view in dbt disables those optimizations and hides the view definition from roles that do not own it. **If a view is the protection — if it exists so that a role sees a subset — it must be secure.** A view that exists only to avoid repeating a join does not need it, and secure views do cost performance.

Two honest limits Snowflake states itself: a secure view still exposes approximate volume through query duration, and for genuinely high-stakes separation the recommendation is a materialised relation per audience rather than one view over everything.

### Other Snowflake constructs worth knowing by name

| Construct | What it does | Where it misleads |
|---|---|---|
| Classification | Samples columns and applies system tags for a **semantic category** (what kind of attribute) and a **privacy category** (`IDENTIFIER`, `QUASI_IDENTIFIER`, `SENSITIVE`) | It is a sampling recommendation with a confidence level, not a guarantee. A `HIGH` confidence result is still an inference, and an unclassified column is not a clean one |
| Projection policy | Allows a column to be joined and filtered on but not returned in a result | Snowflake states plainly that it does not stop a determined actor, and that an error message can occasionally contain a value from the column |
| Aggregation policy | Requires results to be grouped to a minimum group size | Protects against single-record identification, not against a chain of overlapping aggregates |
| Row access policy | Filters rows by role or attribute at query time | The same column cannot appear in both a masking policy signature and a row access policy signature. Row access policies evaluate **before** masking policies |

---

## BigQuery

### Policy tags do not survive a query

Google documents this directly: if you write query results into a new table, the destination has no policy tags, so it has no column-level access control. Exporting to object storage is the same. The only exception is a **table copy job**, which applies no transformation and therefore does propagate policy tags — and not across regions, because a cross-region copy of a policy-tagged table is rejected outright.

Every dbt `table` model is a query with a destination. So on BigQuery, **a mart built from a policy-tagged column is unprotected unless the model re-declares the tag.** dbt supports that per column:

```yaml
models:
  - name: <model>
    config:
      persist_docs:
        columns: true          # required, or policy_tags are silently not applied
    columns:
      - name: <column>
        policy_tags:
          - '{{ var("<taxonomy_var_for_this_tag>") }}'
```

Three details that produce silent failures:

- **`policy_tags` does nothing without `persist_docs: {columns: true}`.** dbt applies tags in the same step that persists column comments. No tag, no error, no protection.
- Only columns **declared in YAML** are visited. A column absent from the `columns:` list is never tagged, so a model documenting three of forty columns tags three of forty.
- BigQuery permits **one policy tag per column**, despite the config being a list, and the tag is a full resource path. Keep the paths in `vars` rather than repeating them — a typo in a path is a tag that does not exist.

Views are the exception in the right direction: a logical or authorized view resolves against the base table, so column-level access control and row-level policies apply through it.

### Row-level access policies and masking on BigQuery

| Behaviour | Consequence for a pipeline |
|---|---|
| Copying a table with row-level policies requires `TRUE` filter access on the source, and the policies are copied to the destination | The one place BigQuery *does* carry protection forward — but only for a copy job, not a query |
| Copying a source **without** policies over a destination **with** them removes the destination's policies, unless the write is an append | A rebuild can strip row-level security from the target. This is the row-level analogue of dropped grants |
| Overwriting a destination table removes existing policy tags unless a schema carrying them is supplied | Same shape: the rebuild is the moment protection disappears |
| Masking is compatible only with **non-subquery** row-level policies | A subquery policy plus masking requires raw-read access on the referenced columns, which defeats the point |
| Masking is applied on top of row-level security | A role can see the rows it is entitled to with the filtering column itself masked |
| Row access policies are rejected under legacy SQL, and cannot be applied to JSON columns | A legacy-SQL consumer does not get filtered results; it gets an error |
| Masking on partitioned or clustered columns is not supported by default and can raise cost significantly | The obvious "mask the date" instinct is expensive and may not be available |
| Column data policies are unavailable in some regions | Verify for the project's region rather than assuming account-wide availability |
| `SELECT *` by a role without fine-grained read fails with an error naming the columns | Useful: it is one of the few cases where the protection announces itself |

Two roles decide what a reader sees, and confusing them is common: **Fine-Grained Reader** grants raw values, **Masked Reader** grants masked values. Grant Masked Reader at the data-policy level; granting it at project level or above hands it out for every policy underneath.

`grant_access_to` in dbt sets up an **authorized view** — the view may read datasets the *querying user* cannot. That is a deliberate privilege-elevation tool, and it is a different feature from `grants`. An authorized view over a sensitive dataset is a decision to let people read a derived result without reading the source; make sure that is what was intended, because it also means the view's own definition is now the entire access control.

---

## Databricks (Unity Catalog)

Column masks and row filters are SQL UDFs bound to a table, typically switching on `is_account_group_member(...)`:

```sql
create function <mask_fn>(v string)
  return if(is_account_group_member('<group>'), v, 'REDACTED');

alter table <table> alter column <column> set mask <mask_fn>;
alter table <table> set row filter <filter_fn> on (<column>);
```

What makes Databricks genuinely different, and it is in dbt's favour:

- **`REPLACE TABLE` retains row filters, and retains column masks for columns whose names are unchanged**, even when not redeclared. Databricks states the reason: preventing accidental loss of data access policies. Since a dbt full refresh replaces the relation, **a rebuild does not strip the mask here** — the opposite of every other engine in this document. Verify it against the runtime in use rather than taking it on trust, and note it only holds for a replace, not for a differently-named new table.
- **Retention cuts both ways: a retained policy that references a column you removed or renamed can make subsequent queries fail.** Because the policy survives the replace but its target column does not, the break appears *after* a successful build, in whoever queries next — not in the dbt run that caused it. So on Databricks a column rename or removal in a masked model is a two-part change: the model and the policy, resolved with `ALTER TABLE`. Renaming a masked column also silently *ends* the masking if the policy is not moved, which is the dangerous direction: the query keeps working and the data stops being protected.
- A **new** table built by `CREATE TABLE AS SELECT` inherits nothing. Same rule as everywhere else.
- Masks and filters are **not inherited from a catalog or schema**, unlike ordinary privileges, which *do* inherit downward. So a catalog-level `SELECT` grant reaches every table while a catalog-level mask does not exist. Newer attribute-based policies attach at catalog or schema level and apply by governed tag; treat that as version-dependent and confirm availability before recommending it.
- Runtime matters and it is not cosmetic. Below Databricks Runtime 12.2 LTS, masks and filters are unsupported and the platform **fails securely — no data is returned**. That is a correctness incident presenting as an empty result, and the natural diagnosis ("the upstream is empty") is wrong.
- Dedicated (single-user) access mode cannot read masked or filtered tables at all on older runtimes, gained read-only support later, and writes came later still and require patterns such as `MERGE INTO`. **If a dbt job runs on dedicated compute, verify it can write to a filtered table before assuming an incremental model will work.**
- Temporary tables support neither masks nor row filters. Column masks also cannot be applied to columns referenced by generated columns.
- Applying a mask needs `EXECUTE` on the function plus `USE SCHEMA` and `USE CATALOG`; changing one on an existing table needs ownership or `MANAGE`. A build role that can create tables cannot necessarily apply masks.

---

## Redshift

Dynamic data masking and row-level security both exist, subject to version. Neither follows a `CREATE TABLE AS SELECT`: Redshift documents that a CTAS table does not inherit constraints, defaults, identity columns or the primary key, and masking and RLS policies are attached to the relation the same way. **Every dbt table model needs its policies reattached.**

Specifics that decide whether an approach is viable at all:

- **RLS is applied before masking.** A masking expression cannot read a row that RLS filtered out, so a masking policy that consults another column may see nothing.
- Masking policies **cannot** attach to system tables and catalogs, external tables, data-sharing tables, cross-database relations, or temporary tables. External tables in particular are a common landing zone.
- Lookup tables inside a masking policy cannot be views, materialized views, late-binding views, external tables, temporary tables or cross-database relations. The obvious "join to a mapping view" design is not available.
- Masking cost lands on every query: Redshift's guidance is to prefer simple expressions, because a policy calling an external function runs per row before predicates and projections.
- Several session-level identifier settings cannot be changed while querying an RLS-protected relation, and RLS may limit query optimization. A protected relation can be measurably slower and can reject a session that a BI tool configured itself.
- Regular users cannot replace a view that has RLS attached without detaching the policy first. A dbt view model over an RLS-protected view will fail on rebuild unless the build role is privileged.
- Column-level `GRANT` on specific columns is available and is the cheapest control here. It is also the bluntest: the reader gets an error rather than a masked value.

---

## Postgres

There is no column masking. Do not synthesise one.

What exists:

- **Column-level privileges.** `GRANT SELECT (<col_a>, <col_b>) ON <table> TO <role>` restricts which columns a role may read. `SELECT *` then errors rather than returning a subset.
- **Row security policies**, enabled per table with `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`. Enabling with no policy is **default-deny** — every row disappears for everyone except the exempt, which is a fine way to make a dbt build return zero rows with no error.
- **Views**, which withhold columns from readers of the view.

Three exemptions decide whether any of it applies to dbt:

- **Superusers and roles with `BYPASSRLS` always bypass row security.** If dbt connects as one, the build reads everything, and a person testing "does RLS work" as that role learns nothing.
- **A table's owner normally bypasses row security too**, unless `ALTER TABLE ... FORCE ROW LEVEL SECURITY` is set. dbt usually owns what it creates, so dbt is usually exempt from policies on its own relations.
- **A view historically runs as its owner, which is how RLS gets bypassed silently.** `security_invoker = true` on the view makes permission checks and policies evaluate as the *calling* user — available from Postgres 15, **not the default**, and not applied retroactively by an upgrade. With it on, the caller also needs direct privileges on the underlying tables, which is a real schema-permissions change rather than a flag flip.

So on Postgres the honest summary is: **access control is a grant question, and a view is a grant surface, not a masking mechanism.** If values must be hidden from a role that can read the relation, the answer is to not carry the values — a hash, a bucket, or omission.

---

## DuckDB and Trino

**DuckDB has nothing.** No masking, no row policies, no classification. It is also typically a local file, which makes the file itself the exposure: an unencrypted database on a laptop is the least protected copy anywhere in the pipeline, it is outside every audit trail, and it is backed up by whatever the laptop backs up to. Treat "materialise it locally to look at it" as a data transfer, because it is one.

**Trino enforces whatever its connector and access-control plugin enforce, and nothing otherwise.** Two symmetric mistakes: assuming a policy in the underlying system is enforced through Trino, and assuming a policy configured in Trino protects anyone who reaches the underlying system directly. Establish which layer holds the control before making a claim about either.

---

## The verification that holds on every engine

Whatever the mechanism, one check decides whether protection is real:

**Query the new relation as a role that should not see the values.** Building as your own privileged role and observing real values proves nothing about anyone else, and observing masked values as a privileged role usually means the mask is broken rather than working.

If you cannot assume such a role, say the protection is **unverified**. That is a different claim from "applied", and the difference is the whole point.
