"""Transcript and OCR sidecars as evidence: parsing, binding to the media file, reports, reviews, manifests.

Fixtures under tests/fixtures/multimodal and examples/memfalsify-media are byte-exact (.gitattributes -text):
the hashes recorded in the sidecars and the offsets asserted here depend on every byte, CRLF included.
"""
import hashlib
import json
import os
import random
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import EXAMPLES, snapshot
from memcore import EvidenceError, ManifestEntry, load_manifest, load_sidecar, locate_quote
from memcore.reading import file_sha256
from memcore.sidecar import SidecarError, media_for, parse_sidecar, sidecar_format, time_range
from memfalsify.audit import MEDIA_NOTE, audit
from memfalsify.cli import app as memfalsify_app
from memfalsify.corpus import NO_SIDECAR, scan
from memfalsify.history import diff_to_markdown, memory_diff
from memfalsify.report import to_markdown
from orgmemory.cli import app as org_app
from orgmemory.replay import Dataset, replay
from orgmemory.report import explain

FIXTURES = Path(__file__).parent / "fixtures" / "multimodal"
runner = CliRunner()
symlinks = pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")
LOCATOR = re.compile(r"t=\d+(\.\d{1,3})?,\d+(\.\d{1,3})?|page=[1-9]\d*&xywh=\d+,\d+,[1-9]\d*,[1-9]\d*")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture(name: str) -> str:
    return (FIXTURES / name).read_bytes().decode("utf-8")


def units(fmt: str, text: str) -> list[tuple[str, str]]:
    return [(text[u.start:u.end], u.locator) for u in parse_sidecar(fmt, text).units]


@pytest.fixture
def media_dir(tmp_path):
    """A copy of the parser fixtures to scan: three media files, each with a bound sidecar."""
    return Path(shutil.copytree(FIXTURES, tmp_path / "evidence"))


# --- parsing the byte-exact fixtures ----------------------------------------------------------------

def test_fixtures_kept_their_bytes():
    assert (FIXTURES / "clip.mp4.vtt").read_bytes().startswith(b"\xef\xbb\xbfWEBVTT")
    assert b"\r\n" in (FIXTURES / "retro.srt").read_bytes()
    assert b"\r" not in (FIXTURES / "clip.mp4.vtt").read_bytes()


def test_webvtt_cues_are_exact_ranges_with_time_locators():
    text = fixture("clip.mp4.vtt")
    parsed = parse_sidecar("vtt", text)
    assert parsed.source_sha256 == sha((FIXTURES / "clip.mp4").read_bytes())
    assert units("vtt", text) == [
        ("<v Priya>The public API rate limit is 60 requests\nper minute per key.", "t=12.5,17"),
        ("Bulk export waits for the billing migration.", "t=3723.004,3725"),
        ("下周上线。", "t=71,72"),  # surrounding blanks trimmed; the empty cue at 70 s has no text
    ]
    assert parsed.note is None and parsed.kind == "transcript"


def test_subrip_with_crlf_comma_or_dot_and_a_binding_note():
    text = fixture("retro.srt")
    parsed = parse_sidecar("srt", text)
    assert parsed.source_sha256 == sha((FIXTURES / "retro.wav").read_bytes())  # recorded in upper case
    assert units("srt", text) == [
        ("The retry budget is 3 attempts per request.", "t=1,4.5"),
        ("<i>Deployments go through staging,</i>\r\nexcept for hotfixes.", "t=5.25,9"),
    ]


def test_ocr_lines_are_exact_ranges_with_page_and_box():
    text = fixture("board.png.ocr.json")
    parsed = parse_sidecar("ocr", text)
    assert parsed.source_sha256 == sha((FIXTURES / "board.png").read_bytes())
    assert units("ocr", text) == [
        ("Feature A (bulk export): rejected for November", "page=1&xywh=40,120,570,38"),
        ("On-call rotation changes every Monday", "page=1&xywh=40,180,480,34"),
        ("公开 API 限流：每分钟 60 次", "page=2&xywh=10,10,390,30"),
    ]
    # The line written with \"...\" escapes cannot be quoted exactly, so it is left out and said so.
    assert parsed.note.startswith("partly indexed: 1 OCR line(s) use JSON escape sequences")
    assert "page 1 line 3" in parsed.note and parsed.kind == "ocr"


def test_time_ranges_read_like_clocks():
    assert time_range("t=12.5,17") == "00:00:12.500-00:00:17.000"
    assert time_range("t=3723.004,3725") == "01:02:03.004-01:02:05.000"
    assert time_range("page=1&xywh=1,2,3,4") is None


@pytest.mark.parametrize("name, fmt", [("clip.mp4.vtt", "vtt"), ("a.SRT", "srt"), ("scan.pdf.ocr.json", "ocr"),
                                       ("Scan.PDF.OCR.JSON", "ocr"), ("notes.json", None), ("ocr.json", None),
                                       (".vtt", None), ("clip.mp4", None), ("x.hocr", None)])
def test_sidecar_formats_by_name(name, fmt):
    assert sidecar_format(name) == fmt


@pytest.mark.parametrize("name, siblings, media", [
    ("clip.mp4.vtt", [], "clip.mp4"),  # named explicitly: bound even when absent
    ("clip.vtt", ["clip.mp4", "clip.txt"], "clip.mp4"),
    ("clip.en.vtt", ["clip.mp4"], "clip.mp4"),
    ("clip.pt-BR.srt", ["clip.mp4"], "clip.mp4"),
    ("scan.ocr.json", ["scan.pdf"], "scan.pdf"),
    ("talk.part2.vtt", ["talk.mp4", "talk.part2.mp4"], "talk.part2.mp4"),  # the closest stem wins
    ("meeting.2026-10-06.vtt", ["meeting.mp4"], None),  # a dated name is not a language tag
    ("call.srt", ["other.wav"], None),
])
def test_which_media_file_a_sidecar_describes(name, siblings, media):
    assert media_for(name, [name, *siblings]) == media


def test_two_media_files_with_one_stem_are_ambiguous():
    with pytest.raises(SidecarError, match=r"more than one media file \(clip.mp4, clip.wav\)"):
        media_for("clip.vtt", ["clip.wav", "clip.mp4"])


# --- malformed input: skipped with a reason, never a crash -----------------------------------------------

HEX = "ab" * 32
MALFORMED = [
    ("vtt", "", "line 1 must be WEBVTT"),
    ("vtt", "WEBVTTX\n", "line 1 must be WEBVTT"),
    ("vtt", "\nWEBVTT\n", "line 1 must be WEBVTT"),
    ("vtt", "WEBVTT\n00:00:01.000 --> 00:00:02.000\nx\n", "blank line between the WEBVTT header"),
    ("vtt", "WEBVTT\n\n00:00:01.000 -> 00:00:02.000\nx\n", "expected a cue timing line"),
    ("vtt", "WEBVTT\n\n00:00:01,000 --> 00:00:02.000\nx\n", "line 3: invalid cue timing"),
    ("vtt", "WEBVTT\n\n00:00:61.000 --> 00:01:02.000\nx\n", "invalid cue timing"),
    ("vtt", "WEBVTT\n\n00:00:05.000 --> 00:00:02.000\nx\n", "ends before it starts"),
    ("vtt", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nx\n00:00:03.000 --> 00:00:04.000\ny\n", "contains '-->'"),
    ("vtt", "WEBVTT\n\nNOTE source_sha256: abc\n", "not a 64-digit hex"),
    ("vtt", "WEBVTT\n\nNOTE source_sha256 " + HEX + "\n", "followed by ':'"),
    ("vtt", f"WEBVTT\n\nNOTE source_sha256: {HEX}\n\nNOTE\nsource_sha256: {HEX}\n", "more than once"),
    ("vtt", "WEBVTT\n\n" + "9" * 5000 + ":00:00.000 --> 00:00:01.000\nx\n", "invalid cue timing"),
    ("srt", "1\nfoo\n", "expected a cue timing line"),
    ("srt", "one\n00:00:01,000 --> 00:00:02,000\nx\n", "expected a cue number"),
    ("srt", "1\n00:00:01 --> 00:00:02\nx\n", "invalid cue timing"),
    ("srt", f"1\n00:00:01,000 --> 00:00:02,000\nx\n\nNOTE source_sha256: {HEX}\n", "expected a cue timing line"),
    ("ocr", "", "invalid JSON at line 1"),
    ("ocr", "[]", "expected a JSON object"),
    ("ocr", '{"format": "other", "pages": []}', "unsupported format"),
    ("ocr", '{"format": "memlab-ocr/1"}', '"pages" must be a list'),
    ("ocr", '{"format": "memlab-ocr/1", "format": "memlab-ocr/1", "pages": []}', "duplicate key"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": []} x', "extra data"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [' * 20, "nested too deeply"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [], "n": 1' + "0" * 60 + "}", "number too long"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": ["unterminated]}', "invalid JSON string"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": ["a\nb"]}', "invalid JSON string"),
    ("ocr", '{"format": "memlab-ocr/1", "source_sha256": "xyz", "pages": []}', "not a 64-digit hex"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [{"page": 0, "lines": []}]}', "page must be an integer"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [{"page": true, "lines": []}]}', "page must be an integer"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [{"page": 1, "lines": []}, {"page": 1, "lines": []}]}',
     "page 1 appears twice"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [{"page": 1}]}', 'needs a "lines" list'),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [{"page": 1, "lines": [{"text": 5, "bbox": [0, 0, 1, 1]}]}]}',
     'needs a "text" string'),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [{"page": 1, "lines": [{"text": "a", "bbox": [0, 0, 1]}]}]}',
     "bbox"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [{"page": 1, "lines": [{"text": "a", "bbox": [0, 0, 1.5, 1]}]}]}',
     "bbox must be an integer"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [{"page": 1, "lines": [{"text": "a", "bbox": [5, 0, 5, 1]}]}]}',
     "empty or outside the page"),
    ("ocr", '{"format": "memlab-ocr/1", "pages": [{"page": 1, "width": 10, "height": 10, '
            '"lines": [{"text": "a", "bbox": [0, 0, 11, 1]}]}]}', "empty or outside the page"),
]


@pytest.mark.parametrize("fmt, text, reason", MALFORMED)
def test_malformed_sidecars_are_rejected_with_a_reason(fmt, text, reason):
    with pytest.raises(SidecarError, match=re.escape(reason)):
        parse_sidecar(fmt, text)


ALPHABET = ["\n", "\r\n", "\r", " ", "\t", "-->", " --> ", ":", ".", ",", "0", "1", "5", "9", "00:00:01.000",
            "00:00:01,000", "NOTE ", "NOTE source_sha256: ", HEX, "WEBVTT", "STYLE", "\ufeff", '"', "\\", "\\u0041",
            "{", "}", "[", "]", '"text": ', '"bbox": [1, 2, 3, 4]', '"page": 1', "-", "e", "true", "null",
            "下周", "\u2028", "\x00", "<v A>"]


def mutate(rng: random.Random, text: str) -> str:
    for _ in range(rng.randint(1, 4)):
        i = rng.randint(0, len(text))
        j = min(len(text), i + rng.randint(0, 12))
        choice = rng.random()
        if choice < 0.3:
            text = text[:i] + text[j:]  # delete
        elif choice < 0.7:
            text = text[:i] + rng.choice(ALPHABET) + text[i:]  # insert a token the parsers care about
        elif choice < 0.85:
            text = text[:i] + text[i:j] * rng.randint(2, 3) + text[j:]  # repeat
        else:
            text = text[:i]  # truncate
    return text


@pytest.mark.parametrize("fmt, name", [("vtt", "clip.mp4.vtt"), ("srt", "retro.srt"), ("ocr", "board.png.ocr.json")])
def test_fuzzed_sidecars_parse_or_fail_cleanly(fmt, name):
    """Mutations of the fixtures either parse into exact, trimmed units or raise SidecarError, nothing else."""
    rng = random.Random(f"memlab-{fmt}")
    seed = fixture(name)
    outcomes = {"parsed": 0, "rejected": 0}
    for _ in range(1500):
        text = mutate(rng, seed)
        try:
            parsed = parse_sidecar(fmt, text)
        except SidecarError as exc:
            assert str(exc) and "\n" not in str(exc)
            outcomes["rejected"] += 1
            continue
        outcomes["parsed"] += 1
        assert parsed.source_sha256 is None or re.fullmatch(r"[0-9a-f]{64}", parsed.source_sha256)
        for unit in parsed.units:
            assert 0 <= unit.start < unit.end <= len(text)
            body = text[unit.start:unit.end]
            assert body == body.strip() and LOCATOR.fullmatch(unit.locator)
            assert fmt == "ocr" or "-->" not in body
            if unit.locator.startswith("t="):
                assert time_range(unit.locator)
    assert outcomes["parsed"] and outcomes["rejected"]  # the mutations exercised both paths


def test_random_bytes_never_crash_the_parsers():
    rng = random.Random("memlab-noise")
    for _ in range(600):
        text = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 40)))
        for fmt in ("vtt", "srt", "ocr"):
            try:
                parse_sidecar(fmt, text)
            except SidecarError:
                pass


def test_malformed_sidecars_are_skipped_by_the_scan(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "bad.vtt").write_bytes(b"not webvtt\n")
    (root / "bad.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:00,500\nbackwards\n")
    (root / "bad.ocr.json").write_bytes(b'{"format": "memlab-ocr/1", "pages": [')
    (root / "latin1.srt").write_bytes("1\n00:00:01,000 --> 00:00:02,000\nCaf\xe9\n".encode("latin-1"))
    (root / "notes.md").write_bytes(b"The queue runs on Kafka.\n")
    passages, skipped = scan(root)
    assert [p.span.path for p in passages] == ["notes.md"]
    assert {s.path: s.reason for s in skipped} == {
        "bad.ocr.json": "OCR sidecar: invalid JSON at line 1, column 38",
        "bad.srt": "SubRip transcript: line 2: cue ends before it starts",
        "bad.vtt": "WebVTT transcript: line 1 must be WEBVTT",
        "latin1.srt": "not UTF-8 text",
    }


# --- binding to the media file ----------------------------------------------------------------------

def by_path(skipped):
    return {s.path: s.reason for s in skipped}


def test_bound_sidecars_are_indexed_and_cover_their_media(media_dir):
    passages, skipped = scan(media_dir)
    assert skipped == [s for s in skipped if s.path == "board.png.ocr.json"]  # only its partial-index note
    media = {(p.span.path, p.span.media.path, p.span.media.locator, p.span.media.sha256) for p in passages}
    assert ("clip.mp4.vtt", "clip.mp4", "t=12.5,17", sha((media_dir / "clip.mp4").read_bytes())) in media
    assert ("retro.srt", "retro.wav", "t=1,4.5", sha((media_dir / "retro.wav").read_bytes())) in media
    assert ("board.png.ocr.json", "board.png", "page=2&xywh=10,10,390,30",
            sha((media_dir / "board.png").read_bytes())) in media
    for passage in passages:
        raw = (media_dir / passage.span.path).read_bytes()
        assert passage.span.sha256 == sha(raw)
        assert raw.decode("utf-8")[passage.span.start:passage.span.end] == passage.span.quote


def test_a_changed_media_file_makes_its_sidecar_stale(media_dir):
    (media_dir / "clip.mp4").write_bytes(b"re-encoded video\n")
    passages, skipped = scan(media_dir)
    reasons = by_path(skipped)
    assert reasons["clip.mp4.vtt"].startswith("WebVTT transcript: stale: its source_sha256 does not match clip.mp4")
    assert reasons["clip.mp4"] == NO_SIDECAR
    assert all(p.span.path != "clip.mp4.vtt" for p in passages)


def test_a_sidecar_without_a_hash_next_to_its_media_is_unbound(media_dir):
    text = fixture("retro.srt")
    (media_dir / "retro.srt").write_bytes(text[text.index("1\r\n"):].encode("utf-8"))
    reasons = by_path(scan(media_dir)[1])
    assert reasons["retro.srt"].startswith("SubRip transcript: not bound to retro.wav: the sidecar records no "
                                           "source_sha256")
    assert reasons["retro.wav"] == NO_SIDECAR


def test_a_sidecar_without_its_media_is_used_and_says_so(media_dir):
    (media_dir / "clip.mp4").unlink()
    (media_dir / "call.vtt").write_bytes(b"WEBVTT\n\n00:00:02.000 --> 00:00:06.500\nThe queue runs on Kafka.\n")
    passages, skipped = scan(media_dir)
    refs = {p.span.path: p.span.media for p in passages}
    assert refs["clip.mp4.vtt"].path == "clip.mp4" and refs["clip.mp4.vtt"].sha256 is None
    assert refs["call.vtt"].path is None and refs["call.vtt"].sha256 is None
    assert "clip.mp4.vtt" not in by_path(skipped) and "call.vtt" not in by_path(skipped)


def test_language_tagged_and_stem_named_sidecars_bind_too(media_dir):
    (media_dir / "clip.mp4.vtt").rename(media_dir / "clip.en.vtt")
    passages, skipped = scan(media_dir)
    assert {p.span.media.path for p in passages if p.span.path == "clip.en.vtt"} == {"clip.mp4"}
    assert "clip.mp4" not in by_path(skipped)
    (media_dir / "clip.wav").write_bytes(b"another rendering\n")
    reasons = by_path(scan(media_dir)[1])
    assert "more than one media file (clip.mp4, clip.wav)" in reasons["clip.en.vtt"]


def test_media_that_cannot_be_checked_fails_the_sidecar_closed(media_dir):
    (media_dir / "board.png").unlink()
    (media_dir / "board.png").mkdir()
    reasons = by_path(scan(media_dir)[1])
    assert "board.png is a broken symlink or not a regular file" in reasons["board.png.ocr.json"]


@symlinks
def test_a_media_symlink_out_of_the_root_is_not_read(media_dir, tmp_path, monkeypatch):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes((media_dir / "clip.mp4").read_bytes())
    (media_dir / "clip.mp4").unlink()
    (media_dir / "clip.mp4").symlink_to(outside)
    hashed = []
    monkeypatch.setattr("memcore.evidence.file_sha256", lambda path: hashed.append(path) or "0" * 64)
    reasons = by_path(scan(media_dir)[1])
    assert "clip.mp4 is a symlink that escapes the evidence root" in reasons["clip.mp4.vtt"]
    assert reasons["clip.mp4"] == "symlink escapes evidence root" and outside not in hashed


def test_each_media_file_is_hashed_once_per_scan(media_dir, monkeypatch):
    shutil.copyfile(media_dir / "clip.mp4.vtt", media_dir / "clip.en.vtt")
    calls = []

    def counting(path):
        calls.append(Path(path).name)
        return file_sha256(path)

    monkeypatch.setattr("memcore.evidence.file_sha256", counting)
    passages, _ = scan(media_dir)
    assert sorted(calls) == ["board.png", "clip.mp4", "retro.wav"]
    assert {p.span.path for p in passages} >= {"clip.en.vtt", "clip.mp4.vtt"}


def test_media_hashing_streams_in_chunks(tmp_path):
    data = os.urandom(3 * 1024 + 7)
    (tmp_path / "big.mp4").write_bytes(data)
    assert file_sha256(tmp_path / "big.mp4", chunk_size=1024) == sha(data)


def test_excluding_a_sidecar_leaves_its_media_uncovered(media_dir):
    passages, skipped = scan(media_dir, exclude=["*.vtt"])
    assert by_path(skipped)["clip.mp4"] == NO_SIDECAR
    assert all(p.span.path != "clip.mp4.vtt" for p in passages)


# --- audit reports ----------------------------------------------------------------------------------

def media_audit(root, *claims, **kwargs):
    memory = root.parent / "MEMORY.md"
    memory.write_bytes("".join(f"- {c}\n" for c in claims).encode("utf-8"))
    return audit(memory, root, as_of=date(2026, 10, 10), **kwargs)


def test_reports_cite_the_media_file_and_position(media_dir):
    report = media_audit(media_dir, "The public API rate limit is 100 requests per minute.",
                         "Feature A (bulk export) is approved for November.")
    markdown = to_markdown(report)
    assert ("· `clip.mp4#t=12.5,17` (00:00:12.500-00:00:17.000) · transcript `clip.mp4.vtt:12` · sha256 "
            f"`{sha((media_dir / 'clip.mp4.vtt').read_bytes())[:12]}` · media sha256 "
            f"`{sha((media_dir / 'clip.mp4').read_bytes())[:12]}` · different values") in markdown
    assert "  > <v Priya>The public API rate limit is 60 requests\n  > per minute per key.\n" in markdown
    assert "· `board.png#page=1&xywh=40,120,570,38` · OCR `board.png.ocr.json:" in markdown
    assert MEDIA_NOTE in report.limitations and f"- {MEDIA_NOTE}" in markdown
    spans = [e["span"] for f in json.loads(report.model_dump_json())["findings"] for e in f["evidence"]]
    assert {"kind": "transcript", "path": "clip.mp4", "locator": "t=12.5,17",
            "sha256": sha((media_dir / "clip.mp4").read_bytes())} in [s["media"] for s in spans]


def test_text_only_reports_are_unchanged_apart_from_a_null_media_field(memfalsify_demo):
    report = audit(memfalsify_demo / "MEMORY.md", memfalsify_demo / "evidence", as_of=date(2026, 10, 10))
    assert MEDIA_NOTE not in report.limitations
    spans = [e.span for f in report.findings for e in f.evidence]
    assert spans and all(s.media is None and s.citation == s.location for s in spans)
    dumped = json.loads(report.model_dump_json())
    assert all(e["span"]["media"] is None for f in dumped["findings"] for e in f["evidence"])


def test_a_transcript_without_media_says_its_binding_was_not_checked(media_dir):
    (media_dir / "clip.mp4").unlink()
    markdown = to_markdown(media_audit(media_dir, "The public API rate limit is 100 requests per minute."))
    assert "`clip.mp4#t=12.5,17` (00:00:12.500-00:00:17.000) · transcript `clip.mp4.vtt:12`" in markdown
    assert "media file not present, binding not checked" in markdown


def test_diff_reports_split_crlf_cues_before_scrubbing_controls(git_repo):
    """Diff Markdown replaces control characters (CR included); a CRLF cue must still print as two lines."""
    base = git_repo.commit("memory", {"MEMORY.md": "- The queue runs on Kafka.\n"})
    git_repo.write({"MEMORY.md": "- The queue runs on Kafka.\n"
                                 "- Deployments go through staging, except for hotfixes.\n"})
    evidence = Path(shutil.copytree(FIXTURES, git_repo.root.parent / "media-evidence"))
    markdown = diff_to_markdown(memory_diff(git_repo.root / "MEMORY.md", base, evidence_root=evidence))
    assert "· `retro.wav#t=5.25,9` (00:00:05.250-00:00:09.000) · transcript `retro.srt:9` ·" in markdown
    assert "  > <i>Deployments go through staging,</i>\n  > except for hotfixes.\n" in markdown
    assert "�" not in markdown


def test_the_media_example_report_is_current(tmp_path, monkeypatch):
    monkeypatch.chdir(shutil.copytree(EXAMPLES / "memfalsify-media", tmp_path / "media"))
    report = audit(Path("MEMORY.md"), Path("evidence"), Path("reviews.jsonl"), as_of=date(2026, 10, 10))
    found = {f.text: f.status for f in report.findings}
    assert found == {
        "The public API rate limit is 100 requests per minute.": "challenged",
        "Feature A (bulk export) is approved for the November release.": "candidate_counterevidence",
        "Bulk export waits for the billing schema migration.": "candidate_support",
        "The on-call rotation changes every Monday.": "candidate_support",
        "The retry budget is 3 attempts per request.": "candidate_support",
        "The payments team owns the refund flow.": "insufficient_evidence",  # its transcript is stale
    }
    assert by_path(report.skipped_evidence) == {
        "recordings/demo.mov": NO_SIDECAR,
        "recordings/retro-2026-09-30.mp4": NO_SIDECAR,
        "recordings/retro-2026-09-30.srt": "SubRip transcript: stale: its source_sha256 does not match "
                                           "retro-2026-09-30.mp4; the media file changed after the sidecar was made",
    }
    assert Path("report.md").read_text(encoding="utf-8") == to_markdown(report)  # committed report is current


# --- reviews ----------------------------------------------------------------------------------------

def review(root, **fields):
    record = {"memory_id": "m-9efe3be5", "relation": "contradicts", "path": "clip.mp4.vtt",
              "quote": "rate limit is 60 requests", "reviewer": "r", **fields}
    path = root.parent / "reviews.jsonl"
    path.write_bytes((json.dumps(record) + "\n").encode("utf-8"))
    return path


def test_reviews_locate_quotes_inside_cues(media_dir):
    report = media_audit(media_dir, "The public API rate limit is 100 requests per minute.",
                         reviews=review(media_dir, locator="t=12.5,17"))
    confirmed = report.findings[0].evidence[0]
    assert report.findings[0].status == "challenged" and confirmed.confirmed
    assert confirmed.span.media.locator == "t=12.5,17" and confirmed.span.citation == "clip.mp4#t=12.5,17"


@pytest.mark.parametrize("fields, message", [
    ({"quote": "requests\nper minute"}, None),  # inside one cue, across its lines: fine
    ({"quote": "per key.\n\nNOTE"}, "not found verbatim in any cue or line"),  # across a cue boundary
    ({"quote": "source_sha256"}, "not found verbatim in any cue or line"),  # a NOTE is not evidence
    ({"quote": "00:00:12.500"}, "not found verbatim in any cue or line"),  # nor is a timing line
    ({"locator": "t=71,72"}, "not found verbatim in the cue or line at t=71,72"),
    ({"path": "notes.md", "quote": "Kafka", "locator": "t=1,2"}, "a locator applies only to transcript"),
])
def test_review_quotes_must_sit_in_one_cue(media_dir, fields, message):
    (media_dir / "notes.md").write_bytes(b"The queue runs on Kafka.\n")
    reviews = review(media_dir, **fields)
    if message is None:
        assert media_audit(media_dir, "The public API rate limit is 100 requests per minute.",
                           reviews=reviews).findings[0].status == "challenged"
        return
    with pytest.raises(EvidenceError, match=re.escape(message)):
        media_audit(media_dir, "The public API rate limit is 100 requests per minute.", reviews=reviews)


def test_a_review_of_a_stale_transcript_stops_the_audit(media_dir):
    (media_dir / "clip.mp4").write_bytes(b"re-encoded video\n")
    memory = media_dir.parent / "MEMORY.md"
    memory.write_bytes(b"- The public API rate limit is 100 requests per minute.\n")
    result = runner.invoke(memfalsify_app, ["audit", str(memory), "--evidence", str(media_dir),
                                            "--reviews", str(review(media_dir))])
    assert result.exit_code == 2 and "unusable WebVTT transcript clip.mp4.vtt: stale" in result.output


# --- orgmemory evidence manifests -------------------------------------------------------------------

def add(dataset, *extra, id="cue"):
    return runner.invoke(org_app, ["evidence", "add", str(dataset), "--id", id, "--path", "media/clip.mp4.vtt",
                                   "--source-uri", "synthetic://standup", "--source-kind", "transcript",
                                   "--captured-at", "2026-10-06T10:00:00Z", *extra])


@pytest.fixture
def org_media(org_demo):
    shutil.copytree(FIXTURES, org_demo / "media")
    return org_demo


def test_evidence_add_records_the_locator_and_validates(org_media):
    before = (org_media / "evidence.jsonl").read_bytes()
    result = add(org_media, "--quote", "The public API rate limit is 60 requests")
    assert result.exit_code == 0, result.output
    assert result.output == ("registered cue at media/clip.mp4#t=12.5,17 (transcript media/clip.mp4.vtt:12)\n")
    data = (org_media / "evidence.jsonl").read_bytes()
    assert data.startswith(before)  # appended only
    record = json.loads(data[len(before):])
    assert record["locator"] == "t=12.5,17" and list(record)[-1] == "locator"
    span = load_manifest(org_media / "evidence.jsonl")["cue"]
    assert span.media.path == "media/clip.mp4" and span.media.sha256 == sha((org_media / "media/clip.mp4").read_bytes())
    assert runner.invoke(org_app, ["validate", str(org_media)]).exit_code == 0


def test_evidence_add_locator_picks_the_cue(org_media):
    clip = org_media / "media" / "clip.mp4.vtt"
    clip.write_bytes(clip.read_bytes() + b"\n00:02:00.000 --> 00:02:01.000\nper minute per key.\n")
    assert add(org_media, "--quote", "per minute per key.", "--locator", "t=120,121").exit_code == 0
    assert load_manifest(org_media / "evidence.jsonl")["cue"].media.locator == "t=120,121"
    missing = add(org_media, "--quote", "per minute per key.", "--locator", "t=1,2", id="other")
    assert missing.exit_code == 2 and "not found verbatim in the cue or line at t=1,2" in missing.output


def test_manifest_records_without_a_locator_keep_their_shape(org_demo):
    entry = ManifestEntry(id="e", path="p", sha256="0" * 64, quote="q", start=0, end=1, source_uri="u",
                          source_kind="k", captured_at="2026-10-07T00:00:00Z")
    assert "locator" not in entry.model_dump(mode="json") and '"locator"' not in entry.model_dump_json()
    assert entry.model_copy(update={"locator": "t=1,2"}).model_dump(mode="json")["locator"] == "t=1,2"
    result = runner.invoke(org_app, ["evidence", "add", str(org_demo), "--id", "plain",
                                     "--path", "sources/2026-10-07-decision-log.txt", "--quote", "Feature A",
                                     "--source-uri", "synthetic://x", "--source-kind", "note",
                                     "--captured-at", "2026-10-09T00:00:00Z"])
    assert result.exit_code == 0
    last = (org_demo / "evidence.jsonl").read_bytes().splitlines()[-1]
    assert list(json.loads(last)) == ["id", "path", "sha256", "quote", "start", "end", "source_uri", "source_kind",
                                      "captured_at"]


@pytest.mark.parametrize("change, message", [
    (lambda r: {**r, "locator": "t=1,2"}, "recorded locator t=1,2 does not match t=12.5,17"),
    (lambda r: {**r, "path": "sources/2026-10-07-decision-log.txt"}, "hash mismatch"),
])
def test_tampered_locators_fail_closed(org_media, change, message):
    assert add(org_media, "--quote", "The public API rate limit is 60 requests").exit_code == 0
    manifest = org_media / "evidence.jsonl"
    lines = manifest.read_bytes().splitlines()
    lines[-1] = json.dumps(change(json.loads(lines[-1]))).encode("utf-8")
    manifest.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(EvidenceError, match=re.escape(message)):
        load_manifest(manifest)


def test_a_locator_on_a_text_file_fails_closed(org_demo):
    manifest = org_demo / "evidence.jsonl"
    records = [json.loads(line) for line in manifest.read_bytes().splitlines()]
    records[0]["locator"] = "t=1,2"
    manifest.write_bytes("".join(json.dumps(r) + "\n" for r in records).encode("utf-8"))
    with pytest.raises(EvidenceError, match="has a locator, but .* is not a transcript or OCR sidecar"):
        load_manifest(manifest)


def test_a_media_file_changed_after_registration_fails_validation(org_media):
    assert add(org_media, "--quote", "The public API rate limit is 60 requests").exit_code == 0
    (org_media / "media" / "clip.mp4").write_bytes(b"re-encoded video\n")
    result = runner.invoke(org_app, ["validate", str(org_media)])
    assert result.exit_code == 2 and "stale" in result.output


def test_explain_and_audit_cite_the_media_position(org_media):
    assert add(org_media, "--quote", "<v Priya>The public API rate limit is 60 requests\nper minute").exit_code == 0
    events = org_media / "events.jsonl"
    for id, actor, action in (("fri", "zhou", "approve"), ("fri-sales", "wu", "reject")):
        events.write_bytes(events.read_bytes() + (json.dumps({
            "id": id, "timestamp": "2026-10-09T09:00:00Z", "actor": actor, "action": action,
            "subject": "Rate limit", "evidence_ids": ["cue"]}) + "\n").encode("utf-8"))
    digest = sha((org_media / "media/clip.mp4.vtt").read_bytes())[:12]
    cited = ("  - evidence `cue` · `media/clip.mp4#t=12.5,17` (00:00:12.500-00:00:17.000) · transcript "
             f"`media/clip.mp4.vtt:12` · synthetic://standup · sha256 `{digest}`\n")
    text = explain(replay(Dataset.load(org_media)), "Rate limit")
    assert cited + "    > <v Priya>The public API rate limit is 60 requests\n    > per minute\n" in text
    assert runner.invoke(org_app, ["explain", str(org_media), "Rate limit"]).output == text + "\n"
    # The decision audit escapes quoted text onto one line, and still shows the media position.
    result = runner.invoke(org_app, ["audit", str(org_media), "--as-of", "2026-10-10T00:00:00Z"])
    assert result.exit_code == 0
    assert (cited + "    > &lt;v Priya&gt;The public API rate limit is 60 requests\\\\u000aper minute\n"
            in result.output)


def test_adr_export_and_html_cite_the_media_position(org_media):
    # ADR export and HTML pages, merged alongside sidecars, locate a cue in its media file too.
    from orgmemory.export import adr_markdown, decision_records
    from orgmemory.htmlreport import replay_html
    assert add(org_media, "--quote", "<v Priya>The public API rate limit is 60 requests\nper minute").exit_code == 0
    events = org_media / "events.jsonl"
    events.write_bytes(events.read_bytes() + (json.dumps({
        "id": "fri", "timestamp": "2026-10-09T09:00:00Z", "actor": "zhou", "action": "approve",
        "subject": "Rate limit", "evidence_ids": ["cue"]}) + "\n").encode("utf-8"))
    data = Dataset.load(org_media)
    _, records = decision_records(data)
    record = next(r for r in records if r.subject == "Rate limit")
    assert ("`media/clip.mp4#t=12.5,17` (00:00:12.500-00:00:17.000) · transcript `media/clip.mp4.vtt:12`"
            in adr_markdown(record))
    page = replay_html(replay(data))
    assert "<code>media/clip.mp4#t=12.5,17</code>" in page and "transcript <code>media/clip.mp4.vtt:12</code>" in page


def test_sidecar_reads_never_write(media_dir):
    before = snapshot(media_dir.parent)
    scan(media_dir)
    load_sidecar(media_dir, "clip.mp4.vtt", fixture("clip.mp4.vtt"))
    locate_quote(media_dir, "board.png.ocr.json", "every Monday")
    assert snapshot(media_dir.parent) == before


def stored_name(path: Path, new_name: str) -> None:
    """Rename, including a case-only change on a case-insensitive filesystem."""
    staging = path.with_name(f".{new_name}.case-tmp")
    path.rename(staging)
    staging.rename(path.with_name(new_name))


def test_embedded_media_names_are_case_sensitive(media_dir, monkeypatch):
    """``Clip.mp4`` is a different file from ``clip.mp4`` on every platform, so it is not hashed."""
    stored_name(media_dir / "clip.mp4.vtt", "Clip.mp4.vtt")
    (media_dir / "talk.vtt").write_bytes(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello there.\n")
    (media_dir / "Talk.mp4").write_bytes(b"video\n")
    hashed = []

    def counting(path):
        hashed.append(Path(path).name)
        return file_sha256(path)

    monkeypatch.setattr("memcore.evidence.file_sha256", counting)
    passages, skipped = scan(media_dir)
    refs = {p.span.path: p.span.media for p in passages}
    assert refs["Clip.mp4.vtt"].path == "Clip.mp4" and refs["Clip.mp4.vtt"].sha256 is None
    assert refs["talk.vtt"].path is None and refs["talk.vtt"].sha256 is None
    reasons = by_path(skipped)
    assert reasons["clip.mp4"] == NO_SIDECAR and reasons["Talk.mp4"] == NO_SIDECAR
    assert not any(name.lower() in {"clip.mp4", "talk.mp4"} for name in hashed)


def test_a_sidecar_record_without_a_locator_is_not_validated(org_media):
    assert add(org_media, "--quote", "The public API rate limit is 60 requests").exit_code == 0
    manifest = org_media / "evidence.jsonl"
    lines = manifest.read_bytes().splitlines()
    record = json.loads(lines[-1])
    assert record.pop("locator") == "t=12.5,17"
    lines[-1] = json.dumps(record).encode("utf-8")
    manifest.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(EvidenceError, match="records no locator"):
        load_manifest(manifest)
    (org_media / "media" / "clip.mp4").write_bytes(b"CHANGED")
    with pytest.raises(EvidenceError, match="records no locator"):
        load_manifest(manifest)


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids control characters in file names")
def test_sidecar_diagnostics_keep_file_names_inert(tmp_path, git_repo):
    stem = "clip\n\x1b\u202eINJECT"
    root = tmp_path / "evidence"
    root.mkdir()
    (root / f"{stem}.mp4").write_bytes(b"a")
    (root / f"{stem}.wav").write_bytes(b"b")
    vtt = f"{stem}.vtt"
    (root / vtt).write_bytes(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello there.\n")
    with pytest.raises(SidecarError, match="more than one media file") as caught:
        media_for(vtt, [f"{stem}.mp4", f"{stem}.wav"])
    message = str(caught.value)
    assert "\n" not in message and "\x1b" not in message and "\u202e" not in message
    report = media_audit(root, "Hello there.")
    reason = by_path(report.skipped_evidence)[vtt]
    assert "more than one media file" in reason
    assert "\n" not in reason and "\x1b" not in reason and "\u202e" not in reason
    assert any(item.path == vtt for item in report.skipped_evidence)  # stored path stays exact
    markdown = to_markdown(report)
    assert "\x1b" not in markdown and "\u202e" not in markdown
    section = markdown.split("## Skipped evidence", 1)[1].split("## Limitations", 1)[0]
    bullets = [line for line in section.splitlines() if line]
    assert bullets and all(line.startswith("- `") for line in bullets)
    base = git_repo.commit("memory", {"MEMORY.md": "- Unrelated claim stays.\n"})
    git_repo.write({"MEMORY.md": "- Unrelated claim stays.\n"
                                 "- The public API rate limit is 100 requests per minute.\n"})
    diff = diff_to_markdown(memory_diff(git_repo.root / "MEMORY.md", base, evidence_root=root,
                                        as_of=date(2026, 10, 10)))
    assert "\x1b" not in diff and "\u202e" not in diff
    diff_section = diff.split("## Skipped evidence", 1)[1].split("## Limitations", 1)[0]
    diff_bullets = [line for line in diff_section.splitlines() if line]
    assert diff_bullets and all(line.startswith("- `") for line in diff_bullets)


def test_explain_quotes_keep_cue_lines_and_drop_terminal_controls(org_media):
    clip = org_media / "media" / "clip.mp4.vtt"
    clip.write_bytes(clip.read_bytes() + (
        "\n00:03:00.000 --> 00:03:01.000\nHello\x1b[2J\u202e there\nline two\n".encode()))
    quote = "Hello\x1b[2J\u202e there\nline two"
    hostile = add(org_media, "--quote", quote, "--locator", "t=180,181", id="bad\n\x1bid")
    assert hostile.exit_code == 0, hostile.output
    assert hostile.output.count("\n") == 1 and "\x1b" not in hostile.output
    assert "registered bad\ufffd\ufffdid at media/clip.mp4#t=180,181" in hostile.output
    missing = add(org_media, "--quote", "no such words", "--locator", "t=1,2\n\x1b", id="nope")
    assert missing.exit_code == 2 and missing.output.count("\n") == 1 and "\x1b" not in missing.output
    assert "not found verbatim" in missing.output
    assert add(org_media, "--quote", quote, "--locator", "t=180,181", id="scrub").exit_code == 0
    record = json.loads((org_media / "evidence.jsonl").read_bytes().splitlines()[-1])
    assert "\x1b" in record["quote"] and "\u202e" in record["quote"]  # the manifest stays exact
    events = org_media / "events.jsonl"
    for event_id, actor, action in (("fri", "zhou", "approve"), ("fri-sales", "wu", "reject")):
        events.write_bytes(events.read_bytes() + (json.dumps({
            "id": event_id, "timestamp": "2026-10-09T09:00:00Z", "actor": actor, "action": action,
            "subject": "Rate limit", "evidence_ids": ["scrub"]}) + "\n").encode())
    text = explain(replay(Dataset.load(org_media)), "Rate limit")
    assert "Hello\ufffd[2J\ufffd there" in text and "    > line two" in text
    assert "\x1b" not in text and "\u202e" not in text
    result = runner.invoke(org_app, ["audit", str(org_media), "--as-of", "2026-10-10T00:00:00Z"])
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output and "\u202e" not in result.output
    assert "\\\\u001b" in result.output and "\\\\u202e" in result.output
