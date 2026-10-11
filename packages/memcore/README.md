# memlab-core

Shared evidence core of [Memory Lab](https://github.com/timothyzhbw-jpg/memory-lab#readme), imported as
`memcore`: evidence spans with SHA-256 and exact quote offsets, path and symlink containment, evidence
manifests, append-only JSONL storage that rejects a repeated object key, transcript and OCR sidecars bound to their
media files with time and page-box locators, and the confined readers used by the MCP server. MemFalsify and
orgmemory depend on it. See the root README for usage and the
[changelog](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/CHANGELOG.md) for changes.

The typed public Python API is documented in the
[API reference](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/docs/api.md).
Import library symbols from the package root; `__all__` defines the supported surface.
