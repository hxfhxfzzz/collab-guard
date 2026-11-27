"""hooks/git_guard.py：哪些命令拦，哪些放行。"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util

guard = _util.load("hooks/git_guard.py", "git_guard")

BLOCKED = [
    "git add -A",
    "git add --all",
    "git add -u",
    "git add .",
    "git add -- .",
    "git add -Av",
    "git stage -A",
    "git -C sub add .",
    "git -c core.editor=true add --all",
    "cd repo && git add . && git commit -m x",
    "echo ok; git push",
    "git push origin main",
    "git push --force-with-lease origin feature",
    "sudo git push",
    "FOO=1 git push",
    "env FOO=1 git push",
    "sh -c 'git push origin main'",
    "bash -c \"git add -A\"",
    "eval git push",
    "if true; then git push; fi",
    "/usr/bin/git push",
    "git commit -m x && git push",
    "git add *",
]

ALLOWED = [
    "git add -- src/app.py",
    "git add src/app.py tests/test_app.py",
    "git add -p src/app.py",
    "git add -n .",
    "git add --dry-run -A",
    "git commit -a -m x",
    "git commit -m 'git push later'",
    "git push --dry-run origin main",
    "git push -n",
    "git status",
    "git log --all --oneline",
    "git diff -u",
    "echo 'git push'",
    "echo git add -A",
    "grep -r 'git add .' docs",
    "cat <<'EOF' > notes.txt\ngit push\ngit add -A\nEOF",
    "ls . && pwd",
    "git stash push -m wip",
    "git remote -v",
    "",
    "git add 'unterminated",
]


class Parse(unittest.TestCase):
    def test_blocked(self):
        for cmd in BLOCKED:
            with self.subTest(cmd=cmd):
                self.assertTrue(guard.check(cmd), "应该拦：%s" % cmd)

    def test_allowed(self):
        for cmd in ALLOWED:
            with self.subTest(cmd=cmd):
                try:
                    reason = guard.check(cmd)
                except Exception:  # 解析不了的命令由 main() 兜底放行
                    reason = None
                self.assertFalse(reason, "不该拦：%s" % cmd)


class Hook(unittest.TestCase):
    """按 Claude Code 传给 hook 的格式，从 stdin 喂 JSON。"""

    def call(self, payload):
        script = os.path.join(_util.ROOT, "hooks", "git_guard.py")
        return _util.run([sys.executable, script], stdin=payload if isinstance(payload, str) else json.dumps(payload))

    def test_block_exits_2_with_reason(self):
        p = self.call({"tool_name": "Bash", "tool_input": {"command": "git add -A"}})
        self.assertEqual(p.returncode, 2)
        self.assertIn("已拦截", p.stderr)

    def test_push_exits_2(self):
        self.assertEqual(self.call({"tool_name": "Bash", "tool_input": {"command": "git push"}}).returncode, 2)

    def test_allow_exits_0(self):
        p = self.call({"tool_name": "Bash", "tool_input": {"command": "git add -- a.py"}})
        self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_other_tool_passes(self):
        self.assertEqual(self.call({"tool_name": "Edit", "tool_input": {"command": "git push"}}).returncode, 0)

    def test_broken_input_passes(self):
        self.assertEqual(self.call("这不是 JSON").returncode, 0)
        self.assertEqual(self.call({"tool_name": "Bash", "tool_input": {"command": "git add 'unterminated"}}).returncode, 0)


if __name__ == "__main__":
    unittest.main()
