# Working on memory-lab locally

English | [中文](local-setup.zh-CN.md)

## 1. Set up

Requirements: Git and Python 3.10 or newer.

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab && cd memory-lab
./scripts/setup-local.sh            # or: ./scripts/setup-local.sh --minimal
source .venv/bin/activate
```

The script creates `.venv`, installs `memcore`, `memfalsify`, `orgmemory` and `memlab-mcp` in editable mode together with the Claude and Jev SDKs (`--minimal` leaves those out), and runs the test suite. Run it again at any time; it reuses `.venv`. If `python3` is older than 3.10, point it at a newer interpreter: `PYTHON=python3.12 ./scripts/setup-local.sh`.

On Windows, run the same steps by hand in PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e packages/memcore -e "packages/memfalsify[all]" -e packages/orgmemory -e packages/memlab-mcp pytest
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

## 3. Run the benchmark

The benchmark lives on the `eval-action` branch until it is merged; see that branch's `HANDOFF.md`.

```bash
python benchmarks/memfalsify/run.py                      # lexical, offline
python benchmarks/memfalsify/run.py --verifier jev       # needs TYPESAFE_API_KEY
python benchmarks/memfalsify/run.py --verifier cascade   # Jev first, Claude only when Jev is unsure
python benchmarks/memfalsify/run.py --verifier dual --json results-dual.json
```

The [measured lexical run](../benchmarks/memfalsify/RESULTS.md) made 227 `judge()` calls for 202 claims. Model runs use different preselection (up to 8 passages per memory), so their actual call counts must come from the harness's Cost line or JSON, not from this lexical count. Use `--case <id>` to limit a run. [The benchmark README](../benchmarks/memfalsify/README.md) explains the metrics.

## 4. Keep working with an agent

The project instructions are in `AGENTS.md`. Codex reads that file directly, and `CLAUDE.md` imports it, so both agents get the same rules.

- **Claude Code:** run `claude` in the repository folder. To drive that local session from the Claude app on your phone or the web, run `claude remote-control` in the folder instead.
- **Codex:** run `codex` in the repository folder.

Cloud sessions install dependencies through `.claude/hooks/session-start.sh`. Locally, `scripts/setup-local.sh` does the same job.
