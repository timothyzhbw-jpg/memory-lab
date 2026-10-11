"""Shared evidence primitives for Verified Memory Infrastructure."""

__version__ = "0.2.0"

from .evidence import (
    EvidenceError,
    EvidenceSpan,
    ManifestEntry,
    MediaRef,
    Sidecar,
    line_of,
    load_manifest,
    load_sidecar,
    locate_quote,
    make_span,
    read_text,
    resolve_inside,
    sha256_bytes,
    verify_entry,
)
from .jsonl import (
    append_jsonl,
    load_json,
    read_jsonl,
)
from .reading import (
    confinement_root,
    confinement_supported,
    confined_reads,
    open_text,
    read_file_bytes,
    read_file_text,
    walk_files,
)
from .sidecar import SidecarError, sidecar_format

__all__ = [
    "EvidenceError",
    "EvidenceSpan",
    "ManifestEntry",
    "MediaRef",
    "Sidecar",
    "SidecarError",
    "__version__",
    "append_jsonl",
    "confined_reads",
    "confinement_root",
    "confinement_supported",
    "line_of",
    "load_json",
    "load_manifest",
    "load_sidecar",
    "locate_quote",
    "make_span",
    "open_text",
    "read_file_bytes",
    "read_file_text",
    "read_jsonl",
    "read_text",
    "resolve_inside",
    "sha256_bytes",
    "sidecar_format",
    "verify_entry",
    "walk_files",
]
