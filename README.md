# jira-tracker — a local, agent-driven work board for any repo

A starting point for new git repositories that gives you (and your AI agent) a
**Jira-style work tracker** living right inside the project — no SaaS, no
accounts, no network. Work is stored as a single JSON file and rendered to a
clean, browsable HTML board.

It's packaged for **Claude Code** (`.claude/skills/jira-tracker/`) and
**Codex** (`.codex/skills/jira-tracker/`) so either agent can check the board at
the start of a session, create the correctly-typed issue whenever a new problem
comes up, and update status + leave a comment after finishing anything.

## What you get

```
.claude/skills/jira-tracker/
├── SKILL.md                 # the workflow the agent follows
├── scripts/jira.py          # the tracker CLI (Python 3 stdlib only)
├── scripts/install-board-hook.py  # optional every-turn reminder hook (Claude Code)
└── references/schema.md     # board.json structure
.codex/skills/jira-tracker/
├── SKILL.md                 # same workflow, packaged for Codex
├── agents/openai.yaml       # Codex UI metadata
├── scripts/jira.py          # same tracker CLI
├── scripts/install-board-hook.py  # same file (unused by Codex — no hook support)
└── references/schema.md     # same board.json structure
examples/sample-board.json   # a filled-in example you can open via the CLI
examples/board.html          # the rendered view of that example
```

When used, the board itself lives in the repo root under:

```
.jira/board.json   # source of truth
.jira/board.html   # generated view — open it in a browser
```

## How it behaves (the five moments)

1. **`/init`, or *any* cue that you're starting work** ("let's start on this
   repo", "read this repo", "help me work here") → if the repo isn't tracked yet
   it **asks first** ("want me to create a board?"), then seeds it. For an
   **existing codebase the already-built work is created as `Done`** so the board
   reflects the repo's real current state, with a short list of open items
   (TODOs, bugs, unfinished work). A blank repo gets a small `To Do` starter
   scaffold. If a board already exists, it **loads it and leaves it untouched
   unless you ask** to change it.
2. **Start of a session** ("continue", "keep working", "update X", "what's
   next") → the agent reads the board, reports status, and proposes the
   highest-priority next task *before* doing anything.
3. **A new problem appears** → it's captured as the right type (Epic / Story /
   Task / Bug / Sub-task) with priority, description, and a parent link.
4. **After work** → the issue moves to Done / In Review / Cancelled with a
   comment recording *what actually changed*.
5. **End of every turn** → in a tracked repo, once the requested work is done
   the agent runs a board-reconciliation check: if the turn finished,
   advanced, or discovered work, the board is updated; if not, it stays
   silent.

**Board writes are tiered.** Routine bookkeeping on the issues being worked —
status moves, comments, re-rendering — is applied immediately and reported in
the agent's summary. Structural changes — new issues, retitles, re-priorities,
seeding — are proposed for your review first. A standing preference ("just
keep it updated" / "always ask first") overrides either way.

## Install globally

Use a global install when you want the skill available to your agent in every
repo without copying `.claude/` or `.codex/` into each project.

From this repository root:

```bash
# Claude Code
mkdir -p "$HOME/.claude/skills"
rm -rf "$HOME/.claude/skills/jira-tracker"
cp -R .claude/skills/jira-tracker "$HOME/.claude/skills/"
```

```bash
# Codex
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/jira-tracker"
cp -R .codex/skills/jira-tracker "${CODEX_HOME:-$HOME/.codex}/skills/"
```

After installing, open any repo with your agent and say
**"set up work tracking for this repo"** or **"continue from the Jira board."**

## Install in a project

1. Copy the agent package you use into your repo, or use this repo as a
   template:
   - Claude Code: copy `.claude/`
   - Codex: copy `.codex/`
   - Both agents: copy both directories
2. Open the target repo with your agent.
3. Run `/init`, or just say **"set up work tracking for this repo."**
4. Open `.jira/board.html` in a browser to see the board.

### Update an existing project install

If this repository is checked out at `/path/to/jira-tracker` and your target
project is `/path/to/my-app`, refresh the local agent package with one command.
Change the two paths first.

```bash
# Claude Code
src=/path/to/jira-tracker dst=/path/to/my-app; mkdir -p "$dst/.claude/skills" && rm -rf "$dst/.claude/skills/jira-tracker" && cp -R "$src/.claude/skills/jira-tracker" "$dst/.claude/skills/"
```

```bash
# Codex
src=/path/to/jira-tracker dst=/path/to/my-app; mkdir -p "$dst/.codex/skills" && rm -rf "$dst/.codex/skills/jira-tracker" && cp -R "$src/.codex/skills/jira-tracker" "$dst/.codex/skills/"
```

To install or refresh both agents in one project, run both commands.

## Guarantee layer (optional, Claude Code only)

Skill instructions are best-effort — for a guaranteed every-turn reminder,
install the bundled `UserPromptSubmit` hook. It injects a one-line reconcile
reminder whenever the current repo has `.jira/board.json`:

```bash
# project-level (.claude/settings.json in the current repo)
python3 .claude/skills/jira-tracker/scripts/install-board-hook.py

# or once, globally (~/.claude/settings.json) — a no-op in untracked repos
python3 .claude/skills/jira-tracker/scripts/install-board-hook.py --global
```

Re-running is safe (idempotent), and existing settings and hooks are
preserved. Codex has no hook mechanism and relies on the skill text alone.

For the smoothest auto-applied bookkeeping, also consider allowlisting the
tracker CLI in your Claude Code permissions (e.g.
`Bash(python3 *jira-tracker/scripts/jira.py *)` in `.claude/settings.json`),
so routine `move`/`comment` updates don't hit permission prompts.

## Driving it by hand (optional)

The CLI is plain Python and works without an agent:

```bash
jira=".claude/skills/jira-tracker/scripts/jira.py"
# or, in a Codex-packaged repo:
# jira=".codex/skills/jira-tracker/scripts/jira.py"

python3 $jira init --name "My App" --key APP --repo "github.com/me/app"
python3 $jira add --type Epic  --title "User accounts" --priority High
python3 $jira add --type Story --title "Sign up with email" --parent APP-1
python3 $jira move APP-2 "in progress" --comment "Building the form."
python3 $jira next        # what should I do?
python3 $jira status      # board summary
python3 $jira render      # regenerate board.html
```

Try it instantly against the included example:

```bash
python3 .claude/skills/jira-tracker/scripts/jira.py --file examples/sample-board.json status
python3 .claude/skills/jira-tracker/scripts/jira.py --file examples/sample-board.json render
# then open examples/board.html
```

## Design notes

- **JSON is truth; HTML is a view.** Every mutating CLI command re-renders the
  HTML automatically, so it's never stale.
- **No dependencies.** Python 3.8+ standard library only.
- **It travels with the repo.** `.jira/` is committed, so the board survives a
  fresh clone and is the same for everyone.
- The HTML board has a Kanban (by-status) view and a By-Epic hierarchy view,
  type/priority filters, and a click-through detail panel with the comment trail.

The board is local and offline; the only network use is loading two web
fonts in the HTML, which degrades gracefully to system fonts if you're offline.
