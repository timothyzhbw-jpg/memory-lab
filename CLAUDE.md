# memory-lab

Monorepo with three Python packages (Python 3.10+):

- `packages/memcore` — shared evidence core (models, JSONL store, retrieval)
- `packages/memfalsify` — `memfalsify` CLI: audits a `MEMORY.md` against an evidence directory. Verifier backends: `verify.py` (lexical, `DualVerifier`), `llm.py` (Claude, extra `[llm]`), `jev.py` (TypeSafe AI's Jev judgment model via `typesafe-sdk`, extra `[jev]`)
- `packages/orgmemory` — `orgmemory` CLI: event replay, permissions, state machine, decision provenance

Skills live in `skills/`, example inputs and generated reports in `examples/`.

## Commands

- Install: `pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory pytest`
- Test: `python3 -m pytest` from the repo root (`pytest.ini` sets `pythonpath`, so tests also run without installing the packages)

## Conventions

- CLIs use Typer, data models use Pydantic, storage is JSONL.
- Model backends import `anthropic` / `typesafe_sdk` lazily; the default path must keep working without them. Tests never use the network: a fake Anthropic client, and the real TypeSafe SDK on an `httpx2.MockTransport`.
- License is Apache-2.0.
