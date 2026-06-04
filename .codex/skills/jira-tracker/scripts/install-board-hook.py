#!/usr/bin/env python3
"""Install the jira-tracker board-reminder hook into Claude Code settings.

Adds a UserPromptSubmit hook that, when the current repo has .jira/board.json,
injects a one-line reminder to reconcile the board at the end of the turn
(SKILL.md Workflow 5). Idempotent: re-running never duplicates the hook.

Usage:
  python3 install-board-hook.py             # project: ./.claude/settings.json
  python3 install-board-hook.py --global    # user:    ~/.claude/settings.json
  python3 install-board-hook.py --settings PATH   # explicit file

Claude Code only — Codex has no hook mechanism and relies on the skill text.
No third-party dependencies; Python 3.8+ standard library only.
"""

import argparse
import json
import os
import sys
from pathlib import Path

HOOK_COMMAND = (
    "[ -f .jira/board.json ] && echo 'Reminder: this repo has a Jira board "
    "(.jira/) - reconcile it per jira-tracker Workflow 5 when you finish "
    "this request.' || true"
)
MARKER = "reconcile it per jira-tracker Workflow 5"  # unique to our hook command


def fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Install the jira-tracker board-reminder UserPromptSubmit hook.")
    ap.add_argument("--global", dest="global_", action="store_true",
                    help="install into ~/.claude/settings.json instead of the project")
    ap.add_argument("--settings", help="explicit settings.json path (overrides --global)")
    args = ap.parse_args(argv)

    if args.settings:
        path = Path(args.settings)
    elif args.global_:
        path = Path.home() / ".claude" / "settings.json"
    else:
        path = Path(".claude/settings.json")

    if path.is_symlink():
        path = path.resolve()  # write through symlinks, don't replace them

    settings = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return fail(f"could not parse {path} ({e})")

    if not isinstance(settings, dict):
        return fail(f"{path} is not a JSON object — refusing to modify it")
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return fail(f'{path} has a non-object "hooks" value — fix it by hand')
    entries = hooks.setdefault("UserPromptSubmit", [])
    if not isinstance(entries, list):
        return fail(f"{path} has a non-list UserPromptSubmit — fix it by hand")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []) or []:
            if isinstance(hook, dict) and MARKER in hook.get("command", ""):
                print(f"already installed in {path}")
                return 0

    entries.append({"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    print(f"installed board-reminder hook -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
