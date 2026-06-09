#!/usr/bin/env python3
"""Sync the jira-tracker skill package from its single source (the .claude tree)
to the .codex mirror, and optionally refresh global installs (JT-48).

The .claude tree is authoritative. The shared files below are copied verbatim
into .codex; codex-only files (e.g. agents/openai.yaml) are left untouched.

Usage:
  python3 sync.py            # mirror shared files .claude -> .codex (in-repo)
  python3 sync.py --check    # verify only; exit 1 if any shared file differs
  python3 sync.py --global   # also refresh ~/.claude and $CODEX_HOME (~/.codex)

Run it after editing anything under .claude/skills/jira-tracker/. The
byte-equality test (test_claude_and_codex_trees_in_sync) is the backstop that
fails CI if the trees ever drift.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

# scripts/sync.py -> jira-tracker -> skills -> .claude -> <repo root>
SKILL_SUBPATH = Path("skills/jira-tracker")
CLAUDE_SKILL = Path(__file__).resolve().parents[1]      # .../.claude/skills/jira-tracker
REPO = CLAUDE_SKILL.parents[2]                          # <repo root>
CODEX_SKILL = REPO / ".codex" / SKILL_SUBPATH

# Files that must be identical across both packagings. Anything not listed
# (codex-only agents/openai.yaml, generated __pycache__) is preserved.
SHARED = (
    "SKILL.md",
    "scripts/jira.py",
    "scripts/install-board-hook.py",
    "scripts/sync.py",
    "references/schema.md",
)


def _differing(src_root: Path, dst_root: Path):
    """Return the shared files whose bytes differ (or are missing) in dst."""
    out = []
    for rel in SHARED:
        src = src_root / rel
        dst = dst_root / rel
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            out.append(rel)
    return out


def _copy_shared(src_root: Path, dst_root: Path):
    for rel in SHARED:
        src = src_root / rel
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _copy_tree(src_root: Path, dst_root: Path):
    """Clean copy of a whole skill tree, minus build cruft, for global installs."""
    dst_root.parent.mkdir(parents=True, exist_ok=True)
    if dst_root.exists():
        shutil.rmtree(dst_root)
    shutil.copytree(
        src_root, dst_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "board.lock"),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sync the jira-tracker skill package.")
    ap.add_argument("--check", action="store_true",
                    help="verify the .codex mirror matches .claude; exit 1 on drift")
    ap.add_argument("--global", dest="globl", action="store_true",
                    help="also refresh ~/.claude and $CODEX_HOME (default ~/.codex)")
    args = ap.parse_args(argv)

    if args.check:
        diffs = _differing(CLAUDE_SKILL, CODEX_SKILL)
        if diffs:
            print("drift between .claude and .codex:")
            for rel in diffs:
                print(f"  {rel}")
            print("run: python3 scripts/sync.py")
            return 1
        print(".claude and .codex are in sync")
        return 0

    _copy_shared(CLAUDE_SKILL, CODEX_SKILL)
    print(f"synced {len(SHARED)} shared files: .claude -> .codex")

    if args.globl:
        home = Path(os.environ.get("HOME", Path.home()))
        codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
        g_claude = home / ".claude" / SKILL_SUBPATH
        g_codex = codex_home / SKILL_SUBPATH
        _copy_tree(CLAUDE_SKILL, g_claude)
        print(f"refreshed global .claude install -> {g_claude}")
        _copy_tree(CODEX_SKILL, g_codex)
        print(f"refreshed global .codex install  -> {g_codex}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
