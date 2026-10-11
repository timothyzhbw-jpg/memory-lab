# AGENTS.md notes: quayside

<!-- Synthetic benchmark data: Quayside is a fictional API and SDK. -->

## Environment

- The SDK requires Python 3.10 or newer.
- Python 3.11 is the oldest version the SDK supports.

## Checks and tests

- `make test` runs ruff and mypy before the test suite.
- Run `make check` before opening a pull request.
- The nightly live job seems to run at 03:00 UTC.
- Live tests need the `QUAYSIDE_API_KEY` environment variable.
- Failures of the nightly live job are probably sandbox outages, not SDK bugs.
- mypy is pinned to 1.11.2 because 1.12 breaks the pydantic plugin.

## Client behaviour

- The client retries a failed request 3 times before giving up.
- The client retries on any 5xx response.
- The retry loop assumes the server sends `Retry-After` in seconds.
- Catching `QuaysideError` handles every failure the SDK can raise.
- The production API currently allows 60 requests per minute per key.
- Release 1.8.0, shipped on 2026-02-03, added retries for HTTP 429.

## Rules

- Never import `requests` in the SDK package.
- Run `make check` before opening a pull request.
- Pull requests are squash-merged.
- The publish stage waits for a manual approval before uploading to PyPI.
