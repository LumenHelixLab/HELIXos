#!/usr/bin/env bash
# HELIXos auto-context loader — a UserPromptSubmit hook (failsafe wrapper).
#
# When a prompt mentions a HELIXos keyword, this injects the relevant repo files
# (or excerpts) as extra context, so contributors and agents don't re-explain the
# architecture every session. Keyword -> file mappings live in
# .claude/helix-keywords.json; the matching logic lives in helix_context_loader.py.
#
# Original implementation for HELIXos. Inspired by the operator-kit
# context-loader (https://github.com/wrg32786/operator-kit, MIT). No code copied.
#
# Contract: reads the hook JSON on stdin (with a .prompt field); anything printed
# to stdout is added to the model's context. Always exits 0 and never blocks a
# prompt, so a misconfiguration degrades to a no-op.

INPUT="$(cat 2>/dev/null || true)"

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
HOOK_DIR="$PROJECT_DIR/.claude/hooks"
CONFIG="$PROJECT_DIR/.claude/helix-keywords.json"

command -v python3 >/dev/null 2>&1 || exit 0
[ -f "$CONFIG" ] || exit 0
[ -f "$HOOK_DIR/helix_context_loader.py" ] || exit 0

printf '%s' "$INPUT" | python3 "$HOOK_DIR/helix_context_loader.py" "$CONFIG" "$PROJECT_DIR" 2>/dev/null || true

exit 0
