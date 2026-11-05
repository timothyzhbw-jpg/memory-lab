# memory-lab

One install for [Memory Lab](https://github.com/timothyzhbw-jpg/memory-lab#readme). This distribution holds no code
of its own. It installs two tools that also work on their own:

- [`memfalsify`](https://pypi.org/project/memfalsify/) audits an agent memory or instruction file (`MEMORY.md`,
  `CLAUDE.md`, `AGENTS.md`, ...) against a directory of evidence. It reports and proposes; it never edits memory.
- [`orgmemory`](https://pypi.org/project/orgmemory/) replays a team's decision log under explicit, effective-dated
  authority rules and explains, with quoted evidence, why a subject is in its current state. It only reads the log.

Both use [`memlab-core`](https://pypi.org/project/memlab-core/), which comes with them.

## Install

```bash
pip install memory-lab
```

Python 3.10 or newer. Extras add the optional parts:

| Extra | Adds | Note |
|---|---|---|
| `memory-lab[ui]` | [`memlab-ui`](https://pypi.org/project/memlab-ui/), a local read-only web page | macOS and Linux |
| `memory-lab[mcp]` | [`memlab-mcp`](https://pypi.org/project/memlab-mcp/), a read-only MCP server for local agents | macOS and Linux |
| `memory-lab[llm]` | The Claude verifier (`memfalsify[llm]`) | Sends memory and evidence text to the Anthropic API |
| `memory-lab[jev]` | The Jev verifier (`memfalsify[jev]`) | Sends memory and evidence text to the TypeSafe API |
| `memory-lab[openai]` | The OpenAI-compatible verifier (`memfalsify[openai]`) | Sends memory and evidence text to the endpoint you configure |
| `memory-lab[all]` | All three verifiers (`memfalsify[all]`) | `ui` and `mcp` stay separate |

The default verifier is lexical and offline.

## Try it

```bash
memfalsify audit MEMORY.md --evidence docs/
orgmemory --help
```

## More

- [Tutorial](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/docs/tutorial.md)
- [Project README](https://github.com/timothyzhbw-jpg/memory-lab#readme) ([中文](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/README.zh-CN.md))
- [Changelog](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/CHANGELOG.md)

Each package keeps its own version number. This one names the release and pins one minor version of each package
it installs.
