# Choosing a modelling shape, without dogma

Four families of answer to "how should this be structured": a star schema, one big table, a Data Vault, and normalised relations. Every one of them is correct somewhere. This document exists so the choice is made against the consumer and the cost, rather than against whichever methodology the last person read about.

The honest summary up front: **the argument has moved, because the assumptions the star schema was designed against no longer hold on a columnar engine.** Storage is cheap, low-cardinality repeated values compress to nearly nothing, and joins are no longer the dominant cost of a query. That does not make the star schema obsolete — its remaining advantages are about *correctness* and *reuse*, not speed — but it does mean a performance argument for normalising is usually no longer valid, and a performance argument for denormalising is real but smaller than its advocates claim.

## The comparison

| | Star schema | One big table | Data Vault | Normalised (3NF-ish) |
|---|---|---|---|---|
| **One row is** | A fact event, with dimension keys | A fact event with all dimension attributes inlined | A business key, a relationship, or a versioned attribute set | An entity in a relation, minimally redundant |
| **Consumer it serves** | Analysts and BI semantic layers writing joins | Humans and join-averse tools reading one relation | Nothing directly — it is an integration layer | Applications, and the source systems themselves |
| **Query cost** | Good; join planning and shuffle on large facts | Best for a known query shape: one scan, no joins | Worst: reconstructing one entity means joining a hub to many versioned satellites and filtering each | Poor for analytics: many joins, several of them fan-out risks |
| **Storage cost** | Lowest of the analytical shapes | Substantially higher — dimension attributes are repeated on every fact row, so a low-cardinality attribute inflated across millions of rows costs real space even after columnar compression softens it | High: every attribute change is a new row, by design | Lowest |
| **Cost of an attribute change** | Update one dimension row | Rebuild or patch every fact row carrying it | Insert a satellite row. Cheapest of the four | Update one row |
| **Cost of adding a source** | Conform its dimensions, then extend or add facts | Rebuild the wide table with new columns | Add hubs, links, satellites. Existing structures untouched | Schema change, usually invasive |
| **Fan-out risk** | Low: separate grains live in separate relations | **High**: mixing two grains in one wide relation is undetectable by inspection | Low in the vault, moved into the layer built on top | High and constant |
| **Conformed dimensions** | Native — one shared dimension, reused | Not expressible: every wide table carries its own copy | Native at the business-key level | Not a concept |
| **History** | Type 2 where chosen, per attribute | Frozen as of build time unless resolved as-of-event deliberately | Complete for everything, unavoidably | None |
| **Audit / lineage evidence** | Partial | Weak | Strongest: insert-only, source-stamped | None |

The performance argument for a wide table is worth stating carefully, because it is the point most often used as a justification and most often overstated. On columnar warehouses a denormalised single table is generally faster than the equivalent star schema on BI-style queries — the join elimination is real, and the gap is widest on engines most tuned for wide scans. Treat any specific speedup figure as workload-dependent: it varies by warehouse, query shape, and clustering, and a benchmark only measures the queries someone chose to run. The more important point is what a speed benchmark cannot capture — **it measures only queries that were already correct**, and the failure mode of a wide table is a fast query returning a wrong number. If you cite a number, cite one you measured on this project's own workload, not a general figure.

## How to actually decide

Ask the four questions in this order. The first one that produces a clear answer decides it.

**1. Who reads this, and can they write a join correctly?** If the consumers are other models, keep it narrow with dimensions: those consumers need different attribute subsets and will each aggregate differently. If the consumers are people or a BI layer, and the alternative is every consumer independently writing the same five joins, go wide. Not because joins are slow, but because five people will not all write them identically, and one `left join` written as an `inner` is a silently different population.

**2. Does any attribute in scope need as-of-event semantics?** A wide table freezes every absorbed attribute as of build time. That is a type 1 decision for each of them, made implicitly. If even one attribute needs history, either resolve it as of the event when building the wide table and document that you did, or keep that attribute in a separate versioned dimension and accept the join.

**3. Is more than one grain involved?** If a request wants both order-level and shipment-level measures in one relation, a wide table is the wrong shape and no amount of care makes it right — the measures on the coarser grain will repeat and sum wrong. Two facts at two grains, aligned by the consumer on shared attributes, is the correct answer, and it is correct in every one of these paradigms.

**4. How many sources, how volatile, and who audits it?** A dozen volatile sources with a regulatory obligation to prove what was known when is the case Data Vault was designed for, and it is the only case where its overhead pays. Below that, a vault is an entire extra modelled layer with its own pipelines and discipline, underneath the dimensional layer you will still have to build — because a vault is not queryable by humans, which is not a criticism, it is its design.

## What each shape actually costs you, stated as a commitment

Choosing a shape is choosing a permanent obligation. Write the obligation into the design note so the next person sees it as a decision rather than an accident.

- **Star schema**: every consumer must join correctly, and every versioned dimension must be joined on its validity window as well as its key. You are betting on consumer discipline. Mitigate it by shipping views that pre-join the common paths.
- **One big table**: the column list grows monotonically, because removing a column from a consumer-facing relation is a breaking change. An attribute change means rebuilding facts rather than updating a dimension row. And the grain is easier to break — every attribute added is another chance to fan out. **Wide by design is a decision; wide by accretion is how a fact ends up at a grain nobody can state.**
- **Data Vault**: two modelled layers to maintain, and a hard requirement that the business keys are genuinely agreed, because a hub with two competing key definitions for the same concept is worse than no hub. You also inherit the point-in-time and bridge machinery needed to make it readable.
- **Normalised**: you have chosen source-shaped data for an analytical consumer. Legitimate for a staging layer, where 1:1 with the source is the point. Not a mart.

## The pragmatic answer most projects land on, and why it is not a cop-out

A dimensional core — facts at their atomic grain, conformed dimensions, history where history was requested — with **wide serving relations derived from it** for specific high-traffic consumers.

The reason this is not a compromise but the actual answer: the two shapes are optimising different things and they do not conflict when one is derived from the other. The core provides correctness, reuse, and one place a definition lives. The serving relation provides a single object a person can read without knowing the join graph. What breaks the arrangement is building the serving relation *independently* of the core — then you have two implementations of the same logic and the near-duplicate problem the main skill warns about.

**The version of this that fails**: a wide table per dashboard, each built from sources directly. Six months later, four of them compute the same measure and three agree. Nothing errored.

## When the answer is "not a model at all"

Two cases worth checking before choosing any shape, because both are cheaper than every option above:

- **The question is a metric definition, not a dataset.** If the request is a formula over an existing fact, and the underlying grain and dimensions already exist, then what is needed is a governed metric definition — in a semantic layer, or at minimum a single named, version-controlled expression — not a new relation. A metric defined once and consumed everywhere is the mechanism that stops the same measure being recomputed differently in four dashboards. A new model that hard-codes the formula at one grain is the opposite: it fixes the formula at that grain and cannot be re-aggregated.
- **The question is asked once.** Exploratory work and one-off analysis do not need a model. A model is a maintenance commitment: it must build on a schedule, keep passing tests, survive upstream changes, and be understood by whoever inherits it. Paying that for a question with no second occurrence is a net loss, and the resulting object is the hardest kind to retire, because nobody can prove it is unused.
