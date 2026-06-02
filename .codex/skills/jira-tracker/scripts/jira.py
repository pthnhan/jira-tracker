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
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save(board: dict, args, render: bool = True):
    p = board_path(args)
    p.parent.mkdir(parents=True, exist_ok=True)
    board["project"]["updated"] = now()
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(board, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
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
    """Match a status/type/priority case-insensitively, allow unique prefixes."""
    if value in options:
        return value
    lowered = {o.lower(): o for o in options}
    if value.lower() in lowered:
        return lowered[value.lower()]
    hits = [o for o in options if o.lower().startswith(value.lower())]
    if len(hits) == 1:
        return hits[0]
    die(f"invalid {label} '{value}'. choose one of: {', '.join(options)}")


def split_csv(value):
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_init(args):
    p = board_path(args)
    if p.exists() and not args.force:
        die(f"board already exists at {p} (use --force to overwrite)")
    key = (args.key or "JT").upper()
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
        issue["parent"] = None if args.parent == "" else find(board, args.parent)["key"]; changed.append("parent")
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
    if not args.all:
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
    board = load(args)
    out = save(board, args)  # re-saving also re-renders
    print(f"rendered -> {out}")


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #

def render_html(board: dict, out_path: Path):
    data_json = json.dumps(board, ensure_ascii=False).replace("</", "<\\/")
    html_doc = HTML_TEMPLATE.replace("__BOARD_DATA__", data_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_doc)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Work Tracker</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Sora:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --panel-2:#1c2230; --line:#2a3340;
    --ink:#e6edf3; --muted:#8b98a9; --faint:#5b6776;
    --accent:#7ee787; --accent-2:#58a6ff;
    --todo:#6e7681; --prog:#d29922; --review:#58a6ff; --done:#3fb950; --cancel:#6e4c4c;
    --epic:#a371f7; --story:#3fb950; --task:#58a6ff; --bug:#f85149; --sub:#8b98a9;
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Sora',system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
    background-image:radial-gradient(circle at 15% -10%,rgba(163,113,247,.08),transparent 40%),
      radial-gradient(circle at 90% 0%,rgba(88,166,255,.06),transparent 45%);}
  a{color:inherit}
  header{padding:22px 28px 14px;border-bottom:1px solid var(--line);position:sticky;top:0;
    background:rgba(13,17,23,.85);backdrop-filter:blur(8px);z-index:20}
  .brand{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
  .brand h1{font-size:19px;margin:0;font-weight:700;letter-spacing:-.01em}
  .key-tag{font-family:var(--mono);font-size:12px;color:var(--bg);background:var(--accent);
    padding:2px 8px;border-radius:5px;font-weight:700}
  .repo{font-family:var(--mono);font-size:12px;color:var(--faint)}
  .stats{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
  .stat{font-family:var(--mono);font-size:12px;color:var(--muted);
    border:1px solid var(--line);border-radius:999px;padding:4px 12px;display:flex;gap:7px;align-items:center}
  .stat b{color:var(--ink);font-weight:700}
  .dot{width:8px;height:8px;border-radius:50%}
  .controls{display:flex;gap:10px;align-items:center;padding:14px 28px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
  .seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
  .seg button{background:transparent;color:var(--muted);border:0;padding:7px 14px;font-family:var(--mono);
    font-size:12px;cursor:pointer}
  .seg button.on{background:var(--panel-2);color:var(--ink)}
  select{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;
    padding:7px 10px;font-family:var(--mono);font-size:12px}
  label.flt{font-family:var(--mono);font-size:11px;color:var(--faint);display:flex;flex-direction:column;gap:3px}
  main{padding:22px 28px 80px}
  /* board view */
  .cols{display:grid;grid-template-columns:repeat(5,minmax(220px,1fr));gap:14px;align-items:start}
  @media(max-width:1100px){.cols{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:640px){.cols{grid-template-columns:1fr}}
  .col{background:var(--panel);border:1px solid var(--line);border-radius:12px;min-height:80px}
  .col h2{font-size:12px;font-family:var(--mono);text-transform:uppercase;letter-spacing:.06em;
    margin:0;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;
    border-bottom:1px solid var(--line);color:var(--muted)}
  .col h2 .count{background:var(--panel-2);border-radius:999px;padding:1px 9px;color:var(--ink)}
  .col .stack{padding:10px;display:flex;flex-direction:column;gap:9px}
  .card{background:var(--panel-2);border:1px solid var(--line);border-left-width:3px;border-radius:9px;
    padding:11px 12px;cursor:pointer;transition:transform .08s,border-color .15s}
  .card:hover{transform:translateY(-2px);border-color:var(--faint)}
  .card .top{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:7px}
  .ttype{font-family:var(--mono);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
    padding:2px 7px;border-radius:4px}
  .ckey{font-family:var(--mono);font-size:11px;color:var(--faint)}
  .card .title{font-size:13.5px;line-height:1.35;font-weight:600}
  .card .meta{display:flex;gap:8px;align-items:center;margin-top:9px;flex-wrap:wrap}
  .pri{font-family:var(--mono);font-size:10px;display:flex;align-items:center;gap:4px;color:var(--muted)}
  .pbar{display:inline-block;width:7px;height:7px;border-radius:2px}
  .chip{font-family:var(--mono);font-size:10px;color:var(--muted);background:var(--panel);
    border:1px solid var(--line);border-radius:4px;padding:1px 6px}
  .cc{font-family:var(--mono);font-size:10px;color:var(--faint)}
  /* epic view */
  .epic-group{margin-bottom:22px;border:1px solid var(--line);border-radius:12px;overflow:hidden}
  .epic-head{background:var(--panel);padding:13px 16px;display:flex;align-items:center;gap:10px;cursor:pointer}
  .epic-head .title{font-weight:700}
  .epic-body{padding:6px 16px 14px;display:flex;flex-direction:column;gap:7px}
  .row{display:flex;align-items:center;gap:12px;padding:9px 10px;border-radius:8px;cursor:pointer;
    background:var(--panel-2);border:1px solid var(--line)}
  .row:hover{border-color:var(--faint)}
  .row .s{font-family:var(--mono);font-size:10px;padding:2px 8px;border-radius:999px;white-space:nowrap}
  .row .title{flex:1;font-size:13.5px}
  .empty{color:var(--faint);font-family:var(--mono);font-size:13px;padding:30px;text-align:center}
  /* modal */
  .scrim{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;
    padding:24px;z-index:50}
  .scrim.on{display:flex}
  .modal{background:var(--panel);border:1px solid var(--line);border-radius:14px;max-width:640px;width:100%;
    max-height:85vh;overflow:auto;padding:24px}
  .modal h3{margin:.2em 0 .1em;font-size:18px}
  .modal .desc{color:var(--muted);line-height:1.55;white-space:pre-wrap;margin:14px 0;font-size:14px}
  .modal .grid{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;font-size:13px;margin:12px 0}
  .modal .grid dt{color:var(--faint);font-family:var(--mono);font-size:11px;padding-top:2px}
  .cmts{border-top:1px solid var(--line);margin-top:16px;padding-top:14px}
  .cmt{background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:8px}
  .cmt .h{font-family:var(--mono);font-size:10.5px;color:var(--faint);margin-bottom:4px}
  .x{float:right;background:transparent;border:1px solid var(--line);color:var(--muted);border-radius:7px;
    width:30px;height:30px;cursor:pointer;font-size:15px}
  footer{padding:20px 28px;color:var(--faint);font-family:var(--mono);font-size:11px;border-top:1px solid var(--line)}
</style>
</head>
<body>
<header>
  <div class="brand">
    <h1 id="pname"></h1>
    <span class="key-tag" id="pkey"></span>
    <span class="repo" id="prepo"></span>
  </div>
  <div class="stats" id="stats"></div>
</header>

<div class="controls">
  <div class="seg" id="viewseg">
    <button data-view="board" class="on">Board</button>
    <button data-view="epics">By Epic</button>
  </div>
  <label class="flt">type
    <select id="ftype"><option value="">all</option></select>
  </label>
  <label class="flt">priority
    <select id="fpri"><option value="">all</option></select>
  </label>
</div>

<main id="main"></main>
<footer id="foot"></footer>

<div class="scrim" id="scrim"><div class="modal" id="modal"></div></div>

<script id="board-data" type="application/json">__BOARD_DATA__</script>
<script>
const BOARD = JSON.parse(document.getElementById('board-data').textContent);
const STATUSES = BOARD.statuses;
const PRIORITIES = BOARD.priorities;
const TYPE_COLOR = {Epic:'var(--epic)',Story:'var(--story)',Task:'var(--task)',Bug:'var(--bug)','Sub-task':'var(--sub)'};
const STATUS_COLOR = {'To Do':'var(--todo)','In Progress':'var(--prog)','In Review':'var(--review)','Done':'var(--done)','Cancelled':'var(--cancel)'};
const PRI_COLOR = {Highest:'#f85149',High:'#f0883e',Medium:'#d29922',Low:'#3fb950',Lowest:'#6e7681'};
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let view='board', fType='', fPri='';

function visible(){
  return BOARD.issues.filter(i=>(!fType||i.type===fType)&&(!fPri||i.priority===fPri));
}
function header(){
  const p=BOARD.project;
  document.getElementById('pname').textContent=p.name;
  document.getElementById('pkey').textContent=p.key;
  document.getElementById('prepo').textContent=p.repo||'';
  document.title=p.name+' · Work Tracker';
  const counts={}; STATUSES.forEach(s=>counts[s]=0);
  BOARD.issues.forEach(i=>counts[i.status]=(counts[i.status]||0)+1);
  document.getElementById('stats').innerHTML =
    `<span class="stat"><b>${BOARD.issues.length}</b> total</span>` +
    STATUSES.filter(s=>counts[s]).map(s=>`<span class="stat"><span class="dot" style="background:${STATUS_COLOR[s]}"></span>${s} <b>${counts[s]}</b></span>`).join('');
  document.getElementById('foot').textContent =
    `source: .jira/board.json · regenerate with: python jira.py render · updated ${p.updated}`;
}
function fillFilters(){
  document.getElementById('ftype').innerHTML='<option value="">all</option>'+BOARD.types.map(t=>`<option>${t}</option>`).join('');
  document.getElementById('fpri').innerHTML='<option value="">all</option>'+PRIORITIES.map(p=>`<option>${p}</option>`).join('');
}
function cardHTML(i){
  const labels=(i.labels||[]).map(l=>`<span class="chip">${esc(l)}</span>`).join('');
  const cc=(i.comments&&i.comments.length)?`<span class="cc">💬 ${i.comments.length}</span>`:'';
  return `<div class="card" style="border-left-color:${TYPE_COLOR[i.type]||'var(--line)'}" onclick="openIssue('${i.key}')">
    <div class="top">
      <span class="ttype" style="background:${TYPE_COLOR[i.type]}22;color:${TYPE_COLOR[i.type]}">${i.type}</span>
      <span class="ckey">${i.key}</span>
    </div>
    <div class="title">${esc(i.title)}</div>
    <div class="meta">
      <span class="pri"><span class="pbar" style="background:${PRI_COLOR[i.priority]}"></span>${i.priority}</span>
      ${labels}${cc}
    </div></div>`;
}
function renderBoard(){
  const items=visible();
  document.getElementById('main').innerHTML='<div class="cols">'+STATUSES.map(s=>{
    const col=items.filter(i=>i.status===s);
    const cards=col.length?col.map(cardHTML).join(''):'<div style="padding:18px 4px;color:var(--faint);font-size:12px;font-family:var(--mono)">—</div>';
    return `<div class="col"><h2><span><span class="dot" style="background:${STATUS_COLOR[s]};display:inline-block;margin-right:7px"></span>${s}</span><span class="count">${col.length}</span></h2><div class="stack">${cards}</div></div>`;
  }).join('')+'</div>';
}
function renderEpics(){
  const items=visible();
  const epics=items.filter(i=>i.type==='Epic');
  const main=document.getElementById('main');
  let html='';
  const byParent=k=>items.filter(i=>i.parent===k);
  epics.forEach(e=>{
    const kids=byParent(e.key);
    html+=`<div class="epic-group"><div class="epic-head" onclick="openIssue('${e.key}')">
      <span class="ttype" style="background:${TYPE_COLOR.Epic}22;color:${TYPE_COLOR.Epic}">Epic</span>
      <span class="ckey">${e.key}</span><span class="title">${esc(e.title)}</span>
      <span class="row-s s" style="margin-left:auto;background:${STATUS_COLOR[e.status]}22;color:${STATUS_COLOR[e.status]}">${e.status}</span></div>`;
    html+='<div class="epic-body">'+(kids.length?kids.map(rowHTML).join(''):'<div class="cc" style="color:var(--faint);padding:6px">no child issues yet</div>')+'</div></div>';
  });
  const orphans=items.filter(i=>i.type!=='Epic'&&!epics.some(e=>e.key===i.parent));
  if(orphans.length){
    html+=`<div class="epic-group"><div class="epic-head"><span class="title" style="color:var(--muted)">No Epic</span></div><div class="epic-body">${orphans.map(rowHTML).join('')}</div></div>`;
  }
  main.innerHTML=html||'<div class="empty">No issues yet. Create one with: python jira.py add --type Task --title "..."</div>';
}
function rowHTML(i){
  return `<div class="row" onclick="openIssue('${i.key}')">
    <span class="ttype" style="background:${TYPE_COLOR[i.type]}22;color:${TYPE_COLOR[i.type]}">${i.type}</span>
    <span class="ckey">${i.key}</span>
    <span class="title">${esc(i.title)}</span>
    <span class="pri"><span class="pbar" style="background:${PRI_COLOR[i.priority]}"></span>${i.priority}</span>
    <span class="s" style="background:${STATUS_COLOR[i.status]}22;color:${STATUS_COLOR[i.status]}">${i.status}</span></div>`;
}
function render(){ header(); view==='board'?renderBoard():renderEpics(); }
function openIssue(key){
  const i=BOARD.issues.find(x=>x.key===key); if(!i)return;
  const m=document.getElementById('modal');
  const rows=[['Type',i.type],['Status',i.status],['Priority',i.priority],['Parent',i.parent||'—'],
    ['Assignee',i.assignee||'—'],['Labels',(i.labels||[]).join(', ')||'—'],
    ['Components',(i.components||[]).join(', ')||'—'],['Created',i.created],['Updated',i.updated]];
  m.innerHTML=`<button class="x" onclick="closeModal()">×</button>
    <span class="ttype" style="background:${TYPE_COLOR[i.type]}22;color:${TYPE_COLOR[i.type]}">${i.type}</span>
    <span class="ckey"> ${i.key}</span>
    <h3>${esc(i.title)}</h3>
    <dl class="grid">${rows.map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
    ${i.description?`<div class="desc">${esc(i.description)}</div>`:''}
    ${i.comments&&i.comments.length?`<div class="cmts"><div class="cc">comments (${i.comments.length})</div>${i.comments.map(c=>`<div class="cmt"><div class="h">${esc(c.author)} · ${esc(c.at)}</div>${esc(c.body)}</div>`).join('')}</div>`:''}`;
  document.getElementById('scrim').classList.add('on');
}
function closeModal(){document.getElementById('scrim').classList.remove('on');}
document.getElementById('scrim').addEventListener('click',e=>{if(e.target.id==='scrim')closeModal();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});
document.getElementById('viewseg').addEventListener('click',e=>{
  const b=e.target.closest('button'); if(!b)return;
  view=b.dataset.view;[...document.querySelectorAll('#viewseg button')].forEach(x=>x.classList.toggle('on',x===b));
  render();
});
document.getElementById('ftype').addEventListener('change',e=>{fType=e.target.value;render();});
document.getElementById('fpri').addEventListener('change',e=>{fPri=e.target.value;render();});
fillFilters(); render();
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
