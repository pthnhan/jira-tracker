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

You never hand-edit the JSON (see Workflow 6 for the one sanctioned exception).
All changes go through the bundled CLI, which validates input and **re-renders
the HTML automatically after every change** so the board is never stale.

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

> **`--help` is authoritative.** Run `python3 "$jira" <cmd> --help` for the
> full flag list of any subcommand. Global flags `--file` and `--json` work
> **both before and after** the subcommand name.

Commands:

| Command | Purpose |
|---|---|
| `init --name N --key K [--repo URL] [--force]` | Create a new board (`--force` overwrites an existing one) |
| `add --type T --title "..." [--priority P --parent KEY --desc "..." --labels a,b --components x --status S --assignee A]` | Create an issue, prints its key; rejects blank/whitespace titles |
| `move KEY [KEY...] STATUS [--comment "..."] [--author A]` | Change status of one or more issues (fuzzy: `prog`, `done`, `review` all work); **reopening (moving from Done/Cancelled to an open status) requires `--comment`** — closed→closed moves don't. Bulk is atomic: all keys + status validated first, nothing changes on any failure |
| `comment KEY "text" [--author A]` | Append a comment (append-only — no edit/delete) |
| `set KEY [KEY...] [--title/--desc/--priority/--type/--parent/--assignee/--labels/--components]` | Edit fields on one or more issues (flags apply to all); rejects blank/whitespace titles; `--parent ""` clears the parent. Bulk is atomic: all keys + values validated first, nothing changes on any failure |
| `list [--status S --type T --parent KEY --all]` | List issues (open only unless `--all`); `--parent KEY` validates the key |
| `next [--limit N]` | Recommend what to work on; blocked issues go to a trailing "blocked:" section; In Review issues are called out as awaiting human review; annotates stale In-Progress issues with `⚠ stale Nd` |
| `show KEY` | Full detail of one issue (shows blocked-by and blocks relationships) |
| `status` | One-screen board summary; lists In Review issues as awaiting human review; annotates stale In-Progress issues with `⚠ stale Nd` |
| `search QUERY` | Case-insensitive substring search across ALL issues (Done included) — matches key, title, description, labels, components, and comment bodies; read-only; exits 0 with a "no matches" line when nothing matches |
| `report` | Read-only metrics summary: total, counts by status/type/priority, stale count, and cycle time (creation → first transition into a terminal status) avg/median for completed issues |
| `set-project [--name N --repo URL]` | Edit project fields after init; `--repo ""` clears the URL; the key is not editable (issue keys derive from it) |
| `doctor` | 12-code integrity scan — exits 0 if healthy, 1 if problems found |
| `link KEY --blocked-by OTHER` | Mark KEY as blocked by OTHER (cycle-rejected, idempotent) |
| `link KEY --unblock OTHER` | Remove OTHER from KEY's blocked_by |
| `render` | Force-regenerate `board.html` (read-only, does not modify `board.json`) |

Global flags (work before or after the subcommand):

| Flag | Purpose |
|---|---|
| `--file PATH` | Use a board at `PATH` instead of `.jira/board.json` |
| `--json` | Emit compact JSON to stdout — preferred for agent use (stable, no human-readable noise) |

Vocabulary (use these exact words): **types** Epic, Story, Task, Bug, Sub-task ·
**statuses** To Do, In Progress, In Review, Done, Cancelled · **priorities**
Highest, High, Medium, Low, Lowest.

An issue is **stale** when it has been In Progress for ≥ 7 days without an
update (configurable via `STALE_DAYS`). `next` and `status` annotate stale
issues and `status --json` lists stale keys in the `stale` array.

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
repo-analysis cue (not an explicit "create the board" / `/init`), **ask first**
— and ask with the **`AskUserQuestion` tool** (interactive multiple-choice
prompt), *not* as plain text in your reply. A plain-text question is easy to
scroll past and ignore; the tool blocks until the user picks an option.

- Question: "This repo isn't tracked yet. Create a Jira-style board for it?"
- Options:
  1. **"Yes, create the board"** — scan the repo and propose a seed plan
     (Step 2).
  2. **"Not now"** — continue with the request without tracking, and don't
     re-ask this session.

If the `AskUserQuestion` tool is not available in the current environment
(e.g. Codex or another non-Claude-Code harness), fall back to the same
question as plain text and wait for a yes.

If the user explicitly asked to set up tracking or ran `/init`, skip the
question and go straight to proposing the seed plan.

For summary/review prompts, do not stop at a plain list of findings. After the
summary, always present the board-creation offer above (via `AskUserQuestion`)
so the user can turn the analysis into tracked Done and To Do items.

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

1. `status` — get counts and what's currently In Progress. Note stale `⚠`
   annotations (In Progress > 7 days without update) — these may need attention.
2. `next` — get the priority-ordered recommendation. The CLI surfaces In
   Progress items first (finish what's started), then To Do by priority.
   Blocked issues appear in a trailing "blocked:" section — don't schedule them
   unless the blocker is already resolved. Use `--json` if you need to process
   the output programmatically.
3. Briefly tell the user the state and your proposed next item, e.g.
   "PAY-2 (async schema) is in progress and PAY-4 is a Highest-priority bug —
   I'll resume PAY-2 first." Let them redirect if they want.
4. If they named a specific area ("update X"), `list` / `show` the matching
   issues to ground yourself before acting.

Never start work in a tracked repo without first reading the board — the whole
point is continuity across sessions.

**Large boards:** prefer `list --status "In Progress"`, `list --type Bug`, or
`list --parent EPIC-KEY` over unfiltered `list` to avoid scrolling. Use
`next --limit N` to cap the recommendation list. Closed issues (Done/Cancelled)
accumulate over time; use `list --status Done` or `list --all` only when you
specifically need them.

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
alone (this preference is **session-scoped** unless they say "always" or
"permanently" — re-check at the next session start).

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

## Workflow 6 - Lifecycle & corrections

### Reopening Done or Cancelled issues

```bash
python3 "$jira" move KEY "To Do" --comment "why reopening"
```

This is a **confirm-tier** write (propose before applying). The CLI enforces
the comment — it will error without `--comment`. The comment becomes the
permanent audit trail entry.

### Cancel, don't delete

There is no `delete` command by design. If work is abandoned, move it to
Cancelled with a comment explaining why. Cancelled issues stay on the board as
a record.

### Comments are append-only

`comment KEY "text"` adds to the trail; there is no edit or delete. Keep
comments accurate from the start — corrections must be new comments that
acknowledge the change.

### Recovery from corruption

- **Corrupt or missing `board.json`:** restore from git (`git checkout HEAD -- .jira/board.json`) or run `init --force` to start fresh. Do **not** hand-edit a corrupt file to fix it; the JSON structure has interdependencies.
- **After any recovery:** always run `doctor` to confirm the board is consistent.

### Resolving merge conflicts on `board.json`

`board.json` is a structured file that git may report as a conflict. This is the **one sanctioned hand-edit**: manually open the conflicted file, union both sides' issues by key (keep any issue that appears on either side), keep the higher `project.counter`, resolve other fields from whichever side has the later `updated` timestamp, then run `render` to regenerate the HTML. Run `doctor` after to confirm.

Example sequence:

```bash
# 1. Resolve the conflict in your editor as described above.
python3 "$jira" render          # 2. regenerate board.html
python3 "$jira" doctor          # 3. confirm integrity
```

---

## Multiple copies & concurrency

### Prefer the repo-local copy

When a repo ships `.claude/skills/jira-tracker/scripts/jira.py`, always invoke
that copy — not a globally-installed one. The local copy is the one whose
`TEMPLATE_VERSION` matches the boards written in that repo. A globally-installed
copy that is older than the repo-local one will be refused by the board's
version guard (`error: this board was written by a newer jira.py`).

```bash
# Always resolve from the loaded skill directory:
jira=<this-skill-dir>/scripts/jira.py
```

The version stamp is the backstop: if an older copy somehow runs against a
board written by a newer one, it refuses to operate — preventing a stale
embedded template from silently regressing the rendered board.

### Same-machine concurrent writes

All mutating commands (`add`, `move`, `comment`, `set`, `link`, `init`) and
`render` serialize on `<board-dir>/board.lock` using `fcntl.flock` (exclusive
lock, POSIX). On non-POSIX systems the lock is a no-op — concurrent writes on
Windows are not serialized at the file level. Board JSON is written atomically
(unique tempfile + rename, 0644 permissions) so readers never see a
half-written file.

`board.lock` is transient — gitignore it (already included in the default
`.gitignore`).

### Cross-branch divergence

If two branches each advance the board independently and then merge, follow the
merge-conflict recipe in Workflow 6 above.

---

## Operating principles

- **Tiered board writes.** Two tiers, honoring any standing user preference
  ("just keep it updated" → apply everything; "always ask first" → confirm
  everything):
  - **Auto-apply, then report in your summary:** `move` and `comment` on
    issues directly tied to **work you actually did or advanced this turn**.
    "This turn" means:
    - An issue you moved to In Progress this turn, or
    - An issue that was already In Progress when the session opened **and** you
      actively worked on it or advanced it this turn.
    A stale In-Progress issue that you looked at but did **not** advance this
    turn is **not** auto-written — propose the update instead.

    *Example — correct auto-apply:* You move PAY-3 to In Progress, implement
    the feature, then move it to Done. Both moves + a closing comment are
    auto-applied.

    *Example — must propose:* You read the board and notice PAY-7 has been
    In Progress for 12 days (stale `⚠`). You didn't touch it this turn.
    Propose: "PAY-7 appears stale — should I add a comment or move it back to
    To Do?"

  - **Confirm first:** `init` seeding, `add` (new issues), `set` (retitle,
    re-priority, re-parent, re-type), reopening Done/Cancelled issues, and bulk
    corrections. State the specific change(s) — keys, type, status
    transition, comment text — and wait for the user's okay.
- **The JSON is the source of truth; the HTML is a view.** Only ever change the
  board through the CLI. If you ever hand-edit `board.json` (only the merge
  recipe in Workflow 6 permits this), run `render` and then `doctor` afterward.
- **Use `--json` for programmatic reads.** `list --json`, `next --json`,
  `show KEY --json`, `status --json`, and `doctor --json` emit stable JSON
  shapes with no human-readable noise — use these when you need to parse board
  state in a script or multi-step agent turn.
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
