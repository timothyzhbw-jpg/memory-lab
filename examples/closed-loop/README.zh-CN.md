# 闭环：从发现到决定

[English](README.md) | 中文

往 Agent 的 `MEMORY.md` 里写一行是一次决策：有人提议，有人批准，而且只有一部分人有权批准。这个演示用真实的 `memfalsify` 和 `orgmemory` 命令，带四行记忆走完 `记住 → 验证 → 质疑 → 裁决 → 演进`。所有内容都是合成的：人、服务和文档。两个工具的关系和逐字段对应见 [docs/architecture.zh-CN.md](../../docs/architecture.zh-CN.md)。

## 运行

从一份全新的检出开始，在 Linux 或 macOS 上，在仓库根目录运行：

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -q -e packages/memcore -e packages/memfalsify -e packages/orgmemory && .venv/bin/python examples/closed-loop/run_demo.py
```

在 Windows 的 PowerShell 里：

```powershell
python -m venv .venv; .venv\Scripts\python -m pip install -q -e packages/memcore -e packages/memfalsify -e packages/orgmemory; .venv\Scripts\python examples/closed-loop/run_demo.py
```

如果已经装好这两个工具，运行 `python examples/closed-loop/run_demo.py` 就够了。需要 git。安装时会访问一次软件包索引；演示本身不联网，也不调用模型。它在一个新建的临时目录里搭一个 git 仓库，所有内容都留在那里供你查看，并告诉你位置。所有报告都和 [`expected/`](expected/) 一致时退出码为 0。

## 这四行

| `MEMORY.md` 里的行 | 提议人 | 批准人 | 有权批准吗？ | MemFalsify | orgmemory |
|---|---|---|---|---|---|
| Refund requests over 500 dollars need a second reviewer. | `lin` | `zhou` | 有 | 可能有支撑 | 已批准 |
| The main database runs PostgreSQL 15. | `lin` | `zhou` | 有 | 可能有反证：批准之后文档改成了 16 | 已批准，并有一条 **high** 的 `risk_after_approval` |
| The public API rate limit is 100 requests per minute. | `agent-7` | `wu` | **没有** | 可能有反证：文档写的是 60 | 仍是提议状态，并有一条 `unauthorized_attempts` |
| The nightly export job finishes before 04:00 UTC. | `agent-7` | `agent-7`（自己合入） | **没有** | 没有证据 | 仍是提议状态，并有一条 `unauthorized_attempts` |

第二行和第三行从 MemFalsify 得到的是同一种发现，接下来要问的却不一样。数据库那一行当初的批准没有问题，要问的是这个决定现在还成不成立。速率限制那一行从来没有过有效的批准，要问的是本该由谁来看。MemFalsify 分不清这两种情况，orgmemory 可以。[`policy.json`](policy.json) 规定谁能做什么：只有 `memory_steward` 能批准，而只有 `zhou` 持有这个角色。

## 逐步说明

**1. 记住。** `run_demo.py` 以作者的身份提交每一行。提交标题末尾可以写 `(approved-by: NAME)`；没有写，就是作者把自己的这一行合入，等于自己批准了自己。

**2. 验证。** MemFalsify 对照 `docs/` 审计记忆，并把每一行追溯到它的提交。它什么也不改。

```text
memfalsify audit MEMORY.md --evidence docs --as-of 2026-10-10 --format json
memfalsify history MEMORY.md --format json
```

报告：[审计](expected/1-memfalsify-audit.md)、[历史](expected/2-memfalsify-history.md)。

**3. 质疑。** [`to_events.py`](to_events.py) 只用标准库，把这些报告转换成证据记录和事件。`run_demo.py` 把每一条交给真实的命令。然后由 orgmemory 读取日志。

```text
orgmemory evidence add DATASET --id=... --path=... --quote=... --source-uri=... --source-kind=... --captured-at=...
orgmemory append DATASET --event='{"id": "write-approve-m-9efe3be5-7ed0c4c7f4b1", "actor": "wu", "action": "approve", ...}'
orgmemory audit DATASET --as-of 2026-10-11T00:00:00+00:00
orgmemory explain DATASET memory:m-9efe3be5
```

[审计](expected/3-orgmemory-audit.md)报告 `wu` 有一次被策略忽略的批准。[`explain`](expected/4-orgmemory-explain.md) 显示速率限制这一行是 `proposed`，仍在等 `memory_steward` 批准，风险留在记录里，还有这一行：

```text
- `write-approve-m-9efe3be5-7ed0c4c7f4b1`: wu tried to approve; actor roles ['account_manager'] may not approve
```

**4. 裁决。** `zhou` 有权限，读了 `docs/api.md` 后否决这一行。脚本替 `zhou` 再执行一次 `orgmemory append`，因为两个工具都不作决定。[`explain`](expected/5-orgmemory-explain-resolved.md) 现在显示 `rejected`，由 `zhou` 决定，并引用文档原文。

**5. 演进。** `lin` 手动改正这一行，`zhou` 批准。循环再跑一遍，只记录新内容。新的措辞有新的 ID，所以是一个新议题：MemFalsify [找到了支撑](expected/6-memfalsify-audit-corrected.md)，orgmemory 显示它[已批准](expected/7-orgmemory-explain-corrected.md)。[最终审计](expected/8-orgmemory-audit-final.md)仍然列出没有解决的问题，数据库那一行还在等人决定。

## 这里有什么

| 文件 | 是什么 |
|---|---|
| [`run_demo.py`](run_demo.py) | 搭建仓库并运行整个循环。`--work DIR` 指定目录；`--update-expected` 重写 `expected/` |
| [`to_events.py`](to_events.py) | 格式转换：输入报告，输出证据记录和事件 |
| [`policy.json`](policy.json) | 谁能做什么 |
| [`expected/`](expected/) | 每一份报告，以及最终的 [`events.jsonl`](expected/events.jsonl) 和 [`evidence.jsonl`](expected/evidence.jsonl) |

`tests/test_closed_loop.py` 会从头运行这一切并比较结果。

## 局限

git 作者名和 `(approved-by: NAME)` 都是任何人都能写的文字，所以 orgmemory 检查的是所写名字的角色，不是这个人真的做过。`(approved-by: NAME)` 只是代替你们团队记录批准的方式；可以修改 `to_events.py` 里的 `APPROVED_BY` 来适配。词法验证器的发现是留给人阅读的候选。`to_events.py` 只引用 history 的 HEAD 里有的内容，所以要先提交，遇到未提交的改动它会停下。审计报告里只有日期，如果有人可能在审计当天作决定，请加上 `--audited-at`；演示是在之后的日子作决定的。完整列表见 [docs/architecture.zh-CN.md](../../docs/architecture.zh-CN.md#局限)。
