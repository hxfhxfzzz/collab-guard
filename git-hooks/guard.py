#!/usr/bin/env python3
"""通用 git 守卫：装成 git 自己的 hook，所以对所有 agent 都生效，不依赖哪个客户端自己的 hook 机制。
由同目录的 pre-push 等入口脚本调用。

只在「是 agent 在操作」时拦，人自己在终端或 IDE 里提交、推送不受影响：
  pre-push    agent 不推送（git push --dry-run 放行）。
只硬拦撤不回来、对外的操作。其余入口（pre-commit、commit-msg…）不做判断，只负责接着执行仓库原来的 hook：
core.hooksPath 指到这里之后，仓库原来的 hook 目录 git 不再看，要靠这里转过去。

拦不到的：git add -A / add . —— git 对 add 没有 hook，这条靠各客户端自己的守卫和规则。
能绕过的：--no-verify、-c core.hooksPath=…。这是防误操作的护栏，不是安全边界。

怎么判断是 agent（满足一条就算）：
  1. 环境变量里有 AGENT_ENV 里的名字；
  2. 进程祖先里有 AGENT_NAMES 里的程序。只看可执行文件路径，以及 node / python 这类解释器后面的脚本路径，
     不看其它参数；.claude、.codex 这种点开头的目录名不算；
  3. 连 ps 都不让执行（Operation not permitted）。只有 agent 的沙箱会这样，人在终端里不会遇到。
     这种情况看不到是哪条 git 命令，push --dry-run 也会被拦。
换一批程序名：git config collabguard.agentNames "claude codex 其它名字"（写了就整个替换默认名单）。
注意：在 agent 客户端自带的终端里手动推送，进程祖先里也有它的名字，同样会被拦；换系统终端推。

守卫放行后，仓库原来的 hook 照常执行（安装时记在 collabguard.prevHooksPath 里的目录，或 .git/hooks）。
守卫自己出错时放行并在 stderr 说明：宁可漏拦，不能挡住人正常提交。
"""
import os
import re
import subprocess
import sys

# 只收「正在替 agent 跑命令」时才会有的变量；人自己也会设的（各种 *_HOME）不收。
AGENT_ENV = ("AI_AGENT", "CLAUDECODE", "CODEX_THREAD_ID", "CODEX_TOOL_CALL_ID", "CODEX_SANDBOX")
# 默认只收命令行 agent。编辑器类客户端（它的内置终端也是它的子进程）容易误伤人，要拦自己加。
AGENT_NAMES = ("claude", "codex", "gemini", "opencode", "aider")
NAMES_KEY = "collabguard.agentNames"
PREV_KEY = "collabguard.prevHooksPath"
# 这些解释器真正跑的是后面的脚本，要连脚本路径一起看
INTERPRETER_RE = re.compile(r"(?i)^(node|bun|deno|python[0-9.]*)$")
INLINE_CODE = ("-c", "-e", "-p", "--eval", "--print")
PS = "/bin/ps" if os.path.exists("/bin/ps") else "ps"
TRACE = os.environ.get("COLLAB_GUARD_TRACE")  # 调试用：把判断依据追加到这个文件，不影响放行还是拦截


def git_config(key):
    return subprocess.run(["git", "config", "--get", key], capture_output=True, text=True).stdout.strip()


def name_re(names):
    return re.compile(r"(?i)(?<![a-z.])(%s)(?![a-z])" % "|".join(re.escape(n) for n in names))


def agent_names():
    configured = [n for n in re.split(r"[\s,]+", git_config(NAMES_KEY)) if n]
    return tuple(configured) or AGENT_NAMES


def process_table():
    """{pid: (ppid, 可执行文件, 完整命令行)}"""
    def ps(columns, fields):
        out = subprocess.run([PS, "-A", "-o", columns], capture_output=True, text=True, timeout=10).stdout
        rows = {}
        for line in out.splitlines():
            parts = line.split(None, fields - 1)
            if len(parts) == fields:
                rows[parts[0]] = parts[1:]
        return rows
    comms, commands = ps("pid=,ppid=,comm=", 3), ps("pid=,args=", 2)
    return {pid: (v[0], v[1], commands.get(pid, [v[1]])[0]) for pid, v in comms.items()}


def ancestry(pid, table):
    """从 pid 往上的进程链 [(可执行文件, 完整命令行), …]，不含 1 号进程。"""
    chain, cur = [], str(pid)
    while cur in table and cur not in ("0", "1") and len(chain) < 40:
        ppid, comm, command = table[cur]
        chain.append((comm, command))
        cur = ppid
    return chain


def script_of(comm, command):
    """解释器后面的脚本路径；是内联代码（-c / -e）或没有脚本时返回空。"""
    rest = command[len(comm):].split() if command.startswith(comm) else command.split()[1:]
    for token in rest:
        if token in INLINE_CODE:
            return ""
        if not token.startswith("-"):
            return token
    return ""


def callers(chain):
    """去掉守卫自己和 git 本身，只留「是谁调用了 git」那一段。"""
    for i, (comm, _) in enumerate(chain):
        if os.path.basename(comm) == "git":
            return chain[i + 1:]
    return chain[1:]


def detect(env, chain, names=AGENT_NAMES):
    """返回判断为 agent 的依据（字符串）；不是 agent 返回空串。"""
    for name in AGENT_ENV:
        if env.get(name):
            return "环境变量 %s" % name
    pattern = name_re(names)
    for comm, command in chain:
        subject = comm
        if INTERPRETER_RE.match(os.path.basename(comm)):
            subject += " " + script_of(comm, command)
        if pattern.search(subject):
            return "上层进程 %s" % subject.strip()[-80:]
    return ""


def identify(env):
    """返回 (判断为 agent 的依据, 进程链)。环境变量能判断时，进程链只用来认 --dry-run。"""
    why = detect(env, [])
    if why:
        try:
            return why, ancestry(os.getpid(), process_table())
        except Exception:
            return why, []
    try:
        chain = ancestry(os.getpid(), process_table())
    except PermissionError:
        return "在沙箱里（不允许执行 ps）", []
    return detect(env, callers(chain), agent_names()), chain


def git_command(chain):
    """调用这个 hook 的那条 git 命令的参数（按空白拆，引号已丢失，只用来认选项）。"""
    for comm, command in chain:
        if os.path.basename(comm) == "git":
            return command.split()[1:]
    return []


def trace(text):
    if TRACE:
        try:
            with open(TRACE, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass


def block(reason, hint):
    sys.stderr.write("【git 守卫】已拦截：%s\n%s\n不要加 --no-verify 或换写法重试。\n" % (reason, hint))
    sys.exit(1)


def check_push(chain, why):
    args = git_command(chain)
    if "--dry-run" in args or any(re.match(r"^-[a-zA-Z]*n[a-zA-Z]*$", a) for a in args):
        return
    block("agent 不推送（判断依据：%s）" % why, "推送由用户自己做：把分支名和提交号告诉用户即可。")


def chain_previous(hook, args):
    """执行仓库原来的同名 hook（如果有且可执行）。stdin 原样留给它。"""
    def rev_parse(flag):
        return subprocess.run(["git", "rev-parse", flag], capture_output=True, text=True).stdout.strip()

    prev = git_config(PREV_KEY)
    if prev:
        base = prev if os.path.isabs(prev) else os.path.join(rev_parse("--show-toplevel"), prev)
    else:
        base = os.path.join(os.path.abspath(rev_parse("--git-common-dir")), "hooks")
    target = os.path.join(base, hook)
    here = os.path.dirname(os.path.realpath(__file__))
    if os.path.isfile(target) and os.access(target, os.X_OK) and os.path.realpath(base) != here:
        os.execv(target, [target] + args)


def main():
    hook, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    try:
        if hook == "pre-push":
            why, chain = identify(os.environ)
            trace("%s agent=%r git=%s 上层=%s" % (
                hook, why, " ".join(git_command(chain))[:120], [os.path.basename(c) for c, _ in callers(chain)]))
            if why:
                check_push(chain, why)
        chain_previous(hook, args)
    except Exception as exc:  # 守卫出错不能挡住正常提交；block() 走的是 SystemExit，不会被这里吞掉
        sys.stderr.write("【git 守卫】自身出错（%s），本次放行\n" % exc)
        sys.exit(0)


if __name__ == "__main__":
    main()
