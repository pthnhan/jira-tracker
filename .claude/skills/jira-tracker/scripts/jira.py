#!/usr/bin/env python3
"""
jira.py — a tiny, dependency-free Jira-style work tracker backed by a JSON file
and rendered to a standalone HTML board.

Source of truth:  .jira/board.json
Rendered view:     .jira/board.html  (regenerated automatically after any change)

This script is meant to be driven by an agent (see SKILL.md) or by a human.
Run `python jira.py --help` for the command list.

No third-party dependencies — Python 3.8+ standard library only.
"""

import argparse
import datetime as _dt
import html
import json
import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

TYPES = ["Epic", "Story", "Task", "Bug", "Sub-task"]
STATUSES = ["To Do", "In Progress", "In Review", "Done", "Cancelled"]
PRIORITIES = ["Highest", "High", "Medium", "Low", "Lowest"]

PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}  # 0 = Highest
OPEN_STATUSES = {"To Do", "In Progress", "In Review"}
CLOSED_STATUSES = {"Done", "Cancelled"}

DEFAULT_FILE = Path(".jira/board.json")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def now() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def board_path(args) -> Path:
    return Path(args.file) if getattr(args, "file", None) else DEFAULT_FILE


def load(args) -> dict:
    p = board_path(args)
    if not p.exists():
        die(f"no board found at {p}. Run `jira.py init` first.")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        die(f"could not parse {p} ({e}) — fix the JSON or restore it from git")


def write_atomic(path: Path, text: str):
    """Write via a temp file + rename so an interrupted write can't corrupt the target."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def save(board: dict, args, render: bool = True):
    p = board_path(args)
    p.parent.mkdir(parents=True, exist_ok=True)
    board["project"]["updated"] = now()
    write_atomic(p, json.dumps(board, indent=2, ensure_ascii=False) + "\n")
    if render:
        out = p.with_name("board.html")
        render_html(board, out)
        return out
    return None


def find(board: dict, key: str) -> dict:
    key = key.upper()
    for issue in board["issues"]:
        if issue["key"].upper() == key:
            return issue
    die(f"issue {key} not found")


def next_key(board: dict) -> str:
    board["project"]["counter"] += 1
    return f'{board["project"]["key"]}-{board["project"]["counter"]}'


def fuzzy(value: str, options: list, label: str) -> str:
    """Match a status/type/priority case-insensitively; allow any unique
    prefix or fragment ('prog' -> 'In Progress', 'review' -> 'In Review')."""
    if value in options:
        return value
    lowered = {o.lower(): o for o in options}
    if value.lower() in lowered:
        return lowered[value.lower()]
    hits = [o for o in options if o.lower().startswith(value.lower())]
    if not hits:
        hits = [o for o in options if value.lower() in o.lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        die(f"ambiguous {label} '{value}' — matches: {', '.join(hits)}")
    die(f"invalid {label} '{value}'. choose one of: {', '.join(options)}")


def split_csv(value):
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def valid_parent(board: dict, issue: dict, parent_key: str) -> str:
    """Resolve a parent key, refusing self-parenting and ancestry cycles."""
    parent = find(board, parent_key)["key"]
    by_key = {i["key"]: i for i in board["issues"]}
    cur, seen = parent, set()
    while cur and cur not in seen:
        if cur == issue["key"]:
            die(f"parent {parent} would create a cycle with {issue['key']}")
        seen.add(cur)
        cur = by_key.get(cur, {}).get("parent")
    return parent


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_init(args):
    p = board_path(args)
    if p.exists() and not args.force:
        die(f"board already exists at {p} (use --force to overwrite)")
    key = (args.key or "JT").upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", key):
        die(f"invalid key '{key}' — use 2-10 letters/digits starting with a letter, e.g. PAY")
    board = {
        "project": {
            "key": key,
            "name": args.name or "Untitled Project",
            "repo": args.repo or "",
            "created": now(),
            "updated": now(),
            "counter": 0,
        },
        "types": TYPES,
        "statuses": STATUSES,
        "priorities": PRIORITIES,
        "issues": [],
    }
    out = save(board, args)
    print(f"initialized board '{board['project']['name']}' [{key}] at {p}")
    print(f"rendered board -> {out}")


def cmd_add(args):
    board = load(args)
    itype = fuzzy(args.type, TYPES, "type")
    priority = fuzzy(args.priority, PRIORITIES, "priority") if args.priority else "Medium"
    status = fuzzy(args.status, STATUSES, "status") if args.status else "To Do"
    parent = None
    if args.parent:
        parent = find(board, args.parent)["key"]  # validates existence
    issue = {
        "key": next_key(board),
        "type": itype,
        "title": args.title,
        "description": args.desc or "",
        "status": status,
        "priority": priority,
        "parent": parent,
        "labels": split_csv(args.labels),
        "components": split_csv(args.components),
        "assignee": args.assignee or "",
        "created": now(),
        "updated": now(),
        "comments": [],
    }
    board["issues"].append(issue)
    save(board, args)
    print(f"created {issue['key']}  {itype}: {issue['title']}  [{status} / {priority}]"
          + (f"  ^{parent}" if parent else ""))


def cmd_move(args):
    board = load(args)
    issue = find(board, args.key)
    status = fuzzy(args.status, STATUSES, "status")
    old = issue["status"]
    issue["status"] = status
    issue["updated"] = now()
    if args.comment:
        issue["comments"].append({"author": args.author or "agent", "at": now(), "body": args.comment})
    save(board, args)
    print(f"{issue['key']}: {old} -> {status}")


def cmd_comment(args):
    board = load(args)
    issue = find(board, args.key)
    issue["comments"].append({"author": args.author or "agent", "at": now(), "body": args.body})
    issue["updated"] = now()
    save(board, args)
    print(f"{issue['key']}: comment added ({len(issue['comments'])} total)")


def cmd_set(args):
    board = load(args)
    issue = find(board, args.key)
    changed = []
    if args.title is not None:
        issue["title"] = args.title; changed.append("title")
    if args.desc is not None:
        issue["description"] = args.desc; changed.append("description")
    if args.priority is not None:
        issue["priority"] = fuzzy(args.priority, PRIORITIES, "priority"); changed.append("priority")
    if args.type is not None:
        issue["type"] = fuzzy(args.type, TYPES, "type"); changed.append("type")
    if args.parent is not None:
        issue["parent"] = None if args.parent == "" else valid_parent(board, issue, args.parent); changed.append("parent")
    if args.assignee is not None:
        issue["assignee"] = args.assignee; changed.append("assignee")
    if args.labels is not None:
        issue["labels"] = split_csv(args.labels); changed.append("labels")
    if args.components is not None:
        issue["components"] = split_csv(args.components); changed.append("components")
    if not changed:
        die("nothing to set — pass at least one field flag")
    issue["updated"] = now()
    save(board, args)
    print(f"{issue['key']}: updated {', '.join(changed)}")


def _matches(issue, args):
    if getattr(args, "status", None) and issue["status"] != fuzzy(args.status, STATUSES, "status"):
        return False
    if getattr(args, "type", None) and issue["type"] != fuzzy(args.type, TYPES, "type"):
        return False
    if getattr(args, "parent", None) and issue.get("parent") != args.parent.upper():
        return False
    return True


def _sort_key(issue):
    # In Progress first, then To Do, then In Review; within, by priority then age.
    status_order = {"In Progress": 0, "To Do": 1, "In Review": 2, "Done": 3, "Cancelled": 4}
    return (status_order.get(issue["status"], 9),
            PRIORITY_RANK.get(issue["priority"], 9),
            issue["created"])


def cmd_list(args):
    board = load(args)
    issues = [i for i in board["issues"] if _matches(i, args)]
    if not args.all and not args.status:  # an explicit --status filter implies --all
        issues = [i for i in issues if i["status"] in OPEN_STATUSES]
    issues.sort(key=_sort_key)
    if not issues:
        print("(no matching issues)")
        return
    print(f"{board['project']['name']} [{board['project']['key']}] — {len(issues)} issue(s)\n")
    for i in issues:
        parent = f"  ^{i['parent']}" if i.get("parent") else ""
        cc = f"  💬{len(i['comments'])}" if i["comments"] else ""
        print(f"  {i['key']:<8} {i['type']:<8} {i['priority']:<7} {i['status']:<12} {i['title']}{parent}{cc}")


def cmd_next(args):
    board = load(args)
    candidates = [i for i in board["issues"]
                  if i["type"] != "Epic" and i["status"] in {"To Do", "In Progress"}]
    candidates.sort(key=_sort_key)
    top = candidates[: args.limit]
    if not top:
        print("nothing actionable — all work is Done, Cancelled, or In Review.")
        return
    print("recommended next:\n")
    for n, i in enumerate(top, 1):
        marker = "▶ (resume)" if i["status"] == "In Progress" else ""
        print(f"  {n}. {i['key']:<8} [{i['priority']}] {i['type']}: {i['title']}  {marker}")


def cmd_show(args):
    board = load(args)
    i = find(board, args.key)
    print(f"{i['key']}  {i['type']}  [{i['status']} / {i['priority']}]")
    print(f"title:      {i['title']}")
    if i.get("parent"):
        print(f"parent:     {i['parent']}")
    if i.get("assignee"):
        print(f"assignee:   {i['assignee']}")
    if i.get("labels"):
        print(f"labels:     {', '.join(i['labels'])}")
    if i.get("components"):
        print(f"components: {', '.join(i['components'])}")
    print(f"created:    {i['created']}")
    print(f"updated:    {i['updated']}")
    if i.get("description"):
        print(f"\n{i['description']}")
    if i["comments"]:
        print(f"\ncomments ({len(i['comments'])}):")
        for c in i["comments"]:
            print(f"  - [{c['at']}] {c['author']}: {c['body']}")


def cmd_status(args):
    board = load(args)
    issues = board["issues"]
    by_status = {s: 0 for s in STATUSES}
    for i in issues:
        by_status[i["status"]] = by_status.get(i["status"], 0) + 1
    print(f"{board['project']['name']} [{board['project']['key']}]"
          + (f" — {board['project']['repo']}" if board["project"].get("repo") else ""))
    print(f"{len(issues)} issue(s)")
    for s in STATUSES:
        if by_status.get(s):
            print(f"  {s:<12} {by_status[s]}")
    in_prog = [i for i in issues if i["status"] == "In Progress"]
    if in_prog:
        print("\nin progress:")
        for i in sorted(in_prog, key=_sort_key):
            print(f"  {i['key']:<8} {i['title']}")


def cmd_render(args):
    board = load(args)  # read-only: regenerate the view without touching the JSON
    out = board_path(args).with_name("board.html")
    render_html(board, out)
    print(f"rendered -> {out}")


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #

def render_html(board: dict, out_path: Path):
    data_json = json.dumps(board, ensure_ascii=False).replace("</", "<\\/")
    html_doc = HTML_TEMPLATE.replace("__BOARD_DATA__", data_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out_path, html_doc)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Work Tracker</title>
<script>
(function(){
  var t=null;
  try{t=localStorage.getItem('jt-theme');}catch(e){}
  if(t!=='dark'&&t!=='light'){
    t=(window.matchMedia&&matchMedia('(prefers-color-scheme: light)').matches)?'light':'dark';
  }
  document.documentElement.dataset.theme=t;
})();
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root,:root[data-theme="dark"]{
    --bg:#0b0d12; --panel:#11141a; --card:#171b23; --line:#232833; --line-2:#2e3645;
    --ink:#e8eaf0; --muted:#9aa3b5; --faint:#5b6778;
    --brand-a:#7c6cf0; --brand-b:#4f9cf7;
    --key-bg:#7ee787; --key-ink:#0b0d12;
    --scrim:rgba(4,6,10,.45);
    --shadow:0 1px 3px rgba(0,0,0,.35);
    --shadow-hover:0 8px 20px rgba(0,0,0,.45);
    --shadow-drawer:-16px 0 48px rgba(0,0,0,.5);
    --glow-a:rgba(124,108,240,.10); --glow-b:rgba(79,156,247,.07);
    --todo:#8b93a7; --prog:#d29922; --review:#58a6ff; --done:#3fb950; --cancel:#a07878;
    --epic:#a371f7; --story:#3fb950; --task:#58a6ff; --bug:#f85149; --sub:#8b93a7;
    --pri-highest:#f85149; --pri-high:#f0883e; --pri-medium:#d29922; --pri-low:#3fb950; --pri-lowest:#8b93a7;
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
    color-scheme:dark;
  }
  :root[data-theme="light"]{
    --bg:#f6f7f9; --panel:#eceef2; --card:#ffffff; --line:#e3e7ee; --line-2:#d3dae4;
    --ink:#1f2733; --muted:#5b6678; --faint:#94a0b1;
    --brand-a:#7c3aed; --brand-b:#2563eb;
    --key-bg:#16a34a; --key-ink:#ffffff;
    --scrim:rgba(15,23,42,.28);
    --shadow:0 1px 2px rgba(16,24,40,.07);
    --shadow-hover:0 8px 20px rgba(16,24,40,.12);
    --shadow-drawer:-16px 0 48px rgba(16,24,40,.18);
    --glow-a:rgba(124,58,237,.05); --glow-b:rgba(37,99,235,.04);
    --todo:#64748b; --prog:#b45309; --review:#2563eb; --done:#16a34a; --cancel:#9f6b6b;
    --epic:#7c3aed; --story:#16a34a; --task:#2563eb; --bug:#dc2626; --sub:#64748b;
    --pri-highest:#dc2626; --pri-high:#ea580c; --pri-medium:#b45309; --pri-low:#16a34a; --pri-lowest:#64748b;
    color-scheme:light;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;
    background-image:radial-gradient(900px 360px at 12% -8%,var(--glow-a),transparent 60%),
      radial-gradient(900px 360px at 92% -4%,var(--glow-b),transparent 55%);
    background-repeat:no-repeat}
  a{color:inherit}
  button{font-family:inherit}

  /* header (scrolls away) */
  header{padding:26px 28px 14px}
  .brand{display:flex;align-items:center;gap:11px;flex-wrap:wrap}
  .mark{width:26px;height:26px;border-radius:8px;flex:none;
    background:linear-gradient(135deg,var(--brand-a),var(--brand-b));
    box-shadow:0 2px 8px color-mix(in srgb,var(--brand-a) 35%,transparent)}
  .brand h1{font-size:20px;margin:0;font-weight:800;letter-spacing:-.02em}
  .key-tag{font-family:var(--mono);font-size:11.5px;color:var(--key-ink);background:var(--key-bg);
    padding:2px 8px;border-radius:5px;font-weight:700}
  .repo{font-family:var(--mono);font-size:12px;color:var(--faint)}
  .stats{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
  .stat{font-family:var(--mono);font-size:11.5px;color:var(--muted);background:var(--card);
    border:1px solid var(--line);border-radius:999px;padding:4px 12px;display:flex;gap:7px;
    align-items:center;box-shadow:var(--shadow)}
  .stat b{color:var(--ink);font-weight:700}
  .dot{width:8px;height:8px;border-radius:50%;flex:none}

  /* sticky toolbar */
  .toolbar{position:sticky;top:0;z-index:30;display:flex;gap:8px;align-items:center;flex-wrap:wrap;
    padding:12px 28px;border-bottom:1px solid transparent;
    transition:padding .18s ease,box-shadow .18s ease,border-color .18s ease,background .18s ease}
  .toolbar.stuck{padding:8px 28px;border-bottom-color:var(--line);background:var(--bg);
    background:color-mix(in srgb,var(--bg) 86%,transparent);
    -webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);box-shadow:var(--shadow)}
  .toolbar .mini{display:none}
  .toolbar.stuck .mini{display:inline-block}
  .seg{display:inline-flex;background:var(--card);border:1px solid var(--line);border-radius:9px;
    overflow:hidden;box-shadow:var(--shadow)}
  .seg button{background:transparent;color:var(--muted);border:0;padding:7px 14px;
    font-size:12.5px;font-weight:600;cursor:pointer;transition:background .15s,color .15s}
  .seg button.on{background:color-mix(in srgb,var(--brand-b) 16%,transparent);color:var(--ink)}
  select,input[type="search"]{background:var(--card);color:var(--ink);border:1px solid var(--line);
    border-radius:9px;padding:7px 10px;font-family:var(--sans);font-size:12.5px;box-shadow:var(--shadow);
    outline:none;transition:border-color .15s}
  select:hover,input[type="search"]:hover{border-color:var(--line-2)}
  select:focus,input[type="search"]:focus{border-color:var(--brand-b)}
  select{cursor:pointer;max-width:250px}
  input[type="search"]{width:190px}
  input[type="search"]::placeholder{color:var(--faint)}
  .clear-btn{background:transparent;border:0;color:var(--muted);font-size:12px;cursor:pointer;
    text-decoration:underline dotted;padding:6px 4px}
  .clear-btn:hover{color:var(--ink)}
  .theme-btn{margin-left:auto;background:var(--card);border:1px solid var(--line);border-radius:999px;
    width:34px;height:34px;cursor:pointer;font-size:15px;line-height:1;box-shadow:var(--shadow)}
  .theme-btn:hover{border-color:var(--line-2)}

  main{padding:16px 28px 80px}

  /* board view */
  .cols{display:grid;grid-template-columns:repeat(5,minmax(220px,1fr));gap:14px;align-items:start}
  @media(max-width:1100px){.cols{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:640px){.cols{grid-template-columns:1fr}}
  .col{background:var(--panel);border:1px solid var(--line);border-radius:14px;min-height:80px}
  .col h2{font-size:11px;font-family:var(--mono);text-transform:uppercase;letter-spacing:.07em;
    margin:0;padding:12px 14px 8px;display:flex;justify-content:space-between;align-items:center;
    color:var(--muted)}
  .col h2 .count{background:var(--card);border:1px solid var(--line);border-radius:999px;
    padding:1px 9px;color:var(--ink)}
  .col .stack{padding:10px;padding-top:2px;display:flex;flex-direction:column;gap:9px}
  .card{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--tc,var(--line-2));
    border-radius:10px;padding:11px 12px;cursor:pointer;box-shadow:var(--shadow);
    transition:transform .12s ease,box-shadow .12s ease,border-color .12s ease}
  .card:hover{transform:translateY(-2px);box-shadow:var(--shadow-hover);border-color:var(--line-2)}
  .card .top{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:7px}
  .ttype{font-family:var(--mono);font-size:9.5px;font-weight:700;text-transform:uppercase;
    letter-spacing:.05em;padding:2px 7px;border-radius:5px;white-space:nowrap}
  .ckey{font-family:var(--mono);font-size:11px;color:var(--faint)}
  .card .title{font-size:13.5px;line-height:1.4;font-weight:600;overflow-wrap:break-word}
  .card .meta{display:flex;gap:8px;align-items:center;margin-top:9px;flex-wrap:wrap}
  .pri{font-family:var(--mono);font-size:10px;display:flex;align-items:center;gap:4px}
  .pbar{display:inline-block;width:7px;height:7px;border-radius:2.5px}
  .chip{font-family:var(--mono);font-size:10px;color:var(--muted);background:var(--panel);
    border:1px solid var(--line);border-radius:5px;padding:1px 6px;max-width:140px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .cc{font-family:var(--mono);font-size:10px;color:var(--faint)}
  .empty-col{padding:12px 6px 16px;color:var(--faint);font-size:12px;font-family:var(--mono)}

  /* by-epic view */
  .epic-group{margin-bottom:20px;border:1px solid var(--line);border-radius:14px;overflow:hidden;
    background:var(--panel);box-shadow:var(--shadow)}
  .epic-head{padding:13px 16px;display:flex;align-items:center;gap:10px;cursor:pointer;flex-wrap:wrap}
  .epic-head:hover{background:color-mix(in srgb,var(--card) 55%,transparent)}
  .epic-head .title{font-weight:700;font-size:14px}
  .prog{display:flex;align-items:center;gap:8px;margin-left:auto}
  .prog-bar{width:90px;height:4px;border-radius:999px;background:var(--line);overflow:hidden}
  .prog-bar i{display:block;height:100%;background:var(--done);border-radius:999px}
  .prog-n{font-family:var(--mono);font-size:10px;color:var(--faint);white-space:nowrap}
  .pill{font-family:var(--mono);font-size:10px;padding:2px 9px;border-radius:999px;
    white-space:nowrap;font-weight:700}
  .epic-body{padding:6px 12px 12px;display:flex;flex-direction:column;gap:7px}
  .row{display:flex;align-items:center;gap:12px;padding:9px 11px;border-radius:9px;cursor:pointer;
    background:var(--card);border:1px solid var(--line);box-shadow:var(--shadow);flex-wrap:wrap;
    transition:transform .12s,box-shadow .12s,border-color .12s}
  .row:hover{transform:translateY(-1px);box-shadow:var(--shadow-hover);border-color:var(--line-2)}
  .row .title{flex:1;font-size:13.5px;min-width:160px}
  .note{color:var(--faint);font-family:var(--mono);font-size:11px;padding:6px 4px}
  .empty{color:var(--muted);font-family:var(--mono);font-size:13px;padding:48px 20px;text-align:center}
  .empty a{color:var(--review)}

  /* drawer */
  .scrim{position:fixed;inset:0;background:var(--scrim);z-index:50;opacity:0;pointer-events:none;
    transition:opacity .2s ease}
  .scrim.on{opacity:1;pointer-events:auto}
  .drawer{position:fixed;top:0;right:0;bottom:0;width:min(480px,100vw);z-index:60;background:var(--card);
    border-left:1px solid var(--line);box-shadow:var(--shadow-drawer);padding:22px 26px 40px;
    overflow-y:auto;transform:translateX(103%);transition:transform .24s cubic-bezier(.2,.8,.2,1)}
  .drawer.on{transform:translateX(0)}
  @media(max-width:640px){.drawer{width:100vw;border-left:0}}
  .d-head{display:flex;align-items:center;gap:9px}
  .d-head .x{margin-left:auto;background:transparent;border:1px solid var(--line);color:var(--muted);
    border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:14px;line-height:1}
  .d-head .x:hover{color:var(--ink);border-color:var(--line-2)}
  .d-title{margin:12px 0 10px;font-size:19px;line-height:1.35;letter-spacing:-.01em}
  .d-pills{display:flex;gap:6px;flex-wrap:wrap}
  .d-sec{font-family:var(--mono);font-size:10px;font-weight:700;letter-spacing:.08em;color:var(--faint);
    text-transform:uppercase;margin:20px 0 8px;padding-top:14px;border-top:1px solid var(--line)}
  .d-desc{color:var(--muted);line-height:1.6;white-space:pre-wrap;font-size:13.5px;overflow-wrap:break-word}
  .d-grid{display:grid;grid-template-columns:92px 1fr;gap:9px 14px;font-size:13px;margin:0}
  .d-grid dt{color:var(--faint);font-family:var(--mono);font-size:11px;padding-top:1px}
  .d-grid dd{margin:0;overflow-wrap:break-word}
  .plink{color:var(--epic);text-decoration:underline dotted;cursor:pointer}
  .cmt{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:10px 12px;
    margin-bottom:8px}
  .cmt .h{font-family:var(--mono);font-size:10.5px;color:var(--faint);margin-bottom:4px}
  .cmt .b{font-size:13px;line-height:1.55;color:var(--muted);white-space:pre-wrap;overflow-wrap:break-word}

  footer{padding:20px 28px;color:var(--faint);font-family:var(--mono);font-size:11px;
    border-top:1px solid var(--line)}
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="mark"></span>
    <h1 id="pname"></h1>
    <span class="key-tag" id="pkey"></span>
    <span class="repo" id="prepo"></span>
  </div>
  <div class="stats" id="stats"></div>
</header>

<div id="sentinel"></div>
<nav class="toolbar" id="toolbar">
  <span class="key-tag mini" id="pkey2"></span>
  <div class="seg" id="viewseg">
    <button data-view="board" class="on">Board</button>
    <button data-view="epics">By Epic</button>
  </div>
  <select id="fepic" title="filter by epic"></select>
  <select id="ftype" title="filter by type"></select>
  <select id="fpri" title="filter by priority"></select>
  <input id="fsearch" type="search" placeholder="search key or title…" title="search by key or title">
  <button class="clear-btn" id="fclear" hidden>✕ clear</button>
  <button class="theme-btn" id="ftheme" title="toggle light/dark theme">🌙</button>
</nav>

<main id="main"></main>
<footer id="foot"></footer>

<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer" aria-hidden="true"></aside>

<script id="board-data" type="application/json">__BOARD_DATA__</script>
<script>
const BOARD=JSON.parse(document.getElementById('board-data').textContent);
const STATUSES=BOARD.statuses, PRIORITIES=BOARD.priorities;
const TYPE_COLOR={Epic:'var(--epic)',Story:'var(--story)',Task:'var(--task)',Bug:'var(--bug)','Sub-task':'var(--sub)'};
const STATUS_COLOR={'To Do':'var(--todo)','In Progress':'var(--prog)','In Review':'var(--review)','Done':'var(--done)','Cancelled':'var(--cancel)'};
const PRI_COLOR={Highest:'var(--pri-highest)',High:'var(--pri-high)',Medium:'var(--pri-medium)',Low:'var(--pri-low)',Lowest:'var(--pri-lowest)'};
const tcol=t=>TYPE_COLOR[t]||'var(--muted)';
const scol=s=>STATUS_COLOR[s]||'var(--muted)';
const pcol=p=>PRI_COLOR[p]||'var(--muted)';
const tint=(c,p)=>`color-mix(in srgb, ${c} ${p}%, transparent)`;
const esc=s=>(s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const trunc=(s,n)=>{s=s==null?'':String(s);return s.length>n?s.slice(0,n-1)+'…':s};
let view='board', fEpic='', fType='', fPri='', q='';

const epicsOf=()=>BOARD.issues.filter(i=>i.type==='Epic');
const isOrphan=i=>i.type!=='Epic'&&!epicsOf().some(e=>e.key===i.parent);
function matchEpic(i){
  if(!fEpic)return true;
  if(fEpic==='__none__')return isOrphan(i);
  return i.key===fEpic||i.parent===fEpic;
}
function matchSearch(i){
  if(!q)return true;
  const s=q.toLowerCase();
  return i.key.toLowerCase().includes(s)||(i.title||'').toLowerCase().includes(s);
}
function passes(i,skip){
  return (skip==='epic'||matchEpic(i))
    &&(skip==='type'||!fType||i.type===fType)
    &&(skip==='pri'||!fPri||i.priority===fPri)
    &&matchSearch(i);
}
const visible=()=>BOARD.issues.filter(i=>passes(i));

function header(){
  const p=BOARD.project;
  document.getElementById('pname').textContent=p.name;
  document.getElementById('pkey').textContent=p.key;
  document.getElementById('pkey2').textContent=p.key;
  document.getElementById('prepo').textContent=p.repo||'';
  document.title=p.name+' · Work Tracker';
  const counts={};STATUSES.forEach(s=>counts[s]=0);
  BOARD.issues.forEach(i=>counts[i.status]=(counts[i.status]||0)+1);
  document.getElementById('stats').innerHTML=
    `<span class="stat"><b>${BOARD.issues.length}</b> total</span>`+
    STATUSES.filter(s=>counts[s]).map(s=>`<span class="stat"><span class="dot" style="background:${scol(s)}"></span>${esc(s)} <b>${counts[s]}</b></span>`).join('');
  document.getElementById('foot').textContent=
    `source: .jira/board.json · regenerate with: python jira.py render · updated ${p.updated}`;
}
function setOpts(id,opts,cur){
  document.getElementById(id).innerHTML=opts.map(o=>
    `<option value="${esc(o.v)}"${o.v===cur?' selected':''}>${esc(o.label)}</option>`).join('');
}
function fillFilters(){
  const base=f=>BOARD.issues.filter(i=>passes(i,f));
  const eb=base('epic');
  const eOpts=[{v:'',label:`Epic · all (${eb.length})`}]
    .concat(epicsOf().map(e=>({v:e.key,label:`${e.key} · ${trunc(e.title,32)} (${eb.filter(i=>i.key===e.key||i.parent===e.key).length})`})))
    .concat([{v:'__none__',label:`— no epic (${eb.filter(isOrphan).length})`}]);
  setOpts('fepic',eOpts,fEpic);
  const tb=base('type');
  setOpts('ftype',[{v:'',label:`Type · all (${tb.length})`}]
    .concat(BOARD.types.map(t=>({v:t,label:`${t} (${tb.filter(i=>i.type===t).length})`}))),fType);
  const pb=base('pri');
  setOpts('fpri',[{v:'',label:`Priority · all (${pb.length})`}]
    .concat(PRIORITIES.map(p=>({v:p,label:`${p} (${pb.filter(i=>i.priority===p).length})`}))),fPri);
  document.getElementById('fclear').hidden=!(fEpic||fType||fPri||q);
}
function clearFilters(){
  fEpic=fType=fPri=q='';
  document.getElementById('fsearch').value='';
  render();
}
const noMatch=()=>`<div class="empty">No issues match the current filters. <a href="#" onclick="clearFilters();return false">Clear filters</a></div>`;
const noIssues=()=>`<div class="empty">No issues yet. Create one with: python jira.py add --type Task --title "..."</div>`;

function cardHTML(i){
  const labels=(i.labels||[]).map(l=>`<span class="chip">${esc(l)}</span>`).join('');
  const cc=(i.comments&&i.comments.length)?`<span class="cc">💬 ${i.comments.length}</span>`:'';
  return `<div class="card" style="--tc:${tcol(i.type)}" onclick="openIssue('${esc(i.key)}')">
    <div class="top">
      <span class="ttype" style="background:${tint(tcol(i.type),14)};color:${tcol(i.type)}">${esc(i.type)}</span>
      <span class="ckey">${esc(i.key)}</span>
    </div>
    <div class="title">${esc(i.title)}</div>
    <div class="meta">
      <span class="pri" style="color:${pcol(i.priority)}"><span class="pbar" style="background:${pcol(i.priority)}"></span>${esc(i.priority)}</span>
      ${labels}${cc}
    </div></div>`;
}
function renderBoard(){
  if(!BOARD.issues.length){document.getElementById('main').innerHTML=noIssues();return;}
  const items=visible();
  if(!items.length){document.getElementById('main').innerHTML=noMatch();return;}
  document.getElementById('main').innerHTML='<div class="cols">'+STATUSES.map(s=>{
    const col=items.filter(i=>i.status===s);
    const cards=col.length?col.map(cardHTML).join(''):'<div class="empty-col">—</div>';
    return `<div class="col"><h2><span><span class="dot" style="background:${scol(s)};display:inline-block;margin-right:7px"></span>${esc(s)}</span><span class="count">${col.length}</span></h2><div class="stack">${cards}</div></div>`;
  }).join('')+'</div>';
}
function rowHTML(i){
  return `<div class="row" onclick="openIssue('${esc(i.key)}')">
    <span class="ttype" style="background:${tint(tcol(i.type),14)};color:${tcol(i.type)}">${esc(i.type)}</span>
    <span class="ckey">${esc(i.key)}</span>
    <span class="title">${esc(i.title)}</span>
    <span class="pri" style="color:${pcol(i.priority)}"><span class="pbar" style="background:${pcol(i.priority)}"></span>${esc(i.priority)}</span>
    <span class="pill" style="background:${tint(scol(i.status),14)};color:${scol(i.status)}">${esc(i.status)}</span></div>`;
}
function renderEpics(){
  if(!BOARD.issues.length){document.getElementById('main').innerHTML=noIssues();return;}
  const eps=epicsOf();
  const items=visible();
  const childFilter=!!(fType||fPri||q);
  let html='';
  eps.forEach(e=>{
    if(fEpic&&fEpic!==e.key)return;
    const kidsAll=BOARD.issues.filter(i=>i.parent===e.key);
    const kids=items.filter(i=>i.parent===e.key);
    if(!fEpic&&childFilter&&!kids.length)return;
    const done=kidsAll.filter(k=>k.status==='Done').length;
    const pct=kidsAll.length?Math.round(done/kidsAll.length*100):0;
    const body=kids.length?kids.map(rowHTML).join('')
      :(childFilter?'<div class="note">no matching issues</div>':'<div class="note">no child issues yet</div>');
    html+=`<section class="epic-group"><div class="epic-head" onclick="openIssue('${esc(e.key)}')">
      <span class="ttype" style="background:${tint(tcol('Epic'),14)};color:${tcol('Epic')}">Epic</span>
      <span class="ckey">${esc(e.key)}</span><span class="title">${esc(e.title)}</span>
      <span class="prog"><span class="prog-bar"><i style="width:${pct}%"></i></span><span class="prog-n">${done}/${kidsAll.length} done</span></span>
      <span class="pill" style="background:${tint(scol(e.status),14)};color:${scol(e.status)}">${esc(e.status)}</span></div>
      <div class="epic-body">${body}</div></section>`;
  });
  if(!fEpic||fEpic==='__none__'){
    const orphans=items.filter(isOrphan);
    if(orphans.length){
      html+=`<section class="epic-group"><div class="epic-head" style="cursor:default"><span class="title" style="color:var(--muted)">No Epic</span></div><div class="epic-body">${orphans.map(rowHTML).join('')}</div></section>`;
    }
  }
  document.getElementById('main').innerHTML=html||noMatch();
}
function render(){header();fillFilters();view==='board'?renderBoard():renderEpics();}

function openIssue(key){
  const i=BOARD.issues.find(x=>x.key===key);if(!i)return;
  const d=document.getElementById('drawer');
  const par=i.parent?BOARD.issues.find(x=>x.key===i.parent):null;
  const parentCell=par
    ?`<a class="plink" onclick="openIssue('${esc(par.key)}')">${esc(par.key)} · ${esc(trunc(par.title,30))}</a>`
    :esc(i.parent||'—');
  const rows=[
    ['Parent',parentCell],
    ['Assignee',esc(i.assignee||'—')],
    ['Labels',(i.labels||[]).map(l=>`<span class="chip">${esc(l)}</span>`).join(' ')||'—'],
    ['Components',(i.components||[]).map(c=>`<span class="chip">${esc(c)}</span>`).join(' ')||'—'],
    ['Created',esc(i.created||'—')],
    ['Updated',esc(i.updated||'—')]
  ];
  d.innerHTML=`<div class="d-head">
      <span class="ttype" style="background:${tint(tcol(i.type),14)};color:${tcol(i.type)}">${esc(i.type)}</span>
      <span class="ckey">${esc(i.key)}</span>
      <button class="x" onclick="closeDrawer()" title="close">✕</button>
    </div>
    <h2 class="d-title">${esc(i.title)}</h2>
    <div class="d-pills">
      <span class="pill" style="background:${tint(scol(i.status),14)};color:${scol(i.status)}">${esc(i.status)}</span>
      <span class="pill" style="border:1px solid ${tint(pcol(i.priority),50)};color:${pcol(i.priority)}">${esc(i.priority)}</span>
    </div>
    ${i.description?`<div class="d-sec">Description</div><div class="d-desc">${esc(i.description)}</div>`:''}
    <div class="d-sec">Details</div>
    <dl class="d-grid">${rows.map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
    ${(i.comments&&i.comments.length)?`<div class="d-sec">Comments (${i.comments.length})</div>`+
      i.comments.map(c=>`<div class="cmt"><div class="h">${esc(c.author)} · ${esc(c.at)}</div><div class="b">${esc(c.body)}</div></div>`).join(''):''}`;
  document.getElementById('scrim').classList.add('on');
  d.classList.add('on');d.setAttribute('aria-hidden','false');d.scrollTop=0;
}
function closeDrawer(){
  document.getElementById('scrim').classList.remove('on');
  const d=document.getElementById('drawer');
  d.classList.remove('on');d.setAttribute('aria-hidden','true');
}

document.getElementById('scrim').addEventListener('click',closeDrawer);
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer();});
document.getElementById('viewseg').addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  view=b.dataset.view;
  [...document.querySelectorAll('#viewseg button')].forEach(x=>x.classList.toggle('on',x===b));
  render();
});
document.getElementById('fepic').addEventListener('change',e=>{fEpic=e.target.value;render();});
document.getElementById('ftype').addEventListener('change',e=>{fType=e.target.value;render();});
document.getElementById('fpri').addEventListener('change',e=>{fPri=e.target.value;render();});
document.getElementById('fsearch').addEventListener('input',e=>{q=e.target.value.trim();render();});
document.getElementById('fclear').addEventListener('click',clearFilters);
const themeBtn=document.getElementById('ftheme');
function themeIcon(){themeBtn.textContent=document.documentElement.dataset.theme==='light'?'☀️':'🌙';}
themeBtn.addEventListener('click',()=>{
  const next=document.documentElement.dataset.theme==='light'?'dark':'light';
  document.documentElement.dataset.theme=next;
  try{localStorage.setItem('jt-theme',next);}catch(err){}
  themeIcon();
});
themeIcon();
new IntersectionObserver(es=>{
  document.getElementById('toolbar').classList.toggle('stuck',!es[0].isIntersecting);
},{rootMargin:'-1px 0px 0px 0px'}).observe(document.getElementById('sentinel'));
render();
</script>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(prog="jira.py", description="Tiny Jira-style work tracker (JSON + HTML).")
    p.add_argument("--file", help=f"path to board json (default: {DEFAULT_FILE})")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a new board")
    s.add_argument("--name"); s.add_argument("--key"); s.add_argument("--repo")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("add", help="create an issue")
    s.add_argument("--type", required=True, help="Epic|Story|Task|Bug|Sub-task")
    s.add_argument("--title", required=True)
    s.add_argument("--desc"); s.add_argument("--priority"); s.add_argument("--status")
    s.add_argument("--parent"); s.add_argument("--labels"); s.add_argument("--components")
    s.add_argument("--assignee")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("move", help="change an issue's status")
    s.add_argument("key"); s.add_argument("status")
    s.add_argument("--comment"); s.add_argument("--author")
    s.set_defaults(func=cmd_move)

    s = sub.add_parser("comment", help="add a comment to an issue")
    s.add_argument("key"); s.add_argument("body"); s.add_argument("--author")
    s.set_defaults(func=cmd_comment)

    s = sub.add_parser("set", help="edit fields on an issue")
    s.add_argument("key")
    s.add_argument("--title"); s.add_argument("--desc"); s.add_argument("--priority")
    s.add_argument("--type"); s.add_argument("--parent"); s.add_argument("--assignee")
    s.add_argument("--labels"); s.add_argument("--components")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("list", help="list issues (open by default)")
    s.add_argument("--status"); s.add_argument("--type"); s.add_argument("--parent")
    s.add_argument("--all", action="store_true", help="include Done/Cancelled")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("next", help="recommend what to work on")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_next)

    s = sub.add_parser("show", help="show full issue detail")
    s.add_argument("key")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("status", help="board summary")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("render", help="regenerate board.html from board.json")
    s.set_defaults(func=cmd_render)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
