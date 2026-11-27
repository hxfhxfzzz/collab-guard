#!/usr/bin/env python3
"""git 守卫（Claude Code 的 PreToolUse(Bash) hook）：拦两类命令。

  1. 整片暂存：git add -A / --all / -u / . / * 以及 git stage 的同样写法。
     工作区里可能有别的会话或用户自己的改动，整片暂存会把它们一起带进提交。
  2. git push（--dry-run 放行）。推送对外、撤不回来，留给人做。

git commit -a 不拦：只在本地，撤得回来。

退出码 2 = 拦截，stderr 的文字会反馈给模型；0 = 放行。
命令解析不了时放行：守卫自己出错不能卡住所有 Bash 调用。
这是防误操作的护栏，不是安全边界，换写法总能绕过。
"""
import json
import os
import re
import shlex
import sys

SEPARATOR_CHARS = ";&|()<>\n"
LEADING_SKIP = {"then", "do", "else", "elif", "if", "while", "until", "!", "{", "}", "time"}
WRAPPERS = {"sudo", "env", "command", "nohup", "exec", "xargs"}
SHELLS = {"sh", "bash", "zsh", "dash"}
# git 的全局选项里，后面要单独跟一个值的
GIT_GLOBAL_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
ADD_COMMANDS = {"add", "stage"}
ADD_ALL_PATHSPECS = {".", "./", "*", ":/", ":/.", ":"}
ADD_ALL_LONG = {"--all", "--update", "--no-ignore-removal"}
DRY_RUN = {"-n", "--dry-run"}
HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

ADD_HINT = "只暂存本任务的具体文件，例如 `git add -- 路径/文件`。不要换写法绕过。"
PUSH_HINT = "推送由用户自己做：把分支名和提交号告诉用户即可。不要换写法绕过。"


def strip_heredocs(cmd):
    """去掉 heredoc 正文，避免把写进文件的文字当成命令。"""
    kept, pending = [], []
    for line in cmd.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        kept.append(line)
        pending.extend(m.group(2) for m in HEREDOC_RE.finditer(line))
    return "\n".join(kept)


def segments(cmd):
    """按 ; && || | 换行等切成一条条简单命令，每条是一个 token 列表。"""
    lex = shlex.shlex(strip_heredocs(cmd.replace("\\\n", " ")), posix=True, punctuation_chars=SEPARATOR_CHARS)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    current = []
    for tok in lex:
        if tok and all(ch in SEPARATOR_CHARS for ch in tok):
            if current:
                yield current
            current = []
        else:
            current.append(tok)
    if current:
        yield current


def check_add(sub, args):
    if any(a in DRY_RUN for a in args):
        return None
    after_dashdash = False
    for a in args:
        if a == "--":
            after_dashdash = True
            continue
        if not after_dashdash and (a in ADD_ALL_LONG or (re.fullmatch(r"-[A-Za-z]+", a) and ("A" in a or "u" in a))):
            return "git %s %s" % (sub, a)
        if (after_dashdash or not a.startswith("-")) and a in ADD_ALL_PATHSPECS:
            return "git %s %s" % (sub, a)
    return None


def check_git(args):
    i = 0
    while i < len(args):
        a = args[i]
        if a in GIT_GLOBAL_OPTS_WITH_VALUE:
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            break
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1:]
    if sub in ADD_COMMANDS:
        hit = check_add(sub, rest)
        if hit:
            return "【git 守卫】已拦截 `%s`：不整片暂存。%s" % (hit, ADD_HINT)
    elif sub == "push":
        if not any(a in DRY_RUN for a in rest):
            return "【git 守卫】已拦截 `git push`：agent 不推送。%s" % PUSH_HINT
    return None


def check(cmd, depth=0):
    """返回拦截原因；放行返回 None。"""
    if depth > 3:
        return None
    for tokens in segments(cmd):
        i = 0
        while i < len(tokens) and (tokens[i] in LEADING_SKIP or ENV_ASSIGN_RE.match(tokens[i])):
            i += 1
        while i < len(tokens) and os.path.basename(tokens[i]) in WRAPPERS:
            i += 1
            while i < len(tokens) and (tokens[i].startswith("-") or ENV_ASSIGN_RE.match(tokens[i])):
                i += 1
        if i >= len(tokens):
            continue
        prog, args = os.path.basename(tokens[i]), tokens[i + 1:]
        if prog == "git":
            reason = check_git(args)
        elif prog in SHELLS and "-c" in args and args.index("-c") + 1 < len(args):
            reason = check(args[args.index("-c") + 1], depth + 1)
        elif prog == "eval" and args:
            reason = check(" ".join(args), depth + 1)
        else:
            reason = None
        if reason:
            return reason
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") != "Bash":
            return 0
        reason = check((payload.get("tool_input") or {}).get("command", ""))
    except Exception:  # 守卫自己出错时放行，不影响正常命令
        return 0
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
