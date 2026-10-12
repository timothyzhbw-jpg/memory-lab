# Working on memory-lab locally

English | [中文](local-setup.zh-CN.md)

## 1. Set up

Requirements: Git and Python 3.10 or newer.

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab && cd memory-lab
./scripts/setup-local.sh            # or: ./scripts/setup-local.sh --minimal
source .venv/bin/activate
```

The script creates `.venv`, installs `memcore`, `memfalsify`, `orgmemory`, `memlab-mcp` and `memlab-ui` in editable mode together with the Claude and Jev SDKs (`--minimal` leaves those out), and runs the test suite. Run it again at any time; it reuses `.venv`. If `python3` is older than 3.10, point it at a newer interpreter: `PYTHON=python3.12 ./scripts/setup-local.sh`.

On Windows, run the same steps by hand in PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e packages/memcore -e "packages/memfalsify[all]" -e packages/orgmemory -e packages/memlab-mcp -e packages/memlab-ui pytest
.venv\Scripts\python -m pytest -q
```

## 2. API keys for the model verifiers

The default `lexical` verifier needs nothing. The model verifiers read their credentials from the environment:

| Verifier | Variable | Notes |
|---|---|---|
| `llm` (Claude) | `ANTHROPIC_API_KEY` | An `ant auth login` profile also works |
| `jev` (TypeSafe Jev) | `TYPESAFE_API_KEY` | From the TypeSafe console |
| `dual`, `cascade` | both | |

```bash
export TYPESAFE_API_KEY=...        # put these in your shell profile or a secrets manager
export ANTHROPIC_API_KEY=...
```

Never commit a key or paste it into a chat. If a key has been shown anywhere it should not have been, revoke it and create a new one.

## 3. What a model audit costs

A model request judges one memory against one evidence sentence. The number of requests is bounded before anything is sent:

| `--verifier` | Requests per audit, at most |
|---|---|
| `llm` | memories × `--max-judged` (default 8) to Claude |
| `jev` | the same number to Jev |
| `dual` | that number to each backend |
| `cascade` | that number to Jev, plus one Claude request for each Jev answer below `--accept-at` |

Fewer are sent when a memory shares terms with fewer sentences (`--min-coverage`), and `diff` judges only added and changed memories. With `--batch`, a request Claude refuses is sent once more directly with the refusal fallback, on models that have one.

Each report says what was used: one "API usage" line per backend (`verifier_stats` in JSON) with the requests, how many went through a batch, input and output tokens, and for Claude the tokens written to and read from the prompt cache. Retries the SDK makes after rate-limit or server errors are not counted. To turn tokens into money, use the current prices on the [Anthropic pricing page](https://platform.claude.com/docs/en/about-claude/pricing); Jev is billed by TypeSafe AI. This repository does not quote prices, because they change.

- `--batch` bills the Claude requests at 50% of standard prices, but a batch can take up to 24 hours (most end within an hour).
- Prompt caching only helps when a cached prefix reaches the model's minimum cacheable length. MemFalsify's instructions are a few hundred tokens, so for short memories the cache counters stay at 0 and nothing changes.
- `--max-concurrency` (default 4) shortens the wait. The requests are the same set either way; cache-token totals, and so the bill when a prefix is cached, are whatever that run's API reported. It is not used with `--batch`. Keep it within your rate limits.

## 4. Run the benchmark

The benchmark lives on the `eval-action` branch until it is merged; see that branch's `HANDOFF.md`.

```bash
python benchmarks/memfalsify/run.py                      # lexical, offline
python benchmarks/memfalsify/run.py --verifier jev       # needs TYPESAFE_API_KEY
python benchmarks/memfalsify/run.py --verifier cascade   # Jev first, Claude only when Jev is unsure
python benchmarks/memfalsify/run.py --verifier dual --json results-dual.json
```

The [measured lexical run](../benchmarks/memfalsify/RESULTS.md) made 136 `judge()` calls for 139 claims. Model runs use different preselection (up to 8 passages per memory), so their actual call counts must come from the harness's Cost line or JSON, not from this lexical count. The harness counts `judge()` calls, not tokens; for token counts, audit a case with `memfalsify audit` and read its API usage lines. Use `--case <id>` to limit a run. [The benchmark README](../benchmarks/memfalsify/README.md) explains the metrics.

## 5. Keep working with an agent

The project instructions are in `AGENTS.md`. Codex reads that file directly, and `CLAUDE.md` imports it, so both agents get the same rules.

- **Claude Code:** run `claude` in the repository folder. To drive that local session from the Claude app on your phone or the web, run `claude remote-control` in the folder instead.
- **Codex:** run `codex` in the repository folder.

Cloud sessions install dependencies through `.claude/hooks/session-start.sh`. Locally, `scripts/setup-local.sh` does the same job.

To use the skills and the MCP tools in your other projects, install them into the agent: [install-agents.md](install-agents.md).
