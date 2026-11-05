# 快速上手

[English](quickstart.md) | 中文

从零到第一份 MemFalsify 报告，只需一步安装和一条命令（生成示例并审计）。克隆和 `pip install` 要联网，之后的一切都离线运行，不需要 API key。[教程](tutorial.zh-CN.md)讲得更多：`diff`、`history`、`rollback`，以及本仓库的另一个工具。

## 1. 安装

需要 Python 3.10 或更新版本，以及 git。从本仓库的一份检出开始：

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab.git && cd memory-lab
python3 -m venv .venv && . .venv/bin/activate
pip install -e packages/memcore -e packages/memfalsify
```

## 2. 审计示例

```bash
# exit 0
memfalsify init demo && cd demo && memfalsify audit
```

`init` 会在一个新目录里写一个小项目：含三条记忆的 `MEMORY.md`、含两个文件的 `evidence/`，以及一个空的 `reviews.jsonl`。只要其中任何一个文件已经存在，它就拒绝执行，退出码为 2，什么也不写。加 `--lang zh` 写的是同样内容的中文版。

不带参数的 `audit` 取当前目录里的记忆文件，和目录里其余的内容对照。报告给出三种结果：

| 报告里写的 | 这条记忆 | 原因 |
|---|---|---|
| `POSSIBLE COUNTEREVIDENCE` | The public API allows 100 requests per minute. | `evidence/api.md:3` 写的是 60。两边的原文都会列出，并带上文件的 SHA-256。 |
| `POSSIBLE SUPPORT` | Database backups run nightly at 02:00 UTC. | `evidence/runbook.md:3` 说的是同一件事。 |
| `NO EVIDENCE` | The design team prefers short release notes. | 这个目录里没有东西提到它。这表示没查证，不表示错。 |

## 读懂报告

- 这些标记来自词语比对，所以一个标记的意思是“读一下这两段原文”，而不是“这条错了”。要有标记，得是同一个设置的值不同，或者两段几乎相同的陈述有差异；比对仍可能漏掉换一种说法的表述，也可能误解上下文，所以没有标记的报告并不能排除什么。报告开头的 `Verifier: lexical` 指的就是这种比对（[规则](lexical-rules.zh-CN.md)）；[模型验证器](local-setup.zh-CN.md)是可选的。
- 扫描会跳过第三方和生成的文件，以及超过 1,000 个字符的行，并在 `Skipped evidence` 里列出。
- `m-c1b4957f` 是这条记忆的 ID，由它的文字算出。改了记忆，ID 就变了，旧的审核也就不再适用。
- `Proposed actions` 只是建议。MemFalsify 只写报告和建议，你的记忆文件保持原样。
- 不管报告里写什么，退出码都是 0，除非加了 `--strict`：这时有记忆需要注意，退出码就是 1。输入无法使用时退出码为 2。

只有人才能把一条记忆标记为 supported 或 challenged，方法是记一条审核。把这一行加到 `reviews.jsonl`：

```bash
# exit 0
printf '%s\n' '{"memory_id": "m-c1b4957f", "relation": "contradicts", "path": "evidence/api.md", "quote": "The public API allows 60 requests per minute.", "reviewer": "you"}' >> reviews.jsonl
memfalsify audit --reviews reviews.jsonl
```

现在这条记忆是 `CHALLENGED`，由你确认。`memfalsify init` 会打印同样的一行，`memfalsify claims MEMORY.md` 会列出所有 ID。不指定文件时，审核记录文件不算证据。它只在传了 `--reviews` 时才生效；如果在证据目录顶层看到了一个你没传的审核文件，命令会提醒你。

## 在你自己的仓库里运行

```bash
cd your-repository
memfalsify audit
```

文件来自 `memfalsify discover`（任意深度的 `CLAUDE.md`、`AGENTS.md`、`GEMINI.md`，以及这些智能体约定位置上的 Cursor、Copilot、Windsurf 和 Devin 规则），再加上目录顶层的 `MEMORY.md`。stderr 上有一行说明选中了哪些文件。一个都没有时，命令以退出码 2 结束，并建议运行 `memfalsify init`。

证据是当前目录，或 `--evidence DIR`。规则和网页用的一样：

- 不读隐藏文件和隐藏文件夹，不读 `node_modules/`、`__pycache__/`、`venv/`。超过 2 MB 的文件和不是 UTF-8 文本的文件也不读。
- 媒体文件只通过转录或 OCR 旁车文件算作证据。
- MemFalsify 和 orgmemory 的报告、审核记录、决策日志都会被排除。
- 每个文件的证据是除它自己和它链接的文件之外的全部内容。其他智能体文件也算在内，两个文件说法不一致时就是这样显现出来的。

报告会列出跳过了什么。`--exclude GLOB`（可重复）同时把文件排除在记忆文件的选择和证据之外：`memfalsify audit --exclude 'tests/fixtures/**' --exclude 'vendor/**'`。

多个文件共用一份报告，按路径排序：先是总数和文件表，然后每个文件一节。两个文件里相同的一条记忆只出现一次，另一处列为重复。只有一个文件时，Markdown 和 HTML 报告的版式与 `memfalsify audit FILE --evidence .` 相同；如果搜索跳过了某个位置，报告会为它单列一节。`--format json` 的结构不随文件数变化：一个对象，含 `files`、`summary` 和 `reports`，后者的每一项就是 `memfalsify audit FILE --format json` 打印的那种对象。`--strict` 的含义不变：任何文件里有记忆需要注意，就退出 1。

指定文件，比如 `memfalsify audit CLAUDE.md`，和以前一样：只审计这个文件，除非传 `--evidence`，否则没有证据；并且只按证据扫描本身的规则跳过文件，所以 `--evidence` 里的审核文件或决策日志在这种用法下会被当作证据。

## 把 git 历史当作证据

提交说明常常记着决定了什么、为什么。从示例目录开始，下面的命令把它们变成证据文件：

```bash
# exit 0
cd .. && git init -q history && cd history
git -c user.name=you -c user.email=you@example.com commit -q --allow-empty -m "Lower the public API limit to 60 requests per minute"
memfalsify evidence from-git --since 2026-01-01 --output commits --max-commits 100
cd ../demo
memfalsify audit --evidence ../history/commits
```

最后两行回到示例目录，因为审计要在放着记忆文件的目录里运行。报告现在会引用这条提交说明，作为那条 100 次请求记忆的可能反证。如果 `commits/` 就在项目里面，直接运行 `memfalsify audit` 也会读到它。

`--since` 接受日期（`2026-01-31` 表示 UTC 零点，`2026-01-31T12:00:00+02:00`，`'30 days ago'`），按提交者日期（committer date）筛选；也接受提交、标签或分支，表示它之后直到 `HEAD` 的提交。看起来像日期的值按日期读，既不是日期也不是提交的值会被拒绝。日期会和历史里的每个提交比较，所以一个日期很旧的提交不会挡住它后面的提交。

每个提交生成一个小的 Markdown 文件，以 12 位哈希命名（如果选中的两个提交前 12 位相同，就用完整哈希）。文件以固定的头部开始：短哈希、完整哈希、作者日期，后面是标题和正文：

```text
# Commit 1a2b3c4d5e6f

- Full hash: 1a2b3c4d5e6f...
- Author date: 2026-11-04T10:00:00-06:00

Lower the public API limit to 60 requests per minute
```

同样的提交产生同样的字节，所以重复运行只会添加新的文件。写全的日期与机器无关；`'30 days ago'` 取决于运行的时间。命令只写 `--output` 里面，对仓库只读。它拒绝把 `--output` 放进 git 目录；如果目录里已有内容不同的文件，它会停下来，什么也不写。

提交说明是提交者写的文字，所以它和其他来源一样只是候选。说明里的控制字符、双向控制字符和零宽字符会存成可见的 `\uXXXX` 转义，既不保留原样，也不删除（真实文字里的连接符会保留，比如波斯文或表情符号序列）：

- 引文和哈希描述的是证据文件本身，所以报告里的引文无论如何都是精确的，也和打开文件看到的一致。`reviews.jsonl` 的每一行是 JSON，所以引文里存下的 `\uXXXX` 要写成双反斜杠；`memfalsify audit --format json` 显示的就是这种写法，可以直接复制。
- 打印这个文件不会操控你的终端。
- 删除这些字符会掩盖它们存在过，还可能把文字变成作者的原始字节里并没有的词。完整哈希可以找到原文。

不是 UTF-8 的字节会变成 U+FFFD，超过 256 KB 的说明会被截断，并附一句说明。比当前 Python 的 Unicode 表更新的字符也会存成转义，这种罕见情况下，不同 Python 版本的结果可能不同。

GitHub 版本（pull request 的标题和正文）在路线图上。

## Windows

用 `.venv\Scripts\Activate.ps1` 激活虚拟环境，其余命令相同。PowerShell 7 接受 `&&`；Windows PowerShell 5 要用 `;`：`memfalsify init demo; cd demo; memfalsify audit`。添加审核那一行时，用编辑器粘贴到 `reviews.jsonl`。上面的 `printf` 命令是 bash 的写法。
