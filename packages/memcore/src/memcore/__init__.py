"""Shared evidence primitives for Verified Memory Infrastructure."""
from .evidence import (EvidenceError, EvidenceSpan, ManifestEntry, MediaRef, Sidecar, line_of, load_manifest,
                       load_sidecar, locate_quote, make_span, read_text, resolve_inside, sha256_bytes)
from .jsonl import append_jsonl, read_jsonl
from .sidecar import SidecarError, sidecar_format

__version__ = "0.2.0"
__all__ = ["EvidenceError", "EvidenceSpan", "ManifestEntry", "MediaRef", "Sidecar", "SidecarError",
           "append_jsonl", "line_of", "load_manifest", "load_sidecar", "locate_quote", "make_span", "read_jsonl",
           "read_text", "resolve_inside", "sha256_bytes", "sidecar_format"]
