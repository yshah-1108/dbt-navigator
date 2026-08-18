# Subagents: an optional accelerator

The skills are the product. The four subagents in `agents/` are a speed
optimization for one harness, and everything they know comes from skills that
ship anyway. **If your harness has no subagent mechanism you lose some context
headroom, never a capability.** That is a deliberate constraint, and
`scripts/check-skills.py` enforces it: an agent definition that grows past 90
lines fails the build, because an agent long enough to hold real dbt guidance is
holding guidance that belongs in a skill.

## Why only four

Subagents earn their overhead when a task **reads wide and reports narrow**. A
delegated task spends its own context window — often tens of thousands of tokens
— and returns perhaps a thousand. The parent gets the finding without paying for
the search.

That shape describes very little of dbt work, which is why there are four agents
and not one per skill:

| Agent | Replaces | Reads | Returns |
|---|---|---|---|
| `dbt-context-deriver` | manual onboarding | every model, macro, workflow, git log | 4 context files + honest gaps |
| `dbt-impact-scout` | blast-radius tracing by hand | manifest, exposures, BI catalog, query log | consumers + evidence quality |
| `dbt-change-reviewer` | self-review | the diff, plus the contract | findings ranked by consequence |
| `dbt-perf-profiler` | reading query profiles inline | profiles, plans, warehouse metadata | the measured bottleneck |

**What is deliberately not a subagent**, because delegating it makes the work
worse rather than cheaper:

- **Debugging.** You form a hypothesis, test it, refine it. A subagent starts
  cold and cannot inherit the hypothesis you just formed, so it re-derives from a
  summary that by definition does not contain it. Slower and worse.
- **Writing SQL, adding columns.** The main agent must own the diff and be able
  to explain every line. Delegating authorship means nobody in the conversation
  can answer why the code looks the way it does.
- **Designing a model.** Needs dialogue with a human, which a subagent cannot have.
- **Answering questions.** That is already the main thread's job; delegating it
  adds a round trip and a lossy summary to a task with no context problem.

The general rule, which is worth applying before adding a fifth: extract a
specialist only after the same task shape has been handled inline three times.
Seven agents invoked twice a month each drift in quality and each cost a cold
start.

## The failure this design guards against

Every handoff is a lossy compression event. The things that reliably disappear
are causal reasoning, unstated constraints, **uncertainty signals**, and negative
space — what was tried and rejected, what could not be checked. Prose summaries
flatten a hedged claim into an assertion because the summarizer chooses what to
keep.

For this library that failure is existential rather than annoying. The whole
point of these skills is that *"no references found" and "unused" are different
claims*. A subagent that trawls four instruments, finds nothing, and reports "no
consumers" has produced a confident falsehood out of an honest gap — and the
summary reads perfectly clean.

Hence the one rule every agent here follows: **return a structured contract, not
prose.** Each has an explicit slot for what it could not verify and which
instrument answered. A schema carries the caveat whether or not the summarizer
thought it was important; prose does not. The checker requires both the contract
and the could-not-verify slot.

This reduces the loss. It does not eliminate it — a typed boundary can only carry
what the agent actually wrote down, and the most dangerous omission is the
constraint so obvious to the delegate that it never reached any field. When the
parent's next decision is expensive, read the evidence rather than the verdict.

## Porting to another harness

The frontmatter is Claude Code's. The body is a portable system prompt.

For Cursor, this is scripted — `python3 scripts/port-agents.py cursor <dest-repo>` writes `.cursor/agents/`, rewriting the frontmatter and moving what Cursor cannot express into the body. Run it again after editing any agent; it is deterministic, so re-running is safe.

| Harness | Where it goes | Notes |
|---|---|---|
| Claude Code | `.claude/agents/` | works as shipped |
| Cursor | `.cursor/agents/` | `scripts/port-agents.py` — frontmatter is `name` and `description` only |
| Codex / Gemini CLI | paste the body as a system prompt | same treatment as Cursor, by hand |
| Anything else | the body is just text | keep the return contract verbatim |

Two mechanical differences, both confirmed against a shipped Cursor plugin rather than assumed:

1. **`skills:` preloading may not exist.** In Claude Code that field injects full skill content at startup. Cursor has no equivalent — its agents name skills in the prose instead. The port script therefore prepends a "read these first" block listing the same skills. Same outcome, one extra step at runtime.
2. **Tool restriction may not exist.** Three of these are read-only by design — `dbt-impact-scout`, `dbt-change-reviewer` and `dbt-perf-profiler`. Cursor's format has no `tools` field, so the port script states the restriction in the body and labels it as a convention rather than a boundary. Be aware of the difference: a reviewer that edits the code it is reviewing has destroyed the independence that made the review worth running, and on such a harness only the prompt is stopping it.

## If you skip subagents entirely

Nothing breaks. `dbt-navigating-skills` routes the same 14 archetypes, and every
skill these agents preload is read directly instead. You pay in main-thread
context: deriving project context inline is the expensive one, since it means
reading the whole repository in the conversation you then have to keep working
in.
