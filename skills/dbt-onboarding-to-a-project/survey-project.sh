#!/usr/bin/env bash
# survey-project.sh -- a fast, read-only first-pass survey of a dbt project you
# are arriving at cold. Run from the dbt project root. It DUMPS facts; it does
# not interpret them. Every judgment the SKILL.md teaches -- is a zero-child
# model terminal-by-design or abandoned, is a deviation grandfathered or a bug,
# is a quiet query log an absent consumer or a short retention window -- is
# still yours to make. This script only saves the typing of the mechanical
# sections (1-4, 7, 8). The prose sections it cannot run for you -- orchestrator
# discovery (5), BI consumers (6), and the query-log tests -- need judgment or a
# warehouse connection and stay in the SKILL.md.
#
# It changes nothing, executes no models, and reads nothing outside this tree
# and the local manifest. Every section degrades gracefully when its input is
# absent. Pass a single model name as $1 to also get its lineage and history.
#
# Usage:  bash survey-project.sh [model_name]

set -uo pipefail

TARGET="${1:-}"

say() { printf '\n=== %s ===\n' "$1"; }

if [ ! -d models ]; then
  echo "No models/ directory here. Run this from the dbt project root." >&2
  exit 1
fi

# --- section 0: the contract -------------------------------------------------
say "CONTRACT (read this first; absent fields degrade specific checks below)"
if   [ -f conventions.yml ];            then echo "found: conventions.yml"
elif [ -f .dbt-agent/conventions.yml ]; then echo "found: .dbt-agent/conventions.yml"
else echo "NO CONTRACT -- everything below is measurement, never policy. You can"
     echo "count nodes and trace children, but you cannot call any pattern a"
     echo "violation, because nothing has declared the rule. Offer to generate one."
fi

# --- section 1: version and adapter -----------------------------------------
say "DBT VERSION + ADAPTERS (installed)"
dbt --version 2>/dev/null || echo "dbt not on PATH"

say "VERSION THE PROJECT ACCEPTS (require-dbt-version is a floor/ceiling, not your machine)"
grep -n 'require-dbt-version' dbt_project.yml 2>/dev/null || echo "no require-dbt-version pin"
grep -rn 'dbt-version\|dbt_version' packages.yml package-lock.yml 2>/dev/null | head

say "BEHAVIOR-CHANGE FLAGS (version-specific defaults; read before assuming any)"
sed -n '/^flags:/,/^[a-z]/p' dbt_project.yml 2>/dev/null | head -30 || echo "no flags: block"

# --- manifest gate -----------------------------------------------------------
# Sections 2, 4, and 7 read target/manifest.json. Refresh it once, here.
say "REFRESHING MANIFEST (dbt parse)"
if dbt parse >/dev/null 2>&1; then echo "manifest refreshed"; else
  echo "dbt parse failed or unavailable -- manifest-based sections may be stale or empty"
fi

MANIFEST_ADAPTER=$(python3 -c "import json;m=json.load(open('target/manifest.json'));print(m['metadata']['adapter_type'], m['metadata']['dbt_version'])" 2>/dev/null || true)
[ -n "$MANIFEST_ADAPTER" ] && printf 'manifest adapter + version: %s\n' "$MANIFEST_ADAPTER"

# --- section 2: DAG shape ----------------------------------------------------
say "NODE COUNTS BY RESOURCE TYPE"
printf 'models:       %s\n' "$(dbt ls --resource-type model 2>/dev/null | wc -l | tr -d ' ')"
printf 'sources:      %s\n' "$(dbt ls --resource-type source 2>/dev/null | wc -l | tr -d ' ')"
printf 'tests:        %s\n' "$(dbt ls --resource-type test 2>/dev/null | wc -l | tr -d ' ')"
printf 'incremental:  %s\n' "$(dbt ls --select 'config.materialized:incremental' 2>/dev/null | wc -l | tr -d ' ')"

say "MODEL COUNTS BY TOP-LEVEL FOLDER"
for p in $(ls -d models/*/ 2>/dev/null); do
  printf '%5s  %s\n' "$(dbt ls --select "path:$p" --resource-type model 2>/dev/null | wc -l | tr -d ' ')" "$p"
done

say "CHILD COUNTS PER MODEL (the load-bearing structure; double-digit = blast radius)"
python3 - <<'PY'
import json, collections, sys
try:
    m = json.load(open('target/manifest.json'))
except Exception as e:
    print("manifest unreadable:", e); sys.exit(0)
kids = collections.Counter()
for node_id, node in list(m['nodes'].items()) + list(m.get('exposures', {}).items()):
    for parent in node.get('depends_on', {}).get('nodes', []):
        kids[parent] += 1
models = [n for n in m['nodes'] if n.startswith('model.')]
zero = [n for n in models if not kids[n]]
print(f"models: {len(models)}   with zero children: {len(zero)}")
print("top 15 by direct child count (exposures counted as children, deliberately):")
for node_id, c in kids.most_common(15):
    if node_id.startswith('model.'):
        print(f"{c:5d}  {node_id.split('.')[-1]}")
PY

# --- section 3: entry points -------------------------------------------------
say "SOURCES, AND MODELS READING A SOURCE DIRECTLY"
dbt ls --resource-type source 2>/dev/null | head -40
printf 'models reading a source directly: %s\n' "$(dbt ls --select 'source:*+1' --resource-type model 2>/dev/null | wc -l | tr -d ' ')"

# --- section 4: dead-model signals (2 of the 3 tests; the query log stays prose)
say "EXPOSURES (the only in-repo record of an external consumer)"
python3 - <<'PY'
import json, sys
try:
    m = json.load(open('target/manifest.json'))
except Exception:
    sys.exit(0)
exp = m.get('exposures', {})
if not exp:
    print("no exposures declared")
for e in exp.values():
    print(e['name'], e.get('type'), '->', [n.split('.')[-1] for n in e.get('depends_on', {}).get('nodes', [])])
PY
echo ""
echo "NOTE: the third dead-model test -- reads in the warehouse query log -- needs"
echo "project.warehouse and a query-history relation. It is not scriptable here;"
echo "run the query in SKILL.md section 4. A model is a deletion CANDIDATE only"
echo "when all three hold, and even then it is a conversation, not a plan."

# --- section 7: test coverage ------------------------------------------------
say "TEST COVERAGE MAP (read concentration, not average; cross against child counts)"
python3 - <<'PY'
import json, collections, sys
try:
    m = json.load(open('target/manifest.json'))
except Exception:
    sys.exit(0)
tested = collections.Counter()
for node in m['nodes'].values():
    if node.get('resource_type') != 'test':
        continue
    for parent in node.get('depends_on', {}).get('nodes', []):
        if parent.startswith('model.'):
            tested[parent] += 1
models = [n for n in m['nodes'] if n.startswith('model.')]
untested = [n for n in models if not tested[n]]
den = max(len(models), 1)
print(f"models: {len(models)}  untested: {len(untested)}  ({100*len(untested)//den}%)")
by_dir = collections.Counter(m['nodes'][n]['path'].rsplit('/', 1)[0] for n in untested)
print("untested, by folder:")
for d, c in by_dir.most_common(12):
    print(f"{c:5d}  {d}")
PY

# --- section 8: activity and staleness --------------------------------------
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
    say "GIT ACTIVITY -- SHALLOW CLONE"
    echo "This repo is a shallow clone; git history is truncated and dates below"
    echo "are unreliable. Fetch full history before trusting them."
  else
    say "MOST-MODIFIED MODELS, LAST 90 DAYS (where the team is actually working)"
    git log --since='90 days ago' --name-only --pretty=format: -- models/ 2>/dev/null \
      | grep '\.sql$' | sort | uniq -c | sort -rn | head -20
    say "AGE DISTRIBUTION OF LAST MODIFICATION, BY YEAR (uniform => suspect a bulk reformat)"
    git ls-files models/ 2>/dev/null | grep '\.sql$' | while read -r f; do
      git log -1 --format=%ad --date=format:%Y -- "$f" 2>/dev/null
    done | sort | uniq -c
  fi
else
  say "GIT ACTIVITY"
  echo "not a git repository -- activity and staleness cannot be measured"
fi

# --- optional: one model's lineage + history --------------------------------
if [ -n "$TARGET" ]; then
  say "LINEAGE + HISTORY FOR: $TARGET"
  printf 'transitive descendants: %s\n' "$(dbt ls --select "${TARGET}+" --resource-type model 2>/dev/null | wc -l | tr -d ' ')"
  printf 'transitive ancestors:   %s\n' "$(dbt ls --select "+${TARGET}" --resource-type model 2>/dev/null | wc -l | tr -d ' ')"
  path=$(git ls-files "models/**/${TARGET}.sql" 2>/dev/null | head -1)
  if [ -n "$path" ] && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git log --diff-filter=A --format='added   %ad by %an' --date=short -- "$path" 2>/dev/null | tail -1
    git log -1 --format='last    %ad by %an' --date=short -- "$path" 2>/dev/null
    printf 'revisions: %s\n' "$(git log --oneline -- "$path" 2>/dev/null | wc -l | tr -d ' ')"
  else
    echo "could not resolve a single file path for '$TARGET' -- skipping history"
  fi
fi

printf '\n--- survey complete. This is measurement, not a plan. ---\n'
printf 'Now read SKILL.md: split zero-child models against layers[].terminal,\n'
printf 'run the orchestrator (5), BI-consumer (6), and query-log tests it describes,\n'
printf 'and report a short brief -- facts and numbers, derivation not narrated.\n'
