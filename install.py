#!/usr/bin/env python3
"""install.py：把 collab-guard 装进一个项目，或者卸掉、看状态。

用法：
  install.py <项目目录>                    装（重复执行就是升级，不会装出两份）
  install.py <项目目录> --status           只看装没装
  install.py <项目目录> --remove           卸掉，恢复原样
  可选：--no-claude-hooks   不动 .claude/settings.json（不用 Claude Code 的可以加）
        --no-git-hooks      不改仓库的 core.hooksPath
        --with-templates    顺手放一份规则模板（AGENTS.md、CLAUDE.md），已有同名文件就不动

装的时候做三件事：
  1. 把 hooks/、git-hooks/、bin/ 复制到 <项目目录>/.claude/collab-guard/。
  2. 在 <项目目录>/.claude/settings.json 里登记 Claude Code 的三个 hook（只增加自己的条目，别的不动）。
  3. 把 git 守卫装到仓库：项目目录本身是 git 仓库就装它，否则装第一层的各个仓库。
     只改仓库本地配置（.git/config，不进版本库）里的 core.hooksPath；仓库原来设过 hooksPath 的，
     记到 collabguard.prevHooksPath，守卫放行后照常执行原来的 hook。
     用的是绝对路径：项目目录挪了位置要重新装一次。worktree 和主目录共用一份配置，主目录装了就都生效。

退出码：0 都处理好了；1 有没处理成的；2 参数问题。
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys

SOURCE = os.path.dirname(os.path.realpath(__file__))
PARTS = ("hooks", "git-hooks", "bin")
INSTALL_REL = os.path.join(".claude", "collab-guard")
COMMAND_MARK = ".claude/collab-guard/"
PREV_KEY = "collabguard.prevHooksPath"
TEMPLATES = ("AGENTS.md", "CLAUDE.md")


def hook_command(script):
    return 'python3 "$CLAUDE_PROJECT_DIR/.claude/collab-guard/hooks/%s"' % script


# (事件, matcher, 脚本)
CLAUDE_HOOKS = (
    ("SessionStart", None, "confirm_gate.py"),
    ("PreToolUse", "Bash", "git_guard.py"),
    ("PreToolUse", "Edit|Write|MultiEdit|NotebookEdit", "confirm_gate.py"),
)


# ---------- 复制文件 ----------

def copy_files(project):
    target = os.path.join(project, INSTALL_REL)
    for part in PARTS:
        dest = os.path.join(target, part)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(os.path.join(SOURCE, part), dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in os.listdir(dest):
            path = os.path.join(dest, name)
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


# ---------- .claude/settings.json ----------

def load_settings(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("最外层不是对象")
    return data


def save_settings(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def is_ours(hook):
    return isinstance(hook, dict) and COMMAND_MARK in str(hook.get("command", ""))


def registered(data):
    """已登记的 (事件, matcher, 命令) 集合，只算自己的条目。"""
    found = set()
    hooks = data.get("hooks")
    for event, groups in (hooks.items() if isinstance(hooks, dict) else ()):
        for group in groups if isinstance(groups, list) else ():
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks") or ():
                if is_ours(hook):
                    found.add((event, group.get("matcher"), hook.get("command")))
    return found


def add_claude_hooks(data):
    have, added = registered(data), 0
    for event, matcher, script in CLAUDE_HOOKS:
        command = hook_command(script)
        if (event, matcher, command) in have:
            continue
        group = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
        if matcher:
            group = {"matcher": matcher, **group}
        data.setdefault("hooks", {}).setdefault(event, []).append(group)
        added += 1
    return added


def drop_claude_hooks(data):
    removed = 0
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                kept = [h for h in group["hooks"] if not is_ours(h)]
                removed += len(group["hooks"]) - len(kept)
                if not kept:
                    continue
                group["hooks"] = kept
            kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        del data["hooks"]
    return removed


# ---------- git 守卫 ----------

def is_repo(path):
    return os.path.exists(os.path.join(path, ".git"))


def find_repos(project):
    if is_repo(project):
        return [project]
    return sorted(os.path.join(project, d) for d in os.listdir(project)
                  if not d.startswith(".") and os.path.isdir(os.path.join(project, d, ".git")))


def config(repo, *args):
    return subprocess.run(["git", "-C", repo, "config", "--local", *args], capture_output=True, text=True)


def get(repo, key):
    return config(repo, "--get", key).stdout.strip()


def guard_state(repo, hooks_dir):
    current = get(repo, "core.hooksPath")
    resolved = current if os.path.isabs(current) else os.path.join(repo, current)
    if current and os.path.realpath(resolved) == os.path.realpath(hooks_dir):
        return "installed", current
    return ("other" if current else "none"), current


def last_line(result):
    return (result.stderr.strip().splitlines() or ["git config 失败"])[-1]


def guard_install(repo, hooks_dir):
    kind, current = guard_state(repo, hooks_dir)
    if kind == "installed":
        return True, "已装，没动"
    if current and config(repo, PREV_KEY, current).returncode != 0:
        return False, "没装：记不下原来的 hooksPath（%s）" % current
    done = config(repo, "core.hooksPath", hooks_dir)
    if done.returncode != 0:
        return False, "没装：%s" % last_line(done)
    return True, "装好了" + ("（原来的 hooksPath=%s 已记下，守卫放行后照常执行）" % current if current else "")


def guard_remove(repo, hooks_dir):
    kind, _ = guard_state(repo, hooks_dir)
    if kind != "installed":
        return True, "本来就没装，没动"
    prev = get(repo, PREV_KEY)
    done = config(repo, "core.hooksPath", prev) if prev else config(repo, "--unset", "core.hooksPath")
    if done.returncode != 0:
        return False, "没卸掉：%s" % last_line(done)
    if prev:
        config(repo, "--unset", PREV_KEY)
    return True, "已卸" + ("，hooksPath 恢复为 %s" % prev if prev else "")


def guard_status(repo, hooks_dir):
    kind, current = guard_state(repo, hooks_dir)
    prev = get(repo, PREV_KEY)
    if kind == "installed":
        return True, "已装" + ("（原 hooksPath=%s）" % prev if prev else "")
    return True, "未装" + ("（现在的 hooksPath=%s）" % current if current else "")


# ---------- 三个动作 ----------

def line(ok, label, text):
    print("  %s %-24s %s" % ("✅" if ok else "❌", label, text))
    return ok


def for_repos(project, hooks_dir, action):
    repos = find_repos(project)
    if not repos:
        return line(True, "git 守卫", "项目目录里没找到 git 仓库，跳过")
    ok_all = True
    for repo in repos:
        ok, text = action(repo, hooks_dir)
        label = "git 守卫 · " + (os.path.basename(repo) if repo != project else "本仓库")
        ok_all = line(ok, label, text) and ok_all
    return ok_all


def do_install(project, args):
    ok_all = True
    target = copy_files(project)
    line(True, "脚本", "已复制到 %s" % os.path.relpath(target, project))
    settings_path = os.path.join(project, ".claude", "settings.json")
    if not args.no_claude_hooks:
        try:
            data = load_settings(settings_path)
            added = add_claude_hooks(data)
            if added:
                save_settings(settings_path, data)
            line(True, "Claude Code hook", "登记了 %d 条" % added if added else "已登记，没动")
        except (ValueError, OSError) as exc:
            ok_all = line(False, "Claude Code hook", "没登记：.claude/settings.json 读不了（%s），请先修好再装" % exc)
    if not args.no_git_hooks:
        ok_all = for_repos(project, os.path.join(target, "git-hooks"), guard_install) and ok_all
    if args.with_templates:
        for name in TEMPLATES:
            dest = os.path.join(project, name)
            if os.path.exists(dest):
                line(True, "模板 " + name, "已有同名文件，没动")
            else:
                shutil.copyfile(os.path.join(SOURCE, "templates", name), dest)
                line(True, "模板 " + name, "已放到项目根目录，里面的〔〕要自己填")
    return ok_all


def do_remove(project, args):
    ok_all = True
    target = os.path.join(project, INSTALL_REL)
    ok_all = for_repos(project, os.path.join(target, "git-hooks"), guard_remove) and ok_all
    settings_path = os.path.join(project, ".claude", "settings.json")
    try:
        data = load_settings(settings_path)
        removed = drop_claude_hooks(data)
        if removed:
            save_settings(settings_path, data)
        line(True, "Claude Code hook", "去掉了 %d 条" % removed if removed else "本来就没登记，没动")
    except (ValueError, OSError) as exc:
        ok_all = line(False, "Claude Code hook", "没处理：.claude/settings.json 读不了（%s）" % exc)
    if os.path.isdir(target):
        shutil.rmtree(target)
        line(True, "脚本", "已删除 %s" % os.path.relpath(target, project))
    else:
        line(True, "脚本", "本来就没有，没动")
    return ok_all


def do_status(project, args):
    target = os.path.join(project, INSTALL_REL)
    missing = [p for p in PARTS if not os.path.isdir(os.path.join(target, p))]
    line(not missing, "脚本", "在 %s" % os.path.relpath(target, project) if not missing else "没装（缺 %s）" % "、".join(missing))
    try:
        have = {(e, m) for e, m, _ in registered(load_settings(os.path.join(project, ".claude", "settings.json")))}
        lacking = [e + ("(%s)" % m if m else "") for e, m, _ in CLAUDE_HOOKS if (e, m) not in have]
        line(not lacking, "Claude Code hook", "三条都登记了" if not lacking else "没登记：%s" % "、".join(lacking))
    except (ValueError, OSError) as exc:
        line(False, "Claude Code hook", ".claude/settings.json 读不了（%s）" % exc)
    for_repos(project, os.path.join(target, "git-hooks"), guard_status)
    return True


def main():
    ap = argparse.ArgumentParser(usage="install.py <项目目录> [--status | --remove] [--no-claude-hooks] [--no-git-hooks] [--with-templates]")
    ap.add_argument("project")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--no-claude-hooks", action="store_true")
    ap.add_argument("--no-git-hooks", action="store_true")
    ap.add_argument("--with-templates", action="store_true")
    args = ap.parse_args()
    project = os.path.realpath(args.project)
    if args.status and args.remove:
        print("--status 和 --remove 只能写一个")
        return 2
    if not os.path.isdir(project):
        print("项目目录不存在：%s" % args.project)
        return 2
    if project == SOURCE or project.startswith(SOURCE + os.sep):
        print("不能装到 collab-guard 自己的目录里")
        return 2
    action, title = (do_status, "状态") if args.status else (do_remove, "卸载") if args.remove else (do_install, "安装")
    print("【collab-guard】%s · %s" % (title, project))
    return 0 if action(project, args) else 1


if __name__ == "__main__":
    sys.exit(main())
