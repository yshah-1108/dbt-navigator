# Derived values: hashing, pseudonymisation, tokenisation, anonymisation

The cheapest way to secure a column is not to carry it. The second cheapest is to carry something derived from it that cannot reconstruct it. This document is about the gap between those two, because the derived value is where confident mistakes happen — most of them variations on **treating a hash as anonymisation**.

**These are technical distinctions with legal consequences.** Whether a given derivation discharges an obligation is a determination for the people who own it, and this skill does not make it. What follows is the engineering: what each technique actually does, and which ones do not do what their name suggests.

---

## The four terms, and why they are not synonyms

| Technique | What it does | Reversible by whom | What it is usually good for |
|---|---|---|---|
| **Anonymisation** | Removes the link to a person such that no one can reasonably restore it | Nobody, if genuinely achieved | Analysis where individual-level joins are never needed |
| **Pseudonymisation** | Replaces an identifier with a stable substitute, keeping the link restorable with additional information | Anyone holding the additional information — the key, the salt, the mapping table, or a list of candidate inputs | Joining across systems without carrying the identifier |
| **Tokenisation** | Substitutes a value with a token that has **no mathematical relationship** to it, resolvable only through a token vault | Whoever can reach the vault | The same as pseudonymisation, with a much stronger reversal barrier |
| **Hashing** | Applies a one-way function to produce a fixed-length digest | Anyone who can guess the input, plus anyone holding the secret if one was used | A join key. **Not** a privacy control on its own |

The distinction that matters operationally: **anonymisation is a property of the whole situation, not of a column.** A column can be perfectly anonymous in isolation and identifying in combination with three others in the same table. Quasi-identifiers — a birth date, a postcode, a role title, a timestamp of an event — do not identify anyone alone and frequently identify one person together. Removing the name and keeping five quasi-identifiers is not anonymisation, and calling it that is the mistake with the largest gap between how it reads and what it is.

---

## Why a hash of an identifier is reversible

A hash is one-way with respect to *arbitrary* inputs. It is not one-way with respect to inputs drawn from a set you can enumerate.

The attack is not clever. Take the set of candidate inputs, hash each one, compare. If the identifier space is small or structured, this is cheap:

| Identifier | Why the space is searchable |
|---|---|
| A national identification number | A fixed number of digits, often with structure and checksums narrowing it further. The whole space is enumerable |
| A phone number | Bounded by country and format. Enumerable |
| An email address | Not enumerable in the abstract, but a breach list, a marketing list, or the organisation's own upstream table is a candidate list — and matching against a candidate list is the entire attack |
| A postcode, a date of birth, a boolean, a low-cardinality category | Trivially enumerable. Hashing a boolean produces exactly two distinct digests |
| A row identifier from a sequence | Enumerable by counting |

So the rule: **a hash of a low-cardinality or structured identifier is a lookup away from plaintext.** The digest is longer and less readable, which makes it feel protective, and that feeling is the hazard. A deterministic hash is a stable, linkable key — which is exactly why it is useful for joining, and exactly why it remains personal data.

Two things follow that surprise people:

- **A per-row salt stored next to the hash does not fix it.** The brute-force search is still feasible; it just gets slower, because each candidate must be tried against each salt rather than once against all of them. Slower is not the same as infeasible.
- **A salt you throw away turns the column into noise.** If nobody can reproduce the digest, the column cannot be joined, cannot be verified, and cannot be matched — at which point it holds no information and the honest move is to drop it rather than retain a column of random strings that reads as data.

### What actually raises the barrier

- **A keyed hash — HMAC with a secret held separately from the data.** The secret is the additional information; without it, candidate hashing has no anchor. This is the one that is worth the effort.
- **A pepper**: random data added before hashing that, unlike a salt, is **not** stored alongside the hashes and lives in a separate secure location. Note the trade explicitly: if you need consistent digests across systems, the secret must be shared with those systems, and every place it is shared is a place it can leak.
- **An input that is not enumerable**, and ideally one that rotates, so there is no stable cross-period key to correlate against.
- **Tokenisation**, where the substitute has no mathematical relationship to the input at all. There is nothing to compute, so there is nothing to brute-force. The barrier becomes access to the vault, which is an access-control problem you can actually reason about.
- **Modern algorithms.** MD5 and SHA-1 are unsuitable for this purpose. If the project's surrogate-key macro uses one of them — several do, by default, because a surrogate key is a *uniqueness* tool and not a privacy tool — then **a surrogate key built over a personal identifier is a reversible hash of that identifier**, sitting in every model downstream. That is a real and common finding worth reporting rather than assuming someone considered it.

  Verify it rather than assuming either way, because the default is usually MD5 and the call is two hops deep. `dbt_utils.generate_surrogate_key` delegates to `dbt.hash`, whose default implementation in dbt's global project is literally `md5(cast(<field> as <string type>))` — so on a stock installation the most widely used surrogate-key macro in the ecosystem is an MD5 of its concatenated inputs. An adapter may override `hash`, which is exactly why you check the compiled SQL rather than reasoning from the macro name. Two checks, neither needing warehouse access:

  ```bash
  # 1. Which models build a surrogate key, and over which columns?
  grep -rn "surrogate_key\|dbt.hash" models/ | head -40

  # 2. What did it compile to? The hash function is visible in the SQL.
  dbt compile --select <model_name> && grep -io "md5\|sha2\?_*[0-9]*" target/compiled/**/<model_name>.sql
  ```

  If step 1 shows a personal identifier among the key columns and step 2 shows `md5`, you have found a reversible identifier propagated through the DAG. Report it with both outputs; do not silently change the macro, because the key values are load-bearing for every incremental model and BI artifact built on them — see the surrogate-key discussion in `dbt-authoring-sql-models` for why a hash change is a rebuild, not an edit.

### Where the hash gets undone anyway

Even a well-constructed keyed hash fails open in two situations a data model creates by default:

- **The mapping table.** If any relation holds the identifier and its digest side by side — a bridge table, a staging model retained "for debugging", a seed — then every downstream pseudonymised column is one join from identified. That table is the additional information, and it is usually inside the same warehouse under the same grants.
- **Grouping alone re-identifies.** A digest with exactly one row, or a group of one, identifies that individual as effectively as the name would. Uniqueness is the leak. This is what Snowflake's aggregation policies and BigQuery's differential-privacy features exist to address; where the engine has no such mechanism, the control has to be a minimum-group-size filter you write and someone reviews.

---

## Choosing a derived value for a real request

Most requests that appear to need a sensitive column need something derived from it. Ask what the column is *for* before propagating it, and match the derivation to the actual use:

| The stated need | What is actually needed | A derivation that serves it |
|---|---|---|
| "Join these two systems" | A stable, matching key | A keyed hash or a token, applied **identically on both sides** — and note that this forces the secret to be shared between them |
| "Count people in a segment" | A count, at a grain | A boolean or a category, with the identifier omitted entirely |
| "Group by location" | A region | The coarsest geography that answers the question. A postcode is a quasi-identifier; a country is generally not |
| "Age analysis" | A band | A bucket. Not a birth date, and not an age in years for very old or very young values, which are themselves distinguishing |
| "Contact-domain analysis" | The domain | The part after the separator. The local part is the identifier |
| "Verify a value someone supplies" | A comparison | A keyed hash. The verifier hashes the candidate and compares digests; the stored value is never needed |
| "Debug a specific record" | To find one record | The internal key, not the personal identifier. Filter on the surrogate key |
| "Show it in a report" | Usually not the value | Ask who reads the report, and route to whoever owns the decision |

A derived value that cannot reconstruct the original inherits no obligation. One that can — which includes most hashes of most identifiers — inherits the same obligation as the column it came from, and must carry the same classification. **Recording a hashed identifier as unclassified because "it's hashed" is the single most common classification error**, and it propagates: every downstream copy of that column is now unclassified too, on the strength of one wrong judgement made once.

---

## Where to compute it

Two mechanical points that decide whether the derivation actually removes the exposure:

- **Derive as far upstream as possible, ideally in the source-facing layer, and do not carry the original alongside it.** A staging model that selects both the identifier and its digest has not reduced anything — it has published the mapping table described above. If the original must exist in one place, that place should be one relation with narrow grants, and nothing downstream should select from it.
- **A secret in the SQL is not a secret.** A salt or key written into a model file is in git, in the compiled artifacts, in the manifest, and in the docs site. It has to come from the platform's secret mechanism — an environment variable resolved at runtime, or a warehouse-side function that holds the key. Verify where the compiled SQL ends up before deciding a value is hidden; see `leak-surfaces.md`.

Both of these are ordinary modelling decisions with an unusual property: getting them wrong produces a pipeline that looks protected, passes every test, and is not.
