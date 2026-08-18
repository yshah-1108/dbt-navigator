# References

Copy to the path you set in `context.references` and replace the entries.

**This is an index, not a library.** Each entry says which question the document answers so an agent can decide whether fetching it is worth the tokens. Never paste document contents into the repository: copies drift from the source, bloat every session that reads them, and are the most common way confidential material ends up somewhere it should not be.

## How to write an entry

Four fields. The **Answers** column is the one that matters — it is what lets an agent skip a document instead of reading all of them.

| Field | Rule |
|---|---|
| Document | Its real name, so a human can find it if the link dies. |
| Where | A URL, or a path if it is in the repo. |
| Answers | The specific question(s). Not a topic — a question. "The revenue recognition policy" is a topic; "when revenue is recognized for multi-month campaigns" is a question. |
| Access | Note if it needs permissions an agent may not have, so a fetch failure reads as expected rather than as a broken link. |

## Runbooks and operational docs

| Document | Where | Answers | Access |
|---|---|---|---|
| `<name>` | `<url or path>` | `<the question it answers>` | `<open / SSO / restricted>` |

## Policies and compliance

Retention rules, data-handling policy, regulatory obligations. These constrain what an agent may do, so they are worth listing even when access is restricted — knowing a policy exists changes behavior.

| Document | Where | Answers | Access |
|---|---|---|---|

## Specs and designs

Source system docs, vendor API references, ERDs, upstream data contracts. Most useful for questions about *why* the source looks the way it does.

| Document | Where | Answers | Access |
|---|---|---|---|

## Ticketing and history

Where work is tracked, and the query or filter that finds a model's history. This is often more useful than a document, because it dates decisions.

| System | Where | How to search it |
|---|---|---|
| `<tracker>` | `<url>` | `<the search or JQL that finds work for a given model>` |

---

## What not to list

- **Anything derivable.** Column lists, lineage, run history, schedules. The tool is the reference.
- **Documents nobody maintains.** A link to a two-year-old spec that no longer matches the pipeline is a trap, not a reference.
- **Whole wikis.** "The wiki" is not an entry. A specific page that answers a specific question is.
- **Secrets, credentials, or connection strings.** Not here, not anywhere in the repository.

## Keeping it honest

A dead link is worse than a missing entry, because it implies an answer exists. When a document moves, fix the entry; when it stops being true, delete it. If an agent reports a reference it could not fetch, treat that as a bug in this file.
