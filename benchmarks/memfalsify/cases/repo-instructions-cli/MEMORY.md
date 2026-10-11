# AGENTS.md notes: fernhook

<!-- Synthetic benchmark data: Fernhook is a fictional CLI. Fernwiki is a fictional wiki. -->

## Environment

- Fernhook requires Python 3.11 or newer.
- Python 3.10 is the oldest version fernhook supports.
- Release 0.9.0, shipped on 2026-02-03, added the `--prefer-local` flag.

## Checks and tests

- `just test` runs ruff and pyright before the test suite.
- `just ci` runs the linter, the type checker and the tests, in that order.
- The docs server listens on 127.0.0.1 port 8000.
- The nightly publish job seems to run at 03:00 UTC.
- A normal pytest run rewrites HTTP cassettes.
- Failures of the cassette rewrite are probably Fernwiki outages, not fernhook bugs.
- uv is pinned to 0.4.20 because 0.5 breaks the Bitbucket cache.

## Command behaviour

- `push` uploads changed notes with 8 pages in parallel by default.
- `push` retries a failed upload 3 times before giving up.
- On a conflict, `push` always keeps the wiki page and never uploads the local note.
- `status` never writes files, and it exits with code 1 when something changed.
- The Fernwiki API currently allows 60 requests per minute per token.

## Rules

- `FERNHOOK_TOKEN` overrides the token in the config file.
- The default ignore patterns are .obsidian/*, *.tmp and drafts/*.
- The docs server listens on 127.0.0.1 port 8000.
- Pyright's include list excludes src.
- Pull requests are squash-merged.
- The publish step waits for a manual approval before uploading to PyPI.
- The publish step runs for every tag.
