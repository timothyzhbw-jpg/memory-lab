# memory-lab

Monorepo with three Python packages (Python 3.10+):

- `packages/memcore` — shared evidence core (models, JSONL store, retrieval)
- `packages/memfalsify` — `memfalsify` CLI: audits a `MEMORY.md` against an evidence directory. Verifier backends: `verify.py` (lexical, `DualVerifier`, `CascadeVerifier`), `llm.py` (Claude, extra `[llm]`), `jev.py` (TypeSafe AI's Jev judgment model via `typesafe-sdk`, extra `[jev]`), `openai_compat.py` (any OpenAI-compatible Chat Completions API: OpenAI, Grok, DeepSeek, Gemini, Ollama; extra `[openai]`)
- `packages/orgmemory` — `orgmemory` CLI: event replay, permissions, state machine, decision provenance

Skills live in `skills/`, example inputs and generated reports in `examples/`. The Claude Code plugin manifests are `.claude-plugin/marketplace.json` and `skills/.claude-plugin/plugin.json`; `tests/test_packaging_agents.py` checks them.

## Commands

- Set up locally: `./scripts/setup-local.sh` (creates `.venv`, installs the three packages with the model extras, runs the tests)
- Install by hand: `pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory pytest`
- Test: `python3 -m pytest` from the repo root (`pytest.ini` sets `pythonpath`, so tests also run without installing the packages)

## Conventions

- CLIs use Typer, data models use Pydantic, storage is JSONL.
- Model backends import `anthropic` / `typesafe_sdk` / `openai` lazily; the default path must keep working without them. Tests never use the network: fake Anthropic and Chat Completions clients, and the real TypeSafe and openai SDKs on an `httpx2.MockTransport`.
- License is Apache-2.0.

### Windows

Development happens on macOS; CI also tests Windows, where text mode writes each LF as CRLF. `tests/test_windows_guard.py` checks three of these rules: text-mode writes, server-test markers and f-strings that only Python 3.12 parses. Exact output and forward slashes are conventions for review.

- Write test fixtures and any hashed or golden-compared file as bytes: `write_text_exact(path, text)` from `tests/conftest.py` in tests, `path.write_bytes(text.encode("utf-8"))` elsewhere. A `write_text` or text-mode `open(..., "w")` needs `newline=""`; `python scripts/exact_writes.py` rewrites the tests.
- Write exact output as bytes: `typer.echo(text.encode("utf-8"), nl=False)` or `sys.stdout.buffer`.
- Tests that start memlab-mcp or memlab-ui (they need POSIX confined reads) get `@requires_confinement` from `tests/conftest.py`, or set `confinement_supported` with `monkeypatch` so every OS takes the same path (also around `pytest.raises`).
- Paths in reports and Markdown use forward slashes (`as_posix()`).
- Keep backslashes, comments and the enclosing quote out of an f-string's `{...}`: Python 3.10 and 3.11 reject them.
- `python3 -m pytest --simulate-windows-newlines` runs the suite with Windows newlines (CRLF) in text-mode writes and `CliRunner` output, on macOS or Linux.
