# Governance mechanisms: contracts, versions, deprecation, access

dbt has four features for making an interface explicit and changing it on a schedule instead of by surprise: **contracts** (the shape is declared and enforced at build time), **`access` and `groups`** (who may reference it), **`versions`** (old and new shapes coexist), and **`deprecation_date`** (the end of life is declared and warned about).

They are worth understanding before you reach for them, because each one adds friction permanently in exchange for safety at one moment. dbt's own guidance is that governance features make rollbacks harder and raise maintenance cost, and that adopting them while models are still churning complicates future change. **If the project does not already use these features, do not introduce them for a single rename.**

## Version and platform dependence

State these when you recommend any of it. Presenting one version's behaviour as universal is how an agent produces confidently wrong advice.

| Feature | Availability |
|---|---|
| `contract: {enforced: true}`, `access`, `groups`, `versions` | dbt Core 1.5 and later |
| `deprecation_date` | dbt Core 1.6 and later |
| Removing or modifying a `constraints` entry counted as a breaking change | dbt Core 1.6 and later |
| `access` declared under a `config:` block | 1.10 and later; earlier versions use the top-level key, which does not inherit from `dbt_project.yml` |
| Promoting deprecation warnings to errors by warning name | The option list was renamed (`include`/`exclude` became `error`/`warn`); a `Deprecations` group covering all deprecation warnings arrived in 1.10 |
| Cross-project `ref()` | A paid dbt platform tier, not dbt Core |
| Contracts on `materialized_view`, `ephemeral`, Python models, or on recursive-CTE models on BigQuery | Not supported |
| Contracts on sources, seeds, snapshots | Not supported — governance features are model-only |
| Constraint *enforcement* | Varies by platform; see the table below |

Read `project.dbt_version` and `project.warehouse` from the contract before asserting any of it. With those fields absent, say which version and platform your statement assumes.

---

## Contracts

```yaml
models:
  - name: <model>
    config:
      contract:
        enforced: true
    columns:
      - name: <key_column>
        data_type: int
        constraints:
          - type: not_null
      - name: <text_column>
        data_type: string
```

Two things happen at build time that do not happen otherwise:

1. A **preflight check** compares the columns the model's query will return — name and `data_type` — against the `columns:` list, and fails before writing any data if they disagree. The check is order-agnostic: column order in the SQL need not match the YAML.
2. The declared names, types, and constraints are put into the **DDL dbt submits**, and the relation's columns are ordered per the contract rather than per the query.

Consequences that surprise people:

- **Every column must be declared.** A contract is not a partial assertion; there is no "declare the important ones" mode.
- **Size, precision, and scale are not compared.** `varchar(256)` versus `varchar(257)` will not fail. But an unspecified `numeric` may default to scale 0 and then fail on a value with decimals — declare `numeric(38,6)` or similar rather than bare `numeric`.
- **`data_type` strings are aliased.** `string` is translated to the platform's spelling (`text` on Postgres and Redshift, for instance). That aliasing can be turned off with `alias_types: false`, at which point the spelling must be exactly what the platform uses.
- **A contract is not a test.** It asserts shape at build time, not content. Uniqueness and referential integrity remain the job of data tests. dbt's own analogy: for an API, the response structure is the contract; reliability is important but is not part of it.

### What counts as a breaking change to a contract

dbt compares against a previous project state (the `state:` selection methods, typically in a CI job) and errors on a breaking change. Its list:

| Change | Breaking? |
|---|---|
| Removing a column | Yes |
| Changing an existing column's `data_type` | Yes |
| Removing or modifying a `constraints` entry on an existing column | Yes |
| Removing `contract: {enforced: true}` from a model that had it | Yes |
| Changing the materialization while enforced constraints are declared | Yes |
| Changing an unversioned contracted model in any of the above ways | Yes — and dbt also warns when a model has or had a contract but is not versioned |
| Adding a new column | No |
| Adding a new constraint to an existing column | No |

```bash
# in CI, against the production manifest
dbt build --select "state:modified+" --state <path/to/prod/artifacts>

# just the contract dimension of the comparison
dbt list --select "state:modified.contract" --state <path/to/prod/artifacts>
```

Two limits to state honestly. First, this check needs a stored previous manifest; without one in CI, none of it fires and the contract only protects against a mismatch between YAML and SQL in the current commit. Second, **the check is about declared shape, not meaning.** Recomputing a column so that every value changes, while its name and type stay the same, is not a contract breach and dbt will not flag it — and it is the change most likely to make a consumer wrong. dbt says this outright: whether such a change is a bug fix or a behaviour change is a judgment call the model's owner has to make.

### Constraint enforcement is platform-dependent

`constraints` fall into three categories: definable and enforced, definable but decorative, and not definable. Roughly:

| Constraint | Commonly enforced | Commonly definable but not enforced |
|---|---|---|
| `not_null` | Snowflake, BigQuery, Postgres, Redshift and others enforce it | — |
| `primary_key`, `unique`, `foreign_key` | Transactional databases such as Postgres | Cloud warehouses generally accept the declaration and do not enforce it |
| `check` | Postgres and some others | Not definable on several platforms |

Check the platform-specific table in dbt's constraints documentation for the project's adapter before claiming a constraint protects anything. **A declared-but-unenforced `primary_key` is metadata.** It will not stop duplicate rows, and a project that dropped its uniqueness *test* in favour of a `primary_key` constraint on a cloud warehouse has quietly stopped checking uniqueness. Say so if you see it.

### Contracts and incremental models

A contracted incremental model must set `on_schema_change` to `append_new_columns` or `fail`. This is a documented requirement, not a suggestion, and the reason is specific: with `ignore` (the default when unset), dbt does not add a new column to the existing relation, while the merge or insert still succeeds against the pre-existing destination columns. The result is a relation whose shape differs from its own contract — the contract is now false and nothing errored.

`sync_all_columns` is excluded because it drops columns that disappear from the query, and dropping a column from a contracted model is precisely the breaking change the contract exists to prevent.

---

## `access` and `groups`

```yaml
groups:
  - name: <group_name>
    owner:
      email: <team_email>

models:
  - name: <model>
    config:
      group: <group_name>
      access: public          # private | protected | public
```

| `access` | Referenceable by | Use it for |
|---|---|---|
| `private` | Models in the same group only | Intermediate steps that are an implementation detail of one group's pipeline |
| `protected` (default) | Any model in the same project | Everything, until there is a reason otherwise |
| `public` | Any group, package, or project | Models you have decided to support as an interface |

Violations are a parse-time error, not a runtime one, which makes this the rare governance feature that gives you a fast, cheap signal.

Two things this changes about breaking-change work:

- **`public` is a blast-radius fact.** It declares that consumers exist outside what your DAG can see. Treat a change to a `public` model as Critical by default.
- **Tightening `access` is itself a breaking change.** Moving a model from `protected` to `private` breaks every reference from outside its group, and those references may be the first time anyone learns they exist. Add `private` incrementally, after confirming there are no out-of-group references — not as a blanket default applied to a folder.

Changing a model's `group` also has reference consequences, since `private` resolves relative to the group. dbt's state comparison does treat `group` as a config, so a group change shows up as a modification — but with partial parsing enabled a group rename may re-parse only the changed model, so the broken downstream reference surfaces only if CI selects downstream nodes (`state:modified+`) too.

---

## `deprecation_date`

```yaml
models:
  - name: <old_model>
    deprecation_date: 2026-06-30            # or with a time and offset
    description: >
      Deprecated. Replaced by <new_model>. Removed after 2026-06-30.
      Migration: <what the consumer must change>.
```

Accepted formats are `YYYY-MM-DD`, with optional time and UTC offset. **Without an offset it is interpreted in the system time zone of whatever machine runs dbt**, which is a real ambiguity for a date that is supposed to be a commitment — include the offset.

What it does:

| Warning | Fires when | Who sees it |
|---|---|---|
| Deprecated model declared | Parsing a project that declares one | The producer |
| Reference to a model whose date has passed | A `ref()` to it | Producer and consumers |
| Reference to a model whose date is upcoming | A `ref()` to it | Producer and consumers |

This turns a silent dependency into a visible one at parse time, which is the whole value. To make it binding rather than advisory, promote those warnings to errors once the window closes:

```yaml
# dbt_project.yml
flags:
  warn_error_options:
    error:
      - DeprecatedModel
      - DeprecatedReference
      - UpcomingReferenceDeprecation
```

Promote them in CI first. Promoting deprecation warnings to errors in production is how a scheduled build starts failing at midnight on a date somebody typed six months earlier.

**One thing it does guarantee.** A model with an *enforced contract* cannot be deleted before its `deprecation_date` has passed — dbt refuses, specifically to stop a producer removing something consumers were promised. Deleting a *versioned* model early raises an error in development runs and fails jobs. This is the one place in this skill where the tool enforces the timeline rather than merely announcing it, and it is an argument for setting the date **before** you want to remove the model rather than after: the date is what buys the protection.

Four limits, each of which has burned someone:

1. **It does not stop the model from being built.** A deprecated model keeps running, keeps costing compute and storage, and keeps looking alive until it is disabled or deleted.
2. **It does not drop the relation.** Exactly as with a deleted model, the warehouse object survives. See the dead-relation problem in `SKILL.md`.
3. **Non-dbt consumers see nothing.** BI tools, notebooks and ad-hoc queries get no warning at any point. The date is a message to the dbt project, not to the warehouse.
4. **On a contracted or versioned model it constrains *you*.** dbt refuses to let you delete a model with an enforced contract before its `deprecation_date`, and errors if you try to remove a versioned model early. That is the feature working — but it means the date you type is a commitment you cannot quietly shorten.

There is no selector for deprecation state. To list what is deprecated, read it out of the manifest:

```bash
dbt list --quiet --output json --output-keys name database schema alias deprecation_date
```

---

## `versions`

Versions let two shapes of the same model exist at once so consumers migrate on their own schedule. This is the heaviest option available and it is only worth it when consumers are genuinely outside your control — other teams, other projects, systems that query the warehouse directly.

```yaml
models:
  - name: <model>
    latest_version: 1
    config:
      contract: {enforced: true}
    columns:
      - name: <key_column>
        data_type: int
      - name: <dropped_column>
        data_type: varchar
    versions:
      - v: 1                       # matches the top-level definition
      - v: 2
        columns:
          - include: all
            exclude: [<dropped_column>]
```

Mechanics worth knowing before you commit to this:

| Aspect | Behaviour |
|---|---|
| File layout | dbt expects `<model>_v<v>.sql`. The latest version may live in `<model>.sql` with no suffix. `defined_in:` overrides this, and should not be used without a reason |
| Relation name | Defaults to `<model>_v<v>`, *not* `<model>` — so the physical name changes the moment a model becomes versioned. Set `alias` on the old version to keep its original relation name for existing consumers |
| `ref()` resolution | `ref('<model>')` resolves to `latest_version`. `ref('<model>', v=1)` pins. An unpinned reference emits a message telling the consumer that a newer or prerelease version exists |
| `latest_version` | If unset, the numerically greatest version is latest. Set it explicitly to keep a new version in "prerelease" while it is tested |
| Selection | `--select <model>` builds all versions; `<model>.v2` or `<model>_v2` builds one; `version:latest`, `version:prerelease`, `version:old`, `version:none` select by lifecycle stage |
| Column inheritance | Each version declares only its diff from the top-level `columns:` via `include`/`exclude`, which is what makes the difference between versions readable on a model with a hundred columns |
| Cost | Every live version is a relation that gets built. dbt's own recommendation is to keep two or three live, not more, and to define old versions as thin `select` transformations over the latest so they cost almost nothing to maintain |

Two traps:

- **A reimplemented `generate_alias_name` macro breaks versioning.** The built-in macro contains the logic that appends the version suffix. A project that overrode it before versions existed will produce colliding relation names. Check `macros/` for an override before recommending versions.
- **Versions are not version control.** Git holds one state of the project; versions hold several shapes of one model in the warehouse simultaneously. If the goal is "roll back my change", the answer is git, not this.

### Choosing between a version and a new model

Functionally they are close, and dbt says so. A versioned model gives you: all live versions tracked in one place, configuration shared with only the diffs highlighted, lifecycle-aware selection, and automatic notification of consumers. A new model gives you none of those and costs nothing to set up.

| Situation | Reach for |
|---|---|
| Consumers are all dbt models in this project | Neither. Update them in the same change |
| One BI tool consumes it, and you control that repository | A shim or an `alias`, with a dated removal task |
| Consumers are other teams, other projects, or unknown | Versions, with a `deprecation_date` on the old one |
| The project does not use versions anywhere yet | A new model plus `deprecation_date` on the old one. Do not adopt a governance feature mid-migration |

### The predictable-cadence pattern

Because every additive change is non-breaking, a long-lived interface accumulates columns nobody reads. dbt's recommendation is not to version on every change but to **bump on a predictable cadence — once or twice a year, announced well in advance — and remove the dead columns then**. That converts an unbounded stream of small migrations into one scheduled one, which is the only version of this that teams actually keep up with.
