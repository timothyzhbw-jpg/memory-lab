#!/usr/bin/env bash
# Regenerate the hash-pinned requirement files the GitHub Action installs from (action/requirements/*.txt).
# Usage: scripts/update-action-lock.sh            re-lock, keeping the versions already pinned
#        scripts/update-action-lock.sh --upgrade  re-resolve every dependency to its newest release
# Needs uv and network access to PyPI. Review the diff before committing: a lock file is only as
# trustworthy as the person who generated it. Extra arguments are passed to `uv pip compile`.
set -euo pipefail
cd "$(dirname "$0")/.."

# One lock per verifier set: lexical (default), llm ([llm]), jev ([jev]), openai ([openai]), and all (dual and
# cascade: [llm] and [jev]).
# --universal resolves once for every platform and Python >= 3.10 (the packages' requires-python), so one
# file serves Linux, macOS and Windows on any Python the action's python-version input can select.
for set in lexical llm jev openai all; do
  uv pip compile --universal --python-version 3.10 --generate-hashes \
    --custom-compile-command "scripts/update-action-lock.sh" \
    "$@" "action/requirements/$set.in" -o "action/requirements/$set.txt"
done
