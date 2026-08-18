---
name: dbt-deriving-project-context
description: Use when installing this library into a project for the first time, when the contract or written context files are missing or stale, or when asked to work out how a project actually does things. Covers deriving conventions by measuring the repository rather than assuming — naming, but also bespoke machinery, config seeds, and the unnamed code-shape patterns an agent would otherwise hand-roll — reading git and pull-request history for intent, and appraising whether what a project does is better practice, an equally valid variant, or a genuine defect.
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/measure-project.sh *) Bash(${CLAUDE_SKILL_DIR}/measure-project.sh)
metadata:
  phase: orient
---

# Deriving project context

This skill runs once per project, and its output is what every other skill in the library reads. Everything downstream inherits its mistakes, which makes it the one place where guessing is most expensive and least visible: a wrong contract is *schema-valid*, reads as authoritative, and silently misinforms every task that follows.

Two failures define the work:

> **Transcription.** Writing down what you already believe about the project instead of what it does. Produces a contract that describes a project nobody has, and it validates clean.

> **Correction on sight.** Finding an unfamiliar pattern, concluding it is wrong, and normalizing it. Produces a diff nobody asked for and a reviewer who now distrusts the whole change.

The discipline against both: **measure, then appraise, then write.** Never write from memory, and never appraise before measuring — a pattern you have not counted is a pattern whose purpose you do not know.

## Scope, and what to route elsewhere

| If you are… | Use |
|---|---|
| Establishing what a project's conventions *are*, first time or refresh | **This skill** |
| Judging whether a practice is good, custom-but-better, or defective | **This skill** — the appraisal section |
| Learning the DAG shape, terminal nodes, orchestrator, BI consumers, dead models | `dbt-onboarding-to-a-project` |
| Writing or validating `conventions.yml` field-by-field | `dbt-project-conventions` |
| Missing one specific fact mid-task | `dbt-gathering-context` |

The boundary against `dbt-onboarding-to-a-project` is worth stating precisely, because both run early and both measure. Onboarding answers **"what is here and what depends on it"** — the graph. This skill answers **"how does this team work, and is that way good"** — the conventions and their quality. Run onboarding for the graph; run this to produce the written artifacts.

## What you are producing

Four artifacts, and the distinction between them is not cosmetic — it determines whether a fact rots.

| Artifact | Holds | Test for belonging |
|---|---|---|
| `conventions.yml` | Structured, measurable conventions | A tool can verify it, and a skill needs it to make a decision |
| `context.mechanisms` | Bespoke machinery of this project | A skill's sensible generic default would be **wrong** here |
| `context.domain_notes` | Business meaning, canonical definitions, traps, closed decisions | **No** tool can compute it |
| `context.references` | Pointers to documents living elsewhere | The document has an owner outside this repository |

**Anything a connected tool can compute belongs in none of them.** A copy of a derivable fact goes stale while the real answer moves on, and the copy is what gets believed. Row counts, column lists, lineage, run history and schedules are all derivable; do not record them.

### What counts as a convention

Naming is the most *visible* convention, so the steps below start there — but it is the smallest part. A convention is **any repeated, load-bearing choice an agent would otherwise guess wrong or hand-roll from scratch.** That includes, and the later steps hunt for, all of:

- **Naming and structure** — prefixes, separators, key column, timestamp suffix, layer→materialization mapping. Section 1.
- **Bespoke machinery** — macros, overridden dbt built-ins, custom generic tests, packages that resolve something a skill would otherwise recommend building, and seeds used as lookup or config tables rather than as data. Section 2.
- **Code-shape patterns** — the house way of writing a thing, repeated across models but often *not* extracted into a macro: a mandated CTE order, a specific dedup idiom, an incremental-boundary pattern, the shape a union of sources always takes, a standard way late-arriving data is handled. Invisible to a macro sweep because there is no named artifact to grep for. Section 2b.
- **Mechanical procedures** — a blue/green swap, a backfill ritual, a two-phase deploy, a reconciliation step with a required order. Often described only in a runbook, a CI workflow, or a PR body. Sections 2 and 3.

The failure this guards against is narrowing "convention" to "naming," recording the prefixes, and leaving an agent to reinvent the project's dedup idiom or hand-roll what a mandated macro already does. When in doubt whether something is a convention, apply one test: **would an agent, not knowing this, produce code the team would reject in review?** If yes, it is a convention, wherever it lives.

## 1. Measure the taxonomy

Count everything before naming anything. A convention is a majority practice, not a preference — and the exceptions are as informative as the rule.

> **Fast start (optional).** `${CLAUDE_SKILL_DIR}/measure-project.sh` runs the mechanical counts of steps 1–2 in one read-only pass — prefix distribution, separator, key and timestamp conventions, layer materializations, macro usage, seed refs, and the most-changed models. The skill pre-approves that exact command in `allowed-tools`, so it runs without a prompt; if your harness does not honor `allowed-tools`, `bash measure-project.sh` from the skill's folder is equivalent. It **dumps counts; it does not interpret them.** Every judgment the rest of this skill teaches — which prefix is live versus retired, whether a zero-usage macro is a built-in override, whether a seed is data or config, which findings are variants versus defects — is still yours to make from reading. Use it to skip the typing, not the thinking. The inline commands below remain the interpret-as-you-go path when you would rather measure one thing, read it, then measure the next.

```bash
# prefix distribution across every model
find models -name '*.sql' | sed 's|.*/||;s|\.sql$||' \
  | grep -oE '^[a-z]+_' | sort | uniq -c | sort -rn

find models -name '*.sql' | wc -l
find models -name '*.py'  | wc -l   # Python models have different rules; count them separately
```

Then the separator, the key column, and the timestamp convention — each as a measured proportion, never as a general sense of what the project looks like:

```bash
find models -name '*.sql' | sed 's|.*/||' | grep -c '__'
grep -rh 'as unique_id\|as surrogate_key' models --include='*.sql' | wc -l
grep -rho '[a-z_]*_utc\b' models --include='*.sql' | sort -u | wc -l
```

Read layer materializations from `dbt_project.yml`, not from a sample of files — the project file is the authority and a sampled file may carry an override.

**Write the counts into the contract as comments.** A contract that records *how* each value was measured can be re-verified by the next reader; one that states bare values must be trusted or re-derived from nothing. This is the single highest-value habit in the file.

### Compute the match rate, and treat the gap as data

```bash
# how many models match the taxonomy you just wrote down?
```

A taxonomy matching 95% of models has 5% that are either grandfathered or defective — and **which one is a question, not an inference.** Route the age-dating procedure to `dbt-onboarding-to-a-project`; record the outcome under `deviations` with the reason. A `deviations` entry means *we know, it is intentional, stop reporting it*, which is what stops every future session re-litigating the same handful of files.

## 2. Find the bespoke machinery

The highest-value and most-missed step, because **some of what matters here is invisible to a reader of the models.** An overridden dbt built-in appears in no model file and grepping for its usage returns nothing, since dbt calls it automatically.

```bash
ls macros/

# load-bearing test: how many models call each one?
for m in $(ls macros/*.sql | xargs -n1 basename | sed 's/\.sql//'); do
  printf '%-34s %s\n' "$m" "$(grep -rl "$m" models --include='*.sql' | wc -l)"
done | sort -k2 -rn
```

Read three groups, in this order:

1. **High-usage macros.** A macro in dozens of models is the sanctioned way to do something. A skill will suggest a generic alternative and be wrong.
2. **Zero-usage macros — do not dismiss these.** Two kinds hide here: dbt built-in overrides (`generate_schema_name`, `generate_alias_name`, `generate_database_name`), which are called automatically and change every model's real target relation; and operations invoked by CI or by hand rather than from SQL. Both are load-bearing and both read as dead code.
3. **Environment-detection macros.** Open and read the body. If the condition is compound, record `detection.strategy: macro` with the *call* in `expression` — never an inline copy of the logic. A compound condition copied by hand is a condition that will be copied wrongly, and a CI build misclassified as production is the expensive direction of that error.

Then sweep the surrounding machinery, all of which changes what is worth doing by hand:

```bash
cat packages.yml          # an installed package resolves a skill's conditional recommendation
ls tests/generic/         # custom generic tests exist because a built-in was unsuitable -- find out why
ls .sqlfluff* 2>/dev/null # a committed config outranks any general style preference
ls .github/workflows/     # read these; label conventions and generated artifacts hide here
```

**Read the CI workflows properly rather than listing them.** They routinely reveal that an artifact is machine-generated — exposures, docs, a schema diff — which inverts a skill's advice from *write this* to *do not hand-edit this*. They also carry label conventions that trigger automated handoffs, and nobody discovers those by reading models.

### Seeds that are configuration, not data

Not every seed is a static dataset. A seed used as a **lookup or config table** — status-code mappings, a channel→category crosswalk, feature flags, a list of models to exclude from something — is a convention: it is the sanctioned place a value is set, and an agent that hardcodes the value inline instead of reading the seed has broken the pattern. Distinguish the two by what references them.

```bash
ls seeds/ 2>/dev/null
# a seed ref()'d from model logic is a config/lookup table; one only tested is data
for s in $(ls seeds/*.csv 2>/dev/null | xargs -n1 basename | sed 's/\.csv//'); do
  printf '%-30s refs:%s\n' "$s" "$(grep -rl "ref('$s')\|ref(\"$s\")" models --include='*.sql' | wc -l)"
done
```

Record a config seed under `mechanisms` with what it governs — a skill's generic instinct is to inline the value, and here that is wrong.

## 2b. Find the code-shape patterns

The steps above hunt for *named* things — a macro, a seed, a workflow — that grep can locate by name. The higher-miss category is a pattern with **no name**: a way of writing SQL repeated by hand across many models because the team never extracted it into a macro. It is a convention every bit as binding as a prefix, and it is invisible to every command run so far, because there is nothing to `ls`.

These do not yield to a single command; they yield to reading a handful of representative models across layers and noticing what recurs. Read three or four models in each layer and look for a shape that appears in all of them:

- A **CTE structure** the models share — an import block, a logical block, a single `final` — applied so uniformly it is clearly a rule.
- A **dedup idiom**: always `qualify row_number() over (...)`, or always a `group by` on the grain, or a specific `distinct` discipline. Pick the wrong one and the review comment writes itself.
- An **incremental-boundary pattern** — the same `where` shape against the same timestamp, the same lookback expression — repeated rather than macro'd.
- A **union-of-sources shape**: how the project conforms several sources into one model, in what order columns are aligned, where the source label is stamped.
- A **type-casting or null-handling discipline** applied at a consistent layer.

```bash
# a cheap starting signal: nearly-identical lines recurring across models often
# mark a hand-copied idiom that was never extracted
grep -rhoE '(qualify|row_number\(\) over|coalesce\(|group by all)' models --include='*.sql' \
  | sort | uniq -c | sort -rn | head
```

Treat the grep as a hint, never the finding — the real evidence is the pattern you *read*, stated in prose. Record each recurring shape in `mechanisms.md` as "the house way to do X," so the next agent reproduces it instead of inventing a second way. A codebase with two rival dedup idioms is one an agent helped fork; the convention exists precisely to prevent that.

## 3. Read history for intent

Git and pull-request history answer the one question the current state cannot: **why**. Present-state measurement tells you a pattern exists; history tells you whether it was chosen.

```bash
# most-changed models -- where the risk and the institutional knowledge sit
git log --format= --name-only --since='1 year ago' -- 'models/**/*.sql' \
  | sort | uniq -c | sort -rn | head -20

# what kinds of work actually happen here
gh pr list --state merged --limit 100 --json title | \
  python3 -c "import json,sys,collections,re; print(collections.Counter(re.match(r'^(\w+)',p['title']).group(1).lower() for p in json.load(sys.stdin) if re.match(r'^(\w+)',p['title'])).most_common())"

# why does this model look like this?
gh pr list --state merged --search '<model_name>' --json number,title,body --limit 5
```

Read the PR body before judging any pattern. It is frequently the only record of a decision, and a pattern that looks wrong is often a documented workaround for something you have not hit yet. **A fix that describes a real incident belongs in `domain.md` as a trap** — those entries are the most valuable content in the file, because each one is a mistake already paid for.

### Separate the current convention from the one it replaced

A whole-repo count answers "what is most common," which is not the same question as "what does this team do *now*." When a convention has shifted — a prefix retired, a separator changed, a test policy tightened — the majority can be the **old** way simply because the old models outnumber the new ones, and a naive count then encodes the abandoned convention as the standard. This is the failure that makes an agent write new models in a style the team stopped using two years ago.

The signal is recency, not frequency. Date each convention's practice by *when models using it were added*, and compare the recent window against the whole history:

```bash
# For a contested prefix, when were its models added? Recent additions = live; all old = legacy.
for p in <prefix_a> <prefix_b>; do
  echo "== $p (add dates, newest first)"
  for f in $(find models -name "${p}*.sql"); do
    git log --diff-filter=A --format='%ad' --date=short -1 -- "$f" 2>/dev/null
  done | sort -r | { head -3; echo '  ...'; }
done

# The direct question: in the last N merged PRs, which prefixes appear in ADDED model files?
gh pr list --state merged --limit 40 --json files --jq \
  '.[].files[].path | select(test("models/.*\\.sql$"))' 2>/dev/null \
  | sed 's|.*/||;s|_.*||' | sort | uniq -c | sort -rn
```

Read the two together. A prefix that is 26% of the repo but appears in **zero** recent additions is a retired convention: record the *recent* practice as the standard and the older one under `deviations` (or `banned_prefixes`) as legacy — not the other way around because it happens to have more files. A prefix that is rare in the repo but present in every recent PR is an *emerging* convention: it is what the team is moving to, and new work should follow it even though the count is small.

Three cautions keep this honest. **A quiet quarter is not a retirement** — a layer that is simply complete gets no new models without being deprecated; corroborate a "no recent additions" signal against the PR bodies or a person before recording a prefix as banned. **Recency cuts both ways** — a burst of a new pattern across three PRs by one author may be an experiment, not a ratified standard; consistency across authors and time is what distinguishes a convention from one person's preference. And **the shift itself is the most valuable thing you can record**: when you find a clear old→new transition, the old form belongs in `banned_prefixes` *with the date and the PR that turned the corner*, so every future session stops proposing the retired style and stops re-flagging the grandfathered files.

## 4. Appraise — better practice, valid variant, or defect

Measurement says what a project does. This step says whether that is *good*, and it is where the temptation to be useful does the most damage. Every finding lands in exactly one of three verdicts:

| Verdict | Meaning | Action |
|---|---|---|
| **Best practice** | Matches the industry norm the skills encode | Record it. Nothing to do. |
| **Deliberate variant** | Departs from the norm and is **demonstrably better or equally valid here** | Record it in `mechanisms.md` *with its reason*, and follow it. Adopt it as the local standard. |
| **Defect** | Departs from the norm with no reason that survives inspection | **Report it. Do not fix it as a side effect of an unrelated task.** |

Prioritise best practice by default. Prefer the local variant when it demonstrably works better — a team that solved a problem you have not hit is more informed about their project than a general rule is, and overriding a working local solution with a textbook one is how a library makes a project worse. Innovation is welcome; the burden it carries is that it must be *working*.

**The test that separates a variant from a defect is a mechanism, not an opinion.** Ask, in order:

1. **Is there a stated reason?** In a comment, a docstring, the PR that introduced it, or a macro's own documentation. A reason that survives reading is usually a real constraint.
2. **Is it consistent?** A pattern applied uniformly across dozens of models is a decision. One applied in four places out of two hundred is drift.
3. **Does it solve a problem the generic advice does not?** Cost, a warehouse limitation, a consumer contract, an incident that already happened.
4. **Is it load-bearing?** Would changing it break a BI reference, a reconciliation, or a downstream contract?

Two or more yes answers, treat it as a deliberate variant and follow it. All four no, and it is a candidate defect — reported, not corrected.

Worked examples of the distinction, all three of which appear in real projects:

- A project with custom recency-scoped uniqueness tests instead of the built-in `unique`. Generic advice says use `unique`. **Variant, and better here:** full-history uniqueness on a large incremental table is too expensive to run per build, so the built-in advice is unaffordable rather than correct.
- A project mandating a macro for dev data limits rather than `--vars`. **Variant, and better:** a var-based limit that someone forgets to remove silently truncates a production build, and nothing in the run reports it.
- A project casting types in a mart because staging was skipped. No reason stated, applied in six models out of hundreds, solves nothing. **Defect** — reported, left alone.

**A finding is not licence to change anything.** Say what you found, which verdict it earned, and what you would do about it. Then wait to be asked. An agent that arrives and normalizes the old layer produces a hundred-file diff, an unsized blast radius, and a reviewer who distrusts everything else in the change.

## 5. Write it down, honestly

Populate the four artifacts. Two habits carry most of the value:

**Leave a field unset rather than guessing it.** An absent field makes a skill withhold version-gated advice or ask a human. A *wrong* field makes it confidently recommend something unavailable, or classify a production build as a sandbox. Unset is a safe state; wrong is not. Say in a comment why it is unset and what would settle it.

The fields that most often get guessed, and should not be:

- **dbt version.** A CLI version is not the dbt-core version that skills gate advice on.
- **Query-log retention.** A vendor's documented default is not a verified fact about a specific account.
- **Sensitivity classification.** If the project records none, record none. **A missing classification never means "safe"** — it means unclassified, and the two are opposite in consequence.

**Mark inferred prose as inferred.** In `domain.md`, anything derived from names rather than confirmed by a person gets an explicit marker (`NEEDS CONFIRMATION`). A leading source system inferred from a name that turns out to be secondary poisons every downstream decision, and unmarked prose is indistinguishable from verified fact.

Leave the canonical-metric section **empty rather than invented** if nobody has confirmed the definitions. SQL shows what is computed, never which of two rival definitions is canonical. An empty section is a visible question; a plausible guess is an invisible error, and it will be quoted back as authority.

**Do not cite `AGENTS.md` rules by number, and do not treat `AGENTS.md` as this project's rulebook.** `AGENTS.md` is *this library's* generic agent guide — it ships with the skills and its universal rules are the same in every project that installs them. It is not a project-specific document, and its rule numbers are not stable: an adopter reorders or inserts one and every numeric citation silently points at the wrong rule while still reading as authoritative. The failure looks like attributing "dev detection keys off `target.database`" to a numbered universal rule that is actually about something else — the fact was *measured*, but the citation makes it look sourced from a rule, and the next agent follows the number to the wrong place. When you record a project convention, attribute it to **what you measured** ("observed in every `int_` model sampled"). When a point genuinely is one of the universal rules, cite its **content** ("the `>=`-not-`>` incremental-boundary rule"), never its number.

### Scaffold per-skill supporting files where a pattern earns one

The four artifacts hold what *every* skill reads. But some conventions you measured are specific enough to one skill that they belong beside it, not in the shared contract — a project-specific example a skill can show instead of its generic one, a note recording the house variant of what that skill teaches. The library ships skills with generic supporting files; a downloading team makes them *theirs* by adding project-specific ones alongside.

Two concrete cases, both drawn from what the steps above already measured:

- **A code-shape pattern (§2b) that a build skill teaches generically.** If you found the project's mandated CTE structure or dedup idiom, a short `examples.md` in the relevant authoring skill's folder — showing that idiom on a real model from *this* repo — is worth more than the skill's neutral illustration. The skill stays generic and portable; the example makes it local.
- **A deliberate variant (§4) that departs from what a skill recommends.** Record the *rule* in `mechanisms.md` as always, but where the variant is intricate enough that an agent needs to see it done, a project note beside the skill that owns the topic keeps the how next to the where.

Three guardrails, because this is where "helpful" turns into invention:

1. **Only scaffold from a measured, load-bearing pattern.** The same test as everything else in this skill: would an agent not knowing it write code the team rejects? A file created because it *might* help is context the next reader must wade through for nothing.
2. **Point, do not duplicate.** A project example belongs in one place. If the pattern is already stated in `mechanisms.md`, the per-skill file references it rather than restating it — two copies of a rule diverge, and then which is current becomes a question.
3. **Never edit the shipped SKILL.md bodies to wire these in.** A project file is additive and lives in the skill's folder; the skill's own prose stays as published so a library update does not collide with your local edits. If a skill genuinely cannot find a project file it should, that is a gap to report upstream, not to patch by rewriting the skill.

When unsure whether a pattern earns its own file, it does not yet — record it in the shared artifacts and let a real task prove the need.

## 6. Validate, then check the facts separately

The schema lives at `schema/conventions.schema.json` in the library. Where that resolves depends on how the library was installed, so **locate it before validating** rather than assuming the project-relative path:

```bash
# Find the schema wherever the library landed: the project root (repo clone),
# or the plugin cache (marketplace install copies the whole repo there).
SCHEMA=$(ls schema/conventions.schema.json 2>/dev/null \
  || ls "$HOME"/.claude/plugins/cache/*/*/schema/conventions.schema.json 2>/dev/null \
  | head -1)
if [ -n "$SCHEMA" ]; then
  python3 -c "import json,yaml,jsonschema; jsonschema.validate(yaml.safe_load(open('conventions.yml')), json.load(open('$SCHEMA')))" \
    && echo "valid against $SCHEMA"
else
  echo "schema not found on disk — record the contract as NOT schema-validated"
fi
```

**If the schema is genuinely not found, do not fabricate a validation pass** — say it was not found, and note in the contract's header that it was not schema-validated. Never validate against a schema from an *unrelated* repo and report it as this project's validation: a real user will not have that repo, so the claim is false for them.

**Schema-valid is not factually true, and the gap is where the damage lives.** The validator confirms shape: that a field exists and has a permitted value. It cannot know your dev-detection expression omits a clause, or that your prefix list describes a taxonomy the project abandoned. Re-derive two or three of the load-bearing values independently and confirm they match what you wrote.

If the schema rejects a value that is genuinely correct for this project, that is a finding about the library, not a reason to weaken the contract. Report it.

## Completion checklist

- [ ] Prefix taxonomy measured with counts, and the match rate computed
- [ ] Exceptions dated and recorded under `deviations` with reasons, not silently normalized
- [ ] Layer materializations read from `dbt_project.yml`
- [ ] Every macro's usage counted; zero-usage ones opened and classified, not dismissed
- [ ] dbt built-in overrides identified and their effect on the target relation recorded
- [ ] Environment detection recorded as a macro call where the condition is compound
- [ ] `packages.yml`, `tests/generic/`, lint config and CI workflows read
- [ ] Seeds classified as data vs config/lookup; config seeds recorded so their values are not inlined
- [ ] Representative models read across layers for unnamed code-shape patterns — CTE structure, dedup idiom, incremental boundary, union shape — and each recurring one recorded as "the house way"
- [ ] Generated artifacts identified and marked do-not-hand-edit
- [ ] Git and PR history read for intent on at least the most-changed models
- [ ] Contested conventions dated by recent additions, so a retired convention is not recorded as current because old models outnumber new ones
- [ ] Every notable finding assigned one of the three verdicts
- [ ] Unverifiable fields left unset with a comment, not guessed
- [ ] Per-skill project files scaffolded only where a measured, load-bearing pattern earns one — pointing to `mechanisms.md`, not duplicating it, and never by editing a shipped SKILL.md
- [ ] Inferred prose marked `NEEDS CONFIRMATION`
- [ ] No `AGENTS.md` rule cited by number for a project fact; conventions attributed to what was measured
- [ ] Contract validated against the schema, or its absence recorded honestly (never a fabricated pass, never a sibling repo's schema)
- [ ] Two or more load-bearing values re-derived independently to confirm
- [ ] Findings reported; nothing normalized without being asked

## Common failure modes

| Failure | Why it happens | Instead |
|---|---|---|
| Narrowing "convention" to naming | Prefixes are the most visible pattern and the first thing measured | A convention is any repeated load-bearing choice — code shapes, config seeds, mechanical procedures. Ask: would an agent not knowing this write code the team rejects? |
| Missing an unnamed code pattern | Grep finds named artifacts; a hand-copied idiom has no name to find | Read representative models across layers. The house dedup idiom or CTE structure shows up in the reading, not the grep. |
| Inlining a value that lives in a config seed | The seed reads as static data | Check what ref()s it. A seed referenced from model logic is the sanctioned source of that value. |
| Transcribing what you already believed | You know the project, or an injected context blob told you | Measure. A remembered fact and a measured one look identical on the page and only one is checkable. |
| Copying a detection condition inline | The macro body is right there and looks simple | Record the call. The clause you drop is the one that misclassifies CI as production. |
| Dismissing zero-usage macros as dead | Grep found nothing | dbt built-in overrides are called automatically and are invisible to grep. Open them. |
| Listing CI workflows without reading them | The filenames look self-explanatory | They reveal generated artifacts and label conventions that invert a skill's advice. |
| "Fixing" a pattern on sight | It looks wrong and fixing feels useful | Apply the four-question test, assign a verdict, report. Correction needs a request. |
| Overriding a working local practice with a textbook one | The library says otherwise | A demonstrably better local variant wins. The burden is that it works, not that it is orthodox. |
| Guessing a version or retention window | A default is documented somewhere | Leave it unset with a comment. Unset withholds advice; wrong misdirects it. |
| Inventing metric definitions | The section looked incomplete | Leave it empty. An invented definition gets quoted as authority. |
| Trusting schema validation as correctness | It passed | Re-derive load-bearing values. Valid shape, wrong facts is the normal failure. |
| Reporting a validation pass with no schema on disk | The skill says to validate, so a pass is expected | The schema ships with the library but not at a fixed project-relative path. Locate it first; if it is genuinely not found, record the contract as not schema-validated — never fabricate a pass or borrow an unrelated repo's schema. |
| Citing an `AGENTS.md` rule by number for a project fact | The rules are numbered and look like stable references | `AGENTS.md` is the library's generic guide, identical across projects. Attribute a project fact to what you measured; the fabricated number sends the next agent to the wrong rule. |
| Recording the majority style when the convention has shifted | A whole-repo count sees old models outnumbering new ones | Date the practice by recent additions. What the last N PRs add is the live convention; the abundant old form may be retired. |
| Recording derivable facts | They were easy to collect | Row counts and lineage belong to the tools. A stale copy outlives the real answer. |
| Scaffolding per-skill files that help nobody | Adding a file feels productive | Only a measured, load-bearing pattern earns one. An unearned file is context the next reader wades through for nothing. When unsure, don't — record it in the shared artifacts and let a task prove the need. |
