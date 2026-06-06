# 2026-06-06 Review fixes — execution plan

Scope (user-approved): all findings from the 2026-06-06 four-angle review — 9 fixes,
top-5 enhancements, and the JT-20–25 backlog. Work lands directly on main.
Source of truth for code: `.claude/skills/jira-tracker/scripts/jira.py` (CLI + embedded
HTML template in one file). `.codex/skills/jira-tracker/` is a byte-identical mirror
(tests enforce equality — **every task that edits a `.claude/skills` file must copy it
to the `.codex` mirror before running tests/committing**). Generated artifacts:
`.jira/board.html`, `examples/board.html`. Tests: `tests/test_jira.py` (stdlib unittest).

Execution: sequential tasks (A→F), one implementer subagent per task, spec review then
quality review after each. Implementers commit only their task's files (explicit
`git add` paths — `.jira/` churn from board bookkeeping is committed separately at the end).

## Task A — CLI core hardening (board tickets JT-26, JT-27, JT-32, JT-34)

All in `.claude/skills/jira-tracker/scripts/jira.py` Python side + `tests/test_jira.py`.

1. **Locking (JT-26, reproduced bug):** concurrent `load()`→mutate→`save()` loses
   updates (10 parallel `add`s persist 8–9). Add a `board_lock(path)` context manager —
   `fcntl.flock` exclusive on `<dir>/board.lock` — spanning load→mutate→save for every
   mutating command (init/add/move/comment/set) and `render`'s HTML write. Blocking
   acquire. Add `.jira/board.lock` to `.gitignore`.
2. **Unique tmp (JT-26):** `write_atomic` uses a fixed `<name>.tmp` — two writers
   interleave bytes. Use `tempfile.mkstemp(dir=path.parent)` (or pid-suffixed name) +
   `os.replace`; clean up the tmp on failure. Apply to both board.json and board.html writes.
3. **U+2028/U+2029 (JT-27):** in `render_html`, after the existing `</` escape, also
   escape ` `/` ` in the embedded JSON payload.
4. **CLI guards (JT-32):**
   - `move` out of Done/Cancelled to an open status requires `--comment` (die otherwise,
     message says reopening needs a reason).
   - `add`/`set` reject empty/whitespace-only titles (strip stored value).
   - `list --parent` resolves the key via `find()` (die on unknown) instead of silent
     `.upper()` equality.
   - Unify parent validation: one `resolve_parent()` helper (existence + cycle check)
     used by both `add` and `set`.
   - `--file` works both before and after the subcommand (shared parent parser).
5. **Version stamp (JT-34, Friday's incident):** `TEMPLATE_VERSION = 2` constant.
   `save()` stamps `board["template_version"]`. `load()` dies with a clear "this board
   was written by a newer jira.py — run the repo-local copy" when the stored stamp is
   NEWER than the CLI's. `render_html` embeds `<!-- jira-tracker template v{N} -->`.

Tests (TDD): parallel-adds concurrency test (10 threads → 10 issues, counter 10);
U+2028 in a title is escaped in board.html; reopen without comment exits 1 / with
comment succeeds; empty title exits 1; `list --parent BAD` exits 1; newer stamp → load
dies; save writes the stamp; `--file` accepted in both positions.

## Task B — CLI features (JT-36 --json, JT-37 doctor, JT-38 links, JT-40 stale-WIP)

Python side only (template display of links is Task D).

1. **`--json` (JT-36):** global flag; `list`/`next`/`show`/`status`/`doctor` emit
   compact JSON to stdout (full issue dicts; `status` → project/counts/in_progress;
   `next` → recommendations + blocked; `doctor` → problems list). Human output unchanged
   without the flag.
2. **`doctor` (JT-37):** read-only integrity scan: duplicate keys, dangling/cyclic
   parents, dangling/cyclic `blocked_by`, counter below max key number, non-canonical
   status/type/priority, unparseable timestamps, missing required fields. Exit 1 when
   problems found; human one-line-per-problem output.
3. **`link` (JT-38):** `link KEY --blocked-by OTHER` / `link KEY --unblock OTHER`;
   stores sorted-unique `issue["blocked_by"]`; rejects self, unknown keys, and cycles in
   the blocked_by graph (DFS). `show` prints "blocked by" and computed reverse "blocks".
   `next` excludes issues with open blockers from recommendations and lists them in a
   separate "blocked" section.
4. **Stale-WIP (JT-40):** `next`/`status` annotate In Progress issues whose `updated` is
   older than 7 days: `⚠ stale 9d` (constant `STALE_DAYS = 7`).

Tests: JSON shape parses for each command; doctor flags seeded corruption; link
add/remove/cycle/unknown; next hides blocked + lists them; stale annotation (backdate
`updated` by editing the JSON in the test fixture).

## Task C — Template interaction & semantics (JT-20 delegation, JT-21, JT-22, JT-23, JT-30)

Embedded template JS in jira.py. Chosen semantics are FINAL — implement as written.

1. **Event delegation (JT-20):** remove ALL inline `onclick` (cards ~655, rows ~677,
   epic heads ~699, parent link ~721). One delegated click listener on `#main` (and
   drawer) resolving `closest('[data-key]')` → `openIssue`. Cards/rows get
   `tabindex="0" role="button"` + Enter/Space via delegated keydown. Drawer parent link
   becomes a real `<a href="#<KEY>" data-key=...>`. Issue keys must no longer be
   interpolated into any JS-string context.
2. **Transitive epic membership (JT-22):** build once per render a Map issueKey→nearest
   Epic ancestor (walk parent chain, visited-set for cycle safety). `isOrphan` = non-Epic
   with no Epic ancestor. All epic grouping/filtering uses this map (also kills the
   epicsOf-per-issue O(n²) rescan).
3. **Unified epic-filter semantics (JT-21):**
   - `memberCount(e)` = issues (excluding e) whose nearest-epic == e AND passing all
     non-epic facets (type/pri/search). `__none__` counts orphans the same way.
   - Dropdown label count uses `memberCount` — identical population to what the epic
     group displays.
   - Epics view: with any child-affecting filter active, render a group iff
     memberCount > 0; with no filters, render all epics (empty body: "no issues yet").
   - `fEpic` selected and memberCount == 0 → render the global noMatch() empty state,
     never a hollow epic header.
   - Progress bar: overall done/total over ALL transitive members (unfiltered), text
     "n/m done", `title` attribute clarifying it is overall progress.
   - Board view with fEpic: issues whose nearest-epic == fEpic, plus the epic card itself.
4. **Stats while scrolled (JT-23):** compact per-status count chips inside the sticky
   toolbar, visible only when `.stuck` (same pattern as the `pkey2` mini badge).
5. **URL-hash state (JT-30):** serialize {view, epic, type, pri, q, open-issue} to
   `location.hash` via `history.replaceState` on every render/open/close; parse on load
   (before first render); support bare `#JT-7` as open-issue shorthand; `hashchange`
   listener re-parses external changes.

Tests: regenerated HTML contains NO `onclick=`; contains `data-key`, `replaceState`,
`hashchange`, `role="button"` markers; update any TestModernTemplate assertions broken
by the new markup. Sync the .codex mirror.

## Task D — Template visual/a11y/polish (JT-28, JT-29, JT-24, JT-33, JT-39 + JT-38 UI, JT-20 focus part)

1. **Drawer dialog (JT-28):** `role="dialog" aria-modal="true" aria-labelledby` on the
   drawer; on open focus the close button and remember the opener; trap Tab inside while
   open; Esc closes; on close restore focus to the opener. Global `:focus-visible`
   outline for cards/rows/links/buttons.
2. **Contrast (JT-29):** raise badge tint 14%→~24%; adjust light-theme greens
   (--story/--done) and --pri-high; darken `--faint` in both themes; promote `.ckey` and
   `.prog-n` to `--muted`; give the drawer priority pill a tinted background like other
   pills. VERIFY computationally (relative-luminance, tint composited over card bg) that
   badge text ≥ 4.5:1 in BOTH themes — include the computation in your report.
3. **Dynamic status CSS (JT-24):** Python generates the grid for `len(statuses)` columns
   (e.g. `repeat(auto-fill, minmax(235px,1fr))` or injected count — no hardcoded
   `repeat(5`), responsive steps ~1240/900/640px, and a status→color assignment for
   EVERY board status (known 5 keep their palette; unknown statuses get distinct colors
   from a deterministic fallback palette, not all-grey).
4. **Polish (JT-33):** `@media print` (force light tokens, static toolbar, hide
   controls/scrim/drawer, cards avoid page-breaks); `prefers-reduced-motion: reduce`
   disables transitions/transforms; linkify `https?://` URLs in descriptions/comments
   (escape-first, `rel="noopener"`); theme syncs across tabs (`storage` event) and
   follows OS changes when no explicit override; search input flexes (`flex:1`,
   min 160px, max 320px).
5. **Relative times (JT-39):** "2d ago"-style relative timestamps on cards (updated),
   drawer meta, comments, footer — absolute ISO in `title`.
6. **Blocked-by display (JT-38 UI):** drawer shows "Blocked by JT-x (open/closed)" as
   data-key chips; cards show a small blocked indicator when any blocker is open.

Tests: marker assertions (`role="dialog"`, `prefers-reduced-motion`, `@media print`,
`storage`, no `repeat(5`); render a 6-status fixture and assert a 6th column + non-grey
color emerge. Sync the .codex mirror.

## Task E — Docs reconciliation (JT-31)

`SKILL.md`, `references/schema.md`, `README.md` (+ .codex mirrors of the first two).

1. CLI table: regenerate against the FINAL `--help` — include `--file` (both positions),
   `--author`, `--force`, `--json`, `doctor`, `link`, stale annotations, the
   reopen-requires-comment rule, locking, version-stamp behavior.
2. New "Workflow 6 — Lifecycle & corrections": reopen mechanics (move + mandatory
   comment, confirm-tier), cancel-don't-delete, comments are append-only, the sanctioned
   merge-conflict recipe (union issues by key, counter = max, then `render` — the ONE
   allowed hand-edit), corrupt/missing board recovery (git restore or `init --force`).
3. New "Multiple copies & concurrency" section: prefer the repo-local copy over global
   installs (2026-06-06 incident), same-machine writes are serialized by the lock,
   version-stamp mismatch behavior.
4. Tighten the auto-apply tier definition: auto-writes only on issues actually worked
   THIS turn; stale In-Progress issues you didn't touch get proposals. Add the two
   worked micro-examples (correct auto-apply vs must-propose).
5. "Leave the board alone" = session-scoped unless stated otherwise; large-board
   guidance (filtered `list`, `next --limit`, Done stays in file).
6. schema.md: `template_version`, `blocked_by`, board.lock (gitignored), `list --parent`
   now validated. README: mention new commands briefly; keep install steps accurate.

## Task F — Integration & sync (JT-25)

1. **Artifact-sync test (JT-25):** test renders `examples/sample-board.json` via
   `--file` into a temp dir and asserts byte-equality with the committed
   `examples/board.html`.
2. Regenerate `examples/board.html` and `.jira/board.html` with the final template.
3. Sync mirrors: `.codex/skills/jira-tracker/` and the global
   `~/.claude/skills/jira-tracker/` (user-approved pattern) — byte-identical.
4. Full test suite green; `doctor` clean on `.jira/board.json`; final commit including
   board bookkeeping.

## Final review

One reviewer subagent over the entire diff (base = commit before Task A) verifying all
board tickets' acceptance criteria; fix-up loop if needed.
