## What changed

<!-- Which skills, docs, or scripts. One line is fine. -->

## Why

<!-- The problem this solves. If it is a guidance change, what was an agent
     doing wrong before? -->

## Type of change

- [ ] Guidance change — an agent will now reach a different conclusion
- [ ] New skill or sub-document
- [ ] Correction — typo, broken link, stale count
- [ ] Tooling, CI, or packaging

## Checklist

- [ ] `python3 scripts/check-skills.py` passes
- [ ] No organisation-specific identifier added: no internal database, schema,
      macro, model prefix, vendor, or industry-specific vocabulary. Values that
      belong to a project belong in `conventions.yml`, not in a skill
- [ ] Any hardcoded value either reads from a contract field or is explicitly
      labelled as an example
- [ ] Warehouse-specific advice is gated on `project.warehouse`, not presented
      as generic dbt guidance
- [ ] No universal rule cited by number — cite its content, since adopters
      renumber `AGENTS.md`
- [ ] Any new sub-document is linked from its `SKILL.md`
- [ ] Any new skill is reachable from `dbt-navigating-skills`
- [ ] Version bumped in all four manifests if this is user-visible, and
      `CHANGELOG.md` updated

## For a guidance change: what is the evidence?

<!-- This library asks agents to measure rather than assert, so it holds
     contributions to the same standard. A row count, a query plan, a failing
     build, a link to dbt's docs, or a case where the current guidance produced
     the wrong result. "This is best practice" is not evidence.

     If the change is based on one project's experience, say so — a caveated n=1
     finding is welcome and useful. An uncaveated one is not. -->
