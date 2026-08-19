# Adopting this on a real team

A path from install to habit, with the checkpoints that tell you it is working. Nothing here needs permission from anyone outside your team.

The whole thing rests on one measured fact: **the full skill set is roughly 135,000 tokens.** It cannot all be loaded, and it is not supposed to be. Each skill is pulled in only when its `description` matches what you asked, so a typical task loads one or two — a median of about 280 lines. Everything below exists to make that selection land on the right skill.

---

## Day 1 — Install and derive the context

Install per the README, then, before anything else:

```
Read the dbt-deriving-project-context skill and derive this project's context.
```

The agent measures your repo and drafts `conventions.yml` with adherence percentages, then writes `.dbt-agent/mechanisms.md`, `domain.md` and `references.md`. **Review these as documents about your team, not config files.** Three failure modes to watch for:

- **Aspiration.** If someone writes down the taxonomy they wish existed, every skill will flag two-thirds of your models as violations, and your team will learn to ignore the output within a week. Record what the project *does*. Fix the codebase separately if you want it to change.
- **Guessed fields.** Anything the agent could not measure should have been asked, not filled in. `project.query_history_retention_days` is the one that matters most: it is stated in the contract because an agent otherwise has no way to know how far back the query log reaches, and a "nothing has read this in 90 days" claim drawn from a 7-day log is how a live model gets deleted.
- **Invented business meaning.** The canonical-metric section of `domain.md` should be *empty* unless someone confirmed the definitions, and inferred entries should be marked `NEEDS CONFIRMATION`. An invented definition is worse than a blank one, because it gets quoted back as authority.

The same standard applies to `domain.md`'s three business sections — which source systems feed the warehouse and what each is the system of record for, how entities link across systems, and what your central marts are for. **Expect most of these to come back as questions on a first run, not as filled-in prose.** They are absent from the repository and easy to guess from names, which is the combination that produces confident errors: "the CRM is authoritative for customers" inferred from a directory name reads exactly like a fact somebody established. A source-system table that arrives complete with no questions attached is the signal to check how the agent knew. Answering that batch is the highest-value hour a new adopter spends here — it is what stops a cross-system join from being written against a population that was never supposed to match.

Set `verified_at` to today. It is the only thing that later tells a reader whether this file was measured last week or two years ago.

**Read `mechanisms.md` yourself, closely.** It is where the agent records what your project already solved — a mandated macro for dev data limits, custom environment detection, a blue/green deployment procedure, generated exposures you must not hand-edit. This is the file that stops an agent hand-rolling your existing solutions, and the one most likely to be incomplete on a first pass, because some of its contents are invisible to a reader of the models.

**Expect an appraisal, and expect to disagree with some of it.** The agent classifies each practice as best practice, a deliberate variant that works better here, or a candidate defect. Read the variants: if it recorded one of your deliberate choices as a defect, say so and have it moved — that is the correction that stops the same argument recurring every session. Nothing is changed on the basis of an appraisal without you asking.

**Checkpoint:** ask the agent to name a new mart model. If it uses your prefix and separator without being told, the contract is wired up. Then ask it to limit a dev build to recent data. If it reaches for your project's macro rather than `--vars` or a hand-written filter, `mechanisms.md` is working.

## Day 2 — One real task, watched closely

Pick a task you would otherwise do yourself in an hour: add a column, fix a failing test. Then read what the agent does, not just what it produces.

You are checking three things:

1. **Did it load a skill at all?** If it free-styles, the description did not match your phrasing. Say the operation out loud — "I need to add a column to X" — and see if that changes the outcome.
2. **Did it derive before asking?** It should discover your grain, types, and dependencies without interrogating you. A question about something the warehouse could answer is a bug worth reporting.
3. **Did it prove the result?** "This should work" is not done. Expect a compile, a row count, or a comparison — see `dbt-verification`.

**Checkpoint:** one merged change where you did not have to correct the conventions.

## Week 1 — Write down what no tool can compute

This is the step teams skip, and it is the one that stops the agent asking you the same question every week.

Create the two files, set `context.domain_notes` and `context.references`, and **write only three things to start**:

- The two or three metrics where a wrong-but-plausible definition exists.
- Your business day boundary, if it is not midnight UTC.
- The two traps that have caused an incident.

Then stop. The filter for everything else is one question: **could a connected tool compute this?** If yes, leave it to the tool — a copy of a derivable fact goes stale while the real answer moves on, and the copy is what gets believed. Templates are in `examples/domain.example.md` and `examples/references.example.md`.

If you have SLAs or criticality rankings anywhere, put them in model `meta` and declare your key names under `context.meta_keys`. Without a declared SLA, "is this table late?" is genuinely unanswerable — staleness is derivable, lateness is not.

**Checkpoint:** ask "what is our definition of \<your trickiest metric\>?" and get your own answer back.

## Week 2 — Make the invariants mechanical

Skills advise. They do not enforce, and advice loses to deadline pressure eventually.

Pick the two or three rules your team actually breaks — the ones that have caused a real problem — and enforce them in CI instead of in prose. Nobody argues with a failing check, and it costs no tokens. `docs/hooks.md` lists the checks worth starting with.

**Checkpoint:** one rule that is now impossible to merge past.

## Ongoing — Keep the contract honest

Contracts rot at different rates, and the fastest-rotting fields describe systems this repo does not control: environments, schedules, warehouse identity. When a skill's advice contradicts what you observe, the observation wins and the contract is stale. Fix it then, and move `verified_at`.

Record accepted departures in `deviations[]` rather than re-litigating them. A grandfathered prefix flagged in every session is how a team learns to ignore the tooling — put it in `deviations[]` with a reason and it stops being noise. Use `expires` when it is debt you actually intend to pay.

---

## Getting the most out of it

**Name the operation, not just the object.** "Split this model in two" loads the DAG-restructuring skill; "clean up this model" loads nothing in particular. The `description` field is the entire selection mechanism, so how you phrase the request decides which expertise arrives.

**Let it push back.** Several skills are designed to slow a request down — refusing to guess a grain, insisting a rename ship separately from a logic change, declining to call a model unused when the query log could not be read. That friction is the product. An agent that cheerfully renames a column feeding a finance dashboard is faster and worse.

**Treat "I could not verify that" as a feature.** The skills are written to distinguish *checked*, *unverifiable*, and *assumed*. When you see the second, the honest answer was that the instrument was blind — an unintegrated BI tool, a query log outside retention. That is a real limit of your setup, not a limit of the agent, and it is worth fixing at the source.

**Do not add per-model facts to `domain.md`.** They belong in that model's `description` or `meta`, where they sit next to the thing they describe and ship to your catalog. Prose files are the fallback for knowledge with no better home.

## What this will not do

- **Enforce anything.** Skills are read, not run. Enforcement is CI.
- **Know your business.** Everything specific to you comes from the contract and written context. With neither, you get competent generic dbt advice that says so.
- **See tools it is not connected to.** Consumer and lineage claims are only as complete as your integrations. Declare `exposures` so the DAG knows about consumers the catalog cannot see.
- **Replace review.** It raises the floor on routine work. A grain change to a model feeding revenue reporting is still a human decision.
