#!/usr/bin/env python3
"""Pre-publication checks.

The library's core promise is that it carries no organisation-specific detail.
That promise cannot rest on reviewer diligence -- a single leaked internal
database name in a public repo is not recoverable. So it is mechanical.

The denylist itself is NOT stored here. A list of an organisation's internal
database names, macros and prefixes is exactly the inventory the scan exists to
keep out of a public repository, and a scanner that embeds it leaks precisely
what it protects. So terms are supplied from outside the repo:

    1. --terms <path>
    2. $DBT_NAVIGATOR_TERMS
    3. .leakcheck-terms in the repo root (gitignored)

With no terms file the structural checks all still run and the leak scan
reports itself as skipped. Contributors therefore get every portability and
consistency check without needing anyone's private list; the maintainer
publishing a release is the one who needs it.

Terms file format: one term per line, `#` comments and blank lines ignored.
A trailing underscore (`foo_`) matches as a prefix.

Usage:  python3 scripts/check-skills.py [--terms PATH]
Exit:   0 = clean, 1 = problems found
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TERMS_ENV = "DBT_NAVIGATOR_TERMS"
TERMS_DEFAULT = ROOT / ".leakcheck-terms"


def load_terms(explicit: str | None) -> tuple[list[str], str]:
    """Return (terms, source_description). Empty list means scan is skipped."""
    for candidate, label in (
        (explicit, "--terms"),
        (os.environ.get(TERMS_ENV), f"${TERMS_ENV}"),
        (str(TERMS_DEFAULT) if TERMS_DEFAULT.exists() else None, str(TERMS_DEFAULT.name)),
    ):
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if not path.exists():
            # An explicitly-named file that does not exist is an error, not a
            # silent fallback: the caller believed a scan was happening.
            print(f"error: terms file not found: {path}", file=sys.stderr)
            sys.exit(2)
        terms = [
            line.strip().lower()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        return terms, f"{label} ({len(terms)} terms)"
    return [], "not supplied"


# Paths exempt from the leak scan, with the reason.
#
# Kept deliberately narrow, because a broad exemption is how leaked vocabulary
# survives an audit. `examples/` is the sharpest case: exempting it feels safe
# because "it's only an example", but examples are the files adopters copy
# verbatim, so a private identifier there propagates further than one buried in
# prose. Examples need the scan more than the skills do, not less.
#
# The same reasoning rules out exempting this file. A scanner that skips itself
# can carry anything, and the only reason to exempt it was to hold the term
# list -- which now lives outside the repository entirely.
EXEMPT = {
    "schema/conventions.schema.json": (
        "the contract is where an adopter names their own BI tools; the enum is "
        "a vocabulary, not a leaked value"
    ),
    ".leakcheck-terms": "the private term list itself, gitignored",
}

# Terms allowed on specific lines of specific files, with the reason. This
# exists so exemptions stay line-scoped instead of file-scoped: a whole-file
# exemption is what let the example contract carry a leaked taxonomy unseen.
LINE_EXEMPT = {
    ("examples/conventions.example.yml", "tool:"): (
        "bi.consumers[].tool is a closed enum in the schema, so a working "
        "example must name a real tool; the value shown is not the origin "
        "project's"
    ),
}

REQUIRED_SECTIONS = ["## Completion checklist"]

# Skills cross-reference each other by bare name ("see `dbt-breaking-changes`"),
# which no link checker catches. A reference to a skill that was renamed or cut
# is a dead end for the agent, so the names are resolved mechanically.
#
# These dbt-prefixed tokens are packages, tools, and files -- not skills.
NOT_SKILLS = {
    "dbt-core", "dbt-cloud", "dbt-utils", "dbt-expectations", "dbt-labs",
    "dbt-date", "dbt-project", "dbt-agent", "dbt-snowflake", "dbt-bigquery",
    "dbt-databricks", "dbt-redshift", "dbt-postgres", "dbt-adapters",
    "dbt-agent-skills", "dbt-artifacts", "dbt-osmosis", "dbt-checkpoint",
    "dbt-navigator", "dbt-navigator-marketplace",
}


def is_exempt(rel: str) -> str | None:
    for prefix, reason in EXEMPT.items():
        if rel.startswith(prefix) or rel == prefix:
            return reason
    return None


def check_frontmatter(path: Path, text: str) -> list[str]:
    problems = []
    if not text.startswith("---\n"):
        return [f"{path}: missing frontmatter"]
    end = text.find("\n---\n", 4)
    if end == -1:
        return [f"{path}: unterminated frontmatter"]
    fm = text[4:end]
    if not re.search(r"^name:\s*\S+", fm, re.M):
        problems.append(f"{path}: frontmatter missing 'name'")
    else:
        declared = re.search(r"^name:\s*(\S+)", fm, re.M).group(1)
        expected = path.parent.name
        if declared != expected:
            problems.append(
                f"{path}: frontmatter name '{declared}' != directory '{expected}'"
            )
    desc = re.search(r"^description:\s*(.+)", fm, re.M)
    if not desc:
        problems.append(f"{path}: frontmatter missing 'description'")
    elif not desc.group(1).strip().lower().startswith(("use when", "use before")):
        problems.append(
            f"{path}: description should start with 'Use when' or 'Use before' "
            f"-- it states a trigger condition, not a topic"
        )

    # Spec-compliance: the Agent Skills spec allows only these six top-level
    # keys for claude.ai / Skills-API distribution. Any other top-level key
    # makes packaging there hard-error, so we forbid it at the source. Our own
    # metadata (phase, and anything downstream tooling wants) lives under the
    # free-form `metadata:` block, which IS spec-valid.
    top_level_keys = re.findall(r"^([A-Za-z0-9_-]+):", fm, re.M)
    unexpected = [k for k in top_level_keys if k not in ALLOWED_TOP_LEVEL_KEYS]
    if unexpected:
        problems.append(
            f"{path}: non-spec top-level frontmatter key(s) {sorted(set(unexpected))} "
            f"-- only {sorted(ALLOWED_TOP_LEVEL_KEYS)} are valid for distribution; "
            f"nest anything else under 'metadata:'"
        )

    # phase now lives under metadata: (indented). It documents the router's
    # phase spine; nothing reads it programmatically at runtime.
    phase = re.search(r"^\s+phase:\s*(\S+)", fm, re.M)
    if not phase:
        problems.append(
            f"{path}: frontmatter missing 'metadata.phase' -- assign one of "
            f"{sorted(VALID_PHASES)} under a 'metadata:' block so the router can place it"
        )
    elif phase.group(1) not in VALID_PHASES:
        problems.append(
            f"{path}: metadata.phase '{phase.group(1)}' not one of {sorted(VALID_PHASES)}"
        )

    if desc:
        problems += check_description_triggers(path, desc.group(1))
    return problems


# The six top-level frontmatter keys the Agent Skills spec permits for
# claude.ai / Skills-API distribution. Everything else -- including our own
# `phase` -- must live under `metadata:`.
ALLOWED_TOP_LEVEL_KEYS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}





# Words that read as generic English or as an operation to us, but that many
# teams use as literal segments in model names. A description containing one
# fires on every request mentioning a model so named, which quietly turns that
# skill into the default answer for unrelated work. Measured rather than
# guessed: a single such word in one description captured over a third of
# matches across a corpus of real requests. Describe the OPERATION
# ("combining several sources") rather than the ARTIFACT ("a wide summary
# table").
VALID_PHASES = {
    "orient",
    "decide",
    "build",
    "prove",
    "ship",
    "diagnose",
    "reference",
}

ROUTER = "dbt-navigating-skills"

COLLISION_NOUNS = {
    "performance",
    "unified",
    "revenue",
    "delivery",
    "pacing",
    "spend",
    "impressions",
    "clicks",
    "sessions",
    "orders",
    "customers",
    "inventory",
    "campaign",
    "auction",
}


def check_description_triggers(path: Path, desc: str) -> list[str]:
    """Flag descriptions whose trigger words collide with business model names."""
    words = set(re.findall(r"[a-z]+", desc.lower()))
    hits = sorted(words & COLLISION_NOUNS)
    if hits:
        return [
            f"{path}: description contains business noun(s) {hits} that collide "
            f"with model names; describe the operation, not the artifact"
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--terms",
        help="path to the private leak-term list (one term per line)",
    )
    args = parser.parse_args()
    banned, terms_source = load_terms(args.terms)

    problems: list[str] = []
    skill_files = sorted((ROOT / "skills").rglob("*.md"))

    if not skill_files:
        print("no skills found")
        return 1

    # 1. leak scan across everything publishable.
    # .yml matters as much as .md here: the example contract is the file users
    # copy verbatim, so a leaked vendor name there propagates further than one
    # buried in prose. .sh and .py are scanned too -- a bundled script can leak a
    # project path or table name as easily as prose can.
    scanned = 0
    for path in (
        sorted(ROOT.rglob("*.md"))
        + sorted(ROOT.rglob("*.json"))
        + sorted(ROOT.rglob("*.yml"))
        + sorted(ROOT.rglob("*.yaml"))
        + sorted(ROOT.rglob("*.sh"))
        + sorted(ROOT.rglob("*.py"))
    ):
        rel = str(path.relative_to(ROOT))
        if is_exempt(rel):
            continue
        if "/.git/" in f"/{rel}" or rel.startswith(".git/"):
            continue
        scanned += 1
        if not banned:
            continue
        lowered = path.read_text(encoding="utf-8").lower()
        for term in banned:
            # Word-boundary matching, not substring. Plain `in` matched a term
            # inside a longer, innocent word -- a false positive that trains
            # people to ignore the checker, which is worse than the leak it was
            # meant to catch. Trailing-underscore terms (table-prefix patterns
            # such as `foo_`) get no closing boundary, since `\b` does not fire
            # between an underscore and a word character.
            pattern = (
                r"\b" + re.escape(term)
                if term.endswith("_")
                else r"\b" + re.escape(term) + r"\b"
            )
            if re.search(pattern, lowered):
                line_no = next(
                    (
                        i
                        for i, line in enumerate(lowered.splitlines(), 1)
                        if re.search(pattern, line)
                        and not any(
                            rel == f and marker in line
                            for (f, marker) in LINE_EXEMPT
                        )
                    ),
                    0,
                )
                if line_no:
                    problems.append(f"{rel}:{line_no}: banned term '{term}'")

    # 2. structure of each SKILL.md
    skill_dirs = set()
    # subagent names share the dbt- prefix but are not skills; the router and
    # docs reference them by name, and they must not read as dangling links
    agent_names = {f.stem for f in (ROOT / "agents").glob("*.md")}
    for path in skill_files:
        if path.name != "SKILL.md":
            continue
        skill_dirs.add(path.parent.name)
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        problems.extend(check_frontmatter(rel, text))
        for section in REQUIRED_SECTIONS:
            if section not in text:
                problems.append(f"{rel}: missing '{section}'")
        if len(text.splitlines()) < 60:
            problems.append(f"{rel}: suspiciously short; a section, not a skill")

    # 2b. no skill may cite a universal rule by NUMBER.
    #
    # "AGENTS.md rule 9" is a pointer into a file every adopter is expected to
    # edit. Reorder or insert one rule and every citation silently misdirects --
    # it still reads as authoritative and now names the wrong rule, which is
    # worse than no citation at all. Cite the rule's CONTENT instead, which
    # survives renumbering and tells the reader the thing they needed anyway.
    # A grep for banned words cannot see this; it was found by reading.
    for path in sorted(ROOT.rglob("*.md")):
        rel = str(path.relative_to(ROOT))
        if any(rel.startswith(p) for p in EXEMPT):
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\brules?\s+\d+", line, re.I):
                problems.append(
                    f"{rel}:{i}: cites a universal rule by number; "
                    "cite its content instead (adopters renumber AGENTS.md)"
                )

    # 3. every skill directory has a SKILL.md
    for d in sorted((ROOT / "skills").iterdir()):
        if d.is_dir() and not (d / "SKILL.md").exists():
            problems.append(f"skills/{d.name}: directory without SKILL.md")

    # 3a. the router must reach every skill from an actual archetype.
    # Naming a skill in the phase-spine table is not routing: an agent picks an
    # archetype and reads what that archetype names. A skill mentioned only in
    # the spine is unreachable in practice, which is how a whole skill went
    # unrouted while a weaker "is the name present anywhere" check passed.
    router_md = ROOT / "skills" / ROUTER / "SKILL.md"
    if not router_md.exists():
        problems.append(f"skills/{ROUTER}/SKILL.md: router missing")
    else:
        router_text = router_md.read_text(encoding="utf-8")
        arch = re.search(
            r"## Archetypes.*?(?=## Escalation triggers)", router_text, re.S
        )
        esc = re.search(
            r"## Escalation triggers.*?(?=## Anti-patterns)", router_text, re.S
        )
        if not arch:
            problems.append(
                f"skills/{ROUTER}/SKILL.md: no '## Archetypes' section to route from"
            )
        reachable = (arch.group(0) if arch else "") + (esc.group(0) if esc else "")
        for d in sorted((ROOT / "skills").iterdir()):
            if not d.is_dir() or d.name == ROUTER:
                continue
            if d.name not in reachable:
                problems.append(
                    f"skills/{ROUTER}/SKILL.md: '{d.name}' is not reachable from "
                    f"an archetype or an escalation trigger -- an agent entering "
                    f"through the router will never find it"
                )

    # 3b. every sub-document is reachable from its SKILL.md.
    # A whole-file rewrite of a SKILL.md can silently drop the link to a
    # sub-document, leaving real content that nothing routes to. The file
    # still exists and every other check passes, so only this catches it.
    for path in skill_files:
        if path.name == "SKILL.md":
            continue
        skill_md = path.parent / "SKILL.md"
        if not skill_md.exists():
            continue
        if path.name not in skill_md.read_text(encoding="utf-8"):
            rel = path.relative_to(ROOT)
            problems.append(
                f"{rel}: sub-document not linked from its SKILL.md (orphaned)"
            )

    # 4. internal links resolve
    for path in skill_files:
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\((?!https?://)([^)#]+\.md)\)", text):
            if not (path.parent / target).exists() and not (ROOT / target).exists():
                problems.append(f"{path.relative_to(ROOT)}: broken link -> {target}")

    # 5. cross-skill references by bare name point at a skill that exists.
    #    No file is exempt. A doc naming a skill the library does not contain is
    #    a dead end for the reader and for the agent, whatever the reason.
    for path in sorted(ROOT.rglob("*.md")):
        rel = str(path.relative_to(ROOT))
        text = path.read_text(encoding="utf-8")
        for ref in set(re.findall(r"`(dbt-[a-z0-9-]+)`", text)):
            if ref in NOT_SKILLS or ref in skill_dirs or ref in agent_names:
                continue
            line_no = next(
                (i for i, ln in enumerate(text.splitlines(), 1) if f"`{ref}`" in ln),
                0,
            )
            problems.append(
                f"{rel}:{line_no}: references skill '{ref}' which does not exist"
            )

    # 6. the README skill table and the skills on disk agree
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"\b(dbt-[a-z0-9-]+)\b", readme)) - NOT_SKILLS
    listed = {name for name in listed if name.startswith("dbt-")}
    for name in sorted(skill_dirs - listed):
        problems.append(f"README.md: skill '{name}' exists on disk but is not listed")
    for name in sorted(listed - skill_dirs):
        problems.append(f"README.md: lists '{name}' which has no skill directory")

    # 7. the subagent layer must stay an accelerator, never a second source of truth.
    # Subagents are harness-specific (.claude/agents/ is Claude Code; other
    # harnesses differ), so any guidance that exists ONLY in an agent definition
    # is guidance a Cursor or Codex user cannot reach. Agents may orchestrate and
    # define a return contract; they may not carry dbt knowledge of their own.
    agents_dir = ROOT / "agents"
    if agents_dir.is_dir():
        for f in sorted(agents_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            rel = f.relative_to(ROOT)
            fm_m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            if not fm_m:
                problems.append(f"{rel}: no YAML frontmatter")
                continue
            fm_a, body = fm_m.group(1), text[fm_m.end() :]

            name_m = re.search(r"^name:\s*(\S+)", fm_a, re.M)
            if not name_m:
                problems.append(f"{rel}: frontmatter missing 'name'")
            elif name_m.group(1) != f.stem:
                problems.append(
                    f"{rel}: name '{name_m.group(1)}' != filename '{f.stem}'"
                )

            d_m = re.search(r"^description:\s*(.+)", fm_a, re.M)
            if not d_m:
                problems.append(f"{rel}: frontmatter missing 'description'")
            elif not d_m.group(1).strip().lower().startswith(("use when", "use before")):
                problems.append(
                    f"{rel}: description should start with 'Use when' or "
                    f"'Use before' -- it is the delegation trigger"
                )

            # every preloaded skill must exist, or the agent launches without it
            for s in re.findall(r"^\s+-\s+(dbt-[a-z0-9-]+)\s*$", fm_a, re.M):
                if s not in skill_dirs:
                    problems.append(
                        f"{rel}: preloads skill '{s}' which does not exist"
                    )

            # a structured return contract is what stops prose summaries
            # flattening hedged findings into assertions at the handoff
            if "Return this structure" not in body:
                problems.append(
                    f"{rel}: no 'Return this structure' section -- a free-form "
                    f"summary drops uncertainty and unverified claims at the handoff"
                )
            if not re.search(r"COULD NOT|CONFIRM|could not", body):
                problems.append(
                    f"{rel}: return contract has no slot for what could not be "
                    f"verified -- the parent will read silence as success"
                )

            # keep them thin: an agent long enough to hold real guidance is
            # holding guidance that belongs in a skill
            n = len(body.splitlines())
            if n > 90:
                problems.append(
                    f"{rel}: body is {n} lines -- too long for an orchestrator. "
                    f"Move dbt knowledge into a skill and preload it instead"
                )

    # 11. the router's self-description must match the library it routes.
    #
    # `dbt-navigating-skills` justifies its own existence with a file and line
    # count ("27 skills across N files"). That number is the argument for reading
    # the router at all, and a number a human must remember to update is a number
    # that will eventually be wrong. It is derivable in one pass, so assert it
    # rather than trusting an edit.
    router = ROOT / "skills" / "dbt-navigating-skills" / "SKILL.md"
    if router.exists():
        all_skill_md = sorted((ROOT / "skills").rglob("*.md"))
        actual_files = len(all_skill_md)
        actual_lines = sum(
            len(f.read_text(encoding="utf-8").splitlines()) for f in all_skill_md
        )
        text = router.read_text(encoding="utf-8")
        m = re.search(
            r"(\d+) skills across (\d+) files and roughly ([\d,]+) lines", text
        )
        if not m:
            problems.append(
                "skills/dbt-navigating-skills/SKILL.md: cannot find the "
                "'N skills across N files and roughly N lines' self-description; "
                "keep it in that form so it can be verified mechanically"
            )
        else:
            claimed_skills = int(m.group(1))
            claimed_files = int(m.group(2))
            claimed_lines = int(m.group(3).replace(",", ""))
            if claimed_skills != len(skill_dirs):
                problems.append(
                    f"router claims {claimed_skills} skills, found {len(skill_dirs)}"
                )
            if claimed_files != actual_files:
                problems.append(
                    f"router claims {claimed_files} files, found {actual_files}"
                )
            # Rounded on purpose ("roughly"), so allow 5% drift before failing.
            if abs(claimed_lines - actual_lines) > actual_lines * 0.05:
                problems.append(
                    f"router claims ~{claimed_lines:,} lines, found "
                    f"{actual_lines:,} (>5% drift)"
                )

    print(f"scanned {scanned} files, {len(skill_dirs)} skills")
    print(f"leak scan: {terms_source}")
    if not banned:
        print(
            "  NOTE: structural checks only. Supply --terms (or set "
            f"${TERMS_ENV}) before publishing a release."
        )
    if problems:
        print(f"\n{len(problems)} problem(s):\n")
        for p in problems:
            print(f"  {p}")
        return 1
    print("clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
