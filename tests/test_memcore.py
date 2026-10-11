import json
import os

import pytest
from pydantic import ValidationError

from conftest import requires_confinement
from memcore import EvidenceError, append_jsonl, load_manifest, locate_quote, read_jsonl
from memcore.jsonutil import loads, loads_at
from memcore.reading import confined_reads, read_file_bytes


def write_manifest(root, **overrides):
    (root / "src.txt").write_text("Alpha line.\nThe PM rejects Feature A.\n", encoding="utf-8")
    span = locate_quote(root, "src.txt", "The PM rejects Feature A.")
    entry = {"id": "e1", "path": "src.txt", "sha256": span.sha256, "quote": span.quote, "start": span.start,
             "end": span.end, "source_uri": "synthetic://t", "source_kind": "test",
             "captured_at": "2026-10-07T00:00:00Z", **overrides}
    (root / "evidence.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return root / "evidence.jsonl"


def test_manifest_verifies_hash_and_span(tmp_path):
    spans = load_manifest(write_manifest(tmp_path))
    assert spans["e1"].quote == "The PM rejects Feature A." and spans["e1"].line == 2


def test_tampered_file_fails_closed(tmp_path):
    manifest = write_manifest(tmp_path)
    (tmp_path / "src.txt").write_text("Alpha line.\nThe PM approves Feature A.\n", encoding="utf-8")
    with pytest.raises(EvidenceError, match="hash mismatch"):
        load_manifest(manifest)


def test_invented_quote_fails_closed(tmp_path):
    with pytest.raises(EvidenceError, match="quote does not match"):
        load_manifest(write_manifest(tmp_path, quote="The PM approves Feature A."))


def test_quote_must_exist_verbatim(tmp_path):
    write_manifest(tmp_path)
    with pytest.raises(EvidenceError, match="not found verbatim"):
        locate_quote(tmp_path, "src.txt", "PM approves")


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/hosts"])
def test_paths_cannot_escape_root(tmp_path, path):
    (tmp_path.parent / "outside.txt").write_text("x", encoding="utf-8")
    with pytest.raises(EvidenceError, match="escapes|relative"):
        locate_quote(tmp_path, path, "x")


def test_symlink_cannot_escape_root(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    with pytest.raises(EvidenceError, match="escapes"):
        locate_quote(tmp_path, "link.txt", "secret")


def test_naive_capture_time_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_manifest(write_manifest(tmp_path, captured_at="2026-10-07T00:00:00"))


def test_duplicate_evidence_id_rejected(tmp_path):
    manifest = write_manifest(tmp_path)
    manifest.write_text(manifest.read_text() * 2, encoding="utf-8")
    with pytest.raises(EvidenceError, match="duplicate"):
        load_manifest(manifest)


@pytest.mark.parametrize("existing", [b"", b'{"id": 1}', b'{"id": 1}\n', b'{"id": 1}\r\n'])
def test_append_jsonl_only_appends(tmp_path, existing):
    path = tmp_path / "log.jsonl"
    path.write_bytes(existing)
    append_jsonl(path, {"id": 2, "text": "证据"})
    separator = b"\n" if existing and not existing.endswith(b"\n") else b""
    assert path.read_bytes() == existing + separator + '{"id": 2, "text": "证据"}\n'.encode("utf-8")
    assert [r["id"] for _, r in read_jsonl(path)] == ([1, 2] if existing else [2])


@pytest.mark.parametrize("component", ["file", "directory", pytest.param(
    "fifo", marks=pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO tests require os.mkfifo"),
)])
@requires_confinement
def test_confined_open_rejects_swaps_after_resolution(tmp_path, monkeypatch, component):
    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "nested").mkdir(parents=True)
    outside.mkdir()
    target = root / "nested" / "data.txt"
    target.write_bytes(b"inside")
    (outside / "data.txt").write_bytes(b"outside-sentinel")
    original = os.open
    triggered = []

    def swap_then_open(path, flags, *args, **kwargs):
        trigger = "nested" if component == "directory" else "data.txt"
        if path == trigger and not triggered:
            triggered.append(True)
            if component == "directory":
                (root / "nested").rename(root / "previous")
                (root / "nested").symlink_to(outside, target_is_directory=True)
            else:
                target.unlink()
                if component == "fifo":
                    os.mkfifo(target)
                else:
                    target.symlink_to(outside / "data.txt")
        return original(path, flags, *args, **kwargs)

    with confined_reads(root):
        monkeypatch.setattr(os, "open", swap_then_open)
        with pytest.raises((OSError, ValueError)):
            read_file_bytes(target)
    assert triggered


@requires_confinement
def test_confined_reads_accept_in_root_links_and_reset_after_failure(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "data.txt").write_bytes(b"inside")
    (root / "link.txt").symlink_to(root / "data.txt")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside-sentinel")
    with pytest.raises(ValueError, match="outside the server root"):
        with confined_reads(root):
            assert read_file_bytes(root / "link.txt") == b"inside"
            read_file_bytes(outside)
    # The context must not affect the existing CLI/library behavior.
    assert read_file_bytes(outside) == b"outside-sentinel"


@pytest.mark.parametrize("text,line", [
    ('{"a": 1, "a": 2}', 1),
    ('{"": 1, "": 2}', 1),
    ('{\n  "a": 1,\n  "a": 2\n}', 3),
    ('{\n  "a": 1,\n  "\\u0061": 2\n}', 3),
    ('{"a": {"b": 1, "b": 2}}', 1),
    ('{\n  "a": {\n    "b": 1,\n    "b": 2\n  }\n}', 4),
    ('[{"id": 1}, {"id": 1, "id": 2}]', 1),
    ('[\n  {"id": 1},\n  {"id": 1, "id": 2}\n]', 3),
    ('{\n  "a": "{\\"b\\": 1, \\"b\\": 2}",\n  "c": 1,\n  "c": 2\n}', 4),
    ('{\n  "actor": {"id": "zhou"},\n  "actor": {"id": "mallory"}\n}', 3),
])
def test_duplicate_key_names_its_line(text, line):
    with pytest.raises(json.JSONDecodeError) as caught:
        loads(text)
    assert caught.value.msg == "duplicate key" and caught.value.lineno == line
    with pytest.raises(ValueError) as wrapped:
        loads_at("policy.json", text)
    assert str(wrapped.value) == f"policy.json:{line}: invalid JSON: duplicate key"


def test_duplicate_key_line_in_crlf_bytes():
    data = b'{\r\n  "a": 1,\r\n  "a": 2\r\n}\r\n'
    with pytest.raises(json.JSONDecodeError) as caught:
        loads(data)
    assert caught.value.msg == "duplicate key" and caught.value.lineno == 3


@pytest.mark.parametrize("text", [
    "{}",
    "[]",
    "null",
    "1",
    '"a"',
    '{"a": 1, "b": {"a": 2}}',
    '{"a": "{\\"a\\": 1, \\"a\\": 2}"}',
    '[{"a": 1}, {"a": 2}]',
    '{\n  "a": {"id": 1},\n  "b": {"id": 1}\n}',
])
def test_unique_keys_parse_like_json(text):
    assert loads(text) == json.loads(text)


def test_parse_constant_still_rejects_nonstandard_numbers():
    def no_constant(name):
        raise ValueError(f"non-standard JSON constant {name}")

    with pytest.raises(ValueError, match="NaN"):
        loads('{"a": NaN}', parse_constant=no_constant)


@pytest.mark.parametrize("body", [
    b'\n{"a": 1, "a": 2}\n',
    b'{"a": 1}\n{"note": {"a": 1, "a": 2}}\n',
    b'{"a": 1}\r\n{"a": 1, "a": 2}\r\n',
])
def test_read_jsonl_rejects_duplicate_keys(tmp_path, body):
    path = tmp_path / "log.jsonl"
    path.write_bytes(body)
    with pytest.raises(ValueError) as caught:
        list(read_jsonl(path))
    assert str(caught.value) == f"{path}:2: invalid JSON: duplicate key"


def test_read_jsonl_allows_the_same_key_in_different_objects(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"actor": "a", "meta": {"actor": "b"}}\n', encoding="utf-8")
    assert list(read_jsonl(path)) == [(1, {"actor": "a", "meta": {"actor": "b"}})]


def test_manifest_rejects_duplicate_keys(tmp_path):
    manifest = write_manifest(tmp_path)
    original = manifest.read_text(encoding="utf-8")
    manifest.write_text(original.replace('{"id":', '{"id": "shadow", "id":', 1), encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_manifest(manifest)
    assert str(caught.value) == f"{manifest}:1: invalid JSON: duplicate key"

    body = original.rstrip("\n")
    manifest.write_text(body[:-1] + ', "meta": {"k": 1, "k": 2}}\n', encoding="utf-8")
    with pytest.raises(ValueError) as nested:
        load_manifest(manifest)
    assert str(nested.value) == f"{manifest}:1: invalid JSON: duplicate key"
