# dbt's own leak surfaces

Every mechanism in `engine-mechanisms.md` protects a *relation*. This document is about the copies that are not relations — the ones the warehouse's governance features never see, because dbt, git, a CI log or a person put them somewhere else.

They share a shape worth naming: **each one is produced by a normal, correct action.** Nobody decides to leak. Someone enables a config to debug a test, commits a fixture, pastes a row into a ticket to show a colleague, or clones production to reproduce a bug. Nothing errors, nothing is reviewed as a data-protection change, and the copy persists.

Ordered by how often each one is the actual cause.

---

## 1. Cloning production into a personal schema

This is the most common leak in a modelled pipeline, and it does not feel like one. The engineer's intent is to reproduce a bug against real data. The result is a full copy of regulated data in a schema whose grants nobody has audited, built by a role that sees plaintext, retained until someone remembers to drop it.

Four properties make it worse than it looks:

- **The build role sees real values by definition.** A masking policy attached to the source is evaluated once, as the build role, and plaintext is written into a column with no policy on it. See the propagation mechanics in `SKILL.md`.
- **Development schemas are frequently readable by everyone on the team.** Many projects grant a shared role broad read across the development database for convenience, precisely so colleagues can inspect each other's work. Check `environments.dev` and the actual grants; do not infer isolation from the word "dev".
- **Tag-based propagation can be defeated by the target.** On engines where a tag-based policy is inherited from a database or schema, a relation cloned or built into a schema with no such policy is governed by the *target's* policy — that is, by nothing.
- **There is no undo.** Dropping the relation does not remove the read from query history, the result cache, time-travel, or an audit log. The exposure survives the object.

### Safer alternatives, in order of preference

| Approach | What it gives you | What it costs |
|---|---|---|
| **Do not select the sensitive column into the dev model at all.** Reproduce with the columns the bug is actually about | Full fidelity on the failing logic, zero exposure | Occasionally the bug *is* in the sensitive column |
| **Build the schema without the data.** dbt's `--empty` flag limits refs and sources to zero rows while still executing the SQL against the warehouse | Catches type errors, missing columns, dependency problems — most compile-adjacent failures | Proves nothing about values or row counts |
| **A bounded window** — filter to a small, recent slice using the project's own dev-limiting mechanism if it has one | Real data shape at a fraction of the volume | Still real values. Reduces scale, not sensitivity |
| **Synthetic or generated fixtures**, as seeds of fabricated rows or as unit-test inputs | Committable, reviewable, reusable, and safe by construction | Someone has to write them, and they encode assumptions that can drift from reality |
| **Masked or tokenised replica**, if the platform team maintains one | Realistic distributions with no plaintext | Only if it exists; building one is a project, not a step |

`--empty` is the underused one. It is available for `run`, `build`, `compile` and `snapshot`, and it makes "does this model build" answerable without moving any data. Two caveats: dbt may skip processing a `ref()` or `source()` under `--empty` as an optimisation, so a relation you need materialised must be forced with `.render()`; and **the flag is ignored for Python models**, so it buys nothing there.

For unit tests as a fixture mechanism, see `dbt-unit-tests` — and note that fabricated rows in a unit test are the one place sample values are unambiguously safe, because you invented them.

---

## 2. `store_failures` writing real values to a table nobody reviewed

A test with `store_failures` enabled writes the offending rows — actual values from the column under test — into a persisted relation. On a test over a sensitive column, that is a plaintext extract of exactly the records that are unusual, which is often the most sensitive subset in the table.

What makes it slip past every other gate:

- **The destination is derived, not declared.** Failures land in a schema suffixed `dbt_test__audit` by default, in whatever database the target points at. Nobody chose that schema, so nobody audited its grants.
- **It is created by a test, not a model.** It appears in no model review, in no `dbt ls --resource-type model`, and in no schema YAML that a reviewer reads.
- **A passing test still replaces the relation.** Results always overwrite the previous run for that test, so the relation exists and looks current even when the current run found nothing.
- **The flag and the config disagree in a way that matters.** `store_failures: true` or `false` overrides the presence of `--store-failures` on the command line; `store_failures_as` overrides `store_failures` entirely. A project-level `+store_failures: true` therefore turns it on for tests nobody thought about, including tests on sensitive columns added later.

Before enabling it on anything that touches a classified column:

1. **Determine the destination and its grants.** Not the intent — the resolved database and schema, and who can read it.
2. **Prefer a bounded test.** The `limit` config caps how many failing rows are stored. It was at one point applied only when *reading* the failures table rather than when *writing* it, which is a version-dependent detail worth verifying rather than assuming: if the write is unbounded, `limit` did not protect you.
3. **Consider `store_failures_as: ephemeral`**, which stores nothing while keeping the test. Or a test that returns a count rather than rows, which tells you the size of the problem without extracting the records.
4. **Set an explicit `schema`** for the failures relation if it must persist, and grant it deliberately.

The general reflex: `store_failures` is a debugging convenience, and the debugging value is highest in development — which is also where the grant surface is widest. Turning it on project-wide to make development easier is the exact trade that produces this incident.

---

## 3. Seeds and git history

A seed is a CSV committed to the repository. That makes it the one destination where the exposure is **permanent and irreversible**: no masking policy applies, no grant restricts it, and deleting the file does not remove it from git history, from every clone, from every fork, or from any CI cache that retained a checkout.

`dbt-sources-and-seeds` states the rule. Two additions specific to this skill:

- **The failure mode is usually "just a few rows for testing".** Someone exports twenty real records to build a mapping table or reproduce a case. Twenty real records is a data transfer into a system with no access control and unbounded retention.
- **A mapping seed is the highest-value target in the repository.** A CSV that maps an identifier to a person is precisely the additional information that turns every pseudonymised column in the warehouse back into identified data. See `derived-values.md`.

If real values have already been committed, say so plainly and escalate. Removing them requires rewriting history across every clone, and whether that is required is not your call.

---

## 4. The docs catalog: column names, types and statistics

`dbt docs generate` queries the warehouse for metadata and writes `catalog.json`: for every model, seed, snapshot and source, the relation's database, schema, name, comment and owner, plus every column's name, type, comment and ordinal — and platform-dependent statistics such as row counts and sizes.

That is not row data, and it is still disclosive:

- **Column names and descriptions leak the schema of sensitive data.** A column named for a document number, a credential, or a health attribute tells a reader what the organisation holds about people, and a description written for internal use tells them more.
- **Statistics leak volume.** A row count on a relation whose name identifies a population is a fact about how many people are in it.
- **The published site embeds the artifacts.** The documentation site loads `manifest.json` and `catalog.json` into the browser, and the `--static` variant inlines both into a single HTML file. Anyone who can fetch the page can read the whole catalog, not just the pages they navigate to. **Access control on the site is access control on the metadata** — and a static file emailed or dropped in object storage has none.
- **`manifest.json` carries compiled SQL.** Every model's rendered SQL, including any hardcoded value, any filter naming an individual, and the full shape of the transformations.

Practical consequences: treat the docs site's audience as a real access decision rather than a hosting detail; limit catalog generation with `--select` where the CI role's access is meant to be limited; and never put a real value in a YAML `description` to illustrate a format, because descriptions are persisted to the warehouse as comments *and* published in the catalog.

---

## 5. Logs, previews, and the CI transcript

Command output is a copy. It is short-lived on a laptop and long-lived almost everywhere else.

| Surface | Retains what | Why it is missed |
|---|---|---|
| A preview or row-sampling command | Real rows, in the terminal scrollback | It is the fastest way to check a column, so it is the first thing anyone does |
| CI job logs | Everything the run printed, for the CI system's retention period, readable by everyone with repository access | Nobody thinks of a build log as a data store |
| `--debug` output | Full SQL, plus error messages that can embed values from the failing row | Enabled precisely when something is already going wrong |
| A notebook or scratch file | Rows, saved to disk, occasionally committed | Feels like a workspace, behaves like a copy |
| An agent transcript or chat log | Any value pasted or returned, retained under a policy you do not control | Not a system anyone maps as holding data |

Two rules that cost nothing:

- **Prefer aggregates over samples.** A count, a null rate, a distinct count, a min and max of lengths, or a regex match rate answers nearly every real question about a column without returning a value. Reach for a sample only when the aggregate genuinely cannot answer it, and then say why.
- **Do not sample a free-text column to determine whether it holds anything sensitive.** Sampling it means reading the sensitive data yourself and copying it into a transcript. Unstructured text has no schema, so it can hold anything anyone typed: identifiers, contact details, credentials. Treat it as sensitive by default and ask.

Error messages deserve their own mention: several engines can include a value in a message — a cast failure naming the offending string, a constraint violation quoting a key, a projection-constrained column surfacing a value in a rare error path. An error message is not a safe channel.

---

## 6. PR bodies, commit messages, and summaries

A PR body is world-readable in a public repository and permanent in a private one. A commit message is in every clone forever. A summary to a user goes wherever that conversation is retained.

The failure is always the same and always well-intentioned: someone pastes a row to *demonstrate* a data quality problem. `"here are the 3 rows failing the uniqueness test"` moves regulated data into a system with entirely different access controls and no retention policy, in the course of doing careful work.

Write the shape instead. It is more useful anyway:

> `<n>` rows fail uniqueness on `<column>`. All are from source `<source>`, all created within a two-hour window, and each duplicate pair differs only in `<other_column>`. Values not reproduced here; the failing keys can be retrieved with `<query>` by a role with access.

That tells a reviewer everything the sample would have, and it names where to look rather than carrying the data along.

The same applies to an agent's own summary and to any artefact it writes: a plan file, a scratch note, a generated markdown report. If a value would not belong in the PR, it does not belong there either.

---

## 7. Warehouse retention artifacts

Outside dbt entirely, and therefore outside the DAG's field of view:

- **Time-travel and fail-safe windows** retain the pre-deletion state of a relation for a configured period. A drop is not an erasure.
- **Clones** are independent objects with their own governance. A zero-copy clone taken for a test is a full logical copy.
- **Backups and snapshots at the platform level** retain whatever they captured.
- **The result cache** can return rows from a query run before a policy was applied.
- **Exports, replicas and reverse-ETL destinations** are copies in systems the warehouse does not govern.

None of these appear in `dbt ls`. When enumerating where a value exists — for a deletion request, for a retention question, or for an incident — say which of these classes you could not enumerate rather than presenting the DAG as complete. See `deletion-and-retention.md`.
