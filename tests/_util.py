"""测试共用的小工具。所有仓库都建在系统临时目录里，不读用户自己的 git 配置。"""
import importlib.util
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_ENV = ("AI_AGENT", "CLAUDECODE", "CODEX_THREAD_ID", "CODEX_TOOL_CALL_ID", "CODEX_SANDBOX")


def load(rel, name):
    """按路径加载一个脚本当模块用（脚本名里有连字符或没有 .py 也行）。"""
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader(name, os.path.join(ROOT, rel))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def clean_env(**extra):
    """去掉 agent 相关变量、隔离用户 git 配置的环境。"""
    env = {k: v for k, v in os.environ.items() if k not in AGENT_ENV and not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
               GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="tester@example.invalid")
    env.update(extra)
    return env


def run(args, cwd=None, env=None, stdin=None):
    return subprocess.run(args, cwd=cwd, env=env or clean_env(), input=stdin, capture_output=True, text=True)


def git(repo, *args, env=None):
    return run(["git", "-C", repo, *args], env=env)


def make_repo(parent, name="repo", commit=True):
    repo = os.path.join(parent, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    if commit:
        with open(os.path.join(repo, "a.txt"), "w") as fh:
            fh.write("a\n")
        git(repo, "add", "--", "a.txt")
        git(repo, "commit", "-q", "-m", "init")
    return repo


def make_remote(parent, repo, name="remote.git"):
    remote = os.path.join(parent, name)
    run(["git", "init", "-q", "--bare", "-b", "main", remote])
    git(repo, "remote", "add", "origin", remote)
    return remote


def install(project, *flags):
    return run([sys.executable, os.path.join(ROOT, "install.py"), project, *flags])


def read_text(path):
    """文件内容；不存在返回空串。"""
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def tempdir():
    return tempfile.TemporaryDirectory(prefix="cg-test-")
