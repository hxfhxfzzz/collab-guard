"""install.py：装、重复装、看状态、卸，都不能动别人的配置。"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util

OTHER = {"permissions": {"allow": ["Bash(ls:*)"]},
         "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}],
                   "Stop": [{"hooks": [{"type": "command", "command": "echo bye"}]}]}}


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = _util.tempdir()
        self.base = os.path.realpath(self.tmp.name)
        self.repo = _util.make_repo(self.base, "project")
        self.settings = os.path.join(self.repo, ".claude", "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        with open(self.settings, encoding="utf-8") as fh:
            return json.load(fh)

    def write(self, data):
        os.makedirs(os.path.dirname(self.settings), exist_ok=True)
        with open(self.settings, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))

    def commands(self):
        return [(event, group.get("matcher"), hook["command"])
                for event, groups in self.read().get("hooks", {}).items() for group in groups for hook in group["hooks"]]

    def hooks_path(self):
        return _util.git(self.repo, "config", "--get", "core.hooksPath").stdout.strip()

    def test_fresh_install(self):
        p = _util.install(self.repo)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        target = os.path.join(self.repo, ".claude", "collab-guard")
        for rel in ("hooks/git_guard.py", "hooks/confirm_gate.py", "git-hooks/guard.py", "git-hooks/pre-push", "bin/verify-run"):
            self.assertTrue(os.access(os.path.join(target, rel), os.X_OK), rel)
        ours = [c for c in self.commands() if ".claude/collab-guard/" in c[2]]
        self.assertEqual(sorted(((e, m) for e, m, _ in ours), key=str),
                         sorted([("SessionStart", None), ("PreToolUse", "Bash"),
                                 ("PreToolUse", "Edit|Write|MultiEdit|NotebookEdit")], key=str))
        self.assertEqual(self.hooks_path(), os.path.join(target, "git-hooks"))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "AGENTS.md")), "没加 --with-templates 不放模板")

    def test_idempotent_and_keeps_other_settings(self):
        self.write(OTHER)
        _util.install(self.repo)
        once = self.read()
        again = _util.install(self.repo)
        self.assertEqual(again.returncode, 0)
        self.assertIn("没动", again.stdout)
        self.assertEqual(self.read(), once)
        self.assertEqual(len(self.commands()), 5)
        self.assertEqual(once["permissions"], OTHER["permissions"])
        self.assertIn(("PreToolUse", "Bash", "echo mine"), self.commands())

    def test_remove_restores_everything(self):
        self.write(OTHER)
        _util.install(self.repo)
        p = _util.install(self.repo, "--remove")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.read(), OTHER)
        self.assertEqual(self.hooks_path(), "")
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".claude", "collab-guard")))
        self.assertEqual(_util.install(self.repo, "--remove").returncode, 0, "重复卸不报错")

    def test_remove_from_fresh_leaves_empty_settings(self):
        _util.install(self.repo)
        _util.install(self.repo, "--remove")
        self.assertEqual(self.read(), {})

    def test_broken_settings_untouched(self):
        self.write("{ 这不是 JSON")
        p = _util.install(self.repo)
        self.assertEqual(p.returncode, 1)
        self.assertIn("读不了", p.stdout)
        self.assertEqual(_util.read_text(self.settings), "{ 这不是 JSON")

    def test_flags(self):
        p = _util.install(self.repo, "--no-claude-hooks", "--no-git-hooks", "--with-templates")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertFalse(os.path.exists(self.settings))
        self.assertEqual(self.hooks_path(), "")
        self.assertIn("@AGENTS.md", _util.read_text(os.path.join(self.repo, "CLAUDE.md")))
        with open(os.path.join(self.repo, "AGENTS.md"), "w") as fh:
            fh.write("mine\n")
        _util.install(self.repo, "--with-templates")
        self.assertEqual(_util.read_text(os.path.join(self.repo, "AGENTS.md")), "mine\n", "已有的规则文件不能覆盖")

    def test_status(self):
        before = _util.install(self.repo, "--status")
        self.assertIn("没装", before.stdout)
        self.assertIn("未装", before.stdout)
        _util.install(self.repo)
        after = _util.install(self.repo, "--status")
        self.assertIn("三条都登记了", after.stdout)
        self.assertIn("已装", after.stdout)

    def test_workspace_with_several_repos(self):
        workspace = os.path.join(self.base, "workspace")
        os.makedirs(workspace)
        one, two = _util.make_repo(workspace, "one"), _util.make_repo(workspace, "two")
        os.makedirs(os.path.join(workspace, "notes"))
        p = _util.install(workspace)
        self.assertEqual(p.returncode, 0, p.stdout)
        expected = os.path.join(workspace, ".claude", "collab-guard", "git-hooks")
        for repo in (one, two):
            self.assertEqual(_util.git(repo, "config", "--get", "core.hooksPath").stdout.strip(), expected)

    def test_bad_arguments(self):
        self.assertEqual(_util.install(os.path.join(self.base, "missing")).returncode, 2)
        self.assertEqual(_util.install(self.repo, "--status", "--remove").returncode, 2)
        self.assertEqual(_util.install(_util.ROOT).returncode, 2)


if __name__ == "__main__":
    unittest.main()
