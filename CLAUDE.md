# memory-lab

Monorepo with three Python packages (Python 3.11+):

- `packages/memcore` — shared evidence core (models, JSONL store, retrieval)
- `packages/memfalsify` — `memfalsify` CLI: audits a `MEMORY.md` against an evidence directory
- `packages/orgmemory` — `orgmemory` CLI: event replay, permissions, state machine, decision provenance

Skills live in `skills/`, example inputs and generated reports in `examples/`.

## Commands

- Install: `pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory pytest`
- Test: `python3 -m pytest` from the repo root (`pytest.ini` sets `pythonpath`, so tests also run without installing the packages)

## Conventions

- CLIs use Typer, data models use Pydantic, storage is JSONL.
- License is Apache-2.0.
