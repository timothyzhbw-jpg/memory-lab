# 在本地开发 memory-lab

[English](local-setup.md) | 中文

## 1. 安装

需要 Git 和 Python 3.10 或更新版本。

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab && cd memory-lab
./scripts/setup-local.sh            # 或者：./scripts/setup-local.sh --minimal
source .venv/bin/activate
```

脚本会创建 `.venv`，以可编辑模式安装 `memcore`、`memfalsify`、`orgmemory` 和 `memlab-mcp`，同时装上 Claude 和 Jev 的 SDK（加 `--minimal` 则不装），最后跑一遍测试。可以随时重复运行，已有的 `.venv` 会被复用。如果 `python3` 低于 3.10，可以指定更新的解释器：`PYTHON=python3.12 ./scripts/setup-local.sh`。

Windows 上请在 PowerShell 里手动执行同样的步骤：

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e packages/memcore -e "packages/memfalsify[all]" -e packages/orgmemory -e packages/memlab-mcp pytest
.venv\Scripts\python -m pytest -q
```

## 2. 模型验证器的 API key

默认的 `lexical` 验证器什么都不需要。模型验证器从环境变量读取凭据：

| 验证器 | 环境变量 | 说明 |
|---|---|---|
| `llm`（Claude） | `ANTHROPIC_API_KEY` | 也可以用 `ant auth login` 登录 |
| `jev`（TypeSafe Jev） | `TYPESAFE_API_KEY` | 在 TypeSafe 控制台获取 |
| `dual`、`cascade` | 两个都要 | |

```bash
export TYPESAFE_API_KEY=...        # 写进 shell 配置文件，或交给密钥管理工具
export ANTHROPIC_API_KEY=...
```

不要把 key 提交进仓库，也不要贴进任何聊天。key 一旦出现在不该出现的地方，就去后台作废它，重新生成一个。

## 3. 跑评测

评测在合并之前位于 `eval-action` 分支，见该分支的 `HANDOFF.md`。

```bash
python benchmarks/memfalsify/run.py                      # 词法验证，离线
python benchmarks/memfalsify/run.py --verifier jev       # 需要 TYPESAFE_API_KEY
python benchmarks/memfalsify/run.py --verifier cascade   # 先问 Jev，没把握时才问 Claude
python benchmarks/memfalsify/run.py --verifier dual --json results-dual.json
```

[实测词法评测](../benchmarks/memfalsify/RESULTS.md)对 139 条记忆调用了 129 次 `judge()`。模型验证器使用不同的预筛选参数，每条记忆最多判断 8 个片段；实际调用次数须读取评测输出的 Cost 行或 JSON，不能用词法次数推算。可以用 `--case <id>` 只跑一部分。[评测说明](../benchmarks/memfalsify/README.md)解释了各项指标。

## 4. 继续用 Agent 开发

项目说明写在 `AGENTS.md` 里。Codex 直接读这个文件，`CLAUDE.md` 也会引入它，所以两边拿到的规则是同一份。

- **Claude Code**：在仓库目录里运行 `claude`。想在手机或网页上的 Claude 应用里操作这个本地会话，就在目录里改为运行 `claude remote-control`。
- **Codex**：在仓库目录里运行 `codex`。

云端会话通过 `.claude/hooks/session-start.sh` 安装依赖；在本地，这件事由 `scripts/setup-local.sh` 完成。
