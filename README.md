# Memory Lab

Two offline prototypes in one runnable Python monorepo: **MemFalsify** audits agent memory against supplied evidence; **Verified Organizational Memory** replays decisions under explicit authority rules. Python 3.10+, standard library only at runtime, no API keys.

## Run

```sh
python -m memory_lab demo examples/demo.json
python -m unittest discover -s tests -v
python -m memory_lab audit examples/demo.json
python -m memory_lab replay examples/demo.json --as-of 2026-10-07T23:59:59Z
```

Run these commands from the repository directory. Optionally install with `pip install -e .` to use the `memory-lab` command (installation may fetch the build tool). All commands read inputs and print JSON to stdout; none alter memories, evidence, events or policy. Redirect output to a separate report if desired.

## Monday–Thursday demo

All demo data is synthetic, clearly labeled by provenance. Monday: customer requests A. Tuesday: engineer identifies B. Wednesday: PM rejects A. Thursday: customer requests reconsideration. Result: A remains **rejected**, dependency B is retained, reconsideration is true, PM review is pending, and Wednesday remains the last authorized decision.

MemFalsify challenges “A is approved,” supports “A depends on B,” and reports insufficient evidence for “A will ship next week.” It proposes review, never fabricates a replacement. Supported means consistent with supplied assertions, not independently established truth.

## Data contract

`examples/demo.json` is a complete editable schema example:

- `evidence`: unique ID, relative UTF-8 file path, SHA-256 of raw bytes, exact quote and zero-based Unicode character `[start,end)` span, source URI/kind and capture timestamp. Paths and symlinks cannot escape the dataset directory. Hash and quote mismatches fail closed.
- `memories`: unique ID and text. `assertions`: memory ID, `supports` or `contradicts`, nonempty evidence IDs. Assertions are reviewed interpretations, not automatically inferred facts.
- `events`: unique ID, timezone-aware effective timestamp, actor, action, feature and nonempty evidence IDs. Dependency events include `dependency`; revocation includes `supersedes` matching the current decision event.
- `policy`: trusted actor-to-role mapping and action-to-allowed-roles mapping. Unknown actors/actions receive no implied authority; unknown actions are rejected as invalid input.

Replay sorts by effective time; ties use input ingestion order. Future events are excluded by `--as-of`. It validates the entire supplied log, even events after the cutoff. Approval/rejection by a permitted actor supersedes the prior decision and clears review requests. A permitted revoke must target the current decision. Unauthorized events remain visible in the audit trail and do not update state. Events are conceptually append-only; this prototype reads a JSON log and does not provide a persistent database or authenticated ingestion. Preserve historical policy versions externally; changing policy changes replay.

## Optional adapters

`memory_lab/adapters.py` defines an extraction boundary and caller-owned callback wrapper for LLM/Jev integrations. “Jev” is treated as an unspecified provider: no invented SDK/API contract. The shipped CLI is fully offline and uses manually supplied assertions/events. The callback validates references, labels candidates for human review, and cannot grant authority or write state. Integration code must separately validate candidate semantics and schema before ingestion.

For images/audio/video, `MultimodalSource` describes a transcription/OCR boundary. Keep original bytes/hash, derived transcript/hash, page/time/region locator and extractor version; verify derived spans manually before registering evidence. The current CLI accepts UTF-8 text evidence only. Multimodal extraction is an extension interface, not a shipped OCR or audio engine.

## Trust boundary and evaluation

Evidence hashes establish integrity relative to the supplied manifest, not authenticity: someone who can replace both can forge them. Quotes prove a span exists, not that it entails an assertion. Actor IDs are trusted dataset inputs; there is no identity authentication. Treat the policy and manifest as reviewer-controlled configuration. Same-time conflicting decisions use ingestion order, a documented deterministic choice, not automatic conflict resolution.

Useful as a reproducible research baseline and small internal demo. Not production-ready organizational infrastructure. Next steps: authenticated event ingestion, signed provenance, versioned policy and bitemporal storage, semantic evaluation against labeled evidence, and durable proposal review. MemFalsify presently flags review instead of autonomously suggesting factual rewrites. No market novelty or performance claims are made.

## Repository

`memory_lab/` contains evidence, audit, state, adapters and CLI modules; `skills/memfalsify/SKILL.md` is the reusable audit workflow; `tests/` tests observable trust and state invariants; `examples/` contains synthetic provenance and the Monday–Thursday scenario.

MIT licensed; see LICENSE. Contributions: include a minimal synthetic reproduction and run the test command above. Never commit private evidence or credentials.
