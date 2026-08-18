# State, deferral, and Slim CI

Contents:
- State and deferral
- What `state:modified` catches, and what it does not
- The artifact trap
- What `--defer` actually does
- Slim CI

## State and deferral

Both need a directory containing a previous `manifest.json`. This is the highest-leverage and most misunderstood part of the CLI, so it is worth reading rather than skimming.

| Flag / selector | Meaning |
|---|---|
| `--state <path>` | Where the comparison artifacts live |
| `--defer-state <path>` | Separate artifacts for deferral only; falls back to `--state` when absent |
| `state:new` | Nodes absent from the comparison manifest |
| `state:modified` | Any change — body, config, contract, description, macro dependency |
| `state:modified.body` | SQL body only, or seed values. Narrower and more useful than plain `modified` |
| `state:modified.configs` | Config changes, excluding `database` / `schema` / `alias` / `tags` / `meta` |
| `state:modified.relation` | Changes to `database` / `schema` / `alias` — where the node lands |
| `state:modified.contract` | Contract changes — the breaking ones |
| `state:modified.macros` | Depends on a changed macro, directly or transitively |
| `state:modified.persisted_descriptions` | Description changes, only where `persist_docs` is on |
| `state:old` | A node with the same `unique_id` exists in the comparison manifest |
| `state:unmodified` | Existing nodes with no changes |
| `--defer` | Unselected `ref()`s resolve to the state manifest's relations |
| `--favor-state` | Prefer the state relation **even when** the node exists in your environment |

```bash
dbt build --select "state:modified+" --state ./prod-artifacts --defer
```

## What `state:modified` catches, and what it does not

The list matters because a CI job selecting `state:modified` is making an implicit claim that everything risky is caught, and two of these gaps break that claim.

| Change | Detected? |
|---|---|
| Model SQL | Yes — `modified.body` |
| Any config that affects materialization | Yes — `modified.configs` |
| A macro the model calls, or a macro that macro calls | Yes — `modified.macros` |
| `group`, `access`, `deprecation_date`, `latest_version` | Yes, as configs — and each can break a downstream reference |
| Source `freshness` or `quoting` rules; exposure `maturity` | Yes |
| A `description`, with `persist_docs` enabled | Yes |
| `tags` and `meta`, at resource or column level | **No.** Deliberate: metadata does not affect materialization |
| A changed `var` or `env_var` **value** | **No.** dbt cannot trace the lineage from a variable's value to the nodes it affects |
| A seed 1 MiB or larger, edited in place | **No.** Only the file *path* is compared above that size; content hashing stops |

The two gaps to plan around. A behavioural switch driven by a variable can change every model's output with `state:modified` reporting nothing — one reason environment-dependent behaviour belongs in code rather than in `--vars`, covered in `dbt-environments`. And a large seed can be edited with no CI signal at all, which is one more argument against large seeds; see `dbt-sources-and-seeds`.

Prefer `state:modified.body` when you want "the SQL actually changed" and `state:modified` when you want "anything that could plausibly matter". Plain `modified` is deliberately broad — a whitespace change matches it.

## The artifact trap

The single most common way state selection breaks is self-inflicted and produces a confusing warning rather than an error.

**Never point `--state` at the same directory dbt writes to.** dbt overwrites `target/manifest.json` during parsing, so by the time comparison runs the "previous" state is the current state, and the result is either "saved manifest not found" or an empty diff that looks like a clean project. Three fixes, in order of preference:

1. Copy the downloaded production manifest into a separate directory — `state/` — and point `--state` there. Clearest, and the one to teach.
2. Redirect the current run's output with `--target-path` so the two never collide.
3. Pass `--no-write-json` on a read-only invocation: `dbt ls --no-write-json --select "state:modified" --state target`.

Also worth stating: the comparison manifest's provenance is part of the result. A manifest from a failed production run, or from a run of a different branch, produces a diff that is precisely wrong rather than obviously wrong. Know which run produced the artifacts you are comparing against.

## What `--defer` actually does

`--defer` rewrites `ref()` resolution. An unselected node's `ref()` points at the state manifest's relation instead of your environment's — but only when both conditions hold:

1. The node is **not** among the selected nodes, and
2. It does **not** exist in your target environment — unless `--favor-state` is passed, which drops this condition.

Consequences that catch people:

| Fact | Consequence |
|---|---|
| Ephemeral models are never deferred | They are pass-throughs; their own `ref()`s defer instead |
| A relation existing in your schema wins over the state manifest | A stale table you built weeks ago is preferred over fresh production. `--favor-state` is the fix |
| Deferral is per-`ref()`, not per-run | One query can read your environment for one parent and production for another |
| A multi-parent test can span environments | A `relationships` test may compare a dev child against a production parent. If dev data is limited, it fails for a reason that is not a defect |
| Some CLI wrappers defer by default | Which is how an unbuilt model silently reads production. See below |

**The classic failure: "it read production silently."** You edit a model, build only that model, and the numbers look plausible — because every parent resolved to production. Nothing in the output distinguishes this from a local build. Two defences: read `target/run/<project>/...` and check which relations the compiled SQL actually names, and treat any validation query as needing an explicit database and schema rather than `ref()`.

This matters most where deferral is the default rather than opt-in. **The dbt Cloud CLI defers to production by default**, so the failure needs no flag at all — it is the baseline behaviour, and an unbuilt model reads production data unless you have built it yourself. See `dbt-environments`.

`--defer-state` exists for the case where you want to compare logical changes against one point in time but fail over to a different environment's applied state. Most projects want the same directory for both, and passing only `--state` gives them that.

## Slim CI

The pattern that makes CI affordable: build only what changed, plus its descendants, and defer everything else to production.

```bash
dbt build --select "state:modified+" --state ./prod-artifacts --defer
```

Each part earns its place. `state:modified` limits the build to changed nodes; the trailing `+` catches the things the change could break; `--defer` means the unchanged ancestors are not rebuilt.

Refinements, each solving a specific cost or noise problem:

| Goal | Addition |
|---|---|
| Re-run only what failed last time | `--select "result:error+"`, or `dbt retry` for a straight resumption |
| Re-run failed tests and the models behind them | `--select "1+result:fail"` — tests have no descendants, so `result:fail+` selects only the test |
| Combine change-based and failure-based | `--select "state:modified+ result:error+"` — a union |
| Build only models fed by freshly-loaded sources | `--select "source_status:fresher+"` with a previous `sources.json` |
| Avoid cross-environment referential noise | `--indirect-selection cautious`, or `--exclude "test_name:relationships"` |
| Validate SQL with no data cost | `--empty` |
| Avoid a full-history rebuild of a modified incremental model | `dbt clone` the modified incremental models first, so an incremental run has something to merge into |
| Stop after the first failure | `--fail-fast` |

Two operational cautions. `result:` selectors read `run_results.json`, and each command overwrites it — so `result:error` (from a run) and `result:fail` (from tests) can only be selected in one command if the artifact came from a single `dbt build`. And `source_status:fresher+` requires `dbt source freshness` to have run **both** previously and in the current invocation, since it compares two `sources.json` files.

What Slim CI does not do: it does not prove a modified incremental model behaves correctly on its second run. A CI build of an incremental model usually builds it from scratch, which exercises the full-refresh branch and not the `is_incremental()` branch — the one that runs in production every day. See `dbt-incremental-models`.
