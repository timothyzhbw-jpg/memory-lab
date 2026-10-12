#!/usr/bin/env bash
# Set up a local development environment: .venv, the packages (editable, including memlab-mcp), and the test suite.
# Usage: ./scripts/setup-local.sh [--minimal]   (--minimal skips the optional Claude / Jev / OpenAI verifier SDKs)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "error: Python 3.10+ is required; set PYTHON=/path/to/python3.10 or newer" >&2
  exit 1
fi

memfalsify="packages/memfalsify[all]"
[ "${1:-}" = "--minimal" ] && memfalsify="packages/memfalsify"

[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e packages/memcore -e "$memfalsify" -e packages/orgmemory -e packages/memlab-mcp pytest
.venv/bin/python -m pytest -q

cat <<'NEXT'

Ready. Activate the environment with:  source .venv/bin/activate
Model verifiers read their keys from the environment (never commit them):
  export ANTHROPIC_API_KEY=...     # memfalsify --verifier llm / dual / cascade
  export TYPESAFE_API_KEY=...      # memfalsify --verifier jev / dual / cascade
  export OPENAI_API_KEY=...        # memfalsify --verifier openai (or XAI_API_KEY, DEEPSEEK_API_KEY, GEMINI_API_KEY
                                   # with --provider grok / deepseek / gemini; --provider ollama needs none)
See docs/local-setup.md for running the benchmark and continuing with Claude Code or Codex.
NEXT
