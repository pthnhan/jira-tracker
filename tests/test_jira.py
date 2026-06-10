"""Black-box tests for the jira.py CLI — stdlib only, no third-party deps.

Each test runs the real CLI via subprocess in a temp directory, so the suite
exercises exactly what agents and humans run. The .claude tree is the code
under test; a sync test asserts the .codex tree is byte-identical.

Run: python3 -m unittest discover -s tests -v
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JIRA = REPO / ".claude/skills/jira-tracker/scripts/jira.py"
CODEX_TREE = REPO / ".codex/skills/jira-tracker"
CLAUDE_TREE = REPO / ".claude/skills/jira-tracker"

# Single source for the shared-file list: import it from the script under test
# so the byte-equality backstop and the sync tests can never list a different
# set of files than sync.py actually mirrors.
sys.path.insert(0, str(CLAUDE_TREE / "scripts"))
import sync as _sync  # noqa: E402
SHARED_FILES = _sync.SHARED


def run(args, cwd):
    return subprocess.run([sys.executable, str(JIRA), *args],
                          cwd=cwd, capture_output=True, text=True)


class BoardTestCase(unittest.TestCase):
    """Base: fresh board in a temp dir per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        r = run(["init", "--name", "Test", "--key", "TST"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)

    def cli(self, *args, ok=True):
        r = run(list(args), self.dir)
        if ok:
            self.assertEqual(r.returncode, 0, f"{args} failed: {r.stderr}")
        return r

    def board(self):
        return json.loads((self.dir / ".jira/board.json").read_text())

    def issue(self, key):
        return next(i for i in self.board()["issues"] if i["key"] == key)


# --------------------------------------------------------------------------- #
# Regression coverage for existing behavior
# --------------------------------------------------------------------------- #

class TestExistingBehavior(BoardTestCase):

    def test_init_creates_json_and_html(self):
        self.assertTrue((self.dir / ".jira/board.json").exists())
        self.assertTrue((self.dir / ".jira/board.html").exists())

    def test_init_refuses_to_overwrite_without_force(self):
        r = run(["init", "--name", "Again", "--key", "AG"], self.dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already exists", r.stderr)

    def test_add_assigns_sequential_keys_and_defaults(self):
        r = self.cli("add", "--type", "Task", "--title", "First")
        self.assertIn("TST-1", r.stdout)
        self.cli("add", "--type", "Bug", "--title", "Second")
        i = self.issue("TST-2")
        self.assertEqual((i["status"], i["priority"]), ("To Do", "Medium"))

    def test_add_rejects_unknown_parent(self):
        r = self.cli("add", "--type", "Task", "--title", "Orphan",
                     "--parent", "TST-99", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not found", r.stderr)

    def test_move_full_status_name(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "in progress")
        self.assertEqual(self.issue("TST-1")["status"], "In Progress")

    def test_fuzzy_exact_lowercase_and_unique_prefix(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "done")            # case-insensitive exact
        self.assertEqual(self.issue("TST-1")["status"], "Done")
        self.cli("move", "TST-1", "can")             # unique prefix
        self.assertEqual(self.issue("TST-1")["status"], "Cancelled")

    def test_fuzzy_ambiguous_input_errors(self):
        self.cli("add", "--type", "Task", "--title", "T")
        r = self.cli("move", "TST-1", "in", ok=False)  # In Progress vs In Review
        self.assertNotEqual(r.returncode, 0)

    def test_comment_appends_to_trail(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("comment", "TST-1", "first note")
        self.cli("comment", "TST-1", "second note", "--author", "human")
        comments = self.issue("TST-1")["comments"]
        self.assertEqual([c["body"] for c in comments], ["first note", "second note"])
        self.assertEqual(comments[1]["author"], "human")

    def test_set_updates_fields(self):
        self.cli("add", "--type", "Task", "--title", "Old")
        self.cli("set", "TST-1", "--title", "New", "--priority", "High")
        i = self.issue("TST-1")
        self.assertEqual((i["title"], i["priority"]), ("New", "High"))

    def test_set_clears_parent_with_empty_string(self):
        self.cli("add", "--type", "Epic", "--title", "E")
        self.cli("add", "--type", "Task", "--title", "T", "--parent", "TST-1")
        self.cli("set", "TST-2", "--parent", "")
        self.assertIsNone(self.issue("TST-2")["parent"])

    def test_list_default_hides_closed(self):
        self.cli("add", "--type", "Task", "--title", "Open one")
        self.cli("add", "--type", "Task", "--title", "Closed one", "--status", "Done")
        r = self.cli("list")
        self.assertIn("Open one", r.stdout)
        self.assertNotIn("Closed one", r.stdout)

    def test_next_recommends_in_progress_before_todo(self):
        self.cli("add", "--type", "Task", "--title", "Queued", "--priority", "Highest")
        self.cli("add", "--type", "Task", "--title", "Started", "--priority", "Low")
        self.cli("move", "TST-2", "In Progress")
        r = self.cli("next")
        self.assertLess(r.stdout.index("Started"), r.stdout.index("Queued"))

    def test_show_displays_detail(self):
        self.cli("add", "--type", "Bug", "--title", "Broken", "--desc", "It hurts")
        r = self.cli("show", "TST-1")
        self.assertIn("Broken", r.stdout)
        self.assertIn("It hurts", r.stdout)

    def test_status_counts(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Task", "--title", "B", "--status", "Done")
        r = self.cli("status")
        self.assertIn("2 issue(s)", r.stdout)


# --------------------------------------------------------------------------- #
# Review bugs — JT-7 .. JT-13
# --------------------------------------------------------------------------- #

class TestReviewBugs(BoardTestCase):

    def test_move_accepts_prog_shorthand(self):
        """JT-7: SKILL.md promises `prog` resolves to In Progress."""
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "prog")
        self.assertEqual(self.issue("TST-1")["status"], "In Progress")

    def test_move_accepts_review_shorthand(self):
        """JT-7: SKILL.md promises `review` resolves to In Review."""
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "review")
        self.assertEqual(self.issue("TST-1")["status"], "In Review")

    def test_list_status_done_finds_closed_without_all(self):
        """JT-8: an explicit --status filter must not be wiped by the open-only default."""
        self.cli("add", "--type", "Task", "--title", "Shipped", "--status", "Done")
        r = self.cli("list", "--status", "done")
        self.assertIn("Shipped", r.stdout)

    def test_render_does_not_modify_board_json(self):
        """JT-9: render is a read-only view regeneration."""
        self.cli("add", "--type", "Task", "--title", "T")
        # Backdate the timestamp so any rewrite by `render` changes the bytes.
        p = self.dir / ".jira/board.json"
        b = json.loads(p.read_text())
        b["project"]["updated"] = "2020-01-01T00:00:00+00:00"
        p.write_text(json.dumps(b, indent=2) + "\n")
        before = p.read_bytes()
        self.cli("render")
        self.assertEqual(before, p.read_bytes())

    def test_init_rejects_invalid_key(self):
        """JT-10: keys land in HTML/JS strings; quotes and tags must be refused."""
        with tempfile.TemporaryDirectory() as d:
            r = run(["init", "--name", "X", "--key", "A'B</script>"], Path(d))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("error:", r.stderr)

    def test_template_esc_covers_single_quote(self):
        """JT-10: the HTML esc() helper must escape ' as well."""
        html = (self.dir / ".jira/board.html").read_text()
        self.assertIn("'&#39;'", html)

    def test_corrupt_json_dies_cleanly(self):
        """JT-11: a broken board.json should produce a friendly error, not a traceback."""
        (self.dir / ".jira/board.json").write_text("{broken")
        r = self.cli("status", ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("error:", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_save_leaves_no_temp_files(self):
        """JT-11: atomic writes must clean up after themselves (board.lock is expected)."""
        self.cli("add", "--type", "Task", "--title", "T")
        leftovers = [p.name for p in (self.dir / ".jira").iterdir()
                     if p.name not in ("board.json", "board.html", "board.lock",
                                       ".gitignore")]
        self.assertEqual(leftovers, [])

    def test_set_rejects_self_parent(self):
        """JT-12: an issue cannot be its own parent."""
        self.cli("add", "--type", "Task", "--title", "T")
        r = self.cli("set", "TST-1", "--parent", "TST-1", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIsNone(self.issue("TST-1")["parent"])

    def test_set_rejects_parent_cycle(self):
        """JT-12: A -> B -> A must be refused."""
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Sub-task", "--title", "B", "--parent", "TST-1")
        r = self.cli("set", "TST-1", "--parent", "TST-2", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIsNone(self.issue("TST-1")["parent"])


# --------------------------------------------------------------------------- #
# Packaging — JT-13, JT-15
# --------------------------------------------------------------------------- #

class TestPackaging(unittest.TestCase):

    def test_skill_md_has_no_zsh_breaking_alias(self):
        """JT-13: `jira="python3 ..."` + `$jira cmd` relies on word splitting,
        which zsh does not do. The docs must use a zsh-safe invocation."""
        for tree in (CLAUDE_TREE, CODEX_TREE):
            text = (tree / "SKILL.md").read_text()
            self.assertNotIn('jira="python3', text, f"zsh-breaking alias in {tree}/SKILL.md")

    def test_claude_and_codex_trees_in_sync(self):
        """JT-15: the shared files of both packagings must be byte-identical."""
        for rel in SHARED_FILES:
            a = (CLAUDE_TREE / rel).read_bytes()
            b = (CODEX_TREE / rel).read_bytes()
            self.assertEqual(a, b, f"{rel} differs between .claude and .codex trees")


class TestSyncScript(unittest.TestCase):
    """JT-48: scripts/sync.py mirrors the shared files from .claude (the single
    source) into .codex, and with --global refreshes external installs.

    Each test runs against an isolated temp copy of both trees (sync.py anchors
    its source to the .claude tree above the script, so the temp copy is self-
    contained) — the real repo is never mutated by the suite."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.claude = root / ".claude/skills/jira-tracker"
        self.codex = root / ".codex/skills/jira-tracker"
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "board.lock")
        shutil.copytree(CLAUDE_TREE, self.claude, ignore=ignore)
        shutil.copytree(CODEX_TREE, self.codex, ignore=ignore)
        self.sync = self.claude / "scripts/sync.py"

    def _run(self, *flags, env=None):
        return subprocess.run([sys.executable, str(self.sync), *flags],
                              capture_output=True, text=True, env=env)

    def test_sync_script_exists_in_both_trees(self):
        self.assertTrue((CLAUDE_TREE / "scripts/sync.py").exists(),
                        "scripts/sync.py missing from .claude tree")
        self.assertTrue((CODEX_TREE / "scripts/sync.py").exists(),
                        "scripts/sync.py missing from .codex tree")

    def test_check_mode_passes_on_a_synced_repo(self):
        """--check exits 0 when the trees already match (freshly copied state)."""
        r = self._run("--check")
        self.assertEqual(r.returncode, 0, f"--check failed on a synced repo: {r.stdout}{r.stderr}")

    def test_sync_restores_a_diverged_codex_file(self):
        """Corrupt a .codex shared file, run sync, confirm it is restored from
        .claude — and that --check flagged the drift beforehand."""
        target = self.codex / "references/schema.md"
        original = target.read_bytes()
        target.write_bytes(original + b"\n<!-- drift -->\n")

        check = self._run("--check")
        self.assertNotEqual(check.returncode, 0, "--check did not detect the drift")

        r = self._run()
        self.assertEqual(r.returncode, 0, f"sync failed: {r.stderr}")
        self.assertEqual(target.read_bytes(), original,
                         "sync did not restore the diverged .codex file from .claude")

    def test_sync_preserves_codex_only_files(self):
        """The .codex tree has agents/openai.yaml with no .claude counterpart;
        sync must not delete it."""
        codex_only = self.codex / "agents/openai.yaml"
        self.assertTrue(codex_only.exists(), "precondition: codex-only file should exist")
        before = codex_only.read_bytes()
        r = self._run()
        self.assertEqual(r.returncode, 0, f"sync failed: {r.stderr}")
        self.assertTrue(codex_only.exists(), "sync deleted a codex-only file")
        self.assertEqual(codex_only.read_bytes(), before)

    def test_check_and_global_are_mutually_exclusive(self):
        """--check and --global together must error, not silently skip --global."""
        r = self._run("--check", "--global")
        self.assertNotEqual(r.returncode, 0, "--check --global should be rejected")
        self.assertIn("not allowed with", r.stderr)

    def test_global_flag_copies_into_a_redirected_home(self):
        """--global refreshes ~/.claude and $CODEX_HOME installs. Redirect both
        to temp dirs via HOME / CODEX_HOME so the test never touches the real
        installs."""
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as codex_home:
            env = dict(os.environ, HOME=home, CODEX_HOME=codex_home)
            r = self._run("--global", env=env)
            self.assertEqual(r.returncode, 0, f"--global failed: {r.stderr}")
            gc = Path(home) / ".claude/skills/jira-tracker/scripts/jira.py"
            gx = Path(codex_home) / "skills/jira-tracker/scripts/jira.py"
            self.assertTrue(gc.exists(), "global .claude jira.py not installed")
            self.assertTrue(gx.exists(), "global .codex jira.py not installed")
            self.assertEqual(gc.read_bytes(), (CLAUDE_TREE / "scripts/jira.py").read_bytes())
            # global installs must not carry over build cruft
            self.assertFalse((Path(home) / ".claude/skills/jira-tracker/scripts/__pycache__").exists(),
                             "__pycache__ leaked into the global install")


# --------------------------------------------------------------------------- #
# Board UI redesign — template features (JT board modernization)
# --------------------------------------------------------------------------- #

class TestModernTemplate(BoardTestCase):
    """The rendered board.html must contain the redesigned UI's hooks.

    Black-box like the rest of the suite: we assert on the rendered file,
    not on jira.py internals."""

    def html(self):
        return (self.dir / ".jira/board.html").read_text()

    def test_template_has_epic_filter(self):
        self.assertIn('id="fepic"', self.html())

    def test_template_has_search_and_clear_controls(self):
        html = self.html()
        self.assertIn('id="fsearch"', html)
        self.assertIn('id="fclear"', html)

    def test_template_has_theme_toggle_and_bootstrap(self):
        html = self.html()
        self.assertIn('id="ftheme"', html)
        self.assertIn("localStorage.getItem('jt-theme')", html)
        self.assertIn('data-theme="light"', html)  # light palette block exists

    def test_template_has_drawer_and_sticky_toolbar(self):
        html = self.html()
        self.assertIn('class="drawer"', html)
        self.assertIn("IntersectionObserver", html)

    # ---- Task D: a11y / visual / polish markers (static, present on any board) ----
    def test_drawer_is_a_labelled_modal_dialog(self):
        html = self.html()
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)
        self.assertIn('aria-labelledby="d-title"', html)

    def test_focus_visible_ring_present(self):
        self.assertIn(":focus-visible", self.html())

    def test_print_and_reduced_motion_media_queries(self):
        html = self.html()
        self.assertIn("@media print", html)
        self.assertIn("prefers-reduced-motion", html)

    def test_linkify_emits_safe_external_anchors(self):
        # The linkify helper builds target=_blank rel=noopener anchors.
        html = self.html()
        self.assertIn("function linkify", html)
        self.assertIn('rel="noopener"', html)
        self.assertIn('target="_blank"', html)

    def test_theme_sync_listeners_present(self):
        html = self.html()
        self.assertIn("'storage'", html)  # cross-tab theme sync
        self.assertIn("prefers-color-scheme: dark", html)  # OS-change follow

    def test_relative_time_function_present(self):
        html = self.html()
        self.assertIn("function rel(", html)
        self.assertIn("just now", html)
        self.assertIn("w ago", html)

    def test_blocked_by_chip_marker_present(self):
        self.assertIn("blk-chip", self.html())

    def test_no_hardcoded_five_column_grid(self):
        # JT-24: the grid must be data-driven, never a literal repeat(5,...).
        self.assertNotIn("repeat(5", self.html())


class TestTemplateInteraction(BoardTestCase):
    """Task C: event delegation, transitive epics, hash state, sticky stats.

    Black-box: assert on the rendered board.html. Build a board with an
    Epic -> Story -> Sub-task chain plus orphans so the interaction markup is
    actually emitted for cards and rows."""

    def setUp(self):
        super().setUp()
        self.cli("add", "--type", "Epic", "--title", "Billing")            # TST-1
        self.cli("add", "--type", "Story", "--title", "Invoices", "--parent", "TST-1")  # TST-2
        self.cli("add", "--type", "Sub-task", "--title", "PDF", "--parent", "TST-2")    # TST-3 (transitive)
        self.cli("add", "--type", "Task", "--title", "Orphan one")          # TST-4
        self.cli("add", "--type", "Bug", "--title", "Orphan two")           # TST-5

    def html(self):
        return (self.dir / ".jira/board.html").read_text()

    # ---- JT-20: event delegation, no inline handlers ----
    def test_no_inline_onclick_anywhere(self):
        self.assertNotIn("onclick=", self.html(),
                         "all inline onclick handlers must be removed (event delegation)")

    def test_cards_and_rows_carry_data_key(self):
        self.assertIn("data-key", self.html())

    def test_clickable_items_are_keyboard_accessible(self):
        html = self.html()
        self.assertIn('role="button"', html)
        self.assertIn('tabindex="0"', html)
        self.assertIn("aria-label", html)

    def test_delegated_listener_resolves_closest_data_key(self):
        self.assertIn("closest('[data-key]')", self.html())

    def test_keydown_activates_on_enter_or_space(self):
        html = self.html()
        self.assertIn("keydown", html)
        # Space activation must call preventDefault (avoid page scroll)
        self.assertIn("preventDefault", html)

    def test_drawer_parent_link_is_real_anchor_no_inline_js(self):
        html = self.html()
        # Parent link is now an <a href="#KEY" data-key="...">, not an onclick span
        self.assertIn('class="plink" href="#', html)

    def test_keys_never_interpolated_into_js_string_context(self):
        # No `openIssue('...')` style inline calls with an interpolated key remain.
        self.assertNotIn("openIssue('", self.html())

    # ---- JT-23: sticky-toolbar stats strip ----
    def test_mini_stats_marker_present(self):
        html = self.html()
        self.assertIn("mini-stats", html)
        self.assertIn('id="ministats"', html)

    # ---- JT-30: URL-hash state ----
    def test_hash_state_uses_replacestate_and_searchparams(self):
        html = self.html()
        self.assertIn("replaceState", html)
        self.assertIn("URLSearchParams", html)

    def test_hash_state_listens_for_hashchange(self):
        self.assertIn("hashchange", self.html())

    # ---- JT-21/22: semantics helpers shipped in the template ----
    def test_transitive_epic_index_built_per_pass(self):
        html = self.html()
        self.assertIn("buildEpicIndex", html)
        self.assertIn("memberCount", html)

    def test_overall_progress_ignores_filters_title(self):
        self.assertIn("overall progress, ignores filters", self.html())

    # ---- existing invariant must stay green ----
    def test_no_raw_u2028_in_template(self):
        self.assertNotIn(" ", self.html())


class TestDynamicStatusCss(BoardTestCase):
    """JT-24: column count and per-status colours are generated from the board.

    A hand-edited board with a 6th status must get a 6-column-capable grid and a
    distinct colour for the new status (not the grey --muted fallback)."""

    def _write_board(self, board):
        p = self.dir / ".jira/board.json"
        p.write_text(json.dumps(board, indent=2) + "\n")

    def _render(self):
        # Render straight from the hand-edited JSON via `render --file`.
        r = run(["render", "--file", str(self.dir / ".jira/board.json")], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.dir / ".jira/board.html").read_text()

    def _scolor_line(self, html):
        """Extract the STATUS_COLOR=... assignment line injected into the HTML."""
        import re
        m = re.search(r'const STATUS_COLOR=\{[^;]+\};', html)
        self.assertIsNotNone(m, "STATUS_COLOR line not found in rendered HTML")
        return m.group(0)

    def test_six_statuses_get_six_columns_and_distinct_color(self):
        board = json.loads((self.dir / ".jira/board.json").read_text())
        board["statuses"] = ["To Do", "In Progress", "In Review",
                             "Blocked", "Done", "Cancelled"]
        self._write_board(board)
        html = self._render()
        # (a) column count is data-driven to 6 (injected count), never repeat(5,...)
        self.assertNotIn("repeat(5", html)
        self.assertIn("--ncols:6", html)
        # (b) unknown status uses a fallback CSS var (--fb-N), not a raw hex or --muted
        import re
        self.assertRegex(html, r'"Blocked":\s*"var\(--fb-\d\)"')
        self.assertNotIn('"Blocked": "var(--muted)"', html)
        # canonical statuses still ride the theme palette vars
        self.assertIn('"To Do": "var(--todo)"', html)

    def test_six_statuses_color_map_is_deterministic(self):
        """Rendering the same board twice must inject identical STATUS_COLOR maps."""
        board = json.loads((self.dir / ".jira/board.json").read_text())
        board["statuses"] = ["To Do", "In Progress", "In Review",
                             "Blocked", "Done", "Cancelled"]
        self._write_board(board)
        html1 = self._render()
        html2 = self._render()
        self.assertEqual(
            self._scolor_line(html1),
            self._scolor_line(html2),
            "STATUS_COLOR map is non-deterministic across renders",
        )

    def test_grid_is_autofill_capable_no_literal_five(self):
        # Even the default 5-status board must not hardcode repeat(5,...).
        html = (self.dir / ".jira/board.html").read_text()
        self.assertNotIn("repeat(5", html)
        self.assertIn("auto-fill", html)
        self.assertIn("--ncols:5", html)


class TestDrawerLinkifyEscaping(BoardTestCase):
    """JT-33: URLs in a description become anchors, but only AFTER escaping —
    a literal '<' from the original text must never appear un-escaped."""

    def test_url_in_description_becomes_escaped_anchor(self):
        # Description carries a URL plus an HTML-looking fragment.
        self.cli("add", "--type", "Task", "--title", "linky",
                 "--desc", "see https://example.com/a?b=1&c=2 <b>x</b>")
        html = (self.dir / ".jira/board.html").read_text()
        # An external anchor is emitted (the linkify helper output is in the file).
        self.assertIn('rel="noopener"', html)
        # The board-data JSON is script-safe: the literal </b> is escaped to <\/b>,
        # so no raw "</b>" closing tag from the user's text leaks into the document.
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("<\\/b>", html)


# --------------------------------------------------------------------------- #
# CLI hardening — JT-26 / JT-27 / JT-32 / JT-34
# --------------------------------------------------------------------------- #

class TestConcurrency(BoardTestCase):
    """JT-26: 10 parallel adds must all persist (no last-writer-wins loss)."""

    def test_ten_parallel_adds_all_persist(self):
        errors = []

        def do_add(n):
            r = run(["add", "--type", "Task", "--title", f"Task {n}"], self.dir)
            if r.returncode != 0:
                errors.append(r.stderr)

        threads = [threading.Thread(target=do_add, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"some adds failed: {errors}")
        b = self.board()
        self.assertEqual(len(b["issues"]), 10, f"expected 10 issues, got {len(b['issues'])}")
        self.assertEqual(b["project"]["counter"], 10)


class TestU2028Escaping(BoardTestCase):
    """JT-27: U+2028/U+2029 must be escaped in the <script> JSON payload."""

    def test_u2028_in_title_is_escaped_in_html(self):
        title = "line sep"
        self.cli("add", "--type", "Task", "--title", title)
        html = (self.dir / ".jira/board.html").read_text()
        # Find the script block with board data
        self.assertNotIn(" ", html, "raw U+2028 must not appear in board.html")
        self.assertIn("\\u2028", html, "escaped \\u2028 must appear in board.html")

    def test_u2029_in_title_is_escaped_in_html(self):
        title = "para sep"
        self.cli("add", "--type", "Task", "--title", title)
        html = (self.dir / ".jira/board.html").read_text()
        self.assertNotIn(" ", html, "raw U+2029 must not appear in board.html")
        self.assertIn("\\u2029", html, "escaped \\u2029 must appear in board.html")


class TestMoveGuard(BoardTestCase):
    """JT-32a: moving out of a closed status requires --comment."""

    def test_move_done_to_todo_without_comment_fails(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "Done")
        r = self.cli("move", "TST-1", "To Do", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("TST-1", r.stderr)
        self.assertIn("Done", r.stderr)

    def test_move_done_to_todo_with_comment_succeeds(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "Done")
        self.cli("move", "TST-1", "To Do", "--comment", "Reopening because spec changed")
        self.assertEqual(self.issue("TST-1")["status"], "To Do")

    def test_move_cancelled_to_in_progress_without_comment_fails(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "Cancelled")
        r = self.cli("move", "TST-1", "In Progress", ok=False)
        self.assertNotEqual(r.returncode, 0)

    def test_move_open_to_open_no_comment_required(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "In Progress")
        self.cli("move", "TST-1", "In Review")  # open -> open: no comment needed
        self.assertEqual(self.issue("TST-1")["status"], "In Review")


class TestEmptyTitleGuard(BoardTestCase):
    """JT-32b: blank/whitespace titles must be rejected."""

    def test_add_whitespace_title_fails(self):
        r = self.cli("add", "--type", "Task", "--title", "   ", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("title", r.stderr)

    def test_add_empty_title_fails(self):
        r = self.cli("add", "--type", "Task", "--title", "", ok=False)
        self.assertNotEqual(r.returncode, 0)

    def test_set_empty_title_fails(self):
        self.cli("add", "--type", "Task", "--title", "Real")
        r = self.cli("set", "TST-1", "--title", "", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("title", r.stderr)


class TestListParentGuard(BoardTestCase):
    """JT-32c: list --parent with an unknown key must die loudly."""

    def test_list_unknown_parent_fails(self):
        r = self.cli("list", "--parent", "NOPE-1", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not found", r.stderr)

    def test_list_known_parent_works(self):
        self.cli("add", "--type", "Epic", "--title", "E")
        self.cli("add", "--type", "Task", "--title", "T", "--parent", "TST-1")
        r = self.cli("list", "--parent", "TST-1")
        self.assertIn("TST-2", r.stdout)


class TestFilePositionBothWays(unittest.TestCase):
    """JT-32e: --file must be accepted before AND after the subcommand.

    The real bug: argparse parses --file at the top level, then the subparser
    runs with its own default (None) and OVERWRITES the namespace value.
    Result: jira.py --file X <cmd> silently ignores X and uses DEFAULT_FILE.

    These tests are intentionally run from a fresh temp cwd that has NO board,
    so the default path .jira/board.json does not exist.  If --file is
    accidentally ignored the command will fail with "no board found" (or will
    write to the wrong place on init), making the bug detectable.
    """

    def setUp(self):
        # empty_cwd: no .jira/ directory at all
        self._empty_tmp = tempfile.TemporaryDirectory()
        self.empty_cwd = Path(self._empty_tmp.name)
        # target_dir: where the board should actually live
        self._target_tmp = tempfile.TemporaryDirectory()
        self.target_dir = Path(self._target_tmp.name)
        self.board_file = self.target_dir / "board.json"
        self.addCleanup(self._empty_tmp.cleanup)
        self.addCleanup(self._target_tmp.cleanup)

    def _init_board(self):
        """Initialise a board at the target path."""
        r = run(["--file", str(self.board_file), "init", "--name", "Remote", "--key", "REM"],
                self.empty_cwd)
        self.assertEqual(r.returncode, 0, f"init failed: {r.stderr}")

    # ---- --file before subcommand ----

    def test_file_before_subcommand_init_writes_target(self):
        """init writes board.json to the --file path, not to cwd/.jira/board.json."""
        r = run(["--file", str(self.board_file), "init", "--name", "Remote", "--key", "REM"],
                self.empty_cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        # Target file must exist
        self.assertTrue(self.board_file.exists(),
                        f"board.json not created at target {self.board_file}")
        # cwd must have no .jira/ directory
        self.assertFalse((self.empty_cwd / ".jira").exists(),
                         ".jira/ must not be created in cwd when --file points elsewhere")

    def test_file_before_subcommand_list_reads_target(self):
        """list with --file before subcommand reads from the target path."""
        self._init_board()
        r = run(["--file", str(self.board_file), "list"], self.empty_cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        # No .jira/ in cwd
        self.assertFalse((self.empty_cwd / ".jira").exists())

    def test_file_before_subcommand_add_writes_target(self):
        """add with --file before subcommand writes to the target path."""
        self._init_board()
        r = run(["--file", str(self.board_file), "add", "--type", "Task", "--title", "Pre-sub"],
                self.empty_cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        board = json.loads(self.board_file.read_text())
        self.assertEqual(len(board["issues"]), 1)
        self.assertFalse((self.empty_cwd / ".jira").exists())

    # ---- --file after subcommand (must stay working) ----

    def test_file_after_subcommand_list(self):
        self._init_board()
        r = run(["list", "--file", str(self.board_file)], self.empty_cwd)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_file_after_add_subcommand(self):
        self._init_board()
        r = run(["add", "--file", str(self.board_file), "--type", "Task", "--title", "Post-sub"],
                self.empty_cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        board = json.loads(self.board_file.read_text())
        self.assertEqual(len(board["issues"]), 1)


class TestJsonPositionBothWays(BoardTestCase):
    """--json must be honored in BOTH positions (before and after the subcommand).

    Before the fix, --json before the subcommand was silently ignored because
    the subparser default (False) overwrote the True set by the top-level parser.
    """

    def setUp(self):
        super().setUp()
        # Seed one issue so list/next/show have something to output.
        self.cli("add", "--type", "Task", "--title", "Seed issue")

    def _assert_json(self, r, check_key):
        """Assert the output is valid JSON containing check_key."""
        self.assertEqual(r.returncode, 0, r.stderr)
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            self.fail(f"output is not valid JSON:\n{r.stdout!r}")
        self.assertIn(check_key, data)

    # ---- list ----

    def test_json_before_subcommand_list(self):
        r = run(["--json", "list"], self.dir)
        self._assert_json(r, "issues")

    def test_json_after_subcommand_list(self):
        r = run(["list", "--json"], self.dir)
        self._assert_json(r, "issues")

    # ---- status ----

    def test_json_before_subcommand_status(self):
        r = run(["--json", "status"], self.dir)
        self._assert_json(r, "project")

    def test_json_after_subcommand_status(self):
        r = run(["status", "--json"], self.dir)
        self._assert_json(r, "project")

    # ---- show ----

    def test_json_before_subcommand_show(self):
        r = run(["--json", "show", "TST-1"], self.dir)
        self._assert_json(r, "key")

    def test_json_after_subcommand_show(self):
        r = run(["show", "TST-1", "--json"], self.dir)
        self._assert_json(r, "key")

    # ---- next ----

    def test_json_before_subcommand_next(self):
        r = run(["--json", "next"], self.dir)
        self._assert_json(r, "recommendations")

    def test_json_after_subcommand_next(self):
        r = run(["next", "--json"], self.dir)
        self._assert_json(r, "recommendations")


class TestTemplateVersion(BoardTestCase):
    """JT-34: template_version stamping and forward-compat guard."""

    def test_save_stamps_template_version(self):
        self.cli("add", "--type", "Task", "--title", "T")
        b = self.board()
        self.assertIn("template_version", b, "template_version must be stamped on save")
        self.assertIsInstance(b["template_version"], int)

    def test_newer_board_version_dies(self):
        p = self.dir / ".jira/board.json"
        b = json.loads(p.read_text())
        b["template_version"] = 99
        p.write_text(json.dumps(b, indent=2) + "\n")
        r = self.cli("list", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("newer", r.stderr)

    def test_html_has_template_version_comment(self):
        html = (self.dir / ".jira/board.html").read_text()
        self.assertIn("<!-- jira-tracker template v", html)

    def test_non_int_template_version_dies(self):
        """A string or null template_version must exit nonzero without a traceback."""
        p = self.dir / ".jira/board.json"
        for bad_value in ("v2", None):
            with self.subTest(value=bad_value):
                b = json.loads(p.read_text())
                b["template_version"] = bad_value
                p.write_text(json.dumps(b, indent=2) + "\n")
                r = run(["list"], self.dir)
                self.assertNotEqual(r.returncode, 0,
                                    f"expected nonzero exit for template_version={bad_value!r}")
                self.assertNotIn("Traceback", r.stderr,
                                 f"traceback must not appear for template_version={bad_value!r}")
                self.assertIn("error:", r.stderr)


class TestLockFileInGitignore(unittest.TestCase):
    """JT-26: board.lock must be gitignored."""

    def test_board_lock_in_gitignore(self):
        gitignore = (REPO / ".gitignore").read_text()
        self.assertIn("board.lock", gitignore)


class TestFileModes(BoardTestCase):
    """write_atomic must produce 0644 files, not 0600 (mkstemp default)."""

    @unittest.skipIf(os.name != "posix", "POSIX-only file permission test")
    def test_board_json_mode_is_0644_after_add(self):
        self.cli("add", "--type", "Task", "--title", "Permissions check")
        p = self.dir / ".jira/board.json"
        mode = stat.S_IMODE(os.stat(p).st_mode)
        self.assertEqual(mode, 0o644,
                         f"board.json mode is {oct(mode)}, expected 0o644")

    @unittest.skipIf(os.name != "posix", "POSIX-only file permission test")
    def test_board_html_mode_is_0644_after_add(self):
        self.cli("add", "--type", "Task", "--title", "Permissions check HTML")
        p = self.dir / ".jira/board.html"
        mode = stat.S_IMODE(os.stat(p).st_mode)
        self.assertEqual(mode, 0o644,
                         f"board.html mode is {oct(mode)}, expected 0o644")


# --------------------------------------------------------------------------- #
# JT-36 — --json output mode
# --------------------------------------------------------------------------- #

class TestJsonOutput(BoardTestCase):
    """--json flag emits compact JSON with the specified contract shapes."""

    def setUp(self):
        super().setUp()
        self.cli("add", "--type", "Epic", "--title", "Big Epic")
        self.cli("add", "--type", "Task", "--title", "Alpha", "--parent", "TST-1")
        self.cli("add", "--type", "Bug", "--title", "Beta", "--status", "Done")

    def test_json_list_shape(self):
        r = self.cli("list", "--json", "--all")
        data = json.loads(r.stdout)
        self.assertIn("issues", data)
        self.assertIsInstance(data["issues"], list)
        keys = {i["key"] for i in data["issues"]}
        self.assertIn("TST-1", keys)

    def test_json_list_no_human_noise(self):
        r = self.cli("list", "--json")
        # No header line, just JSON
        json.loads(r.stdout)  # must not raise

    def test_json_show_shape(self):
        r = self.cli("show", "TST-2", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["key"], "TST-2")
        self.assertIn("title", data)
        self.assertIn("status", data)

    def test_json_status_shape(self):
        r = self.cli("status", "--json")
        data = json.loads(r.stdout)
        self.assertIn("project", data)
        self.assertIn("counts", data)
        self.assertIn("total", data)
        self.assertIn("in_progress", data)
        self.assertIn("stale", data)
        self.assertEqual(data["total"], 3)

    def test_json_next_shape(self):
        r = self.cli("next", "--json")
        data = json.loads(r.stdout)
        self.assertIn("recommendations", data)
        self.assertIn("blocked", data)
        self.assertIsInstance(data["recommendations"], list)
        self.assertIsInstance(data["blocked"], list)


# --------------------------------------------------------------------------- #
# JT-37 — doctor command
# --------------------------------------------------------------------------- #

class TestDoctor(BoardTestCase):
    """doctor scans for integrity problems."""

    def _write_board(self, board):
        p = self.dir / ".jira/board.json"
        p.write_text(json.dumps(board, indent=2) + "\n")

    def _fresh_board(self):
        return json.loads((self.dir / ".jira/board.json").read_text())

    def test_clean_board_exits_zero(self):
        r = self.cli("doctor")
        self.assertEqual(r.returncode, 0)
        self.assertIn("healthy", r.stdout)

    def test_clean_board_json_mode(self):
        r = self.cli("doctor", "--json")
        data = json.loads(r.stdout)
        self.assertIn("problems", data)
        self.assertEqual(data["problems"], [])

    def test_seeded_problems_detected(self):
        """Seed: dangling parent, bad status, counter drift, blocked_by cycle."""
        board = self._fresh_board()
        # Issue with dangling parent
        board["issues"].append({
            "key": "TST-10", "type": "Task", "title": "Dangling",
            "status": "To Do", "priority": "Medium",
            "parent": "TST-999",  # dangling
            "labels": [], "components": [], "assignee": "",
            "created": "2024-01-01T00:00:00+00:00",
            "updated": "2024-01-01T00:00:00+00:00",
            "comments": [],
        })
        # Issue with bad status
        board["issues"].append({
            "key": "TST-11", "type": "Task", "title": "Bad status",
            "status": "Limbo",  # invalid
            "priority": "Medium",
            "parent": None,
            "labels": [], "components": [], "assignee": "",
            "created": "2024-01-01T00:00:00+00:00",
            "updated": "2024-01-01T00:00:00+00:00",
            "comments": [],
        })
        # Two issues forming a blocked_by cycle: TST-12 <-> TST-13
        board["issues"].append({
            "key": "TST-12", "type": "Task", "title": "Cycle A",
            "status": "To Do", "priority": "Medium",
            "parent": None, "blocked_by": ["TST-13"],
            "labels": [], "components": [], "assignee": "",
            "created": "2024-01-01T00:00:00+00:00",
            "updated": "2024-01-01T00:00:00+00:00",
            "comments": [],
        })
        board["issues"].append({
            "key": "TST-13", "type": "Task", "title": "Cycle B",
            "status": "To Do", "priority": "Medium",
            "parent": None, "blocked_by": ["TST-12"],
            "labels": [], "components": [], "assignee": "",
            "created": "2024-01-01T00:00:00+00:00",
            "updated": "2024-01-01T00:00:00+00:00",
            "comments": [],
        })
        # Counter drift: counter is 0 but max suffix is 13
        board["project"]["counter"] = 0
        self._write_board(board)

        r = run(["doctor"], self.dir)
        self.assertEqual(r.returncode, 1)
        output = r.stdout

        # All four problem categories present
        self.assertIn("dangling_parent", output)
        self.assertIn("invalid_status", output)
        self.assertIn("blocked_by_cycle", output)
        self.assertIn("counter_drift", output)

    def test_seeded_problems_json_mode(self):
        """Same seed as above but with --json: all codes present in JSON."""
        board = self._fresh_board()
        board["issues"].append({
            "key": "TST-10", "type": "Task", "title": "Dangling",
            "status": "To Do", "priority": "Medium",
            "parent": "TST-999",
            "labels": [], "components": [], "assignee": "",
            "created": "2024-01-01T00:00:00+00:00",
            "updated": "2024-01-01T00:00:00+00:00",
            "comments": [],
        })
        board["issues"].append({
            "key": "TST-11", "type": "Task", "title": "Bad status",
            "status": "Limbo",
            "priority": "Medium", "parent": None,
            "labels": [], "components": [], "assignee": "",
            "created": "2024-01-01T00:00:00+00:00",
            "updated": "2024-01-01T00:00:00+00:00",
            "comments": [],
        })
        board["issues"].append({
            "key": "TST-12", "type": "Task", "title": "Cycle A",
            "status": "To Do", "priority": "Medium",
            "parent": None, "blocked_by": ["TST-13"],
            "labels": [], "components": [], "assignee": "",
            "created": "2024-01-01T00:00:00+00:00",
            "updated": "2024-01-01T00:00:00+00:00",
            "comments": [],
        })
        board["issues"].append({
            "key": "TST-13", "type": "Task", "title": "Cycle B",
            "status": "To Do", "priority": "Medium",
            "parent": None, "blocked_by": ["TST-12"],
            "labels": [], "components": [], "assignee": "",
            "created": "2024-01-01T00:00:00+00:00",
            "updated": "2024-01-01T00:00:00+00:00",
            "comments": [],
        })
        board["project"]["counter"] = 0
        self._write_board(board)

        r = run(["doctor", "--json"], self.dir)
        self.assertEqual(r.returncode, 1)
        data = json.loads(r.stdout)
        codes = {p["code"] for p in data["problems"]}
        self.assertIn("dangling_parent", codes)
        self.assertIn("invalid_status", codes)
        self.assertIn("blocked_by_cycle", codes)
        self.assertIn("counter_drift", codes)


# --------------------------------------------------------------------------- #
# JT-38 — link command (blocked-by)
# --------------------------------------------------------------------------- #

class TestLink(BoardTestCase):
    """link command: add/unblock round-trips, self-block, unknown key, cycles."""

    def setUp(self):
        super().setUp()
        self.cli("add", "--type", "Task", "--title", "Alpha")   # TST-1
        self.cli("add", "--type", "Task", "--title", "Beta")    # TST-2
        self.cli("add", "--type", "Task", "--title", "Gamma")   # TST-3

    def test_link_add_blocked_by(self):
        self.cli("link", "TST-2", "--blocked-by", "TST-1")
        i = self.issue("TST-2")
        self.assertIn("TST-1", i.get("blocked_by", []))

    def test_link_unblock_round_trip(self):
        self.cli("link", "TST-2", "--blocked-by", "TST-1")
        self.cli("link", "TST-2", "--unblock", "TST-1")
        i = self.issue("TST-2")
        self.assertNotIn("TST-1", i.get("blocked_by", []))

    def test_link_unblock_when_not_present_fails(self):
        r = self.cli("link", "TST-2", "--unblock", "TST-1", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not in", r.stderr)

    def test_link_self_block_fails(self):
        r = self.cli("link", "TST-1", "--blocked-by", "TST-1", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("itself", r.stderr)

    def test_link_unknown_key_fails(self):
        r = self.cli("link", "TST-999", "--blocked-by", "TST-1", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not found", r.stderr)

    def test_link_unknown_blocker_fails(self):
        r = self.cli("link", "TST-1", "--blocked-by", "TST-999", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not found", r.stderr)

    def test_link_cycle_a_blocked_by_b_then_b_blocked_by_a_fails(self):
        self.cli("link", "TST-1", "--blocked-by", "TST-2")
        r = self.cli("link", "TST-2", "--blocked-by", "TST-1", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cycle", r.stderr)

    def test_link_blocked_by_sorted_and_unique(self):
        self.cli("link", "TST-3", "--blocked-by", "TST-2")
        self.cli("link", "TST-3", "--blocked-by", "TST-1")
        self.cli("link", "TST-3", "--blocked-by", "TST-2")  # duplicate → no-op
        i = self.issue("TST-3")
        self.assertEqual(i["blocked_by"], ["TST-1", "TST-2"])

    def test_show_displays_blocked_by_and_blocks(self):
        self.cli("link", "TST-2", "--blocked-by", "TST-1")
        r = self.cli("show", "TST-2")
        self.assertIn("blocked by", r.stdout)
        self.assertIn("TST-1", r.stdout)
        r2 = self.cli("show", "TST-1")
        self.assertIn("blocks", r2.stdout)
        self.assertIn("TST-2", r2.stdout)


# --------------------------------------------------------------------------- #
# JT-38 — next integration with blocked issues
# --------------------------------------------------------------------------- #

class TestNextWithBlocked(BoardTestCase):
    """next excludes issues with open blockers and lists them separately."""

    def setUp(self):
        super().setUp()
        self.cli("add", "--type", "Task", "--title", "Blocker")   # TST-1
        self.cli("add", "--type", "Task", "--title", "Blocked")   # TST-2
        self.cli("link", "TST-2", "--blocked-by", "TST-1")

    def test_blocked_issue_not_in_recommendations(self):
        r = self.cli("next")
        self.assertNotIn("TST-2", r.stdout.split("blocked:")[0] if "blocked:" in r.stdout else r.stdout)

    def test_blocked_section_shows_blocked_issue(self):
        r = self.cli("next")
        # After the "blocked:" header, TST-2 should appear
        self.assertIn("blocked", r.stdout.lower())

    def test_blocker_done_restores_issue_to_recommendations(self):
        self.cli("move", "TST-1", "Done")
        r = self.cli("next")
        self.assertIn("TST-2", r.stdout)

    def test_json_next_blocked_section(self):
        r = self.cli("next", "--json")
        data = json.loads(r.stdout)
        rec_keys = [i["key"] for i in data["recommendations"]]
        blocked_keys = [b["key"] for b in data["blocked"]]
        self.assertNotIn("TST-2", rec_keys)
        self.assertIn("TST-2", blocked_keys)
        # The blocked entry must list its open blockers
        entry = next(b for b in data["blocked"] if b["key"] == "TST-2")
        self.assertIn("TST-1", entry["blocked_by_open"])

    def test_json_next_blocker_done_restores(self):
        self.cli("move", "TST-1", "Done")
        r = self.cli("next", "--json")
        data = json.loads(r.stdout)
        rec_keys = [i["key"] for i in data["recommendations"]]
        blocked_keys = [b["key"] for b in data["blocked"]]
        self.assertIn("TST-2", rec_keys)
        self.assertNotIn("TST-2", blocked_keys)


# --------------------------------------------------------------------------- #
# JT-40 — stale-WIP detection
# --------------------------------------------------------------------------- #

class TestStaleWIP(BoardTestCase):
    """Stale In Progress issues are annotated with ⚠ stale Nd."""

    def _backdate(self, key, days=10):
        """Edit the board.json directly to backdate an issue's updated field."""
        p = self.dir / ".jira/board.json"
        board = json.loads(p.read_text())
        stale_dt = (__import__("datetime").datetime.now().astimezone()
                    - __import__("datetime").timedelta(days=days))
        for i in board["issues"]:
            if i["key"] == key:
                i["updated"] = stale_dt.replace(microsecond=0).isoformat()
                break
        p.write_text(json.dumps(board, indent=2) + "\n")

    def setUp(self):
        super().setUp()
        self.cli("add", "--type", "Task", "--title", "Fresh work")    # TST-1
        self.cli("add", "--type", "Task", "--title", "Old work")      # TST-2
        self.cli("move", "TST-1", "In Progress")
        self.cli("move", "TST-2", "In Progress")
        self._backdate("TST-2", days=10)

    def test_status_shows_stale_annotation(self):
        r = self.cli("status")
        self.assertIn("stale", r.stdout)
        self.assertIn("TST-2", r.stdout)

    def test_status_fresh_issue_no_stale_annotation(self):
        r = self.cli("status")
        # TST-1 line should not have "stale"
        lines = r.stdout.splitlines()
        tst1_lines = [l for l in lines if "TST-1" in l]
        for l in tst1_lines:
            self.assertNotIn("stale", l)

    def test_next_shows_stale_annotation(self):
        r = self.cli("next")
        self.assertIn("stale", r.stdout)

    def test_next_fresh_issue_no_stale_annotation(self):
        r = self.cli("next")
        lines = r.stdout.splitlines()
        tst1_lines = [l for l in lines if "TST-1" in l]
        for l in tst1_lines:
            self.assertNotIn("stale", l)

    def test_json_status_stale_list(self):
        r = self.cli("status", "--json")
        data = json.loads(r.stdout)
        self.assertIn("TST-2", data["stale"])
        self.assertNotIn("TST-1", data["stale"])

    def test_stale_detection_with_naive_timestamp(self):
        """Fix #2: a tz-naive updated stamp 10 days old must still trigger stale."""
        import datetime
        p = self.dir / ".jira/board.json"
        board = json.loads(p.read_text())
        # Write a naive ISO timestamp (no UTC offset) 10 days in the past
        naive_old = (datetime.datetime.now() - datetime.timedelta(days=10))
        naive_str = naive_old.replace(microsecond=0).isoformat()  # no +HH:MM suffix
        for i in board["issues"]:
            if i["key"] == "TST-2":
                i["updated"] = naive_str
                break
        p.write_text(json.dumps(board, indent=2) + "\n")
        r = self.cli("status")
        self.assertIn("stale", r.stdout,
                      "naive-tz 10-day-old updated should still be flagged as stale")
        self.assertIn("TST-2", r.stdout)


# --------------------------------------------------------------------------- #
# Fix #1/Fix #3 — doctor: full code coverage + cycle attribution correctness
# --------------------------------------------------------------------------- #

class TestDoctorFullCoverage(BoardTestCase):
    """Every doctor diagnostic code fires at least once; cycle attribution is exact."""

    def _write_board(self, board):
        p = self.dir / ".jira/board.json"
        p.write_text(json.dumps(board, indent=2) + "\n")

    def _fresh_board(self):
        return json.loads((self.dir / ".jira/board.json").read_text())

    def _run_doctor_json(self, board):
        self._write_board(board)
        r = run(["doctor", "--json"], self.dir)
        data = json.loads(r.stdout)
        return {p["code"] for p in data["problems"]}, {p["key"] for p in data["problems"]}

    def _base_issue(self, key, **kwargs):
        base = {
            "key": key, "type": "Task", "title": f"Issue {key}",
            "status": "To Do", "priority": "Medium",
            "parent": None, "labels": [], "components": [], "assignee": "",
            "created": "2024-01-01T00:00:00+00:00",
            "updated": "2024-01-01T00:00:00+00:00",
            "comments": [],
        }
        base.update(kwargs)
        return base

    def test_missing_key_detected(self):
        board = self._fresh_board()
        issue = self._base_issue("TST-10")
        del issue["key"]
        board["issues"].append(issue)
        codes, _ = self._run_doctor_json(board)
        self.assertIn("missing_key", codes)

    def test_missing_field_detected(self):
        board = self._fresh_board()
        issue = self._base_issue("TST-10")
        del issue["title"]
        board["issues"].append(issue)
        codes, _ = self._run_doctor_json(board)
        self.assertIn("missing_field", codes)

    def test_invalid_type_detected(self):
        board = self._fresh_board()
        board["issues"].append(self._base_issue("TST-10", type="NotAType"))
        codes, _ = self._run_doctor_json(board)
        self.assertIn("invalid_type", codes)

    def test_invalid_priority_detected(self):
        board = self._fresh_board()
        board["issues"].append(self._base_issue("TST-10", priority="Urgent"))
        codes, _ = self._run_doctor_json(board)
        self.assertIn("invalid_priority", codes)

    def test_dangling_blocked_by_detected(self):
        board = self._fresh_board()
        board["issues"].append(
            self._base_issue("TST-10", blocked_by=["TST-999"])
        )
        codes, _ = self._run_doctor_json(board)
        self.assertIn("dangling_blocked_by", codes)

    def test_parent_cycle_detected(self):
        """A ↔ B parent cycle: both members must be flagged."""
        board = self._fresh_board()
        # We inject a self-referential parent chain directly (bypassing CLI guards)
        board["issues"].append(self._base_issue("TST-10", parent="TST-11"))
        board["issues"].append(self._base_issue("TST-11", parent="TST-10"))
        codes, keys = self._run_doctor_json(board)
        self.assertIn("parent_cycle", codes)
        # Both members of the cycle should be flagged
        self.assertIn("TST-10", keys)
        self.assertIn("TST-11", keys)

    def test_duplicate_key_detected(self):
        board = self._fresh_board()
        board["issues"].append(self._base_issue("TST-10"))
        board["issues"].append(self._base_issue("TST-10"))  # duplicate
        codes, _ = self._run_doctor_json(board)
        self.assertIn("duplicate_key", codes)

    def test_bad_timestamp_detected(self):
        board = self._fresh_board()
        board["issues"].append(
            self._base_issue("TST-10", updated="not-a-date")
        )
        codes, _ = self._run_doctor_json(board)
        self.assertIn("bad_timestamp", codes)

    def test_node_pointing_into_cycle_not_flagged(self):
        """Fix #1: TST-3 → TST-1 where TST-1 ↔ TST-2: TST-3 must NOT be flagged."""
        board = self._fresh_board()
        # TST-1 and TST-2 form a blocked_by cycle
        board["issues"].append(
            self._base_issue("TST-10", blocked_by=["TST-11"])
        )
        board["issues"].append(
            self._base_issue("TST-11", blocked_by=["TST-10"])
        )
        # TST-12 merely points into the cycle (not a member)
        board["issues"].append(
            self._base_issue("TST-12", blocked_by=["TST-10"])
        )
        self._write_board(board)
        r = run(["doctor", "--json"], self.dir)
        data = json.loads(r.stdout)
        cycle_probs = [p for p in data["problems"] if p["code"] == "blocked_by_cycle"]
        flagged_keys = {p["key"] for p in cycle_probs}
        self.assertIn("TST-10", flagged_keys, "TST-10 is in the cycle and must be flagged")
        self.assertIn("TST-11", flagged_keys, "TST-11 is in the cycle and must be flagged")
        self.assertNotIn("TST-12", flagged_keys,
                         "TST-12 only points INTO the cycle; it must NOT be flagged")


# --------------------------------------------------------------------------- #
# Fix #3 extras — next --limit with blocked, link --unblock last element
# --------------------------------------------------------------------------- #

class TestNextLimitWithBlocked(BoardTestCase):
    """next --limit only caps recommendations; blocked section is unaffected."""

    def setUp(self):
        super().setUp()
        # 3 normal tasks + 1 blocker + 1 blocked task
        self.cli("add", "--type", "Task", "--title", "Task A")  # TST-1
        self.cli("add", "--type", "Task", "--title", "Task B")  # TST-2
        self.cli("add", "--type", "Task", "--title", "Task C")  # TST-3
        self.cli("add", "--type", "Task", "--title", "Blocker") # TST-4
        self.cli("add", "--type", "Task", "--title", "Blocked") # TST-5
        self.cli("link", "TST-5", "--blocked-by", "TST-4")

    def test_limit_caps_recommendations_not_blocked(self):
        r = self.cli("next", "--limit", "1")
        # Only 1 recommendation
        recs_part = r.stdout.split("blocked:")[0] if "blocked:" in r.stdout else r.stdout
        rec_count = sum(1 for line in recs_part.splitlines() if line.strip().startswith("1.") or
                        (line.strip() and line.strip()[0].isdigit() and ". TST-" in line))
        # The blocked section must still appear
        self.assertIn("blocked", r.stdout.lower())
        self.assertIn("TST-5", r.stdout)

    def test_limit_json_blocked_section_always_complete(self):
        r = self.cli("next", "--limit", "1", "--json")
        data = json.loads(r.stdout)
        self.assertLessEqual(len(data["recommendations"]), 1)
        blocked_keys = [b["key"] for b in data["blocked"]]
        self.assertIn("TST-5", blocked_keys)


class TestLinkUnblockLastElement(BoardTestCase):
    """link --unblock removing the only blocked_by entry leaves an empty list."""

    def setUp(self):
        super().setUp()
        self.cli("add", "--type", "Task", "--title", "Alpha")  # TST-1
        self.cli("add", "--type", "Task", "--title", "Beta")   # TST-2
        self.cli("link", "TST-2", "--blocked-by", "TST-1")

    def test_unblock_last_element_leaves_empty_list(self):
        self.cli("link", "TST-2", "--unblock", "TST-1")
        i = self.issue("TST-2")
        # blocked_by should be present but empty (or absent)
        blocked = i.get("blocked_by", [])
        self.assertEqual(blocked, [], f"Expected empty blocked_by, got {blocked!r}")

    def test_unblock_last_element_issue_becomes_actionable(self):
        """After unblocking, the issue must appear in next recommendations."""
        self.cli("link", "TST-2", "--unblock", "TST-1")
        r = self.cli("next", "--json")
        data = json.loads(r.stdout)
        rec_keys = [i["key"] for i in data["recommendations"]]
        blocked_keys = [b["key"] for b in data["blocked"]]
        self.assertIn("TST-2", rec_keys)
        self.assertNotIn("TST-2", blocked_keys)


# --------------------------------------------------------------------------- #
# JT-17 — In Review surfacing in next/status + set-project command
# --------------------------------------------------------------------------- #

class TestInReviewSurfacing(BoardTestCase):
    """JT-17: next and status call out In Review issues as awaiting human review."""

    def setUp(self):
        super().setUp()
        self.cli("add", "--type", "Task", "--title", "Reviewed work")   # TST-1
        self.cli("move", "TST-1", "review")

    def test_next_text_lists_in_review_section(self):
        r = self.cli("next")
        self.assertIn("in review (awaiting human review):", r.stdout)
        self.assertIn("TST-1", r.stdout)
        # with nothing else actionable, the existing message is preserved
        self.assertIn("nothing actionable", r.stdout)

    def test_next_json_has_in_review_keys(self):
        data = json.loads(self.cli("next", "--json").stdout)
        self.assertEqual(data["in_review"], ["TST-1"])
        self.assertEqual(data["recommendations"], [])

    def test_next_shows_recommendations_and_in_review_together(self):
        self.cli("add", "--type", "Task", "--title", "Live work")        # TST-2
        r = self.cli("next")
        self.assertIn("recommended next:", r.stdout)
        self.assertIn("TST-2", r.stdout)
        self.assertIn("in review (awaiting human review):", r.stdout)
        self.assertNotIn("nothing actionable", r.stdout)

    def test_status_text_lists_in_review_section(self):
        r = self.cli("status")
        self.assertIn("in review (awaiting human review):", r.stdout)
        self.assertIn("TST-1", r.stdout)

    def test_status_json_has_in_review_keys(self):
        data = json.loads(self.cli("status", "--json").stdout)
        self.assertEqual(data["in_review"], ["TST-1"])


class TestSetProject(BoardTestCase):
    """JT-17: edit project fields (name, repo) after init via set-project."""

    def test_updates_name_and_repo(self):
        r = self.cli("set-project", "--name", "Renamed", "--repo", "github.com/me/renamed")
        self.assertIn("updated", r.stdout)
        p = self.board()["project"]
        self.assertEqual(p["name"], "Renamed")
        self.assertEqual(p["repo"], "github.com/me/renamed")
        # key is intentionally not editable — issue keys derive from it
        self.assertEqual(p["key"], "TST")

    def test_clears_repo_with_empty_string(self):
        self.cli("set-project", "--repo", "github.com/me/x")
        self.cli("set-project", "--repo", "")
        self.assertEqual(self.board()["project"]["repo"], "")

    def test_rejects_blank_name(self):
        r = self.cli("set-project", "--name", "   ", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.board()["project"]["name"], "Test")

    def test_requires_at_least_one_flag(self):
        r = self.cli("set-project", ok=False)
        self.assertNotEqual(r.returncode, 0)

    def test_rerenders_html_with_new_name(self):
        self.cli("set-project", "--name", "Renamed Board")
        self.assertIn("Renamed Board", (self.dir / ".jira/board.html").read_text())


# --------------------------------------------------------------------------- #
# JT-25/JT-45 — artifact-sync: every committed json→html pair must match template
# --------------------------------------------------------------------------- #

class TestArtifactSync(unittest.TestCase):
    """JT-25/JT-45: each committed (board json → board.html) pair must be
    byte-identical to a fresh render of its JSON.  A stale artifact fails the
    suite — examples/ and the repo's own .jira/ board alike."""

    PAIRS = [
        (REPO / "examples/sample-board.json", REPO / "examples/board.html"),
        (REPO / ".jira/board.json", REPO / ".jira/board.html"),
    ]

    def test_committed_board_html_matches_rendered_template(self):
        """Render each pair's JSON in a temp dir and assert byte-identity with
        its committed HTML.  Also asserts render does not modify the JSON
        (read-only contract)."""
        import shutil

        for src_json, committed_html in self.PAIRS:
            with self.subTest(pair=str(src_json.parent.relative_to(REPO))):
                orig_json_bytes = src_json.read_bytes()
                committed_html_bytes = committed_html.read_bytes()

                with tempfile.TemporaryDirectory() as tmp:
                    tmp_dir = Path(tmp)
                    # render writes board.html NEXT TO the board file
                    tmp_json = tmp_dir / src_json.name
                    shutil.copy2(src_json, tmp_json)

                    r = run(["render", "--file", str(tmp_json)], tmp_dir)
                    self.assertEqual(r.returncode, 0,
                                     f"render failed: {r.stderr}")

                    # Verify render did NOT mutate the JSON input
                    after_json_bytes = tmp_json.read_bytes()
                    self.assertEqual(
                        orig_json_bytes, after_json_bytes,
                        f"render mutated {src_json.name} (must be read-only on the JSON)",
                    )

                    # Verify rendered HTML is byte-identical to the committed artifact
                    tmp_html = tmp_dir / "board.html"
                    self.assertTrue(tmp_html.exists(),
                                    "render did not produce board.html next to the board file")
                    rendered_bytes = tmp_html.read_bytes()
                    self.assertEqual(
                        rendered_bytes, committed_html_bytes,
                        f"{committed_html.relative_to(REPO)} is stale — re-run: "
                        f"render --file {src_json.relative_to(REPO)} and commit the result",
                    )


# --------------------------------------------------------------------------- #
# FEATURE 1: Activity history
# --------------------------------------------------------------------------- #

class TestHistory(BoardTestCase):
    """Append-only status-transition history per issue."""

    def test_add_initializes_history_with_creation_entry(self):
        self.cli("add", "--type", "Task", "--title", "T")
        i = self.issue("TST-1")
        self.assertIn("history", i)
        self.assertEqual(len(i["history"]), 1)
        entry = i["history"][0]
        self.assertEqual(entry["from"], "")
        self.assertEqual(entry["to"], "To Do")
        self.assertEqual(entry["at"], i["created"])

    def test_add_with_explicit_status_records_that_status(self):
        self.cli("add", "--type", "Task", "--title", "T", "--status", "In Progress")
        i = self.issue("TST-1")
        self.assertEqual(i["history"][0], {"at": i["created"], "from": "", "to": "In Progress"})

    def test_move_appends_transition(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "In Progress")
        self.cli("move", "TST-1", "Done")
        hist = self.issue("TST-1")["history"]
        self.assertEqual(len(hist), 3)
        self.assertEqual((hist[1]["from"], hist[1]["to"]), ("To Do", "In Progress"))
        self.assertEqual((hist[2]["from"], hist[2]["to"]), ("In Progress", "Done"))

    def test_move_noop_does_not_append_duplicate(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "In Progress")
        before = self.issue("TST-1")["history"]
        self.cli("move", "TST-1", "In Progress")  # no-op
        after = self.issue("TST-1")["history"]
        self.assertEqual(len(before), len(after))

    def test_show_displays_history_section(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "In Progress")
        r = self.cli("show", "TST-1")
        self.assertIn("History", r.stdout)
        self.assertIn("To Do", r.stdout)
        self.assertIn("In Progress", r.stdout)

    def test_backward_compat_board_without_history(self):
        """An older board whose issues lack a 'history' key must not crash and
        must not be retro-filled on load."""
        board = self.board()
        board["issues"].append({
            "key": "TST-1", "type": "Task", "title": "Legacy", "description": "",
            "status": "In Progress", "priority": "Medium", "parent": None,
            "labels": [], "components": [], "assignee": "",
            "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00",
            "comments": [],  # NOTE: no "history" key
        })
        board["project"]["counter"] = 1
        (self.dir / ".jira/board.json").write_text(json.dumps(board))
        # show, doctor, status, list must all work
        r = self.cli("show", "TST-1")
        self.assertIn("TST-1", r.stdout)
        self.cli("doctor")  # healthy or at least not a crash; exit code asserted by ok=True
        self.cli("status")
        self.cli("list", "--all")
        # not retro-filled on load (show is read-only)
        self.assertNotIn("history", self.issue("TST-1"))

    def test_move_on_legacy_issue_creates_history_from_old_status(self):
        board = self.board()
        board["issues"].append({
            "key": "TST-1", "type": "Task", "title": "Legacy", "description": "",
            "status": "To Do", "priority": "Medium", "parent": None,
            "labels": [], "components": [], "assignee": "",
            "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00",
            "comments": [],
        })
        board["project"]["counter"] = 1
        (self.dir / ".jira/board.json").write_text(json.dumps(board))
        self.cli("move", "TST-1", "In Progress")
        hist = self.issue("TST-1")["history"]
        self.assertEqual(len(hist), 1)
        self.assertEqual((hist[0]["from"], hist[0]["to"]), ("To Do", "In Progress"))


# --------------------------------------------------------------------------- #
# FEATURE 2: Bulk move / set
# --------------------------------------------------------------------------- #

class TestBulkMove(BoardTestCase):

    def test_single_key_move_still_works(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("move", "TST-1", "Done")
        self.assertEqual(self.issue("TST-1")["status"], "Done")

    def test_bulk_move_multiple_keys(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Task", "--title", "B")
        self.cli("add", "--type", "Task", "--title", "C")
        r = self.cli("move", "TST-1", "TST-2", "TST-3", "Done")
        for k in ("TST-1", "TST-2", "TST-3"):
            self.assertEqual(self.issue(k)["status"], "Done")
            self.assertIn(k, r.stdout)

    def test_bulk_move_invalid_key_aborts_all(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Task", "--title", "B")
        r = self.cli("move", "TST-1", "TST-99", "Done", ok=False)
        self.assertNotEqual(r.returncode, 0)
        # nothing changed
        self.assertEqual(self.issue("TST-1")["status"], "To Do")
        self.assertEqual(self.issue("TST-2")["status"], "To Do")

    def test_bulk_move_invalid_status_aborts_all(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Task", "--title", "B")
        r = self.cli("move", "TST-1", "TST-2", "bogusstatus", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.issue("TST-1")["status"], "To Do")
        self.assertEqual(self.issue("TST-2")["status"], "To Do")

    def test_bulk_move_records_history_per_issue(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Task", "--title", "B")
        self.cli("move", "TST-1", "TST-2", "In Progress")
        for k in ("TST-1", "TST-2"):
            hist = self.issue(k)["history"]
            self.assertEqual(hist[-1]["to"], "In Progress")

    def test_bulk_move_reopen_guard_applies_to_each(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Task", "--title", "B")
        self.cli("move", "TST-1", "Done")
        # reopening TST-1 without comment must fail and change nothing
        r = self.cli("move", "TST-1", "TST-2", "To Do", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.issue("TST-1")["status"], "Done")
        self.assertEqual(self.issue("TST-2")["status"], "To Do")


class TestBulkSet(BoardTestCase):

    def test_single_key_set_still_works(self):
        self.cli("add", "--type", "Task", "--title", "T")
        self.cli("set", "TST-1", "--priority", "High")
        self.assertEqual(self.issue("TST-1")["priority"], "High")

    def test_bulk_set_multiple_keys(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Task", "--title", "B")
        r = self.cli("set", "TST-1", "TST-2", "--priority", "High")
        for k in ("TST-1", "TST-2"):
            self.assertEqual(self.issue(k)["priority"], "High")
            self.assertIn(k, r.stdout)

    def test_bulk_set_invalid_key_aborts_all(self):
        self.cli("add", "--type", "Task", "--title", "A")
        r = self.cli("set", "TST-1", "TST-99", "--priority", "High", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.issue("TST-1")["priority"], "Medium")

    def test_bulk_set_invalid_value_aborts_all(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Task", "--title", "B")
        r = self.cli("set", "TST-1", "TST-2", "--priority", "Bogus", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.issue("TST-1")["priority"], "Medium")
        self.assertEqual(self.issue("TST-2")["priority"], "Medium")

    def test_bulk_set_requires_a_field(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Task", "--title", "B")
        r = self.cli("set", "TST-1", "TST-2", ok=False)
        self.assertNotEqual(r.returncode, 0)


class TestSearch(BoardTestCase):
    """JT: `search` — case-insensitive substring across all issues/fields."""

    def test_matches_title(self):
        self.cli("add", "--type", "Task", "--title", "Fix the widget")
        self.cli("add", "--type", "Task", "--title", "Unrelated")
        r = self.cli("search", "widget")
        self.assertIn("TST-1", r.stdout)
        self.assertNotIn("TST-2", r.stdout)

    def test_matches_description(self):
        self.cli("add", "--type", "Task", "--title", "A", "--desc", "needle in here")
        r = self.cli("search", "needle")
        self.assertIn("TST-1", r.stdout)

    def test_matches_comment_body(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("comment", "TST-1", "this is a special remark")
        r = self.cli("search", "special")
        self.assertIn("TST-1", r.stdout)
        self.assertIn("matched: comment", r.stdout)

    def test_matches_label(self):
        self.cli("add", "--type", "Task", "--title", "A", "--labels", "backend,urgent")
        r = self.cli("search", "urgent")
        self.assertIn("TST-1", r.stdout)

    def test_matches_component(self):
        self.cli("add", "--type", "Task", "--title", "A", "--components", "auth-service")
        r = self.cli("search", "auth-service")
        self.assertIn("TST-1", r.stdout)

    def test_matches_key(self):
        self.cli("add", "--type", "Task", "--title", "A")
        r = self.cli("search", "tst-1")
        self.assertIn("TST-1", r.stdout)

    def test_case_insensitive(self):
        self.cli("add", "--type", "Task", "--title", "Fix the WIDGET")
        r = self.cli("search", "widget")
        self.assertIn("TST-1", r.stdout)

    def test_finds_done_issues(self):
        self.cli("add", "--type", "Task", "--title", "shipped feature")
        self.cli("move", "TST-1", "Done")
        r = self.cli("search", "shipped")
        self.assertIn("TST-1", r.stdout)

    def test_no_match_exits_zero(self):
        self.cli("add", "--type", "Task", "--title", "A")
        r = self.cli("search", "zzzznomatch")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no matches", r.stdout.lower())

    def test_json_shape(self):
        self.cli("add", "--type", "Task", "--title", "Fix the widget")
        r = self.cli("search", "widget", "--json")
        payload = json.loads(r.stdout)
        self.assertIn("issues", payload)
        self.assertEqual([i["key"] for i in payload["issues"]], ["TST-1"])

    def test_json_no_match_is_empty_list(self):
        self.cli("add", "--type", "Task", "--title", "A")
        r = self.cli("search", "zzzznomatch", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout), {"issues": []})


class TestReport(BoardTestCase):
    """JT: `report` — read-only metrics summary."""

    def test_total_and_status_counts(self):
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Bug", "--title", "B")
        self.cli("move", "TST-2", "In Progress")
        r = self.cli("report", "--json")
        m = json.loads(r.stdout)
        self.assertEqual(m["total"], 2)
        self.assertEqual(m["by_status"]["To Do"], 1)
        self.assertEqual(m["by_status"]["In Progress"], 1)

    def test_type_and_priority_counts(self):
        self.cli("add", "--type", "Task", "--title", "A", "--priority", "High")
        self.cli("add", "--type", "Bug", "--title", "B", "--priority", "High")
        r = self.cli("report", "--json")
        m = json.loads(r.stdout)
        self.assertEqual(m["by_type"]["Task"], 1)
        self.assertEqual(m["by_type"]["Bug"], 1)
        self.assertEqual(m["by_priority"]["High"], 2)

    def test_stale_count_uses_existing_logic(self):
        # Fabricate a board with an In Progress issue updated long ago.
        self.cli("add", "--type", "Task", "--title", "stale one")
        self.cli("move", "TST-1", "In Progress")
        b = self.board()
        old = "2000-01-01T00:00:00+00:00"
        for i in b["issues"]:
            if i["key"] == "TST-1":
                i["updated"] = old
        (self.dir / ".jira/board.json").write_text(json.dumps(b))
        r = self.cli("report", "--json")
        m = json.loads(r.stdout)
        self.assertEqual(m["stale"], 1)

    def test_cycle_time_math_on_known_history(self):
        # Two completed issues with known creation->Done spans (2 and 4 days).
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("add", "--type", "Task", "--title", "B")
        b = self.board()
        for i in b["issues"]:
            if i["key"] == "TST-1":
                i["created"] = "2024-01-01T00:00:00+00:00"
                i["history"] = [
                    {"at": "2024-01-01T00:00:00+00:00", "from": "", "to": "To Do"},
                    {"at": "2024-01-03T00:00:00+00:00", "from": "To Do", "to": "Done"},
                ]
                i["status"] = "Done"
            if i["key"] == "TST-2":
                i["created"] = "2024-01-01T00:00:00+00:00"
                i["history"] = [
                    {"at": "2024-01-01T00:00:00+00:00", "from": "", "to": "To Do"},
                    {"at": "2024-01-05T00:00:00+00:00", "from": "To Do", "to": "Done"},
                ]
                i["status"] = "Done"
        (self.dir / ".jira/board.json").write_text(json.dumps(b))
        r = self.cli("report", "--json")
        m = json.loads(r.stdout)
        self.assertEqual(m["cycle_time"]["count"], 2)
        self.assertAlmostEqual(m["cycle_time"]["avg_days"], 3.0)
        self.assertAlmostEqual(m["cycle_time"]["median_days"], 3.0)

    def test_cycle_time_uses_first_transition_into_terminal(self):
        # A reopened issue: first Done at day 2, reopened, Done again at day 10.
        # Cycle time must use the FIRST transition into Done (2 days).
        self.cli("add", "--type", "Task", "--title", "A")
        b = self.board()
        for i in b["issues"]:
            if i["key"] == "TST-1":
                i["created"] = "2024-01-01T00:00:00+00:00"
                i["history"] = [
                    {"at": "2024-01-01T00:00:00+00:00", "from": "", "to": "To Do"},
                    {"at": "2024-01-03T00:00:00+00:00", "from": "To Do", "to": "Done"},
                    {"at": "2024-01-05T00:00:00+00:00", "from": "Done", "to": "In Progress"},
                    {"at": "2024-01-11T00:00:00+00:00", "from": "In Progress", "to": "Done"},
                ]
                i["status"] = "Done"
        (self.dir / ".jira/board.json").write_text(json.dumps(b))
        r = self.cli("report", "--json")
        m = json.loads(r.stdout)
        self.assertEqual(m["cycle_time"]["count"], 1)
        self.assertAlmostEqual(m["cycle_time"]["avg_days"], 2.0)

    def test_report_without_history_does_not_crash(self):
        # Simulate an old board: strip history entirely.
        self.cli("add", "--type", "Task", "--title", "A")
        self.cli("move", "TST-1", "Done")
        b = self.board()
        for i in b["issues"]:
            i.pop("history", None)
        (self.dir / ".jira/board.json").write_text(json.dumps(b))
        r = self.cli("report", "--json")
        self.assertEqual(r.returncode, 0)
        m = json.loads(r.stdout)
        self.assertEqual(m["cycle_time"]["count"], 0)
        self.assertIsNone(m["cycle_time"]["avg_days"])
        self.assertIsNone(m["cycle_time"]["median_days"])
        self.assertEqual(m["total"], 1)

    def test_report_human_output(self):
        self.cli("add", "--type", "Task", "--title", "A")
        r = self.cli("report")
        self.assertEqual(r.returncode, 0)
        self.assertIn("total", r.stdout.lower())

    def test_report_json_shape(self):
        self.cli("add", "--type", "Task", "--title", "A")
        r = self.cli("report", "--json")
        m = json.loads(r.stdout)
        for k in ("total", "by_status", "by_type", "by_priority", "stale", "cycle_time"):
            self.assertIn(k, m)
        for k in ("count", "avg_days", "median_days"):
            self.assertIn(k, m["cycle_time"])


# --------------------------------------------------------------------------- #
# JT-58: init writes <board-dir>/.gitignore so board.lock never lands in git
# --------------------------------------------------------------------------- #

class TestInitBoardDirGitignore(BoardTestCase):
    """init must create .jira/.gitignore ignoring the transient board.lock,
    so the lock stays out of git whether or not .jira/ itself is committed."""

    def test_init_writes_gitignore_for_board_lock(self):
        gi = self.dir / ".jira/.gitignore"
        self.assertTrue(gi.exists(), ".jira/.gitignore not created by init")
        self.assertIn("board.lock", gi.read_text().splitlines())

    def test_init_preserves_existing_board_dir_gitignore(self):
        gi = self.dir / ".jira/.gitignore"
        gi.write_text("custom-entry\n")
        r = run(["init", "--name", "Again", "--key", "AG", "--force"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(gi.read_text(), "custom-entry\n")

    def test_init_with_file_flag_writes_gitignore_next_to_board(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "elsewhere/board.json"
            r = run(["--file", str(target), "init", "--name", "R", "--key", "REM"],
                    Path(d))
            self.assertEqual(r.returncode, 0, r.stderr)
            gi = target.parent / ".gitignore"
            self.assertTrue(gi.exists())
            self.assertIn("board.lock", gi.read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
