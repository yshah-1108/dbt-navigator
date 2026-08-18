# Security Policy

## What this project is

dbt Navigator is a library of Markdown files. It ships no runtime, no service,
and no dependencies that execute in your environment. The realistic security
surface is therefore narrow, but not empty — the skills instruct an agent that
*does* have credentials and shell access, so the content itself is the surface.

## Reporting a vulnerability

Report privately via [GitHub Security
Advisories](https://github.com/yshah-1108/dbt-navigator/security/advisories/new).
Please do not open a public issue for anything in the categories below.

Expect an acknowledgement within 7 days.

## In scope

- **Guidance that would cause data loss or exposure if followed.** A skill that
  recommends a destructive operation without the guard the situation requires,
  or that would widen access to a governed column. This is the highest-severity
  category and the one most worth reporting.
- **Prompt injection through skill content** — text that could redirect an agent
  toward an action the user did not ask for.
- **A leaked credential, internal hostname, or private identifier** in any file
  or in git history.
- **A bundled script** (`skills/**/*.sh`, `scripts/*.py`) that does more than it
  claims, or that could be induced to.

## Out of scope

- Vulnerabilities in dbt, an adapter, a warehouse, or an agent harness. Report
  those to the relevant project.
- Guidance you disagree with on style or architecture grounds. That is an issue
  or a pull request, not a security report.

## For adopters

Two properties worth knowing, because they are deliberate design choices:

- **The skills instruct; they do not enforce.** Nothing here can prevent an
  agent from acting outside this guidance. Treat the library as a competent
  colleague's advice, not as a control. If you need enforcement, it belongs in
  CI, in warehouse grants, and in your harness's own permission model —
  `docs/hooks.md` covers what is worth wiring up.
- **Read the diff on upgrade.** These files change how your agent behaves in a
  repository that has warehouse credentials. Review a version bump the way you
  would review a dependency that runs in production.
