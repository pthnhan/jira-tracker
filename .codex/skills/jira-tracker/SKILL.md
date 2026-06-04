---
name: jira-tracker
description: >-
  Use when starting, onboarding, analyzing, resuming, or tracking work in any
  code repository; when the user runs /init, asks to read a repo, says continue
  or what next, or raises a bug, feature, task, TODO, or follow-up that should be
  recorded in a local Jira-style board.
---

# Jira Tracker

Give any repo a lightweight, local Jira-style board so work is captured,
prioritized, and kept up to date across sessions. The board is one JSON file
(`.jira/board.json`, the source of truth) plus a generated standalone HTML view
(`.jira/board.html`) the user can open in a browser.

You never hand-edit the JSON. All changes go through the bundled CLI, which
validates input and **re-renders the HTML automatically after every change** so
the board is never stale.

This skill is agent-neutral. In Claude Code it can live at
`.claude/skills/jira-tracker/`; in Codex it can live at
`.codex/skills/jira-tracker/`. In both cases, resolve bundled files relative to
the loaded skill directory.

## The CLI

The tracker ships with this skill at `scripts/jira.py` (pure Python 3 standard
library — no installs). Resolve its path relative to this skill, keep it in a
variable, and invoke it through `python3` (this form works in bash and zsh
alike — don't embed `python3` in the variable, since zsh won't word-split it):

```bash
jira=<this-skill-dir>/scripts/jira.py     # then: python3 "$jira" <command> ...
```

Commands (run `python3 "$jira" <cmd> --help` for flags):

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

## Workflow 1 - Starting work on a repo

Trigger: `/init`, **or** any cue that you're beginning work from a repository —
"set up tracking", "let's start on this project", "help me work on this repo",
"read this repo", or simply being pointed at a codebase to work in. You do not
need an explicit `/init`.

Repo-analysis prompts also count here for tracker purposes: "summarize this
repo", "what's done?", "what are the potential issues?", "review this project",
or similar requests to inspect the codebase.

### Step 0 - Is there already a board?

Check for `.jira/board.json` or `.jira/board.html`.

- **If either exists, the repo is already tracked.** Load it, summarize with
  `status` and `next`, and continue from it (this is Workflow 2). Do **not**
  modify or regenerate the board silently — start *with* the existing board
  as-is. If the user asked for a repo scan, summary, review, "what's done", or
  potential issues, compare the scan against the board and end with a tracker
  proposal: either list the new/updated tickets you recommend, or say no tracker
  changes seem needed. Apply proposed changes only after the user approves them.
- If only `board.html` exists but `board.json` is missing, tell the user the
  source-of-truth JSON is absent and ask whether to rebuild it before changing
  anything.

### Step 1 - Ask before creating a board

If there is no board and the request was an *implicit* start-work or
repo-analysis cue (not an explicit "create the board" / `/init`), **ask first**:

> "This repo isn't tracked yet. Do you want me to create a Jira-style board for
> it?"

Wait for a yes. If the user explicitly asked to set up tracking or ran `/init`,
skip the question and go straight to proposing the seed plan.

For summary/review prompts, do not stop at a plain list of findings. After the
summary, always include the board-creation offer above so the user can turn the
analysis into tracked Done and To Do items.

### Step 2 - Create and seed after the user agrees

1. Pick a name and key: name → repo folder or `package.json` / `pyproject.toml`;
   key → 2–4 uppercase letters (e.g. "Payments" → `PAY`).
2. **Propose the seed plan and let the user review it before writing** (see
   "Tiered board writes" below): list the issues you intend to create with
   their types and statuses, then apply only after they approve.
3. Run `init`, then create the issues.

**Seed to reflect reality: an existing repo starts mostly Done.** The board
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
repo and update the tickets", "summarize what's done", "what are the potential
issues?"), do a scan pass: look for new problems, completed work that's still
marked open, or drift between the board and the code. Then **propose** the new
or updated tickets (new Bugs/Tasks, status corrections), or explicitly say that
no tracker changes seem needed. Propose new or updated issues and apply them only after the user reviews
them; status moves and comments on issues you are actively working follow the
standard tiered rules. Never silently rewrite the board's structure.

---

## Workflow 2 - Start of session

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

## Workflow 3 - A new problem appears

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
python3 "$jira" add --type Bug --title "Webhook retries fire twice" \
  --priority Highest --parent PAY-1 --components webhooks \
  --desc "Duplicate delivery on 5xx; idempotency key not honored on retry."
```

If one request actually contains several pieces of work, create an Epic and
parent the pieces under it rather than cramming everything into one issue.

---

## Workflow 4 - While working: keep the board honest

This discipline is what makes the board trustworthy. Each step below is a
board write on the issue you're working — per "Tiered board writes", apply it
directly and report it in your summary. Confirm first only for confirm-tier
writes (e.g. creating follow-up issues). Apply it every time:

1. **Before** you start an issue: move it to In Progress with a short comment on
   your plan.
   ```bash
   python3 "$jira" move PAY-2 prog --comment "Drafting the async job schema in jobs/schema.py."
   ```
2. **After** you finish:
   - If it's truly complete → `move KEY done --comment "What changed + where."`
   - If it needs the user's review/merge → `move KEY review --comment "..."`.
   - If you abandon it → `move KEY cancelled --comment "why"`.
   Always include a comment that records *what you actually did* and any
   follow-ups (create new issues for follow-ups rather than burying them).
3. If scope changed, **propose the `set` change first** (confirm-tier): retitle,
   re-parent, or adjust priority, then apply after the user agrees so the
   record matches reality.

A good comment is specific: "Added RedisQueue in infra/queue.py, wired into
worker, added unit tests" — not "done".

---

## Workflow 5 - End of every turn: reconcile the board

Trigger: **every turn in a tracked repo** (`.jira/board.json` exists), once
the requested work or thinking is done — regardless of which workflow (if
any) the turn followed. Skip only if the user told you to leave the board
alone.

Run this check silently before closing out your response:

1. Did this turn **finish or advance** any work?
2. Did it **discover** work nobody asked about (a bug noticed while reading,
   a TODO spotted, a follow-up implied by an answer)?
3. Did **scope or priority** change?
4. Was a **decision** made that belongs in an issue's comment trail?

If none apply, end the turn without mentioning the board — no "nothing to
update" noise. If any apply, write per "Tiered board writes" below: statuses
and comments on the issues you touched go in immediately and are reported in
your summary; new issues, scope changes, or bulk corrections are proposed
first.

If a write cannot be applied (e.g. a permission prompt is declined or a
command is blocked), do not silently drop it — say so in your summary and
list the exact pending command(s) so the user can apply or approve them.

Red flags — if you catch yourself thinking any of these, the check applies:

| Rationalization | Reality |
|---|---|
| "Too trivial to track" | Changed files ⇒ recordable; fold into a parent issue's comments, or propose a small Done issue when no parent exists |
| "The user didn't mention the board" | The board exists — that *is* the mention |
| "I'll batch updates later" | Later = never across sessions |
| "This turn was just a question" | Answers that reveal bugs or work still count |
| "The board write was blocked, moving on" | Surface the pending change at turn end instead of dropping it |

---

## Operating principles

- **Tiered board writes.** Two tiers, honoring any standing user preference
  ("just keep it updated" → apply everything; "always ask first" → confirm
  everything):
  - **Auto-apply, then report in your summary:** `move` and `comment` on
    issues tied to this turn's work, and `render`. "Tied to this turn's
    work" means issues that are In Progress (whether moved this session or
    already in progress when the session opened), or issues the user or you
    explicitly named as the subject of the turn.
  - **Confirm first:** `init` seeding, `add` (new issues), `set` (retitle,
    re-priority, re-parent, re-type), reopening Done issues, and bulk
    corrections. State the specific change(s) — keys, type, status
    transition, comment text — and wait for the user's okay.
- **The JSON is the source of truth; the HTML is a view.** Only ever change the
  board through the CLI, which re-renders the HTML for you. If you ever edit
  `board.json` directly, run `render` afterward.
- **Capture first, work second.** New work becomes an issue before you act on it,
  so nothing is lost between sessions.
- **One issue, one status truth.** Move issues as their real state changes; don't
  let In Progress pile up.
- **Don't over-track.** Trivial, sub-five-minute steps inside a larger tracked
  task don't each need an issue — fold them into the parent's comments. But
  standalone work that changed files and has no parent to fold into gets
  recorded: propose a small issue (status Done if already finished) instead of
  leaving the change off the board.
- The board is committed with the repo (it's just files under `.jira/`), so it
  travels with the project and works the same on a fresh clone.

See `references/schema.md` for the exact `board.json` structure if you ever need
to read it programmatically.
