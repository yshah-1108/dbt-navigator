# Inferring conventions from an existing project

Most teams have never written their conventions down, but the conventions are already in the repo. Derive the contract, then ask for confirmation — never hand someone an empty YAML file.

Run these against the target project and present the findings as a draft the user corrects.

This file covers the **structured fields** of `conventions.yml` — the measurable naming and policy values. Conventions are broader than that: bespoke macros, config seeds, mechanical procedures, and unnamed code-shape patterns (a house dedup idiom, a CTE structure) are conventions too, and they live in the prose `mechanisms` file, not these fields. `dbt-deriving-project-context` runs the full pass and produces all four artifacts; use this file for the field-by-field structured part of it.

## 1. Layers and prefixes

```bash
# Prefix distribution across the whole project
find models -name '*.sql' -exec basename {} \; \
  | sed -E 's/^([a-z]+_).*/\1/' | sort | uniq -c | sort -rn

# Prefixes per top-level folder, which reveals the layer mapping
for d in models/*/; do
  echo "== $d"
  find "$d" -name '*.sql' -exec basename {} \; \
    | sed -E 's/^([a-z]+_).*/\1/' | sort | uniq -c | sort -rn | head -5
done
```

Read the output as: dominant prefix in a folder is that layer's prefix. A prefix with a long tail of a few files is usually **legacy** — a candidate for `banned_prefixes`, but confirm, because it may equally be a current-but-rare layer.

## 2. Separator

```bash
find models -name '*__*.sql' | wc -l   # double underscore
find models -name '*.sql' | wc -l      # total
```

High ratio means `__` is the separator. Low means single `_`.

## 3. Materialization per layer

```bash
# Project-level defaults
sed -n '/^models:/,$p' dbt_project.yml

# Per-model overrides, which reveal where the default is not trusted
grep -rl 'materialized=' models/ | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn
```

## 4. Environments and dev detection

```bash
grep -A12 -E '^\s*(outputs|target):' ~/.dbt/profiles.yml 2>/dev/null

# How the project currently detects dev -- adopt what it already does
grep -rn "target\.\(name\|database\|schema\)" models/ macros/ | head -20
```

If the project uses `target.name`, record that in the contract rather than "correcting" it. It is more fragile than a database-name check, but it is what their code does, and a contract that disagrees with the code is worse than a fragile contract. Note the fragility once; do not silently change behavior.

## 5. Timestamp column suffix

```bash
# Endings of timestamp/date columns as declared in schema YAML
grep -rhoE '^\s+- name: \w+_(utc|est|pst|local|at|ts)\b' models/ \
  | sed -E 's/.*_(\w+)$/\1/' | sort | uniq -c | sort -rn
```

Report as a percentage. Below roughly 70% adherence it is a tendency, not a convention — say so rather than encoding it as a rule.

## 6. Surrogate key column

```bash
grep -rn 'generate_surrogate_key' models/ | grep -oE 'as \w+' | sort | uniq -c | sort -rn
```

## 7. Test policy per column role

```bash
# Which tests appear at all, and how often
grep -rhoE '^\s+- (unique|not_null|relationships|accepted_values)\w*' models/ \
  | sed 's/[- ]//g' | sort | uniq -c | sort -rn
```

Distinguishing PK from FK policy reliably needs the manifest; if `target/manifest.json` exists, prefer parsing it over grepping YAML.

## 8. Schedule tags

```bash
grep -rhoE "tags\s*=\s*\[[^]]*\]" models/ | sort | uniq -c | sort -rn
grep -A5 -E '^\s*\+tags:' dbt_project.yml
```

A tag set globally in `dbt_project.yml` is the **default** — record it as `default_tag` and note that agents must not add it to individual models. Actual crons should come from the orchestrator API, not from this file.

## 9. Banned prefixes — must be asked

Not inferable. A legacy prefix and a current prefix look identical in a filename; only the team knows which are deprecated. Present the prefix distribution and ask directly:

> These prefixes appear in your project: `stg_` (142), `dim_` (38), `fct_` (26), `int_` (24), `base_` (9), `final_` (3). Which, if any, are deprecated for new models?

Use `git log --diff-filter=A` on a sample to help them decide:

```bash
# Newest file per prefix -- a prefix whose newest file is old is likely deprecated
for p in fct_ base_ final_; do
  newest=$(find models -name "${p}*.sql" -exec git log --diff-filter=A --format='%ad %H' --date=short -1 -- {} \; 2>/dev/null | sort -r | head -1)
  echo "$p last added: $newest"
done
```

## 10. Project identity, orchestrator, and BI — mostly asked

```bash
grep -n '^name:' dbt_project.yml            # project.dbt_project_name
dbt --version                                # project.dbt_version, and the adapter
grep -rn 'timezone' dbt_project.yml profiles.yml 2>/dev/null
```

`project.warehouse` comes from the adapter `dbt --version` reports, not from what anyone says they use. If the two disagree, the adapter is the answer.

`orchestrator` and `bi` are largely **not** inferable from inside the repository, and this is where inference most often manufactures a wrong answer. Locating the orchestrator and enumerating BI consumers is `dbt-onboarding-to-a-project`'s job — run it, then record only what it established:

- `orchestrator.type` — set `unknown` unless you confirmed what runs the project. **Never `none` because this repo has no CI config**; orchestration defined in another repository looks identical from here.
- `bi.consumers[]` — ask which tools read this project. A repo path you cannot list is a path that will silently return no matches.
- `bi.use_exposures` — `dbt ls --resource-type exposure | wc -l` tells you whether any exist, not whether the project intends to maintain them. Ask before recording `true`.
- `project.query_history_relation` and `query_history_retention_days` — ask. Both are permission-gated, and the retention number is what makes a lookback claim defensible.

## 11. Terminal layers and reference rules — must be asked

```bash
# Which declared layers currently have models with zero children.
# Evidence for the question; not the answer to it.
dbt parse && python3 - <<'PY'
import json, collections
m = json.load(open('target/manifest.json'))
kids = collections.Counter()
for node in list(m['nodes'].values()) + list(m.get('exposures', {}).values()):
    for parent in node.get('depends_on', {}).get('nodes', []):
        kids[parent] += 1
leaves = collections.Counter(
    n['path'].rsplit('/', 1)[0]
    for k, n in m['nodes'].items()
    if k.startswith('model.') and not kids[k]
)
for d, c in leaves.most_common():
    print(f"{c:5d}  {d}")
PY
```

Zero children is a fact. *Should have* zero children is a design intent, and no measurement reaches it — a folder of leaves is either a terminal layer working correctly or a pile of abandoned models. Present the counts per folder and ask which layers are terminal by design.

`may_reference` has the same shape and is worse: the DAG shows which edges **exist**, never which are **permitted**. An observed edge may be the violation the rule is meant to catch, so deriving the rule from observation encodes the violation as policy. Show the observed cross-layer edges as a starting point and have the team strike the ones that should not exist.

## Presenting the result

Show a summary with adherence percentages and let the user correct it. Confidence language matters:

- **≥90% adherence** — state it as the convention.
- **70–90%** — state it as the convention, note the exceptions exist.
- **<70%** — do not encode it. Report the split and ask.

Never present an inferred value as confirmed. The output of inference is a draft.

Then validate the draft before committing it — `dbt-project-conventions` carries the command, and a draft that fails schema validation is a draft, not a contract. Two habits worth keeping past the first run:

- **Omit rather than fill.** A section you could not establish should be absent. Absent means "no convention here" and degrades to labelled-generic guidance; a guessed value degrades to confident noise.
- **Keep the derivation in comments.** Recording what was measured, and when, is what lets the next person tell a stale field from a deliberate one.

Re-run this whole pass on the events listed in `dbt-project-conventions` — a dbt upgrade, a warehouse or BI migration, an orchestrator change, a layer added or renamed — and diff the fresh draft against the committed contract. The diff is the staleness report.
