# `board.json` schema

The single source of truth. Lives at `.jira/board.json` by default (override
with `--file PATH`). Change it only through `jira.py` (which validates and
re-renders `board.html`); this doc is for reading.

```jsonc
{
  "project": {
    "key": "PAY",                 // issue-key prefix, e.g. PAY-1
    "name": "Payments Service",
    "repo": "github.com/acme/payments",
    "created": "2026-06-02T10:00:00+07:00",
    "updated": "2026-06-02T10:30:00+07:00",
    "counter": 4                  // last number used; next issue is PAY-5
  },
  "template_version": 2,          // int; written on every save (see note below)
  "types":      ["Epic", "Story", "Task", "Bug", "Sub-task"],
  "statuses":   ["To Do", "In Progress", "In Review", "Done", "Cancelled"],
  "priorities": ["Highest", "High", "Medium", "Low", "Lowest"],
  "issues": [
    {
      "key": "PAY-1",            // unique, "<project.key>-<n>"
      "type": "Epic",            // one of project.types
      "title": "Migrate to async payment processing",
      "description": "Move the sync pipeline to a queue-based model.",
      "status": "In Progress",   // one of project.statuses
      "priority": "High",        // one of project.priorities
      "parent": null,            // key of the parent issue, or null
      "blocked_by": [],          // sorted unique array of blocker keys (see note)
      "labels": ["backend"],     // free-form tags
      "components": ["api"],     // larger functional areas
      "assignee": "agent",       // free text ("agent", a name, or "")
      "created": "2026-06-02T10:00:00+07:00",
      "updated": "2026-06-02T10:25:00+07:00",
      "comments": [
        { "author": "agent", "at": "2026-06-02T10:25:00+07:00",
          "body": "Drafted the queue interface in infra/queue.py." }
      ],
      "history": [                // append-only status-transition log (see note)
        { "at": "2026-06-02T10:00:00+07:00", "from": "",            "to": "To Do" },
        { "at": "2026-06-02T10:25:00+07:00", "from": "To Do",       "to": "In Progress" }
      ]
    }
  ]
}
```

## Field notes

- **key** — assigned by `add`; never reuse. `counter` only ever increases, so
  keys are stable even after deletes (deletes aren't a CLI feature by design —
  cancel issues instead with `move KEY cancelled`).
- **template_version** — integer stamped on every save by the CLI
  (`TEMPLATE_VERSION = 2` as of this writing). At load time the CLI compares
  this value to its own constant: if the board's value is **greater** the CLI
  refuses to operate (`error: this board was written by a newer jira.py`) to
  prevent silent data corruption. A non-integer value (string, null) also causes
  an immediate hard error. Missing value is treated as 0 (pre-versioning board —
  the CLI will stamp the correct version on the next save).
- **blocked_by** — sorted, unique array of issue keys that block this issue.
  Maintained by `link KEY --blocked-by OTHER` / `link KEY --unblock OTHER`. The
  CLI enforces: no self-block, no cycles (DFS cycle detection), no dangling
  references (both keys must exist). Idempotent re-link (adding an already-listed
  blocker is a no-op). `next` excludes issues with at least one open (non-Done,
  non-Cancelled) blocker from the recommendation list and moves them to a trailing
  "blocked:" section.
- **parent** — models hierarchy: Stories/Tasks/Bugs point to an Epic; Sub-tasks
  point to a Story or Task. Used by the "By Epic" view in the HTML board.
  `list --parent KEY` accepts an exact key and returns a hard error if the key
  does not exist (fuzzy matching is not applied to `--parent`).
- **status / type / priority** — must be values from the arrays at the top of the
  file. The CLI accepts case-insensitive unique fragments (`prog` →
  `In Progress`, `review` → `In Review`) and normalizes to the canonical value.
- **comments** — append-only audit trail. This is where the "what I actually did"
  history lives; keep it specific. No edit or delete operation exists.
- **history** — append-only log of status transitions, oldest first. Each entry
  is `{ "at": <iso8601>, "from": <previous status or ""> , "to": <new status> }`.
  `add` seeds one entry stamped at the issue's `created` time with `from: ""`
  and `to: <initial status>`. `move` appends one entry per status change; a
  no-op move (target equals current status) appends nothing. Bulk
  `move KEY1 KEY2 … STATUS` records an entry per affected issue. Older boards
  may lack this key entirely — it is treated as empty everywhere and is **never**
  retro-filled on load. `show` renders it under a "History:" section
  (`From → To  (timestamp)`).

## Runtime files

- **`board.html`** — generated view alongside `board.json`. Every mutating
  command re-renders it automatically. Contains a version comment
  `<!-- jira-tracker template v<N> -->` so the template generation can be
  identified. Do not edit it by hand.
- **`board.lock`** — transient lock file created in the same directory as
  `board.json`. Used by the CLI to serialize concurrent writes via
  `fcntl.flock` (POSIX; no-op on non-POSIX). Not written to `board.json`.
  Must never be committed — `init` writes a `.gitignore` next to the board
  covering it.
- **`.gitignore`** — written by `init` (if absent) in the board directory,
  containing `board.lock`. Committed with the board when `.jira/` is tracked.
- **`.no-board`** — empty marker created by the skill (not the CLI) when the
  user picks "No, don't ask again" at the board-creation offer; while it
  exists the offer is never made. Kept out of git via `.git/info/exclude`.
- **`.no-reconcile`** — empty marker created by the skill (not the CLI) when
  the user permanently opts out of end-of-turn board reconciliation. Same git
  handling as `.no-board`.

## `--json` output shapes

All read commands accept `--json` for stable, machine-readable output (no
human-readable headers or decorations). Recommended for agent programmatic use.

| Command | Top-level keys |
|---|---|
| `list --json` | `{"issues": [...]}` — array of full issue objects matching the current filter |
| `show KEY --json` | full issue object (same fields as in `board.json`) |
| `status --json` | `{"project": {...}, "counts": {status: n, ...}, "total": n, "in_progress": [key, ...], "in_review": [key, ...], "stale": [key, ...]}` |
| `next --json` | `{"recommendations": [{issue object}, ...], "blocked": [{"key": ..., "blocked_by_open": [key, ...]}, ...], "in_review": [key, ...]}` |
| `search QUERY --json` | `{"issues": [...]}` — array of full issue objects matching the query (empty array if none) |
| `report --json` | `{"total": n, "by_status": {status: n, ...}, "by_type": {type: n, ...}, "by_priority": {priority: n, ...}, "stale": n, "stale_keys": [key, ...], "cycle_time": {"count": n, "avg_days": x\|null, "median_days": x\|null}}` |
| `doctor --json` | `{"problems": [{"code": "...", "key": "...", "message": "..."}, ...]}` — empty array if healthy |

## `doctor` diagnostic codes

`doctor` exits 0 if healthy, 1 if any problem is found. Codes:

| Code | Meaning |
|---|---|
| `missing_key` | An issue object has no `key` field |
| `duplicate_key` | Two issues share the same key |
| `missing_field` | A required field (`title`, `type`, or `status`) is absent |
| `invalid_status` | `status` is not in `board.statuses` |
| `invalid_type` | `type` is not in `board.types` |
| `invalid_priority` | `priority` is not in `board.priorities` |
| `dangling_parent` | `parent` references a key that does not exist |
| `parent_cycle` | An issue is a member of a cycle in the parent chain |
| `dangling_blocked_by` | A `blocked_by` entry references a key that does not exist |
| `blocked_by_cycle` | An issue is a member of a cycle in the blocked_by graph |
| `counter_drift` | The highest issue suffix exceeds `project.counter` |
| `bad_timestamp` | A `created`, `updated`, or comment `at` field is not valid ISO-8601 |

## Recommendation logic (`next`)

Candidates are non-Epic issues in `To Do` or `In Progress` with no open
(non-closed) `blocked_by` entries, sorted by:

1. status — In Progress before To Do (finish what's started),
2. priority — Highest → Lowest,
3. age — oldest `created` first.

An issue whose blockers are all Done/Cancelled is treated as unblocked and stays in the recommendations.
Issues with at least one open blocker appear after the main list in a separate
"blocked:" section (`blocked` array in JSON mode).

`--limit N` caps only the `recommendations` list; the `blocked` section is
always complete.

## Search (`search`)

`search QUERY` is a read-only, case-insensitive substring scan across **all**
issues regardless of status (Done/Cancelled included). It matches the issue
`key`, `title`, `description`, any `labels` value, any `components` value, and
any comment `body`. Results render with the same compact one-line format as
`list`, annotated with `(matched: ...)` listing which fields hit. With no match
it prints a `no matches` line and exits 0 (`--json` emits `{"issues": []}`).

## Report (`report`)

`report` is a read-only metrics summary. It counts total issues, groups counts
by status / type / priority, and reports the stale count (reusing the same
`In Progress` + `>= 7d since updated` rule as `status` and `next`). When issues
carry `history`, it computes **cycle time** = days from the issue's `created`
timestamp to the **first** transition whose `to` is a terminal status
(`Done` or `Cancelled`, i.e. the closed-status set), reporting `avg_days` and
`median_days` over completed issues. Issues with no history, or no transition
into a terminal status, are excluded from cycle time but still counted
everywhere else; on a board with no history at all, `cycle_time.count` is 0 and
`avg_days`/`median_days` are `null`.

## Board HTML: status colors and dynamic columns

The rendered `board.html` injects a `STATUS_COLOR` map and a `--ncols` CSS
variable based on the board's `statuses` array at render time:

- **Canonical statuses** (`To Do`, `In Progress`, `In Review`, `Done`,
  `Cancelled`) map to named CSS theme palette variables (`--todo`, `--prog`,
  `--review`, `--done`, `--cancel`) that switch automatically between light and
  dark themes.
- **Non-canonical statuses** (added by editing the `statuses` array directly)
  receive a fallback CSS variable `var(--fb-N)` where `N` is a 1-based slot (`--fb-1` through `--fb-8`)
  cycling through 8 distinct hue slots. Each non-canonical status gets its own
  consistent color across renders (deterministic assignment based on position in
  the statuses array); they never collapse to a single grey fallback.
- The column grid is data-driven: `--ncols` equals the number of statuses in the
  board, so adding or removing a status automatically adjusts the layout without
  any hardcoded column count.

URL-hash deep-links are supported: `#KEY` (e.g. `#PAY-3`) opens the issue
drawer directly; hash-encoded params (`#v=epics&e=<epic>&t=<type>&p=<priority>&q=<search>&i=<issue>`)
set the filter/view state on load. The hash state is kept in sync with
`replaceState` and `URLSearchParams` as the user interacts.
