#!/usr/bin/env python3
"""「先确认再写」卡点（Claude Code hook）：本会话第一次改某个仓库的代码时，先拦一次，让模型自查三条。

接两个事件：
- PreToolUse(Edit|Write|MultiEdit|NotebookEdit)：目标是项目里的代码文件，且本会话还没对这个仓库提醒过
  → 退出码 2 拦下这一次，stderr 把自查清单反馈给模型，并记一个标记；之后同一仓库直接放行。
  仓库按文件所在的 git 仓库根目录区分；项目里有多个仓库（或多个 worktree）时各拦一次。
  文件不在任何 git 仓库里时，整个项目目录算一个。
- SessionStart（新开 / 续接 / 压缩后）：清掉本会话的标记，让卡点重新生效。
  压缩之后最容易忘掉还有哪些没确认，所以要再提醒一次。

不拦的：项目目录以外的文件、不是代码的文件（按 CODE_EXTS 判断）、文档和工具目录（DOC_PARTS）、
Plan 模式（本来就改不了文件）。

它只提醒一次，不判断模型的自查对不对：这是让模型停一下的卡点，不是权限系统。
hook 自己出错时一律放行，不卡编辑。要调整范围，改下面三个常量。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

CODE_EXTS = (
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".html", ".css", ".scss", ".less",
    ".java", ".kt", ".scala", ".groovy", ".gradle", ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs",
    ".rb", ".php", ".swift", ".m", ".dart", ".lua", ".sh", ".bash", ".zsh", ".sql", ".ipynb",
    ".json", ".xml", ".yml", ".yaml", ".toml", ".ini", ".properties",
)
# 路径里带这些目录的不拦：文档，以及各客户端自己的配置目录
DOC_PARTS = ("docs/", "doc/", ".claude/", ".cursor/", ".codex/")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
MARK_DIR = os.path.join(tempfile.gettempdir(), "collab-guard-confirm-gate")

MESSAGE = """【先确认再写】本会话第一次改 {scope} 的代码（{rel}）。这一次编辑已拦下，先自查三条：
1. 这次改动涉及的需求和口径都确认了吗？还在等用户或需求方回复的问题，算没确认。
2. 用户明确说过可以开始改这部分代码吗？解释、分析、评审这类请求默认只读。
3. 要改的内容在用户确认过的范围里吗？
任何一条不满足：不要重试，也不要改用命令行写文件。停下来把没闭环的点列给用户，
每个问题不超过两句，并附上「我建议 X，因为 Y」。
三条都满足：原样重试这次编辑即可，本会话内 {scope} 不再提醒（压缩或续接后会再提醒一次）。"""


def safe(text):
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)[-80:]


def marker(session_id, scope):
    return os.path.join(MARK_DIR, "%s__%s" % (safe(session_id), safe(scope)))


def reset(session_id):
    prefix = safe(session_id) + "__"
    if os.path.isdir(MARK_DIR):
        for name in os.listdir(MARK_DIR):
            if name.startswith(prefix):
                os.remove(os.path.join(MARK_DIR, name))


def inside(path, root):
    return path == root or path.startswith(root + os.sep)


def repo_of(path):
    """文件所在 git 仓库的根目录；文件还不存在时从最近的已有上级目录找。不在仓库里返回 None。"""
    directory = os.path.dirname(path)
    while directory and not os.path.isdir(directory):
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent
    p = subprocess.run(["git", "--no-optional-locks", "-C", directory, "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, timeout=5)
    return os.path.realpath(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None


def gate(payload, project):
    """返回要反馈的文字；放行返回 None。"""
    if payload.get("permission_mode") == "plan":
        return None
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path or not project:
        return None
    project = os.path.realpath(project)
    path = os.path.realpath(file_path if os.path.isabs(file_path) else os.path.join(project, file_path))
    if not path.lower().endswith(CODE_EXTS) or not inside(path, project):
        return None
    repo = repo_of(path)
    root = repo if repo and inside(repo, project) else project
    rel = os.path.relpath(path, root)
    if any(("/" + rel.replace(os.sep, "/")).find("/" + part) >= 0 for part in DOC_PARTS):
        return None
    scope = os.path.basename(project) if root == project else os.path.relpath(root, project)
    mark = marker(payload.get("session_id") or "unknown", root)
    if os.path.exists(mark):
        return None
    os.makedirs(MARK_DIR, exist_ok=True)
    with open(mark, "w", encoding="utf-8") as fh:
        fh.write(rel + "\n")
    return MESSAGE.format(scope=scope, rel=rel)


def main():
    try:
        payload = json.load(sys.stdin)
        event = payload.get("hook_event_name")
        if event == "SessionStart":
            reset(payload.get("session_id") or "unknown")
            return 0
        if event != "PreToolUse" or payload.get("tool_name") not in EDIT_TOOLS:
            return 0
        message = gate(payload, os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd"))
    except Exception:  # 卡点自己出错时放行，不影响正常编辑
        return 0
    if message:
        print(message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
