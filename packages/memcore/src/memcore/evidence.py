"""Evidence integrity shared by MemFalsify and Organizational Memory.

An evidence span is an exact quote at a known character range of a file whose
bytes hash to a recorded SHA-256. That proves the quote exists in those bytes;
it does not prove the source is authentic or that the quote means what a
reviewer says it means.

A transcript or OCR sidecar (see ``memcore.sidecar``) is evidence like any other file: its spans are exact
ranges of the sidecar's bytes. They also carry a ``MediaRef``: the media file the sidecar describes, the
position in it (a time range or a page box), and the media file's SHA-256 when it was present and matched
the hash the sidecar records.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_serializer, model_validator

from .jsonl import read_jsonl
from .paths import InputPathError, input_path
from .reading import entry_names, file_sha256, list_files, read_file_bytes
from .sidecar import (LABELS, Parsed, SidecarError, Unit, is_media, media_for, one_line, parse_sidecar,
                      sidecar_base, sidecar_format, time_range)


class EvidenceError(ValueError):
    """Evidence is missing, tampered with, or points outside its root."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class MediaRef(BaseModel):
    """Where in a media file a transcript cue or OCR line comes from."""
    model_config = ConfigDict(frozen=True)

    kind: Literal["transcript", "ocr"]
    path: str | None = Field(description="The media file the sidecar describes, as a POSIX path relative to the "
                                          "evidence root; None when no media file is named or found")
    locator: str = Field(description="Position in the media file: t=START,END in seconds (transcript cue) or "
                                     "page=N&xywh=X,Y,W,H in pixels (OCR line)")
    sha256: str | None = Field(description="SHA-256 of the media file, which matched the sidecar's source_sha256; "
                                           "None when the media file was not present")

    @property
    def time_range(self) -> str | None:
        """``00:00:12.500-00:00:17.000`` for a transcript cue, else None."""
        return time_range(self.locator)

    @property
    def label(self) -> str:
        """What kind of sidecar the quote is from, for reports: ``transcript`` or ``OCR``."""
        return "OCR" if self.kind == "ocr" else self.kind


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
    media: MediaRef | None = Field(default=None, description="Set when the file is a transcript or OCR sidecar")

    @model_validator(mode="after")
    def _ordered(self) -> "EvidenceSpan":
        if self.end <= self.start:
            raise ValueError("evidence span end must be after start")
        return self

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"

    @property
    def citation(self) -> str:
        """Where a reader looks: ``clip.mp4#t=12.5,17`` for a sidecar passage (the sidecar's own path when it
        names no media file), else ``path:line``."""
        if self.media is None:
            return self.location
        return f"{self.media.path or self.path}#{self.media.locator}"


def resolve_inside(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root``; refuse absolute paths, null bytes and escapes (incl. symlinks)."""
    if "\x00" in relative or "\x00" in str(root):
        raise EvidenceError("evidence path contains a null character")
    root = Path(root).resolve()
    if Path(relative).is_absolute():
        raise EvidenceError(f"evidence path must be relative: {relative}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise EvidenceError(f"evidence path escapes its root: {relative}")
    return path


def read_text(root: Path, relative: str) -> tuple[str, str]:
    """Return (sha256 of raw bytes, decoded UTF-8 text)."""
    path = resolve_inside(root, relative)
    if path.exists() and not path.is_file():
        raise EvidenceError(f"evidence is not a regular file: {relative}")
    raw = read_file_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"evidence is not UTF-8 text: {relative}") from exc
    return sha256_bytes(raw), text


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def make_span(relative: str, digest: str, text: str, start: int, end: int, **provenance: Any) -> EvidenceSpan:
    return EvidenceSpan(path=relative, sha256=digest, start=start, end=end,
                        quote=text[start:end], line=line_of(text, start), **provenance)


# --- sidecars ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Sidecar:
    """A parsed sidecar, checked against the media file it describes."""
    parsed: Parsed
    media: str | None  # POSIX path of the media file relative to the root; None when none is named or found
    media_sha256: str | None  # set when the media file is present (it then matched source_sha256)

    @property
    def units(self) -> tuple[Unit, ...]:
        return self.parsed.units

    def ref(self, unit: Unit) -> MediaRef:
        return MediaRef(kind=self.parsed.kind, path=self.media, locator=unit.locator, sha256=self.media_sha256)


def _media_sha256(root: Path, media: str, name: str, hashes: dict[Path, str] | None) -> str | None:
    """The media file's SHA-256, or None when it is absent. A media file that is present but cannot be checked
    (escaping symlink, broken link, not a regular file) makes the sidecar unusable; a read error propagates.

    The name must match a directory entry exactly. ``exists`` and ``resolve`` follow case-insensitive
    filesystems, so ``Clip.mp4`` would hash ``clip.mp4`` on macOS and Windows and be missing on Linux."""
    shown = one_line(name)
    folder = PurePath(media).parent.as_posix()
    try:
        names = entry_names(resolve_inside(root, "." if folder == "." else folder))
    except EvidenceError:
        raise SidecarError(f"cannot check the binding: the media file {shown} is a symlink that escapes the "
                           "evidence root") from None
    except (OSError, RuntimeError):
        raise SidecarError(f"cannot check the binding: the media file {shown} is a broken or cyclic symlink") from None
    if name not in names:
        return None
    try:
        path = resolve_inside(root, media)
        # The media file obeys the shared input policy: never hash a file behind a hidden or skipped component.
        input_path(root, Path(root).resolve() / media)
    except EvidenceError:
        raise SidecarError(f"cannot check the binding: the media file {shown} is a symlink that escapes the "
                           "evidence root") from None
    except InputPathError as exc:
        if str(exc) == "path escapes root":
            raise SidecarError(f"cannot check the binding: the media file {shown} is a symlink that escapes the "
                               "evidence root") from None
        raise SidecarError(f"cannot check the binding: the media file {shown} is excluded ({exc})") from None
    except (OSError, RuntimeError):
        raise SidecarError(f"cannot check the binding: the media file {shown} is a broken or cyclic symlink") from None
    if not path.is_file():
        raise SidecarError(f"cannot check the binding: the media file {shown} is a broken symlink or not a "
                           "regular file")
    if hashes is not None and path in hashes:
        return hashes[path]
    digest = file_sha256(path)
    if hashes is not None:
        hashes[path] = digest
    return digest


def load_sidecar(root: Path, relative: str, text: str, siblings: Iterable[str] | None = None,
                 hashes: dict[Path, str] | None = None) -> Sidecar:
    """Parse the sidecar at ``relative`` (decoded ``text``) and bind it to its media file.

    When the media file is present, its SHA-256 must equal the ``source_sha256`` the sidecar records; otherwise
    the sidecar is stale or unbound and SidecarError says which. ``siblings`` are the names in the sidecar's
    directory (listed here when omitted and needed); ``hashes`` caches media hashes across calls.
    """
    name, folder = PurePath(relative).name, PurePath(relative).parent
    if sidecar_format(name) is None:
        raise SidecarError("not a transcript or OCR sidecar")
    parsed = parse_sidecar(sidecar_format(name), text)
    if siblings is None:
        siblings = [] if is_media(sidecar_base(name)) else list_files(resolve_inside(root, folder.as_posix()))
    media_name = media_for(name, siblings)
    if media_name is None:
        return Sidecar(parsed, None, None)
    media = (folder / media_name).as_posix()
    digest = _media_sha256(root, media, media_name, hashes)
    shown = one_line(media_name)
    if digest is not None:
        if parsed.source_sha256 is None:
            raise SidecarError(f"not bound to {shown}: the sidecar records no source_sha256, so it cannot be "
                               "checked against the media file next to it")
        if parsed.source_sha256 != digest:
            raise SidecarError(f"stale: its source_sha256 does not match {shown}; the media file changed after "
                               "the sidecar was made")
    return Sidecar(parsed, media, digest)


def _sidecar(root: Path, relative: str, text: str) -> Sidecar:
    try:
        return load_sidecar(root, relative, text)
    except SidecarError as exc:
        label = LABELS[sidecar_format(PurePath(relative).name)]
        raise EvidenceError(one_line(f"unusable {label} {relative}: {exc}")) from None


def locate_quote(root: Path, relative: str, quote: str, locator: str | None = None,
                 **provenance: Any) -> EvidenceSpan:
    """Find the first exact occurrence of ``quote``; fail closed if it is absent.

    In a transcript or OCR sidecar the quote must lie inside one cue or line (the one at ``locator``, if
    given), and the span carries that position as its ``media``."""
    digest, text = read_text(root, relative)
    if sidecar_format(PurePath(relative).name) is None:
        if locator is not None:
            raise EvidenceError(one_line(f"a locator applies only to transcript and OCR sidecars: {relative}"))
        start = text.find(quote) if quote else -1
        if start < 0:
            raise EvidenceError(one_line(f"quote not found verbatim in {relative}: {quote!r}"))
        return make_span(relative, digest, text, start, start + len(quote), **provenance)
    sidecar = _sidecar(root, relative, text)
    for unit in sidecar.units:
        if locator is not None and unit.locator != locator:
            continue
        start = text.find(quote, unit.start, unit.end) if quote else -1
        if start >= 0:
            return make_span(relative, digest, text, start, start + len(quote), media=sidecar.ref(unit),
                             **provenance)
    where = f"the cue or line at {locator}" if locator is not None else "any cue or line"
    raise EvidenceError(one_line(f"quote not found verbatim in {where} of {relative}: {quote!r}"))


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
    locator: str | None = Field(default=None, min_length=1,
                                description="Position in the media file, for a quote from a transcript or OCR "
                                            "sidecar; omitted otherwise")

    @model_serializer(mode="wrap")
    def _omit_missing_locator(self, handler):
        # A record without a locator keeps exactly the shape (and bytes) records had before locators existed.
        data = handler(self)
        if isinstance(data, dict) and data.get("locator") is None:
            data.pop("locator", None)
        return data


def verify_entry(entry: ManifestEntry, root: Path) -> EvidenceSpan:
    """Check one manifest entry against the file under ``root`` and return its verified span."""
    return _verify_entry(entry, root, None)


def _verify_entry(entry: ManifestEntry, root: Path, cache: dict[str, tuple[str, str]] | None) -> EvidenceSpan:
    """``verify_entry``; ``cache`` stores (sha256, text) for paths already read in this load."""
    if cache is None:
        digest, text = read_text(root, entry.path)
    else:
        cached = cache.get(entry.path)
        if cached is None:
            cached = read_text(root, entry.path)
            cache[entry.path] = cached
        digest, text = cached
    if digest != entry.sha256:
        raise EvidenceError(f"evidence hash mismatch: {entry.id}")
    # Compare bounds before slicing. A hostile end must not be turned into a giant slice.
    length = len(text)
    if (entry.start >= length or entry.end > length or entry.end <= entry.start
            or text[entry.start:entry.end] != entry.quote):
        raise EvidenceError(f"quote does not match recorded span: {entry.id}")
    media = None
    if sidecar_format(PurePath(entry.path).name) is not None:
        # Omitting locator used to skip the cue check and the media binding, so a replaced recording still
        # validated. Sidecar records are new; a missing locator is not a legacy shape.
        if entry.locator is None:
            raise EvidenceError(f"evidence {entry.id}: {one_line(entry.path)} is a transcript or OCR sidecar but "
                                "records no locator")
        sidecar = _sidecar(root, entry.path, text)
        unit = next((u for u in sidecar.units if u.start <= entry.start and entry.end <= u.end), None)
        if unit is None:
            raise EvidenceError(f"evidence {entry.id}: the quote is not inside one cue or line of "
                                f"{one_line(entry.path)}")
        if unit.locator != entry.locator:
            raise EvidenceError(f"evidence {entry.id}: recorded locator {one_line(entry.locator)} does not match "
                                f"{unit.locator}")
        media = sidecar.ref(unit)
    elif entry.locator is not None:
        raise EvidenceError(f"evidence {entry.id} has a locator, but {one_line(entry.path)} is not a transcript "
                            "or OCR sidecar")
    return make_span(entry.path, digest, text, entry.start, entry.end, id=entry.id,
                     source_uri=entry.source_uri, source_kind=entry.source_kind,
                     captured_at=entry.captured_at, media=media)


def load_manifest(manifest: Path) -> dict[str, EvidenceSpan]:
    """Load and verify every entry; paths are relative to the manifest's directory.

    A path is read and hashed once. Later entries for that same path are checked
    against those bytes, still in manifest order.
    """
    manifest = Path(manifest)
    spans: dict[str, EvidenceSpan] = {}
    cache: dict[str, tuple[str, str]] = {}
    for number, record in read_jsonl(manifest):
        entry = ManifestEntry.model_validate(record)
        if entry.id in spans:
            raise EvidenceError(f"{manifest}:{number}: duplicate evidence id {entry.id}")
        spans[entry.id] = _verify_entry(entry, manifest.parent, cache)
    return spans
