# 参与 Memory Lab

[English](CONTRIBUTING.md) | 中文

现阶段最有用的是问题报告、误报反馈和小修复。Issue 和 PR 用中文或英文写都可以。

## 报告问题

- [Bug report](https://github.com/timothyzhbw-jpg/memory-lab/issues/new?template=bug_report.yml)：命令崩溃、输出不对，或者行为和文档说的不一样。
- [False flag or missed contradiction](https://github.com/timothyzhbw-jpg/memory-lab/issues/new?template=false_flag.yml)：审计把没问题的说法标了出来，或者漏掉了本该标出的说法。这类真实案例是改进检查的主要依据。
- 安全问题：看 [SECURITY.md](SECURITY.md)，不要开公开 issue。

问题区是公开的。最有用的是一个最小的合成复现示例。不要粘贴或提交私密证据和凭据。

## 准备环境

需要 Git 和 Python 3.10 或更新版本。

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab && cd memory-lab
./scripts/setup-local.sh
source .venv/bin/activate
```

脚本会创建 `.venv`，以可编辑模式安装各个包和模型 SDK（`--minimal` 不装这些 SDK），然后跑一遍测试。如果 `python3` 版本太旧，可以指定别的解释器：`PYTHON=python3.12 ./scripts/setup-local.sh`。Windows 上的 PowerShell 步骤在 [docs/local-setup.zh-CN.md](docs/local-setup.zh-CN.md)。

CI 还会安装 `cmarkgfm` 和 `setuptools>=77`。需要它们的测试在你安装之前会被跳过。

## 提 PR 之前

在检出目录的根目录运行：

```bash
python3 -m pytest
python3 -m pytest --simulate-windows-newlines
memfalsify audit AGENTS.md --evidence . --exclude 'benchmarks/**' --exclude 'examples/**' --exclude 'tests/fixtures/**' --exclude '.worktrees/**' --exclude '.venv*/**' --strict
```

- 第一条是测试套件，要跑几分钟。它用假的客户端，不访问网络，所以不需要 API key。
- 第二条在文本模式写入和 `CliRunner` 输出里模拟 Windows 换行（CRLF），在 macOS 或 Linux 上就能提前发现换行问题，不用等 Windows CI。
- 第三条是严格模式的自检审计：这个仓库审计自己的 `AGENTS.md`。除了被排除的目录和 `.github/` 这样的隐藏目录，每个新文件都会成为这次审计的证据，所以一句新写的话可能读起来和 `AGENTS.md` 矛盾。这项检查是词法的，英文里的 "without"，或者同一话题附近出现另一个数字，都足以触发。如果是 `AGENTS.md` 错了，就改 `AGENTS.md`；如果你写的没错、只是读起来像矛盾，就换成正面的说法。

CI 会在 Linux、macOS 和 Windows 上跑测试，也会用声明的最低依赖版本跑一遍，还会演练构建和安装 wheel。PR 需要让这些都保持通过。

## 必须保持不变的事

- MemFalsify 只报告和提议，不写入事实，也不改动记忆。`orgmemory replay` 和 `orgmemory audit` 只读。
- 默认路径离线运行，不需要 key。模型后端延迟导入 `anthropic`、`typesafe_sdk` 和 `openai`。
- 各工具保持可以独立安装：`memcore` 加上任意一个工具就能单独使用。
- 来自记忆文件、证据、日志或抓取内容的文字都不可信，凡是会到达终端或报告的地方，都要经过 `memcore.display` 里的函数。
- CLI 用 Typer，数据模型用 Pydantic，存储用 JSONL。
- 每个直接依赖都要声明一个确实可用的下限：CI 的 `lowest` 任务会安装这些版本并运行测试。

## Windows 规则

开发主要在 macOS 上进行，这些规则用来提前发现只会在 Windows CI 上失败的问题。完整列表在 `AGENTS.md`，`tests/test_windows_guard.py` 会检查文本模式写入、启动服务的测试标记，以及只有 Python 3.12 才能解析的 f-string。

- 测试夹具和任何要计算哈希或逐字节比较的文件都按字节写：测试里用 `tests/conftest.py` 的 `write_text_exact(path, text)`，其他地方用 `path.write_bytes(text.encode("utf-8"))`。`python scripts/exact_writes.py` 可以改写测试。
- 要求逐字节一致的输出也按字节写：`typer.echo(text.encode("utf-8"), nl=False)` 或 `sys.stdout.buffer`。
- 启动 memlab-mcp 或 memlab-ui 的测试要加 `tests/conftest.py` 里的 `@requires_confinement`。
- 报告和 Markdown 里的路径用正斜杠（`as_posix()`）。
- f-string 的 `{...}` 里不要放反斜杠、注释和外层引号。

## 改了这里，也要改那里

| 你改的 | 还要更新 |
|---|---|
| 任何用户能看到的东西 | `CHANGELOG.md` 的 `## Unreleased` 和 README |
| 有 `.zh-CN.md` 对应版本的文档 | 两种语言都改 |
| `docs/tutorial.md` | `docs/tutorial.zh-CN.md`：`bash` 代码块必须完全相同，第一行写 `# exit N`，测试会实际运行它们 |
| 对外导出的名字 | `tests/public_api.json`、`docs/api.md` 和它的中文版 |
| CLI 的命令或参数 | 文档和 `skills/`：`tests/test_skills.py` 会确认 skill 里写的命令和参数都真实存在 |
| GitHub Action 的依赖 | 运行 `scripts/update-action-lock.sh`，需要 `uv` 和网络 |

## 提交和 PR

一个 PR 只做一件事，并加一个没有这个改动就会失败的测试。提交说明用简短的英文，需要时加上模块前缀（`memfalsify: ...`、`tests: ...`）。安全修复以 `Security:` 开头。

这个仓库也有多个 AI agent 在开发，它们的角色和关卡写在 [process/README.zh-CN.md](process/README.zh-CN.md)。外部贡献者只需要看这一页。

## 许可证

Memory Lab 使用 Apache-2.0。按 [LICENSE](LICENSE) 第 5 条，除非你另有声明，你提交的内容也使用同样的许可证。不需要另外签贡献者协议。
