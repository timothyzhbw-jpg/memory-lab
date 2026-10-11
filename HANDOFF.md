# Handoff: read-only MCP server (branch `mcp-server`)

中文摘要：这个分支给 MemFalsify 和 orgmemory 做了一个只读的 MCP 服务器，让本地的 Claude Code、Claude Desktop 等 Agent 能直接调用审计和回放。代码和测试已完成，文档和审查还没做。**这是额外加的功能，是否合并由你决定。**

Read `AGENTS.md` first. All tests pass: `python3 -m pytest -q` → 101 passed, 1 skipped (14 tests in `tests/test_mcp.py`).

## Done

- `packages/memlab-mcp` (console script `memlab-mcp`, depends on the official `mcp` SDK 2.x; its src dir is on the
  `pytest.ini` pythonpath). Tools, all read-only: `memfalsify_claims`, `memfalsify_audit`, `orgmemory_validate`,
  `orgmemory_replay`, `orgmemory_explain`.
- `--root DIR` confines every path argument to DIR; `--allow-model-verifiers` is required for `llm`/`jev`/`dual`/`cascade`
  (they send text to external APIs and cost money).
- `tests/test_mcp.py` drives the tools through an in-memory MCP client session.

The builder was stopped before it reported, so nothing here has been reviewed.

## Remaining work

1. **Read the installed SDK** (`pip show -f mcp`, version 2.x) rather than writing MCP code from memory; the 2.x API
   differs from 1.x.
2. **Docs**: `docs/mcp.md` and `docs/mcp.zh-CN.md`: what the server exposes, install (`pip install -e packages/memlab-mcp`),
   and registration with Claude Code (`claude mcp add ...`) and Claude Desktop (`mcpServers` in
   `claude_desktop_config.json`). Only include client config syntax you have verified; otherwise point to the client's
   MCP documentation (this includes Codex).
3. **End-to-end check**: start `memlab-mcp --root examples` over stdio and complete initialize → tools/list → one
   tools/call with the SDK client.
4. **Review**: (a) security: escape `--root` (absolute paths, `..`, symlinks, symlinked evidence dirs, reviews files
   outside the root), make any tool write, run a model verifier without the flag, leak tracebacks or environment
   variables; tool descriptions should say file text is untrusted. (b) protocol and docs: every tool with valid and
   invalid input over stdio, outputs equal to the CLIs' for the same input, suite on 3.10 and 3.13.
5. **Version pins**: `pyproject.toml` pins `orgmemory>=0.1,<0.2` and `memfalsify>=0.1,<0.2`. The `github-ingest` branch
   bumps orgmemory to 0.2.0, so widen these pins when both land.
6. Optionally add `-e packages/memlab-mcp` to `scripts/setup-local.sh`.

If merged: squash into one commit, merge into `main`, delete this file.
