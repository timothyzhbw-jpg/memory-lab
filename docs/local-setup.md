# Working on memory-lab locally

English | [中文](local-setup.zh-CN.md)

## 1. Set up

Requirements: Git and Python 3.10 or newer.

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab && cd memory-lab
./scripts/setup-local.sh            # or: ./scripts/setup-local.sh --minimal
source .venv/bin/activate
```

The script creates `.venv`, installs `memcore`, `memfalsify`, `orgmemory` and `memlab-mcp` in editable mode together with the Claude, Jev and OpenAI SDKs (`--minimal` leaves those out), and runs the test suite. Run it again at any time; it reuses `.venv`. If `python3` is older than 3.10, point it at a newer interpreter: `PYTHON=python3.12 ./scripts/setup-local.sh`.

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
| `openai` | the `--provider`'s variable, or the one `--api-key-env` names | See below; `--provider ollama` needs none |

```bash
export TYPESAFE_API_KEY=...        # put these in your shell profile or a secrets manager
export ANTHROPIC_API_KEY=...
```

Never commit a key or paste it into a chat. If a key has been shown anywhere it should not have been, revoke it and create a new one.

### OpenAI-compatible providers (`--verifier openai`)

One verifier speaks the OpenAI Chat Completions API, so it works with OpenAI and with any provider that offers an OpenAI-compatible endpoint. `--openai-model` is required: there is no default, because model names differ between providers and change over time. `--provider` fills in the base URL and the key's variable:

| `--provider` | Base URL | Key variable |
|---|---|---|
| `openai` (default) | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `grok` (xAI) | `https://api.x.ai/v1` | `XAI_API_KEY` |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` ([Google's OpenAI-compatible endpoint](https://ai.google.dev/gemini-api/docs/openai)) | `GEMINI_API_KEY` |
| `ollama` | `http://localhost:11434/v1` | none |

```bash
export XAI_API_KEY=...
memfalsify audit MEMORY.md --evidence docs --verifier openai --provider grok --openai-model <model>

# a local model: nothing leaves the machine and no key is needed
memfalsify audit MEMORY.md --evidence docs --verifier openai --provider ollama --openai-model <model>

# another OpenAI-compatible server, with its key in a variable you choose
export GATEWAY_KEY=...
memfalsify audit MEMORY.md --evidence docs --verifier openai --base-url https://llm.example.com/v1 \
  --api-key-env GATEWAY_KEY --openai-model <model>
```

- `--base-url` and `--api-key-env` override the preset. The base URL must be `https://`; plain `http://` is accepted only for `localhost`, `127.0.0.1` and `::1`, and a URL with a user name, password, query or fragment is refused.
- `--api-key-env` takes the variable's *name*. There is no option for the key itself, so it never appears in your shell history or process list. The key is never printed: error messages from the provider are shown with it redacted.
- Only these options configure the endpoint. The OpenAI SDK's own variables (`OPENAI_BASE_URL`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, `OPENAI_CUSTOM_HEADERS`) are not used, so settings meant for one provider never reach another.
- Memory claims and evidence passages are sent to the configured endpoint (except with a local server). Use a provider only for material you may send there.
- The verifier asks for the verdict as a strict JSON schema (`response_format` `json_schema`). If the endpoint answers HTTP 400 or 422 to that, it asks for a JSON object (`json_object`), then for plain text, keeps the format that worked, and prints the switch on stderr. An answer that is not a valid verdict counts as no verdict (`unrelated`), never as support.
- `--batch` is not supported with this verifier (exit 2). `dual` and `cascade` still pair Claude with Jev.

## 3. What a model audit costs

A model request judges one memory against one evidence sentence. The number of requests is bounded before anything is sent:

| `--verifier` | Requests per audit, at most |
|---|---|
| `llm` | memories × `--max-judged` (default 8) to Claude |
| `jev` | the same number to Jev |
| `dual` | that number to each backend |
| `cascade` | that number to Jev, plus one Claude request for each Jev answer below `--accept-at` |
| `openai` | memories × `--max-judged` to the configured endpoint, plus one retry per format the endpoint refuses (at most two, once per run) |

Fewer are sent when a memory shares terms with fewer sentences (`--min-coverage`), and `diff` judges only added and changed memories. With `--batch`, a request Claude refuses is sent once more directly with the refusal fallback, on models that have one.

Each report says what was used: one "API usage" line per backend (`verifier_stats` in JSON) with the requests, how many went through a batch, input and output tokens, and for Claude the tokens written to and read from the prompt cache. For `openai`, input tokens are the provider's `prompt_tokens` (any provider-side cache is included) and the cache counters are empty. Retries the SDK makes after rate-limit or server errors are not counted, nor are requests an endpoint refused. To turn tokens into money, use the current prices on the [Anthropic pricing page](https://platform.claude.com/docs/en/about-claude/pricing); Jev is billed by TypeSafe AI. This repository does not quote prices, because they change.

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
python benchmarks/memfalsify/run.py --verifier openai --provider ollama --openai-model <model>   # local, no key
```

The [measured lexical run](../benchmarks/memfalsify/RESULTS.md) made 136 `judge()` calls for 139 claims. Model runs use different preselection (up to 8 passages per memory), so their actual call counts must come from the harness's Cost line or JSON, not from this lexical count. The harness counts `judge()` calls, not tokens; for token counts, audit a case with `memfalsify audit` and read its API usage lines. Use `--case <id>` to limit a run. [The benchmark README](../benchmarks/memfalsify/README.md) explains the metrics.

## 5. Keep working with an agent

The project instructions are in `AGENTS.md`. Codex reads that file directly, and `CLAUDE.md` imports it, so both agents get the same rules.

- **Claude Code:** run `claude` in the repository folder. To drive that local session from the Claude app on your phone or the web, run `claude remote-control` in the folder instead.
- **Codex:** run `codex` in the repository folder.

Cloud sessions install dependencies through `.claude/hooks/session-start.sh`. Locally, `scripts/setup-local.sh` does the same job.

To use the skills and the MCP tools in your other projects, install them into the agent: [install-agents.md](install-agents.md).
