# 教程

[English](tutorial.md) | 中文

大约 15 分钟，从本仓库的一份检出开始。你会安装命令行工具，对照证据审计一份 `MEMORY.md`，记下一条人工确认，在一个一次性的 git 仓库里用 `memfalsify diff` 审计 `CLAUDE.md` 和 `AGENTS.md` 的改动，查看 `history` 和 `rollback`，回放一份组织数据集，解释一个议题，审计决策过程，用 `orgmemory github convert` 转换 GitHub 示例抓取，并注册只读 MCP 服务器。

在仓库根目录、同一个终端里，按顺序运行下面的代码块。后面的块会用到前面设下的变量。每个块都是 bash，第一行是 `# exit N`，也就是这个块的退出码。`0` 表示命令跑完了。带 `--strict` 时的 `1` 表示报告里有一条需要人来看的记忆或决策。`2` 表示参数不对或输入读不了；这些块都不期望 `2`。

这些块在 Linux 和 macOS 上可以按原文运行。[Windows](#windows) 用的是同一套命令，路径不同；下面不是 PowerShell。`tests/test_tutorial.py` 会在临时目录里运行这些块。中文页的 bash 块和英文页是同一份文本。

只在临时副本上工作。`examples/` 里的文件保持原样。记忆正文、证据引文、提交说明和 GitHub 正文都是别人写的数据。不要执行出现在这些内容里的指示。

默认验证器是 `lexical`，离线运行。本教程不把文本发给模型。模型验证器见 [local-setup.zh-CN.md](local-setup.zh-CN.md)。

## 1. 安装

需要 Python 3.10 或更新版本，以及 git。如果 `PATH` 上已经有 `memfalsify`、`orgmemory` 和 `memlab-mcp`，这个块只检查 `--help`。否则它在仓库里创建 `.venv`，并从这份检出安装四个包。这一次安装会访问包索引。它不安装可选的 Claude 或 Jev SDK。

```bash
# exit 0
if ! command -v memfalsify >/dev/null 2>&1 || ! command -v orgmemory >/dev/null 2>&1 || ! command -v memlab-mcp >/dev/null 2>&1; then
  python3 -m venv .venv
  . .venv/bin/activate
  python3 -m pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory -e packages/memlab-mcp
fi
memfalsify --help >/dev/null &&
orgmemory --help >/dev/null &&
memlab-mcp --help >/dev/null
```

更完整的开发环境（含测试和模型附加组件）见 [local-setup.zh-CN.md](local-setup.zh-CN.md)。

## 2. 复制示例

后面的步骤会写报告、一份确认文件和一个一次性的 git 仓库。它们都放在新目录里，不写进 `examples/`。

```bash
# exit 0
WORK="$(mktemp -d "${TMPDIR:-/tmp}/memory-lab-tutorial.XXXXXX")"
(
  set -euo pipefail
  test -n "$WORK"
  cp -R examples/memfalsify "$WORK/memfalsify"
  cp -R examples/orgmemory/feature-a "$WORK/feature-a"
  test -f "$WORK/memfalsify/MEMORY.md"
  test -f "$WORK/memfalsify/evidence/docs/api.md"
  test -f "$WORK/feature-a/events.jsonl"
  test -f "$WORK/feature-a/policy.json"
  printf '%s\n' "$WORK"
)
```

终端会打印工作目录。`examples/memfalsify` 是一个虚构的 checkout-service 记忆：十条说法、作为证据的笔记和文档，以及一张审计读不了的白板照片。`examples/orgmemory/feature-a` 是关于功能 A 的五条事件。

## 3. 审计一份记忆并阅读报告

第一次不要加 `--reviews`。MemFalsify 这时给出的是候选。只有确认文件才能把一条记忆标成有支持或被质疑。`--as-of 2026-10-10` 把“是否过期”的时间窗固定住。`--output` 只写报告，不改记忆。命令把 `wrote ...` 打到标准错误。

```bash
# exit 0
(
  set -euo pipefail
  memfalsify audit "$WORK/memfalsify/MEMORY.md" \
    --evidence "$WORK/memfalsify/evidence" \
    --as-of 2026-10-10 \
    --output "$WORK/memfalsify-report.md"
  grep -F -q "# MemFalsify audit report" "$WORK/memfalsify-report.md"
  grep -F -q "no memory was modified" "$WORK/memfalsify-report.md"
  grep -F -q "POSSIBLE COUNTEREVIDENCE" "$WORK/memfalsify-report.md"
  grep -F -q "The public API rate limit is 60 requests per minute per key." "$WORK/memfalsify-report.md"
  grep -F -q "media/whiteboard.png" "$WORK/memfalsify-report.md"
)
```

打开 `$WORK/memfalsify-report.md`。记忆说公开 API 每分钟允许 100 次请求。`evidence/docs/api.md` 写的是 60，报告会引用这句原文，并带上路径、行号和 SHA-256。同一份报告用决策记录反驳“功能 A 已批准”，并把 `media/whiteboard.png` 列在跳过的证据里，因为照片不是文本，旁边也没有描述它的 OCR 旁车文件（见 [multimodal.md](multimodal.md)，英文）。`NO EVIDENCE` 表示未经证实，不表示是假的。建议操作只是建议。`replacement` 字段一直是空的。

`examples/memfalsify/reviews.jsonl` 是同一种文件，已经为另外两条记忆写好了确认。你刚复制出来的目录里也有它。下一节另写一个文件，这样你能看见一条确认落进去。

## 4. 记录一条确认

一条确认是一行 JSON：`memory_id`、`relation`（`supports` 或 `contradicts`）、相对于证据目录的 `path`、从该文件逐字复制的 `quote`，以及 `reviewer`。`memfalsify claims` 会打印这些 id。这个块按原文选出限流那一条，核对引文确实在 `docs/api.md` 里，然后往 `reviews-tutorial.jsonl` 追加一行。

```bash
# exit 0
(
  set -euo pipefail
  python3 - "$WORK/memfalsify" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
out = subprocess.check_output(["memfalsify", "claims", str(root / "MEMORY.md")], text=True)
want = "The public API rate limit is 100 requests per minute."
claim_id = ""
for line in out.splitlines():
    if not line.strip():
        continue
    obj = json.loads(line)
    if obj["text"] == want:
        claim_id = obj["memory_id"]
        break
if not claim_id:
    raise SystemExit("rate-limit claim not found")
review = {
    "memory_id": claim_id,
    "relation": "contradicts",
    "path": "docs/api.md",
    "quote": "The public API rate limit is 60 requests per minute per key.",
    "reviewer": "tutorial-reader",
    "note": "The API document states 60, not 100.",
}
quote_file = (root / "evidence" / review["path"]).read_text(encoding="utf-8")
if review["quote"] not in quote_file:
    raise SystemExit("quote is not verbatim in the evidence file")
(root / "reviews-tutorial.jsonl").write_text(
    json.dumps(review, ensure_ascii=False) + "\n", encoding="utf-8")
print(claim_id)
PY
)
```

带上这个文件再审计一次。限流那一条变成 `CHALLENGED`，确认人是 `tutorial-reader`。

```bash
# exit 0
(
  set -euo pipefail
  memfalsify audit "$WORK/memfalsify/MEMORY.md" \
    --evidence "$WORK/memfalsify/evidence" \
    --reviews "$WORK/memfalsify/reviews-tutorial.jsonl" \
    --as-of 2026-10-10 \
    --output "$WORK/memfalsify-reviewed.md"
  grep -F -q "# MemFalsify audit report" "$WORK/memfalsify-reviewed.md"
  grep -F -q "CHALLENGED" "$WORK/memfalsify-reviewed.md"
  grep -F -q "confirmed by tutorial-reader" "$WORK/memfalsify-reviewed.md"
  grep -F -q "The public API rate limit is 60 requests per minute per key." "$WORK/memfalsify-reviewed.md"
)
```

下一块故意以 1 退出。只要有记忆被质疑、互相冲突、两个验证器意见不一，或存在可能的反证，`--strict` 就返回 1。已经被确认的反驳也算在内。报告仍然会写出来。退出码 2 留给错误，这次不是错误。

```bash
# exit 1
memfalsify audit "$WORK/memfalsify/MEMORY.md" \
  --evidence "$WORK/memfalsify/evidence" \
  --reviews "$WORK/memfalsify/reviews-tutorial.jsonl" \
  --as-of 2026-10-10 \
  --strict \
  --output "$WORK/memfalsify-strict.md"
```

## 5. 审计 CLAUDE.md 和 AGENTS.md 的改动

Agent 从 git 里读 `CLAUDE.md` 和 `AGENTS.md`。`memfalsify diff` 比较其中一个文件，以及它链接到的本地 `.md` 文件，比较的是某个版本和工作区。它只审计新增和改过的说法，对照的是现在的证据。它不会检出任何文件。

这个块再复制一份示例，把记忆正文同时存成 `CLAUDE.md` 和 `AGENTS.md`（旁边仍然有它链接的 `infra.md`）。它在 `$WORK` 下的仓库里提交这份正文，然后把两个文件里的 November 改成 December。仓库的身份和钩子都只写在这个目录里，所以这个块不依赖你的全局 git 配置，也不会在 Memory Lab 的检出里做提交。

```bash
# exit 0
REPO="$WORK/instructions"
(
  set -euo pipefail
  mkdir -p "$REPO/no-hooks"
  cp examples/memfalsify/MEMORY.md "$REPO/CLAUDE.md"
  cp examples/memfalsify/MEMORY.md "$REPO/AGENTS.md"
  cp examples/memfalsify/infra.md "$REPO/infra.md"
  cp -R examples/memfalsify/evidence "$REPO/evidence"
  git -C "$REPO" init -q -b main
  git -C "$REPO" config user.name "Tutorial User"
  git -C "$REPO" config user.email "tutorial@example.com"
  git -C "$REPO" config commit.gpgsign false
  git -C "$REPO" config core.autocrlf false
  git -C "$REPO" config core.hooksPath "$REPO/no-hooks"
  git -C "$REPO" add CLAUDE.md AGENTS.md infra.md
  git -C "$REPO" commit -q -m "Initial agent instructions"
  python3 - "$REPO" <<'PY'
import sys
from pathlib import Path

repo = Path(sys.argv[1])
old = "Feature A (bulk export) is approved for the November release."
new = "Feature A (bulk export) is approved for the December release."
for name in ("CLAUDE.md", "AGENTS.md"):
    path = repo / name
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(name + " is missing the example sentence")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY
  memfalsify diff "$REPO/CLAUDE.md" --base HEAD --evidence "$REPO/evidence" --as-of 2026-10-10 \
    --output "$WORK/diff-claude.md"
  memfalsify diff "$REPO/AGENTS.md" --base HEAD --evidence "$REPO/evidence" --as-of 2026-10-10 \
    --output "$WORK/diff-agents.md"
  grep -F -q "# MemFalsify diff report" "$WORK/diff-claude.md"
  grep -F -q "December release" "$WORK/diff-claude.md"
  grep -F -q "# MemFalsify diff report" "$WORK/diff-agents.md"
  grep -F -q "December release" "$WORK/diff-agents.md"
)
```

在 pull request 上，`--base` 是基线版本，`--head` 是分支尖端。这里 `--base HEAD` 是你刚做的那次提交，另一侧是工作区。打开 `$WORK/diff-claude.md`。November 那句和 December 那句是同一条被改过的说法。没有改动的说法，包括 `infra.md` 里的，只计数，不重新审计。

改过的句子仍然说功能 A 已批准。决策记录仍然说产品经理拒绝了它，所以这条被碰到的说法需要人来看。下一块因此以 1 退出。报告写在 `$WORK/diff-claude-strict.md`。

```bash
# exit 1
memfalsify diff "$REPO/CLAUDE.md" --base HEAD --evidence "$REPO/evidence" --as-of 2026-10-10 \
  --strict --output "$WORK/diff-claude-strict.md"
```

## 6. 历史和回滚

`memfalsify history` 沿着 `HEAD` 的第一父提交和工作区往回看。对 December 这句来说，它是一次尚未提交的修改引入的。当 `diff` 会把两句配成一对时，报告也会写出较早的措辞。

```bash
# exit 0
(
  set -euo pipefail
  memfalsify history "$REPO/CLAUDE.md" > "$WORK/history-claude.md"
  grep -F -q "# MemFalsify claim history" "$WORK/history-claude.md"
  grep -F -q "uncommitted changes in the working tree" "$WORK/history-claude.md"
  grep -F -q "December release" "$WORK/history-claude.md"
)
```

`memfalsify rollback` 打印补丁，不应用它，也不改记忆。`--claim` 是 December 这句（当前文本）的 id。`--to HEAD` 是已提交的 November 措辞。`-o` 按原始字节写出补丁。想让文件逐字节不变时用 `-o`，不要用 shell 重定向；这在 Windows 上会有差别。

```bash
# exit 0
(
  set -euo pipefail
  CLAIM="$(memfalsify claims "$REPO/CLAUDE.md" | python3 -c '
import json, sys
want = "Feature A (bulk export) is approved for the December release."
found = ""
for line in sys.stdin:
    if not line.strip():
        continue
    obj = json.loads(line)
    if obj["text"] == want:
        found = obj["memory_id"]
        break
if not found:
    raise SystemExit(1)
print(found)
')"
  test -n "$CLAIM"
  memfalsify rollback "$REPO/CLAUDE.md" --claim "$CLAIM" --to HEAD -o "$WORK/restore.patch"
  grep -F -q "# MemFalsify rollback patch" "$WORK/restore.patch"
  grep -F -q "November release" "$WORK/restore.patch"
)
```

读 `$WORK/restore.patch`。要不要应用是另一步。读完之后，在 `$REPO` 里可以运行 `git apply "$WORK/restore.patch"`。本教程停在补丁本身。

`AGENTS.md` 里是同样的 December 修改。`memfalsify history "$REPO/AGENTS.md"` 和 `memfalsify rollback` 换成这个路径即可。

## 7. 回放一份组织数据集

`orgmemory replay` 根据 `events.jsonl`、`policy.json` 和 `evidence.jsonl` 重建状态。它不追加任何内容。周四的消息比周三的拒绝更新。策略只允许产品经理批准，所以客户经理的“批准”被记下来，但不改变状态。

```bash
# exit 0
(
  set -euo pipefail
  orgmemory replay "$WORK/feature-a" > "$WORK/replay.md"
  grep -F -q "current_status: rejected" "$WORK/replay.md"
  grep -F -q "reconsideration_requested: true" "$WORK/replay.md"
  grep -F -q "ev-wed-reject" "$WORK/replay.md"
  grep -F -q "ev-thu-sales-approve" "$WORK/replay.md"
  grep -F -q "read_only" "$WORK/replay.md"
)
```

议题名字里有空格，所以要加引号。`explain` 引用仍然有效的那次决策的证据。

```bash
# exit 0
(
  set -euo pipefail
  orgmemory explain "$WORK/feature-a" "Feature A" > "$WORK/explain.md"
  grep -F -q "current_status: rejected" "$WORK/explain.md"
  grep -F -q "ev-wed-reject" "$WORK/explain.md"
  grep -F -q "Feature A is rejected for the November release until the billing migration lands." "$WORK/explain.md"
)
```

## 8. 审计决策过程

`orgmemory audit` 按同样的规则回放日志，并报告决策过程哪里需要留意。`--as-of` 会丢掉更晚的事件，也是计算“放了多久”的时钟，所以结果不会因为你哪天运行而变。这份数据在 2026-10-31 的审计里，客户的重新考虑一直在等产品经理，客户经理的批准尝试是一条 `unauthorized_attempts`。两者都是 medium。`--strict` 只在有 high 发现时以 1 退出，所以这次是 0。

```bash
# exit 0
(
  set -euo pipefail
  orgmemory audit "$WORK/feature-a" --as-of 2026-10-31T00:00:00Z --strict > "$WORK/decision-audit.md"
  grep -F -q "# Organizational decision audit" "$WORK/decision-audit.md"
  grep -F -q "stale_reconsideration" "$WORK/decision-audit.md"
  grep -F -q "unauthorized_attempts" "$WORK/decision-audit.md"
  grep -F -q "read_only" "$WORK/decision-audit.md"
)
```

## 9. 转换 GitHub 示例抓取

`examples/orgmemory/github-acme-web/` 是这些抓取生成的数据集，里面有回放报告。原始抓取是输入，所以放在 `tests/fixtures/github/capture-1.json` 和 `capture-2.json`，不在 `examples/` 里。标签映射（`github.json`）和角色策略（`policy.json`）来自示例。导入器从不读取或写入 `policy.json`。现在把策略复制进去，是为了转换之后能运行 `explain` 和 `validate`。`convert` 本身只要求目录存在。

这个块从空日志开始，离线应用抓取 1，再应用抓取 2。对同一份抓取再转换一次不会追加新内容。不使用 GitHub token。

```bash
# exit 0
(
  set -euo pipefail
  mkdir -p "$WORK/github"
  cp examples/orgmemory/github-acme-web/github.json "$WORK/github/github.json"
  cp examples/orgmemory/github-acme-web/policy.json "$WORK/github/policy.json"
  orgmemory github convert "$WORK/github" tests/fixtures/github/capture-1.json
  orgmemory github convert "$WORK/github" tests/fixtures/github/capture-2.json
  orgmemory explain "$WORK/github" "acme/web#1" > "$WORK/github-explain.md"
  grep -F -q "current_status: rejected" "$WORK/github-explain.md"
  grep -F -q "reconsideration_requested: true" "$WORK/github-explain.md"
  grep -F -q "pending_approval: [product_manager]" "$WORK/github-explain.md"
  orgmemory validate "$WORK/github"
)
```

`acme/web#1` 仍然是拒绝。产品经理以“不计划做”关闭了它。后来的标签和重新打开，表现为策略没有采纳的尝试。`validate` 会打印 `ok:`，并在这份策略下指出还没有角色的 GitHub 账号。他们的事件在日志里，在你自己把角色映射上去之前不能改变状态。

`orgmemory github convert` 的退出码：`0` 表示完整跑完，`2` 表示输入被拒绝、什么都没追加，`3` 表示抓取不完整。夹具里的两份抓取都是完整的，所以两条命令都是 0。这个块的退出码是 `validate` 的退出码。

## 10. 注册只读 MCP 服务器

`memlab-mcp` 在标准输入和标准输出上说 MCP。这个块不启动它，因为没有客户端的服务会一直等。`--help` 只打印用法。`--root` 是工具可以读取的唯一目录。客户端传来的路径相对于这个目录（`memfalsify/MEMORY.md`、`orgmemory/feature-a`）。本教程的根目录是检出里的 `examples/`。进程是只读的：它不写报告、确认、事件或策略。除非启动时加上 `--allow-model-verifiers`，否则模型验证器保持关闭。本教程不加这个旗标。

注册必须和本机安装的客户端一致。这个块读取 `claude mcp add --help` 和 `codex mcp add --help`，只有当帮助文本仍然是这里用的 stdio 形式（命令前面有 `--`，这样 `--root` 不会被客户端自己吃掉）时才运行 `mcp add`。如果没有安装某个客户端，或者帮助文本对不上，这个块会指向 [mcp.zh-CN.md](mcp.zh-CN.md)，并且不改你的配置。[mcp.md](mcp.md) 是同一页的英文。

Claude Code 的帮助对得上时，实际运行的命令是：

`claude mcp add memlab -- "$(command -v memlab-mcp)" --root "$(pwd)/examples"`

Codex 的帮助对得上时，实际运行的命令是：

`codex mcp add memlab -- "$(command -v memlab-mcp)" --root "$(pwd)/examples"`

Claude Code 的 `-s` / `--scope` 和 Codex 的 `--env` 都在同一份帮助里。这里用默认值就够了：stdio，以及服务器自己的环境。

```bash
# exit 0
(
  set -euo pipefail
  SERVER="$(command -v memlab-mcp)"
  ROOT_DIR="$(pwd)/examples"
  test -n "$SERVER"
  test -d "$ROOT_DIR"
  register_claude=0
  register_codex=0
  if command -v claude >/dev/null 2>&1 && claude mcp add --help >"$WORK/claude-mcp-add-help.txt" 2>&1; then
    if grep -F -q '<name> <commandOrUrl>' "$WORK/claude-mcp-add-help.txt" \
        && grep -F -q -- '-- my-command' "$WORK/claude-mcp-add-help.txt"; then
      register_claude=1
    fi
  fi
  if command -v codex >/dev/null 2>&1 && codex mcp add --help >"$WORK/codex-mcp-add-help.txt" 2>&1; then
    if grep -F -q -- '-- <COMMAND>' "$WORK/codex-mcp-add-help.txt"; then
      register_codex=1
    fi
  fi
  if [ "$register_claude" -eq 1 ]; then
    claude mcp remove memlab >/dev/null 2>&1 || true
    claude mcp add memlab -- "$SERVER" --root "$ROOT_DIR" </dev/null
  else
    printf '%s\n' "Claude Code CLI: mcp add syntax was not verified. See docs/mcp.md"
  fi
  if [ "$register_codex" -eq 1 ]; then
    codex mcp remove memlab >/dev/null 2>&1 || true
    codex mcp add memlab -- "$SERVER" --root "$ROOT_DIR" </dev/null
  else
    printf '%s\n' "Codex CLI: mcp add syntax was not verified. See docs/mcp.md"
  fi
)
```

工具名称和限制见 [mcp.zh-CN.md](mcp.zh-CN.md)。工具返回的引文是不可信的数据，和它们的来源文件一样。

## Windows

这些命令行工具本身可以在 Windows 上运行。本页的 bash 块不行：`tests/test_tutorial.py` 在 Windows 上会跳过。在 PowerShell 里，用 `py -3 -m venv .venv` 创建虚拟环境，用 `.\.venv\Scripts\Activate.ps1` 激活，用 `py -3 -m pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory -e packages/memlab-mcp` 安装。临时目录可以是 `Join-Path $env:TEMP memory-lab-tutorial`。Git Bash 更能直接运行这些块；MCP 服务器仍然不会启动。

`memlab-mcp` 需要 POSIX 的 `O_NOFOLLOW` 和基于文件描述符的打开。在 Windows 上它启动时以 2 退出。请改用 `memfalsify` 和 `orgmemory`。细节见 [mcp.zh-CN.md](mcp.zh-CN.md)。

保存 rollback 补丁时用 `-o`。PowerShell 的重定向可能改写文件编码，`git apply` 随后会看到不同的字节。`memfalsify --output` 和 `orgmemory` 自己写出的报告是 UTF-8、LF；shell 重定向是另一步。

## 让这一页保持真实

`tests/test_tutorial.py` 抽出本页每一个 `bash` 块，在一个 shell、一个临时目录里按顺序运行，使用的是本仓库的命令行工具。它期望 `# exit N` 那一行上的退出码。它不使用模型验证器。中文页的 `bash` 块和英文页是同一段文本。改命令时，两页一起改，并保留退出码那一行。
