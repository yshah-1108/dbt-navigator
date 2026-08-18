# Node selection syntax

Contents:
- Graph operators
- How selection is evaluated
- Wildcards
- Set operations
- Selector files
- Method selectors
- Indirect test selection

## Graph operators

| Selector | Selects |
|---|---|
| `<model>` | That node only |
| `<model>+` | Node and all descendants |
| `+<model>` | Node and all ancestors |
| `+<model>+` | Full lineage both directions |
| `2+<model>` | Node plus 2 generations of ancestors |
| `<model>+3` | Node plus 3 generations of descendants |
| `@<model>` | Node, all descendants, **and all ancestors of those descendants** — the set needed to build the descendants from scratch |

`@` is the one to reach for when you need a downstream chain to be buildable in an environment that may be missing intermediate models. `+` prefixes and suffixes can carry independent depths: `3+<model>+4` is three generations up and four down.

## How selection is evaluated

The order is fixed, and knowing it explains most surprising results:

1. Selection **methods** resolve (`tag:`, `state:`, `config.`, and so on).
2. **Graph operators** expand each match (`+`, `@`, depths).
3. **Set operators** combine (union, intersection, then `--exclude`).
4. Finally, dbt discards anything whose resource type the current task cannot run.

Step 4 is why `dbt run --select "tag:nightly"` can report fewer nodes than `dbt ls --select "tag:nightly"` for the same tag: `ls` lists the seeds, sources and tests too, and `run` throws them away. That is not under-selection, and chasing it as a bug wastes time.

## Wildcards

Most methods accept Unix-style patterns, which is easy to forget and cheaper than listing nodes by hand.

| Pattern | Matches |
|---|---|
| `*` | Any number of any characters, including none |
| `?` | Exactly one character |
| `[abc]` | One character from the set |
| `[a-z]` | One character from the range |

```bash
dbt ls --select "stg_*"              # every staging model by name prefix
dbt ls --select "package:*_source"    # every package with that suffix
```

## Set operations

| Form | Meaning |
|---|---|
| `--select "<a> <b>"` | Union — space separated |
| `--select "<a>,<b>"` | Intersection — comma separated, no spaces |
| `--select "tag:<t>,config.materialized:incremental"` | Intersection of two methods |
| `--exclude "<c>"` | Remove from the selected set |
| `--selector <name>` | A named selector defined in `selectors.yml` |

**Quote every multi-value selector.** How an unquoted list fails depends on the implementation, and the difference matters. The dbt Cloud CLI rejects the second bare word outright with `Error: unknown argument "<name>" for "dbt list"` and a hint to quote, exiting non-zero — **verified on dbt Cloud CLI 0.40.14**, with and without a trailing flag, both exit 1. dbt Core's `--select` accepts multiple space-separated values by design, so it does not reject the same command; the hazard there is a following flag or argument being absorbed as a selector value, which changes the selection without an error.

The practical consequence: quoting is correct on every implementation, and it is the only form that behaves identically across them. Confirm the resulting set with `dbt ls` rather than trusting either the presence or the absence of an error.

```bash
dbt build --select "<model_a> <model_b>"     # correct everywhere
dbt build --select <model_a> <model_b>       # rejected on some CLIs, silently under-selects on others
```

## Selector files

Once a selector needs a comment to be intelligible on the command line, move it to `selectors.yml` at the project root. The gain is not brevity — it is that the criteria become reviewable in a diff and reusable across jobs, instead of being retyped into an orchestrator UI where nobody can see them.

```yaml
selectors:
  - name: <selector_name>
    description: "<what this set is for, and why these criteria>"
    definition:
      union:
        - intersection:
            - 'tag:<tag>'
            - 'config.materialized:incremental'
        - 'path:models/<directory>'
        - exclude:
            - 'tag:<excluded_tag>'
```

```bash
dbt build --selector <selector_name>
dbt build --select "selector:<selector_name>"   # usable inside --select from 1.12
```

Three properties of the YAML form differ from the CLI form, and each has bitten someone:

| Property | Detail |
|---|---|
| `exclude` semantics | Always a set difference, always applied last **within its scope**. The CLI `--exclude` is applied last overall |
| Multiple `--select` values | Treated as a union, never an intersection. Same for multiple excludes |
| `indirect_selection` | Settable per criterion, and a YAML value **overrides** the CLI flag |
| `default: true` | Makes this the selection for any command given no selector at all — including `dbt build` with no arguments |

A default selector is powerful and quietly dangerous: someone running a bare `dbt build` expecting the whole project gets a subset, with nothing in the output announcing that a default applied. Only one selector may be `default: true` per invocation, and a Jinja expression can vary it by target. If a project has one, know it before reasoning about what a bare command did.

The full-YAML form maps operators to keywords: `parents` / `children` with `parents_depth` / `children_depth` for `+`, and `childrens_parents` for `@`. Selectors can also reuse each other through the `selector` method, which returns the complete node set of the named selector — useful for defining one canonical set and subtracting from it, but note that inheritance cannot re-apply `parents`, `children`, or `indirect_selection` to the inherited set.

## Method selectors

| Method | Example | Notes |
|---|---|---|
| `tag:` | `tag:nightly` | Matches tags on models, sources, tests. Tests inherit tags from columns, sources and source tables — **not** from models, seeds or snapshots |
| `path:` | `path:models/staging` | Directory or file path |
| `file:` | `file:<model>.sql` | Filename |
| `fqn:` | `fqn:<project>.staging.<model>` | Dotted node path. `fqn:"*"` selects everything |
| `package:` | `package:<package_name>` | Nodes from one package. `package:this` means the root project |
| `config:` | `config.materialized:incremental` | Any config value, including nested keys and list members |
| `resource_type:` | `resource_type:model` | Also `test`, `source`, `seed`, `snapshot`, `exposure`, `analysis`, and `function` (1.11+) |
| `test_type:` | `test_type:unit` | Also `generic`, `singular`, `data` |
| `test_name:` | `test_name:not_null` | One generic test across the project |
| `source:` | `source:<source_name>` | Or `source:<source_name>.<table>` |
| `exposure:` | `exposure:<name>` | Combine with `+` to find feeding models |
| `metric:` | `+metric:<name>` | Parents of a metric |
| `semantic_model:` | `+semantic_model:<name>` | Parents of a semantic model |
| `saved_query:` | `+saved_query:<name>` | Parents of a saved query |
| `unit_test:` | `unit_test:*` | Unit tests by name |
| `group:` | `group:<group_name>` | Models in a group |
| `access:` | `access:public` | Also `protected`, `private` |
| `version:` | `version:latest` | Also `prerelease`, `old`, `none` |
| `selector:` | `selector:<name>` | A named selector, usable inside `--select` from 1.12; the standalone `--selector` flag is older |
| `state:` | `state:modified` | Needs `--state` |
| `result:` | `result:error` | Needs a previous `run_results.json` |
| `source_status:` | `source_status:fresher+` | Needs a previous `sources.json` (1.1+) |

`config.` reaches into structures, which saves writing a list by hand:

```bash
dbt ls --select "config.unique_key:<column>"        # a member of a list config
dbt ls --select "config.grants.select:<role>"        # a nested dictionary key
dbt ls --select "config.meta.<key>:true"             # a boolean in meta
```

### Indirect test selection

Selecting models also selects tests, and the mode governing *which* tests is the setting most likely to make a CI run fail on something unrelated to the change.

| Mode | Runs a test when | Use for |
|---|---|---|
| `eager` (default) | Any parent of the test is selected | A full build where everything exists |
| `buildable` | Every parent is selected, or is an ancestor of something selected | Building a subset of the DAG |
| `cautious` | Every parent is selected | The most conservative subset build |
| `empty` | Never — only the selected node builds | Materializing without testing |

```bash
dbt build --select "<model>" --indirect-selection cautious
```

The failure this prevents: in `eager` mode a multi-parent test — a `relationships` test is the usual one — is pulled in even though one of its parents was never built in this environment. The test then queries a relation that does not exist, or one belonging to a different environment, and fails for reasons that have nothing to do with the change under review. `buildable` or deferral both fix it; picking one is a CI design decision, covered in `dbt-shipping-changes`.

Exclusion is greedier than inclusion: if **any** parent of a test is explicitly excluded, the test is excluded, regardless of mode.
