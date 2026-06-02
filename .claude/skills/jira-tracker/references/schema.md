# `board.json` schema

The single source of truth. Lives at `.jira/board.json`. Change it only through
`jira.py` (which validates and re-renders `board.html`); this doc is for reading.

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
      "labels": ["backend"],     // free-form tags
      "components": ["api"],      // larger functional areas
      "assignee": "agent",       // free text ("agent", a name, or "")
      "created": "2026-06-02T10:00:00+07:00",
      "updated": "2026-06-02T10:25:00+07:00",
      "comments": [
        { "author": "agent", "at": "2026-06-02T10:25:00+07:00",
          "body": "Drafted the queue interface in infra/queue.py." }
      ]
    }
  ]
}
```

## Field notes

- **key** — assigned by `add`; never reuse. `counter` only ever increases, so
  keys are stable even after deletes (deletes aren't a CLI feature by design —
  cancel issues instead with `move KEY cancelled`).
- **parent** — models hierarchy: Stories/Tasks/Bugs point to an Epic; Sub-tasks
  point to a Story or Task. Used by the "By Epic" view in the HTML board.
- **status / type / priority** — must be values from the arrays at the top of the
  file. The CLI accepts case-insensitive and unique-prefix input (`prog` →
  `In Progress`) and normalizes to the canonical value.
- **comments** — append-only audit trail. This is where the "what I actually did"
  history lives; keep it specific.

## Recommendation logic (`next`)

Candidates are non-Epic issues in `To Do` or `In Progress`, sorted by:
1. status — In Progress before To Do (finish what's started),
2. priority — Highest → Lowest,
3. age — oldest `created` first.
