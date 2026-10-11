"""Shared evidence primitives for Verified Memory Infrastructure."""
from .evidence import (EvidenceError, EvidenceSpan, ManifestEntry, line_of, load_manifest,
                       locate_quote, make_span, read_text, resolve_inside, sha256_bytes)
from .jsonl import append_jsonl, read_jsonl

__version__ = "0.1.0"
__all__ = ["EvidenceError", "EvidenceSpan", "ManifestEntry", "append_jsonl", "line_of",
           "load_manifest", "locate_quote", "make_span", "read_jsonl", "read_text",
           "resolve_inside", "sha256_bytes"]
