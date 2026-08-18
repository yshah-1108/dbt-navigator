# Entity resolution and identity stitching

Two sources both know about a customer. Neither carries the other's identifier. Deciding that record A and record B are the same customer is **entity resolution**, and it is the one part of unifying sources where the correct answer depends on a tolerance for error that only a person can set.

Read this when the union or the join needs a key that does not exist in either source. If both sources already carry a shared identifier that is genuinely reliable, you do not need any of this — verify the reliability and move on.

The failure signature is the same one that governs the whole skill: **every outcome looks plausible.** Over-matching collapses two customers into one and the total is merely low with one unusually large customer. Under-matching splits one customer into two and the total is right while every per-customer figure is wrong. Neither errors.

## 1. Establish what you are resolving, and what error costs

Before any matching logic, three answers. None is derivable, all three change the implementation, and getting them after the fact means rebuilding.

| Question | Why it decides the design |
|---|---|
| **What is the entity, exactly?** A person, a household, a legal entity, a billing account, a logged-in user? | These are different populations with different counts. "Customer" is usually at least two of them, and the requester holds one |
| **Which error is worse: a false merge or a false split?** | This sets the matching threshold, and it is a business decision. A false merge in a billing context sends one party's data to another; a false split in a marketing context sends two messages to one person. The costs are not symmetric and they are not comparable |
| **Is the result allowed to change?** Can a pair resolved as the same entity today be split tomorrow when better evidence arrives? | If not, resolution must be append-only and every decision is permanent. If so, downstream aggregates restate whenever the resolution improves, and someone must be told that is expected |

Write the answers into the model's description. A resolution model without a stated error preference is a model whose threshold was chosen by whoever wrote it, silently.

## 2. Deterministic matching first

Exact agreement on an identifier that is meant to be unique: a shared account identifier, a normalised email, a government-issued number, a device identifier.

High precision, low recall. It misses real matches and rarely invents false ones — **rarely, not never**, and the exceptions are worth knowing because each one is a specific class of false merge:

- **A shared placeholder value.** An empty string, `unknown`, `none`, `n/a`, `test@example.com`, a default identifier the source system emits when the field was not collected. Every record carrying it matches every other record carrying it, producing one enormous false entity. **Exclude known placeholder values explicitly before matching**, and check for them rather than assuming: a value appearing on thousands of records is a placeholder, not a coincidence.
- **A genuinely shared attribute.** A household email, a shared business phone, a company-wide billing address. The attribute is unique in the source's intent and not in reality.
- **A reused identifier.** An identifier retired and reissued to a different entity. Rare and expensive, and only detectable if the source carries dates that make the reuse visible.

```sql
-- Placeholder detection: run before choosing a matching key.
-- A value on many records is not a coincidence.
select <candidate_key>, count(*) as n
from <source_relation>
where <candidate_key> is not null
group by <candidate_key>
having count(*) > <threshold>
order by n desc
limit 50
```

Normalise before comparing, and normalise identically on both sides. Case folding, whitespace trimming, punctuation removal, and any format-specific normalisation. The normalisation is part of the matching rule and belongs in one place, applied to every source, or two sources normalised differently will fail to match for a reason nobody can see in the join condition.

**Null keys never match themselves.** A null on both sides of an equality is not a match, so records with a missing key silently fall out of a join and duplicate on a merge. If nulls must be treated as equal, the portable and plan-friendly form is an explicit disjunction:

```sql
on (a.<key> = b.<key> or (a.<key> is null and b.<key> is null))
```

`is not distinct from` expresses the same thing more clearly and is available on several engines, but on at least two major columnar warehouses it prevents the planner from using a hash join — degrading to a full comparison of both sides — so it is a readability win that can be a serious performance loss in a join condition. Prefer the disjunction in joins; use `is not distinct from` in a `select` or a `where` where the plan is not at stake. Some engines also offer a dedicated null-safe equality operator or function; check `project.warehouse` before relying on either.

## 3. Hierarchical (waterfall) matching

The practical pattern for real data: an ordered sequence of rules, strongest first, where a record is matched by the first rule that fires and is then removed from consideration.

```sql
-- Structure, not a template: each tier is a CTE producing (left_id, right_id, tier).
-- Records matched in tier 1 are excluded from tier 2 by an anti-join.
with tier_1 as (          -- exact normalised account identifier
    select l.record_id as left_id, r.record_id as right_id, 1 as match_tier
    from left_records  as l
    join right_records as r
        on l.account_identifier_normalised = r.account_identifier_normalised
    where l.account_identifier_normalised is not null
),

tier_2 as (               -- exact normalised email, for records tier 1 did not match
    select l.record_id, r.record_id, 2
    from left_records  as l
    join right_records as r
        on l.email_normalised = r.email_normalised
    where l.email_normalised is not null
      and l.record_id not in (select left_id  from tier_1)
      and r.record_id not in (select right_id from tier_1)
)
-- further tiers, then union all
```

Three properties make this worth the verbosity:

- **The tier number is retained on every match**, so every downstream consumer can see how confident the link is and can exclude weak tiers. A resolution model that does not expose which rule matched cannot be audited, and the first disputed match becomes an investigation from scratch.
- **Tier ordering is an explicit statement of trust**, reviewable in a pull request rather than buried in a `coalesce`.
- **Exclusion between tiers is what keeps the output at one row per pair.** Without it, a pair matching on both account identifier and email appears twice, and every measure joined through the crosswalk doubles for exactly those entities. This is the most common bug in a waterfall implementation and it is invisible in a spot check, because it affects only the records that matched twice.

`not in` against a subquery has a null trap of its own — if the subquery returns any null, the predicate is never true and the tier matches nothing. Use `not exists`, or guarantee the subquery's column is non-null. See the null discussion in `dbt-authoring-sql-models`.

**Watch transitivity.** If A matches B on email and B matches C on phone, is A the same entity as C? Chaining links produces clusters, and clusters grow: a single bad link merges two clusters permanently. If the design allows transitive matching, cap the chain length or require two independent pieces of agreeing evidence, and **monitor the size of the largest cluster** — a cluster far larger than any plausible real entity is the signature of a placeholder value that got through.

```sql
-- Cluster size distribution: the cheapest detector of an over-merge
select entity_count, count(*) as clusters
from (
    select resolved_entity_id, count(distinct source_record_id) as entity_count
    from <crosswalk_relation>
    group by resolved_entity_id
) as sized
group by entity_count
order by entity_count desc
limit 20
```

## 4. Probabilistic matching, and why it needs a human

When no shared identifier exists, matching rests on the weight of agreeing evidence across several fuzzy attributes — name similarity, address similarity, partial identifier agreement — combined into a score, with a threshold above which a pair is treated as a match.

**Two things must be true before this is a reasonable choice**, and if either is false, do not build it:

1. **Someone has stated the acceptable error rate, in both directions.** A threshold is a choice about how many false merges you will accept in exchange for how many recovered true matches. There is no threshold that avoids both, and the trade is not a technical question. Presenting a score distribution and asking "where should the line be, and who owns that decision?" is the correct move.
2. **There is a review path for the uncertain band.** The useful output of a scored match is three populations, not two: confident match, confident non-match, and a band in the middle that a person resolves. A design that auto-decides the middle band has silently chosen an error rate; a design that discards it has silently chosen the other one.

What to build if you build it:

- **Store the score and the contributing evidence on every link**, not just the verdict. Without them, no link can be explained and the threshold cannot be re-tuned without recomputing everything.
- **Store the threshold as a versioned configuration value, not a literal in the SQL.** Changing it restates every downstream aggregate, so the change should be reviewable and datable.
- **Anchor with deterministic tiers first.** Probabilistic matching on top of deterministic anchors is a smaller, better-behaved problem than probabilistic matching alone, because the anchors reduce the candidate space and give you a labelled set to sanity-check the score against.
- **Blocking is a correctness concern, not only a performance one.** Comparing every pair is quadratic and infeasible at scale, so candidates are restricted to those agreeing on some coarse key. That key's choice determines which true matches are *unreachable* — anything disagreeing on the blocking key can never match, at any threshold. State the blocking key and what it structurally excludes.

Fuzzy string similarity functions are strongly engine-specific: available functions, their names, and their semantics differ, and several are not available at all without an extension. Check `project.warehouse` and name the function you used; a model that silently depends on one engine's similarity implementation is not portable, and its threshold does not transfer.

**Be honest about what this cannot do.** Probabilistic matching produces a defensible guess. It does not produce truth, and it should not be used where a wrong answer has a legal or financial consequence for an individual. Where personal data is involved, the retention and access consequences of building an identity graph are their own problem — see `dbt-handling-sensitive-data`.

## 5. Survivorship: choosing the winning attribute value

Two matched records disagree about an attribute. Choosing the value that survives is a separate decision from choosing that the records match, and conflating them is why "golden record" logic becomes unmaintainable.

**Decide survivorship per attribute, not per record.** Picking one record as the winner and taking all its values discards good values from the loser and is almost never what anyone wants: the system with the most reliable legal name is frequently not the system with the most current contact details.

| Rule | Use when | Failure mode it has |
|---|---|---|
| **Source priority** | One system is authoritative for this attribute by policy | A stale value from the priority source beats a fresh value from another. Combine with a recency floor if that matters |
| **Most recent update** | The attribute genuinely changes and the latest value is the truth | Requires a trustworthy per-attribute update timestamp. A row-level timestamp does not tell you which attribute changed, so a touched row can promote an unchanged stale value |
| **Most complete** | Values differ mainly by being partially populated | "Longest string wins" promotes junk: a padded value, a concatenated blob, a value with a note appended |
| **Most frequent** | Several sources independently agree | Correlated sources are not independent evidence. Three systems fed by the same upstream vote as one |
| **Non-null preference** | The only disagreement is presence versus absence | Fine, and it is the one rule that needs no justification. Make sure absent means absent and not a placeholder |

Two rules that apply regardless:

- **Record which source won, per attribute.** One column per contested attribute holding the winning source's name, or one relation of (entity, attribute, source, value). The first disputed value will be disputed, and without provenance the answer is "the SQL decided" — which does not resolve anything and does not survive an audit.
- **A survivorship rule is a business rule.** Write it in the model's description with its owner, in the same place the matching tiers are documented. A rule chosen by whoever wrote the `coalesce` will be silently changed by whoever edits it next.

## 6. The ID crosswalk as its own model

Build the resolution as a **crosswalk relation**, not inline in the model that needs it.

> **Grain:** one row per source system per source record identifier.
> **Columns:** `source_system`, `source_record_id`, `resolved_entity_id`, `match_tier`, `match_score`, `resolved_at`.

Why it earns its own model:

- **Every consumer resolves identity identically.** Inline resolution in three models is three implementations, and they will disagree — the same near-duplicate problem as any other duplicated logic, with a worse symptom, because the disagreement is in the population rather than in a measure.
- **It is testable in isolation.** `unique` on (`source_system`, `source_record_id`) is the test that proves one record maps to exactly one entity. Without the crosswalk as a relation, that assertion has nowhere to live, and a record mapped to two entities fans out every join through it.
- **The resolution can be reviewed and corrected as data.** An override relation unioned in at the highest tier lets a human decision be recorded once and applied everywhere, which is the only sustainable way to handle disputed matches.
- **Its churn is measurable.** Comparing today's crosswalk to yesterday's tells you how many records changed entity, which is the signal that resolution logic drifted or a source changed format. A large jump with no code change is an incident.

Tests worth having on the crosswalk itself:

```sql
-- 1. One entity per source record. Any output is a fan-out waiting to happen.
select source_system, source_record_id, count(distinct resolved_entity_id) as entities
from <crosswalk_relation>
group by source_system, source_record_id
having count(distinct resolved_entity_id) > 1

-- 2. Every source contributes. A source with no rows stopped being resolved.
select source_system, count(*) as records, count(distinct resolved_entity_id) as entities
from <crosswalk_relation>
group by source_system

-- 3. Collapse ratio per source. A ratio far from its historical value means
--    the matching rules started behaving differently.
--    records / entities: near 1 means little merging, high means aggressive merging.
```

The second and third queries are the ones to run *before and after* every change to the matching rules. A rule change that improves recall and quietly merges two large real entities shows up in the collapse ratio and in nothing else.

## Where this connects

- The union-versus-join decision comes first: entity resolution is what makes a join possible when no shared key exists, and it does not make a union safe. Both are in the parent skill.
- Deduplication **within** one source is a different problem with a different remedy — see the deduplication discussion in `dbt-authoring-sql-models`, and be sure the ordering is total.
- Once the crosswalk exists, the conformed-dimension work is ordinary modelling: one dimension keyed on `resolved_entity_id`, with survivorship applied per attribute.
