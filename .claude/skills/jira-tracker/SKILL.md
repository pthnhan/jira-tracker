---
name: jira-tracker
description: >-
  Maintain a Jira-style work tracker (epics / stories / tasks / bugs / sub-tasks
  with statuses To Do, In Progress, In Review, Done, Cancelled) for any code
  repository, stored as .jira/board.json and rendered to a browsable
  .jira/board.html board. ALWAYS use this skill when the user runs /init, asks
  you to read / onboard / analyze a repo, starts a work session with phrases
  like "keep working", "continue", "update X", "what should I do next", or
  whenever they raise a new problem, bug, feature, or task that should be
  tracked. Use it to check status and pick the highest-priority next task before
  working, to create the correctly-typed issue for any new request, and to
  update status and add a comment after finishing anything. Works on both brand
  new and existing repositories. It asks before creating a board, loads an
  existing board without altering it unless asked, seeds an existing repo's
  already-built work as Done, and proposes every board change for your review
  before applying it.
---

# Jira Tracker

Give any repo a lightweight, local Jira-style board so work is captured,
prioritized, and kept up to date across sessions. The board is one JSON file
(`.jira/board.json`, the source of truth) plus a generated, standalone HTML view
(`.jira/board.html`) the user can open in a browser.

You never hand-edit the JSON. All changes go through the bundled CLI, which
validates input and **re-renders the HTML automatically after every change** so
the board is never stale.

## The CLI

The tracker ships with this skill at `scripts/jira.py` (pure Python 3 standard
library — no installs). Resolve its path relative to this skill and reuse it.
For brevity below it is written as `jira`:

```bash
jira="python3 <this-skill-dir>/scripts/jira.py"
```

Commands (run `$jira <cmd> --help` for flags):

| Command | Purpose |
|---|---|
| `init --name N --key K --repo URL` | Create a new board |
| `add --type T --title "..." [--priority P --parent KEY --desc "..." --labels a,b --components x --status S]` | Create an issue, prints its key |
| `move KEY STATUS [--comment "..."]` | Change status (status is fuzzy: `prog`, `done`, `review` all work) |
| `comment KEY "text"` | Add a comment |
| `set KEY [--title/--desc/--priority/--type/--parent/--assignee/--labels/--components]` | Edit fields |
| `list [--status S --type T --parent KEY --all]` | List issues (open only unless `--all`) |
| `next [--limit N]` | Recommend what to work on next |
| `show KEY` | Full detail of one issue |
| `status` | One-screen board summary |
| `render` | Force-regenerate `board.html` |

Vocabulary (use these exact words): **types** Epic, Story, Task, Bug, Sub-task ·
**statuses** To Do, In Progress, In Review, Done, Cancelled · **priorities**
Highest, High, Medium, Low, Lowest.

---

## Workflow 1 — Starting work on a repo (any "let's start" cue, not just `/init`)

Trigger: `/init`, **or** any cue that you're beginning work from a repository —
"set up tracking", "let's start on this project", "help me work on this repo",
"read this repo", or simply being pointed at a codebase to work in. You do not
need an explicit `/init`.

### Step 0 — Is there already a board?

Check for `.jira/board.json` or `.jira/board.html`.

- **If either exists, the repo is already tracked.** Load it, summarize with
  `status` and `next`, and continue from it (this is Workflow 2). Do **not**
  modify or regenerate the board unless the user asks you to — start *with* the
  existing board as-is.
- If only `board.html` exists but `board.json` is missing, tell the user the
  source-of-truth JSON is absent and ask whether to rebuild it before changing
  anything.

### Step 1 — Ask before creating a board

If there is no board and the request was an *implicit* start-work cue (not an
explicit "create the board" / `/init`), **ask first**:

> "This repo isn't tracked yet. Do you want me to create a Jira-style board for
> it?"

Wait for a yes. If the user explicitly asked to set up tracking or ran `/init`,
skip the question and go straight to proposing the seed plan.

### Step 2 — Create and seed (after the user agrees)

1. Pick a name and key: name → repo folder or `package.json` / `pyproject.toml`;
   key → 2–4 uppercase letters (e.g. "Payments" → `PAY`).
2. **Propose the seed plan and let the user review it before writing** (see
   "Confirm board writes" below): list the issues you intend to create with
   their types and statuses, then apply only after they approve.
3. Run `init`, then create the issues.

**Seed to reflect reality — an existing repo starts mostly Done.** The board
should mirror the repo's *current* state, not an imaginary backlog:

- **Existing / non-blank repo:** work that is already implemented and shipped is
  **Done**. Create issues for the features and modules that already exist with
  `--status Done`, so the board reflects what's been built. *Then* add the
  **open** items for what remains or is broken — `TODO`/`FIXME`/`XXX` comments,
  failing tests, known bugs, unfinished roadmap entries — as `To Do` (or
  `In Progress` if clearly underway). Net effect: a freshly-seeded mature repo is
  largely Done with a short list of live work.
- **Blank / new repo:** nothing is built yet, so seed a small starter scaffold —
  e.g. an Epic "Project setup" with `To Do` tasks for the obvious first steps
  (tooling, CI, first feature).

Keep it proportionate — a handful of well-chosen issues beats fifty noisy ones.

### Reviewing an already-tracked repo on request

When the user asks you to review the project / refresh the board ("review the
repo and update the tickets"), do a scan pass: look for new problems, completed
work that's still marked open, or drift between the board and the code. Then
**propose** the new or updated tickets (new Bugs/Tasks, status corrections) and
apply them only after the user reviews them. Never silently rewrite the board.

---

## Workflow 2 — Start of session ("continue", "keep working", "update X", "what's next")

Trigger: any session-opening cue that you're resuming work — "keep your work
going", "continue where we left off", "update the auth stuff", "what should I do
now", or simply being asked to work in a repo that already has a board.

Do this **before** writing any code:

1. `status` — get counts and what's currently In Progress.
2. `next` — get the priority-ordered recommendation. The CLI surfaces In
   Progress items first (finish what's started), then To Do by priority.
3. Briefly tell the user the state and your proposed next item, e.g.
   "PAY-2 (async schema) is in progress and PAY-4 is a Highest-priority bug —
   I'll resume PAY-2 first." Let them redirect if they want.
4. If they named a specific area ("update X"), `list` / `show` the matching
   issues to ground yourself before acting.

Never start work in a tracked repo without first reading the board — the whole
point is continuity across sessions.

---

## Workflow 3 — A new problem appears → create the right issue

Trigger: the user raises anything new — a feature idea, a defect, a chunk of
work, a refactor, a question that implies work. Capture it immediately as a
**correctly-typed** issue (don't wait until the end).

Pick the type by what the thing *is*:

- **Epic** — a large body of work spanning multiple deliverables ("add billing",
  "migrate to async"). Epics are containers; child issues point to them via
  `--parent`.
- **Story** — a user-facing increment that delivers value on its own ("user can
  reset password").
- **Task** — technical work that isn't a user-facing feature ("add Redis client",
  "set up CI", "refactor config loader").
- **Bug** — something is broken or behaves wrong ("webhook fires twice").
- **Sub-task** — a small breakdown of a Story/Task, parented to it.

Set a priority deliberately (default Medium; bugs blocking work are High/Highest)
and parent it to the right Epic when one exists. Write a one- or two-sentence
`--desc` so the issue is understandable later. Example:

```bash
$jira add --type Bug --title "Webhook retries fire twice" \
  --priority Highest --parent PAY-1 --components webhooks \
  --desc "Duplicate delivery on 5xx; idempotency key not honored on retry."
```

If one request actually contains several pieces of work, create an Epic and
parent the pieces under it rather than cramming everything into one issue.

---

## Workflow 4 — While working: keep the board honest

This discipline is what makes the board trustworthy. Each step below is a board
write, so confirm it with the user first per "Confirm board writes" (or proceed
directly if they've opted into auto-updates for the session). Apply it every
time:

1. **Before** you start an issue: move it to In Progress with a short comment on
   your plan.
   ```bash
   $jira move PAY-2 "in progress" --comment "Drafting the async job schema in jobs/schema.py."
   ```
2. **After** you finish:
   - If it's truly complete → `move KEY done --comment "What changed + where."`
   - If it needs the user's review/merge → `move KEY review --comment "..."`.
   - If you abandon it → `move KEY cancelled --comment "why"`.
   Always include a comment that records *what you actually did* and any
   follow-ups (create new issues for follow-ups rather than burying them).
3. If scope changed, `set` the fields (retitle, re-parent, adjust priority) so
   the record matches reality.

A good comment is specific: "Added RedisQueue in infra/queue.py, wired into
worker, added unit tests" — not "done".

---

## Operating principles

- **Confirm board writes — ask the user to review before applying them.** Treat
  the board as something you *propose* changes to, not something you silently
  rewrite. Before running any mutating command (`init` seeding, `add`, `move`,
  `comment`, `set`), state the specific change(s) — keys, type, status
  transition, comment text — and wait for the user's okay. If the user gives a
  standing "just keep it updated without asking", honor that for the rest of the
  session; otherwise ask each time. This applies to both new and existing repos.
- **The JSON is the source of truth; the HTML is a view.** Only ever change the
  board through the CLI, which re-renders the HTML for you. If you ever edit
  `board.json` directly, run `render` afterward.
- **Capture first, work second.** New work becomes an issue before you act on it,
  so nothing is lost between sessions.
- **One issue, one status truth.** Move issues as their real state changes; don't
  let In Progress pile up.
- **Don't over-track.** Trivial, sub-five-minute steps don't each need an issue —
  fold them into a parent task's comments.
- The board is committed with the repo (it's just files under `.jira/`), so it
  travels with the project and works the same on a fresh clone.

See `references/schema.md` for the exact `board.json` structure if you ever need
to read it programmatically.
