# Contributing

## What this project accepts, and what it does not

Being explicit up front saves everyone time.

| Contribution | Status | Why |
|---|---|---|
| Bug reports on a skill giving wrong guidance | **Wanted** | The highest-value input. A skill that is confidently wrong is worse than no skill. |
| Corrections to existing skills | **Wanted** | Especially warehouse-specific advice for a warehouse the maintainers don't run. |
| New warehouse support in existing skills | **Wanted** | Gated on `project.warehouse` in the contract, so it's additive. |
| Contract schema fields | **Discussed first** | Open an issue. Every field is a permanent compatibility commitment. |
| CI linter rules (once unparked) | **Wanted, with tests** | A rule without a test proving both the true positive and the false positive it must not fire on will not be merged. |
| Brand-new skills | **Curated, usually declined** | See below. |

### On new skills

The skill set is deliberately closed at a fixed number, organized by **what an
engineer is doing** rather than by topic. That structure only works if it stays
small enough that an agent can hold the whole map in mind.

Most proposed new skills are one of:

- **A section of an existing skill.** Adding a column, backfilling, and changing
  a grain are all one workflow. Splitting them means the agent loads one third of
  the guidance and misses the part that would have stopped it.
- **A reference note.** If it is under about 60 lines and has no decision points,
  it belongs inside a related skill, not beside it.
- **Genuinely organization-specific.** These belong in your own fork, or expressed
  as fields in your `conventions.yml`.

If you believe a real gap exists in the task surface, open an issue describing
**the task an engineer performs** that no current skill triggers on. That framing
makes the gap easy to confirm or refute.

## The one non-negotiable rule

**No organization-specific detail, ever.** No internal database names, schema
names, model prefixes, vendor names, macro names, or company names.

This is not enforced by review, because a single leaked internal identifier in a
public repository is not recoverable. It is mechanical:

```bash
python3 scripts/check-skills.py
```

That must exit 0 before anything merges. CI runs it on every pull request. It also
verifies structure, that cross-skill references resolve, and that the README and
the skills on disk agree.

If a term is legitimately needed — an example contract, a schema enum naming BI
vendors as a vocabulary — add it to `EXEMPT` in that script **with the reason
written out**. An exemption without a stated reason will be rejected.

## Skill anatomy

Every skill is a directory under `skills/` containing `SKILL.md`:

```markdown
---
name: dbt-<task-name>          # must equal the directory name
description: Use when ...       # must begin with "Use when"
---
```

The description is the trigger. It is the only part the agent reads before
deciding whether to load the skill, so it must describe **situations**, not
topics. "Use when adding a column to an existing model" triggers reliably;
"Column management" does not.

Required structure:

- **Read the contract first** — how the skill behaves with and without a
  `conventions.yml`. Skills must degrade to sensible generic guidance, never fail.
- **Numbered steps** in the order the work is actually done.
- **`## Completion checklist`** — checked by CI.
- **Common failure modes** — what goes wrong and how it is noticed. This section
  carries most of the value; it is the part a competent engineer cannot derive
  from the documentation.

Longer material goes in sibling files in the same directory, linked from
`SKILL.md`. Links are resolved by CI.

### Writing rules

- **Never hardcode a project's values.** Read them from the contract. A skill that
  asserts a prefix or a database name is a skill only its author can use.
- **Gate warehouse-specific advice** on `project.warehouse`. Clustering advice that
  is correct on one warehouse is noise or wrong on another.
- **Show, don't assert.** Skills should instruct the agent to run a command and
  read the output, not to conclude that something worked.
- **State the trap.** Where a default silently does the wrong thing, say so
  plainly. That is the reason the skill exists.

## Local setup

```bash
pip install pre-commit
pre-commit install
```

Run the checks directly:

```bash
python3 scripts/check-skills.py
```

The leak scan needs a private term list that is deliberately not in this
repository — publishing an inventory of internal identifiers is the exact leak
it exists to prevent. Without one, every structural check still runs and the
scan reports itself as skipped, which is all a contributor needs. Maintainers
cutting a release pass `--terms <path>` or set `$DBT_NAVIGATOR_TERMS`.

## Pull requests

Conventional commit format for the title: `type(scope): description`.

In the description, state which skills changed and — for any behavioral
change — what an agent would now do differently. A diff of prose is hard to
review without that.
