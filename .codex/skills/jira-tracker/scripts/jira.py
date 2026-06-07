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
import contextlib
import datetime as _dt
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    import fcntl as _fcntl
    def _flock_exclusive(fh):
        _fcntl.flock(fh, _fcntl.LOCK_EX)
    def _flock_release(fh):
        _fcntl.flock(fh, _fcntl.LOCK_UN)
except ImportError:  # non-POSIX (Windows) — no locking, document the risk
    def _flock_exclusive(fh): pass   # noqa: no-op on non-POSIX platforms
    def _flock_release(fh): pass     # noqa: no-op on non-POSIX platforms

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

TEMPLATE_VERSION = 2

STALE_DAYS = 7


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


@contextlib.contextmanager
def board_lock(path: Path):
    """Exclusive advisory lock spanning the load→mutate→save cycle.

    Uses fcntl.flock on <board dir>/board.lock (POSIX).  On non-POSIX
    platforms (Windows) fcntl is unavailable and the lock is a no-op —
    concurrent writes on those platforms remain unprotected.
    """
    lock_path = path.parent / "board.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a", encoding="utf-8")
    try:
        _flock_exclusive(fh)
        yield
    finally:
        _flock_release(fh)
        fh.close()


def load(args) -> dict:
    p = board_path(args)
    if not p.exists():
        die(f"no board found at {p}. Run `jira.py init` first.")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            board = json.load(fh)
    except json.JSONDecodeError as e:
        die(f"could not parse {p} ({e}) — fix the JSON or restore it from git")
    stamp = board.get("template_version", 0)
    if not isinstance(stamp, int):
        die(f"board has a non-integer template_version ({stamp!r}) — fix the JSON or restore it from git")
    if stamp > TEMPLATE_VERSION:
        die(
            f"this board was written by a newer jira.py (template_version "
            f"{stamp} > {TEMPLATE_VERSION}). "
            f"Run the repo-local copy at .claude/skills/jira-tracker/scripts/jira.py"
        )
    return board


def write_atomic(path: Path, text: str):
    """Write via a unique tmp file + rename; cleans up on failure."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save(board: dict, args, render: bool = True):
    p = board_path(args)
    p.parent.mkdir(parents=True, exist_ok=True)
    board["project"]["updated"] = now()
    board["template_version"] = TEMPLATE_VERSION
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


def _stale_days(issue) -> int:
    """Return whole days since issue['updated'], or -1 if unparseable."""
    try:
        updated = _dt.datetime.fromisoformat(issue["updated"])
        now_dt = _dt.datetime.now().astimezone()
        # If the stored stamp is tz-naive (no UTC offset), attach local tz so
        # the subtraction doesn't raise TypeError.
        if updated.tzinfo is None:
            updated = updated.astimezone()
        return max(0, (now_dt - updated).days)
    except Exception:
        return -1


def _is_stale(issue) -> bool:
    """True when the issue is In Progress and updated >= STALE_DAYS ago."""
    if issue.get("status") != "In Progress":
        return False
    d = _stale_days(issue)
    return d >= STALE_DAYS


def resolve_parent(board: dict, parent_key: str, child_key: str = None) -> str:
    """Resolve a parent key; when child_key given, also refuse self-parent and cycles."""
    parent = find(board, parent_key)["key"]
    if child_key is None:
        return parent
    by_key = {i["key"]: i for i in board["issues"]}
    cur, seen = parent, set()
    while cur and cur not in seen:
        if cur == child_key:
            die(f"parent {parent} would create a cycle with {child_key}")
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
    with board_lock(p):
        out = save(board, args)
    print(f"initialized board '{board['project']['name']}' [{key}] at {p}")
    print(f"rendered board -> {out}")


def cmd_add(args):
    title = args.title.strip()
    if not title:
        die("title must not be empty")
    p = board_path(args)
    with board_lock(p):
        board = load(args)
        itype = fuzzy(args.type, TYPES, "type")
        priority = fuzzy(args.priority, PRIORITIES, "priority") if args.priority else "Medium"
        status = fuzzy(args.status, STATUSES, "status") if args.status else "To Do"
        parent = None
        if args.parent:
            parent = resolve_parent(board, args.parent)  # validates existence
        issue = {
            "key": next_key(board),
            "type": itype,
            "title": title,
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
    p = board_path(args)
    with board_lock(p):
        board = load(args)
        issue = find(board, args.key)
        status = fuzzy(args.status, STATUSES, "status")
        old = issue["status"]
        if old in CLOSED_STATUSES and status in OPEN_STATUSES and not args.comment:
            die(f"{issue['key']} is {old}; reopening requires --comment explaining why")
        issue["status"] = status
        issue["updated"] = now()
        if args.comment:
            issue["comments"].append({"author": args.author or "agent", "at": now(), "body": args.comment})
        save(board, args)
    print(f"{issue['key']}: {old} -> {status}")


def cmd_comment(args):
    p = board_path(args)
    with board_lock(p):
        board = load(args)
        issue = find(board, args.key)
        issue["comments"].append({"author": args.author or "agent", "at": now(), "body": args.body})
        issue["updated"] = now()
        save(board, args)
    print(f"{issue['key']}: comment added ({len(issue['comments'])} total)")


def cmd_set(args):
    p = board_path(args)
    with board_lock(p):
        board = load(args)
        issue = find(board, args.key)
        changed = []
        if args.title is not None:
            title = args.title.strip()
            if not title:
                die("title must not be empty")
            issue["title"] = title; changed.append("title")
        if args.desc is not None:
            issue["description"] = args.desc; changed.append("description")
        if args.priority is not None:
            issue["priority"] = fuzzy(args.priority, PRIORITIES, "priority"); changed.append("priority")
        if args.type is not None:
            issue["type"] = fuzzy(args.type, TYPES, "type"); changed.append("type")
        if args.parent is not None:
            issue["parent"] = None if args.parent == "" else resolve_parent(board, args.parent, issue["key"]); changed.append("parent")
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


def _matches(issue, args, canonical_parent=None):
    if getattr(args, "status", None) and issue["status"] != fuzzy(args.status, STATUSES, "status"):
        return False
    if getattr(args, "type", None) and issue["type"] != fuzzy(args.type, TYPES, "type"):
        return False
    if canonical_parent is not None:
        if issue.get("parent") != canonical_parent:
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
    canonical_parent = None
    if getattr(args, "parent", None):
        canonical_parent = find(board, args.parent)["key"]  # validate + die loudly on unknown key before filtering
    issues = [i for i in board["issues"] if _matches(i, args, canonical_parent)]
    if not args.all and not args.status:  # an explicit --status filter implies --all
        issues = [i for i in issues if i["status"] in OPEN_STATUSES]
    issues.sort(key=_sort_key)
    if getattr(args, "json", False):
        print(json.dumps({"issues": issues}, ensure_ascii=False))
        return
    if not issues:
        print("(no matching issues)")
        return
    print(f"{board['project']['name']} [{board['project']['key']}] — {len(issues)} issue(s)\n")
    for i in issues:
        parent = f"  ^{i['parent']}" if i.get("parent") else ""
        cc = f"  💬{len(i['comments'])}" if i["comments"] else ""
        print(f"  {i['key']:<8} {i['type']:<8} {i['priority']:<7} {i['status']:<12} {i['title']}{parent}{cc}")


def _open_blockers(issue, by_key):
    """Return list of open blocker keys for an issue."""
    return [k for k in issue.get("blocked_by", [])
            if k in by_key and by_key[k]["status"] not in CLOSED_STATUSES]


def cmd_next(args):
    board = load(args)
    by_key = {i["key"]: i for i in board["issues"]}
    actionable = [i for i in board["issues"]
                  if i["type"] != "Epic" and i["status"] in {"To Do", "In Progress"}]
    # Partition into unblocked recommendations and blocked issues
    blocked_list = []
    candidates = []
    for i in actionable:
        open_blk = _open_blockers(i, by_key)
        if open_blk:
            blocked_list.append({"key": i["key"], "blocked_by_open": open_blk})
        else:
            candidates.append(i)
    candidates.sort(key=_sort_key)
    top = candidates[: args.limit]
    if getattr(args, "json", False):
        print(json.dumps({"recommendations": top, "blocked": blocked_list}, ensure_ascii=False))
        return
    if not top and not blocked_list:
        print("nothing actionable — all work is Done, Cancelled, or In Review.")
        return
    if top:
        print("recommended next:\n")
        for n, i in enumerate(top, 1):
            marker = "▶ (resume)" if i["status"] == "In Progress" else ""
            sd = _stale_days(i)
            stale_note = f"  ⚠ stale {sd}d" if sd >= STALE_DAYS and i["status"] == "In Progress" else ""
            print(f"  {n}. {i['key']:<8} [{i['priority']}] {i['type']}: {i['title']}  {marker}{stale_note}")
    if blocked_list:
        print("\nblocked:\n")
        for entry in blocked_list:
            blk_str = ", ".join(
                f"{k} ({by_key[k]['status']})" if k in by_key else k
                for k in entry["blocked_by_open"]
            )
            print(f"  {entry['key']:<8} blocked by: {blk_str}")


def cmd_show(args):
    board = load(args)
    i = find(board, args.key)
    if getattr(args, "json", False):
        print(json.dumps(i, ensure_ascii=False))
        return
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
    # blocked_by / blocks
    blocked_by = i.get("blocked_by", [])
    if blocked_by:
        by_key = {x["key"]: x for x in board["issues"]}
        parts = [f"{k} ({by_key[k]['status']})" if k in by_key else k for k in blocked_by]
        print(f"blocked by: {', '.join(parts)}")
    # Compute reverse: which issues does this block?
    blocks = [x["key"] for x in board["issues"] if i["key"] in x.get("blocked_by", [])]
    if blocks:
        print(f"blocks:     {', '.join(blocks)}")
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
    in_prog = [i for i in issues if i["status"] == "In Progress"]
    stale_keys = [i["key"] for i in in_prog if _is_stale(i)]
    if getattr(args, "json", False):
        print(json.dumps({
            "project": board["project"],
            # counts covers only canonical statuses; non-canonical ones are omitted
            # here but still included in total (doctor flags them as invalid_status).
            "counts": {s: by_status.get(s, 0) for s in STATUSES},
            "total": len(issues),
            "in_progress": [i["key"] for i in in_prog],
            "stale": stale_keys,
        }, ensure_ascii=False))
        return
    print(f"{board['project']['name']} [{board['project']['key']}]"
          + (f" — {board['project']['repo']}" if board["project"].get("repo") else ""))
    print(f"{len(issues)} issue(s)")
    for s in STATUSES:
        if by_status.get(s):
            print(f"  {s:<12} {by_status[s]}")
    if in_prog:
        print("\nin progress:")
        for i in sorted(in_prog, key=_sort_key):
            sd = _stale_days(i)
            stale_note = f"  ⚠ stale {sd}d" if sd >= STALE_DAYS else ""
            print(f"  {i['key']:<8} {i['title']}{stale_note}")


def cmd_doctor(args):
    """Read-only integrity scan of the board."""
    board = load(args)
    issues = board["issues"]
    problems = []

    def prob(code, key, message):
        problems.append({"code": code, "key": key, "message": message})

    by_key = {}
    # Duplicate key check
    for i in issues:
        k = i.get("key")
        if not k:
            prob("missing_key", None, f"issue is missing a 'key' field: {i.get('title', '?')!r}")
            continue
        if k in by_key:
            prob("duplicate_key", k, f"duplicate key {k!r}")
        else:
            by_key[k] = i

    # Missing required fields
    for i in issues:
        k = i.get("key", "?")
        for field in ("title", "type", "status"):
            if not i.get(field):
                prob("missing_field", k, f"{k}: missing required field {field!r}")

    # Status/type/priority validity
    board_statuses = set(board.get("statuses", STATUSES))
    board_types = set(board.get("types", TYPES))
    board_priorities = set(board.get("priorities", PRIORITIES))
    for i in issues:
        k = i.get("key", "?")
        if i.get("status") and i["status"] not in board_statuses:
            prob("invalid_status", k, f"{k}: unknown status {i['status']!r}")
        if i.get("type") and i["type"] not in board_types:
            prob("invalid_type", k, f"{k}: unknown type {i['type']!r}")
        if i.get("priority") and i["priority"] not in board_priorities:
            prob("invalid_priority", k, f"{k}: unknown priority {i['priority']!r}")

    # Dangling parent references + parent cycles
    for i in issues:
        k = i.get("key", "?")
        parent = i.get("parent")
        if parent and parent not in by_key:
            prob("dangling_parent", k, f"{k}: parent {parent!r} does not exist")

    def _find_cycle_members(nodes, neighbors_fn):
        """Return the set of node keys that are TRUE members of a cycle.

        Uses iterative DFS with enter/exit markers (WHITE/GRAY/BLACK colouring).
        A back-edge (GRAY→GRAY) identifies a cycle; we then walk back along the
        ancestor stack to collect every node on that cycle path.  Nodes that
        merely point INTO a cycle — without being on it — are NOT included.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in nodes}
        cycle_members = set()

        for start in nodes:
            if color[start] != WHITE:
                continue
            # Stack entries: (node, iterator_of_neighbours, ancestor_path)
            path = []
            path_set = set()
            stack = [(start, iter(neighbors_fn(start)))]
            color[start] = GRAY
            path.append(start)
            path_set.add(start)

            while stack:
                node, nbr_iter = stack[-1]
                try:
                    nxt = next(nbr_iter)
                    if nxt not in nodes:
                        continue
                    if color[nxt] == GRAY:
                        # Back-edge: nxt is an ancestor — collect the cycle
                        idx = path.index(nxt)
                        cycle_members.update(path[idx:])
                    elif color[nxt] == WHITE:
                        color[nxt] = GRAY
                        path.append(nxt)
                        path_set.add(nxt)
                        stack.append((nxt, iter(neighbors_fn(nxt))))
                except StopIteration:
                    # Done with node — pop it
                    stack.pop()
                    color[node] = BLACK
                    if path and path[-1] == node:
                        path.pop()
                        path_set.discard(node)

        return cycle_members

    # Cycle in parent chain
    parent_cycle_members = _find_cycle_members(
        set(by_key),
        lambda k: [by_key[k]["parent"]] if by_key[k].get("parent") in by_key else [],
    )
    for k in parent_cycle_members:
        prob("parent_cycle", k, f"{k}: is part of a parent-chain cycle")

    # Dangling blocked_by references + blocked_by cycles
    for i in issues:
        k = i.get("key", "?")
        for blk in i.get("blocked_by", []):
            if blk not in by_key:
                prob("dangling_blocked_by", k, f"{k}: blocked_by {blk!r} does not exist")

    blocked_by_cycle_members = _find_cycle_members(
        set(by_key),
        lambda k: [n for n in by_key[k].get("blocked_by", []) if n in by_key],
    )
    for k in blocked_by_cycle_members:
        prob("blocked_by_cycle", k, f"{k}: is part of a blocked_by cycle")

    # Counter drift
    counter = board.get("project", {}).get("counter", 0)
    prefix = board.get("project", {}).get("key", "")
    max_suffix = 0
    for k in by_key:
        if k.startswith(prefix + "-"):
            try:
                max_suffix = max(max_suffix, int(k[len(prefix) + 1:]))
            except ValueError:
                pass
    if max_suffix > counter:
        prob("counter_drift", None,
             f"counter={counter} but max issue suffix is {max_suffix}")

    # Unparseable timestamps
    for i in issues:
        k = i.get("key", "?")
        for field in ("created", "updated"):
            val = i.get(field)
            if val:
                try:
                    _dt.datetime.fromisoformat(val)
                except ValueError:
                    prob("bad_timestamp", k, f"{k}: {field}={val!r} is not valid ISO-8601")
        for c in i.get("comments", []):
            val = c.get("at")
            if val:
                try:
                    _dt.datetime.fromisoformat(val)
                except ValueError:
                    prob("bad_timestamp", k, f"{k}: comment timestamp {val!r} is not valid ISO-8601")

    if getattr(args, "json", False):
        print(json.dumps({"problems": problems}, ensure_ascii=False))
        sys.exit(1 if problems else 0)

    if not problems:
        print("board is healthy")
        sys.exit(0)

    for p in problems:
        key_part = f"[{p['key']}] " if p["key"] else ""
        print(f"  {p['code']}: {key_part}{p['message']}")
    print(f"\n{len(problems)} problem(s) found")
    sys.exit(1)


def _blocked_by_has_cycle_adding(by_key, from_key, to_key) -> bool:
    """Return True if adding `to_key` to `from_key`'s blocked_by would create a cycle.

    A cycle exists if `from_key` is reachable from `to_key` following blocked_by edges
    (i.e., to_key → … → from_key).
    """
    visited, stack = set(), [to_key]
    while stack:
        cur = stack.pop()
        if cur == from_key:
            return True
        if cur in visited:
            continue
        visited.add(cur)
        for nxt in by_key.get(cur, {}).get("blocked_by", []):
            stack.append(nxt)
    return False


def cmd_link(args):
    p = board_path(args)
    with board_lock(p):
        board = load(args)
        by_key = {i["key"]: i for i in board["issues"]}
        issue = find(board, args.key)
        key = issue["key"]

        if args.blocked_by:
            other_key = find(board, args.blocked_by)["key"]
            if other_key == key:
                die(f"an issue cannot block itself ({key})")
            # Cycle check: does adding key blocked_by other_key create a cycle?
            if _blocked_by_has_cycle_adding(by_key, key, other_key):
                die(f"adding {other_key} to {key}'s blocked_by would create a cycle")
            current = issue.setdefault("blocked_by", [])
            if other_key in current:
                print(f"{key}: already blocked by {other_key} (no change)")
            else:
                current.append(other_key)
                current.sort()
                issue["updated"] = now()
                save(board, args)
                print(f"{key}: now blocked by {other_key}")

        elif args.unblock:
            other_key = find(board, args.unblock)["key"]
            current = issue.get("blocked_by", [])
            if other_key not in current:
                die(f"{other_key} is not in {key}'s blocked_by list")
            current.remove(other_key)
            issue["updated"] = now()
            save(board, args)
            print(f"{key}: unblocked from {other_key}")


def cmd_render(args):
    p = board_path(args)
    out = p.with_name("board.html")
    with board_lock(p):
        board = load(args)  # read-only: regenerate the view without touching the JSON
        render_html(board, out)
    print(f"rendered -> {out}")


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #

# Canonical statuses map to the theme palette vars (which adapt per light/dark).
CANONICAL_STATUS_VAR = {
    "To Do": "var(--todo)",
    "In Progress": "var(--prog)",
    "In Review": "var(--review)",
    "Done": "var(--done)",
    "Cancelled": "var(--cancel)",
}
# Deterministic fallback palette (8 slots) for any non-canonical status.
# Each slot is a CSS var defined per-theme in both :root blocks below so that
# the text colour vs tinted-pill AND text vs card-bg both pass WCAG AA (≥4.5:1).
# A 9th+ unknown status recycles colours (slot index wraps mod 8).
FALLBACK_STATUS_COUNT = 8


def status_color_map(statuses) -> dict:
    """Map every board status to a CSS colour string.

    Canonical names keep their theme palette var; unknown statuses are assigned
    a distinct, deterministic fallback CSS var (--fb-1..--fb-8) by the order in
    which they appear, wrapping every 8."""
    out = {}
    fb_i = 0
    for s in statuses:
        if s in CANONICAL_STATUS_VAR:
            out[s] = CANONICAL_STATUS_VAR[s]
        else:
            slot = (fb_i % FALLBACK_STATUS_COUNT) + 1
            out[s] = f"var(--fb-{slot})"
            fb_i += 1
    return out


def render_html(board: dict, out_path: Path):
    data_json = (
        json.dumps(board, ensure_ascii=False)
        .replace("</", "<\\/")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )
    statuses = board.get("statuses", STATUSES)
    ncols = max(1, len(statuses))
    scolor_json = json.dumps(status_color_map(statuses), ensure_ascii=False).replace("</", "<\\/")
    html_doc = (
        HTML_TEMPLATE
        .replace("__BOARD_DATA__", data_json)
        .replace("__STATUS_COLOR_JSON__", scolor_json)
        .replace("__NCOLS__", str(ncols))
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out_path, html_doc)


HTML_TEMPLATE = r"""<!-- jira-tracker template v2 -->
<!DOCTYPE html>
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
    --ink:#e8eaf0; --muted:#9aa3b5; --faint:#808da2;
    --brand-a:#7c6cf0; --brand-b:#4f9cf7;
    --focus:#6ea8ff;
    --key-bg:#7ee787; --key-ink:#0b0d12;
    --scrim:rgba(4,6,10,.45);
    --shadow:0 1px 3px rgba(0,0,0,.35);
    --shadow-hover:0 8px 20px rgba(0,0,0,.45);
    --shadow-drawer:-16px 0 48px rgba(0,0,0,.5);
    --glow-a:rgba(124,108,240,.10); --glow-b:rgba(79,156,247,.07);
    --todo:#8b93a7; --prog:#d29922; --review:#58a6ff; --done:#3fb950; --cancel:#b08585;
    --epic:#a371f7; --story:#3fb950; --task:#58a6ff; --bug:#f85149; --sub:#8b93a7;
    --pri-highest:#f85149; --pri-high:#f0883e; --pri-medium:#d29922; --pri-low:#3fb950; --pri-lowest:#8b93a7;
    --fb-1:#ff7eb3; --fb-2:#5ccfe6; --fb-3:#ffb347; --fb-4:#b39ddb;
    --fb-5:#69db7c; --fb-6:#ff8c69; --fb-7:#74b9ff; --fb-8:#e879f9;
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
    color-scheme:dark;
  }
  :root[data-theme="light"]{
    --bg:#f6f7f9; --panel:#eceef2; --card:#ffffff; --line:#e3e7ee; --line-2:#d3dae4;
    --ink:#1f2733; --muted:#5b6678; --faint:#677386;
    --brand-a:#7c3aed; --brand-b:#2563eb;
    --focus:#1d4ed8;
    --key-bg:#16a34a; --key-ink:#ffffff;
    --scrim:rgba(15,23,42,.28);
    --shadow:0 1px 2px rgba(16,24,40,.07);
    --shadow-hover:0 8px 20px rgba(16,24,40,.12);
    --shadow-drawer:-16px 0 48px rgba(16,24,40,.18);
    --glow-a:rgba(124,58,237,.05); --glow-b:rgba(37,99,235,.04);
    --todo:#64748b; --prog:#b45309; --review:#2563eb; --done:#0f7a37; --cancel:#8a5757;
    --epic:#7c3aed; --story:#0f7a37; --task:#2563eb; --bug:#dc2626; --sub:#64748b;
    --pri-highest:#dc2626; --pri-high:#c2410c; --pri-medium:#b45309; --pri-low:#0f7a37; --pri-lowest:#64748b;
    --fb-1:#9d174d; --fb-2:#0a5565; --fb-3:#92400e; --fb-4:#5b21b6;
    --fb-5:#14532d; --fb-6:#9a3412; --fb-7:#1e3a8a; --fb-8:#86198f;
    color-scheme:light;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;
    background-image:radial-gradient(900px 360px at 12% -8%,var(--glow-a),transparent 60%),
      radial-gradient(900px 360px at 92% -4%,var(--glow-b),transparent 55%);
    background-repeat:no-repeat}
  a{color:inherit}
  button{font-family:inherit}
  /* Visible keyboard-focus ring on every interactive element (JT-28). Uses the
     theme --focus var so it reads on both palettes; mouse focus stays quiet. */
  :focus{outline:none}
  a:focus-visible,button:focus-visible,select:focus-visible,input:focus-visible,
  [tabindex]:focus-visible{outline:2px solid var(--focus);outline-offset:2px;
    border-radius:8px}

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
  /* #ministats owns its own visibility (JT-23 cleanup): hidden by default,
     shown as inline-flex only when the toolbar is stuck — no reliance on
     source order against the generic .mini rule. */
  .mini-stats{display:none;margin-left:auto;align-items:center;gap:9px;font-family:var(--mono);font-size:11px;color:var(--muted)}
  .toolbar.stuck .mini-stats{display:inline-flex}
  .mini-stats .ms{display:inline-flex;align-items:center;gap:4px}
  .mini-stats .ms b{color:var(--ink);font-weight:700}
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
  input[type="search"]{flex:1;min-width:160px;max-width:320px}
  input[type="search"]::placeholder{color:var(--faint)}
  .clear-btn{background:transparent;border:0;color:var(--muted);font-size:12px;cursor:pointer;
    text-decoration:underline dotted;padding:6px 4px}
  .clear-btn:hover{color:var(--ink)}
  .theme-btn{margin-left:auto;background:var(--card);border:1px solid var(--line);border-radius:999px;
    width:34px;height:34px;cursor:pointer;font-size:15px;line-height:1;box-shadow:var(--shadow)}
  .theme-btn:hover{border-color:var(--line-2)}

  main{padding:16px 28px 80px}

  /* board view — column count is data-driven (JT-24): --ncols is the number of
     statuses on the board.  auto-fill capped at --ncols never overflows (kills the
     old 1100-1200px horizontal-scroll gap) and wraps responsively as width shrinks. */
  .cols{display:grid;gap:14px;align-items:start;--ncols:__NCOLS__;--col-min:220px;
    grid-template-columns:repeat(auto-fill,minmax(
      min(100%, max(var(--col-min), calc((100% - (var(--ncols) - 1)*14px)/var(--ncols)))),1fr))}
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
  .ckey{font-family:var(--mono);font-size:11px;color:var(--muted)}
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
  .prog-n{font-family:var(--mono);font-size:10px;color:var(--muted);white-space:nowrap}
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
  /* links auto-detected in description / comment bodies (JT-33) */
  .d-desc a,.cmt .b a{color:var(--review);text-decoration:underline}

  /* blocked-by (JT-38) — drawer chips + a compact card/row indicator */
  .blk-row{display:flex;gap:6px;flex-wrap:wrap}
  .blk-chip{font-family:var(--mono);font-size:10.5px;font-weight:700;padding:2px 9px;
    border-radius:999px;white-space:nowrap;cursor:pointer}
  .blk-ind{display:inline-flex;align-items:center;font-size:11px;line-height:1;color:var(--bug);
    cursor:help}

  footer{padding:20px 28px;color:var(--faint);font-family:var(--mono);font-size:11px;
    border-top:1px solid var(--line)}

  /* Reduced motion (JT-33): kill transitions/animations and hover lifts. */
  @media (prefers-reduced-motion: reduce){
    *{transition:none!important;animation:none!important;scroll-behavior:auto!important}
    .card:hover,.row:hover{transform:none!important}
  }

  /* Print (JT-33): force the light palette, drop chrome/overlays, let the board
     reflow and keep cards intact across page breaks; no background gradients. */
  @media print{
    :root,:root[data-theme="dark"],:root[data-theme="light"]{
      --bg:#fff; --panel:#fff; --card:#fff; --line:#ccc; --line-2:#bbb;
      --ink:#111; --muted:#333; --faint:#555; color-scheme:light}
    body{background:#fff!important;background-image:none!important}
    .toolbar{position:static!important;display:none!important}
    #sentinel{display:none!important} /* toolbar children are hidden with it above */
    .scrim,.drawer{display:none!important}
    .cols{grid-template-columns:repeat(2,1fr)!important}
    .card,.row,.epic-group{break-inside:avoid;page-break-inside:avoid;box-shadow:none!important}
    a{text-decoration:underline}
  }
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
  <span class="mini-stats" id="ministats"></span>
  <button class="theme-btn" id="ftheme" title="toggle light/dark theme">🌙</button>
</nav>

<main id="main" tabindex="-1"></main>
<footer id="foot"></footer>

<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer" role="dialog" aria-modal="true" aria-labelledby="d-title" aria-hidden="true"></aside>

<script id="board-data" type="application/json">__BOARD_DATA__</script>
<script>
const BOARD=JSON.parse(document.getElementById('board-data').textContent);
const STATUSES=BOARD.statuses, PRIORITIES=BOARD.priorities;
const TYPE_COLOR={Epic:'var(--epic)',Story:'var(--story)',Task:'var(--task)',Bug:'var(--bug)','Sub-task':'var(--sub)'};
// STATUS_COLOR is generated by Python (JT-24): canonical statuses map to theme
// palette vars; any non-canonical status gets a distinct deterministic hue.
const STATUS_COLOR=__STATUS_COLOR_JSON__;
const PRI_COLOR={Highest:'var(--pri-highest)',High:'var(--pri-high)',Medium:'var(--pri-medium)',Low:'var(--pri-low)',Lowest:'var(--pri-lowest)'};
const CLOSED=new Set(['Done','Cancelled']);
const tcol=t=>TYPE_COLOR[t]||'var(--muted)';
const scol=s=>STATUS_COLOR[s]||'var(--muted)';
const pcol=p=>PRI_COLOR[p]||'var(--muted)';
const tint=(c,p)=>`color-mix(in srgb, ${c} ${p}%, transparent)`;
const esc=s=>(s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const trunc=(s,n)=>{s=s==null?'':String(s);return s.length>n?s.slice(0,n-1)+'…':s};
// Relative time (JT-39): "just now" / "Nm/h/d/w ago", date for >8 weeks.
// Returns a raw (un-escaped) string; relSpan is the single escape point.
function rel(ts){
  if(!ts)return '';
  const t=Date.parse(ts); if(isNaN(t))return String(ts);
  let s=Math.floor((Date.now()-t)/1000); if(s<0)s=0;
  const m=Math.floor(s/60),h=Math.floor(s/3600),d=Math.floor(s/86400),w=Math.floor(s/604800);
  if(s<45)return 'just now';
  if(m<60)return m+'m ago';
  if(h<24)return h+'h ago';
  if(d<7)return d+'d ago';
  if(w<=8)return w+'w ago';
  try{return new Date(t).toISOString().slice(0,10);}catch(e){return String(ts);}
}
// A relative-time element that exposes the full ISO stamp on hover.
// esc() is called here and ONLY here — rel() returns the raw string.
const relSpan=ts=>`<span title="${esc(ts)}">${esc(rel(ts))}</span>`;
// Linkify http(s) URLs in ALREADY-ESCAPED text (JT-33). Run AFTER esc() so the
// surrounding body stays escaped and only real URLs become anchors. In escaped
// text every literal "&" is an entity: keep &amp; (a real "&") inside the URL
// but stop at &quot;/&gt;/&#39;/&lt;, and peel trailing punctuation plus any
// unbalanced ")" out of the link (JT-42).
function linkify(escaped){
  return escaped.replace(/https?:\/\/(?:[^\s<&]|&amp;)+/g,u=>{
    let t='';
    for(;;){
      if(/[.,;:!?]$/.test(u)){t=u.slice(-1)+t;u=u.slice(0,-1);continue;}
      if(u.endsWith(')')&&(u.match(/\(/g)||[]).length<(u.match(/\)/g)||[]).length){t=')'+t;u=u.slice(0,-1);continue;}
      break;
    }
    return `<a href="${u}" target="_blank" rel="noopener">${u}</a>`+t;
  });
}
// Open blockers of an issue (blocker exists and is not Done/Cancelled).
const openBlockers=i=>(i.blocked_by||[]).filter(k=>byKey.has(k)&&!CLOSED.has(byKey.get(k).status));
let view='board', fEpic='', fType='', fPri='', q='', openKey='';

// Transitive epic membership (JT-22). The embedded board never changes in the
// browser, so the index is built ONCE at load (JT-46).
// byKey: key -> issue;  EPIC_OF: key -> nearest Epic ancestor key (or undefined);
// EPICS: the Epic issues in board order.
const byKey=new Map(BOARD.issues.map(i=>[i.key,i]));
let EPIC_OF=new Map(), EPICS=[];
function buildEpicIndex(){
  EPIC_OF=new Map();
  EPICS=BOARD.issues.filter(i=>i.type==='Epic');
  for(const i of BOARD.issues){
    let cur=i, seen=new Set(), found;
    while(cur&&!seen.has(cur.key)){
      seen.add(cur.key);
      if(cur.type==='Epic'&&cur.key!==i.key){found=cur.key;break;}
      cur=cur.parent?byKey.get(cur.parent):null;
    }
    if(found)EPIC_OF.set(i.key,found);
  }
}
buildEpicIndex();
const epicOf=i=>EPIC_OF.get(i.key);
const isOrphan=i=>i.type!=='Epic'&&!EPIC_OF.has(i.key);
function matchSearch(i){
  if(!q)return true;
  const s=q.toLowerCase();
  return i.key.toLowerCase().includes(s)||(i.title||'').toLowerCase().includes(s);
}
// Non-epic facets only (type, priority, search) — the shared filter for member counts.
function passesFacets(i,skip){
  return (skip==='type'||!fType||i.type===fType)
    &&(skip==='pri'||!fPri||i.priority===fPri)
    &&matchSearch(i);
}
function matchEpic(i){
  if(!fEpic)return true;
  if(fEpic==='__none__')return isOrphan(i);
  return i.key===fEpic||epicOf(i)===fEpic;
}
function passes(i,skip){
  return (skip==='epic'||matchEpic(i))&&passesFacets(i,skip);
}
const visible=()=>BOARD.issues.filter(i=>passes(i));
// One bucketing pass per render (JT-46): every epic's transitive members are
// collected once instead of re-scanning BOARD.issues per epic per consumer.
// MEMBERS: epic key -> members passing the non-epic facets (JT-21 semantics —
// epicOf never points at the issue itself); MEMBERS_ALL: same but unfiltered
// (progress rollup); ORPHANS: facet-passing non-epics with no epic ancestor.
let MEMBERS=new Map(), MEMBERS_ALL=new Map(), ORPHANS=[];
function buildMembership(){
  MEMBERS=new Map();MEMBERS_ALL=new Map();ORPHANS=[];
  for(const i of BOARD.issues){
    const ek=epicOf(i);
    if(ek){
      if(!MEMBERS_ALL.has(ek))MEMBERS_ALL.set(ek,[]);
      MEMBERS_ALL.get(ek).push(i);
      if(passesFacets(i)){
        if(!MEMBERS.has(ek))MEMBERS.set(ek,[]);
        MEMBERS.get(ek).push(i);
      }
    }else if(isOrphan(i)&&passesFacets(i))ORPHANS.push(i);
  }
}
// Members of an epic that pass the non-epic facets, EXCLUDING the epic itself (JT-21).
// '__none__' counts orphans passing the same facets.
const memberCount=ek=>ek==='__none__'?ORPHANS.length:(MEMBERS.get(ek)||[]).length;

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
  // Condensed per-status strip shown only when the toolbar is stuck (JT-23).
  document.getElementById('ministats').innerHTML=
    STATUSES.filter(s=>counts[s]).map(s=>`<span class="ms" title="${esc(s)}"><span class="dot" style="background:${scol(s)}"></span><b>${counts[s]}</b></span>`).join('');
  // Footer "generated" line uses a relative time with the full ISO on hover (JT-39).
  document.getElementById('foot').innerHTML=
    `source: .jira/board.json · regenerate with: python jira.py render · generated ${relSpan(p.updated)}`;
}
function setOpts(id,opts,cur){
  document.getElementById(id).innerHTML=opts.map(o=>
    `<option value="${esc(o.v)}"${o.v===cur?' selected':''}>${esc(o.label)}</option>`).join('');
}
function fillFilters(){
  const base=f=>BOARD.issues.filter(i=>passes(i,f));
  // 'Epic · all' counts every issue passing the non-epic facets (the union of all groups).
  const ebAll=BOARD.issues.filter(i=>passesFacets(i)).length;
  const eOpts=[{v:'',label:`Epic · all (${ebAll})`}]
    .concat(EPICS.map(e=>({v:e.key,label:`${e.key} · ${trunc(e.title,32)} (${memberCount(e.key)})`})))
    .concat([{v:'__none__',label:`— no epic (${memberCount('__none__')})`}]);
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
const noMatch=()=>`<div class="empty">No issues match the current filters. <a href="#" data-action="clear">Clear filters</a></div>`;
const noIssues=()=>`<div class="empty">No issues yet. Create one with: python jira.py add --type Task --title "..."</div>`;

// Shared chip markup (JT-46): one place for the tinted-label styling used by
// type badges and status/priority pills across cards, rows, epic heads, drawer.
const badge=(cls,label,c)=>`<span class="${cls}" style="background:${tint(c,24)};color:${c}">${esc(label)}</span>`;
const typeBadge=t=>badge('ttype',t,tcol(t));
const statusPill=s=>badge('pill',s,scol(s));
// Blocked indicator (JT-38): shown only when an issue has >=1 OPEN blocker.
function blockedInd(i){
  const open=openBlockers(i);
  if(!open.length)return '';
  return `<span class="blk-ind" title="Blocked by ${esc(open.join(', '))}" aria-label="blocked">⛔</span>`;
}
function cardHTML(i){
  const labels=(i.labels||[]).map(l=>`<span class="chip">${esc(l)}</span>`).join('');
  const cc=(i.comments&&i.comments.length)?`<span class="cc">💬 ${i.comments.length}</span>`:'';
  const upd=i.updated?`<span class="cc">${relSpan(i.updated)}</span>`:'';
  return `<div class="card" style="--tc:${tcol(i.type)}" data-key="${esc(i.key)}" tabindex="0" role="button" aria-label="${esc(i.key)}: ${esc(i.title)}">
    <div class="top">
      ${typeBadge(i.type)}
      <span class="ckey">${blockedInd(i)}${esc(i.key)}</span>
    </div>
    <div class="title">${esc(i.title)}</div>
    <div class="meta">
      <span class="pri" style="color:${pcol(i.priority)}"><span class="pbar" style="background:${pcol(i.priority)}"></span>${esc(i.priority)}</span>
      ${labels}${cc}${upd}
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
  return `<div class="row" data-key="${esc(i.key)}" tabindex="0" role="button" aria-label="${esc(i.key)}: ${esc(i.title)}">
    ${typeBadge(i.type)}
    <span class="ckey">${blockedInd(i)}${esc(i.key)}</span>
    <span class="title">${esc(i.title)}</span>
    <span class="pri" style="color:${pcol(i.priority)}"><span class="pbar" style="background:${pcol(i.priority)}"></span>${esc(i.priority)}</span>
    ${statusPill(i.status)}</div>`;
}
function renderEpics(){
  if(!BOARD.issues.length){document.getElementById('main').innerHTML=noIssues();return;}
  const childFilter=!!(fType||fPri||q);
  // A specific epic with zero matching members under active filters → global empty state.
  if(fEpic&&fEpic!=='__none__'&&childFilter&&memberCount(fEpic)===0){
    document.getElementById('main').innerHTML=noMatch();return;
  }
  let html='';
  EPICS.forEach(e=>{
    if(fEpic&&fEpic!=='__none__'&&fEpic!==e.key)return;
    if(fEpic==='__none__')return; // No-Epic filter hides epic groups
    const membersAll=MEMBERS_ALL.get(e.key)||[]; // unfiltered, transitive
    const kids=MEMBERS.get(e.key)||[];
    if(childFilter&&!kids.length)return; // with filters, only groups that still have members
    const done=membersAll.filter(k=>k.status==='Done').length;
    const pct=membersAll.length?Math.round(done/membersAll.length*100):0;
    const body=kids.length?kids.map(rowHTML).join('')
      :(childFilter?'<div class="note">no matching issues</div>':'<div class="note">no issues yet</div>');
    html+=`<section class="epic-group"><div class="epic-head" data-key="${esc(e.key)}">
      ${typeBadge('Epic')}
      <span class="ckey">${esc(e.key)}</span><span class="title">${esc(e.title)}</span>
      <span class="prog" title="overall progress, ignores filters"><span class="prog-bar"><i style="width:${pct}%"></i></span><span class="prog-n">${done}/${membersAll.length} done</span></span>
      ${statusPill(e.status)}</div>
      <div class="epic-body">${body}</div></section>`;
  });
  if(!fEpic||fEpic==='__none__'){
    if(ORPHANS.length){
      html+=`<section class="epic-group"><div class="epic-head" style="cursor:default"><span class="title" style="color:var(--muted)">No Epic</span></div><div class="epic-body">${ORPHANS.map(rowHTML).join('')}</div></section>`;
    }
  }
  document.getElementById('main').innerHTML=html||noMatch();
}
function render(){buildMembership();header();fillFilters();view==='board'?renderBoard():renderEpics();syncHash();}

// Element that had focus before the drawer opened, restored on close (JT-28).
let drawerOpener=null;
const FOCUSABLE='a[href],button:not([disabled]),input,select,textarea,[tabindex]:not([tabindex="-1"])';
const cssEsc=s=>(window.CSS&&window.CSS.escape)?window.CSS.escape(s):String(s).replace(/["\\]/g,'\\$&');
function openIssue(key){
  const i=BOARD.issues.find(x=>x.key===key);if(!i)return;
  // Remember the opener so focus can be restored on close.
  drawerOpener=(document.activeElement&&document.activeElement!==document.body)?document.activeElement:null;
  openKey=key;
  const d=document.getElementById('drawer');
  const par=i.parent?BOARD.issues.find(x=>x.key===i.parent):null;
  const parentCell=par
    ?`<a class="plink" href="#${esc(par.key)}" data-key="${esc(par.key)}">${esc(par.key)} · ${esc(trunc(par.title,30))}</a>`
    :esc(i.parent||'—');
  // Blocked-by chips (JT-38): each blocker is a clickable chip styled by its status.
  const blk=(i.blocked_by||[]).map(k=>{
    const b=byKey.get(k);const st=b?b.status:'?';const c=scol(st);
    return `<span class="blk-chip" data-key="${esc(k)}" style="background:${tint(c,24)};color:${c}" title="${esc(k)} (${esc(st)})">${esc(k)} (${esc(st)})</span>`;
  }).join('');
  const rows=[
    ['Parent',parentCell],
    ['Assignee',esc(i.assignee||'—')],
    ['Labels',(i.labels||[]).map(l=>`<span class="chip">${esc(l)}</span>`).join(' ')||'—'],
    ['Components',(i.components||[]).map(c=>`<span class="chip">${esc(c)}</span>`).join(' ')||'—'],
    ['Created',i.created?relSpan(i.created):'—'],
    ['Updated',i.updated?relSpan(i.updated):'—']
  ];
  d.innerHTML=`<div class="d-head">
      ${typeBadge(i.type)}
      <span class="ckey">${esc(i.key)}</span>
      <button class="x" data-action="close" title="close" aria-label="close">✕</button>
    </div>
    <h2 class="d-title" id="d-title">${esc(i.title)}</h2>
    <div class="d-pills">
      ${statusPill(i.status)}
      ${badge('pill',i.priority,pcol(i.priority))}
    </div>
    ${blk?`<div class="d-sec">Blocked by</div><div class="blk-row">${blk}</div>`:''}
    ${i.description?`<div class="d-sec">Description</div><div class="d-desc">${linkify(esc(i.description))}</div>`:''}
    <div class="d-sec">Details</div>
    <dl class="d-grid">${rows.map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
    ${(i.comments&&i.comments.length)?`<div class="d-sec">Comments (${i.comments.length})</div>`+
      i.comments.map(c=>`<div class="cmt"><div class="h">${esc(c.author)} · ${relSpan(c.at)}</div><div class="b">${linkify(esc(c.body))}</div></div>`).join(''):''}`;
  document.getElementById('scrim').classList.add('on');
  d.classList.add('on');d.setAttribute('aria-hidden','false');d.scrollTop=0;
  // Move focus into the dialog (the close button) so keyboard users land inside.
  const x=d.querySelector('.x');if(x)x.focus();
  syncHash();
}
// Confine Tab/Shift+Tab to the drawer's focusables while it is open (JT-28).
function trapFocus(e){
  if(e.key!=='Tab')return;
  const d=document.getElementById('drawer');
  if(!d.classList.contains('on'))return;
  const f=Array.from(d.querySelectorAll(FOCUSABLE)).filter(el=>el.offsetParent!==null||el===document.activeElement);
  if(!f.length){e.preventDefault();return;}
  const first=f[0],last=f[f.length-1];
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
}
function closeDrawer(){
  const d=document.getElementById('drawer');
  const wasOpen=d.classList.contains('on');
  const k=openKey; // captured for the focus fallback below, before the state clears (JT-46)
  document.getElementById('scrim').classList.remove('on');
  d.classList.remove('on');d.setAttribute('aria-hidden','true');
  openKey='';
  // Restore focus to the opener; if it's gone after a re-render, fall back to the
  // matching card/row, then to the body (JT-28).
  if(wasOpen){
    let tgt=(drawerOpener&&document.body.contains(drawerOpener))?drawerOpener:null;
    if(!tgt&&k)tgt=document.querySelector(`[data-key="${cssEsc(k)}"]`);
    if(tgt&&tgt.focus)tgt.focus();else document.getElementById('main').focus();
  }
  drawerOpener=null;
  syncHash();
}

// ---- URL-hash state (JT-30) ----
let lastHash=null; // guards the hashchange listener against our own writes
function syncHash(){
  const p=new URLSearchParams();
  if(view==='epics')p.set('v','epics');
  if(fEpic)p.set('e',fEpic);
  if(fType)p.set('t',fType);
  if(fPri)p.set('p',fPri);
  if(q)p.set('q',q);
  if(openKey)p.set('i',openKey);
  const s=p.toString();
  // Skip the history write when nothing changed — render() runs per keystroke
  // and browsers rate-limit replaceState (JT-44).
  if(s===lastHash&&location.hash.replace(/^#/,'')===s)return;
  lastHash=s;
  history.replaceState(null,'',s?'#'+s:location.pathname+location.search);
}
function applyHash(){
  let h=location.hash.replace(/^#/,'');
  view='board';fEpic=fType=fPri=q='';openKey='';
  if(h){
    if(h.indexOf('=')===-1){
      // bare "#JT-7" shorthand → open that issue
      openKey=decodeURIComponent(h);
    }else{
      const p=new URLSearchParams(h);
      if(p.get('v')==='epics')view='epics';
      fEpic=p.get('e')||'';fType=p.get('t')||'';fPri=p.get('p')||'';
      q=(p.get('q')||'').trim();openKey=p.get('i')||''; // trim to match the live input handler (JT-43)
    }
  }
  document.getElementById('fsearch').value=q;
  setView(view);
  if(openKey&&!BOARD.issues.some(i=>i.key===openKey))openKey='';
}
function setView(v){
  view=v;
  document.querySelectorAll('#viewseg button').forEach(x=>x.classList.toggle('on',x.dataset.view===v));
}

// ---- event delegation (JT-20) ----
// One click + one keydown handler resolve the nearest [data-key]/[data-action].
function handleActivate(t){
  const act=t.closest('[data-action]');
  if(act){const a=act.dataset.action;if(a==='close')closeDrawer();else if(a==='clear')clearFilters();return true;}
  const el=t.closest('[data-key]');
  if(el){openIssue(el.dataset.key);return true;}
  return false;
}
document.body.addEventListener('click',e=>{
  if(handleActivate(e.target))e.preventDefault();
});
document.body.addEventListener('keydown',e=>{
  if(e.key!=='Enter'&&e.key!==' ')return;
  const el=e.target.closest('[data-key],[data-action]');
  if(!el)return;
  e.preventDefault(); // also stops Space from scrolling
  handleActivate(e.target);
});

document.getElementById('scrim').addEventListener('click',closeDrawer);
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer();else trapFocus(e);});
document.getElementById('viewseg').addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  setView(b.dataset.view);
  render();
});
document.getElementById('fepic').addEventListener('change',e=>{fEpic=e.target.value;render();});
document.getElementById('ftype').addEventListener('change',e=>{fType=e.target.value;render();});
document.getElementById('fpri').addEventListener('change',e=>{fPri=e.target.value;render();});
document.getElementById('fsearch').addEventListener('input',e=>{q=e.target.value.trim();render();});
document.getElementById('fclear').addEventListener('click',clearFilters);
const themeBtn=document.getElementById('ftheme');
function themeIcon(){themeBtn.textContent=document.documentElement.dataset.theme==='light'?'☀️':'🌙';}
function applyTheme(t){document.documentElement.dataset.theme=(t==='light'?'light':'dark');themeIcon();}
themeBtn.addEventListener('click',()=>{
  const next=document.documentElement.dataset.theme==='light'?'dark':'light';
  applyTheme(next);
  try{localStorage.setItem('jt-theme',next);}catch(err){}
});
themeIcon();
// Theme sync (JT-33): a theme picked in another tab updates this one immediately.
window.addEventListener('storage',e=>{
  if(e.key==='jt-theme'&&(e.newValue==='light'||e.newValue==='dark'))applyTheme(e.newValue);
});
// Follow OS light/dark changes ONLY when the user hasn't pinned a theme here.
if(window.matchMedia){
  const mq=matchMedia('(prefers-color-scheme: dark)');
  const onOS=e=>{let pinned=null;try{pinned=localStorage.getItem('jt-theme');}catch(err){}
    if(pinned!=='light'&&pinned!=='dark')applyTheme(e.matches?'dark':'light');};
  if(mq.addEventListener)mq.addEventListener('change',onOS);else if(mq.addListener)mq.addListener(onOS);
}
new IntersectionObserver(es=>{
  document.getElementById('toolbar').classList.toggle('stuck',!es[0].isIntersecting);
},{rootMargin:'-1px 0px 0px 0px'}).observe(document.getElementById('sentinel'));
window.addEventListener('hashchange',()=>{
  const h=location.hash.replace(/^#/,'');
  if(h===lastHash)return; // ignore our own writes
  // Seed lastHash so the render() below doesn't rewrite the hash it is
  // consuming; syncHash re-canonicalizes only if the applied state differs (JT-44).
  lastHash=h;
  applyHash();
  render();
  if(openKey)openIssue(openKey);else closeDrawer();
});
applyHash();
render();
if(openKey)openIssue(openKey);
</script>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #

def build_parser():
    # common parent carries --file and --json so every subparser accepts them (JT-32e, JT-36)
    # SUPPRESS as default means: if the flag is not provided by a particular parser level,
    # that level does NOT write the key into the namespace at all — so a value set by an
    # earlier parser level (e.g. the top-level parent) survives instead of being overwritten
    # by the subparser's own default of None/False.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", default=argparse.SUPPRESS,
                        help=f"path to board json (default: {DEFAULT_FILE})")
    common.add_argument("--json", action="store_true", dest="json",
                        default=argparse.SUPPRESS,
                        help="emit compact JSON to stdout instead of human-readable output")

    p = argparse.ArgumentParser(prog="jira.py", description="Tiny Jira-style work tracker (JSON + HTML).",
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a new board", parents=[common])
    s.add_argument("--name"); s.add_argument("--key"); s.add_argument("--repo")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("add", help="create an issue", parents=[common])
    s.add_argument("--type", required=True, help="Epic|Story|Task|Bug|Sub-task")
    s.add_argument("--title", required=True)
    s.add_argument("--desc"); s.add_argument("--priority"); s.add_argument("--status")
    s.add_argument("--parent"); s.add_argument("--labels"); s.add_argument("--components")
    s.add_argument("--assignee")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("move", help="change an issue's status", parents=[common])
    s.add_argument("key"); s.add_argument("status")
    s.add_argument("--comment"); s.add_argument("--author")
    s.set_defaults(func=cmd_move)

    s = sub.add_parser("comment", help="add a comment to an issue", parents=[common])
    s.add_argument("key"); s.add_argument("body"); s.add_argument("--author")
    s.set_defaults(func=cmd_comment)

    s = sub.add_parser("set", help="edit fields on an issue", parents=[common])
    s.add_argument("key")
    s.add_argument("--title"); s.add_argument("--desc"); s.add_argument("--priority")
    s.add_argument("--type"); s.add_argument("--parent"); s.add_argument("--assignee")
    s.add_argument("--labels"); s.add_argument("--components")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("list", help="list issues (open by default)", parents=[common])
    s.add_argument("--status"); s.add_argument("--type"); s.add_argument("--parent")
    s.add_argument("--all", action="store_true", help="include Done/Cancelled")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("next", help="recommend what to work on", parents=[common])
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_next)

    s = sub.add_parser("show", help="show full issue detail", parents=[common])
    s.add_argument("key")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("status", help="board summary", parents=[common])
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("doctor", help="integrity scan of the board", parents=[common])
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("link", help="manage blocked-by relationships", parents=[common])
    s.add_argument("key", help="issue key to link")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--blocked-by", metavar="OTHER", help="mark KEY as blocked by OTHER")
    g.add_argument("--unblock", metavar="OTHER", help="remove OTHER from KEY's blocked_by")
    s.set_defaults(func=cmd_link)

    s = sub.add_parser("render", help="regenerate board.html from board.json", parents=[common])
    s.set_defaults(func=cmd_render)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    # --file and --json use default=SUPPRESS in the shared common parser so that
    # a subparser that doesn't see the flag does NOT overwrite a value that was
    # already set by the top-level parser.  All read sites use getattr(..., None)
    # / getattr(..., False) which are the safe fallbacks when the key is absent.
    args.func(args)


if __name__ == "__main__":
    main()
