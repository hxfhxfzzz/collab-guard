# collab-guard

让 AI 编程助手少翻车的四样小东西：两个守卫、一个验证脚本、一份规则模板。
规则写在文档里，模型会忘；能用脚本拦的就用脚本拦，拦不了的才写成规则。

只用 Python 3 标准库和 POSIX shell，没有第三方依赖。

## 解决什么问题

| 翻车现场 | 对应的东西 | 怎么起作用 |
| --- | --- | --- |
| `git add -A` 把别的会话、你自己没做完的改动一起提交了 | git 守卫 | Claude Code 执行命令前拦下 |
| agent 自作主张 `git push`，撤不回来 | git 守卫 | git 自己的 `pre-push` hook，任何 agent 都拦，人不受影响 |
| 需求还没对完，模型已经开始改代码 | 先确认再写的卡点 | 每个会话第一次改代码时拦一次，要求自查三条 |
| 「测试通过」其实是 0 个用例，或者是上一次的报告 | 验证脚本 `verify-run` | 把三条规则固化成退出码 |
| 每个客户端一份规则，改了这份忘了那份 | 规则模板 | 只维护一份 `AGENTS.md`，各客户端指向它 |

这些都是防误操作的护栏，不是安全边界。模型想绕总能绕过去，它们的作用是让「不小心」变成「得故意」。

## 环境

- macOS 或 Linux，`git`，`python3`（3.8 以上）。Windows 不支持。
- 目前只在 macOS、Python 3.14、git 2.49 上跑过测试，Linux 还没实测。

## 怎么装

```sh
git clone https://github.com/hxfhxfzzz/collab-guard.git
python3 collab-guard/install.py /path/to/your/project
```

安装做三件事，都只动项目本地：

1. 把脚本复制到 `<项目>/.claude/collab-guard/`。
2. 在 `<项目>/.claude/settings.json` 里登记 Claude Code 的三个 hook。只增加自己的条目，原有内容不动。
3. 把仓库本地配置里的 `core.hooksPath` 指到 `<项目>/.claude/collab-guard/git-hooks`。项目目录本身是 git 仓库就装它，否则装第一层的各个仓库。仓库原来的 hook（`.git/hooks`、husky 等）照常执行。

```sh
python3 collab-guard/install.py /path/to/your/project --status   # 看装没装
python3 collab-guard/install.py /path/to/your/project --remove   # 卸掉，恢复原样
```

| 选项 | 作用 |
| --- | --- |
| `--no-claude-hooks` | 不动 `.claude/settings.json`。不用 Claude Code 的加这个 |
| `--no-git-hooks` | 不改 `core.hooksPath` |
| `--with-templates` | 顺手放一份 `AGENTS.md` 和 `CLAUDE.md`，已有同名文件就不动 |

重复执行安装就是升级。Claude Code 要新开会话才会读到新登记的 hook。

`.claude/collab-guard/` 要不要提交进你的项目，自己定：提交了队友也能用；不想提交就加进 `.git/info/exclude`。
`core.hooksPath` 记的是绝对路径，项目挪了位置要重新装一次。

## 先看一遍效果

```sh
collab-guard/examples/demo.sh
```

它在系统临时目录建一个干净的示例项目，装上，再逐项演示：`git add -A` 被拦、agent 的 `git push` 被拦且远程确实没收到提交、第一次改代码被拦、0 个用例判不通过，最后卸载并删掉示例项目。不碰你的任何仓库和 git 配置。

## 四样东西

### 1. git 守卫

两层，拦的东西不一样：

| | Claude Code 的 hook（`hooks/git_guard.py`） | git 的 hook（`git-hooks/`） |
| --- | --- | --- |
| 对谁生效 | 只有 Claude Code | 所有 agent |
| `git add -A` / `--all` / `-u` / `.` / `*` | 拦 | 拦不到，git 对 add 没有 hook |
| `git push` | 拦 | 只拦 agent 的；人自己推不受影响 |
| `--dry-run` | 放行 | 放行 |
| `git commit -a` | 不拦，本地撤得回来 | 不拦 |

git 的 hook 怎么判断是 agent 在操作，满足一条就算：

1. 环境变量里有 `AI_AGENT`、`CLAUDECODE`、`CODEX_THREAD_ID`、`CODEX_TOOL_CALL_ID`、`CODEX_SANDBOX` 之一。
2. 进程祖先里有 agent 的程序名。默认名单：`claude`、`codex`、`gemini`、`opencode`、`aider`。
3. 连 `ps` 都不让执行。只有 agent 的沙箱会这样。

换名单（写了就整个替换默认名单）：

```sh
git config collabguard.agentNames "claude codex 其它名字"
```

想知道它这次是怎么判断的，设一个环境变量，判断依据会追加到这个文件：

```sh
COLLAB_GUARD_TRACE=/tmp/guard-trace.txt git push --dry-run
```

### 2. 先确认再写的卡点

`hooks/confirm_gate.py`，Claude Code 的 hook。本会话第一次用编辑工具改某个仓库的代码时拦一次，把三条自查反馈给模型：

1. 这次改动涉及的需求和口径都确认了吗？还在等回复的算没确认。
2. 用户明确说过可以开始改这部分代码吗？
3. 要改的内容在用户确认过的范围里吗？

不满足就停下来把问题列给用户；都满足就原样重试，本会话内这个仓库不再提醒。新开、续接、压缩上下文之后会重新提醒一次。

不拦的：文档（`.md` 等不在代码扩展名里的文件）、`docs/` 和各客户端的配置目录、项目目录以外的文件、Plan 模式。
要调整范围，改脚本开头的 `CODE_EXTS` 和 `DOC_PARTS`。

它只负责让模型停一下，不判断模型的自查对不对。

### 3. 验证脚本的三条规则

```sh
verify-run --report '<JUnit XML 报告的通配符>' -- <测试命令…>
```

```sh
.claude/collab-guard/bin/verify-run --report 'reports/junit.xml' -- pytest --junitxml=reports/junit.xml
.claude/collab-guard/bin/verify-run --report 'target/surefire-reports/TEST-*.xml' -- mvn -q test -Dtest=FooTest
```

| 规则 | 脚本怎么执行 |
| --- | --- |
| 1. 只认本次运行生成的证据 | 运行前就在、运行后没变的报告不算。运行期间工作区的源码被改了，结果作废 |
| 2. 0 个用例不算通过 | 按报告里实际执行的用例数算。全部跳过算 0，没有本次生成的报告也算 0 |
| 3. 跑不了就是「未验证」 | 命令不存在、参数不对，退出码是 2，不是 0 |

退出码：`0` 通过，`1` 不通过，`2` 未验证。写交付说明时照着写，`2` 不能写成「已通过」。

报告格式是 JUnit XML，pytest、Maven Surefire、Gradle、jest-junit、go-junit-report 等都能出。
「源码被改」靠 `git status` 前后对比，所以测试产生的临时文件要在 `.gitignore` 里，否则会被当成源码变化。不在 git 仓库里时这一项不检查，输出里会说明。

### 4. 多客户端共用的规则模板

`templates/AGENTS.md` 是一份带占位的规则正文，`templates/CLAUDE.md` 只有一行导入。做法：

- 规则只在 `AGENTS.md` 里维护一份。
- Claude Code：`CLAUDE.md` 里写 `@AGENTS.md`。
- Codex：直接读 `AGENTS.md`。
- 其他客户端：读 `AGENTS.md` 的不用管；不读的，在它自己的规则文件里只写一行指向 `AGENTS.md`。
- 读不了文件的客户端：粘贴模板里的短版。

模板里带〔〕的地方要自己填。前三样拦得住的事，模板里也写了一遍：守卫只是兜底，规则要让模型一开始就知道。

## 验证到什么程度

| 项 | 状态 |
| --- | --- |
| git hook 拦 agent 的 `git push` | 已验证：临时仓库里真实 push，环境变量和进程祖先两种依据都测到 |
| 人自己 push 不受影响 | 已验证：测试里用脱离 agent 进程树的子进程模拟 |
| 仓库原有 hook 照常执行、卸载后恢复 | 已验证：`.git/hooks` 和自定义 `hooksPath` 两种情况 |
| Claude Code 的两个 hook | 已验证：按 hook 的输入格式喂 JSON；另在一次真实的 Claude Code 会话里实测，`git add -A` 被拦且没有文件被暂存，第一次改代码被拦、原样重试放行 |
| Codex | 环境变量名取自 Codex 0.171.0 的实际环境；没有在真实 Codex 会话里跑过本仓库的守卫 |
| `gemini`、`opencode`、`aider` | 只按进程名判断，没有实测 |
| Linux | 没有实测 |

## 已知限制

- 都能绕过：`--no-verify`、`-c core.hooksPath=…`、换个写法、改用命令行写文件。
- `git add -A` 只有 Claude Code 拦得住，别的 agent 靠规则。
- 在 agent 客户端自带的终端里手动 `git push`，进程祖先里也有它的名字，会被当成 agent。换系统终端推。
- 编辑器类客户端没放进默认名单：它的内置终端也是它的子进程，放进去会误伤人。要拦自己加到 `collabguard.agentNames`。
- 沙箱里看不到是哪条 git 命令，`git push --dry-run` 也会被拦。
- 提示文字是中文的。

## 测试

```sh
python3 -m unittest discover -s tests
examples/demo.sh
```

测试自己在系统临时目录造仓库，不读也不改你的 git 配置。

## 许可

MIT，见 [LICENSE](LICENSE)。
