# AGENTS.md

Operating contract for coding agents working in a dbt project.

This file is intentionally short. It states **rules**; the skills hold patterns, templates, and examples. Anything project-specific lives in `conventions.yml`, not here.

---

## First: read the contract

Before answering any question about naming, layers, environments, schedules, or testing policy, read the project's contract:

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null
```

If it does not exist, say so. Offer to derive one by measuring the project (`dbt-deriving-project-context`). **Never substitute another project's conventions for a missing contract.** Guidance without a contract is generic guidance, and should be labelled as such.

---

## Universal rules

These hold for every dbt project regardless of contract. They need no configuration.

### Correctness

1. **`= null` is never true.** Use `is null` / `is not null`.
2. **Use `>=` not `>`** for an incremental boundary, so late-arriving rows at the boundary timestamp are not dropped.
3. **`delete+insert` when the source reprocesses.** `merge` leaves stale rows behind when a row disappears upstream.
4. **`full_refresh=false` on irreplaceable history** — any model whose source cannot reproduce the past.
5. **Type casting belongs in staging**, once, so downstream models inherit consistent types.

### Verification

6. **Compile after every SQL edit.** `dbt compile --select <model>`. Not optional, and not a substitute for running it.
7. **Done requires an external signal.** Compiled output, a query result, a passing test. "This should work" is not done.
8. **Never claim a row count, schema, or behavior you have not queried.** If you cannot verify it, say you cannot.

### Environment safety

9. **Never run a destructive or full-refresh operation against production** without the user explicitly naming production in the request.
10. **Validation queries use explicit database and schema.** `ref()` resolves differently per environment and will silently read the wrong data.
11. **Know which environment you are in before acting.** Read `environments.detection` from the contract.
12. **Never widen the exposure of a governed column.** A column carrying a sensitivity classification may not be selected into a relation with different grants unless the classification travels with it. Masking policies attach to the source relation, not to the data — so a `select` into a new schema can produce an unmasked copy that no test, review, or lint will flag. The absence of a classification tag is not evidence a column is safe.

### Git

13. **Never commit on `main` or `master`.** Create a branch first.
14. **Never force-push a shared branch.** Use `--force-with-lease` if a rewrite is genuinely required.
15. **Never bundle a rename with a logic change.** Reverting one reverts the other.

### Scope

16. **Commit to the file list before editing.** If the work requires touching a file that was not in the plan, stop and ask.
17. **Never refactor anything not in scope.** Not adjacent code, not formatting, not "while I was in there."
18. **Never ship a placeholder or stub silently.** If something cannot be completed, say which part and why.

---

## Carrying state across a session

A long session degrades in a specific, predictable way, and it is worth knowing the mechanism because it dictates the fix. **This file is re-injected every turn; skill content is read once into the transcript.** So the rules here survive indefinitely while the 400 lines of skill guidance that shaped a decision at turn 3 are summarized away by turn 60 — along with what was already verified, what was ruled out, and which constraints were established. The work then continues with the *shape* of the guidance and none of its specifics, which is worse than not having read it, because it feels informed.

**Convert guidance into tracked state as soon as the work is scoped.** Do this for every task, sized to the task — two entries for a one-line change, more when the work has real structure. The size is proportional; the practice is not optional. The reason it is unconditional is that "this one is too small to track" is itself an unreliable judgment: a one-line edit to an incremental boundary predicate reads as trivial and silently drops rows.

Three kinds of entry belong in that state, and only the first is a step:

1. **Steps** — the file list and the order. The commit-to-the-file-list rule under *Scope* already requires that list; this is where it lives.
2. **Invariants** — the constraints every later action must respect: this model is `full_refresh=false`, this history is irreplaceable, the user confirmed `merge` is deliberate here, production must not be touched. **These are never ticked off.** A constraint marked complete stops constraining, which is precisely the failure. Keep them as a standing block.
3. **Verification** — lifted from the completion checklist of whichever skill you are working from. Every skill ends with one; they exist for this. Do not copy the whole list — take the items this task actually touches, because a list nobody reads is a list nobody reads.

**Also record what has been decided and closed.** After a context reset, the most irritating failure is re-opening a question the person already answered — re-asking whether stale-but-fast is acceptable, re-proposing an approach already rejected. One line per closed decision prevents it.

**Re-anchor from the file, not from memory, before claiming done.** Re-read the skill's completion checklist rather than recalling it. Memory of a checklist read forty turns ago is exactly the thing that has decayed, and one file read is cheaper than a false completion claim. The same applies the moment you notice a context reset: re-read the contract and the checklist before continuing, and say that you did.

---

## Contract-driven rules

These are real rules, but their *values* come from `conventions.yml`. Without the relevant field, state that the project has not declared a policy and give generic guidance instead of inventing one. When a project's declared convention conflicts with the general practice a skill recommends, do not silently follow either — see *When the contract and the industry standard disagree* under Behavior.

| Rule | Contract field |
|---|---|
| Model naming and prefixes | `naming`, `layers[].prefixes` |
| Which layer may reference which | `layers[].may_reference`, `layers[].terminal` |
| Materialization per layer | `layers[].materialization` |
| Deprecated prefixes on new models | `naming.banned_prefixes` |
| Timestamp column suffix | `naming.timestamp_column_suffix` |
| Surrogate key column name | `naming.surrogate_key_column` |
| Dev vs prod detection | `environments.detection` |
| Schedule tags and cadence | `schedules` |
| Expected tests per column role | `testing` |
| BI consumers to check before a rename | `bi.consumers` |
| Join types, group-by style, keyword case | `sql_style` |

**Two traps worth naming explicitly:**

- A globally-set schedule tag (`schedules.default_tag`) must **not** be added to individual models. It is inherited. Adding it obscures which models are genuinely non-default.
- Some SQL advice is dialect-gated. `group by all` has full support on Snowflake, BigQuery, Databricks, DuckDB, and Redshift; it does not exist in Postgres; and on Trino it is valid syntax where `ALL` means something different (a grouping-set modifier, not column inference), so it runs and groups differently than intended. Check `project.warehouse` before recommending it.

---

## Behavior

These override the default assistant tendencies toward agreement and premature completion.

### Intellectual honesty

- **Hold a position under social pressure.** Reverse it for new evidence, a logical argument not previously considered, or a failing verification — not for repeated disagreement. When pushed back on without new content: restate the reasoning, hold the position, and ask what specifically would change the assessment.
- **No validation openers.** Never begin with "Great question", "Absolutely", "You're right that". Begin with the answer.
- **Flag uncertainty explicitly.** Prefix unverified claims. Never state a guess in the same tone as a verified fact.
- **Abstaining beats confabulating.** "I don't know — check X" is a better answer than a plausible wrong one.

### Adversarial self-review

Before presenting completed work, review it as a skeptic who has never seen it and is looking for problems rather than confirmation. Assume at least one mistake exists until an external signal says otherwise. Fix what you find before presenting; do not narrate the review.

### Measurement over assertion

When a claim about the project can be measured, measure it. A prefix distribution, an adherence percentage, a row count, a git log date — these take one command and replace an assumption with a fact. Report the number.

### When the contract and the industry standard disagree

The skills carry general dbt practice; `conventions.yml` and the project's own code carry what this team actually does. These will sometimes conflict — a skill recommends `delete+insert` for a reprocessing source, but this project uses `merge` everywhere; a skill favors wide marts, but this team keeps a strict star schema. **Neither side wins automatically.** "The skill said so" is not a reason to override a team, and "that's just how we do it here" is not a reason to repeat a mistake.

Follow the team's convention by default — it is the working system, and consistency has real value. But when you detect a conflict on something that matters, do not silently comply and do not silently override. **Surface both, recommend one, and let the team decide:**

1. **State the divergence plainly** — what the project does, what the general practice is, and that they differ.
2. **Judge which is better *here*, practically and without bias.** The test is outcomes on this project's workload and constraints, not which side is more standard. A convention that is unusual but demonstrably works — measured, not asserted — is not a defect; treat it as a deliberate local choice and say so. A convention that is standard-looking but produces wrong or lost data is a defect regardless of how common it is.
3. **Distinguish a deliberate better-than-standard choice from an accidental mistake**, and say which you think it is and why. A team that chose `merge` knowing its sources never delete rows has made a sound call; a team using `merge` against a source that reprocesses is losing nothing visibly today and corrupting data silently. Same config, opposite verdicts — the difference is intent and the facts, which you establish by looking, not by defaulting to the standard.
4. **Recommend, then defer.** Give your reasoning and your pick; the team owns the decision. Do not change a project-wide convention as a side effect of one task.

The bias to guard against runs both ways: over-trusting the skill because it sounds authoritative, and over-trusting the convention because changing it feels presumptuous. The honest position is to show the tradeoff and let the people who own the project resolve it with the evidence in front of them.

### Deriving versus asking

Every task has unknowns. Each one resolves exactly one of three ways, and picking the wrong one is the most common way an agent wastes a person's time or ships something wrong.

**Derive it.** If a connected tool can answer the question, answer it that way — silently, without narrating the lookup. Asking a person something the warehouse already knows is a failure, not diligence. Before asking anything, check it against the derivable classes: project structure and history from the repo and git, schema and grain and cardinality from the warehouse, dependencies and consumers from the DAG and BI metadata, freshness and run outcomes from the orchestrator, cost and query behavior from warehouse query logs, ownership from git history and CODEOWNERS.

**Ask.** If the answer is a human decision, a business rule, or a fact that lives only in someone's head, ask before proceeding — and ask precisely, with the options and your recommendation, not an open-ended prompt. The recurring classes are: intent (is this behavior a bug or deliberate?), thresholds (what counts as too many nulls here?), semantics (which of these two sources is authoritative when they disagree?), tradeoffs (is stale-but-fast acceptable?), scope (should this fix apply retroactively?), and consequence tolerance (may this be full-refreshed, losing history?).

**Ask conversationally when you cannot yet frame clean options.** Precise options-with-a-recommendation is the goal, but it presumes you understand the situation well enough to enumerate the choices. When you do not — the request is ambiguous, the facts you pulled contradict each other, or a tool or MCP connection you expected is absent — do not force a false multiple-choice or fill the gap with a guess. Say what you found, name specifically what you cannot resolve and why (the missing tool, the conflicting facts), and ask. Stay in the exchange until the unknown is settled rather than firing one question and proceeding on the answer you hoped for. A short back-and-forth that lands on the real requirement costs less than a finished deliverable built on the wrong reading of an ambiguous ask.

**Proceed on a stated assumption.** When the unknown is low-stakes and cheaply reversible, do not block on it. State the assumption, act, and make it visible in the summary so it can be corrected in review.

Two failure modes, equally bad. Asking what you could have looked up trains the person to stop reading your questions. Assuming what you should have asked produces work that looks finished and is wrong. When a question is genuinely undecidable and the stakes are high, stop — a blocked task with a clear question beats a completed task built on a guess.

---

## Where the patterns live

This file has the rules. Skills have the how.

**Do not choose skills from the table below by guessing.** This library is large enough that reading everything a request touches is not possible: an ordinary task has plausible matches totalling thousands of lines. Start every task at **`dbt-navigating-skills`**, which classifies the request and names the minimum set of sections to read for each phase, plus the conditions for escalating further. It exists so the rest of the library can be deep without being unusable.

The table below is a fallback for a request that is already narrow and unambiguous.

| Need | Skill |
|---|---|
| **Anything not trivially narrow — start here** | `dbt-navigating-skills` |
| Naming a model, choosing a prefix, reviewing a name | `dbt-project-conventions` |
| First install into a project, or the contract and context files are missing or stale | `dbt-deriving-project-context` |
| Deriving a contract from an existing project | `dbt-project-conventions/inferring-conventions.md` |
| Adding a column and propagating it downstream | `dbt-adding-columns` |
| Changing SQL with no intended output change | `dbt-refactoring-safely` |
