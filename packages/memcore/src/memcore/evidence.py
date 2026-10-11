"""Evidence integrity shared by MemFalsify and Organizational Memory.

An evidence span is an exact quote at a known character range of a file whose
bytes hash to a recorded SHA-256. That proves the quote exists in those bytes;
it does not prove the source is authentic or that the quote means what a
reviewer says it means.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .jsonl import read_jsonl


class EvidenceError(ValueError):
    """Evidence is missing, tampered with, or points outside its root."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(description="POSIX path relative to the evidence root")
    sha256: str = Field(description="SHA-256 of the file's raw bytes")
    start: int = Field(ge=0, description="Zero-based Unicode character offset, inclusive")
    end: int = Field(description="Zero-based Unicode character offset, exclusive")
    quote: str = Field(min_length=1)
    line: int = Field(ge=1)
    id: str | None = None
    source_uri: str | None = None
    source_kind: str | None = None
    captured_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _ordered(self) -> "EvidenceSpan":
        if self.end <= self.start:
            raise ValueError("evidence span end must be after start")
        return self

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def resolve_inside(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root``; refuse absolute paths and escapes (incl. symlinks)."""
    root = Path(root).resolve()
    if Path(relative).is_absolute():
        raise EvidenceError(f"evidence path must be relative: {relative}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise EvidenceError(f"evidence path escapes its root: {relative}")
    return path


def read_text(root: Path, relative: str) -> tuple[str, str]:
    """Return (sha256 of raw bytes, decoded UTF-8 text)."""
    raw = resolve_inside(root, relative).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"evidence is not UTF-8 text: {relative}") from exc
    return sha256_bytes(raw), text


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def make_span(relative: str, digest: str, text: str, start: int, end: int, **provenance) -> EvidenceSpan:
    return EvidenceSpan(path=relative, sha256=digest, start=start, end=end,
                        quote=text[start:end], line=line_of(text, start), **provenance)


def locate_quote(root: Path, relative: str, quote: str, **provenance) -> EvidenceSpan:
    """Find the first exact occurrence of ``quote``; fail closed if it is absent."""
    digest, text = read_text(root, relative)
    start = text.find(quote) if quote else -1
    if start < 0:
        raise EvidenceError(f"quote not found verbatim in {relative}: {quote!r}")
    return make_span(relative, digest, text, start, start + len(quote), **provenance)


class ManifestEntry(BaseModel):
    """One registered evidence record, as stored in an evidence JSONL manifest."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    path: str
    sha256: str
    quote: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int
    source_uri: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    captured_at: AwareDatetime


def verify_entry(entry: ManifestEntry, root: Path) -> EvidenceSpan:
    digest, text = read_text(root, entry.path)
    if digest != entry.sha256:
        raise EvidenceError(f"evidence hash mismatch: {entry.id}")
    if entry.end <= entry.start or text[entry.start:entry.end] != entry.quote:
        raise EvidenceError(f"quote does not match recorded span: {entry.id}")
    return make_span(entry.path, digest, text, entry.start, entry.end, id=entry.id,
                     source_uri=entry.source_uri, source_kind=entry.source_kind,
                     captured_at=entry.captured_at)


def load_manifest(manifest: Path) -> dict[str, EvidenceSpan]:
    """Load and verify every entry; paths are relative to the manifest's directory."""
    manifest = Path(manifest)
    spans: dict[str, EvidenceSpan] = {}
    for number, record in read_jsonl(manifest):
        entry = ManifestEntry.model_validate(record)
        if entry.id in spans:
            raise EvidenceError(f"{manifest}:{number}: duplicate evidence id {entry.id}")
        spans[entry.id] = verify_entry(entry, manifest.parent)
    return spans
