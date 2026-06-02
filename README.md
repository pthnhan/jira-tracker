# jira-tracker — a local, agent-driven work board for any repo

A starting point for new git repositories that gives you (and your AI agent) a
**Jira-style work tracker** living right inside the project — no SaaS, no
accounts, no network. Work is stored as a single JSON file and rendered to a
clean, browsable HTML board.

It's packaged as a **Claude Code Agent Skill** (`.claude/skills/jira-tracker/`)
so the agent knows, on its own, to check the board at the start of a session,
create the correctly-typed issue whenever a new problem comes up, and update
status + leave a comment after finishing anything.

## What you get

```
.claude/skills/jira-tracker/
├── SKILL.md                 # the workflow the agent follows
├── scripts/jira.py          # the tracker CLI (Python 3 stdlib only)
└── references/schema.md     # board.json structure
examples/sample-board.json   # a filled-in example you can open via the CLI
```

When used, the board itself lives in the repo root under:

```
.jira/board.json   # source of truth
.jira/board.html   # generated view — open it in a browser
```

## How it behaves (the four moments)

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

**Every board change is proposed for your review before it's applied** — the
agent won't silently rewrite the board. You can grant a standing "just keep it
updated without asking" for a session if you prefer.

## Using the starter

1. Copy this `.claude/` folder into your repo (or use this repo as a template),
   then `git init` if it's new.
2. Open the repo with Claude Code (or any agent that reads `.claude/skills/`).
3. Run `/init`, or just say **"set up work tracking for this repo."**
4. Open `.jira/board.html` in a browser to see the board.

## Driving it by hand (optional)

The CLI is plain Python and works without an agent:

```bash
jira=".claude/skills/jira-tracker/scripts/jira.py"

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
