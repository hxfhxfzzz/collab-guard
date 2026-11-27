"""bin/verify-run：三条规则各对应哪个退出码。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util

SCRIPT = os.path.join(_util.ROOT, "bin", "verify-run")
CASE = {"pass": '<testcase name="t{i}"/>', "fail": '<testcase name="t{i}"><failure/></testcase>',
        "error": '<testcase name="t{i}"><error/></testcase>', "skip": '<testcase name="t{i}"><skipped/></testcase>'}


def junit(*kinds):
    cases = "".join(CASE[k].format(i=i) for i, k in enumerate(kinds))
    return '<?xml version="1.0"?><testsuites><testsuite name="s" tests="%d">%s</testsuite></testsuites>' % (len(kinds), cases)


class Verify(unittest.TestCase):
    def setUp(self):
        self.tmp = _util.tempdir()
        self.repo = _util.make_repo(os.path.realpath(self.tmp.name), "project")
        with open(os.path.join(self.repo, ".gitignore"), "w") as fh:
            fh.write("reports/\n")
        _util.git(self.repo, "add", "--", ".gitignore")
        _util.git(self.repo, "commit", "-q", "-m", "ignore")

    def tearDown(self):
        self.tmp.cleanup()

    def write_report(self, xml, rel="reports/junit.xml", repo=None):
        path = os.path.join(repo or self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(xml)

    def fake_tests(self, xml, exit_code=0, rel="reports/junit.xml", extra=""):
        """一条假的测试命令：写一份报告，然后按指定退出码结束。"""
        code = ("import os,sys\nos.makedirs(os.path.dirname(%r), exist_ok=True)\n"
                "open(%r,'w').write(%r)\n%s\nsys.exit(%d)") % (rel, rel, xml, extra, exit_code)
        return [sys.executable, "-c", code]

    def verify(self, command, *options, cwd=None):
        args = [sys.executable, SCRIPT, "--cwd", cwd or self.repo, *(options or ("--report", "reports/*.xml"))]
        return _util.run(args + ["--"] + command if command is not None else args)

    def test_pass(self):
        p = self.verify(self.fake_tests(junit("pass", "pass", "skip")))
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("通过 ✅", p.stdout)
        self.assertIn("执行 2", p.stdout)

    # 规则 1：只认本次运行生成的证据
    def test_stale_report_not_counted(self):
        self.write_report(junit("pass", "pass"))
        p = self.verify([sys.executable, "-c", "pass"])
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("没有本次运行生成的报告", p.stdout)
        self.assertIn("旧的", p.stdout)

    def test_source_changed_during_run(self):
        p = self.verify(self.fake_tests(junit("pass"), extra="open('a.txt','a').write('edited\\n')"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("运行期间源码被改了", p.stdout)
        self.assertIn("a.txt", p.stdout)

    def test_new_untracked_file_during_run(self):
        p = self.verify(self.fake_tests(junit("pass"), extra="open('new_module.py','w').write('x=1\\n')"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("new_module.py", p.stdout)

    def test_dirty_tree_before_run_is_fine(self):
        with open(os.path.join(self.repo, "a.txt"), "a") as fh:
            fh.write("work in progress\n")
        self.assertEqual(self.verify(self.fake_tests(junit("pass"))).returncode, 0)

    def test_untracked_report_is_not_a_source_change(self):
        p = self.verify(self.fake_tests(junit("pass"), rel="out/junit.xml"), "--report", "out/*.xml")
        self.assertEqual(p.returncode, 0, p.stdout)

    # 规则 2：0 个用例不算通过
    def test_zero_tests(self):
        p = self.verify(self.fake_tests(junit()))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("实际执行的用例是 0 个", p.stdout)

    def test_all_skipped(self):
        self.assertEqual(self.verify(self.fake_tests(junit("skip", "skip"))).returncode, 1)

    def test_no_report_at_all(self):
        self.assertEqual(self.verify([sys.executable, "-c", "print('BUILD SUCCESS')"]).returncode, 1)

    def test_failures_and_errors(self):
        self.assertEqual(self.verify(self.fake_tests(junit("pass", "fail"))).returncode, 1)
        self.assertEqual(self.verify(self.fake_tests(junit("pass", "error"))).returncode, 1)

    def test_command_failed_even_if_report_is_green(self):
        p = self.verify(self.fake_tests(junit("pass"), exit_code=3))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("命令退出码是 3", p.stdout)

    def test_broken_report(self):
        p = self.verify(self.fake_tests("<testsuite"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("报告读不了", p.stdout)

    def test_suite_attributes_without_testcases(self):
        xml = '<testsuites tests="4"><testsuite name="s" tests="4" failures="0" errors="0" skipped="1"/></testsuites>'
        p = self.verify(self.fake_tests(xml))
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("执行 3", p.stdout)

    def test_recursive_glob_sums_reports(self):
        self.write_report("old", rel="mod/a/reports/TEST-old.xml")
        extra = "os.makedirs('reports/sub', exist_ok=True)\nopen('reports/sub/TEST-b.xml','w').write(%r)" % junit("pass", "pass")
        p = self.verify(self.fake_tests(junit("pass"), extra=extra), "--report", "reports/**/*.xml")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("报告 2 份", p.stdout)
        self.assertIn("执行 3", p.stdout)

    # 规则 3：跑不了就是未验证
    def test_command_not_found(self):
        p = self.verify(["no-such-test-runner-xyz", "test"])
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertIn("未验证", p.stdout)

    def test_shell_command_not_found(self):
        self.assertEqual(self.verify(["sh", "-c", "no-such-test-runner-xyz"]).returncode, 2)

    def test_missing_report_option(self):
        p = _util.run([sys.executable, SCRIPT, "--cwd", self.repo, "--", sys.executable, "-c", "pass"])
        self.assertEqual(p.returncode, 2, p.stdout)

    def test_missing_command(self):
        self.assertEqual(self.verify(None).returncode, 2)

    def test_unknown_option(self):
        self.assertEqual(self.verify([sys.executable, "-c", "pass"], "--report", "r/*.xml", "--bogus").returncode, 2)

    def test_outside_git_repo(self):
        plain = os.path.join(os.path.dirname(self.repo), "plain")
        os.makedirs(plain)
        p = self.verify(self.fake_tests(junit("pass")), cwd=plain)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("不是 git 仓库", p.stdout)


if __name__ == "__main__":
    unittest.main()
