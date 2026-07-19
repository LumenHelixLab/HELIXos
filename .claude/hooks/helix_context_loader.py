#!/usr/bin/env python3
"""HELIXos auto-context loader (matching logic).

Reads the UserPromptSubmit hook JSON on stdin, matches keywords from
``.claude/helix-keywords.json``, and prints the relevant repo files/excerpts to
stdout so they are injected as context. Failsafe: any error prints nothing and
exits 0, degrading to a no-op.

Usage: helix_context_loader.py <config.json> <project_dir>

Original implementation for HELIXos. Inspired by the operator-kit context-loader
(https://github.com/wrg32786/operator-kit, MIT). No code was copied.
"""

from __future__ import annotations

import json
import os
import sys

TOTAL_BUDGET = 60_000   # hard cap on injected bytes
DEFAULT_LINES = 40


def load_prompt() -> str:
    raw = sys.stdin.read()
    try:
        return (json.loads(raw).get("prompt") or "").lower()
    except Exception:
        return raw.lower()  # tolerate non-JSON stdin


def excerpt(project_dir: str, path: str, lines: int) -> str | None:
    full = os.path.join(project_dir, path)
    if not os.path.isfile(full):
        return None
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            body = fh.read().splitlines()
    except Exception:
        return None
    clipped = body[:lines] if lines else body
    tag = f" (first {lines} lines)" if lines and len(body) > lines else ""
    return f"### {path}{tag}\n" + "\n".join(clipped)


def main() -> None:
    if len(sys.argv) < 3:
        return
    config_path, project_dir = sys.argv[1], sys.argv[2]
    prompt = load_prompt()
    if not prompt:
        return
    try:
        with open(config_path, encoding="utf-8") as fh:
            entries = json.load(fh)
    except Exception:
        return

    chunks: list[str] = []
    seen: set[str] = set()
    budget = TOTAL_BUDGET
    for entry in entries:
        kws = [k.lower() for k in entry.get("keywords", [])]
        hit = next((k for k in kws if k in prompt), None)
        if not hit:
            continue
        label = entry.get("label", hit)
        files: list[tuple[str, int]] = []
        if entry.get("priority_file"):
            files.append((entry["priority_file"], entry.get("priority_lines", DEFAULT_LINES)))
        files += [(f, 0) for f in entry.get("files", [])]

        rendered: list[str] = []
        for path, lines in files:
            if path in seen:
                continue
            piece = excerpt(project_dir, path, lines)
            if piece is None or len(piece) > budget:
                continue
            rendered.append(piece)
            seen.add(path)
            budget -= len(piece)
        if rendered:
            chunks.append(f'Matched "{hit}" — loading {label}:\n\n' + "\n\n".join(rendered))
        if budget <= 0:
            break

    if chunks:
        print('<helix-context source="auto-context-loader">')
        print("\n\n---\n\n".join(chunks))
        print("</helix-context>")


if __name__ == "__main__":
    main()
