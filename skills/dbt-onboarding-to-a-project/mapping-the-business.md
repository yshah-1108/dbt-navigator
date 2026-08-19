# Mapping the business behind the models

A DAG tells you which model feeds which. It does not tell you that `stg_salesforce__accounts` and `stg_stripe__customers` describe the *same companies* under different identifiers, that the CRM's "customer" is a signed contract while the product database's is a login, or that `fct_revenue` is what Finance closes the books on while `fct_bookings` is what Sales forecasts from. That is the layer where an agent's mistakes become expensive, because the SQL compiles, the tests pass, and the number is wrong in a way only a person who knows the business can see.

This pass builds that layer. It is the difference between an agent that can edit a model and one that understands what the model is *about*.

## The shape of the answer

You are building three things, in this order. Each one makes the next cheaper.

1. **A source-system inventory** — which operational systems feed this warehouse, and what each one is the system of record *for*.
2. **An entity map** — the business nouns, which datasets represent each, and which representation is authoritative when two disagree.
3. **The join fabric** — how entities link across systems, on which keys, and where the links are known to be imperfect.

Then, per central mart: what it is *for*, who reads it, and which decision it drives. Purpose is the fact that makes everything else interpretable, and it is never in the repository.

## 1. Source systems: what feeds this warehouse

Start from the sources, because every business fact enters through one and the source names carry the vendor vocabulary the rest of the project inherits.

```bash
# every declared source and its tables -- the entry points to the whole project
grep -rh -A2 '^\s*- name:' models --include='*.yml' | head -60
find models -name '*.yml' | xargs grep -l 'sources:' 2>/dev/null
```

Group the sources by the system they come from, then answer per system — and these are **questions for a person**, because no query returns any of them:

| Question | Why it cannot be derived |
|---|---|
| What is this system *for* in the business? | A schema shows tables, never the business process that produces them. |
| What is it the **system of record** for? | Authority is an organizational decision. Two systems holding overlapping data is normal; which one wins is a policy. |
| Who owns it, and who changes its schema? | Upstream ownership determines whether a breaking change upstream is negotiable or an event you absorb. |
| How does data arrive — batch, stream, CDC, manual upload? | Determines whether late-arriving rows and mutable history are expected. Changes incremental strategy. |
| Is it being migrated, deprecated, or replaced? | The single highest-value fact here, and it exists only in someone's head. Building on a system being retired next quarter is wasted work. |

The last one deserves its own emphasis. A project routinely contains two generations of the same source — the old system still landing data and the new one partially built — and nothing in the repository marks which is which. Both look live. Ask.

**Ingestion tooling is worth one question of its own.** Whether sources arrive via a managed connector, an in-house pipeline, or a reverse-ETL loop back into the warehouse changes what "the source changed" means and who can fix it. It is also invisible in a dbt project, which sees only the landed tables.

## 2. Entities: the business nouns and which dataset is authoritative

This is the section that prevents the most expensive class of error, because the same word means different things in different systems and the difference is never in a column name.

For each core noun the business runs on — customer, account, order, subscription, product, campaign, user — establish:

- **What it means here**, in business terms rather than table shape. Not "a row in `dim_customer`" but "an organization with at least one paid contract."
- **Which datasets represent it**, across systems. Usually several.
- **Which one is authoritative**, and for which questions. A CRM may be authoritative for the commercial relationship while the product database is authoritative for usage.
- **The trap** — the thing a newcomer assumes that is wrong.

The traps are the payload. The canonical example, and a version of it exists in almost every company: *a "customer" in the CRM is a signed contract, while a "customer" in the product database is a login. The two counts have never matched and are not supposed to.* An agent that does not know this will "fix" the discrepancy, and the fix is a bug.

Where the same entity appears in two systems, ask the question that decides every future join: **is one a subset of the other, or do they overlap partially?** A subset means a left join is safe. Partial overlap means every join loses rows on both sides, and whether that loss is acceptable is a business call.

## 3. The join fabric: how entities link across systems

Cross-system joins are where business meaning and technical mechanics meet, and where the failures are quiet.

For each link between systems, establish and record:

| Fact | Why it matters |
|---|---|
| The join key, and whether it is a natural or a mapped key | A mapped key means a crosswalk table exists — find it, because hand-rolling a second one is a classic duplication |
| Whether the link is 1:1, 1:many, or many:many | Determines fan-out. A join assumed 1:1 that is 1:many silently multiplies every measure downstream |
| The match rate, and whether unmatched rows are expected | 100% is suspicious, 60% may be entirely normal — but only a person knows which |
| What unmatched rows *mean* | Trials with no CRM record, self-serve signups outside the sales process, test accounts. Each implies a different filter |
| Whether identity resolution happens, and where | If a model reconciles identities across systems, that model is load-bearing and its logic is business policy, not code |

`dbt-unifying-sources` covers the mechanics of building these joins. This pass establishes the *facts* they must respect. Do the map first: a union written without knowing that two sources overlap partially is a union that either drops or duplicates real business events.

## 4. Per mart: what it is for

For the top 10–20 models by fan-out — the ranking step 2 produced — establish the three facts that no tool holds:

1. **What decision it drives.** "Finance closes the month on this" and "an analyst browses it occasionally" call for very different care with the same one-line change.
2. **Who reads it, and how.** A dashboard, a scheduled export, a reverse-ETL sync into an operational tool, a notebook. The DAG and query log tell you *that* something reads it; only a person tells you what breaks in their week when it is wrong.
3. **Which of two similar models is canonical.** Nearly every mature project has `fct_revenue` and `fct_revenue_v2`, or a mart and its "daily" sibling. The names never settle it and the wrong choice is invisible.

An exposure with an empty description gives you the link and not the criticality — the link is derivable, the importance is a question. That gap is exactly what this step closes.

## 5. Sampling the data — as an instrument, not the goal

Reading actual values is how you *check* the business map and find the vocabulary nobody documented. It is a means to the map, not a substitute for it: a thousand row counts still will not tell you which system is the system of record.

Preconditions first, because a profile of the wrong relation is worse than none — it reads as fact. Read **production** with explicit database and schema (`ref()` in a dev target may resolve to a partial build or fall back silently — see `dbt-environments`), and take the relation name from **compiled SQL, not the filename**, since an `alias` or a custom schema macro breaks that assumption (`dbt-gathering-context` §7).

The three queries that teach the most about *meaning*:

```sql
-- 1. Categorical domains: the live vocabulary, which the docs rarely match
select <column>, count(*) as n
from <database>.<schema>.<relation>
group by 1 order by n desc limit 30;

-- 2. Whole rows: what columns mean together, which no per-column stat shows
select * from <database>.<schema>.<relation> limit 5;

-- 3. Cross-system match rate: whether a business link actually holds
select count(*) as total,
       count(<foreign_key>) as has_key,
       count(distinct <foreign_key>) as distinct_keys
from <database>.<schema>.<relation>;
```

What to look for, and what each finding means in business terms:

- **The enum has four live values, not the eleven in the vendor docs.** The other seven are either historical or belong to a product line this company does not use. Which is a question, and the answer is business knowledge.
- **A sentinel appears** — `'UNKNOWN'`, `-1`, `'9999-12-31'`. These are nulls in disguise: they pass every not-null test and break every aggregate. Record the *convention*, since it is usually project-wide.
- **One value is 94% of rows.** That is the default path; the rest are edge cases someone will forget. Worth knowing which are real business cases and which are legacy.
- **Units are wrong from the range.** Amounts averaging 4,000 on a consumer product are cents. A "rate" above 1.0 is not a rate. No documentation states this and every downstream calculation depends on it.
- **History starts later than expected.** A migration discarded what came before. Any year-over-year calculation is wrong before it is written, and *why* the boundary exists is knowledge only a person has.
- **A match rate that surprises you.** This is the fastest way to discover that two systems do not describe the same population.

**Measured distributions are not thresholds.** Two years at 3% null tells you 3% is *normal*, never that it is *acceptable*. That line is a business tolerance: bring the number and ask.

## Recording it

This goes in `context.domain_notes`, whose template (`examples/domain.example.md`) already has the sections — source systems, core entities with a trap column, entity links, canonical metric definitions, timezone rules, known traps, closed decisions. `dbt-deriving-project-context` owns the artifact and its rules.

Three constraints, each preventing a specific failure:

- **Prefer the most specific home.** A fact about one column belongs in that column's dbt `description`, where it versions next to the model and reaches the catalog and the docs site. A reader of the YAML will never think to look in a prose file. `domain.md` is for what spans models or has no node to attach to.
- **Record interpretation, never measurement.** That `amount` is in cents, not its current average. That history starts in 2023 *because of the billing migration*, not `min(date)`. A recorded measurement is a second source of truth that starts disagreeing with the warehouse immediately, and it is the copy that gets believed.
- **Never write a real data value.** A sentinel convention is a fact about the schema; a customer name or an actual revenue figure is data, and copying it into a repository file exports it past whatever grants and masking protected it. See `dbt-handling-sensitive-data`.

**Leave what you could not confirm visibly empty, marked as a question.** An empty "canonical definitions" section is a visible gap someone will fill. A plausible guess is an invisible error that will be cited as fact — and unlike a stale tool reading, a wrong note gives no sign that it is wrong.

## Failure modes

1. **Profiling instead of understanding.** Row counts, max dates and null rates feel like progress and answer none of the questions above. Metadata is the instrument; the business map is the deliverable.
2. **Inventing business meaning from names.** `dim_customer` does not tell you what a customer is here. A definition assembled from naming conventions reads authoritative and is a guess.
3. **Assuming one system is authoritative because it has more rows.** Authority is a policy, not a row count.
4. **Treating two systems' entities as the same population.** The most expensive error available here. Ask whether one is a subset or the overlap is partial, before writing the join.
5. **Reading a discrepancy as a bug.** Two customer counts that never matched may be correct by definition. Classify with whoever owns the definition before "fixing" it.
6. **Profiling dev and believing it.** A partial or 100-row dev copy makes every conclusion worthless, and the numbers look just as real.
7. **Querying by filename on an aliased model.** You read a stale relation or nothing, and "no rows" reads as a finding.
8. **Copying real data into a repository file.** The one irreversible mistake available in this pass.
9. **Doing this for every model.** Twenty central models is a day well spent. Three hundred is a week that teaches less, because nobody reads the result.
