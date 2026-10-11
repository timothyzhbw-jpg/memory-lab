"""Transcript and OCR sidecars: text someone extracted from a media file, with where each passage came from.

A sidecar sits next to the media file it describes: ``clip.mp4.vtt``, ``clip.vtt`` or ``clip.en.vtt`` for
``clip.mp4``, and ``scan.pdf.ocr.json`` for ``scan.pdf``. Every cue or OCR line becomes a ``Unit``: a
[start, end) range of the sidecar's own decoded text plus a locator inside the media file (a Media Fragments
time range ``t=12.5,17`` or ``page=2&xywh=40,120,570,38``). A quote is therefore still an exact substring of
the sidecar's bytes, and the usual evidence-span checks (hash, offsets) apply to it unchanged.

Everything here is pure: no file access. Malformed input raises ``SidecarError`` and nothing else, so a
caller can skip the sidecar with the error message as the reason.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from json.decoder import scanstring

MEDIA_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".tiff", ".mp3", ".wav", ".m4a", ".flac",
    ".ogg", ".mp4", ".mov", ".webm", ".mkv", ".pdf"})
# Longest first, so that ".ocr.json" wins over a plain ".json" (which is not a sidecar).
SIDECAR_SUFFIXES = ((".ocr.json", "ocr"), (".vtt", "vtt"), (".srt", "srt"))
KINDS = {"vtt": "transcript", "srt": "transcript", "ocr": "ocr"}
LABELS = {"vtt": "WebVTT transcript", "srt": "SubRip transcript", "ocr": "OCR sidecar"}
OCR_FORMAT = "memlab-ocr/1"
MAX_DEPTH = 16  # the OCR schema nests four levels; anything deeper is not an OCR sidecar

_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_BINDING = re.compile(r"source_sha256\s*:\s*(\S*)\s*")
_EOL = re.compile(r"\r\n|\r|\n")
# Hours are bounded so that hostile input can never build a huge integer.
_VTT_TIME = r"(?:(\d{2,9}):)?([0-5]\d):([0-5]\d)\.(\d{3})"
_VTT_TIMING = re.compile(rf"{_VTT_TIME}[ \t]+-->[ \t]+{_VTT_TIME}(?:[ \t]+.*)?")
_SRT_TIME = r"(\d{1,9}):([0-5]\d):([0-5]\d)[,.](\d{3})"
_SRT_TIMING = re.compile(rf"[ \t]*{_SRT_TIME}[ \t]*-->[ \t]*{_SRT_TIME}(?:[ \t]+.*)?")
_LANGUAGE = re.compile(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*")
_WS = re.compile(r"[ \t\n\r]*")
_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?")


class SidecarError(ValueError):
    """A sidecar that cannot be used. The message is the reason a report shows."""


@dataclass(frozen=True)
class Unit:
    """One cue or OCR line: ``text[start:end]`` of the sidecar, and its position in the media file."""
    start: int
    end: int
    locator: str


@dataclass(frozen=True)
class Parsed:
    format: str  # "vtt", "srt" or "ocr"
    source_sha256: str | None  # SHA-256 of the media file the sidecar was made from, as it records it
    units: tuple[Unit, ...]
    note: str | None = None  # what was left out of an otherwise usable sidecar

    @property
    def kind(self) -> str:
        return KINDS[self.format]


# --- names -------------------------------------------------------------------------------------------

def sidecar_format(name: str) -> str | None:
    """``vtt``, ``srt`` or ``ocr`` for a sidecar file name (case-insensitive), else None."""
    lower = name.lower()
    for suffix, fmt in SIDECAR_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return fmt
    return None


def is_media(name: str) -> bool:
    dot = name.rfind(".")
    return dot > 0 and name[dot:].lower() in MEDIA_SUFFIXES


def media_for(name: str, siblings: Iterable[str]) -> str | None:
    """The media file a sidecar describes: the name it embeds (``clip.mp4.vtt``), else the one sibling whose
    stem it shares, optionally with a language tag (``clip.vtt``, ``clip.en.vtt``). None when no media file
    is named or found; SidecarError when several media files fit."""
    if sidecar_format(name) is None:
        raise SidecarError("not a transcript or OCR sidecar")
    base = sidecar_base(name)
    if is_media(base):
        return base
    found: dict[str, int] = {}
    for other in siblings:
        if other == name or not is_media(other):
            continue
        stem = other[:other.rfind(".")]
        if base == stem or (base.startswith(stem + ".") and _LANGUAGE.fullmatch(base[len(stem) + 1:])):
            found[other] = len(stem)
    if not found:
        return None
    longest = max(found.values())
    best = sorted(other for other, size in found.items() if size == longest)
    if len(best) > 1:
        raise SidecarError(f"describes more than one media file ({', '.join(best)}); name it after one of "
                           f"them, such as {best[0]}{_suffix(name)}")
    return best[0]


def _suffix(name: str) -> str:
    lower = name.lower()
    return next(name[len(name) - len(s):] for s, _ in SIDECAR_SUFFIXES if lower.endswith(s))


def sidecar_base(name: str) -> str:
    """A sidecar's name without its sidecar suffix: ``clip.mp4`` for ``clip.mp4.vtt``."""
    return name[:len(name) - len(_suffix(name))]


# --- locators ----------------------------------------------------------------------------------------

def _seconds(ms: int) -> str:
    whole, frac = divmod(ms, 1000)
    return f"{whole}.{frac:03d}".rstrip("0").rstrip(".")


def _clock(ms: int) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    return f"{hours:02d}:{minutes:02d}:{rest // 1000:02d}.{rest % 1000:03d}"


_TIME_LOCATOR = re.compile(r"t=(\d+)(?:\.(\d{1,3}))?,(\d+)(?:\.(\d{1,3}))?")


def time_range(locator: str) -> str | None:
    """``t=12.5,17`` as ``00:00:12.500-00:00:17.000``; None for a locator that is not a time range."""
    match = _TIME_LOCATOR.fullmatch(locator)
    if match is None:
        return None
    a, a_frac, b, b_frac = match.groups()
    start = int(a) * 1000 + int((a_frac or "0").ljust(3, "0"))
    end = int(b) * 1000 + int((b_frac or "0").ljust(3, "0"))
    return f"{_clock(start)}-{_clock(end)}"


# --- transcripts -------------------------------------------------------------------------------------

def _lines(text: str) -> list[tuple[int, int]]:
    """(start, end) of each line's content, without its terminator (CRLF, LF or CR)."""
    lines, pos = [], 0
    while pos < len(text):
        match = _EOL.search(text, pos)
        if match is None:
            lines.append((pos, len(text)))
            break
        lines.append((pos, match.start()))
        pos = match.end()
    return lines


def _blocks(text: str) -> list[list[tuple[int, int, int]]]:
    """Runs of non-blank lines, as (line number, start, end); a whitespace-only line separates blocks."""
    blocks, current = [], []
    for number, (start, end) in enumerate(_lines(text), start=1):
        if text[start:end].strip(" \t﻿"):
            current.append((number, start, end))
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _ms(hours: str | None, minutes: str, seconds: str, millis: str) -> int:
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def _note_binding(text: str, block: list[tuple[int, int, int]]) -> str | None:
    """The ``source_sha256`` recorded in a NOTE block, if any."""
    found = None
    for index, (number, start, end) in enumerate(block):
        line = text[start:end].strip(" \t﻿")
        if index == 0:
            line = line[4:].strip(" \t")  # after "NOTE"
        match = _BINDING.fullmatch(line)
        if match is None:
            if line.startswith("source_sha256"):
                raise SidecarError(f"line {number}: source_sha256 must be followed by ':' and a SHA-256")
            continue
        if not _HEX64.fullmatch(match.group(1)):
            raise SidecarError(f"line {number}: source_sha256 is not a 64-digit hex SHA-256")
        if found is not None:
            raise SidecarError(f"line {number}: source_sha256 is recorded more than once")
        found = match.group(1).lower()
    return found


def _is_note(line: str) -> bool:
    return line == "NOTE" or line.startswith(("NOTE ", "NOTE\t"))


def parse_transcript(fmt: str, text: str) -> Parsed:
    """Cues of a WebVTT (``vtt``) or SubRip (``srt``) file. A NOTE block may record ``source_sha256: <hex>``
    (in SubRip, a MemFalsify extension allowed only before the first cue). Cue text is quoted as written,
    including any markup; a malformed timing, a missing timing line or ``-->`` inside cue text rejects the
    whole file."""
    blocks = _blocks(text)
    if fmt == "vtt":
        if not blocks or blocks[0][0][0] != 1:
            raise SidecarError("line 1 must be WEBVTT")
        number, start, end = blocks[0][0]
        first = text[start:end].removeprefix("﻿")
        if not (first == "WEBVTT" or first.startswith(("WEBVTT ", "WEBVTT\t"))):
            raise SidecarError("line 1 must be WEBVTT")
        for number, start, end in blocks[0][1:]:
            if "-->" in text[start:end]:
                raise SidecarError(f"line {number}: expected a blank line between the WEBVTT header and the first cue")
        blocks = blocks[1:]
    timing = _VTT_TIMING if fmt == "vtt" else _SRT_TIMING
    binding, units, cues = None, [], 0
    for block in blocks:
        number, start, end = block[0]
        head = text[start:end].strip(" \t﻿")
        if _is_note(head) and (fmt == "vtt" or cues == 0):
            found = _note_binding(text, block)
            if found is not None:
                if binding is not None:
                    raise SidecarError(f"line {number}: source_sha256 is recorded more than once")
                binding = found
            continue
        if (fmt == "vtt" and cues == 0 and head in ("STYLE", "REGION")
                and not any("-->" in text[s:e] for _, s, e in block)):
            continue
        if "-->" in head:
            cue = 0
        elif len(block) > 1 and "-->" in text[block[1][1]:block[1][2]]:
            if fmt == "srt" and not (head.isascii() and head.isdigit()):
                raise SidecarError(f"line {number}: expected a cue number, found text")
            cue = 1
        else:
            raise SidecarError(f"line {number}: expected a cue timing line (start --> end)")
        number, start, end = block[cue]
        match = timing.fullmatch(text[start:end].lstrip("﻿"))
        if match is None:
            raise SidecarError(f"line {number}: invalid cue timing")
        groups = match.groups()
        begin, finish = _ms(*groups[0:4]), _ms(*groups[4:8])
        if finish < begin:
            raise SidecarError(f"line {number}: cue ends before it starts")
        payload = block[cue + 1:]
        for number, line_start, line_end in payload:
            if "-->" in text[line_start:line_end]:
                raise SidecarError(f"line {number}: cue text contains '-->' (is a blank line missing?)")
        cues += 1
        if payload:
            first, last = payload[0][1], payload[-1][2]
            body = text[first:last]
            lead = len(body) - len(body.lstrip())
            trail = len(body.rstrip())
            if trail > lead:
                units.append(Unit(first + lead, first + trail, f"t={_seconds(begin)},{_seconds(finish)}"))
    return Parsed(format=fmt, source_sha256=binding, units=tuple(units))


# --- OCR JSON ----------------------------------------------------------------------------------------

class _Text(str):
    """A JSON string value that remembers where its literal is in the sidecar text."""
    start: int
    end: int
    exact: bool  # the literal has no escape sequences, so text[start:end] equals the value


def _where(text: str, pos: int) -> str:
    line = text.count("\n", 0, pos) + 1
    return f"line {line}, column {pos - (text.rfind(chr(10), 0, pos) + 1) + 1}"


def _ws(text: str, pos: int) -> int:
    return _WS.match(text, pos).end()


def _value(text: str, pos: int, depth: int):
    if depth > MAX_DEPTH:
        raise SidecarError(f"nested too deeply at {_where(text, pos)}")
    char = text[pos:pos + 1]
    if char == "{":
        result: dict = {}
        pos = _ws(text, pos + 1)
        if text[pos:pos + 1] == "}":
            return result, pos + 1
        while True:
            if text[pos:pos + 1] != '"':
                raise SidecarError(f"invalid JSON: expected a key at {_where(text, pos)}")
            key, pos = _value(text, pos, depth + 1)
            if key in result:
                raise SidecarError(f"duplicate key at {_where(text, pos)}")  # never echo file content
            pos = _ws(text, pos)
            if text[pos:pos + 1] != ":":
                raise SidecarError(f"invalid JSON: expected ':' at {_where(text, pos)}")
            result[str(key)], pos = _value(text, _ws(text, pos + 1), depth + 1)
            pos = _ws(text, pos)
            if text[pos:pos + 1] == "}":
                return result, pos + 1
            if text[pos:pos + 1] != ",":
                raise SidecarError(f"invalid JSON: expected ',' or '}}' at {_where(text, pos)}")
            pos = _ws(text, pos + 1)
    if char == "[":
        items: list = []
        pos = _ws(text, pos + 1)
        if text[pos:pos + 1] == "]":
            return items, pos + 1
        while True:
            item, pos = _value(text, pos, depth + 1)
            items.append(item)
            pos = _ws(text, pos)
            if text[pos:pos + 1] == "]":
                return items, pos + 1
            if text[pos:pos + 1] != ",":
                raise SidecarError(f"invalid JSON: expected ',' or ']' at {_where(text, pos)}")
            pos = _ws(text, pos + 1)
    if char == '"':
        try:
            decoded, end = scanstring(text, pos + 1, True)
        except ValueError:  # json.JSONDecodeError: unterminated string, bad escape, control character
            raise SidecarError(f"invalid JSON string at {_where(text, pos)}") from None
        value = _Text(decoded)
        value.start, value.end = pos + 1, end - 1
        value.exact = text[pos + 1:end - 1] == decoded
        return value, end
    for literal, result in (("true", True), ("false", False), ("null", None)):
        if text.startswith(literal, pos):
            return result, pos + len(literal)
    match = _NUMBER.match(text, pos)
    if match and match.end() > pos:
        number = match.group()
        if len(number) > 40:
            raise SidecarError(f"number too long at {_where(text, pos)}")
        return (float(number) if match.group(1) or match.group(2) else int(number)), match.end()
    raise SidecarError(f"invalid JSON at {_where(text, pos)}")


def _int(value, what: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise SidecarError(f"{what} must be an integer of at least {minimum}")
    return value


def parse_ocr(text: str) -> Parsed:
    """An OCR sidecar (``memlab-ocr/1``): ``{"format", "source_sha256", "pages": [{"page", "lines": [{"text",
    "bbox": [x0, y0, x1, y1]}]}]}``, coordinates in pixels from the top left. A line whose text literal uses a
    JSON escape is left out (its quote could not be exact), and ``note`` says how many were."""
    start = 1 if text.startswith("﻿") else 0
    document, end = _value(text, _ws(text, start), 0)
    if _ws(text, end) != len(text):
        raise SidecarError(f"invalid JSON: extra data at {_where(text, _ws(text, end))}")
    if not isinstance(document, dict):
        raise SidecarError("expected a JSON object")
    if document.get("format") != OCR_FORMAT:
        raise SidecarError(f'unsupported format: "format" must be "{OCR_FORMAT}"')
    binding = document.get("source_sha256")
    if binding is not None and not (isinstance(binding, str) and _HEX64.fullmatch(binding)):
        raise SidecarError("source_sha256 is not a 64-digit hex SHA-256")
    pages = document.get("pages")
    if not isinstance(pages, list):
        raise SidecarError('"pages" must be a list')
    units, seen, escaped = [], set(), []
    for page in pages:
        if not isinstance(page, dict):
            raise SidecarError("each page must be an object")
        number = _int(page.get("page"), "page", 1)
        if number in seen:
            raise SidecarError(f"page {number} appears twice")
        seen.add(number)
        width = _int(page["width"], f"page {number} width", 1) if "width" in page else None
        height = _int(page["height"], f"page {number} height", 1) if "height" in page else None
        lines = page.get("lines")
        if not isinstance(lines, list):
            raise SidecarError(f'page {number} needs a "lines" list')
        for index, line in enumerate(lines, start=1):
            where = f"page {number} line {index}"
            if not isinstance(line, dict) or not isinstance(line.get("text"), _Text):
                raise SidecarError(f'{where} needs a "text" string')
            box = line.get("bbox")
            if not isinstance(box, list) or len(box) != 4:
                raise SidecarError(f'{where} needs "bbox": [x0, y0, x1, y1]')
            x0, y0, x1, y1 = (_int(v, f"{where} bbox", 0) for v in box)
            if x1 <= x0 or y1 <= y0 or (width and x1 > width) or (height and y1 > height):
                raise SidecarError(f"{where} bbox is empty or outside the page")
            value: _Text = line["text"]
            if not value.exact:
                escaped.append(where)
                continue
            lead = len(value) - len(value.lstrip())
            trail = len(value.rstrip())
            if trail > lead:
                units.append(Unit(value.start + lead, value.start + trail,
                                  f"page={number}&xywh={x0},{y0},{x1 - x0},{y1 - y0}"))
    note = None
    if escaped:
        note = (f"partly indexed: {len(escaped)} OCR line(s) use JSON escape sequences and were left out, "
                f"first {escaped[0]} (write the text as UTF-8 without escapes)")
    return Parsed(format="ocr", source_sha256=binding.lower() if binding else None, units=tuple(units),
                  note=note)


def parse_sidecar(fmt: str, text: str) -> Parsed:
    """Parse a sidecar's decoded text. Raises SidecarError (only) when it cannot be used."""
    if fmt == "ocr":
        return parse_ocr(text)
    if fmt in ("vtt", "srt"):
        return parse_transcript(fmt, text)
    raise SidecarError(f"unknown sidecar format {fmt!r}")
