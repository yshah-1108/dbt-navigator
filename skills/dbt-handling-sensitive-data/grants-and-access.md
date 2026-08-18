# Grants from dbt, and how a rebuild drops them

A grant decides whether a role can read a relation at all. Masking decides what a permitted reader sees. They are not interchangeable, and the most expensive mistakes here come from using one where the other was needed.

The trap is not the syntax. It is this:

**A relation in a broadly-readable schema effectively re-grants everything it contains.** A model built into a schema a wide group can read makes every column in it readable by that group, whatever the source relation's grants were. You have not copied a column; you have republished it to a different audience. The `select` is one line and the change of audience is invisible in the diff.

---

## The `grants` config

```yaml
models:
  - name: <model>
    config:
      grants:
        select: ["<role_or_group>"]
```

Configurable in `dbt_project.yml`, in a schema YAML `config:` block, or in a model's `config()` call. dbt's stated goal is that the grants on the database object match the configured `grants` exactly — no more, no less — which it achieves by issuing `grant` and `revoke` statements after the relation is built.

Four behaviours decide whether that goal is met in practice.

### Merge is "clobber" unless you ask for addition

Set `grants` at project level and again on a model, and the **more specific set replaces the less specific one** for that privilege. A project-level `select: ['<reader_a>', '<reader_b>']` plus a model-level `select: ['<reader_c>']` yields `<reader_c>` only.

The `+` prefix on the **privilege name** makes it additive instead:

```yaml
# adds to the inherited grantees rather than replacing them
{{ config(grants = {'+select': ['<reader_c>']}) }}
```

Three points that get confused:

- The `+` on the privilege (`'+select'`) is a **different feature** from the `+` used in `dbt_project.yml` to mark a config key (`+grants:`). Both appear in the same file and they mean different things.
- It applies **per privilege**. `{'+select': [...], 'insert': [...]}` adds selectors and clobbers inserters, in one config.
- The direction matters for safety in opposite ways: **clobbering can silently narrow access** and break a consumer, and **adding can silently widen it** past what a reviewer thought they were approving. Read which one is in play before assuming either.

### Removing the config does not revoke

If you delete a `+grants` section entirely, dbt concludes you no longer want it to manage grants and **changes nothing**. Existing grants stay. To actually revoke, supply an empty list of grantees — which is the opposite of the intuitive action, and it means "we removed the grants config" and "we removed the grants" are different events that look the same in a diff.

### Platform vocabulary differs, and a wrong name is not always an error

The privilege and grantee names are the platform's, not dbt's. On BigQuery in particular, privileges are IAM roles of the form `roles/<service>.<roleName>`, and grantees need a type prefix (`user:`, `group:`, `serviceAccount:`, `domain:`). A grantee written without its prefix, or a role name from another engine, is a config that reads as protective and does not do what it says.

Note also that BigQuery's `grant_access_to` is a **different feature**: it authorises a view to read datasets the querying user cannot. That is a deliberate privilege elevation, not a grant, and the two are frequently conflated. See `engine-mechanisms.md`.

### `grants` covers relations, not policies

dbt's own guidance is that hooks remain the right tool for grants on objects other than views and tables, for row- and column-level access, for masking policies, and for future grants. **Do not model a masking requirement as a `grants` config.** If a group needs the rows but not the values, the answer is a policy or a derived column — not a grant, and not both used interchangeably.

---

## How a rebuild silently drops grants

This is the failure mode with the widest blast radius, because it presents as a broken dashboard rather than as a permissions change, and the person debugging it usually fixes it by widening access.

Different engines replace a relation differently, and that decides what happens to grants applied **outside dbt** — by a platform team, by a script, by a click in a console:

| Situation | What happens to externally-applied grants |
|---|---|
| The engine drops and recreates the relation | **All grants are lost** and must be reapplied from scratch. dbt's configured grants become the complete set |
| The engine issues `create or replace` and can carry grants forward | Grants may survive, so there is a delta between what dbt configured and what the object ends up with |
| A `merge` or `insert` into an existing incremental relation | The object is not replaced, so grants carry over |

On Snowflake this is explicit and worth spelling out, because the config does two things rather than one:

- **`copy_grants: false` (the default).** dbt issues `create or replace` **without** `copy grants`, so grants do not carry over — and because dbt then aims to make the object match the config exactly, it will also `revoke` what it finds that the config does not list. An externally-applied grant on a table is therefore removed on the next full refresh.
- **`copy_grants: true`.** dbt adds the `copy grants` qualifier, and it **also stops revoking**: the presumption is that grants are managed elsewhere, so dbt only ever adds. Note that this is a behavioural change beyond the DDL keyword, which is not obvious from the config's name — the flag does more than alter the statement.

Two consequences to state in a summary rather than leave implicit:

- **A full refresh is a permissions event.** On an incremental model, ordinary runs preserve grants and a full refresh may not. That means a change with no permissions content can drop access, at whatever hour the refresh runs.
- **`copy_grants` support is per-object-type and version-dependent.** Support was extended over time — dynamic tables, for instance, gained it only in a later adapter version, and before that they reset permissions on every replacement. Verify against the adapter version in use rather than the current documentation.

The reliable posture: **if a relation's grants matter, they belong in the `grants` config**, not in an external script that a rebuild can undo. And when investigating "someone lost access", check whether a full refresh ran before concluding that a grant was revoked deliberately.

---

## Schema-level defaults usually dominate

A permissive default on the schema is applied to relations created in it. So a carefully narrow per-model `grants` config can be overridden — or rendered irrelevant — by a schema someone configured a year ago and nobody has looked at since.

**Check the schema's defaults, not only the model's config.** The model config tells you what dbt will grant; the schema tells you what everyone already has.

This is also why the development environment is the usual site of the leak. Many projects grant a shared role broad read across the development database so colleagues can inspect each other's work. A sensitive column materialised in a personal development schema may therefore be readable by the whole team. Read `environments.dev` from the contract and check the actual grants; do not infer isolation from the word "dev". See `leak-surfaces.md`.

---

## Least privilege, applied concretely

- Grant to the **narrowest role that satisfies the actual need**, and name the need in the change. A grant with no stated purpose is indistinguishable from an oversight to the next reader.
- **A terminal relation is the widest surface**, because consumers read it directly. `layers[].terminal` marks these; an over-broad grant there reaches the most people and is the hardest to walk back.
- **Never widen a grant to make a build or a test pass.** The build succeeds and an access boundary has been permanently moved, in a change whose description says nothing about access. If a masked value breaks a join, the join needs a hashed or tokenised key — see `derived-values.md`.
- **Say when you widened something.** A grant added to unblock work is a permanent access change, and it needs to be in the summary and the PR body, because a reviewer cannot infer an audience change from a `select`.
- **`grants` is recorded in the manifest** like any config, which makes it greppable and auditable across the project. That is the cheapest way to answer "who can read the marts" — and worth preferring over asking.
