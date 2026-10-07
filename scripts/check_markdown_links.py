#!/usr/bin/env python3
"""Check that local targets in Markdown inline links exist."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = {".git", ".next", ".venv", "build", "dist", "node_modules", "__pycache__"}
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^)]*)\)")
CODE_BLOCK_PATTERN = re.compile(r"```.*?```|~~~.*?~~~", flags=re.DOTALL)
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "tel:", "data:", "codex:")


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not any(part in IGNORED_DIRS for part in path.relative_to(ROOT).parts)
    )


def local_target(raw: str) -> str | None:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    else:
        target = target.split(None, 1)[0] if target else ""
    if not target or target.startswith(EXTERNAL_SCHEMES) or target.startswith("//"):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    return unquote(target) if target else None


def main() -> int:
    missing: list[tuple[Path, str]] = []
    checked = 0
    for markdown in markdown_files():
        content = CODE_BLOCK_PATTERN.sub("", markdown.read_text(encoding="utf-8"))
        for match in LINK_PATTERN.finditer(content):
            target = local_target(match.group(1))
            if target is None:
                continue
            checked += 1
            if not (markdown.parent / target).resolve().exists():
                missing.append((markdown.relative_to(ROOT), target))

    if missing:
        for markdown, target in missing:
            print(f"{markdown}: unresolved local Markdown link: {target}", file=sys.stderr)
        print(f"{len(missing)} unresolved links across {checked} local links", file=sys.stderr)
        return 1

    print(f"Checked {checked} local Markdown links across {len(markdown_files())} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
