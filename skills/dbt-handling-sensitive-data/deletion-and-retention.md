# Deletion, retention, and minimisation

A modelled pipeline is a machine for making copies. That is its purpose, and it is why a request to remove one person's data, or to enforce a maximum age on a dataset, does not stop at the source system.

**This document describes engineering steps only.** Whether a relation must be purged, on what timeline, and what may be retained instead, is a legal and policy determination. Do not decide it, do not estimate it, and do not reassure anyone that a particular retention is acceptable. The job is to make the full set of affected relations visible so the people who own the decision can make it against facts rather than assumptions.

---

## 1. Why deletion has to be designed for, not retrofitted

Every mechanism that makes a pipeline reliable also makes it resistant to deletion. This is not an accident or a set of bugs — it is the same property viewed from two directions.

| The reliability property | Why it exists | What it does to a deletion request |
|---|---|---|
| An incremental model only ever adds and updates | Reprocessing history is expensive, often impossible | It **never removes a row it has already written** unless something explicitly removes it |
| `full_refresh: false` on a model whose history cannot be reproduced | One accidental flag would otherwise destroy irreplaceable data | It disables the exact operation that a rebuild-based purge requires |
| A snapshot retains history the source no longer holds | That is the entire point of a snapshot | It preserves the subject's rows *by design*, including after the source stops producing them |
| A time-travel or fail-safe window | Recovery from mistakes | The pre-deletion state remains queryable or restorable for the window |
| Downstream extracts and reverse-ETL | Getting data to the systems that use it | Copies exist in systems the warehouse does not govern and `dbt ls` cannot see |

The consequence is structural: **a pipeline built without deletion in mind will require a bespoke procedure for every deletion request, forever.** Retrofitting is possible but it is a project — carrying the subject key end to end, adding a delete path per relation, and re-verifying every incremental boundary afterwards. That is worth saying out loud when the topic first comes up, because the cheap moment to design for it is when the model is created, and the expensive moment is when the request arrives.

What "designed for" concretely means, if a project is choosing now:

- **The subject identifier reaches every relation that holds subject rows**, so a delete can find them. A model that drops the key and keeps derived attributes cannot be purged by key at all, only rebuilt.
- **Deletion is a path, not an incident** — an operation or a macro that takes a key set and applies it to the enumerated relations in order, so the procedure is reviewable and repeatable rather than reconstructed under pressure.
- **Snapshots and `full_refresh: false` models are chosen deliberately**, with someone aware that each one is a standing conflict rather than a default.
- **Any relation that cannot be purged is recorded as such**, with what it holds and why. An undocumented one is discovered during an audit.

---

## 2. Enumerating every relation that holds the subject's rows

```bash
# Models downstream of the source
dbt ls --select source:<source>+ --resource-type model

# Every reference to the subject identifier, including in snapshots and seeds
grep -rn "<subject_identifier_column>" models/ snapshots/ seeds/ \
  --include=*.sql --include=*.yml --include=*.csv

# The resource types that are easiest to forget
dbt ls --resource-type snapshot
dbt ls --resource-type seed
```

Then work through the classes. Each behaves differently under deletion and each is missed for its own reason:

| Class | Behaviour under deletion | Why it is missed |
|---|---|---|
| **View** | Nothing to delete — resolves against its source | Sometimes listed anyway, wasting the reviewer's attention on a non-issue |
| **Table** | Rebuild after the source is purged and the rows are gone | Only if the rebuild actually happens. Nothing triggers it automatically |
| **Incremental** | **A rebuild does not remove them.** The model never deletes a row it has written | Looks like a table in the DAG and behaves nothing like one |
| **Incremental with `full_refresh: false`** | The mechanism that would rebuild it is deliberately disabled | The config exists precisely to prevent the operation deletion requires |
| **Snapshot** | **Retains history by design**, including rows the source has dropped | A different resource type, so routinely excluded from the enumeration |
| **Seed** | Committed to the repository. Deleting the file does not remove it from git history | Not queried, so not thought of as data |
| **Test failures table** | May hold sample values from the sensitive column, in a schema nobody reviewed | Created by a test rather than a model. See `leak-surfaces.md` |
| **Warehouse retention artifacts** | Time-travel, fail-safe, backups, clones, exports and replicas can retain rows past the deletion | Outside dbt, so outside the DAG's field of view |
| **Non-dbt consumers** | Extracts, reverse-ETL destinations, BI extracts and applications hold their own copies | `dbt ls` structurally cannot see them |

**Do not report the DAG as the complete list.** Use the warehouse query log to find readers, per `dbt-breaking-changes`, and then name the classes you could not enumerate. "No other consumers found" and "I could not check for other consumers" are different claims, and only one of them is honest here.

### The snapshot detail worth knowing by name

Snapshots have a config for source deletions, and it is easy to misread as a deletion mechanism. `hard_deletes` accepts `ignore` (the default — the deleted record's `dbt_valid_to` simply stays null, so it looks current forever), `invalidate` (closes the record by setting `dbt_valid_to`), and `new_record` (adds a new row flagged in a `dbt_is_deleted` metadata column). All three **record** the deletion. **None of them remove the subject's historical rows** — `new_record` adds a row rather than removing any. Verify the version: `new_record` and the `hard_deletes` config itself arrived in dbt 1.9, replacing an earlier `invalidate_hard_deletes` flag.

So a snapshot configured to track hard deletes is *more* complete evidence of the subject's history, not less. That is exactly right for auditing and exactly wrong for erasure, and stating it that way is more useful than either half alone.

---

## 3. The conflict, and how to hold it honestly

`dbt-incremental-models` recommends `full_refresh: false` on any model whose history cannot be reproduced from its source, and that recommendation is correct: without it, one flag causes unrecoverable loss. A snapshot's purpose is likewise to retain history the source no longer holds.

**Those two protections are precisely what block a deletion request from propagating.** There is no configuration that satisfies both. It is a direct conflict between two legitimate obligations:

- Retaining history the source cannot reproduce, for reporting, audit, or contractual reasons.
- Removing a person's data on request.

**The resolution is not an engineering decision.** State the conflict, enumerate exactly which relations are affected and what each holds, and escalate. What you can legitimately do:

- Enumerate the conflicting relations precisely — names, materialisations, what subject data each holds, and what removing it would cost in history.
- Lay out the mechanically available options **without recommending one**: targeted row deletion; overwriting identifying values in place while retaining the non-identifying rows; deleting from some relations and retaining others; excluding the subject going forward only.
- Note which options require a bespoke procedure because `full_refresh: false` blocks the ordinary rebuild path.
- Say plainly that the trade-off between retaining history and honouring the deletion is a legal determination you are not making.

Overwriting in place deserves one note, because it is the option that most often looks like a compromise and sometimes is not: replacing an identifier with a null or a constant leaves the non-identifying attributes intact, and if those attributes are quasi-identifiers, the row may still be attributable to the person. Whether that counts is not an engineering question, but whether the row is still distinguishable *is*, and you can answer it — see `derived-values.md`.

### Two mechanical warnings, whichever option is chosen

- **A targeted delete against a `full_refresh: false` model is irreversible and outside dbt's model.** dbt did not perform it and cannot reproduce it. If the model is later rebuilt from a source that still holds those rows, they come back. Record the deletion somewhere the rebuild path will encounter it.
- **Deleting rows from an incremental model can break its boundary predicate.** A boundary computed from `max(<timestamp>)` on the target changes when rows are removed, so the next run may skip a window or re-read one. Verify the boundary behaviour after any manual delete — see `dbt-incremental-models`.

---

## 4. Retention

A retention policy says data older than some age must not exist. An incremental model never deletes anything. Left alone, these two facts coexist quietly for years, and the discovery event is an audit.

- **An incremental model accumulates without bound.** It has no expiry mechanism. If a policy requires a maximum age, something must delete: a scheduled purge, a bounded rebuild, or an engine-level expiry feature. Nothing in the model config does it for you.
- **A `full_refresh: false` model cannot be purged by rebuilding.** The purge must be an explicit deletion, with the irreversibility that implies.
- **Snapshots grow by design and retain what the source dropped.** A snapshot over a column subject to a retention policy is a standing conflict. Name it rather than discovering it later.
- **A retention policy on the source does not propagate downstream.** If the source expires rows at ninety days and a downstream table holds three years, that downstream table is now the system of record for data the policy said should be gone — and it is the copy nobody is monitoring.
- **Warehouse-level retention features** — partition expiry, time-travel windows, table lifecycle rules — exist on some engines and not others, and are gated on `project.warehouse`. Where one exists, prefer it: an engine-enforced expiry does not depend on a scheduled job somebody might disable, and it does not silently stop working when a job's owner leaves.
- **Time-travel and fail-safe windows are themselves retention.** A relation purged today may be restorable for days afterwards. Whether that satisfies or violates the policy is a question for whoever owns the policy; that it is true is a fact you should surface.

Whether a policy applies, and what its window is, is a policy question. Ask. **Do not infer a retention window from what the data currently looks like** — the oldest row in a table tells you what happened, not what was permitted.

---

## 5. Minimisation is the only fully reliable control

Every other control in this skill can fail. A masking policy can be absent from a derived relation. A grant can be widened to unblock a build. A classification can be dropped in the change that felt most mechanical. A retention job can be disabled. A hash can be brute-forced.

**Not selecting a column cannot fail.** There is nothing to protect, nothing to propagate, nothing to purge, nothing to verify as a role that should not see values, and nothing to enumerate when a deletion request arrives. It is the only control in this document with no failure mode.

That makes minimisation the first question, not the fallback:

- **Ask what the column is for before propagating it.** Most requests that appear to need a sensitive column need something derived from it — see `derived-values.md`. The request "add the email address to this mart" is very often the requirement "let me segment by domain".
- **Column count is retention.** Every additional column in a mart is a column that must be classified, granted, masked, purged, and enumerated forever. A wide `select *` through a staging layer propagates that obligation to columns nobody asked for and nobody will notice.
- **A column carried "in case someone needs it" is the worst case**: it has all the obligations and none of the value, and because no consumer depends on it, nobody will ever notice it is unprotected.
- **Removing a column later is a breaking change**, so the cost of adding one is not symmetric with the cost of not adding it. See `dbt-breaking-changes`.

The reflex to build: when a change adds a sensitive column, ask whether the *requirement* needs the column or needs a fact derivable from it, and offer the derived version first. That conversation takes one exchange and removes the entire chain of obligations described in this document.
