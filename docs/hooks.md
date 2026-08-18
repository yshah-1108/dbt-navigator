# Hooks — optional, and yours to adapt

This library is deliberately **skill-based**. The skills teach an agent to do the right thing; they do not depend on any harness feature to *force* it. That is on purpose — a skill works in Claude Code, Cursor, Codex, Gemini CLI, or a plain `AGENTS.md` reader, because it is just text the agent reads.

Hooks are the other half of the story, and they are **not portable**. Every harness spells them differently — Claude Code has `PreToolUse`/`PostToolUse` shell hooks, Cursor and others have their own mechanisms or none — and the payload each one passes to your script has a different shape. Shipping one harness's hook config would help one audience and mislead the rest. So this directory ships **no hook config**. It ships a shortlist of the checks that are worth wiring up wherever your harness lets you, with a minimal snippet you adapt.

Think of a hook as a **deterministic backstop for the handful of mistakes a skill can still make under pressure** — not as the enforcement layer. The skill is the enforcement layer. A hook catches the case where the agent knew the rule and slipped anyway.

Two rules keep hooks from becoming a liability:

- **A hook enforces a fact, never a preference.** "Don't commit on `main`" is a fact — the branch either is or is not `main`. "Prefer wide marts" is a judgement, and a hook that blocks a judgement call is a hook people learn to disable.
- **Prefer a warning to a block.** A block that fires on a false positive trains the user to bypass the whole mechanism. Reserve hard blocks for the irreversible (a destructive warehouse command); warn for the rest.

Where the checks are genuinely deterministic and portable across harnesses, they belong in **CI or a pre-commit hook**, not a per-harness agent hook — because CI is not a harness, so the same check runs for everyone. A companion linter takes that path; see the roadmap in the top-level `README.md`.

---

## The five worth wiring up

Ordered by value. Each maps to a rule the skills already teach, so the hook is a backstop, not a new policy.

### 1. Compile after editing a model (warn)

The single highest-value backstop. A `ref()` typo or a broken CTE is invisible until compile, and the agent does not always run it. Fire after any write to a `.sql` file under `models/`.

```bash
# after-edit hook: $EDITED_FILE is whatever your harness names the touched path
case "$EDITED_FILE" in
  models/*.sql)
    model="$(basename "$EDITED_FILE" .sql)"
    dbt compile --select "$model" >/dev/null 2>&1 \
      || echo "⚠ $model failed to compile — check the last edit before continuing"
    ;;
esac
```

### 2. Branch guard (block)

Never commit on the default branch — one of the universal rules. This one earns a hard block because the cost of catching it late (a commit on `main`) is a force-push and a bad afternoon.

```bash
# before running any git command
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
case "$branch" in
  main|master)
    echo "✋ On $branch. Create a feature branch before committing."
    exit 1   # non-zero blocks the command in most harnesses
    ;;
esac
```

### 3. Destructive-command gate (block, with confirmation)

A `drop`, a `truncate`, a full-refresh of an irreplaceable model, a warehouse `delete`. These are the commands where "the agent was confident" is not consent. Match the verbs and require an explicit human acknowledgement your harness can carry.

```bash
# before running a shell/SQL command; $CMD is the command text
if printf '%s' "$CMD" | grep -qiE '\b(drop|truncate)\s+(table|schema|database)\b|\bdelete\s+from\b'; then
  echo "✋ Destructive command. Confirm the target and intent before running:"
  echo "   $CMD"
  exit 1
fi
```

Keep the pattern conservative. A gate that fires on the word "delete" inside a comment is a gate that gets turned off.

### 4. Anti-pattern lint on changed SQL (warn)

A few dbt anti-patterns are pure text and cheap to catch: `= null` (never true), a `right join`, a hardcoded environment name where a detection macro belongs, a banned prefix on a *new* file. This is a lint, so it warns and points; it does not block.

```bash
# after-edit hook on a .sql file
f="$EDITED_FILE"
grep -nE '=\s*null\b'      "$f" && echo "⚠ $f: use IS NULL, not = null"
grep -niE '\bright join\b' "$f" && echo "⚠ $f: rewrite as a left join"
# extend with the checks your project's conventions.yml actually declares —
# read banned_prefixes and sql_style from the contract, do not hardcode them here
```

The important line is the comment: **read the values from `conventions.yml`, do not bake a taxonomy into the hook.** A hook with a hardcoded prefix list is the same mistake the whole library is built to avoid — it belongs to one project and silently misfires on the next.

### 5. Validation-query environment check (warn)

A validation query must name its database and schema explicitly, because `ref()` resolves per-environment and can silently read production (see `dbt-environments`). If your harness can see the query text, flag a `ref()` inside an ad-hoc validation query.

```bash
if printf '%s' "$CMD" | grep -qE 'count\(\*\).*ref\('; then
  echo "⚠ Validation query uses ref() — name <database>.<schema>.<relation> explicitly instead"
fi
```

---

## Adapting these to your harness

Three things differ between harnesses, and they are the three you must fill in:

1. **The event name.** "After a file edit," "before a shell command," "before a tool call" — each harness names these differently, and some fire on tool categories rather than specific tools. Read your harness's hook documentation for the exact trigger.
2. **How the payload reaches your script.** The snippets above use `$EDITED_FILE` and `$CMD` as stand-ins. Your harness passes the touched path or the command text via an environment variable, a JSON blob on stdin, or positional arguments — wire the stand-in to whatever it actually provides.
3. **How a non-zero exit is interpreted.** In many harnesses a non-zero exit from a "before" hook blocks the action; in some it only warns. Confirm which, so a check you intend as a block is not silently advisory — and one you intend as a warning does not halt the session.

If your harness has no hook mechanism at all, none of this is lost. The skills already carry every one of these checks as instructions the agent follows — the hook only makes the deterministic subset mechanical. Skip this directory entirely and the library still works exactly as designed.
