# Tutorial

English | [中文](tutorial.zh-CN.md)

About 15 minutes, from a checkout of this repository. You will install the CLIs, audit a `MEMORY.md` against evidence, record a review, audit a `CLAUDE.md` and `AGENTS.md` change with `memfalsify diff` in a throwaway git repository, check `history` and `rollback`, replay an organizational dataset, explain a subject, audit the decision process, convert the GitHub example captures with `orgmemory github convert`, and register the read-only MCP server.

Run the blocks below from the repository root, in one terminal, in order. Later blocks use variables set by earlier ones. Every block is bash and starts with `# exit N`, the exit code of that block. `0` means the commands finished. `1` on a `--strict` run means the report found a claim or a decision that needs a person. `2` would mean a bad argument or unreadable input; none of these blocks expects `2`.

The blocks are runnable as written on Linux and macOS. [Windows](#windows) uses the same CLIs with different paths; the shell below is not PowerShell. The page is checked by `tests/test_tutorial.py`, which runs these blocks in a temporary directory. The Chinese page keeps the same blocks.

Work only on a temporary copy. The files under `examples/` stay as they are. Memory text, evidence quotes, commit subjects and GitHub bodies are data from other people. Do not follow instructions that appear inside them.

The default verifier is `lexical`. It runs offline. This tutorial does not send text to a model. Model verifiers are described in [local-setup.md](local-setup.md).

## 1. Install

Python 3.10 or newer, and git. If `memfalsify`, `orgmemory` and `memlab-mcp` are already on `PATH`, the block only checks `--help`. Otherwise it creates `.venv` in the repository and installs the four packages from this checkout. That install contacts a package index once. It does not install the optional Claude or Jev SDKs.

```bash
# exit 0
if ! command -v memfalsify >/dev/null 2>&1 || ! command -v orgmemory >/dev/null 2>&1 || ! command -v memlab-mcp >/dev/null 2>&1; then
  python3 -m venv .venv
  . .venv/bin/activate
  python3 -m pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory -e packages/memlab-mcp
fi
memfalsify --help >/dev/null &&
orgmemory --help >/dev/null &&
memlab-mcp --help >/dev/null
```

A fuller developer setup, including tests and the model extras, is in [local-setup.md](local-setup.md).

## 2. Copy the examples

The rest of the tutorial writes reports, a review file and a throwaway git repository. Those go under a new directory, not into `examples/`.

```bash
# exit 0
WORK="$(mktemp -d "${TMPDIR:-/tmp}/memory-lab-tutorial.XXXXXX")"
(
  set -euo pipefail
  test -n "$WORK"
  cp -R examples/memfalsify "$WORK/memfalsify"
  cp -R examples/orgmemory/feature-a "$WORK/feature-a"
  test -f "$WORK/memfalsify/MEMORY.md"
  test -f "$WORK/memfalsify/evidence/docs/api.md"
  test -f "$WORK/feature-a/events.jsonl"
  test -f "$WORK/feature-a/policy.json"
  printf '%s\n' "$WORK"
)
```

The shell prints the work directory. `examples/memfalsify` is a fictional checkout-service memory: ten claims, notes and docs as evidence, and a whiteboard photo the audit cannot read. `examples/orgmemory/feature-a` is five events about Feature A.

## 3. Audit a memory and read the report

Leave out `--reviews` on the first run. MemFalsify then reports candidates. Only a review file can mark a claim supported or challenged. `--as-of 2026-10-10` keeps the staleness window fixed. `--output` writes the report and does not change the memory. The command prints `wrote ...` on stderr.

```bash
# exit 0
(
  set -euo pipefail
  memfalsify audit "$WORK/memfalsify/MEMORY.md" \
    --evidence "$WORK/memfalsify/evidence" \
    --as-of 2026-10-10 \
    --output "$WORK/memfalsify-report.md"
  grep -F -q "# MemFalsify audit report" "$WORK/memfalsify-report.md"
  grep -F -q "no memory was modified" "$WORK/memfalsify-report.md"
  grep -F -q "POSSIBLE COUNTEREVIDENCE" "$WORK/memfalsify-report.md"
  grep -F -q "The public API rate limit is 60 requests per minute per key." "$WORK/memfalsify-report.md"
  grep -F -q "media/whiteboard.png" "$WORK/memfalsify-report.md"
)
```

Open `$WORK/memfalsify-report.md`. The memory claims the public API allows 100 requests per minute. `evidence/docs/api.md` says 60, and the report quotes that sentence with its path, line and SHA-256. The same report quotes the decision log against "Feature A is approved", and it lists `media/whiteboard.png` under skipped evidence because a photo is not text and no OCR sidecar describes it ([transcripts and OCR](multimodal.md)). `NO EVIDENCE` means unverified, not false. Proposed actions are suggestions. The `replacement` field stays empty.

`examples/memfalsify/reviews.jsonl` is the same kind of file, already filled in for two other claims. The copy you just made still has it. The next section writes a separate file so you can see one confirmation land.

## 4. Record a review

A review is one JSON object per line: `memory_id`, `relation` (`supports` or `contradicts`), `path` relative to the evidence directory, a `quote` copied verbatim from that file, and a `reviewer`. `memfalsify claims` prints the ids. The block selects the rate-limit claim by its text, checks that the quote is really in `docs/api.md`, and appends one line to `reviews-tutorial.jsonl`.

```bash
# exit 0
(
  set -euo pipefail
  python3 - "$WORK/memfalsify" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
out = subprocess.check_output(["memfalsify", "claims", str(root / "MEMORY.md")], text=True)
want = "The public API rate limit is 100 requests per minute."
claim_id = ""
for line in out.splitlines():
    if not line.strip():
        continue
    obj = json.loads(line)
    if obj["text"] == want:
        claim_id = obj["memory_id"]
        break
if not claim_id:
    raise SystemExit("rate-limit claim not found")
review = {
    "memory_id": claim_id,
    "relation": "contradicts",
    "path": "docs/api.md",
    "quote": "The public API rate limit is 60 requests per minute per key.",
    "reviewer": "tutorial-reader",
    "note": "The API document states 60, not 100.",
}
quote_file = (root / "evidence" / review["path"]).read_text(encoding="utf-8")
if review["quote"] not in quote_file:
    raise SystemExit("quote is not verbatim in the evidence file")
(root / "reviews-tutorial.jsonl").write_text(
    json.dumps(review, ensure_ascii=False) + "\n", encoding="utf-8")
print(claim_id)
PY
)
```

Audit again with that file. The rate-limit claim moves to `CHALLENGED`, confirmed by `tutorial-reader`.

```bash
# exit 0
(
  set -euo pipefail
  memfalsify audit "$WORK/memfalsify/MEMORY.md" \
    --evidence "$WORK/memfalsify/evidence" \
    --reviews "$WORK/memfalsify/reviews-tutorial.jsonl" \
    --as-of 2026-10-10 \
    --output "$WORK/memfalsify-reviewed.md"
  grep -F -q "# MemFalsify audit report" "$WORK/memfalsify-reviewed.md"
  grep -F -q "CHALLENGED" "$WORK/memfalsify-reviewed.md"
  grep -F -q "confirmed by tutorial-reader" "$WORK/memfalsify-reviewed.md"
  grep -F -q "The public API rate limit is 60 requests per minute per key." "$WORK/memfalsify-reviewed.md"
)
```

The next block exits 1 on purpose. `--strict` returns 1 when any claim is challenged, conflicted, disputed or has possible counterevidence. A confirmed contradiction is one of those. The report is still written. Exit 2 is reserved for errors, and this run is not an error.

```bash
# exit 1
memfalsify audit "$WORK/memfalsify/MEMORY.md" \
  --evidence "$WORK/memfalsify/evidence" \
  --reviews "$WORK/memfalsify/reviews-tutorial.jsonl" \
  --as-of 2026-10-10 \
  --strict \
  --output "$WORK/memfalsify-strict.md"
```

## 5. Audit a CLAUDE.md and AGENTS.md change

Agents read `CLAUDE.md` and `AGENTS.md` from git. `memfalsify diff` compares one of those files, and the local `.md` files it links to, between a revision and the working tree. It audits only the claims that were added or changed, against the evidence as it is now. Nothing is checked out.

The block makes a second copy of the example, with the memory text saved as both `CLAUDE.md` and `AGENTS.md` (the link to `infra.md` stays beside them). It commits that text in a repository under `$WORK`, then changes November to December in both files. The repository's identity and hooks are local to that directory, so the block does not depend on your global git config and does not commit anything in the Memory Lab checkout.

```bash
# exit 0
REPO="$WORK/instructions"
(
  set -euo pipefail
  mkdir -p "$REPO/no-hooks"
  cp examples/memfalsify/MEMORY.md "$REPO/CLAUDE.md"
  cp examples/memfalsify/MEMORY.md "$REPO/AGENTS.md"
  cp examples/memfalsify/infra.md "$REPO/infra.md"
  cp -R examples/memfalsify/evidence "$REPO/evidence"
  git -C "$REPO" init -q -b main
  git -C "$REPO" config user.name "Tutorial User"
  git -C "$REPO" config user.email "tutorial@example.com"
  git -C "$REPO" config commit.gpgsign false
  git -C "$REPO" config core.autocrlf false
  git -C "$REPO" config core.hooksPath "$REPO/no-hooks"
  git -C "$REPO" add CLAUDE.md AGENTS.md infra.md
  git -C "$REPO" commit -q -m "Initial agent instructions"
  python3 - "$REPO" <<'PY'
import sys
from pathlib import Path

repo = Path(sys.argv[1])
old = "Feature A (bulk export) is approved for the November release."
new = "Feature A (bulk export) is approved for the December release."
for name in ("CLAUDE.md", "AGENTS.md"):
    path = repo / name
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(name + " is missing the example sentence")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY
  memfalsify diff "$REPO/CLAUDE.md" --base HEAD --evidence "$REPO/evidence" --as-of 2026-10-10 \
    --output "$WORK/diff-claude.md"
  memfalsify diff "$REPO/AGENTS.md" --base HEAD --evidence "$REPO/evidence" --as-of 2026-10-10 \
    --output "$WORK/diff-agents.md"
  grep -F -q "# MemFalsify diff report" "$WORK/diff-claude.md"
  grep -F -q "December release" "$WORK/diff-claude.md"
  grep -F -q "# MemFalsify diff report" "$WORK/diff-agents.md"
  grep -F -q "December release" "$WORK/diff-agents.md"
)
```

On a pull request, `--base` is the base revision and `--head` is the branch tip. Here `--base HEAD` is the commit you just made, and the head side is the working tree. Open `$WORK/diff-claude.md`. The November sentence and the December sentence are one changed claim. Claims that did not change, including the ones in `infra.md`, are counted and not re-audited.

The changed sentence still says Feature A is approved. The decision log still says the product manager rejects it, so the touched claim needs attention. The next block exits 1 for that reason. The report is written to `$WORK/diff-claude-strict.md`.

```bash
# exit 1
memfalsify diff "$REPO/CLAUDE.md" --base HEAD --evidence "$REPO/evidence" --as-of 2026-10-10 \
  --strict --output "$WORK/diff-claude-strict.md"
```

## 6. History and rollback

`memfalsify history` follows the first-parent line of `HEAD` and the working tree. For the December sentence, the introduction is an uncommitted edit. The report also names the earlier wording when `diff` would pair the two sentences.

```bash
# exit 0
(
  set -euo pipefail
  memfalsify history "$REPO/CLAUDE.md" > "$WORK/history-claude.md"
  grep -F -q "# MemFalsify claim history" "$WORK/history-claude.md"
  grep -F -q "uncommitted changes in the working tree" "$WORK/history-claude.md"
  grep -F -q "December release" "$WORK/history-claude.md"
)
```

`memfalsify rollback` prints a patch and does not apply it, and does not edit the memory. `--claim` is the id of the December sentence (the current text). `--to HEAD` is the committed November wording. `-o` writes the patch as exact bytes. Use `-o` rather than shell redirection when you want the file to stay byte-for-byte, which matters on Windows.

```bash
# exit 0
(
  set -euo pipefail
  CLAIM="$(memfalsify claims "$REPO/CLAUDE.md" | python3 -c '
import json, sys
want = "Feature A (bulk export) is approved for the December release."
found = ""
for line in sys.stdin:
    if not line.strip():
        continue
    obj = json.loads(line)
    if obj["text"] == want:
        found = obj["memory_id"]
        break
if not found:
    raise SystemExit(1)
print(found)
')"
  test -n "$CLAIM"
  memfalsify rollback "$REPO/CLAUDE.md" --claim "$CLAIM" --to HEAD -o "$WORK/restore.patch"
  grep -F -q "# MemFalsify rollback patch" "$WORK/restore.patch"
  grep -F -q "November release" "$WORK/restore.patch"
)
```

Read `$WORK/restore.patch`. Applying it is a separate decision. From `$REPO`, after you have read the patch: `git apply "$WORK/restore.patch"`. This tutorial stops at the patch.

`AGENTS.md` has the same December edit. `memfalsify history "$REPO/AGENTS.md"` and `memfalsify rollback` take that path the same way.

## 7. Replay an organizational dataset

`orgmemory replay` rebuilds state from `events.jsonl`, `policy.json` and `evidence.jsonl`. It does not append anything. Thursday's messages are newer than Wednesday's rejection. The policy lets only the product manager approve, so the account manager's "approval" is recorded and does not change the status.

```bash
# exit 0
(
  set -euo pipefail
  orgmemory replay "$WORK/feature-a" > "$WORK/replay.md"
  grep -F -q "current_status: rejected" "$WORK/replay.md"
  grep -F -q "reconsideration_requested: true" "$WORK/replay.md"
  grep -F -q "ev-wed-reject" "$WORK/replay.md"
  grep -F -q "ev-thu-sales-approve" "$WORK/replay.md"
  grep -F -q "read_only" "$WORK/replay.md"
)
```

The subject's name has a space, so it is quoted. `explain` cites the evidence of the decision that still stands.

```bash
# exit 0
(
  set -euo pipefail
  orgmemory explain "$WORK/feature-a" "Feature A" > "$WORK/explain.md"
  grep -F -q "current_status: rejected" "$WORK/explain.md"
  grep -F -q "ev-wed-reject" "$WORK/explain.md"
  grep -F -q "Feature A is rejected for the November release until the billing migration lands." "$WORK/explain.md"
)
```

## 8. Audit the decision process

`orgmemory audit` replays the same log and reports where the process needs attention. `--as-of` drops later events and is the clock for staleness, so the result does not move from one day to the next. On this dataset as of 2026-10-31 the customer's reconsideration has been waiting on the product manager, and the account manager's approval attempt is an `unauthorized_attempts` finding. Both are medium. `--strict` exits 1 only when a finding is high, so this run exits 0.

```bash
# exit 0
(
  set -euo pipefail
  orgmemory audit "$WORK/feature-a" --as-of 2026-10-31T00:00:00Z --strict > "$WORK/decision-audit.md"
  grep -F -q "# Organizational decision audit" "$WORK/decision-audit.md"
  grep -F -q "stale_reconsideration" "$WORK/decision-audit.md"
  grep -F -q "unauthorized_attempts" "$WORK/decision-audit.md"
  grep -F -q "read_only" "$WORK/decision-audit.md"
)
```

## 9. Convert the GitHub example captures

`examples/orgmemory/github-acme-web/` is the dataset those captures produce, including a replay report. The raw captures are the inputs, so they are stored at `tests/fixtures/github/capture-1.json` and `capture-2.json` rather than inside `examples/`. The label map (`github.json`) and the role policy (`policy.json`) come from the example. The importer never reads or writes `policy.json`. Copying the policy in now means `explain` and `validate` can run after convert. `convert` itself only needs the directory to exist.

The block starts from an empty log and applies capture 1, then capture 2, offline. A second convert of the same capture appends nothing new. No GitHub token is used.

```bash
# exit 0
(
  set -euo pipefail
  mkdir -p "$WORK/github"
  cp examples/orgmemory/github-acme-web/github.json "$WORK/github/github.json"
  cp examples/orgmemory/github-acme-web/policy.json "$WORK/github/policy.json"
  orgmemory github convert "$WORK/github" tests/fixtures/github/capture-1.json
  orgmemory github convert "$WORK/github" tests/fixtures/github/capture-2.json
  orgmemory explain "$WORK/github" "acme/web#1" > "$WORK/github-explain.md"
  grep -F -q "current_status: rejected" "$WORK/github-explain.md"
  grep -F -q "reconsideration_requested: true" "$WORK/github-explain.md"
  grep -F -q "pending_approval: [product_manager]" "$WORK/github-explain.md"
  orgmemory validate "$WORK/github"
)
```

`acme/web#1` stays rejected. The product manager closed it as not planned. A later label and a reopen show up as attempts the policy did not apply. `validate` prints `ok:` and, on this policy, notes the GitHub accounts that have no role. Their events are in the log and cannot change state until you map them yourself.

Exit codes for `orgmemory github convert`: `0` complete, `2` nothing appended because the input was refused, `3` an incomplete capture. Both captures in the fixture are complete, so both commands exit 0. The block's exit code is `validate`'s.

## 10. Register the read-only MCP server

`memlab-mcp` speaks MCP on stdin and stdout. This block does not start it, because a server with no client would wait. `--help` only prints usage. `--root` is the one directory the tools may read. Paths a client sends are relative to that directory (`memfalsify/MEMORY.md`, `orgmemory/feature-a`). The tutorial root is the checkout's `examples/` directory. The process is read-only: it does not write reports, reviews, events or policy. Model verifiers stay off unless the server is started with `--allow-model-verifiers`. This tutorial does not pass that flag.

Registration has to match the installed client. The block reads `claude mcp add --help` and `codex mcp add --help` and runs `mcp add` only when that help still shows the stdio form used here (`--` before the command, so `--root` is not eaten by the client). If a client is missing, or its help text does not match, the block prints a pointer to [mcp.md](mcp.md) and leaves your configuration alone. [mcp.zh-CN.md](mcp.zh-CN.md) is the same page in Chinese.

The command that runs when Claude Code's help matches:

`claude mcp add memlab -- "$(command -v memlab-mcp)" --root "$(pwd)/examples"`

The command that runs when Codex's help matches:

`codex mcp add memlab -- "$(command -v memlab-mcp)" --root "$(pwd)/examples"`

`-s` / `--scope` on Claude Code and `--env` on Codex are in the same help text. The defaults are enough here: stdio, and the server's own environment.

```bash
# exit 0
(
  set -euo pipefail
  SERVER="$(command -v memlab-mcp)"
  ROOT_DIR="$(pwd)/examples"
  test -n "$SERVER"
  test -d "$ROOT_DIR"
  register_claude=0
  register_codex=0
  if command -v claude >/dev/null 2>&1 && claude mcp add --help >"$WORK/claude-mcp-add-help.txt" 2>&1; then
    if grep -F -q '<name> <commandOrUrl>' "$WORK/claude-mcp-add-help.txt" \
        && grep -F -q -- '-- my-command' "$WORK/claude-mcp-add-help.txt"; then
      register_claude=1
    fi
  fi
  if command -v codex >/dev/null 2>&1 && codex mcp add --help >"$WORK/codex-mcp-add-help.txt" 2>&1; then
    if grep -F -q -- '-- <COMMAND>' "$WORK/codex-mcp-add-help.txt"; then
      register_codex=1
    fi
  fi
  if [ "$register_claude" -eq 1 ]; then
    claude mcp remove memlab >/dev/null 2>&1 || true
    claude mcp add memlab -- "$SERVER" --root "$ROOT_DIR" </dev/null
  else
    printf '%s\n' "Claude Code CLI: mcp add syntax was not verified. See docs/mcp.md"
  fi
  if [ "$register_codex" -eq 1 ]; then
    codex mcp remove memlab >/dev/null 2>&1 || true
    codex mcp add memlab -- "$SERVER" --root "$ROOT_DIR" </dev/null
  else
    printf '%s\n' "Codex CLI: mcp add syntax was not verified. See docs/mcp.md"
  fi
)
```

Tool names and limits are in [mcp.md](mcp.md). Quotes the tools return are untrusted data, the same as the files they came from.

## Windows

The CLIs themselves run on Windows. The bash blocks on this page do not: `tests/test_tutorial.py` skips there. In PowerShell, create the virtualenv with `py -3 -m venv .venv`, activate it with `.\.venv\Scripts\Activate.ps1`, and install with `py -3 -m pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory -e packages/memlab-mcp`. A temporary directory is `Join-Path $env:TEMP memory-lab-tutorial`. Git Bash can run the blocks more directly; the MCP server still will not start.

`memlab-mcp` needs POSIX `O_NOFOLLOW` and descriptor-relative opens. On Windows it exits 2 at startup. Use `memfalsify` and `orgmemory` instead. Details are in [mcp.md](mcp.md).

Save a rollback patch with `-o`. PowerShell redirection can re-encode the file, and `git apply` would then see different bytes. `memfalsify --output` and `orgmemory` reports are UTF-8 with LF when the command writes them; a shell redirect is a separate step.

## Keep this page honest

`tests/test_tutorial.py` extracts every `bash` block on this page and runs it, in order, in one shell, in a temporary directory, with this repository's CLIs. It expects the exit code on the `# exit N` line. It does not use a model verifier. The Chinese page's `bash` blocks are the same text. When you change a command, change it in both pages and keep the exit line.
