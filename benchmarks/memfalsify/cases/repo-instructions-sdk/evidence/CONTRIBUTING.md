# Contributing to quayside

## Setup

Install [uv](https://docs.astral.sh/uv/) and run `make install`. It creates `.venv` with the `cli` extra and the `dev` dependency group.

## Checks

- `make check` runs lint, type checks and tests together, the same as CI.
- `make test` only runs pytest. It fails if line coverage drops below 85%.
- `make format` runs `ruff format` and the safe ruff fixes.
- mypy runs in strict mode over the whole `quayside` package.

## Tests

Tests marked `live` call the Quayside sandbox API and are deselected by default (see `addopts` in pyproject.toml).
Run them with `make test-live`.
Live tests authenticate with `QUAYSIDE_SANDBOX_KEY`, not with `QUAYSIDE_API_KEY`.
Unit tests mock HTTP with respx and must not open network connections.

## Dependencies

Do not import `requests` anywhere under `src/quayside/`.
The SDK has used httpx only since 2.0.
New runtime dependencies need a maintainer's approval in the pull request.

## Pull requests

Rebase on `main` before requesting review.
Run `make check` locally before you open a pull request.
Every pull request needs one approving review from a maintainer, and CI must be green.
Add a line under "Unreleased" in CHANGELOG.md for any user-visible change.

## Releases

Maintainers bump `version` in pyproject.toml, then push a `vX.Y.Z` tag.
The `publish` stage in azure-pipelines.yml builds the wheel and uploads it to PyPI.
There is no `make release` target.
