# Compliance vocabulary an engineer needs to recognise

**Nothing here is legal advice, and this document does not enable you to give any.** Its purpose is narrower and genuinely useful: these terms arrive in tickets, source-system documentation, contracts and Slack threads, and an engineer who cannot recognise them will either ignore an obligation or invent one. Both are worse than routing the question to a human.

The rule for every entry below: **recognise it, say what it appears to imply for the pipeline, and route the determination to whoever owns it.** Never assert that a given design satisfies a regime, and never estimate a deadline.

---

## Categories of regulated data

| Term | What it labels | Why it changes engineering |
|---|---|---|
| **PII** — personally identifiable information | Data identifying a person, alone or in combination | The broadest and vaguest of the three. What counts is contested and context-dependent, which is exactly why an engineer must not adjudicate it |
| **PHI** — protected health information | Health data about an identifiable individual, in regimes that regulate healthcare specifically | Often carries obligations beyond access control: audit trails of *who read what*, and restrictions on which environments may hold it at all. A dev clone may be prohibited outright |
| **PCI** — payment card data | Cardholder data, under a payment-industry standard rather than a statute | The standard is prescriptive about storage and often the answer is that the warehouse **must not hold it**. Truncated or tokenised forms exist precisely so the pipeline never sees the number |
| **Special category / sensitive data** | Attributes given extra protection in several regimes — health, biometrics, ethnicity, religion, sexual orientation, trade-union membership, and similar | The presence of one of these usually raises the required protection above what the project applies by default. A column that looks ordinary — a dietary preference, a support-group membership, a medication — can be one of these by inference |
| **Quasi-identifier** | An attribute that does not identify anyone alone and does in combination | The reason "we removed the name" is not a sufficient statement. See `derived-values.md` |

**Do not classify a column into one of these categories on your own authority.** Say which category it *appears* to fall in, say that the determination is not yours, and ask. That is a useful contribution; a confident label is not.

---

## Concepts from data-protection regimes

These come up constantly and are worth recognising by name. Framed as engineering implications, not as legal statements.

| Concept | What it asks for | The engineering shape |
|---|---|---|
| **Lawful basis / purpose limitation** | Data collected for one purpose may not be freely reused for another | A new mart may be a **new purpose** even though the data is already in the warehouse. "We already have it" is not an answer to "may we use it for this" |
| **Data minimisation** | Hold no more than is needed for the purpose | Directly actionable: do not select the column. See `deletion-and-retention.md` |
| **Storage limitation / retention** | Do not keep it longer than needed | An incremental model has no expiry mechanism. Something must delete |
| **Right of access** | A person may ask what you hold about them | Requires being able to *find* their rows across the pipeline — the same enumeration problem as deletion, minus the deleting |
| **Right to erasure / right to be forgotten** | A person may ask you to remove their data | The conflict with snapshots and irreproducible history. Enumerate, present, escalate |
| **Right to rectification** | A person may ask you to correct it | Corrections propagate only where the pipeline reprocesses. An incremental model may retain the wrong value indefinitely |
| **Right to object / opt out of sale or sharing** | A person may withdraw from certain uses | Usually a filter that must be applied in **every** downstream relation, not just the mart someone remembered. A missed relation is a live violation, not a stale one |
| **Data-subject request deadlines** | Responses are due within a period | You may state that a deadline exists and that it is not yours to interpret. **Never estimate one** |
| **Processor / controller distinction** | Who decides how data is used, versus who acts on instructions | Determines who may authorise a change. Sometimes the answer is that your organisation *cannot* consent to a use on a customer's behalf |
| **Data residency / cross-border transfer** | Data must stay in a jurisdiction, or may cross borders only under conditions | The most directly technical entry here — see below |
| **Automated decision-making** | Decisions made about people by an algorithm may carry extra obligations | A scoring model whose output drives an action about a person may be in scope. Relevant to `dbt-python-models` |
| **Breach notification** | Disclosures must be reported within a window | If you believe a leak has occurred, **report it immediately and do not investigate quietly first**. Time spent confirming is time spent inside someone else's deadline |

---

## Data residency, because it is genuinely a pipeline concern

This is the one item on the list where an engineer can make a technical mistake with immediate legal consequence, so it is worth more than a row in a table.

Warehouses are regional. Several protection mechanisms are region-scoped, and several operations cross regions silently:

- **A cross-region copy can be rejected or can strip protection.** On BigQuery, a cross-region table copy of a policy-tagged table is rejected, and cross-region copies do not carry policy tags. A cross-region move is therefore both a protection change and a residency event.
- **Some governance features are unavailable in some regions.** Verify availability for the project's actual region rather than assuming account-wide parity.
- **Cross-region or cross-cloud replication is a transfer.** Failover replicas, disaster-recovery copies and data-sharing arrangements move data across boundaries by design, and they are outside dbt's field of view entirely.
- **A compute service can be in a different region from the storage.** This bites on Python models specifically: the runtime executing the code is configured with its own region, so a Python model can move data across a boundary that every SQL model in the same project respects. See `dbt-python-models`.
- **A destination outside the warehouse is the least visible case.** An export to object storage, a reverse-ETL destination, or a BI extract lands wherever that system lives.

What to do: **state the regions involved and stop.** "This model reads from a dataset in region A and materialises into region B" is a fact you can establish and a reviewer can act on. "This is fine under the transfer rules" is not a sentence you can write.

---

## How to route, in practice

The routing is the deliverable, and a good one has four parts:

1. **What you established, and how.** "Column `<column>` carries classification `<level>` in the source-facing layer; no masking policy is attached to it in the target relation; the target schema grants read to `<role>`."
2. **What you could not establish.** "I could not verify whether a BI tool exposes this column; `bi.consumers` is not set in the contract."
3. **Which decision is not yours, named as such.** "Whether this column may appear in a relation this group can read is a policy decision."
4. **Who is likely to own it**, if the project records that. Source ownership, a data-protection contact, a security team — and if nothing records it, say that too, because "nobody is recorded as owning this" is itself a finding.

Two things never to write:

- **"This is GDPR compliant"**, or any equivalent for any regime. It is a legal conclusion about an organisation, not a property of a model.
- **"This model contains no PII."** An unsupportable claim, and specifically a claim about *data* made on the basis of *metadata*. Write what you established instead: no classification is recorded, the project records classifications for `<n>` columns elsewhere so the instrument is populated, and source ownership needs to confirm.

The general principle from `dbt-gathering-context` applies with unusual force here: an unverifiable claim must be labelled as one. In this domain, a confident wrong answer does not merely waste someone's time — it ends the investigation.
