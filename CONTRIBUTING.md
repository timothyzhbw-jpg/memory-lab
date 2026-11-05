# Contributing to Memory Lab

English | [中文](CONTRIBUTING.zh-CN.md)

Bug reports, false flags and small fixes help most at this stage. Issues and pull requests can be written in English or Chinese.

## Reporting a problem

- [Bug report](https://github.com/timothyzhbw-jpg/memory-lab/issues/new?template=bug_report.yml): a command crashed, printed something wrong, or did something other than what the docs say.
- [False flag or missed contradiction](https://github.com/timothyzhbw-jpg/memory-lab/issues/new?template=false_flag.yml): an audit flagged a claim that is fine, or passed over one it should have flagged. Real cases like these are how the checks get better.
- Security problems: [SECURITY.md](SECURITY.md), not a public issue.

The tracker is public. A minimal synthetic reproduction is the most useful thing you can send. Never paste or commit private evidence or credentials.

## Setting up

You need Git and Python 3.10 or newer.

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab && cd memory-lab
./scripts/setup-local.sh
source .venv/bin/activate
```

The script creates `.venv`, installs the packages in editable mode with the model SDKs (`--minimal` leaves the SDKs out) and what the tests need, as CI does, and runs the tests. If `python3` is too old, name another interpreter: `PYTHON=python3.12 ./scripts/setup-local.sh`. For Windows, [docs/local-setup.md](docs/local-setup.md) has the same steps for PowerShell.

## Before you open a pull request

Run these from the root of the checkout:

```bash
python3 -m pytest
python3 -m pytest --simulate-windows-newlines
memfalsify audit AGENTS.md --evidence . --exclude 'benchmarks/**' --exclude 'examples/**' --exclude 'tests/fixtures/**' --exclude '.worktrees/**' --exclude '.venv*/**' --strict
```

- The first is the test suite, which takes a few minutes. It uses fake clients and stays off the network, so it needs no API key.
- The second runs the suite with Windows newlines (CRLF) in text-mode writes and `CliRunner` output, which catches line-ending bugs on macOS or Linux before Windows CI does.
- The third is the strict dogfood audit: this repository audits its own `AGENTS.md`. Every new file outside the excluded folders and hidden folders such as `.github/` becomes evidence for that audit, so a new sentence can read as contradicting `AGENTS.md`. The check is lexical, and words like "without" or a different number near the same topic are enough to trigger it. If `AGENTS.md` is wrong, fix `AGENTS.md`. If your sentence is right and only reads as a contradiction, say the same thing in positive terms.

CI runs the suite on Linux, macOS and Windows, on the lowest declared dependency versions, and rehearses building and installing the wheels. A pull request should keep all of those green.

## What has to stay true

- MemFalsify reports and proposes. It writes no facts and edits no memory. `orgmemory replay` and `orgmemory audit` only read.
- The default path runs offline with no key. Model backends import `anthropic`, `typesafe_sdk` and `openai` lazily.
- The tools stay independently installable: `memcore` plus either tool works on its own.
- Text from a memory file, evidence, a log or a capture is untrusted. It goes through the helpers in `memcore.display` wherever it reaches a terminal or a report.
- CLIs use Typer, data models use Pydantic, storage is JSONL.
- Every direct dependency declares a lower bound that really works: CI's `lowest` job installs those versions and runs the tests.

## Windows rules

Development mostly happens on macOS, so these rules catch what would otherwise fail only in Windows CI. `AGENTS.md` has the full list, and `tests/test_windows_guard.py` checks text-mode writes, server-test markers and f-strings that only Python 3.12 parses.

- Write test fixtures and any hashed or golden-compared file as bytes: `write_text_exact(path, text)` from `tests/conftest.py` in tests, `path.write_bytes(text.encode("utf-8"))` elsewhere. `python scripts/exact_writes.py` rewrites the tests.
- Write exact output as bytes: `typer.echo(text.encode("utf-8"), nl=False)` or `sys.stdout.buffer`.
- Tests that start memlab-mcp or memlab-ui get `@requires_confinement` from `tests/conftest.py`.
- Paths in reports and Markdown use forward slashes (`as_posix()`).
- Keep backslashes, comments and the enclosing quote out of an f-string's `{...}`.

## When you change these, change that too

| You change | Also update |
|---|---|
| Anything users can see | `## Unreleased` in `CHANGELOG.md`, and the README |
| A doc that has a `.zh-CN.md` pair | Both languages |
| `docs/tutorial.md` | `docs/tutorial.zh-CN.md`: the `bash` blocks must be identical, start with `# exit N`, and they run in the tests |
| An exported name | `tests/public_api.json` and `docs/api.md` with its pair |
| A CLI command or flag | The docs and `skills/`: `tests/test_skills.py` confirms that skills name only real commands and flags |
| A dependency of the GitHub Action | `scripts/update-action-lock.sh`, which needs `uv` and network access |

## Commits and pull requests

Keep a pull request to one change and add a test that fails without it. Write commit messages in short, plain English, with an area prefix when it helps (`memfalsify: ...`, `tests: ...`). Security fixes start with `Security:`.

Several AI agents also work on this repository: [process/README.md](process/README.md) describes their roles and gates. Outside contributors only need this page.

## License

Memory Lab is Apache-2.0. As section 5 of the [LICENSE](LICENSE) says, what you submit is under the same license unless you state otherwise. There is no separate contributor agreement to sign.
