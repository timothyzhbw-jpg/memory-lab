# memlab-mcp

Part of [Memory Lab](https://github.com/timothyzhbw-jpg/memory-lab#readme). A read-only MCP server that
exposes MemFalsify and Organizational Memory to local agents as tools, resources and prompts, with every
path confined to one root directory.

```bash
pip install memlab-mcp
memlab-mcp --root /path/to/your/project
```

It needs macOS or Linux, because confined reads use POSIX `O_NOFOLLOW` and `dir_fd`: on Windows it exits with
code 2. See [docs/mcp.md](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/docs/mcp.md) for
usage and the [changelog](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/CHANGELOG.md) for
changes.

The typed public Python API is documented in the
[API reference](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/docs/api.md).
Import library symbols from the package root; `__all__` defines the supported surface.
