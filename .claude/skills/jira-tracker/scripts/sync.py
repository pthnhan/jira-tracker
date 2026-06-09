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

SKILL_SUBPATH = Path("skills/jira-tracker")


def _find_repo(start: Path) -> Path:
    """Locate the repo root by walking up to the first ancestor holding a
    .claude/skills/jira-tracker tree. sync.py is byte-identical in both the
    .claude and .codex copies, so resolving the source from __file__'s position
    would flip the source-of-truth when the .codex copy is run; anchoring to the
    .claude tree keeps .claude authoritative no matter which copy is launched."""
    for parent in start.parents:
        if (parent / ".claude" / SKILL_SUBPATH).is_dir():
            return parent
    raise SystemExit(
        "sync.py: could not locate the repo root "
        "(no .claude/skills/jira-tracker above this script)"
    )


REPO = _find_repo(Path(__file__).resolve())            # <repo root>
CLAUDE_SKILL = REPO / ".claude" / SKILL_SUBPATH        # the single source of truth
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


def _require_src(src: Path, rel: str):
    """A SHARED entry with no source file means the list and the tree have
    drifted (e.g. a renamed/deleted file left in SHARED). Fail loudly rather
    than with a bare FileNotFoundError traceback."""
    if not src.exists():
        raise SystemExit(
            f"sync.py: '{rel}' is listed in SHARED but missing from the source "
            f"tree ({src.parent}); update SHARED."
        )


def _differing(src_root: Path, dst_root: Path):
    """Return the shared files whose bytes differ (or are missing) in dst."""
    out = []
    for rel in SHARED:
        src = src_root / rel
        dst = dst_root / rel
        _require_src(src, rel)
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            out.append(rel)
    return out


def _copy_shared(src_root: Path, dst_root: Path):
    for rel in SHARED:
        src = src_root / rel
        dst = dst_root / rel
        _require_src(src, rel)
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
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="verify the .codex mirror matches .claude; exit 1 on drift")
    mode.add_argument("--global", dest="globl", action="store_true",
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
        # `or` (not get's default) so a set-but-empty HOME/CODEX_HOME — common in
        # CI/containers — doesn't collapse to a relative path that rmtree would
        # then resolve against the cwd.
        home = Path(os.environ.get("HOME") or Path.home())
        codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex")
        g_claude = home / ".claude" / SKILL_SUBPATH
        g_codex = codex_home / SKILL_SUBPATH
        _copy_tree(CLAUDE_SKILL, g_claude)
        print(f"refreshed global .claude install -> {g_claude}")
        _copy_tree(CODEX_SKILL, g_codex)
        print(f"refreshed global .codex install  -> {g_codex}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
