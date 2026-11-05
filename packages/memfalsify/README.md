# memfalsify

Part of [Memory Lab](https://github.com/timothyzhbw-jpg/memory-lab#readme). The `memfalsify` command
audits an agent memory file (`MEMORY.md`, `CLAUDE.md`, `AGENTS.md`, ...) against a directory of evidence
(including transcripts and OCR of recordings and scans, cited by media file and position), and for memory
kept in git shows what a revision changed (`diff`), where each claim came from
(`history`) and a patch to restore a claim (`rollback`). It never modifies memory.

```bash
pip install memfalsify
memfalsify audit MEMORY.md --evidence docs/
```

The default verifier is lexical and offline. The model verifiers are extras: `memfalsify[llm]` (Claude),
`memfalsify[jev]` (Jev), `memfalsify[openai]` (any OpenAI-compatible API), `memfalsify[all]` (all three). They send
memory and evidence text to the API you choose.

See the [project README](https://github.com/timothyzhbw-jpg/memory-lab#readme) for usage and the
[changelog](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/CHANGELOG.md) for changes.

The typed public Python API is documented in the
[API reference](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/docs/api.md).
Import library symbols from the package root; `__all__` defines the supported surface.
