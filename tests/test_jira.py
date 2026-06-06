"""Black-box tests for the jira.py CLI — stdlib only, no third-party deps.

Each test runs the real CLI via subprocess in a temp directory, so the suite
exercises exactly what agents and humans run. The .claude tree is the code
under test; a sync test asserts the .codex tree is byte-identical.

Run: python3 -m unittest discover -s tests -v
"""

import json
import os
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
                     if p.name not in ("board.json", "board.html", "board.lock")]
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
        for rel in ("SKILL.md", "scripts/jira.py", "scripts/install-board-hook.py",
                    "references/schema.md"):
            a = (CLAUDE_TREE / rel).read_bytes()
            b = (CODEX_TREE / rel).read_bytes()
            self.assertEqual(a, b, f"{rel} differs between .claude and .codex trees")


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


class TestFilePositionBothWays(BoardTestCase):
    """JT-32e: --file must be accepted before AND after the subcommand."""

    def test_file_before_subcommand(self):
        board_path = str(self.dir / ".jira/board.json")
        r = run(["--file", board_path, "list"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_file_after_subcommand(self):
        board_path = str(self.dir / ".jira/board.json")
        r = run(["list", "--file", board_path], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_file_after_add_subcommand(self):
        board_path = str(self.dir / ".jira/board.json")
        r = run(["add", "--file", board_path, "--type", "Task", "--title", "Via subparser file"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        b = json.loads(Path(board_path).read_text())
        self.assertEqual(len(b["issues"]), 1)


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


if __name__ == "__main__":
    unittest.main()
