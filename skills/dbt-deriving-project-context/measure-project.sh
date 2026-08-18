#!/usr/bin/env bash
# measure-project.sh -- a fast, read-only first-pass survey of a dbt project's
# conventions. Run from the dbt project root. It DUMPS counts; it does not
# interpret them. The interpretation -- which prefix is live vs retired, whether
# a zero-usage macro is dead or a built-in override, whether a seed is data or
# config -- is the work the SKILL.md walks you through. This script only saves
# you the typing of the mechanical parts of steps 1-2.
#
# It changes nothing and reads nothing outside the current directory tree.
# Every section degrades gracefully when the thing it looks for is absent.

set -uo pipefail

say() { printf '\n=== %s ===\n' "$1"; }

if [ ! -d models ]; then
  echo "No models/ directory here. Run this from the dbt project root." >&2
  exit 1
fi

say "MODEL COUNTS (sql vs python -- python models follow different rules)"
printf 'sql models:    %s\n' "$(find models -name '*.sql' | wc -l | tr -d ' ')"
printf 'python models: %s\n' "$(find models -name '*.py' | wc -l | tr -d ' ')"

say "PREFIX DISTRIBUTION (leading token before first underscore)"
find models -name '*.sql' | sed 's|.*/||;s|\.sql$||' \
  | grep -oE '^[a-z]+_' | sort | uniq -c | sort -rn

say "SEPARATOR: models using '__' double-underscore"
total=$(find models -name '*.sql' | wc -l | tr -d ' ')
dd=$(find models -name '*.sql' | sed 's|.*/||' | grep -c '__' || true)
printf '%s of %s models use __\n' "$dd" "$total"

say "KEY COLUMN: surrogate/unique key aliases in use"
grep -rhoE 'as (unique_id|surrogate_key|[a-z_]*_key|[a-z_]*_id)\b' models --include='*.sql' 2>/dev/null \
  | sort | uniq -c | sort -rn | head

say "TIMESTAMP SUFFIX: distinct *_utc-style column names"
grep -rhoE '[a-z_]+_(utc|est|pst|local)\b' models --include='*.sql' 2>/dev/null \
  | sort | uniq -c | sort -rn | head

say "LAYER MATERIALIZATIONS (authority is dbt_project.yml, not a file sample)"
if [ -f dbt_project.yml ]; then
  sed -n '/^models:/,$p' dbt_project.yml | grep -nE 'materialized:|^[[:space:]]+[a-z_+]+:' | head -40
  echo '(read the models: block above in full -- this is a hint, not the answer)'
else
  echo 'no dbt_project.yml found'
fi

say "MACROS: usage count per macro (0 does NOT mean dead -- see step 2)"
if [ -d macros ] && ls macros/*.sql >/dev/null 2>&1; then
  for m in $(ls macros/*.sql | xargs -n1 basename | sed 's/\.sql//'); do
    printf '%-36s %s\n' "$m" "$(grep -rl "$m" models --include='*.sql' 2>/dev/null | wc -l | tr -d ' ')"
  done | sort -k2 -rn
  echo '(zero-usage: check for dbt built-in overrides -- generate_schema_name,'
  echo ' generate_alias_name, generate_database_name -- and CI-invoked operations)'
else
  echo 'no macros/ directory'
fi

say "SEEDS: ref count from model logic (ref>0 => config/lookup table, not data)"
if [ -d seeds ] && ls seeds/*.csv >/dev/null 2>&1; then
  for s in $(ls seeds/*.csv | xargs -n1 basename | sed 's/\.csv//'); do
    printf '%-32s refs:%s\n' "$s" "$(grep -rlE "ref\((['\"])$s\1\)" models --include='*.sql' 2>/dev/null | wc -l | tr -d ' ')"
  done
else
  echo 'no seeds/ directory'
fi

say "SURROUNDING MACHINERY (read these; do not just note they exist)"
for p in packages.yml tests/generic .sqlfluff .github/workflows; do
  if [ -e "$p" ]; then echo "present: $p"; else echo "absent:  $p"; fi
done

say "CODE-SHAPE HINT: recurring idioms (a hint toward step 2b, never the finding)"
grep -rhoE '(qualify|row_number\(\) over|coalesce\(|group by all)' models --include='*.sql' 2>/dev/null \
  | sort | uniq -c | sort -rn | head

say "HISTORY: most-changed models in the last year (where the risk sits)"
if [ -d .git ]; then
  git log --format= --name-only --since='1 year ago' -- 'models/**/*.sql' 2>/dev/null \
    | grep -E '\.sql$' | sort | uniq -c | sort -rn | head -15
else
  echo 'not a git repository'
fi

printf '\n--- survey complete. This is measurement, not appraisal. ---\n'
printf 'Now read SKILL.md: interpret these counts, read history for intent,\n'
printf 'assign each finding a verdict, and write the four artifacts.\n'
