# Memory Lab: Verified Memory Infrastructure for AI Agents

Agent memory tools mostly store and retrieve. This project is about **keeping what an agent remembers true**: every memory should have evidence, a place in time, and a traceable origin, and information without authority should not be able to change the current state.

`Remember → Verify → Challenge → Resolve → Evolve`

It contains two independent tools that share one evidence core:

| | **MemFalsify** | **Verified Organizational Memory** (`orgmemory`) |
|---|---|---|
| Question | *Is this memory backed by evidence?* | *Is this the current, authorized state?* |
| Input | `MEMORY.md` + a directory of evidence files | Event log + role policy + evidence manifest |
| Output | Audit report: cited quotes, issues, proposed actions | Current state per subject, decision provenance, audit trail |
| Writes | Nothing (only the report you ask for) | Nothing on replay; `append` only appends |

By default everything runs offline with no API keys; optional model verifiers (Claude and TypeSafe AI's Jev) can be switched on. Python 3.10+, Apache-2.0.

```text
        Claude Code / Codex / other agents  (skills/)
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
     MemFalsify                     orgmemory
  evidence support for          state + authority for
  remembered claims             organizational decisions
          └───────────────┬───────────────┘
                          ▼
           memcore: hashed, exact-quote evidence
```

## Quick start

```bash
git clone <this repo> memory-lab && cd memory-lab
python -m venv .venv && source .venv/bin/activate
pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory
```

Install only what you need: `memcore` plus either tool works on its own.

### MemFalsify: audit a MEMORY.md

```bash
cd examples/memfalsify
memfalsify audit MEMORY.md --evidence evidence --reviews reviews.jsonl --as-of 2026-10-10
```

The example memory holds ten claims about a fictional service. The audit ([full report](examples/memfalsify/report.md)) finds:

| Memory | Result | Why |
|---|---|---|
| Feature A (bulk export) is approved for the November release. | **challenged** | A reviewer confirmed the decision log: *"Product manager rejects Feature A…"*; also conflicts with another memory |
| The public API rate limit is 100 requests per minute. | possible counterevidence | `docs/api.md:3` says 60 |
| The main database runs PostgreSQL 15. | possible counterevidence | `docs/database.md:3` says 16 |
| Deployments always go through the staging cluster. | possible counterevidence, overgeneralization | An incident note records a hotfix that skipped staging |
| The bulk export will ship next week. | no evidence, relative time | "next week" relative to when? |
| 2026-03-02: Feature B was postponed to Q3. | no evidence, possibly stale | Over 180 days old |
| We probably use squash merges for pull requests. | possible support, inference marker | `CONTRIBUTING.md` agrees, but the memory is phrased as a guess |
| Bulk export depends on the billing schema migration. | **supported**, duplicate | Confirmed by a reviewer; stated in two files |

Every quote carries `path:line`, character offsets and the SHA-256 of the file it came from. A whiteboard photo in the evidence folder is listed as skipped, not guessed at.

**How status is decided.** MemFalsify nominates evidence by itself, but only a human review can settle a memory:

| Status | Meaning |
|---|---|
| `supported` / `challenged` / `conflicted` | Human-confirmed evidence in `reviews.jsonl` supports / contradicts / does both |
| `candidate_counterevidence` | The verifier found a passage that may contradict the memory (lexically: opposing terms such as *approved* / *rejected*, different numbers, or negation) |
| `disputed` | Two verifiers disagree (see *Verifier backends*) |
| `candidate_support` | A passage that restates the memory |
| `insufficient_evidence` | Nothing found. That means unverified, not false |

Independent of evidence, each memory is checked for `overgeneralization` (always, all, never…), `relative_time` without a date, `possibly_stale` dates, `inference_marker` (probably, seems…), `internal_conflict` with another memory, and `duplicate`.

Proposed actions such as `verify_counterevidence`, `add_absolute_date` and `narrow_scope` are suggestions only. The `replacement` field is always empty: MemFalsify never writes facts.

Other commands and options: `memfalsify claims MEMORY.md` lists claim IDs; `--format json`, `--output report.md`, `--strict` (exit 1 when anything needs attention, for CI), `--stale-days`, and `--no-follow-links`.

### Organizational Memory: replay decisions

```bash
orgmemory replay examples/orgmemory/feature-a
```

| When | Who (role) | Event |
|---|---|---|
| Mon | customer | requests Feature A |
| Tue | engineer | raises a technical risk |
| Wed | product manager | **rejects** Feature A |
| Thu | customer | requests Feature A again |
| Thu | account manager | posts "Feature A is approved" |

Result ([full report](examples/orgmemory/replay-report.md)):

```yaml
subject: Feature A
current_status: rejected
reconsideration_requested: true
pending_approval: [product_manager]
open_risks: ["Bulk export reads the billing schema, which is mid-migration"]
last_authorized_decision: ev-wed-reject (reject by zhou)
unauthorized_change_attempts: [ev-thu-sales-approve]
```

Thursday's newer messages do not overwrite Wednesday's decision. The repeated request becomes a reconsideration awaiting the product manager. The account manager's "approval" is logged but ignored, because the policy does not let that role approve.

Other commands:

```bash
orgmemory explain  <dataset> "Feature A"      # why it is in this state, with quoted evidence
orgmemory replay   <dataset> --as-of 2026-10-07T23:59:59Z   # state at a past time
orgmemory validate <dataset>                  # check every event, hash and quote
orgmemory evidence add <dataset> --id ... --path ... --quote "..." --source-uri ... --source-kind ... --captured-at ...
orgmemory append   <dataset> --event '{"id": ..., ...}'
```

## Data formats

**`reviews.jsonl`** (MemFalsify, optional): human-confirmed assertions.

```json
{"memory_id": "m-27e874e0", "relation": "contradicts", "path": "notes/2026-10-07-decision-log.md", "quote": "Product manager rejects Feature A (bulk export) for the November release.", "reviewer": "demo-reviewer", "note": "optional"}
```

`memory_id` is derived from the memory's normalized text. If the memory is edited, the ID changes and old reviews stop applying, so it must be reviewed again. `quote` must appear verbatim in `path`, which is relative to the evidence directory.

**Organizational dataset**: a directory with three files.

- `events.jsonl`: one event per line, append-only. Fields: `id`, `timestamp` (with timezone; it is the effective time), `actor`, `action`, `subject`, `evidence_ids` (at least one), plus `note` (required for `raise_risk`), `dependency` (required for `dependency`) and `supersedes` (required for `revoke`, which must target the current decision). Actions: `request`, `reconsider`, `raise_risk`, `dependency`, `approve`, `reject`, `revoke`, `comment`.
- `policy.json`: `{"actors": {actor: [roles]}, "permissions": {action: [roles]}}`. Unknown actors have no roles. `pending_approval` lists the roles that can both approve and reject.
- `evidence.jsonl`: `id`, `path`, `sha256` of the raw bytes, exact `quote` and its `[start, end)` character span, `source_uri`, `source_kind`, `captured_at`. Use `orgmemory evidence add` instead of hashing by hand.

Replay rules: events are ordered by effective time, and ties keep file order. The whole log is validated before replay, including events after `--as-of`. An authorized `approve` / `reject` sets the status and clears reconsideration, pending approval and ignored attempts. Reversals and same-time conflicting decisions are recorded under `conflicts`.

## Agent skills

[`skills/memfalsify`](skills/memfalsify/SKILL.md) and [`skills/orgmemory`](skills/orgmemory/SKILL.md) teach an agent the workflow and its boundaries: report first, change memory only with permission, never self-confirm a review, never touch the policy. Copy them into your agent's skills directory:

```bash
cp -r skills/memfalsify skills/orgmemory ~/.claude/skills/   # Claude Code
cp -r skills/memfalsify skills/orgmemory ~/.codex/skills/    # Codex
```

The CLIs must be installed in the environment the agent's shell uses.

## Verifier backends: lexical, LLM, Jev

A verifier judges one memory against one evidence passage as `supports`, `contradicts` or `unrelated`, with a rationale. Three backends ship, and `dual` runs two of them side by side:

| `--verifier` | Who judges | Needs | Trade-off |
|---|---|---|---|
| `lexical` (default) | Term overlap, opposing terms, numbers, negation | Nothing | Offline, deterministic, explainable; misses paraphrases |
| `llm` | Claude reads the passage and explains its verdict (`--llm-model`, default `claude-opus-5-5`) | `pip install 'memfalsify[llm]'` and Anthropic credentials | Understands paraphrase and context; costs API calls and **sends memory and evidence text to the API** |
| `jev` | [Jev](https://docs.typesafe.ai), TypeSafe AI's decision-only judgment model, picks one of the three relations and returns a probability for each (`--jev-model`, default `jev-latest`) | `pip install 'memfalsify[jev]'` and `TYPESAFE_API_KEY` | A small fraction of an LLM judge's cost, with a calibrated confidence; no written explanation, and **sends memory and evidence text to the TypeSafe API** |
| `dual` | `llm` and `jev`, independently | Both | Agreement passes through; disagreement becomes `disputed`, and the report shows both opinions |

```bash
memfalsify audit MEMORY.md --evidence evidence --verifier dual
```

The two model backends work differently on purpose: Claude reasons its way to a verdict and writes why, while Jev outputs a decision with probabilities. Both get the same written definitions of the three relations (`RELATION_CRITERIA` in `verify.py`), so when they disagree it is about the evidence, not the wording of the task, and a human reads that evidence. A Jev answer whose confidence is below `JevVerifier(threshold=0.5)` counts as `unrelated`; the rationale keeps all three probabilities and the resolved model version (for example `jev-1.13.0`, since `jev-latest` is an alias).

**Which passages a model sees.** The term index still pre-selects passages. Model verifiers use a looser threshold (`--min-coverage`, default 0.2 instead of 0.5) so paraphrases are judged, and only the best `--max-judged` passages per memory (default 8) are sent. That bounds cost at *memories × max-judged* calls per model. A passage that shares no terms with a memory is never judged.

The LLM verifier asks for structured output, so every verdict fits the schema, and its instructions treat memory and evidence as quoted data. On models that support it, it opts in to the server-side refusal fallback; a refused request is recorded as no verdict (`unrelated`, with the stop reason in the rationale). For both model backends, an API failure or missing key stops the audit with exit code 2 rather than producing a partial report.

From Python:

```python
from memfalsify.audit import audit
from memfalsify.jev import JevVerifier
from memfalsify.llm import LLMVerifier
from memfalsify.verify import DualVerifier

report = audit("MEMORY.md", "evidence", verifier=DualVerifier(LLMVerifier(), JevVerifier()),
               min_coverage=0.2, max_judged=8)
```

Any object with a `name` and a `judge(claim, passage) -> Judgment` method is a verifier, so other models plug in the same way. Model judgments stay candidates: they never replace the human review step.

## Trust boundaries and limitations

- A hash proves a quote exists in particular bytes. It does not prove the source is authentic or that the quote means what someone says it means.
- Lexical and model candidates are leads, not findings. Only `reviews.jsonl` settles a memory.
- `--verifier llm` sends memory claims and evidence passages to the Anthropic API, `jev` sends them to the TypeSafe API, and `dual` sends them to both. Use them only on material you are allowed to send there.
- Actor IDs and `policy.json` are trusted input. There is no identity authentication, signed provenance or policy versioning yet, and changing the policy changes every replay.
- Evidence is UTF-8 text only. Images, audio, video and PDF are reported as skipped.
- This is a research MVP, not production infrastructure.

## Roadmap

| | MemFalsify | Organizational Memory |
|---|---|---|
| **v0.1** (this release) | Text memory audit, evidence tracing, review workflow | Event log, state machine, authority rules, provenance |
| **v0.2** (MemFalsify part done) | LLM + Jev dual verification ✓ | GitHub Issues / PR ingestion |
| v0.3 | Git history of memory: diff, review, rollback; cross-agent skill polish | State conflicts and decision audit |
| v0.4 | Multimodal evidence (OCR / transcription with locators) | Cross-platform organizational memory |
| v1.0 | Benchmark, stable API | Benchmark, stable API |

## Development

```bash
pip install pytest
pytest          # runs against packages/*/src, no install needed
```

Layout: `packages/memcore` (evidence integrity, JSONL), `packages/memfalsify`, `packages/orgmemory`, `skills/`, `examples/`, `tests/`. Contributions should include a minimal synthetic reproduction. Never commit private evidence or credentials.

## License

Apache-2.0. See [LICENSE](LICENSE).
