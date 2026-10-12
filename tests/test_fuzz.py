"""Deterministic fuzz of untrusted parsers, plus a regression for each crash that fuzzing found.

Stdlib mutations only (seed 20261011): truncation, byte flips, control and bidi
insertion, and slice duplication. A few thousand cases, capped so CI stays well
under 20 seconds. Allowed failures are ValueError (including EvidenceError,
HistoryError, ValidationError, and UnicodeError) and OSError.
"""
from __future__ import annotations

import copy
import json
import random
import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import write_text_exact
from memcore import EvidenceError, load_json, locate_quote, read_jsonl, sha256_bytes
from memcore.evidence import ManifestEntry, verify_entry
from memfalsify.audit import audit
from memfalsify.cli import app as memfalsify_app
from memfalsify.corpus import scan
from memfalsify.gitstore import MAX_GIT_HEADER, MAX_GIT_OBJECT, HistoryError, _tree_entries, read_batch_object
from memfalsify.history import parse_git_log
from memfalsify.memory import parse_memory, parse_text
from memfalsify.report import to_markdown as audit_markdown
from orgmemory.cli import app as orgmemory_app
from orgmemory.github_map import GithubConfig, convert
from orgmemory.models import Event
from orgmemory.replay import Dataset, load_events, replay
from orgmemory.report import to_markdown

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "github"
SEED = 20261011
DEPTH = 20000  # rejected on 3.10–3.13; shallower nests can succeed and allocate
RUNNER = CliRunner()
UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")
INJECT = ("\x00", "\x1b[31m", "\u202e", "\u202d", "\r", "\ufeff", "\u200b", "\u2028", "\x7f", "\x9f", "\n")
BYTE_INJECT = (b"\x00", b"\x1b[31m", b"\xff", b"\xfe\xff", b"\xef\xbb\xbf", b"\r\n", b"\r", b"\x1b")
BODY = ("---\ntitle: x\n---\n- Priya owns the payments integration.\n"
        "- Feature A was rejected by the product manager.\n")


def _mutate_text(rng: random.Random, text: str, cap: int = 2000) -> str:
    text = text[:cap] or "- hello world\n"
    kind = rng.randrange(4)
    if kind == 0:
        return text[:rng.randrange(len(text) + 1)]
    if kind == 1 and text:
        i = rng.randrange(len(text))
        return text[:i] + text[i + 1:]
    if kind == 2:
        i = rng.randrange(len(text) + 1)
        return (text[:i] + rng.choice(INJECT) + text[i:])[:cap]
    start = rng.randrange(len(text))
    end = min(len(text), start + rng.randrange(1, 40))
    i = rng.randrange(len(text) + 1)
    return (text[:i] + text[start:end] + text[i:])[:cap]


def _cap_lines(text: str, width: int = 240, total: int = 2000) -> str:
    return "\n".join(line[:width] for line in text.split("\n"))[:total]


def _mutate_bytes(rng: random.Random, data: bytes, cap: int = 4000) -> bytes:
    data = data[:cap] or b"{}"
    kind = rng.randrange(4)
    if kind == 0:
        return data[:rng.randrange(len(data) + 1)]
    if kind == 1:
        i = rng.randrange(len(data))
        return data[:i] + bytes([data[i] ^ rng.randrange(1, 256)]) + data[i + 1:]
    if kind == 2:
        i = rng.randrange(len(data) + 1)
        return (data[:i] + rng.choice(BYTE_INJECT) + data[i:])[:cap]
    start = rng.randrange(len(data))
    end = min(len(data), start + rng.randrange(1, 80))
    i = rng.randrange(len(data) + 1)
    return (data[:i] + data[start:end] + data[i:])[:cap]


def _tweak(rng: random.Random, node, budget: int = 4) -> None:
    if budget <= 0:
        return
    if isinstance(node, dict):
        if not node:
            node["x"] = []
            return
        key = rng.choice(list(node))
        roll = rng.randrange(6)
        if roll == 0:
            node[key] = [node[key]]
        elif roll == 1:
            node[key] = rng.choice([None, True, "", "\x1b", "\u202e", [], {}])
        elif roll == 2:
            node[key] = "\x00"
        elif roll == 3 and isinstance(node[key], (dict, list)):
            _tweak(rng, node[key], budget - 1)
        elif roll == 4:
            node["event"] = "commented"
            node["id"] = [1, 2]
        else:
            node[key] = (str(node[key])[:80] + "\u202e")
    elif isinstance(node, list) and node:
        _tweak(rng, node[rng.randrange(len(node))], budget - 1)


class _Stream:
    """Finite stand-in for git cat-file stdout. A read larger than 32MB is a test failure."""

    def __init__(self, blob: bytes):
        self.blob, self.pos, self.reads, self.limits = blob, 0, [], []

    def readline(self, limit: int = -1) -> bytes:
        self.limits.append(limit)
        rest = self.blob[self.pos:]
        nl = rest.find(b"\n")
        chunk = rest if nl < 0 else rest[:nl + 1]
        if isinstance(limit, int) and limit >= 0:
            chunk = chunk[:limit]
        self.pos += len(chunk)
        return chunk

    def read(self, n: int) -> bytes:
        self.reads.append(n)
        if n > MAX_GIT_OBJECT:
            raise RuntimeError(f"oversized read {n}")
        chunk = self.blob[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _assert_cli(result, *needles: str) -> None:
    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert result.output.count("\n") == 1
    assert result.output.startswith("error:")
    assert "\x1b" not in result.output and "\u202e" not in result.output
    for needle in needles:
        assert needle in result.output, result.output


def test_fuzz_harness(tmp_path):
    rng = random.Random(SEED)
    failures: list[str] = []
    count = 0
    memory = (EXAMPLES / "memfalsify" / "MEMORY.md").read_text(encoding="utf-8")
    memory += "\n```\ncode fence\n```\n- [Infrastructure](infra.md)\n"
    reviews = (EXAMPLES / "memfalsify" / "reviews.jsonl").read_bytes()
    events = (EXAMPLES / "orgmemory" / "feature-a" / "events.jsonl").read_bytes()
    policy = (EXAMPLES / "orgmemory" / "feature-a" / "policy.json").read_bytes()
    manifest = (EXAMPLES / "orgmemory" / "feature-a" / "evidence.jsonl").read_bytes()
    capture_bytes = (FIXTURES / "capture-1.json").read_bytes()
    capture = json.loads(capture_bytes)

    def exercise(name, fn, sink=None):
        nonlocal count
        count += 1
        try:
            value = fn()
        except (ValueError, OSError):
            return
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            if len(failures) >= 8:
                pytest.fail("\n".join(failures))
            return
        if sink is not None:
            sink(name, value)

    def terminal(name, text):
        if isinstance(text, str) and UNSAFE.search(text):
            failures.append(f"{name}: terminal control in rendered output")

    for i in range(2000):
        sample = _cap_lines(_mutate_text(rng, memory))
        exercise(f"memory-{i}", lambda sample=sample: parse_text(sample, "MEMORY.md"))
    for i in range(450):
        blob = _mutate_bytes(rng, reviews)
        exercise(f"reviews-{i}", lambda blob=blob: load_json(blob))
    for i in range(250):
        blob = _mutate_bytes(rng, events)
        exercise(f"events-json-{i}", lambda blob=blob: load_json(blob))
    for i in range(200):
        blob = _mutate_bytes(rng, policy)
        exercise(f"policy-json-{i}", lambda blob=blob: load_json(blob))

    jsonl_dir = tmp_path / "jsonl"
    jsonl_dir.mkdir()
    for i in range(80):
        path = jsonl_dir / f"{i}.jsonl"
        path.write_bytes(_mutate_bytes(rng, reviews if i % 2 == 0 else events))
        exercise(f"jsonl-{i}", lambda path=path: list(read_jsonl(path)))

    mem_dir = tmp_path / "mem"
    mem_dir.mkdir()
    for i in range(25):
        path = mem_dir / f"{i}.md"
        path.write_bytes(_mutate_bytes(rng, memory.encode("utf-8"), cap=1500))
        exercise(f"parse-memory-{i}", lambda path=path: parse_memory(path, follow_links=False))

    for i in range(8):
        deep = "[" * DEPTH + "]" * DEPTH
        exercise(f"deep-{i}", lambda deep=deep: load_json(deep if i % 2 == 0 else deep.encode()))

    exercise("stress-lists", lambda: parse_text("\n".join("  " * n + "- item text" for n in range(60)), "MEMORY.md"))
    exercise("stress-line", lambda: parse_text("- " + "word " * 300, "MEMORY.md"))
    exercise("stress-emphasis", lambda: parse_text("**" * 200 + "hello world. " + "**" * 200, "MEMORY.md"))
    exercise("stress-fence", lambda: parse_text("```\n" + "x\n" * 100 + "```\n- A real claim here.\n", "MEMORY.md"))

    oid = "a" * 40
    date = "2026-10-01T00:01:00+00:00"
    for i in range(250):
        raw = f"{oid}\0{date}\0Author {i}\0subject {i}\0".encode()
        blob = _mutate_bytes(rng, raw, cap=300)

        def check(name, commits, _unsafe=UNSAFE):
            for info in commits:
                if _unsafe.search(f"{info.date}\n{info.author}\n{info.subject}"):
                    failures.append(f"{name}: control survived git log parse")

        exercise(f"gitlog-{i}", lambda blob=blob: parse_git_log(blob), check)

    for i in range(120):
        kind = rng.choice([b"blob", b"tree", b"commit", b"tag", b"missing", b"nope"])
        size = rng.choice([b"0", b"1", b"4", b"999999999999", b"00", b"x", b"12"])
        head = bytes([rng.randrange(256) for _ in range(6)]) + b" " + kind + b" " + size
        if rng.randrange(5) == 0:
            head = b"z" * rng.choice([3, 5000])
        elif rng.randrange(4):
            head += b"\n"
        stream = _Stream(head + b"abcd\n")
        exercise(f"cat-{i}", lambda stream=stream: read_batch_object(stream))

    for i in range(60):
        blob = _mutate_bytes(rng, b"100644 hello\x00" + b"\x11" * 20 + b"40000 link\x00" + b"\x22" * 20, cap=200)
        exercise(f"tree-{i}", lambda blob=blob: _tree_entries(blob, 20))

    clean = convert(capture, {}, set(), GithubConfig())
    assert clean.events
    count += 1
    for i in range(40):
        tweaked = copy.deepcopy(capture)
        _tweak(rng, tweaked)
        exercise(f"capture-{i}", lambda tweaked=tweaked: convert(tweaked, {}, set(), GithubConfig()))
    for i in range(30):
        blob = _mutate_bytes(rng, capture_bytes[:4000])

        def run(blob=blob):
            loaded = load_json(blob)
            if isinstance(loaded, dict):
                convert(loaded, {}, set(), GithubConfig())

        exercise(f"capture-bytes-{i}", run)

    note = tmp_path / "note.txt"
    note.write_bytes(b"hello\n")
    digest = sha256_bytes(b"hello\n")
    for i in range(30):
        start = rng.choice([0, 1, 10**18, 10**100])
        end = start + rng.choice([1, 5, 0, -1])
        try:
            entry = ManifestEntry(id=f"e{i}", path="note.txt", sha256=digest if i % 3 else "0" * 64,
                                  quote="hello" if i % 2 else "nope", start=max(start, 0), end=end,
                                  source_uri="u", source_kind="file", captured_at="2026-10-01T00:00:00Z")
        except ValueError:
            count += 1
            continue
        exercise(f"span-{i}", lambda entry=entry: verify_entry(entry, tmp_path))

    ev_root = tmp_path / "evidence"
    ev_root.mkdir()
    for i in range(15):
        (ev_root / "a.md").write_bytes(_mutate_bytes(rng, b"The cache uses Redis.\n", cap=500))
        (ev_root / "bin.dat").write_bytes(b"\xff\x00" + _mutate_bytes(rng, b"text", cap=20))
        exercise(f"scan-{i}", lambda: scan(ev_root))

    org_src = EXAMPLES / "orgmemory" / "feature-a"
    for i in range(20):
        dest = tmp_path / f"org{i}"
        shutil.copytree(org_src, dest)
        target = dest / rng.choice(["events.jsonl", "policy.json", "evidence.jsonl"])
        target.write_bytes(_mutate_bytes(rng, target.read_bytes()))

        def run(dest=dest):
            return to_markdown(replay(Dataset.load(dest)))

        exercise(f"dataset-{i}", run, terminal)

    for i in range(20):
        path = jsonl_dir / f"events-{i}.jsonl"
        path.write_bytes(_mutate_bytes(rng, events))
        exercise(f"load-events-{i}", lambda path=path: load_events(path))

    assert not failures, "\n".join(failures)
    assert count >= 3000, count


def test_load_json_rejects_deep_nesting_and_bad_utf8(tmp_path):
    deep = "[" * DEPTH + "]" * DEPTH
    with pytest.raises(ValueError, match="too deeply nested"):
        load_json(deep)
    with pytest.raises(ValueError, match="too deeply nested"):
        load_json(deep.encode())
    assert load_json('{"a": 1}') == {"a": 1}
    assert load_json("[[]]") == [[]]
    with pytest.raises(ValueError, match="not UTF-8"):
        load_json(b"\xff")
    path = tmp_path / "rows.jsonl"
    write_text_exact(path, deep + "\n")
    with pytest.raises(ValueError, match="too deeply nested") as caught:
        list(read_jsonl(path))
    assert f"{path}:1:" in str(caught.value)
    path.write_bytes(b"\xff\n")
    with pytest.raises(ValueError, match="not UTF-8"):
        list(read_jsonl(path))


def test_claims_invalid_utf8_exits_2(tmp_path):
    memory = tmp_path / "MEMORY.md"
    memory.write_bytes(b"\xff\xfe not utf-8\n")
    result = RUNNER.invoke(memfalsify_app, ["claims", str(memory)])
    _assert_cli(result, "not UTF-8")


def test_deep_events_policy_and_append_exit_2(org_demo):
    events = org_demo / "events.jsonl"
    before = events.read_bytes()
    deep = "[" * DEPTH + "]" * DEPTH
    write_text_exact(events, deep + "\n")
    _assert_cli(RUNNER.invoke(orgmemory_app, ["validate", str(org_demo)]), "too deeply nested")
    events.write_bytes(before)
    write_text_exact(org_demo / "policy.json", deep)
    _assert_cli(RUNNER.invoke(orgmemory_app, ["validate", str(org_demo)]), "too deeply nested")
    events.write_bytes(before)
    write_text_exact(
        org_demo / "policy.json", (EXAMPLES / "orgmemory" / "feature-a" / "policy.json").read_text(encoding="utf-8"))
    result = RUNNER.invoke(orgmemory_app, ["append", str(org_demo), "--event", deep])
    _assert_cli(result, "too deeply nested")
    assert events.read_bytes() == before


def test_deep_github_capture_exits_2(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    capture = tmp_path / "capture.json"
    write_text_exact(capture, "[" * DEPTH + "]" * DEPTH)
    result = RUNNER.invoke(orgmemory_app, ["github", "convert", str(dataset), str(capture), "--dry-run"])
    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "invalid input" in result.output
    assert "\x1b" not in result.output


def test_comment_id_list_is_a_valueerror(tmp_path):
    capture = json.loads((FIXTURES / "capture-1.json").read_bytes())
    found = False
    for item in capture["items"]:
        for event in item.get("timeline", []):
            if isinstance(event, dict) and event.get("event") == "commented":
                event["id"] = [1, 2]
                found = True
                break
        if found:
            break
    assert found
    with pytest.raises(ValueError):
        convert(capture, {}, set(), GithubConfig())
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    path = tmp_path / "capture.json"
    write_text_exact(path, json.dumps(capture))
    result = RUNNER.invoke(orgmemory_app, ["github", "convert", str(dataset), str(path), "--dry-run"])
    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output and "TypeError" not in result.output
    assert "invalid input" in result.output


def test_cat_file_bounds_header_and_size():
    empty = _Stream(b"")
    with pytest.raises(HistoryError, match="stopped unexpectedly"):
        read_batch_object(empty)
    assert empty.limits == [MAX_GIT_HEADER + 1] and empty.reads == []

    huge = _Stream(b"abcdef blob 999999999999\n" + b"z" * 8)
    with pytest.raises(HistoryError, match="32MB"):
        read_batch_object(huge)
    assert huge.reads == []

    missing = _Stream(b"abcdef missing\n")
    assert read_batch_object(missing) is None and missing.reads == []

    overlong = _Stream(b"a" * 5000)
    with pytest.raises(HistoryError, match="malformed header"):
        read_batch_object(overlong)
    assert overlong.reads == []

    truncated = _Stream(b"abcd blob 4\nxy\n")
    with pytest.raises(HistoryError, match="truncated"):
        read_batch_object(truncated)

    assert read_batch_object(_Stream(b"abcd blob 4\nWXYZ\n")) == ("abcd", "blob", b"WXYZ")
    with pytest.raises(HistoryError, match="malformed header"):
        read_batch_object(_Stream(b"\xff blob 1\nZ\n"))


def test_git_log_date_is_strict_and_text_is_printable():
    oid, date = "a" * 40, "2026-10-01T00:01:00+00:00"

    def record(when, author="Ada", subject="subject"):
        return f"{oid}\0{when}\0{author}\0{subject}\0".encode()

    info = parse_git_log(record(date))[0]
    assert info.date == date and info.author == "Ada" and info.subject == "subject"
    assert parse_git_log(record("2026-10-11T14:15:40-05:00"))[0].date.endswith("-05:00")
    assert parse_git_log(record("2026-10-11T14:15:40Z"))[0].date.endswith("Z")
    assert parse_git_log(b"") == []
    with pytest.raises(HistoryError, match="cannot parse git log"):
        parse_git_log(record(date[:19] + "\x1b[31m"))
    with pytest.raises(HistoryError, match="cannot parse git log"):
        parse_git_log(record("2026-10-01T00:01:00.1+00:00"))
    cleaned = parse_git_log(record(date, "Au\x1bthor", "sub\u202eject"))[0]
    assert "\x1b" not in cleaned.author and "\u202e" not in cleaned.subject
    assert "\\u001b" in cleaned.author and "\\u202e" in cleaned.subject


def test_null_in_a_link_is_skipped_and_locate_quote_refuses_it(tmp_path):
    memory = tmp_path / "MEMORY.md"
    write_text_exact(memory, "- See [notes](bad\x00name.md) for the details.\n"
                             "- Priya owns the payments integration.\n")
    claims, _ = parse_memory(memory)
    assert any(claim.text == "Priya owns the payments integration." for claim in claims)
    with pytest.raises(EvidenceError, match="null"):
        locate_quote(tmp_path, "a\x00b", "x")


def test_bom_and_cr_match_lf_claims(tmp_path):
    def pairs(text):
        claims, _ = parse_text(text, "MEMORY.md")
        return [(claim.text, claim.line, claim.id) for claim in claims]

    lf = pairs(BODY)
    assert [row[0] for row in lf] == ["Priya owns the payments integration.",
                                      "Feature A was rejected by the product manager."]
    assert lf[0][1] == 4
    assert pairs("\ufeff" + BODY) == lf
    assert pairs(BODY.replace("\n", "\r\n")) == lf
    assert pairs(BODY.replace("\n", "\r")) == lf
    path = tmp_path / "MEMORY.md"
    path.write_bytes(b"\xef\xbb\xbf" + BODY.encode("utf-8"))
    claims, _ = parse_memory(path, follow_links=False)
    assert claims[0].text == "Priya owns the payments integration." and claims[0].line == 4


def test_huge_evidence_offsets_are_evidence_errors(tmp_path):
    raw = b"hello\n"
    (tmp_path / "note.txt").write_bytes(raw)
    digest = sha256_bytes(raw)
    common = dict(path="note.txt", sha256=digest, quote="hello", source_uri="u", source_kind="file",
                  captured_at="2026-10-01T00:00:00Z")
    huge = ManifestEntry(id="huge", start=10**100, end=10**100 + 5, **common)
    with pytest.raises(EvidenceError, match="quote does not match"):
        verify_entry(huge, tmp_path)
    mismatch = ManifestEntry(id="bad", start=0, end=5, sha256="0" * 64, quote="hello", path="note.txt",
                             source_uri="u", source_kind="file", captured_at="2026-10-01T00:00:00Z")
    with pytest.raises(EvidenceError, match="hash mismatch"):
        verify_entry(mismatch, tmp_path)
    quote = ManifestEntry(id="quote", start=0, end=5, sha256=digest, quote="world", path="note.txt",
                          source_uri="u", source_kind="file", captured_at="2026-10-01T00:00:00Z")
    with pytest.raises(EvidenceError, match="quote does not match"):
        verify_entry(quote, tmp_path)


def test_naive_timestamp_is_a_validation_error():
    with pytest.raises(ValueError):
        Event.model_validate({"id": "e", "timestamp": "2026-10-01T00:00:00", "actor": "zhou",
                              "action": "approve", "subject": "Feature A", "evidence_ids": ["e"]})


def test_cli_error_is_one_line_without_controls(tmp_path):
    try:
        root = tmp_path / "bad\x1bdir"
        root.mkdir()
    except OSError:
        pytest.skip("platform rejects control characters in directory names")
    (root / "events.jsonl").write_bytes(b"\xff\n")
    write_text_exact(root / "policy.json", "{}")
    write_text_exact(root / "evidence.jsonl", "")
    _assert_cli(RUNNER.invoke(orgmemory_app, ["validate", str(root)]), "not UTF-8")


def test_audit_stdout_and_written_output_escape_controls(tmp_path):
    memory = tmp_path / "MEMORY.md"
    write_text_exact(memory, "- The cache uses \x1b[31mRedis\u202e for sessions.\n")
    library = audit_markdown(audit(memory))
    assert "\x1b" not in library and "\u202e" not in library
    assert "u001b" in library and "u202e" in library
    shown = RUNNER.invoke(memfalsify_app, ["audit", str(memory)])
    assert shown.exit_code == 0, shown.output
    assert "\x1b" not in shown.output and "\u202e" not in shown.output
    out = tmp_path / "report.md"
    saved = RUNNER.invoke(memfalsify_app, ["audit", str(memory), "-o", str(out)])
    assert saved.exit_code == 0, saved.output
    assert out.read_bytes() == library.encode("utf-8")
    payload = RUNNER.invoke(memfalsify_app, ["audit", str(memory), "--format", "json"])
    assert payload.exit_code == 0, payload.output
    assert "\x1b" not in payload.output and "\u202e" not in payload.output
    assert any("\u202e" in item for item in _strings(json.loads(payload.output)))


def test_claims_json_shields_bidi_and_round_trips(tmp_path):
    memory = tmp_path / "MEMORY.md"
    write_text_exact(memory, "- The cache uses \u202eRedis for sessions.\n")
    result = RUNNER.invoke(memfalsify_app, ["claims", str(memory)])
    assert result.exit_code == 0, result.output
    assert "\u202e" not in result.output
    assert "\u202e" in json.loads(result.output)["text"]


def test_replay_markdown_strips_terminal_controls(org_demo):
    path = org_demo / "events.jsonl"
    write_text_exact(path, path.read_text(encoding="utf-8").replace("Bulk export", "Bulk \\u001b\\u202eexport", 1))
    rendered = to_markdown(replay(Dataset.load(org_demo)))
    assert "\x1b" not in rendered and "\u202e" not in rendered
    assert "rejected" in rendered
    result = RUNNER.invoke(orgmemory_app, ["replay", str(org_demo)])
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output and "\u202e" not in result.output
    assert "rejected" in result.output
