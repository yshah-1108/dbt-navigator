#!/usr/bin/env python3
"""Port the Claude Code subagents in agents/ to another harness.

Claude Code supports `skills:` (preloads full skill content at startup) and
`tools:` (a real allowlist). Cursor's subagent format supports only `name` and
`description`. A blind copy therefore produces an agent that never reads its
skills and silently has write access it was designed not to have.

This script rewrites the frontmatter to the target's supported fields and moves
what is lost into the body as instructions, so the same definition still works.

Usage:  python3 scripts/port-agents.py cursor <dest-repo>
Exit:   0 = wrote all agents, 1 = nothing to port
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

READ_FIRST = """## Read these first

Your harness does not preload skills, so read them before doing anything else. \
They contain the procedure; this file only says what to return.

{skills}
"""

READ_ONLY = """
**You are read-only by convention here.** Your harness may not enforce a tool \
allowlist, so treat it as a rule you keep rather than a boundary you cannot \
cross: do not edit, and do not fix what you were asked to assess.
"""


def port(target: str, dest: pathlib.Path) -> int:
    src = ROOT / "agents"
    if not src.is_dir():
        print("no agents/ directory to port")
        return 1

    if target != "cursor":
        print(f"unknown target '{target}' -- only 'cursor' is implemented")
        return 1

    out_dir = dest / ".cursor" / "agents"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for f in sorted(src.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not m:
            print(f"  SKIP {f.name}: no frontmatter")
            continue
        fm, body = m.group(1), text[m.end() :]

        name = re.search(r"^name:\s*(\S+)", fm, re.M)
        desc = re.search(r"^description:\s*(.+)", fm, re.M)
        if not (name and desc):
            print(f"  SKIP {f.name}: missing name or description")
            continue

        skills = re.findall(r"^\s+-\s+(dbt-[a-z0-9-]+)\s*$", fm, re.M)
        tools = re.search(r"^tools:\s*(.+)", fm, re.M)
        read_only = bool(tools) and "Write" not in tools.group(1)

        preamble = READ_FIRST.format(
            skills="\n".join(f"- `{s}`" for s in skills)
        )
        if read_only:
            preamble += READ_ONLY

        out = (
            f"---\nname: {name.group(1)}\n"
            f"description: {desc.group(1).strip()}\n---\n\n"
            f"{preamble}\n{body.strip()}\n"
        )
        (out_dir / f.name).write_text(out, encoding="utf-8")
        print(
            f"  {f.name}: {len(skills)} skill(s) named in body, "
            f"read-only={read_only}"
        )
        written += 1

    print(f"\nwrote {written} agent(s) to {out_dir}")
    return 0 if written else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    sys.exit(port(sys.argv[1], pathlib.Path(sys.argv[2]).expanduser()))
