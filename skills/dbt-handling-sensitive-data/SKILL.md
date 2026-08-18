---
name: dbt-handling-sensitive-data
description: Use when selecting a column that may be personal or regulated data, adding a column to a mart that originates in a sensitive source, a masked or tagged column appears in a new model, setting grants on a model or schema, handling a data-subject deletion or retention request, or deciding where sensitive values may appear in tests, seeds, PRs and summaries. Covers classification propagation, why masking does not follow a select, and the deletion conflict with irreplaceable history.
metadata:
  phase: reference
---

# Handling sensitive data

The failure this skill prevents is invisible to every other gate in this library:

> An agent selects a governed column into a new model, that model materializes into a schema with different grants, and the result is an unmasked, ungoverned copy of regulated data. It compiles. Every test passes. The diff looks like a normal column addition. Nothing anywhere reports a problem.

That is the whole shape of the risk. Sensitive data does not break when it leaks — it works perfectly. There is no failing build to investigate, no null column, no row-count drift. The only mechanism that catches it is a person deciding to check before writing the `select`.

So the discipline is front-loaded. **Establish a column's classification before you select it, and carry that classification into every relation the column reaches.** A downstream copy that does not carry the classification has silently escaped governance, and it will keep working in that state indefinitely.

Two boundaries, stated once and honoured throughout:

- **This skill is not legal advice.** It marks which decisions are legal or policy decisions and routes them to a human. It does not make them, and neither should you.
- **Warehouse mechanics differ fundamentally.** Masking, tagging, classification and row-level policies are not a common feature set with dialect variations — some engines have them, some have partial analogues, some have nothing. Everything mechanism-specific here is gated on `project.warehouse`.

## Sub-documents

This file holds the discipline that is true everywhere. The detail lives alongside it, and each of these is worth reading in full before acting in its area:

- [engine-mechanisms.md](engine-mechanisms.md) — what masking, row filtering, tagging and classification actually are per engine, with real names, real limits, which engines have **nothing**, and the version dependencies that must be verified rather than asserted. Read before writing any policy statement.
- [grants-and-access.md](grants-and-access.md) — the `grants` config, the `+` prefix, why removing the config does not revoke, `copy_grants`, and how a full refresh silently drops externally-applied grants.
- [leak-surfaces.md](leak-surfaces.md) — the copies the warehouse's governance features never see: production clones in dev, `store_failures`, seeds, the docs catalog, logs and previews, PR bodies, and warehouse retention artifacts.
- [derived-values.md](derived-values.md) — hashing, pseudonymisation, tokenisation and anonymisation, and why a hash of a low-cardinality identifier is a lookup away from plaintext. Read before offering a hash as a protection.
- [deletion-and-retention.md](deletion-and-retention.md) — enumerating every relation holding a subject's rows, the irreducible conflict with irreplaceable history, retention, and why minimisation is the only control with no failure mode.
- [compliance-vocabulary.md](compliance-vocabulary.md) — the terms an engineer must recognise without adjudicating: PII, PHI, PCI, erasure, residency. Route-to-a-human, explicitly.

## Read the contract first

```bash
cat conventions.yml 2>/dev/null || cat .dbt-agent/conventions.yml 2>/dev/null || echo "NO CONTRACT"
```

| Field | What it decides here |
|---|---|
| `sensitivity.meta_key` / `tag_namespace` | Where this project records a column's classification. **Absent, you cannot distinguish an unclassified column from a project that classifies nothing** — so you must ask, and you must not read a missing tag as "safe" |
| `sensitivity.levels` | The permitted values, in increasing order. The ordering is what makes a ceiling comparison meaningful |
| `sensitivity.required_on_new_columns_in` | Layers where a new column must carry a classification. Usually the source-facing layer, because that is the boundary where the obligation is still cheap to resolve |
| `sensitivity.warehouse_policy_is_authoritative` | Which record wins on a conflict. When `true`, a YAML claim of protection the warehouse does not enforce is the dangerous direction of drift — it reads as a guarantee and is not one |
| `layers[].max_classification` | The ceiling. A column classified above a layer's maximum appearing in that layer is a defect **regardless of whether any grant or masking policy is currently correct** — the one check here that does not depend on warehouse introspection |
| `project.warehouse` | Whether column-level masking, tag-based policy propagation, row-level policies and classification exist at all, and what they are called |
| `environments.dev` / `environments.prod` | Whether a build lands in a broadly-readable shared schema or a personal one — the grant surface differs, and dev is where unmasked copies usually appear |
| `environments.dev.role` / `prod.role` | Which role dbt builds as, which is the role whose visibility of a masked column you are actually observing |
| `layers[].materialization` | Whether the model persists data at all. A view generally inherits the source's policy evaluation; a table is a physical copy that does not |
| `layers[].terminal` | Whether the relation is an endpoint consumers read directly, which raises the cost of an over-broad grant |
| `bi.consumers[].repo_path` | Where an exposed sensitive column becomes visible to people outside the data team |
| `testing` | Which tests will run on the column — relevant because a failing test can write sample values to a persisted failures table |

**Without `project.warehouse`:** determine the adapter before offering any masking, tagging or row-policy guidance:

```bash
grep -rn "type:" profiles.yml ~/.dbt/profiles.yml 2>/dev/null
grep -rn "dbt-" requirements.txt pyproject.toml packages.yml 2>/dev/null
dbt debug 2>&1 | grep -i adapter
```

If it still cannot be established, **withhold the mechanism-specific guidance entirely and say that you are withholding it.** Ask which engine the project runs on. *That is generic behavior, not your project's rule.* Do not offer a plausible dialect: a masking or row-policy statement written for the wrong engine either errors, or is accepted and does nothing, and the second outcome ends the conversation with an engineer believing regulated data is protected when it is in plaintext. Of everything in this library, this is the worst place to guess.

The engine-independent sections — 1, 4, 5, 6, 7, 8 and 9 — hold everywhere and remain available, as do [leak-surfaces.md](leak-surfaces.md), [derived-values.md](derived-values.md), [deletion-and-retention.md](deletion-and-retention.md) and [compliance-vocabulary.md](compliance-vocabulary.md). Section 3's ordering also holds, minus the create-time option, which needs the engine.

**Without `environments`:** you cannot tell whether your target schema is personal or shared, so treat it as shared and readable by others until someone confirms otherwise. Assuming isolation you have not verified is how a dev build becomes the leak.

**Without `bi.consumers`:** do not claim a sensitive column is not exposed to end users. State that BI exposure was not verified, per `dbt-gathering-context`.

---

## 1. Classification propagation is the core discipline

A column carrying a sensitivity classification is not just data — it is data with obligations attached. Those obligations live in three separate places, and nothing keeps them in step automatically:

| Where the obligation lives | What it governs | What propagates it |
|---|---|---|
| The warehouse policy | Who sees real values | The engine, only under specific conditions (section 2) |
| The dbt YAML record | What humans and agents believe about the column | You, by hand |
| The grant on the relation | Who can read the relation at all | The `grants` config or the schema's default (section 8) |

**A `select` propagates the data and none of the three.** That asymmetry is the entire problem. The copy is real immediately; the governance is real only if someone reproduces it deliberately.

### Before selecting any column, resolve its classification

Do this before writing SQL, not after building. Four checks, in order of cost:

```bash
# 1. Does the project already record a classification for this column, anywhere?
grep -rn "<column>" models/ --include=*.yml

# 2. Does the project record classifications at all? If no column anywhere
#    has a sensitivity entry, the YAML is not an instrument you can trust.
grep -rn -i "pii\|sensitiv\|classification\|confidential" models/ --include=*.yml | head -20

# 3. Where does the column originate? Classification belongs at the source.
grep -rn "<column>" models/staging/ --include=*.sql
```

Then, fourth, ask the warehouse what policy is actually attached — the query differs per engine, see section 2. The YAML records a belief; the warehouse records the truth, and they drift (section 5).

### What to do with the answer

| Finding | Action |
|---|---|
| Column is classified, and the downstream relation can carry the same protection | Propagate the classification and the policy together, in the same change |
| Column is classified, and the downstream relation **cannot** carry the protection | **Stop.** Do not select it. Report the conflict and ask. This is a policy decision. |
| Column is unclassified but the values are plainly personal or regulated | Treat as classified, say you are doing so, and ask for the classification to be recorded |
| Column is unclassified and you cannot tell | **Ask.** Do not select an unknown into a mart to find out what it looks like |
| Genuinely non-sensitive | Proceed normally |

There is a fifth option agents reach for and should not: selecting the column now and adding governance in a follow-up. That leaves a plaintext copy in production between the two changes, and the follow-up is the change most likely never to happen.

**Prefer not carrying the column at all.** Most requests that appear to need a sensitive column need something derived from it: a keyed hash for joining, a boolean for a segment, a domain rather than an address, a bucket rather than a birth date. A derived value that cannot reconstruct the original inherits no obligation — but note the size of that "cannot", because **a plain hash of a low-cardinality or structured identifier is reversible by brute force** and inherits the full obligation. [derived-values.md](derived-values.md) has the distinctions that make this a real control rather than a comforting one. Ask what the column is *for* before propagating it; the cheapest way to secure a column is not to copy it, and that is the only control here with no failure mode.

> When adding a column that comes from a sensitive source, follow `dbt-adding-columns` for propagation mechanics — and add one step to its sequence: resolve classification at the origin *before* step 1. Column-propagation work is exactly where classification gets dropped, because the layer-by-layer edit reads as mechanical and the YAML entry gets copied without its `meta`.

---

## 2. Why masking does not follow the data — gated on `project.warehouse`

The mechanism, stated generically because the principle holds even where the feature does not exist:

**A masking policy is a property of a column in a specific relation, not a property of the values.** When the engine returns rows from a relation whose column has a policy attached, it evaluates the policy against the querying role and returns a masked value. When you `create table <new> as select <column> from <governed>`, the engine evaluates the policy *once*, as the role dbt is building with — and writes whatever that role sees into a new column that has no policy on it.

Two outcomes, both bad, and which one you get depends on the build role's privileges:

- **The build role sees real values.** Almost always the case, since a role that could not read the data could not build the model. The new column now holds plaintext, permanently, and is governed only by whatever grants the new schema happens to have.
- **The build role sees masked values.** The new column holds masked strings as though they were data. Joins on it fail to match, aggregates count wrong, and the model is quietly incorrect rather than quietly leaky.

Neither errors. Neither fails a test. The first is a data-protection incident; the second is a correctness bug that looks like a data problem.

Where the engine supports **tag-based policy propagation**, a policy attached to a tag can follow the tagged column into derived relations — but that is a specific configured feature with real conditions, not default behavior, and the conditions are what people get wrong. Verify it applies to *your* relation, in *your* schema, before relying on it. "The platform handles this" is a claim to check, not a premise.

### View versus table versus materialized view

The materialisation decides *when and against whom* a policy is evaluated, and the three cases are genuinely different rather than variations on one theme:

| Materialisation | How policy evaluation works | The consequence |
|---|---|---|
| **View** | Resolved at query time against the underlying relation. Policy evaluation generally happens against the source, as the **querying** role | No copy exists, so there is nothing to protect and nothing to drift. Usually the safest option, and the strongest one available on engines with no create-time policy attachment |
| **Table** | A physical copy, written once as the **build** role. The new column has no policy on it | The failure at the top of this skill. Plaintext or masked-as-data, permanently, governed only by the new schema's grants |
| **Materialized view / equivalent** | Precomputed, and **frequently incompatible with masking outright** | Do not assume this is "a view with better performance" for governance purposes. On some engines a masking policy cannot be set on it, cannot be set on a base column once it exists, and blocks creating one. Verify before proposing it |

A view is also not a free win: whether a view enforces the *querying* role's policies or the view **owner's** differs by engine and, on at least one engine, by a per-view option that is not the default. And a non-secure view can leak the rows it filters out through predicate pushdown. Both are in [engine-mechanisms.md](engine-mechanisms.md).

### Per engine

The named mechanisms, their real limits, the version dependencies, and the engines that have **nothing** are in [engine-mechanisms.md](engine-mechanisms.md). Read it before writing a policy statement. The summary of where the trap lies:

| `project.warehouse` | What exists | The mistake this engine invites |
|---|---|---|
| `snowflake` | Column-level masking policies, tag-based policy attachment, row access policies, classification, projection and aggregation policies, secure views | Assuming a tag-attached policy follows a column into a `create table as select`. Propagation is a per-tag property that must be configured for **data movement**, and system tags from classification do not propagate at all. |
| `bigquery` | Policy tags via a taxonomy, column-level access control, row-level access policies, dynamic data masking via data policies, authorized views | Copying a policy-tagged column into a new table with no tag on the destination. A query with a destination table never propagates tags; only a same-region table copy job does. And `policy_tags` in dbt does nothing without column-level `persist_docs`. |
| `databricks` | Unity Catalog column masks and row filters, table and column tags | Assuming a new table inherits a mask — it does not. But note this engine goes the *other* way on replace: `REPLACE TABLE` retains masks and filters, and older runtimes fail **securely**, returning no data rather than erroring. |
| `redshift` | Dynamic data masking and row-level security where the version supports them, column-level `GRANT` | Assuming a policy attached to a table applies to a table derived from it. It does not. Also: RLS evaluates before masking, and masking cannot attach to external or temporary tables at all. |
| `postgres` | **No column masking.** Row security policies per table; column-level `GRANT`; views | Reaching for masking guidance that has no implementation, then substituting a view and believing it is equivalent. Also: dbt usually **owns** what it creates, and an owner normally bypasses row security. |
| `duckdb` | **Nothing.** Typically a local file — the file itself is the exposure | Treating a local database as a safe place to materialize regulated data. An unencrypted local file is the least protected copy in the pipeline. |
| `trino` | Depends entirely on the connector and any access-control plugin | Assuming a policy in the underlying system is enforced through the engine, or the reverse |
| `other` / unknown | **Unknown** | Any specific recommendation. Withhold and ask. |

Whatever the engine, the verification is the same shape and it is not optional: **after building, query the new relation as a role that should not see the values.** Building as your own privileged role and observing real values proves nothing about anyone else. If you cannot assume such a role, say that the protection is unverified rather than reporting it as applied.

---

## 3. The create-time versus post-hook window

A `post_hook` that applies a masking policy runs **after** the relation is created and populated. Between those two moments the unmasked relation exists, is committed, and is queryable by anyone the schema grants access to.

```sql
-- The trap. Ordering is create -> populate -> commit -> then protect.
{{ config(
    materialized='table',
    post_hook="<statement that applies a masking policy to <column>>",
) }}
```

Call this window what it is: **a real interval during which regulated data sits in a shared schema in plaintext.** Its properties make it worse than its duration suggests:

- It recurs on **every build**, not once at deploy. A model built hourly opens the window hourly, forever.
- Some engines' incremental and full-refresh paths create a new relation and swap it in. The replacement can arrive without the policy until the hook lands.
- If the hook fails — a syntax error, a missing privilege, a transient failure — the relation stays unmasked. dbt reports the model as an error, but the **data is already there**. An engineer reading the failure sees a hook problem, not an exposure.
- Query history, result caches and time-travel may retain the unmasked read that happened inside the window. The exposure can outlive the window itself.

**Prefer, in this order:**

1. **Do not materialize the sensitive column.** Nothing to protect. Ask whether a derived value serves the purpose (section 1).
2. **Apply protection at create time**, where the engine supports it — a policy attached via tag or classification at creation, or a governed-relation construct that carries the policy with the object. Requires `project.warehouse` and confirmation the mechanism applies; the per-engine syntax and its privilege requirements are in [engine-mechanisms.md](engine-mechanisms.md).
3. **Materialize as a view**, so evaluation happens against the source relation and the querying role. A view has no window because it holds no copy. This is the strongest option available on engines with no create-time policy attachment.
4. **A `pre_hook` that restricts the relation, plus a `post_hook` that applies the policy and then opens access.** Narrows the window rather than closing it. Correct only if the pre-hook genuinely denies reads for the interval — verify that, do not assume it.
5. **Post-hook alone.** Acceptable only when the window has been named to a human who accepted it, and when the schema's grants make the interval low-consequence. Never as a silent default.

Where a model **contract** or an equivalent create-time declaration can carry the constraint, prefer it — a declaration enforced at creation cannot be skipped by a hook that failed. See `dbt-authoring-schema-yaml` for contract mechanics.

And if you leave a post-hook in place, **the ordering must be in the summary and the PR body.** A reviewer cannot see a timing window in a diff.

---

## 4. Detect, do not assume

### The hard rule

**An untagged column is not a safe column.** Absence of a tag, a policy, a classification or a `meta` entry is not evidence of absence of sensitive data — it is evidence that nobody has recorded anything, which is the normal state of most projects. Tagging is human-entered metadata, and human-entered metadata is incomplete by default.

This is the general principle from `dbt-gathering-context` applied to its highest-consequence case: an empty result from a governance query has three possible meanings, and only one of them is "no sensitive data here."

| The query returned nothing because | Reality | What it needs |
|---|---|---|
| There genuinely is no sensitive data | The negative is real | Nothing |
| The engine has no such catalog, or your role cannot read it | The instrument is blind | Establish coverage another way |
| The catalog works and nobody ever populated it | Untagged sensitive data | A human who knows the source |

The third is the common case. So before treating any empty governance result as evidence, **prove the instrument can return a presence**: query for *any* tagged column, or *any* policy, anywhere you can see. Zero across the whole account means the mechanism is unused or invisible to you, and its silence about your column is worth nothing.

### What to check

Four things, and they are independent — a column can have any subset:

1. **Tags or classification labels** on the column and on its relation.
2. **A masking policy** attached to the column, directly or through a tag.
3. **A row-level policy** on the relation, which changes which *rows* a role sees and therefore what an aggregate over your copy will contain.
4. **Grants** on the source relation — a restrictively-granted source is itself a classification signal, often the only one present.

The catalog views and functions for all four are engine-specific. **Ask the user for the query if `project.warehouse` is absent**, rather than writing one against a guessed catalog: a query against a non-existent view errors (recoverable), and a query against a similarly-named view that means something else returns a confident empty result (not recoverable).

### Signals when the metadata is empty

With no tags and no policies, you are inferring, and you must label it as inference:

- **Column name and description.** Names suggesting a person, a contact, a location, a document number, a credential, an authentication token, or a financial instrument. Weak evidence, easy to fool, still better than nothing.
- **The source system's purpose.** Data from a system whose job is to hold information about people is about people, whatever the columns are called.
- **Restrictive grants on the source.** Someone restricted it for a reason. Find out what it was.
- **A free-text column.** The most under-appreciated case. A notes, comment, description or payload column has no schema, so it can hold anything anyone typed — including identifiers, contact details and credentials. Sampling a free-text column to "check whether it contains anything sensitive" means reading the sensitive data yourself and possibly copying it into a transcript. Treat unstructured text as sensitive by default and ask, rather than inspecting it to decide.

**Never write "this model contains no PII."** Write what you actually established: *"No sensitivity tags or masking policies are recorded on these columns; the project records no classifications anywhere, so this is an absence of metadata rather than an absence of sensitive data. Source ownership needs to confirm."* The first sentence is a claim nobody can support. The second is true, useful, and routes the question to someone who can answer it.

---

## 5. Carrying classification in dbt

dbt cannot enforce a warehouse policy, but it can carry the *record*, and the record is what the next agent and the next engineer will read.

```yaml
models:
  - name: <model>
    columns:
      - name: <column>
        description: "<what the column is, at what grain>."
        meta:
          sensitivity: <classification_level>
          classification_source: <where the classification came from>
        tags: ["<sensitivity_tag>"]
```

Four rules, each earned by a specific failure:

- **Record it at the origin and on every relation the column reaches.** A classification present in staging and missing in the mart is worse than absent from both: it tells a reader the project classifies things, so the unmarked mart column reads as deliberately unclassified.
- **Use the project's existing vocabulary.** Grep for how sensitivity is already expressed before inventing a key. Two vocabularies for one concept means every future query over the metadata is wrong for half the project.
- **Keep it in sync when a column propagates.** Adding a column downstream and copying the YAML entry without its `meta` is the single most common way classification is lost. It happens in exactly the change that looks most mechanical.
- **Where the engine supports it, prefer a tag the warehouse also understands** over a `meta` key only humans read. A tag that a policy can attach to closes the loop between the record and the enforcement; a `meta` key is documentation, and documentation does not mask anything.

### The YAML record and the warehouse policy drift

They are two independent systems with no reconciliation, and they diverge in both directions:

- YAML says sensitive, warehouse has no policy → **false confidence.** A reader believes the column is protected. It is in plaintext. This is the more dangerous direction, because the record was created by someone acting in good faith.
- Warehouse has a policy, YAML is silent → **a surprise.** Someone copies the column downstream unaware of the obligation, and the copy escapes it. This is the failure at the top of this skill.

Neither drift produces an error, a warning, or a failing test. Nothing detects it but a deliberate comparison. **When you touch a classified column, verify the warehouse state rather than trusting the YAML, and correct the YAML if it is wrong** — a stale classification record is an active hazard, not merely stale documentation.

> **The one check here that needs no warehouse access.** If the contract declares `sensitivity.levels` and a layer declares `layers[].max_classification`, a column classified above its layer's ceiling is a defect you can prove from the repository alone — no grants, no masking policies, no introspection. It answers a narrower question than "is this column protected," but it answers it definitively, and it is the only check in this skill that cannot be defeated by a blind or unpopulated instrument.

```bash
# Columns whose recorded classification exceeds the layer ceiling.
# Reads the project's own vocabulary from the contract -- do not hardcode levels.
dbt parse
python3 - <<'PY'
import json
manifest = json.load(open('target/manifest.json'))
# levels in increasing order, and each layer's ceiling, both from conventions.yml
levels = ["public", "internal", "confidential", "restricted"]   # sensitivity.levels
ceilings = {"marts_reports": "internal"}                        # layers[].max_classification
rank = {name: i for i, name in enumerate(levels)}
for node in manifest["nodes"].values():
    if node["resource_type"] != "model":
        continue
    layer = node.get("config", {}).get("tags", [])            # or however layers are identified
    for col_name, col in node.get("columns", {}).items():
        found = col.get("meta", {}).get("classification")     # sensitivity.meta_key
        if not found:
            continue
        for lyr, ceiling in ceilings.items():
            if lyr in layer and rank.get(found, 0) > rank.get(ceiling, 0):
                print(f"{node['name']}.{col_name}: {found} exceeds {lyr} ceiling {ceiling}")
PY
```

Two honest limits on that check. It sees only columns someone classified, so it cannot find an unclassified sensitive column — that is what section 4 is for. And it compares records, not reality: a column correctly classified `restricted` and correctly kept out of a capped layer can still be unprotected in the warehouse if no policy was ever applied.

---

## 6. Deletion requests through a modelled pipeline

A request to delete a person's data does not stop at the source system. Every relation derived from that source may hold their rows, and a modelled pipeline is specifically designed to make copies.

**This section describes engineering steps. Whether a given relation must be purged, and on what timeline, is a legal and policy determination.** Do not decide it, do not estimate it, and do not reassure anyone that a particular retention is acceptable. Your job is to make the full set of affected relations visible so that the people who own the decision can make it against facts instead of assumptions.

The full procedure — the enumeration commands, the behaviour of each resource class, the snapshot `hard_deletes` config and why none of its modes actually remove history, and what "designing for deletion" costs versus retrofitting it — is in [deletion-and-retention.md](deletion-and-retention.md). Read it before responding to a request.

### Enumerate every relation holding the subject's rows

```bash
# Every model downstream of the source that carries the subject identifier
dbt ls --select source:<source>+ --resource-type model
grep -rn "<subject_identifier_column>" models/ snapshots/ seeds/ \
  --include=*.sql --include=*.yml --include=*.csv
dbt ls --resource-type snapshot
dbt ls --resource-type seed
```

Then work through the classes below. Each one behaves differently and each is missed for its own reason.

| Class | Behavior under deletion | Why it is missed |
|---|---|---|
| **View** | Nothing to delete — resolves against its source | Sometimes listed anyway, which wastes the reviewer's attention |
| **Table** | Rebuild after the source is purged and the rows are gone | Only if the rebuild actually happens; nothing triggers it automatically |
| **Incremental** | **Rebuilding does not remove them.** An incremental model never deletes a row it has already written unless something explicitly deletes it | Looks like a table in the DAG and behaves nothing like one |
| **Incremental with `full_refresh=false`** | The mechanism that would rebuild it is deliberately disabled | The config exists precisely to prevent the operation deletion requires |
| **Snapshot** | **Retains history by design.** That is the entire purpose. Rows are preserved even after the source stops producing them | Frequently excluded from the enumeration because it is a different resource type |
| **Seed** | Committed to the repository. Deleting the file does not remove it from git history | Not queried, so not thought of as data |
| **Test failures table** | May hold sample values from the sensitive column | Nobody thinks of it as a relation. Section 9 and [leak-surfaces.md](leak-surfaces.md) |
| **Warehouse retention artifacts** | Time-travel, fail-safe, backups, clones, exports and replicas can retain deleted rows past the deletion | Outside dbt entirely, so outside the DAG's field of view |

Do not report the DAG as the complete list. Direct extracts, reverse-ETL destinations and downstream applications hold copies too, and `dbt ls` cannot see any of them. Use the warehouse query log to find readers, per `dbt-breaking-changes`, and name the classes you could not enumerate.

### The genuine conflict, resolved honestly

`dbt-incremental-models` recommends `full_refresh=false` on any model whose history cannot be reproduced from its source, and that recommendation is correct: without it, one flag causes unrecoverable loss. A snapshot's purpose is likewise to retain history the source no longer holds.

**Those two protections are exactly what block a deletion request from propagating.** This is not a bug in either recommendation and there is no configuration that satisfies both. It is a direct conflict between two legitimate obligations:

- Retaining history that the source cannot reproduce, for reporting, audit, or contractual reasons.
- Removing a person's data on request.

**The resolution is not an engineering decision and you must not make it.** State the conflict explicitly, enumerate exactly which relations are affected and what each one holds, and escalate. What you can legitimately do:

- Enumerate the conflicting relations precisely — names, materializations, what subject data each holds, and what removing it would cost in history.
- Lay out the mechanically available options *without recommending one*: targeted row deletion; overwriting identifying values in place while retaining the non-identifying rows; deleting from some relations and retaining others; excluding the subject going forward only.
- Note which options require a bespoke procedure because `full_refresh=false` blocks the ordinary rebuild path.
- Say plainly that the tradeoff between retaining history and honouring the deletion is a legal determination, and that you are not making it.

Two mechanical warnings that hold regardless of which option is chosen:

- **A targeted delete against a `full_refresh=false` model is irreversible and outside dbt's model.** dbt did not perform it and cannot reproduce it. If the model is later rebuilt from a source that still holds those rows, they come back. Record the deletion somewhere the rebuild path will encounter it.
- **Deleting rows from an incremental model can break its boundary predicate.** A boundary computed from `max(<timestamp>)` on the target changes when rows are removed, so the next run may skip or re-read a window. Verify the boundary behavior after any manual delete — see `dbt-incremental-models`.

**Deletion has to be designed for rather than retrofitted.** Every property that makes a pipeline reliable — incremental accumulation, `full_refresh=false`, snapshot history, a time-travel window — is a property that resists deletion. Retrofitting means carrying the subject key end to end, adding a delete path per relation, and re-verifying every incremental boundary afterwards: a project, not a step. Say so when the topic first arises, because the cheap moment to design for it is model creation and the expensive moment is when the request arrives.

---

## 7. Retention, purge, and minimisation

A retention policy says data older than some age must not exist. An incremental model never deletes anything. Left alone, these two facts coexist quietly for years, and the discovery event is an audit. Full treatment in [deletion-and-retention.md](deletion-and-retention.md).

- **An incremental model accumulates without bound.** It has no expiry mechanism. If a policy requires a maximum age, something must delete — a scheduled purge, a bounded rebuild, or a partition-expiry feature where the engine has one. Nothing in the model config does it for you.
- **A `full_refresh=false` model cannot be purged by rebuilding.** The purge must be an explicit deletion, with the irreversibility that implies.
- **Snapshots grow by design and retain what the source dropped.** A snapshot over a column subject to a retention policy is a standing conflict; name it rather than discovering it during an audit.
- **A retention policy on the source does not propagate downstream.** If the source expires rows at ninety days and a downstream table has three years, the downstream table is now the system of record for data the policy said should be gone — and it is the copy nobody is monitoring.
- **Warehouse-level retention features** — partition expiry, time-travel windows, table lifecycle rules — exist on some engines and not others, and are gated on `project.warehouse`. Where one exists, prefer it: an engine-enforced expiry does not depend on a scheduled job somebody might disable.
- **A time-travel or fail-safe window is itself retention.** A relation purged today may be restorable for days afterwards. Whether that satisfies or violates a policy is not yours to decide; that it is true is a fact to surface.

Whether a policy applies, and what its window is, is a policy question. Ask; do not infer a retention window from what the data currently looks like — the oldest row in a table tells you what happened, not what was permitted.

### Minimisation is the only control with no failure mode

Everything else in this skill can fail. A masking policy can be absent from a derived relation. A grant can be widened to unblock a build. A classification can be dropped in the change that felt most mechanical. A retention job can be disabled. A hash can be brute-forced.

**Not selecting a column cannot fail.** Nothing to protect, nothing to propagate, nothing to purge, nothing to verify as an unprivileged role, nothing to enumerate when a request arrives.

So it is the first question, not the fallback. Two consequences worth stating in a review: every additional column in a mart is an obligation carried forever — classified, granted, masked, purged, enumerated — and a column carried "in case someone needs it" has all of those obligations and none of the value, which also means no consumer will ever notice it is unprotected. And because removing a column later is a breaking change (`dbt-breaking-changes`), the cost of adding one is not symmetric with the cost of declining.

---

## 8. Grants and least privilege from dbt

dbt applies grants per relation through the `grants` config:

```yaml
models:
  - name: <model>
    config:
      grants:
        select: ["<role_or_group>"]
```

The trap is not the syntax. It is this:

**A relation in a broadly-readable schema effectively re-grants everything it contains.** A model built into a schema that a wide group can read makes every column in it readable by that group, whatever the source relation's grants were. You have not copied a column; you have republished it to a different audience. The `select` is one line and the change of audience is invisible in the diff.

Consequences worth stating separately. The full mechanics — clobber-versus-add on the `+` prefix, why deleting the config does not revoke, platform-specific privilege and grantee naming, `copy_grants`, and how a rebuild drops externally-applied grants — are in [grants-and-access.md](grants-and-access.md).

- **Schema-level grants usually dominate.** A permissive default on the schema is applied to relations created in it, so a careful per-model `grants` config can be overridden by a schema someone configured a year ago. Check the schema's defaults, not only the model's config.
- **`grants` is additive or replacing depending on a `+` prefix on the *privilege name***, and the difference decides whether you tightened access or merely added to it. Clobbering can silently narrow access and break a consumer; adding can silently widen it past what a reviewer approved. Verify the compiled behavior rather than the intent.
- **A full refresh is a permissions event.** Depending on the engine and on whether grants are copied, replacing a relation can drop every grant applied outside dbt — so a change with no permissions content in its diff can remove someone's access at whatever hour the refresh runs. When investigating lost access, check whether a full refresh ran before concluding a revoke was deliberate.
- **Removing the `grants` config does not revoke anything.** dbt concludes you no longer want it to manage grants and changes nothing. Revoking requires an empty list of grantees, which means "we removed the grants config" and "we removed the grants" look identical in a diff and are not.
- **Development schemas are not private by default.** Many projects grant a shared role broad read across the development database for convenience. A sensitive column materialized in a personal development schema may be readable by everyone in the project. Check `environments.dev` and the actual grants rather than assuming isolation from the word "dev".
- **A terminal relation is the widest surface**, because consumers read it directly. `layers[].terminal` marks these; an over-broad grant there reaches the most people.
- **Grants are not masking.** A grant decides whether a role can read the relation; masking decides what a permitted reader sees. Where a group needs the rows but not the values, the answer is a policy or a derived column — not a grant, and not both used interchangeably. dbt's own guidance is that anything beyond relation-level grants — column- and row-level access, masking policies, future grants — stays in hooks rather than in `grants`.

Least privilege applied concretely: grant to the narrowest role that satisfies the actual need, name that need, and do not widen a grant to make something work without saying that you widened it. A grant added to unblock a build is a permanent access change.

---

## 9. What to never do

Four absolutes. Each has been a real incident somewhere, and none of them are caught by a test. The full inventory of copies the warehouse's governance features never see — production clones in a dev schema, the docs catalog, logs and previews, CI transcripts — is in [leak-surfaces.md](leak-surfaces.md), along with the safer alternatives to each.

**Never copy a governed column into a seed.** Seeds are committed to the repository and remain in git history after the file is deleted. There is no masking, no grant, and no removal short of rewriting history across every clone. `dbt-sources-and-seeds` states the rule; the point here is that a seed is the one destination where the exposure is permanent and irreversible. The usual pretext is "just a few rows for testing", and a few real rows is still a transfer into a system with no access control and unbounded retention.

**Never enable `store_failures` on a test over a sensitive column without knowing where the failures table lands.** A failing test writes the offending rows — real values, and specifically the *unusual* records, which are often the most sensitive subset in the table — into a persisted relation, in a schema whose grants you have probably not checked, created by the test rather than by a model, so it appears in no model review. Before enabling it: determine the resolved database and schema and their grants; or set `store_failures_as: ephemeral`, which keeps the test and stores nothing; or use a test that returns a count rather than rows. The `limit` config caps stored rows, but whether it bounds the *write* rather than only the subsequent read is version-dependent — verify rather than assume. And note that a project-level default turns this on for every test added afterwards, including on columns nobody had classified yet.

**Never paste real values anywhere they persist.** Not in a PR title or body, not in a commit message, not in a YAML description, not in a code comment, not in a summary to the user, not in an example row. A sample value pasted to illustrate a data quality problem moves regulated data into a system with entirely different access controls and no retention policy — and a PR body is world-readable in a public repository and permanent in a private one. Describe the shape, the count, and the pattern. Never the value. Two channels people forget: a YAML `description` is persisted to the warehouse as a comment *and* published in the docs catalog, and an error message can itself carry a value from the failing row.

**Never build a sensitive model into an unverified schema to see whether something works.** The build is the exposure. There is no undo — dropping the relation does not remove it from query history, result cache, time-travel or an audit log. Verify the target and its grants first. Where the question is only "does this build", dbt's `--empty` flag limits refs and sources to zero rows while still executing the SQL against the warehouse, which answers the structural question without moving any data. It is ignored for Python models, so it buys nothing there.

Two more, less absolute but worth the same reflex: **prefer aggregates over samples** when inspecting a column — a count, a null rate, a distinct count, or a regex match rate answers nearly every real question without returning a value, and sampling a free-text column to decide whether it holds anything sensitive means reading the sensitive data yourself; and do not widen a grant, disable a policy, or bypass masking to make a build or a test pass. If a masked value breaks a join, the join needs a different key — a keyed-hash or tokenized column, per [derived-values.md](derived-values.md) — not the unmasked value.

---

## Compliance vocabulary

Terms that arrive in tickets and source documentation — PII, PHI, PCI, special-category data, quasi-identifiers, purpose limitation, erasure, rectification, data residency — are catalogued in [compliance-vocabulary.md](compliance-vocabulary.md), framed as things to **recognise and route**, never to adjudicate. Read it when a request arrives carrying one of them, and read it before writing any sentence that sounds like a conclusion about a regime.

The one entry there that is genuinely a technical concern rather than a legal one is **data residency**. Engines are regional, several protection mechanisms are region-scoped, a cross-region copy can strip protection or be rejected outright, and a Python model's compute can be configured in a different region from the storage that every SQL model in the same project respects — see `dbt-python-models`. State the regions involved; do not state whether the arrangement is permitted.

---

## Completion checklist

- [ ] `project.warehouse` read from the contract, or determined from the adapter and the method stated
- [ ] Mechanism-specific masking, tagging and row-policy guidance **withheld entirely** where the engine is unknown or `other`, and the withholding stated
- [ ] Classification resolved for every column being selected, **before** the SQL was written
- [ ] Warehouse queried for existing tags, masking policies, row policies and grants — not inferred from YAML alone
- [ ] Instrument coverage proven before any empty governance result was treated as evidence
- [ ] No claim made that a column is not sensitive; scope of what was checked stated instead
- [ ] Free-text and payload columns treated as sensitive by default rather than sampled to decide
- [ ] Aggregates used instead of row samples wherever they answered the question
- [ ] Considered and offered a derived, hashed or bucketed value in place of carrying the sensitive column
- [ ] Any hash offered as protection assessed for brute-force reversibility, and a keyed hash or token used where the identifier space is enumerable
- [ ] Derived column carries the same classification as its origin unless it genuinely cannot reconstruct it
- [ ] No relation left holding both an identifier and its digest side by side
- [ ] Classification recorded in `meta` or tags at the origin **and** on every downstream relation the column reaches
- [ ] YAML record reconciled against the actual warehouse policy, and corrected if it drifted
- [ ] Materialisation chosen with policy evaluation in mind — view versus table versus materialized view, not assumed equivalent
- [ ] Protection applied at create time, or via view, or the post-hook window explicitly named to a human who accepted it
- [ ] Masking verified by querying as a role that should **not** see values, or reported as unverified
- [ ] Target schema and its grants confirmed before building, including in development
- [ ] `grants` config reviewed against schema-level defaults, not read in isolation
- [ ] Clobber-versus-add behaviour of any `grants` change established, not assumed
- [ ] Grant loss on full refresh considered where grants are applied outside dbt
- [ ] Terminal relations checked for over-broad grants
- [ ] Production data **not** cloned into a personal schema; `--empty`, a bounded window, or fabricated fixtures used instead
- [ ] Deletion request: every relation enumerated across views, tables, incrementals, snapshots, seeds, failures tables and warehouse retention artifacts
- [ ] Deletion request: `full_refresh=false` and snapshot conflicts named explicitly and **escalated as a legal decision**, not resolved
- [ ] Incremental boundary re-verified after any manual row deletion
- [ ] Retention implications of unbounded incremental accumulation stated
- [ ] No sensitive column in any seed
- [ ] `store_failures` destination checked, or the test bounded to a count, or `store_failures_as: ephemeral`
- [ ] No real values in the PR, commit message, descriptions, comments, docs site, or the summary
- [ ] Compliance vocabulary in the request recognised and routed rather than adjudicated; no claim made about any regime
- [ ] Regions stated where data crosses one, without any claim about whether the transfer is permitted

## Common failure modes

1. **Selecting a masked column into a new table in a different schema.** The engine evaluates the policy once as the build role and writes the result into an unpoliced column. Plaintext regulated data, in a schema with different grants, produced by a change that compiles cleanly and passes every test. The failure this skill exists to prevent, and nothing else in the library detects it.
2. **Assuming tag-based propagation covers a `create table as select`.** On the engine where it exists, propagation is a per-tag property that must be configured for **data movement** specifically, tags applied that way are not updated afterwards, and system tags from automated classification do not propagate at all. Three separate conditions, each of which silently produces an unprotected column.
3. **Treating an untagged column as a safe column.** Untagged sensitive data is the normal case, not the exception. An empty governance query means nobody populated the catalog far more often than it means there is nothing to find.
4. **Reporting "no PII in this model."** An unsupportable claim. What was actually established is that no classification is recorded — a statement about metadata, not about data.
5. **Offering a hash as if it were anonymisation.** A hash of a structured or low-cardinality identifier is reversible by hashing the candidate set and comparing, and a per-row salt stored beside the digest makes that slower rather than infeasible. The digest inherits the classification of the column it came from.
6. **Applying masking in a post-hook and calling the model protected.** There is a real interval, recurring on every build, during which the unmasked relation is committed and queryable. If the hook fails, the interval never ends and the error names the hook rather than the exposure.
7. **Verifying masking as the build role.** A privileged role sees real values whether or not a policy exists. The test is a role that should not see them; anything else confirms nothing.
8. **Dropping classification while propagating a column.** The YAML entry gets copied without its `meta`, in the change that feels most mechanical. The downstream copy now looks deliberately unclassified.
9. **Trusting the YAML classification over the warehouse.** They drift in both directions with no error and no test. The dangerous direction is YAML claiming protection the warehouse does not implement, because it was written in good faith and reads as authoritative.
10. **Substituting a view for a masking policy on an engine that has no masking.** A view withholds a column from people who query the view. It does nothing about people who query the table, and on at least one engine it evaluates as its *owner* rather than the caller, which bypasses row security silently.
11. **Reaching for a materialized view to get a cheap protected copy.** On some engines masking and materialized views are mutually exclusive in both directions — the policy cannot be added once the view exists, and the view cannot be created over a masked column. Not a view with better performance.
12. **Omitting snapshots and incremental models from a deletion enumeration.** Snapshots retain history by design; incrementals never delete a row they have written. Both look like ordinary nodes in the DAG and behave nothing like the tables they resemble. And a snapshot's hard-delete config *records* deletions rather than removing history.
13. **Resolving the `full_refresh=false` conflict yourself.** Retaining irreplaceable history and honouring a deletion request are mutually exclusive obligations. Choosing between them is a legal determination. Enumerate, present, escalate — do not decide, and do not reassure.
14. **Assuming a rebuild removes a purged row.** It does for a table. It does not for an incremental model, it does not for a snapshot, and it cannot for a model where `full_refresh=false` blocks the rebuild entirely.
15. **Cloning production into a personal schema to reproduce a bug.** The most common leak in a modelled pipeline, produced by a reasonable instinct. The build role sees plaintext, the dev database is frequently readable by the whole team, and dropping the relation afterwards does not undo the read. `--empty` answers the structural question for free.
16. **Assuming a development schema is private.** Many projects grant broad read across the development database, deliberately, so colleagues can inspect each other's work.
17. **Enabling `store_failures` on a sensitive column.** The failing rows — real values, and specifically the anomalous subset — persist in a relation created by the test rather than by a model, in a schema nobody reviewed, appearing in no model diff.
18. **Treating the docs site as documentation rather than as disclosure.** The published catalog carries every column name, type, comment and platform statistic, and the manifest carries compiled SQL. The static build inlines all of it into one file that anyone who can fetch the page can read entirely.
19. **Pasting a sample value into a PR to illustrate a problem.** It moves regulated data into a system with different access controls, no retention policy, and permanent history. Describe the pattern and the count.
20. **Losing grants to a full refresh and fixing it by widening access.** The rebuild dropped an externally-applied grant; the debugging engineer grants something broader to restore service. The symptom is fixed and an access boundary has moved permanently.
21. **Widening a grant or bypassing masking to make a build pass.** The build succeeds and an access boundary has been permanently moved. If a masked value breaks a join, the join needs a keyed-hash or tokenized key, not the real value.
22. **Guessing the warehouse to give masking advice.** A policy statement for the wrong engine is either an error, or accepted and inert. The second outcome ends the investigation with an engineer believing regulated data is protected when it is in plaintext.
23. **Answering a compliance question instead of routing it.** "This is compliant" is a legal conclusion about an organisation, not a property of a model. A confident wrong answer here does not waste time — it ends the investigation.
