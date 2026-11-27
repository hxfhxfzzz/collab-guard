"""git-hooks/：通用 git 守卫。先测判断逻辑，再在临时仓库里真的 push。"""
import filecmp
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util

guard = _util.load("git-hooks/guard.py", "guard")
HOOKS = os.path.join(_util.ROOT, "git-hooks")


class Detect(unittest.TestCase):
    def test_env(self):
        self.assertIn("CLAUDECODE", guard.detect({"CLAUDECODE": "1"}, []))
        self.assertIn("CODEX_SANDBOX", guard.detect({"CODEX_SANDBOX": "seatbelt"}, []))
        self.assertEqual(guard.detect({"CODEX_HOME": "/home/u/.codex", "CLAUDECODE": ""}, []), "")

    def test_ancestors_agent(self):
        chains = [
            [("/bin/zsh", "/bin/zsh -c git push"), ("/usr/local/bin/claude", "claude")],
            [("/bin/zsh", "zsh"), ("node", "node /usr/lib/node_modules/@scope/codex/bin/codex.js exec")],
            [("/opt/homebrew/bin/node", "/opt/homebrew/bin/node --no-warnings /opt/tools/Claude-Code/cli.js")],
            [("/usr/bin/python3", "python3 /home/u/.local/bin/aider --model x")],
        ]
        for chain in chains:
            with self.subTest(chain=chain):
                self.assertTrue(guard.detect({}, chain))

    def test_ancestors_human(self):
        chains = [
            [("/bin/zsh", "-zsh"), ("/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal", "Terminal")],
            # 点开头的目录名不算
            [("/bin/zsh", "zsh"), ("/usr/bin/python3", "python3 /home/u/.claude/tools/sync.py")],
            # 只看脚本路径，不看其它参数
            [("/usr/local/bin/node", "node /srv/app/server.js --notify claude")],
            # 内联代码不算脚本
            [("/usr/bin/python3", "python3 -c print('codex')")],
            # 名字只是别的词的一部分
            [("/usr/local/bin/claudette", "claudette"), ("/usr/bin/codexctl", "codexctl")],
        ]
        for chain in chains:
            with self.subTest(chain=chain):
                self.assertEqual(guard.detect({}, chain), "")

    def test_custom_names(self):
        chain = [("/Applications/MyEditor.app/Contents/MacOS/MyEditor Helper", "MyEditor Helper")]
        self.assertEqual(guard.detect({}, chain), "")
        self.assertTrue(guard.detect({}, chain, ("myeditor",)))

    def test_callers_skips_guard_and_git(self):
        chain = [("/usr/bin/python3", "python3 guard.py pre-push"), ("/usr/bin/git", "git push origin main"),
                 ("/bin/zsh", "zsh"), ("/usr/local/bin/claude", "claude")]
        self.assertEqual(guard.callers(chain), chain[2:])
        self.assertEqual(guard.git_command(chain), ["push", "origin", "main"])


class Files(unittest.TestCase):
    def test_entries_identical_and_executable(self):
        names = sorted(n for n in os.listdir(HOOKS) if n != "guard.py" and not n.startswith((".", "_")))
        self.assertIn("pre-push", names)
        self.assertIn("pre-commit", names)
        for name in names:
            with self.subTest(hook=name):
                path = os.path.join(HOOKS, name)
                self.assertTrue(filecmp.cmp(path, os.path.join(HOOKS, "pre-push"), shallow=False))
                self.assertTrue(os.access(path, os.X_OK))


class Push(unittest.TestCase):
    def setUp(self):
        self.tmp = _util.tempdir()
        self.base = os.path.realpath(self.tmp.name)
        self.repo = _util.make_repo(self.base, "project")
        self.remote = _util.make_remote(self.base, self.repo)
        done = _util.install(self.repo, "--no-claude-hooks")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def pushed(self):
        return _util.git(self.remote, "rev-parse", "-q", "--verify", "refs/heads/main").returncode == 0

    def test_agent_env_blocked(self):
        p = _util.git(self.repo, "push", "origin", "main", env=_util.clean_env(CLAUDECODE="1"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("已拦截", p.stderr)
        self.assertIn("CLAUDECODE", p.stderr)
        self.assertFalse(self.pushed())

    def test_agent_dry_run_allowed(self):
        p = _util.git(self.repo, "push", "--dry-run", "origin", "main", env=_util.clean_env(CLAUDECODE="1"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(self.pushed())

    def test_commit_not_blocked_for_agent(self):
        with open(os.path.join(self.repo, "a.txt"), "a") as fh:
            fh.write("b\n")
        p = _util.git(self.repo, "commit", "-q", "-a", "-m", "second", env=_util.clean_env(CLAUDECODE="1"))
        self.assertEqual(p.returncode, 0, p.stderr)

    def orphan_push(self):
        """在脱离当前进程树的子进程里 push：模拟人自己在终端里推。返回 (退出码, 输出, 判断记录)。"""
        out, rc, trace = (os.path.join(self.base, n) for n in ("out.txt", "rc.txt", "trace.txt"))
        script = '( git -C "$1" push origin main >"$2" 2>&1; echo $? >"$3" ) &'
        _util.run(["sh", "-c", script, "sh", self.repo, out, rc], env=_util.clean_env(COLLAB_GUARD_TRACE=trace))
        for _ in range(200):
            if _util.read_text(rc).strip():
                break
            time.sleep(0.05)
        return _util.read_text(rc).strip(), _util.read_text(out), _util.read_text(trace)

    def test_human_push_passes(self):
        rc, out, trace = self.orphan_push()
        if rc != "0" and "上层进程" in out:
            self.skipTest("这个环境里子进程脱离不了 agent 的进程树，没法模拟人推送：%s" % trace.strip())
        self.assertEqual(rc, "0", out)
        self.assertIn("agent=''", trace)
        self.assertTrue(self.pushed())

    def test_ancestor_detection_when_run_by_agent(self):
        """测试本身由 agent 启动时：就算环境变量清干净，也应该靠进程祖先认出来。"""
        trace = os.path.join(self.base, "trace.txt")
        probe = guard.detect({}, guard.callers(guard.ancestry(os.getpid(), guard.process_table())))
        if not probe:
            self.skipTest("这次测试不是 agent 启动的，进程祖先里没有 agent")
        p = _util.git(self.repo, "push", "origin", "main", env=_util.clean_env(COLLAB_GUARD_TRACE=trace))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("上层进程", p.stderr)
        self.assertFalse(self.pushed())

    def test_agent_names_from_git_config(self):
        """名单换成一个不存在的名字后，同样的进程祖先就不再算 agent。"""
        _util.git(self.repo, "config", "collabguard.agentNames", "no-such-agent-name")
        p = _util.git(self.repo, "push", "origin", "main")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.pushed())


class Chain(unittest.TestCase):
    """装了守卫之后，仓库原来的 hook 还要照常执行。"""

    def setUp(self):
        self.tmp = _util.tempdir()
        self.base = os.path.realpath(self.tmp.name)
        self.repo = _util.make_repo(self.base, "project")
        self.flag = os.path.join(self.base, "hook-ran")

    def tearDown(self):
        self.tmp.cleanup()

    def write_hook(self, directory, name, body):
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n%s\n" % body)
        os.chmod(path, 0o755)

    def commit(self, env=None):
        with open(os.path.join(self.repo, "a.txt"), "a") as fh:
            fh.write("more\n")
        return _util.git(self.repo, "commit", "-q", "-m", "next", "--", "a.txt", env=env)

    def test_default_hooks_dir_still_runs(self):
        self.write_hook(os.path.join(self.repo, ".git", "hooks"), "pre-commit", 'echo ran > "%s"' % self.flag)
        _util.install(self.repo, "--no-claude-hooks")
        self.assertEqual(self.commit().returncode, 0)
        self.assertTrue(os.path.exists(self.flag))

    def test_failing_previous_hook_still_blocks(self):
        self.write_hook(os.path.join(self.repo, ".git", "hooks"), "pre-commit", "echo no >&2; exit 1")
        _util.install(self.repo, "--no-claude-hooks")
        p = self.commit()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("no", p.stderr)

    def test_custom_hooks_path_kept_and_restored(self):
        self.write_hook(os.path.join(self.repo, ".husky"), "commit-msg", 'cat "$1" > "%s"' % self.flag)
        _util.git(self.repo, "config", "core.hooksPath", ".husky")
        done = _util.install(self.repo, "--no-claude-hooks")
        self.assertIn(".husky", done.stdout)
        self.assertEqual(_util.git(self.repo, "config", "--get", "collabguard.prevHooksPath").stdout.strip(), ".husky")
        self.assertEqual(self.commit().returncode, 0)
        self.assertEqual(_util.read_text(self.flag).strip(), "next")
        gone = _util.install(self.repo, "--remove")
        self.assertEqual(gone.returncode, 0, gone.stdout)
        self.assertEqual(_util.git(self.repo, "config", "--get", "core.hooksPath").stdout.strip(), ".husky")
        self.assertEqual(_util.git(self.repo, "config", "--get", "collabguard.prevHooksPath").stdout.strip(), "")

    def test_previous_pre_push_gets_stdin(self):
        remote = _util.make_remote(self.base, self.repo)
        self.write_hook(os.path.join(self.repo, ".git", "hooks"), "pre-push", 'cat > "%s"' % self.flag)
        _util.install(self.repo, "--no-claude-hooks")
        _util.git(self.repo, "config", "collabguard.agentNames", "no-such-agent-name")
        p = _util.git(self.repo, "push", "origin", "main")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("refs/heads/main", _util.read_text(self.flag))
        self.assertEqual(_util.git(remote, "rev-parse", "-q", "--verify", "refs/heads/main").returncode, 0)


if __name__ == "__main__":
    unittest.main()
