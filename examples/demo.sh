#!/bin/sh
# 一键演示：在系统临时目录建一个干净的示例项目，装上 collab-guard，逐项看是不是真能拦住。
# 用法：examples/demo.sh         跑完自动删掉示例项目
#       examples/demo.sh --keep  保留示例项目，结尾打印它的路径
# 退出码：0 每一项的结果都和预期一致；1 有不一致的。
set -u

here=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/collab-guard-demo.XXXXXX")
demo="$work/demo-project"
keep=${1:-}
bad=0

cleanup() { [ "$keep" = "--keep" ] || rm -rf "$work"; }
trap cleanup EXIT

# 不读、不改你自己的 git 配置；卡点的标记也写在示例目录里
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 TMPDIR="$work"
unset AI_AGENT CLAUDECODE CODEX_THREAD_ID CODEX_TOOL_CALL_ID CODEX_SANDBOX

title() { printf '\n== %s\n' "$1"; }

# expect <预期退出码> <说明> <命令…>：跑命令，缩进打印输出，核对退出码
expect() {
    want=$1; what=$2; shift 2
    out=$("$@" 2>&1); got=$?
    [ -n "$out" ] && printf '%s\n' "$out" | sed 's/^/    | /'
    if [ "$got" = "$want" ]; then
        printf '  ✅ %s（退出码 %s）\n' "$what" "$got"
    else
        printf '  ❌ %s：预期退出码 %s，实际 %s\n' "$what" "$want" "$got"; bad=1
    fi
}

# 按 Claude Code 传给 hook 的格式拼一段 JSON
bash_payload() { python3 -c 'import json,sys; print(json.dumps({"hook_event_name":"PreToolUse","session_id":"demo","tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$1"; }
edit_payload() { python3 -c 'import json,sys; print(json.dumps({"hook_event_name":"PreToolUse","session_id":"demo","tool_name":"Edit","tool_input":{"file_path":sys.argv[1]}}))' "$1"; }
bash_hook() { bash_payload "$1" | CLAUDE_PROJECT_DIR="$demo" python3 "$demo/.claude/collab-guard/hooks/git_guard.py"; }
edit_hook() { edit_payload "$1" | CLAUDE_PROJECT_DIR="$demo" python3 "$demo/.claude/collab-guard/hooks/confirm_gate.py"; }
agent_push() { CLAUDECODE=1 git -C "$demo" push origin main; }
remote_is_empty() { ! git -C "$work/remote.git" rev-parse -q --verify refs/heads/main >/dev/null; }

title "0. 建示例项目并安装"
mkdir -p "$demo"
git -C "$demo" init -q -b main
printf 'def add(a, b):\n    return a + b\n' > "$demo/app.py"
printf 'reports/\n' > "$demo/.gitignore"
git -C "$demo" add -- app.py .gitignore
git -C "$demo" -c user.name=demo -c user.email=demo@example.invalid commit -q -m "init"
git init -q --bare -b main "$work/remote.git"
git -C "$demo" remote add origin "$work/remote.git"
expect 0 "安装" python3 "$here/install.py" "$demo"

title "1. git 守卫（Claude Code 的 hook）：整片暂存和推送"
expect 2 "git add -A 被拦" bash_hook "git add -A"
expect 2 "git push 被拦" bash_hook "cd src && git push origin main"
expect 0 "git add -- app.py 放行" bash_hook "git add -- app.py"

title "2. git 守卫（git 自己的 hook）：agent 真的去 push"
expect 1 "带 agent 标记的 git push 被拦" agent_push
expect 0 "远程仓库确实没收到任何提交" remote_is_empty

title "3. 先确认再写：本会话第一次改代码"
expect 2 "第一次编辑 app.py 被拦，给出三条自查" edit_hook "$demo/app.py"
expect 0 "自查通过后原样重试，放行" edit_hook "$demo/app.py"
expect 0 "改文档不拦" edit_hook "$demo/README.md"

title "4. 验证脚本的三条规则"
verify="$demo/.claude/collab-guard/bin/verify-run"
write_report='import os,sys; os.makedirs("reports", exist_ok=True); open("reports/junit.xml","w").write(sys.argv[1])'
two_tests='<testsuite name="demo" tests="2"><testcase name="a"/><testcase name="b"/></testsuite>'
no_tests='<testsuite name="demo" tests="0"></testsuite>'
expect 0 "2 个用例都过：通过" "$verify" --cwd "$demo" --report 'reports/*.xml' -- python3 -c "$write_report" "$two_tests"
expect 1 "规则 1：命令成功，但报告是上一次留下的：不通过" "$verify" --cwd "$demo" --report 'reports/*.xml' -- python3 -c "print('BUILD SUCCESS')"
expect 1 "规则 2：命令成功，但执行了 0 个用例：不通过" "$verify" --cwd "$demo" --report 'reports/*.xml' -- python3 -c "$write_report" "$no_tests"
expect 2 "规则 3：测试命令不存在：未验证" "$verify" --cwd "$demo" --report 'reports/*.xml' -- no-such-test-runner

title "5. 卸载"
expect 0 "卸载" python3 "$here/install.py" "$demo" --remove

printf '\n'
[ "$keep" = "--keep" ] && printf '示例项目留在：%s\n' "$demo"
if [ "$bad" = 0 ]; then
    printf '结论：每一项都和预期一致 ✅\n'
else
    printf '结论：有和预期不一致的项 ❌\n'
fi
exit "$bad"
