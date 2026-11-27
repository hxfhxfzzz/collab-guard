"""hooks/confirm_gate.py：每个仓库每个会话拦一次，文档和项目外的文件不拦。"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util

SCRIPT = os.path.join(_util.ROOT, "hooks", "confirm_gate.py")


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = _util.tempdir()
        base = os.path.realpath(self.tmp.name)
        self.project = _util.make_repo(base, "project")
        self.marks = os.path.join(base, "marks")
        os.makedirs(self.marks)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, file_path=None, session="s1", event="PreToolUse", tool="Edit", project=None, **extra):
        payload = {"hook_event_name": event, "session_id": session, "tool_name": tool,
                   "tool_input": {"file_path": file_path} if file_path else {}}
        payload.update(extra)
        env = _util.clean_env(TMPDIR=self.marks, CLAUDE_PROJECT_DIR=project or self.project)
        return _util.run([sys.executable, SCRIPT], env=env, stdin=json.dumps(payload))

    def path(self, *parts):
        return os.path.join(self.project, *parts)

    def test_first_edit_blocked_then_allowed(self):
        first = self.call(self.path("src", "app.py"))
        self.assertEqual(first.returncode, 2)
        for text in ("先确认再写", "src/app.py", "1.", "2.", "3."):
            self.assertIn(text, first.stderr)
        self.assertEqual(self.call(self.path("src", "app.py")).returncode, 0)
        self.assertEqual(self.call(self.path("src", "other.py"), tool="Write").returncode, 0)

    def test_session_start_resets(self):
        self.assertEqual(self.call(self.path("a.py")).returncode, 2)
        self.assertEqual(self.call(self.path("a.py")).returncode, 0)
        self.assertEqual(self.call(event="SessionStart").returncode, 0)
        self.assertEqual(self.call(self.path("a.py")).returncode, 2)

    def test_sessions_are_separate(self):
        self.assertEqual(self.call(self.path("a.py"), session="s1").returncode, 2)
        self.assertEqual(self.call(self.path("a.py"), session="s2").returncode, 2)

    def test_not_gated(self):
        outside = os.path.join(os.path.dirname(self.project), "elsewhere", "x.py")
        cases = {
            "文档": self.call(self.path("README.md")),
            "docs 目录": self.call(self.path("docs", "conf.py")),
            "客户端配置目录": self.call(self.path(".claude", "settings.json")),
            "项目外的文件": self.call(outside),
            "Plan 模式": self.call(self.path("a.py"), permission_mode="plan"),
            "不是编辑工具": self.call(self.path("a.py"), tool="Read"),
            "没有路径": self.call(None),
        }
        for name, p in cases.items():
            with self.subTest(case=name):
                self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertEqual(self.call(self.path("a.py")).returncode, 2, "前面那些放行不应该消耗掉这一次提醒")

    def test_each_repo_gated_once(self):
        workspace = os.path.join(os.path.dirname(self.project), "workspace")
        os.makedirs(workspace)
        one, two = _util.make_repo(workspace, "one"), _util.make_repo(workspace, "two")
        first = self.call(os.path.join(one, "a.py"), project=workspace)
        self.assertEqual(first.returncode, 2)
        self.assertIn("one", first.stderr)
        self.assertEqual(self.call(os.path.join(one, "b.py"), project=workspace).returncode, 0)
        self.assertEqual(self.call(os.path.join(two, "a.py"), project=workspace).returncode, 2)

    def test_project_without_git(self):
        plain = os.path.join(os.path.dirname(self.project), "plain")
        os.makedirs(plain)
        self.assertEqual(self.call(os.path.join(plain, "a.py"), project=plain).returncode, 2)
        self.assertEqual(self.call(os.path.join(plain, "b.py"), project=plain).returncode, 0)

    def test_notebook_and_relative_path(self):
        payload_tool = {"tool": "NotebookEdit"}
        p = _util.run([sys.executable, SCRIPT], env=_util.clean_env(TMPDIR=self.marks, CLAUDE_PROJECT_DIR=self.project),
                      stdin=json.dumps({"hook_event_name": "PreToolUse", "session_id": "nb", "tool_name": "NotebookEdit",
                                        "tool_input": {"notebook_path": "nb/a.ipynb"}}))
        self.assertEqual(p.returncode, 2, payload_tool)

    def test_broken_input_passes(self):
        p = _util.run([sys.executable, SCRIPT], stdin="不是 JSON")
        self.assertEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
