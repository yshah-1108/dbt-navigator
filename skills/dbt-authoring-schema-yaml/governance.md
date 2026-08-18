# Contracts, constraints, versions, and access

Four features that all answer the same question — **what may consumers rely on, and what happens when that changes?** They are worth adopting in the order below, and worth *not* adopting on a model whose shape is still moving. dbt's own guidance is blunt about this: governance features add structure, structure makes rollbacks harder, and adopting them while models still change weekly increases maintenance without buying stability.

Everything here applies to models only. Snapshots, seeds, and sources cannot be contracted, versioned, or access-controlled, because their structure is expected to change.

## Contracts

A contract makes the model's schema enforced at build time. dbt verifies that the output has exactly the declared columns with the declared types, and fails the build if it does not.

```yaml
models:
  - name: <model_name>
    config:
      contract:
        enforced: true
    columns:
      - name: <key_column>
        data_type: varchar
        constraints:
          - type: not_null
      - name: <date_column>
        data_type: date
      - name: <measure_column>
        data_type: decimal(38,6)
```

### What it catches that a test cannot

| | Enforced at | Catches |
|---|---|---|
| Contract | Build time, before data is written | Wrong type, missing column, extra column |
| Test | After the model is built | Wrong values in a correct schema |

A contract turns "a consumer's dashboard broke because a column changed type" into "the build failed with a clear message". That is a strictly better failure, and it is the entire argument for the feature.

### Requirements and costs

- **`table` or `incremental` only.** Contracts and constraints are never applied to `view` or `ephemeral` models.
- **Every column must be listed with a `data_type`.** Omit one and the build fails. The YAML now has to move in lockstep with the SQL — that is the real cost, and it is why a contract on a model under active development gets removed, usually along with the ones that needed it.
- **Type strings are dialect-specific.** `varchar`, `string`, `text`, `numeric`, `decimal(38,6)`, `timestamp`, `timestamp_ntz`, `int64` are not uniformly available. Check `project.warehouse`.
- **A type mismatch fails hard.** A column the SQL emits as an integer, declared `varchar`, fails. Usually the SQL is what should change — declaring the intended type and casting to it is how the contract earns its keep.
- **`dbt_utils.generate_model_yaml` (codegen) writes the column list**, which removes most of the tedium of the initial adoption. Treat the output as a draft: it reports the current types, not the intended ones.

### Where to enforce

| Enforce | Do not enforce |
|---|---|
| Consumer-facing models BI tools or external systems read | Source-facing and transformation models under active development |
| Any model another team's work depends on | Models with a single consumer inside the same project |
| Versioned models | Anything whose schema you expect to change this month |

Enforce it at the boundary, not everywhere.

## Constraints

`constraints` sit alongside `data_type` and are enforced **by the warehouse**, which means support varies substantially and the variation is the important part.

dbt classifies each constraint on each platform into three states: definable and enforced, definable but not enforced (metadata only, included in the DDL), and not definable at all. Approximate current position — **verify against the adapter's own documentation, because this changes**:

| | Postgres | Snowflake | BigQuery | Redshift | Databricks |
|---|---|---|---|---|---|
| `not_null` | Enforced | Enforced | Enforced | Enforced | Enforced |
| `primary_key` | Enforced | Definable, not enforced | Definable, not enforced | Definable, not enforced | Varies by table format |
| `unique` | Enforced | Definable, not enforced | Not definable | Definable, not enforced | Varies |
| `foreign_key` | Enforced | Definable, not enforced | Definable, not enforced | Definable, not enforced | Varies |
| `check` | Enforced | Not definable | Not definable | Not definable | Varies |

The pattern: **transactional databases enforce the ANSI set; analytical warehouses enforce `not_null` and treat the rest as metadata.** Some of them will use an unenforced key constraint for query optimisation if told it can be trusted — which means declaring one that is false can produce wrong results, not merely undetected ones.

This is not theoretical. On Snowflake the opt-in is the `RELY` constraint property, which permits *join elimination*: the optimizer may drop a joined table entirely when the query needs nothing from it beyond a trusted key. Snowflake's own documentation states that if the integrity of those constraints is not maintained, query results **might differ** from the same query without `RELY`, and it has shipped a behaviour-change bundle specifically about wrong results from this optimisation extending to DML and `CREATE TABLE AS`. A duplicate in a dimension therefore stops being a data-quality nit and becomes a silently wrong number in a query that does not even mention the dimension.

`RELY` is never on by default and must be set explicitly, so the risk only exists where somebody opted in. That is checkable rather than assumable — on Snowflake:

```sql
select table_schema, table_name, constraint_type
from information_schema.table_constraints
where rely = 'YES'
  and constraint_type in ('PRIMARY KEY', 'UNIQUE')
```

If that returns rows, the `unique` tests on those models are load-bearing for correctness, not just for monitoring, and deleting one as "redundant" is a correctness regression. Run the equivalent lookup for whichever platform is in play before trusting a declared key.

The consequence that matters:

> **Keep the `unique` test even when a `primary_key` constraint is declared**, unless you have confirmed your platform enforces it.

An unenforced `primary_key` provides documentation and catalog integration. It provides no guarantee. Deleting the `unique` test as redundant is how duplicates arrive unannounced.

Two flags exist for managing the noise rather than the behaviour: `warn_unenforced: false` suppresses the warning about a supported-but-unenforced constraint, and `warn_unsupported: false` suppresses it for one the platform cannot express. Neither changes what is enforced. Silencing them is reasonable once the position is understood and documented; doing it before that removes the only signal that the constraint is decorative.

A `custom` constraint type exists for platform-specific column DDL — masking policies, tags, and similar. It requires the full column list, like any contract, and it is the only route to some platform features under a `create table as select`. Anything touching a masking or classification policy is `dbt-handling-sensitive-data` territory.

## Versions

A contract makes breaking a schema loud. Versions are how you break it deliberately, with a migration window.

```yaml
models:
  - name: <model_name>
    latest_version: 1
    config:
      contract: {enforced: true}
    columns:
      - name: <key_column>
        data_type: varchar
      - name: <column_being_removed>
        data_type: varchar
    versions:
      - v: 1
      - v: 2
        columns:
          - include: all
            exclude: [<column_being_removed>]
```

Mechanics worth knowing before deciding:

- **Each version lives in its own file**, conventionally `<model_name>_v<n>.sql`. The unsuffixed filename may hold the latest version.
- **All versions keep the model's name.** `ref('<model_name>')` resolves to `latest_version`; `ref('<model_name>', v=1)` pins.
- **`latest_version` is explicit, not automatic.** Setting it below the highest number makes the higher one a prerelease — which is how a new version gets tested in production before anyone is moved onto it.
- **Define versions as diffs from the shared column list** via `include` / `exclude`. This is the feature's actual value: on a model with eighty columns, the diff between versions is otherwise undiscoverable.
- **Each version can be configured independently** — materialise the old one as a view to cut cost while it is being migrated off.
- **`alias`** lets an old version keep the original relation name, so external consumers reading the physical table are not broken by the introduction of versioning itself.
- **dbt notifies consumers.** An unpinned `ref()` to a versioned model logs which version it resolved to and whether a prerelease exists.

### When a version is warranted

Only for a **breaking** change to a model that people, systems, or processes outside your control depend on: removing or renaming a column, changing its type or nullability, changing the grain. Adding a column is not breaking. Fixing a bug in a column's calculation is not breaking, though it may still need announcing.

**Prefer non-breaking changes, and batch the breaking ones.** Versioning on every small change produces a proliferation nobody migrates off. dbt's own recommendation is a predictable cadence — once or twice a year, announced well in advance — where the latest version is bumped and accumulated dead columns are removed together.

**A version is only a little different from copying the file to `<model>_v2.sql`.** What you get for using the feature: all live versions tracked in one place, configuration reused, version-based selection (exclude old versions in development while still building them in production), and automatic consumer notification. What you do not get is any reduction in the migration work itself.

## `deprecation_date`

Independent of versions and useful without them.

```yaml
models:
  - name: <model_name>
    deprecation_date: <YYYY-MM-DD>
```

- Referencing a model with a passed or upcoming deprecation date raises a warning; `WARN_ERROR_OPTIONS` can promote those to errors, which is how a deprecation becomes a deadline rather than a suggestion.
- **A model with an enforced contract cannot be deleted before its deprecation date.** dbt refuses, to protect consumers. This is a feature and it surprises people mid-cleanup.
- **dbt does not drop the relation.** A deprecated model keeps building and keeps occupying storage until it is disabled or removed, exactly as with a deleted model. Deprecation communicates; it does not clean up.

There is no selector for `deprecation_date`. `dbt ls` with JSON output and explicit output keys is how you enumerate them.

Removing the model afterwards is `dbt-breaking-changes`.

## Groups, access, and owner

```yaml
groups:
  - name: <group_name>
    owner:
      name: <team>
      email: <contact>
    description: "What this group is responsible for."

models:
  - name: <model_name>
    config:
      group: <group_name>
      access: private
```

| `access` | Referenceable by |
|---|---|
| `private` | Models in the same group only |
| `protected` | Any model in the same project or in a package installed into it |
| `public` | Any group, package, or project |

- **`protected` is the default**, for backward compatibility. A `ref()` violating access raises a parse-time reference error, which makes this the only mechanism in dbt that mechanically prevents an unwanted dependency.
- **`group` and `access` belong under `config:`** as of 1.10. Top-level keys still work but do not participate in config inheritance, so a `dbt_project.yml` default will not apply to them.
- **Setting them per directory in `dbt_project.yml`** is how a whole layer becomes private in one edit — and how a model becomes public accidentally, so check what a directory-level default covers.
- **A group requires an owner.** That is most of the value: it answers "who do I tell" without asking around, which is the same problem exposures solve from the other direction.

This is the enforceable version of the terminal-layer convention in `dbt-authoring-sql-models`. Where that rule relies on review, `access: private` relies on the parser. If a project has a layer nothing is supposed to `ref()`, making it private is strictly better than documenting it.

Note that model access is not user access. It governs which models may reference which, not who may see data.

## Adoption order

1. **Descriptions and tests.** Nothing below is worth anything on an undocumented, untested model.
2. **`deprecation_date` when you need it.** Zero standing cost, and it works alone.
3. **Groups and owners.** Cheap, immediately useful, and no build-time constraint.
4. **`access: private` on the layers that should not be built on.** Converts a convention into an enforced rule.
5. **Contracts on the boundary.** Real ongoing cost; the payoff is a build-time failure instead of a broken consumer.
6. **Versions, only when a breaking change to a contracted model is actually needed.**

Skipping to 5 or 6 on a model still under development is the standard way these features get a bad reputation and then get removed.

## Checklist

- [ ] Contract enforced only at consumer boundaries, on `table` or `incremental` models
- [ ] Every contracted column has a `data_type` valid for `project.warehouse`
- [ ] Constraint enforcement confirmed for the platform, not assumed
- [ ] `unique` test retained alongside any unenforced `primary_key` constraint
- [ ] `warn_unenforced` / `warn_unsupported` silenced only after the position is documented
- [ ] A new version created only for a genuinely breaking change to a model with external consumers
- [ ] Versions defined as diffs via `include` / `exclude`, not copied in full
- [ ] `latest_version` set deliberately; prerelease used before promotion
- [ ] `alias` used where an old version must keep its original relation name
- [ ] `deprecation_date` set with the knowledge that the relation is not dropped automatically
- [ ] Every group has a reachable owner
- [ ] `group` and `access` under `config:`, and directory-level defaults checked for scope
- [ ] `access: private` applied to any layer nothing should `ref()`

## Failure modes

1. **A `primary_key` constraint trusted as a uniqueness guarantee** on a platform that treats it as metadata. The `unique` test was deleted as redundant.
2. **An enforced contract on a model still being developed.** Every SQL change needs a matching YAML change, the friction is noticed, and the contract is removed — from the models that needed it as well.
3. **A contract whose types were copied from an example written for another engine.** The build fails on type names the adapter does not recognise, which reads as a dbt problem.
4. **A version created for a non-breaking change.** Two relations to maintain and nobody has a reason to migrate.
5. **Versions adopted without a deprecation date.** Old versions accumulate indefinitely, each costing storage and build time, because nothing states when they end.
6. **A deprecated model assumed to have stopped building.** It builds every run, costs the same, and reports no problem.
7. **A contracted model that cannot be deleted** because its deprecation date has not passed, discovered mid-cleanup.
8. **A directory-level `access: public` default** making implementation-detail models referenceable across projects, discovered when something outside the project depends on one.
9. **A group with no reachable owner.** The one question the group existed to answer is unanswerable.
