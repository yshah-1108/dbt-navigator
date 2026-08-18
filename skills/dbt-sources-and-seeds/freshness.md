# Source freshness

How freshness is calculated, how to choose and shape `loaded_at_field`, how to set thresholds that mean something, where to put the config, how to run and debug it, and what it structurally cannot detect. Read [SKILL.md](SKILL.md) first for what a source is; this document is what you consult once you are configuring or debugging freshness specifically.

## Contents

- [Two ways freshness is calculated](#two-ways-freshness-is-calculated)
- [Choosing `loaded_at_field`](#choosing-loaded_at_field)
- [`loaded_at_field` accepts an expression](#loaded_at_field-accepts-an-expression)
- [Limiting the freshness query's scan](#limiting-the-freshness-querys-scan)
- [Thresholds](#thresholds)
- [Source-level vs table-level](#source-level-vs-table-level)
- [One source, multiple loaders](#one-source-multiple-loaders)
- [Running and debugging](#running-and-debugging)
- [Building only what got new data](#building-only-what-got-new-data)
- [What freshness cannot detect](#what-freshness-cannot-detect)

## Two ways freshness is calculated

Before choosing a `loaded_at_field`, know that on some adapters you can omit it — and that the two mechanisms fail differently.

| Mechanism | How it works | Trade-off |
|---|---|---|
| **Column-based** — `loaded_at_field` set | Runs `select max(<field>) ... from <relation>` | Measures the data. Costs a scan, and needs a column that means what you think |
| **Metadata-based** — `loaded_at_field` omitted | Reads the warehouse's own last-modified metadata | Free and needs no column. Measures the *object*, not the data |

Metadata-based freshness is supported on a subset of adapters — verify support for the adapter in use rather than assuming, since the list has grown over time. If the adapter does not support it, omitting `loaded_at_field` means no freshness is calculated at all, silently.

The metadata mechanism's specific weakness, which is documented behaviour and not a bug: the timestamp it reads advances on **any** modification to the object, not only on new data. Depending on the platform that can include DDL changes, unrelated DML, and the platform's own background maintenance. So a table nobody has loaded in a week can report as fresh because something touched its metadata.

Where that matters: on a table that is loaded rarely, metadata-based freshness is the least trustworthy — precisely the case where staleness matters most. Rule of thumb: **metadata-based freshness for broad, cheap coverage across many tables; a column-based `loaded_at_field` for the tables an alert would actually wake someone for.**

If a freshness block exists but specifies neither `warn_after` nor `error_after`, dbt calculates nothing. That is the quietest way to have monitoring that does not exist.

## Choosing `loaded_at_field`

Freshness needs a column that reliably advances when data lands. Do not guess. Find the candidates:

```sql
select column_name, data_type
from <database>.information_schema.columns
where table_schema = '<schema>'
  and table_name = '<table>'
  and (
      column_name ilike '%load%'
      or column_name ilike '%sync%'
      or column_name ilike '%updated%'
      or column_name ilike '%insert%'
      or data_type ilike '%timestamp%'
  )
order by column_name
```

Then confirm the candidate moves, with `select max(<candidate>) from <database>.<schema>.<table>`. Use an explicit database and schema — never `ref()` or `source()` in a validation query. See `dbt-environments`.

Not every timestamp qualifies. The distinction is **load time versus event time**:

| Column meaning | Valid for freshness | Why |
|---|---|---|
| When the pipeline wrote the row | Yes | This is what freshness measures |
| When the source system last modified the record | Usually | Advances on change, but a table with no changes looks stale |
| When the business event occurred | **No** | A late batch of old events makes a fresh table look stale, and a stalled pipeline delivering backdated rows looks fine |
| When the row was created in the source system | **No** | An append-only table with no new records is indistinguishable from a broken pipeline |

An event timestamp used for freshness produces alerts that fire for correct pipelines and stay quiet for broken ones. That is worse than no freshness config, because the team learns to ignore it.

If there is no load timestamp, configure no freshness and document why — including the actual cadence, so the next person is not guessing:

```yaml
      - name: <table_name>
        # No freshness: no load timestamp column exists.
        # Loaded by <owning system> approximately <cadence>; monitored in <system>, not here.
```

A false freshness config is worse than an honest gap.

## `loaded_at_field` accepts an expression

It does not have to be a bare column name, and three cases need it not to be:

```yaml
        config:
          loaded_at_field: "cast(<date_column> as timestamp)"      # a date column
          # loaded_at_field: "convert_timezone('<zone>', 'UTC', <local_ts>)"   # non-UTC
```

| Situation | Why the expression is needed |
|---|---|
| The column is a `date`, not a `timestamp` | Freshness compares timestamps; a date compares as midnight, making the source look up to a day staler than it is |
| The column is a string in date format | It must be cast, or the check errors on type |
| The column is in a local timezone | Freshness compares against UTC now. An uncast local timestamp produces an offset error equal to the zone difference — which can look like consistent staleness, or hide real staleness by hours |

The timezone case is the one worth naming: a source recorded in a zone several hours behind UTC appears permanently stale by that offset, and the natural fix — widening the threshold — hides genuine lateness by the same amount. Cast, do not widen.

## Limiting the freshness query's scan

```yaml
        config:
          freshness:
            warn_after: {count: 6, period: hour}
            filter: <column> >= <recent boundary expression>
```

`filter` adds a `where` clause to the freshness query only; it does not affect models reading the source. On a large or partitioned table, a `max()` over full history is an expensive query to run on a schedule, and the filter is how to make freshness checking affordable.

The mistake to avoid: a filter narrower than the staleness you want to detect. If the filter restricts to the last two days and the source has been dead for a week, the query returns no rows, and what dbt concludes from an empty result is not something to guess at — verify the behaviour on the version in use before relying on a tight filter to catch a long outage. Keep the window comfortably wider than `error_after`.

## Thresholds

```yaml
        config:
          loaded_at_field: <column>
          freshness:
            warn_after: {count: 36, period: hour}
            error_after: {count: 72, period: hour}
```

Derive thresholds from the **observed** cadence, not the intended one. Systems that "run hourly" have a gap distribution, and the tail is what matters. Measure it with `lag()` over the load timestamp and take the maximum gap, not the mean.

Set `warn_after` above the routine worst case and `error_after` where a human should be woken. Where no contract states a policy, these are a defensible starting point — label them as generic:

| Load cadence | `warn_after` | `error_after` |
|---|---|---|
| Continuous / streaming | 2 hours | 6 hours |
| Hourly | 3 hours | 6 hours |
| Every few hours | 12 hours | 24 hours |
| Daily | 36 hours | 72 hours |
| Weekly | 10 days | 14 days |
| Ad hoc / manual | none | none |

Two adjustments matter more than the multiples:

- **Account for the schedule, not just the interval.** A daily load at 02:00, checked at 01:00, is always ~23 hours stale at its best. A 24-hour threshold fires every day. Threshold against the worst legitimate age.
- **Weekends and holidays.** A source fed by a weekday business process breaches any weekday-calibrated threshold every Monday. Either widen the threshold to cover the gap or document the recurring alert. An alert that fires every weekend is an alert that gets muted.

Configure `warn_after` alone when nobody would act on an error at 3am. An `error_after` on a source with no owner on call produces failed builds and no response.

## Source-level vs table-level

Put freshness at the **source level** when every table shares one loader, one cadence, and one timestamp column. This is the common case for a single connector, and a table added later then inherits monitoring instead of being silently unmonitored.

```yaml
sources:
  - name: <source_name>
    database: <database>
    schema: <schema>
    config:
      loaded_at_field: <shared_column>
      freshness:
        warn_after: {count: 6, period: hour}
    tables:
      - name: <table_a>
      - name: <table_b>
```

Put it at the **table level** when cadences genuinely differ, when only some tables have a usable timestamp, or when one table is critical and the rest are not. Table level can also disable an inherited config:

```yaml
    tables:
      - name: <hourly_table>
        config:
          freshness:
            warn_after: {count: 3, period: hour}
      - name: <ad_hoc_table>
        config:
          freshness: null   # loaded manually; inherited threshold does not apply
```

Prefer source level with targeted overrides. A file where every table restates the same block hides which tables are actually different.

## One source, multiple loaders

A logical grouping of tables loaded by *different* systems is the case that produces wrong config. Sibling-looking tables may live in different databases, carry different timestamp columns, and refresh on different schedules.

Before writing source-level freshness, confirm every table shares the loader — check the physical location and timestamp column per table. Getting it wrong means either false alerts on the tables that do not fit, or no monitoring on the ones you assumed were covered, with YAML that looks complete either way.

## Running and debugging

```bash
dbt source freshness
dbt source freshness --select "source:<source_name>"
dbt source freshness --select "source:<source_name>.<table>"
```

Results are written to `target/sources.json`, containing `max_loaded_at`, `snapshotted_at`, the age in seconds, the pass/warn/error state, and the criteria applied. Read that rather than the console when triaging — it says which threshold was compared against, which is usually the disputed part.

`dbt source freshness` **exits non-zero when a source is stale**, which makes its position in a job a real decision:

| Placement | Consequence |
|---|---|
| First step | Models do not run on stale data. Also: one late source blocks the entire build |
| Last step, or a separate job | Models always run; staleness is reported, not enforced |
| Middle of a job | Everything after it is skipped when any source is late. Rarely what anyone intended |

Pick deliberately. The first is right when wrong-but-fresh output is worse than no update; the second is right when consumers would rather have slightly stale numbers than none. A dedicated freshness job on its own cadence is often the cleanest answer, and it should run at least twice as often as the tightest threshold it checks — a daily check cannot police a six-hour threshold.

## Building only what got new data

With a previous `sources.json` to compare against, freshness results become a selector:

```bash
dbt source freshness                                                   # writes current state
dbt build --select "source_status:fresher+" --state ./prod-artifacts
```

`source_status:fresher+` selects the sources whose `max_loaded_at` advanced since the comparison run, plus everything downstream. Available from 1.1. This is the cheapest way to avoid rebuilding models whose inputs did not change.

Two requirements that are easy to miss: the comparison `sources.json` must exist in `--state`, **and** `dbt source freshness` must have run in the current invocation too, because the selector compares two artifacts. Miss either and the selector matches nothing — which, being only a warning, exits zero and builds nothing. See `dbt-command-reference` on promoting that warning to an error.

| Symptom | Likely cause |
|---|---|
| Fires constantly, data is fine | threshold below the routine worst-case gap, or an event-time column used as `loaded_at_field` |
| Consistently stale by a fixed number of hours | timezone mismatch — a local timestamp compared against UTC now |
| Every Monday | weekday-only loader, weekend-blind threshold |
| Never fires, data is stale | event-time column; a loader that stamps the timestamp regardless of content; or metadata-based freshness picking up a non-data modification |
| Errors on the check itself | `loaded_at_field` missing, or not a timestamp type |
| Reports fresh on a table nobody loads | metadata-based freshness on a rarely-loaded table |
| No result at all for a table | `freshness` block with neither `warn_after` nor `error_after`, config placed at the wrong nesting level for this dbt version, or metadata-based freshness on an unsupported adapter |
| Fresh but empty | freshness measures recency, not completeness |

## What freshness cannot detect

Worth stating as a list, because each one produces a green check over broken data, and no threshold tuning fixes any of them:

| Failure | Why freshness misses it |
|---|---|
| **A load that succeeded with zero rows** | The timestamp advanced. Recency is not volume |
| **A partial load** — a tenth of the expected rows | Same. `max()` only needs one recent row |
| **A load of duplicated rows** | Recency says nothing about uniqueness |
| **Correct volume, wrong values** | Freshness never looks at any column but one |
| **One partition or entity missing** | A single fresh row from any other entity satisfies the check |
| **Schema drift** — a column silently dropped upstream | Not examined |
| **A backfill of old rows** | With a load timestamp, this looks fresh, correctly. With an event-time column it looks stale, incorrectly |

The general shape: freshness answers "did anything arrive recently", and nothing else. Volume, completeness and distribution assertions are separate work — see `dbt-data-quality-triage`. A team that believes freshness covers completeness has a monitoring gap it does not know about, which is worse than a gap it does.

When freshness fails, distinguish a dbt problem from a pipeline problem first: query `max(<loaded_at_field>)` directly against the explicit database and schema. If the warehouse agrees the data is old, the pipeline is the problem and no dbt change fixes it.
