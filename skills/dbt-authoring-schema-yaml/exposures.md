# Exposures: telling the DAG who consumes it

An exposure declares a downstream consumer — a dashboard, a report, a notebook, a reverse-ETL sync, an application — as a node in the DAG. It is the only record of an external consumer that lives inside the repository.

Read `bi.use_exposures` from the contract. If it is `true`, the project has committed to declaring consumers and a new terminal model without an exposure is an omission. If it is `false` or absent, exposures may still be worth introducing, but do not retrofit the whole project as a side effect of an unrelated task.

## Why this matters more than it looks

Six of the skills in this library ask "who consumes this model?" before allowing a change:

- Blast-radius tracing before a rename or removal.
- The dead-model test — a model consumed only by an exposure has no dbt descendants and is emphatically not unused.
- Impact assessment before a grain change.
- Deciding whom to notify when published numbers were wrong.

Every one of those degrades to guesswork when consumers are undeclared. The external catalog may be blind to the BI tool in question, and the warehouse query log is permission-gated and retention-limited. An exposure is the one instrument that is none of those things, because it is checked into the repository next to the model.

The catch: **an exposure only exists because someone wrote it.** Its absence is never evidence that nothing consumes a model — it is evidence that nobody wrote one. Treating a missing exposure as proof of no consumer is the single most common way this feature is misused.

## The minimum that is actually useful

```yaml
version: 2

exposures:
  - name: revenue_weekly_review
    label: Revenue Weekly Review
    type: dashboard          # dashboard | notebook | analysis | ml | application
    maturity: high           # high | medium | low
    url: https://<bi-tool>/dashboards/<id>
    description: >
      Weekly revenue review used by the finance team to close the week.
      Reads the daily grain and aggregates to week in the tool.
    owner:
      name: <team or person>
      email: <contact>
    depends_on:
      - ref('<model_name>')
```

`type`, `name`, and `owner` are required. Everything else is optional and most of it is what makes the exposure worth having.

## The fields that do the work

| Field | Why it matters |
|---|---|
| `owner.email` | The reason to declare an exposure at all: it answers "who do I tell?" without asking around. An exposure with no reachable owner cannot do its main job. |
| `depends_on` | What puts the exposure in the DAG. Use `ref()` for models and `source()` for sources — a hardcoded relation name is invisible to the graph and the exposure becomes decorative. |
| `maturity` | Triage input. A `high`-maturity exposure is a stop-and-coordinate signal; a `low` one is a heads-up. Without it every consumer looks equally critical, which means none of them do. |
| `url` | Lets a reviewer confirm the thing still exists. This is what makes staleness detectable. |
| `description` | Must state what the consumer *does with the data*, not what the model contains. |

## Writing the description

The same rule as model descriptions, pointed the other way: a model description states the entity and grain; an exposure description states **the decision the data supports and who makes it**.

Good — a reader learns what breaks if the model changes:

> Weekly revenue review used by the finance team to close the week. Aggregates the daily grain to week in the tool, so a change to the daily grain changes these totals.

Useless — restates the name:

> Dashboard for the revenue weekly review.

Empty descriptions are worse than absent exposures, because they pass a completeness check while conveying nothing. If you cannot say what the consumer does with the data, that is a question for its owner, not a blank to fill in.

## Verify it landed in the graph

An exposure with a typo in `depends_on` parses fine and silently participates in nothing.

```bash
# does the exposure exist as a node?
dbt ls --resource-type exposure

# what does this model actually feed? the exposure should appear
dbt ls --select <model_name>+ --resource-type exposure
```

If the second command does not list the exposure, `depends_on` is wrong. That is the whole check, and it takes one command.

## Keeping them honest

A stale exposure is actively harmful: it manufactures a consumer that no longer exists and blocks changes for no reason, which is how teams learn to ignore the mechanism entirely.

- When deleting a dashboard, delete its exposure in the same change.
- When a model is renamed, the `ref()` in `depends_on` moves with it — `dbt parse` will fail on a bad ref, so this one is self-enforcing.
- Declare the consumers that would matter if they broke. An exposure per tile is noise; an exposure per reviewed artifact is signal.
- Do not add exposures for consumers you have not confirmed exist. An invented exposure is worse than a missing one.

## What exposures do not do

- They do not restrict anything. Nothing prevents a change to a model an exposure depends on.
- They do not discover consumers. They record the ones a human already knew about.
- They are not a substitute for the query log when the question is "is anything reading this at all," because they only cover what someone chose to write down.
