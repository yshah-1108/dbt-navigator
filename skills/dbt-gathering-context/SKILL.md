---
name: dbt-gathering-context
description: Use when a task depends on a fact you do not have — who consumes a model, what the grain is, which job builds it, whether a value is a bug, what threshold is acceptable. Covers which facts are derivable from connected tools versus which require asking a human, how to verify a tool can actually see what you asked it, and how to ask well when asking is unavoidable.
metadata:
  phase: orient
---

# Gathering context

Every request arrives underspecified. "Add a column", "this number looks wrong", "make this faster" — each one hides facts the requester assumed you had. How you resolve those unknowns determines whether your work is correct, and it is the single largest source of confidently-wrong output.

Each unknown resolves exactly one of three ways:

| Resolution | When | Cost of getting it wrong |
|---|---|---|
| **Derive it** | A connected tool can answer it | Asking a person what the warehouse knows trains them to stop reading your questions |
| **Ask** | It is a human decision, a business rule, or lives only in someone's head | Assuming produces work that looks finished and is wrong |
| **Assume and state** | Low-stakes and cheaply reversible | Blocking on trivia is its own failure |

The two failure modes are symmetric and both are common. Over-asking makes you useless; under-asking makes you dangerous. The discipline below is how to tell which situation you are in.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | What it decides here |
|---|---|
| `project.warehouse` | Which metadata views and query-log relations exist to derive from |
| `bi.consumers[]` | Which consumer classes exist at all — the list you must prove your tooling covers |
| `bi.consumers[].status` | Whether a discovered consumer blocks or merely needs notice |
| `environments` | Whether a fact you derived came from production or from a stale dev copy |

The contract deliberately does **not** enumerate which tools or MCP servers are connected. A hand-maintained inventory of live integrations rots faster than anything else in a config file, and a stale one is worse than none because it produces false confidence. Discover capability at runtime instead — see step 3.

---

## 1. The derivation ladder

Work down this list and stop at the first layer that answers the question. Higher layers are faster, cheaper, and need no credentials.

| Layer | Answers | How |
|---|---|---|
| **Written project context** | Business meaning, canonical metric definitions, closed decisions, known traps, SLAs, and this project's own sanctioned mechanisms | `context.domain_notes`, `context.mechanisms`, `context.references`, and `context.meta_keys` from the contract |
| **Repo and git** | Conventions, history, ownership, intent-at-the-time | `git log`, `git blame`, `grep`, `CODEOWNERS`, PR bodies |
| **dbt project** | Dependencies, config, tests, documented grain | `dbt ls`, `dbt compile`, the manifest, YAML |
| **Warehouse metadata** | Real types, real grain, real cardinality, row counts | `information_schema`, `count(*)`, `count(distinct)` |
| **Warehouse query log** | Actual readers, cost, slow queries, unlisted consumers | query-history and access-history views |
| **Orchestrator** | Which job builds this, when it next runs, whether last night passed | scheduler API or dbt Platform jobs API |
| **Catalog / observability** | Cross-tool lineage, freshness, incidents, importance | catalog or observability platform API |
| **BI platform** | Field-level references, dashboard usage, whether anyone would notice | BI metadata and usage APIs |
| **Issue tracker and docs** | Why this was built, prior decisions, known problems | ticket and wiki search |
| **A human** | Everything in step 4 | Ask, per step 5 |

Written context sits at the top for a reason: it is the only layer holding answers that are **not computable from anything below it**, and it is the cheapest to read. A canonical metric definition, a business day boundary, a decision already made — no query returns these. Read it first and some must-ask questions are already answered.

Three rules about using the ladder. **Do not narrate the climb** — derive silently and report the fact, not the search. **Prefer the lower-numbered layer even when a fancier one exists**: `count(distinct)` settles a grain question in one query more reliably than any catalog's documented grain, because the catalog records what someone once believed and the query records what is true. And **written context loses to observation on any fact a query can check**. Its authority covers intent and meaning, never current state: if `domain.md` says a key is unique and `count(*)` disagrees, the query is right and the note is stale.

---

## 2. The derivability matrix

The questions that actually come up, and where they land. Verify specifics against your own stack; the classification generalizes.

| # | Question | Verdict | Where from |
|---|---|---|---|
| 1 | What breaks if I change this? | **Partial** | `dbt ls --select <model>+` and exposures, **plus** the query log — the DAG systematically undercounts, being blind to ad-hoc SQL, reverse ETL and apps |
| 2 | Is this column used in a dashboard? | **Partial** | BI usage APIs, catalog field-lineage, or warehouse column-level access history — *only for tools actually integrated*. See step 3. |
| 3 | When did this last load; is it late? | **Partial** | *When* is derivable (`max(<timestamp>)`, catalog last-altered, run history). ***Late* is not** — it needs a declared SLA, so check `context.meta_keys.sla` before asking; freshness is very often unconfigured |
| 4 | What is the grain / is this key unique? | **Partial** | "Is key K unique" is derivable: `count(*)` vs `count(distinct K)`. "What *is* the grain" is a hypothesis you can only test — and whether a duplicate is a bug is intent |
| 5 | Which job builds this, and when next? | **Derivable** | Orchestrator API. Mapping model→job means resolving each job's selector against the DAG — mechanical, but not one call. Never infer from tags alone. |
| 6 | Did last night's run succeed? | **Derivable** | Orchestrator run history and error detail |
| 7 | Why is this slow / what does it cost? | **Derivable** | Query profile; for credits, attribute queries to nodes via the query log. Verify how your adapter tags queries — a documented recipe may not match what your account actually emits. |
| 8 | Who owns this / who changed it and why? | **Partial** | *Who changed it* is derivable from git. *Why* is only as good as the PR body. ***Who owns it* is a must-ask** unless recorded in CODEOWNERS, dbt `groups`, or the key named by `context.meta_keys.owner` |
| 9 | Is there an open incident or ticket? | **Partial** | Alert→table linkage is derivable where the observability tool has the asset; ticket→model linkage is fuzzy text matching |
| 10 | What did this look like before? | **Derivable** | `git show`, or warehouse time-travel within retention |
| 11 | Does this dashboard get used, or can I retire it? | **Partial** | Usage counts are derivable where the BI tool exposes them; whether a quarterly report matters more than a daily bot query is judgement |
| 12 | What is the business definition of this metric? | **Mostly ask** | Derivable only where encoded — semantic layer, docs, glossary, or `context.domain_notes`. SQL tells you what is *computed*, never what the business *means*, nor which of two rival definitions is canonical |
| 13 | Has the source schema changed upstream? | **Partial** | Current shape is derivable; the *change* needs history — a monitor or snapshot. Bare metadata views have no history. Whether it was intentional is a must-ask. |
| 14 | Is this PII / how is it classified? | **Partial** | Existing tags and masking policies are derivable. **Absence of a tag is not evidence of absence of PII.** |
| 15 | What is an acceptable range for this metric? | **Ask** | The historical distribution is derivable; which deviations *matter* is a business tolerance |

Two patterns worth internalizing. **The data is derivable, the meaning is not** — you can always compute what a metric has done; you cannot compute whether a 5% drop is a problem. And **most questions are only *conditionally* derivable**: the tool exists and works, but the answer depends on metadata a human had to populate first. Barely a third of these are clean lookups. Treat every "partial" as a **precondition to check**, not a capability to assume.

---

## 3. Prove the instrument can answer before trusting a negative

The most dangerous result any tool returns is a well-formed empty one. A successful call with nothing in it reads exactly like knowledge, and it is the way an agent most often confabulates while believing it is being rigorous.

An empty or null answer has **three** possible meanings, indistinguishable from the response alone:

| Meaning | Reality | What it needs |
|---|---|---|
| 1. Genuinely nothing | The answer is no | Nothing — the negative is real |
| 2. **The instrument is blind** | The tool does not index that class at all | Trace the class another way |
| 3. **The metadata was never populated** | The tool works perfectly; no human ever filled the field in | Ask the human who should have |

Reading (2) or (3) as (1) is how an agent reports "no BI impact found" and breaks a dashboard the next morning.

Both failure modes are ordinary, not exotic. A catalog can be fully integrated with one BI tool and blind to another, answering for the blind one with the same empty list and the same confident phrasing. And a freshness API returns `"Unconfigured"` with a null timestamp — not an error — when nobody ever set a threshold; a lineage API returns exposures whose descriptions are all empty strings, so the *link* is derivable and the *purpose and criticality* are not.

**Technique for a blind instrument: query for the class before the instance.**

```
# Wrong: one call, ambiguous negative
downstream_bi_reports(table)  ->  []   # means what?

# Right: establish coverage first
search(resource_types=[<bi_asset_type>])  ->  0 assets   # BLIND; negative is worthless
                                          ->  N assets   # has coverage; negative is evidence
```

Do this once per consumer class, then cross-check against `bi.consumers`. A tool listed in the contract with zero assets of its type is a **coverage gap**: trace it by grep and query log instead, and name the gap in your summary.

**Technique for unpopulated metadata: treat the enabling field as a precondition, not a capability.**

Before relying on an answer that depends on human-entered metadata, check the metadata exists. Is `freshness:` actually configured, or is the status "unconfigured"? Does the exposure have a description, or an empty string? Is there an `owner` in `meta`, or only a git last-toucher? Is the column tagged, or merely untagged? When the field is empty, the honest output is a question, not an inference.

Generalize both: **an absence is only evidence once you have shown the instrument can detect a presence and that someone recorded one.** The same rule applies to query logs with short retention, partial catalog integrations, and grep over a repo that builds relation names dynamically.

Report the distinction precisely. These are three different claims and only the last two are honest:

- "No consumers." — asserts a fact you have not established
- "No consumers in the dbt DAG or the 30-day query log; the primary BI tool is not indexed by any tool I can reach."
- "Freshness is unconfigured on all three sources, so I cannot say whether this is late. Someone needs to set a threshold."

---

## 4. What you must ask

Nine classes. No tool answers these, because they are not facts about the system — they are decisions, judgements, or things nobody recorded.

| Class | The question underneath | Example |
|---|---|---|
| **Intent** | Is this behavior a bug or deliberate? | Duplicate rows: broken join, or legitimately multiple events? |
| **Threshold** | Where is the line between fine and broken? | How many nulls in this column is acceptable? |
| **Semantics** | Which meaning is authoritative? | Two sources disagree on revenue — which wins, and is the gap a bug or a definition? |
| **Tradeoff** | Which cost is preferred? | Cheaper and an hour staler, or fresh and 3× the spend? |
| **Scope** | How far does this apply? | Fix forward only, or backfill history too? |
| **Consequence tolerance** | Is this loss acceptable? | May this be full-refreshed, discarding unreconstructable history? |
| **Proven absence** | Is there really none, or did I just not find any? | "Nothing consumes this model"; "there is no PII here" |
| **Accountable ownership** | Who is on the hook, as opposed to who typed last? | git blame names a contractor who left |
| **Criticality** | Does this matter enough to act? | A dashboard viewed four times a year may be the board deck |

**Check written context before asking any of these.** Six of the nine classes have somewhere they *can* be recorded, and a project that has recorded them has already answered you. Thresholds, semantics, and closed tradeoffs belong in `context.domain_notes`; SLAs, criticality, and accountable ownership belong in the `meta` keys named by `context.meta_keys`. Asking a question the repository already answers is the same failure as asking one the warehouse already answers.

Two cautions on that. A recorded answer can be **stale** — it beats a guess, but on any fact a query can check, the query wins. And an **absent** record is not an answer: no `sla_hours` on a model means nobody declared one, which makes lateness unanswerable rather than acceptable.

---

## Where written context lives

Three homes, and the choice between them is mechanical rather than a matter of taste.

| Home | For | Why there |
|---|---|---|
| **dbt-native fields** — `description`, `meta`, `exposures`, source `freshness`, `groups`/`owner` | Anything about one specific node | It sits next to the thing it describes, versions with it, and ships to the catalog and the docs site. A fact about a model recorded anywhere else will drift away from that model. |
| **`context.domain_notes`** | Knowledge spanning many models: what the business means, canonical definitions, closed decisions, traps | Has no single node to attach to. Attaching it to one arbitrary model hides it from everyone reading the others. |
| **`context.mechanisms`** | Bespoke machinery of *this* project: a mandated macro for limiting data in dev, custom environment detection, an overridden dbt built-in, generated-rather-than-hand-written exposures, CI checks that change what is worth doing by hand | Answers a question no amount of warehouse access can: *has this project already solved the thing I am about to hand-roll?* Some of it is invisible even to a careful reader of the models — an overridden `generate_schema_name` appears in no model file at all, and grepping for its usage returns nothing, because dbt calls it automatically. |
| **`context.references`** | Pointers to documents that live elsewhere | The document has an owner and a lifecycle outside this repository. Copy it in and you own a stale fork of someone else's file. |

The ordering is a rule, not a preference: **prefer the most specific home that fits.** A column's meaning goes in that column's `description`, never in `domain.md`, because a reader of the YAML will never think to look in a prose file. Prose is the fallback for knowledge with nowhere better to go.

**Read `context.mechanisms` before writing SQL or shipping, not only when stuck.** It is the one context file whose value is preventive: by the time you notice you needed it, you have already hand-written the filter the project has a macro for. Its inclusion test is correspondingly strict — a mechanism earns a place only where a skill's sensible generic default would be *wrong* here. Anything that merely restates advice a skill already gives is a duplicate that will drift out of sync with the skill and still be believed.

**If these files do not exist, or are stale enough to mislead, that is its own task** — see [`dbt-deriving-project-context`](../dbt-deriving-project-context/SKILL.md), which produces all four artifacts by measurement. Do not fill them in opportunistically from what one task happened to teach you: a context file assembled from fragments reads as authoritative while covering only what someone recently looked at.

### What is worth bringing

The filter is one question: **could a connected tool compute this?** If yes, do not record it — a copy of a derivable fact is a copy that goes stale while the real answer moves on, and the copy is what gets believed.

| Bring | Leave to the tools |
|---|---|
| Canonical metric definitions where rivals exist | Column names, types, row counts |
| The business day boundary and fiscal calendar | Dependencies and lineage |
| Decisions already made, with dates and reasons | Run history, timings, schedules |
| Traps that caused past incidents | Which job builds a model |
| SLAs and criticality rankings | When a table last loaded |
| Accountable teams per domain | Who last committed |
| Pointers to policies and runbooks | Anything in the manifest |

Start with the left column's first three rows and stop. The failure mode here is not a thin file — it is a thorough one nobody maintains, because unlike a stale tool reading, a stale note gives no sign that it is stale.

Templates for both files are in `examples/domain.example.md` and `examples/references.example.md`.

### Reading them

```bash
# resolve the paths from the contract, then read what exists
grep -A4 '^context:' conventions.yml 2>/dev/null || grep -A4 '^context:' .dbt-agent/conventions.yml 2>/dev/null
```

Absence is normal and not a blocker — most projects have no written context, which is exactly why the derivation ladder exists. Read what is there, note what is not, and carry on down the ladder. Do not offer to author a `domain.md` as a side effect of an unrelated task; knowledge nobody chose to write down is knowledge you would be inventing.

Four of these deserve emphasis, because agents guess at them constantly.

**Thresholds.** Deriving the historical distribution feels like deriving the threshold. It is not. A column 3% null for two years tells you 3% is *normal*; it does not tell you 3% is *acceptable*, or that 4% should page someone. Anomaly detectors have the same limit — they learn what is **unusual**, never what is **unacceptable**. History is an *is*; a threshold is an *ought*, and no volume of the former yields the latter. Bring the distribution and ask for the line.

**Proven absence.** Every tool in the ladder demonstrates *presence*. None can demonstrate absence, because each is bounded by its own coverage, retention, and tagging completeness. So never write "there are no consumers" or "this contains no PII." Write "no evidence of X within scope Y" and name Y. The absence of a sensitivity tag is emphatically not evidence that a column is not sensitive.

**Accountable ownership.** Git tells you who last typed; it cannot tell you who is responsible. Ownership is an organizational assignment that must be *recorded* to be readable, and it decays silently as people move on. Treat a last-toucher as a lead, not an answer.

**Consequence tolerance.** Anything irreversible — dropping a relation, full-refreshing a model whose source no longer holds full history, deleting rows — is never derivable, however confident the surrounding analysis. Ask, every time.

---

## 5. How to ask

A bad question costs almost as much as a wrong assumption. Four properties of a good one:

- **Specific, not open-ended.** "Should nulls in `<column>` fail the build, or warn?" beats "how do you want me to handle nulls?"
- **Carries the derived context.** Do the lookup first, then ask. "`<column>` has been 2–4% null for 18 months, spiking to 11% last March. Where should the test threshold sit?" is answerable in seconds; the same question without the numbers pushes your work onto the reader.
- **Offers options and a recommendation.** Give the realistic choices, say which you would pick and why. A person correcting a recommendation is faster than a person authoring an answer.
- **Batched.** Collect the unknowns and ask once. Serial one-line questions across an hour are the most expensive way to gather the same information.

The four properties above assume you can *frame* the question — that you understand the situation well enough to state options. Sometimes you cannot, and forcing a tidy multiple-choice then is its own failure. Two cases warrant a plainer, more conversational ask:

- **A connection you expected is absent.** You reached for the warehouse, the query log, the DAG, or an MCP server to derive a fact and it is not there. Do not silently downgrade to a guess, and do not ask the human to run the query you would have run. Say which capability is missing and what it would have told you — "I can't reach the query log, so I can't see ad-hoc consumers of this model; from the DAG alone I see three, but that list is incomplete" — and ask how they want to proceed. Naming the gap is what lets them either connect the tool or accept the narrower answer knowingly.
- **The facts you gathered contradict each other, or the request itself is ambiguous.** When the evidence does not cohere into clean options, present what you found, say specifically what you cannot resolve, and stay in the exchange until it is settled. A short back-and-forth that lands on the real requirement beats one batched question answered against the reading you happened to favor.

State plainly what is blocked. If the task genuinely cannot proceed, say so — a blocked task with a clear question is a better outcome than a completed task built on a guess.

---

## 6. Proceeding on a stated assumption

Not every unknown deserves a question. When something is low-stakes and cheaply reversible — a column ordering, a description's wording, a name where the convention is ambiguous — pick the sensible default and move.

The requirement is that the assumption becomes **visible**, not that it becomes invisible:

> Assumed the new column belongs after `<column>` to keep the group together; trivial to move.

Put these in the summary and the PR body, not in a code comment. A reader scanning your summary can correct a stated assumption in one line. An unstated one gets discovered in production.

The line between "assume" and "ask": if being wrong means a cosmetic fix, assume. If being wrong means wrong numbers, a rebuild, or an irreversible loss, ask.

---

## 7. The agent's own errors

Everything above concerns gaps in what the tools can tell you. This section concerns a different and more insidious class: the moments an automated agent's internal model of the project is confidently wrong. These do not come from an empty result — they come from the agent generating something plausible that was never checked against the project at all. Three recur often enough to name.

**Inventing a column, table, or field that does not exist.** A model that references `customer_tier` when the column is `customer_segment`, a join onto a table whose name is close but not exact. The SQL is well-formed and reads correctly; it fails only at compile or execution, and if the invented name happens to collide with a real one, it does not fail at all — it returns the wrong thing. The discipline is cheap: before referencing a column or relation you have not already seen in this session, confirm it exists — `dbt compile` for refs, `information_schema.columns` or `describe` for column names. A name you reproduced from memory of "how these projects usually look" is a guess wearing the costume of a fact.

**Confusing a model's filename with its physical relation name.** `ref('<model>')` keys off the filename; the table it builds is named by the model's `alias` config when one is set. Construct a direct query from the filename and, on any aliased model, you query a relation that does not exist or a stale one under the old name. The authority is the compiled relation name, never the filename — see `dbt-environments` §4. The same trap covers custom schema macros and `database` overrides: the folder a model lives in does not dictate the schema it lands in.

**Believing a result came from where you intended.** In a development target, `ref()` may resolve to your build, silently fall back to production under deferral, or fail — and the three are indistinguishable from the numbers alone. An agent that reports "the change works, verified in dev" after unknowingly reading production has verified nothing. Before trusting any dev result, know which physical relations it actually read (`dbt-environments` §3). "It worked in dev" is a claim about *which table*, not just *what number*.

The common thread: an agent's fluency makes a fabricated name or a mis-resolved reference look exactly like a checked one. The defence is not more caution in tone — it is one cheap verification against the project before the plausible-looking artifact is trusted, and labelling anything not so verified as unverified.

## Completion checklist

- [ ] Unknowns enumerated explicitly before work started, not discovered midway
- [ ] Each unknown classified: derive, ask, or assume-and-state
- [ ] Derivation ladder worked from the cheapest layer, and the climb not narrated
- [ ] Grain and uniqueness measured against the warehouse, never taken from documentation
- [ ] Instrument coverage established before any negative result was treated as evidence
- [ ] Enabling metadata checked before relying on a metadata-dependent answer — freshness configured, exposure described, owner recorded, column tagged
- [ ] `bi.consumers` cross-checked against actual tool coverage; gaps named
- [ ] Nothing asked that a connected tool could have answered
- [ ] Nothing assumed from the nine ask-classes — especially thresholds, ownership, and irreversible actions
- [ ] No absence claimed as proven; scope stated instead
- [ ] Every column and relation referenced was confirmed to exist, not reproduced from memory of how such projects usually look
- [ ] Physical relation names taken from compiled SQL, not assumed equal to filenames
- [ ] Which environment each dev result actually read is known, not assumed
- [ ] Questions batched, specific, carrying derived context, with a recommendation
- [ ] Assumptions stated in the summary and PR body
- [ ] Unverified claims labelled as unverified, distinct from measured ones

## Common failure modes

1. **Treating a well-formed empty result as proof of absence.** The highest-consequence error here. A blind instrument and an unpopulated field both return success with nothing in it, in the same confident language as a real negative. Establish coverage, then check the field was ever filled in.
2. **Mistaking "the call succeeded" for "I know the answer."** A freshness status of "unconfigured", an exposure with an empty description, a null timestamp — these are questions wearing the costume of answers.
3. **Asking what the warehouse knows.** "What is the grain?" is one `count(distinct)` away. Asking it spends credibility you need for the questions that matter.
4. **Deriving a distribution and calling it a threshold.** Historical behavior is an *is*; acceptability is an *ought*. Anomaly detection has the identical limit — unusual is not unacceptable.
5. **Trusting documented grain over measured grain.** YAML records a belief from the day it was written. The table records what is true. They diverge constantly.
6. **Treating the DAG as the complete consumer list.** It cannot see ad-hoc SQL, notebooks, scheduled exports, or reverse ETL. The query log can.
7. **Confusing last-toucher with owner.** Git names who typed. It cannot name who is accountable, and the person who typed may have left.
8. **Inferring a schedule from tags.** Tags express intent. Only the orchestrator knows what runs, and when it last succeeded.
9. **Assuming a bug is a bug.** Duplicates, nulls and outliers are as often deliberate as broken. Classify with whoever owns the definition before "fixing" something a consumer depends on.
10. **Serial questioning.** Five one-line questions cost more than one batched list and make the work feel stalled.
11. **Narrating the derivation.** A list of every lookup buries the answer. Report the fact; mention method only where it bears on confidence.
12. **Silent assumptions on irreversible actions.** Full refresh, dropping a relation, deleting rows. Confidence in the analysis is not consent.
13. **Reporting a verified and an assumed fact in the same tone.** If the reader cannot tell which is which, the verified ones lose their value too.
14. **Referencing a column or table the agent never confirmed exists.** Fluency generates plausible names; only the project confirms real ones. A fabricated name either fails loudly or — worse — collides with a real one and returns the wrong thing. See §7.
15. **Querying a model by its filename when it has an alias.** The physical relation is named by `alias`, not the file. The filename is a hint; the compiled name is the fact.
