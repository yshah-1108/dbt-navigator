# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Versioning policy

Read this before assuming an update reached you.

**Claude Code and Cursor pin on the `version` field in the plugin manifest.** An
install does not track the default branch — it tracks a version. A fix shipped
without a version bump never reaches anyone who already installed. So every
user-visible change gets a release, and `version` is identical in all four of
`.claude-plugin/plugin.json`, `.cursor-plugin/plugin.json`,
`.claude-plugin/marketplace.json`, and `package.json`. CI asserts that they
agree.

What the numbers mean for a library whose artifact is guidance:

| Bump | Means |
|---|---|
| **Major** | A skill was removed or renamed, or a contract field changed shape. Anything that can break an existing `conventions.yml` or a reference to a skill by name |
| **Minor** | A skill or sub-document was added, or guidance changed in a way that changes what an agent does |
| **Patch** | Wording, a typo, a broken link, a corrected count. Nothing that changes behaviour |

**A guidance change is a behavioural change.** Rewriting a section so an agent
reaches a different conclusion is a minor bump even though no code moved. Treat
these releases the way you would treat a dependency upgrade that runs in
production, and read the diff.

## [0.1.0] — Unreleased

Initial public release.

- 27 skills covering the dbt development lifecycle — orient, decide, build,
  prove, ship — plus diagnostics and reference, with `dbt-navigating-skills` as
  the router that decides what to read for a given task.
- `AGENTS.md`: 18 universal rules, 11 contract-driven ones, and a behaviour
  contract covering intellectual honesty, scope discipline, the derive-versus-ask
  discipline, and adversarial self-review.
- `conventions.yml` contract with a JSON Schema and worked examples, so a team
  states its taxonomy, layer rules, environment detection, schedules, and test
  policy once instead of editing skills.
- Installs as a plugin on Claude Code and Cursor, via `npx skills add` for any
  agent supporting the Agent Skills standard, and through `AGENTS.md` elsewhere.
- Four read-only subagents for context-heavy investigation.
- CI enforces the portability and consistency properties the library claims,
  including that no skill carries an organisation-specific identifier and that
  every manifest agrees on name and version.
