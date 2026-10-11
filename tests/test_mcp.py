"""The MCP server, exercised through the SDK's own client: in memory, and over real stdio."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("mcp")
import anyio  # noqa: E402  (an mcp dependency)
from mcp import Client, StdioServerParameters, stdio_client  # noqa: E402

from conftest import EXAMPLES, requires_confinement, snapshot  # noqa: E402
from memfalsify.audit import audit  # noqa: E402
from memfalsify import corpus as memfalsify_corpus  # noqa: E402
from memfalsify.memory import parse_memory  # noqa: E402
from memfalsify.verify import LexicalVerifier  # noqa: E402
from memlab_mcp import server as memlab_server  # noqa: E402
from memlab_mcp.server import create_server  # noqa: E402
from orgmemory.audit import audit as org_audit  # noqa: E402
from orgmemory.replay import Dataset  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
GIT_TOOLS = {"memfalsify_diff", "memfalsify_history", "memfalsify_rollback"}
TOOLS = {"memfalsify_claims", "memfalsify_audit", "orgmemory_validate", "orgmemory_replay", "orgmemory_explain",
         "orgmemory_audit"} | GIT_TOOLS
# The arguments each tool needs besides its path, for tests that vary only the path.
REQUIRED = {"orgmemory_explain": {"subject": "Feature A"}, "memfalsify_diff": {"base": "HEAD"},
            "memfalsify_rollback": {"claim": "m-00000000", "to": "HEAD"}}
MEMORY, EVIDENCE, REVIEWS, DATASET = "memfalsify/MEMORY.md", "memfalsify/evidence", "memfalsify/reviews.jsonl", "feature-a"
SECRET = "outside-the-root secret: do not read"


@pytest.fixture
def root(tmp_path):
    """A server root holding copies of both examples, next to a file the server must never read."""
    base = tmp_path / "root"
    shutil.copytree(EXAMPLES / "memfalsify", base / "memfalsify")
    shutil.copytree(EXAMPLES / "orgmemory" / "feature-a", base / DATASET)
    (tmp_path / "outside.md").write_text(f"- {SECRET}.\n", encoding="utf-8")
    return base


def session(server, *calls, mode="legacy"):
    """Open one SDK client session, run (tool, arguments) calls, return the results.

    mode="legacy" sends JSON-RPC over in-memory streams after an initialize handshake;
    mode="auto" negotiates the SDK's newer protocol in process.
    """
    async def main():
        async with Client(server, mode=mode) as client:
            return [await client.call_tool(name, arguments) for name, arguments in calls]
    return anyio.run(main)


def call(server, name, **arguments):
    return session(server, (name, arguments))[0]


def text(result) -> str:
    assert len(result.content) == 1 and result.content[0].type == "text"
    return result.content[0].text


def ok(result) -> str:
    assert not result.is_error, text(result)
    return text(result)


def error(result) -> str:
    assert result.is_error, text(result)
    message = text(result)
    assert "Traceback" not in message
    return message


# --- listing and round trips -----------------------------------------------------------

def test_server_refuses_unsupported_confinement(root, monkeypatch):
    monkeypatch.setattr(memlab_server, "confinement_supported", lambda: False)
    with pytest.raises(ValueError, match="POSIX"):
        create_server(root)


def test_startup_error_is_reported_without_a_traceback(root, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setattr(memlab_server, "confinement_supported", lambda: False)
    result = CliRunner().invoke(memlab_server.app, ["--root", str(root)])
    assert result.exit_code == 2 and "POSIX filesystem support" in result.output
    assert "Traceback" not in result.output

@pytest.mark.parametrize("mode", ["legacy", "auto"])
@requires_confinement
def test_lists_read_only_tools_and_calls_one(root, mode):
    async def main():
        async with Client(create_server(root), mode=mode) as client:
            assert client.server_info.name == "memlab"
            assert str(root.resolve()) in client.instructions and "untrusted" in client.instructions
            listed = (await client.list_tools()).tools
            return listed, await client.call_tool("orgmemory_validate", {"dataset": DATASET})

    listed, result = anyio.run(main)
    assert {t.name for t in listed} == TOOLS
    for tool in listed:
        assert tool.annotations.read_only_hint is True and tool.annotations.destructive_hint is False
        assert tool.annotations.open_world_hint is False
        lowered = tool.description.lower()
        assert "file contents" in lowered and "untrusted data" in lowered
    assert ok(result).startswith("ok: ")


@requires_confinement
def test_claims_match_the_library(root):
    claims = json.loads(ok(call(create_server(root), "memfalsify_claims", memory_file=MEMORY)))
    expected, _ = parse_memory(root / MEMORY)
    assert claims == [{"memory_id": c.id, "location": c.location, "text": c.text} for c in expected]


@requires_confinement
def test_audit_matches_the_library_and_hides_the_root(root):
    server = create_server(root)
    arguments = dict(memory_file=MEMORY, evidence_dir=EVIDENCE, reviews_file=REVIEWS, as_of="2026-10-10")
    report = json.loads(ok(call(server, "memfalsify_audit", format="json", **arguments)))
    direct = audit(root / MEMORY, root / EVIDENCE, root / REVIEWS, as_of=date(2026, 10, 10))
    assert {f["memory_id"]: f["status"] for f in report["findings"]} == {f.memory_id: f.status for f in direct.findings}
    assert report["summary"] == direct.summary
    assert (report["memory_file"], report["evidence_root"], report["verifier"]) == (MEMORY, EVIDENCE, "lexical")
    assert report["mode"] == "read_only"

    markdown = ok(call(server, "memfalsify_audit", **arguments))
    assert markdown.startswith("# MemFalsify audit report")
    assert "CHALLENGED" in markdown and "media file" in markdown
    assert str(root.resolve()) not in markdown


@requires_confinement
def test_audit_without_evidence_and_absolute_path_inside_root(root):
    report = json.loads(ok(call(create_server(root), "memfalsify_audit",
                                memory_file=str(root / MEMORY), format="json")))
    assert report["evidence_root"] is None
    assert {f["status"] for f in report["findings"]} == {"insufficient_evidence"}


@requires_confinement
def test_orgmemory_replay_and_explain(root):
    server = create_server(root)
    state = json.loads(ok(call(server, "orgmemory_replay", dataset=DATASET, format="json")))["states"]["Feature A"]
    assert (state["current_status"], state["reconsideration_requested"]) == ("rejected", True)
    assert state["last_authorized_decision"]["event_id"] == "ev-wed-reject"

    tuesday = json.loads(ok(call(server, "orgmemory_replay", dataset=DATASET, format="json",
                                 as_of="2026-10-06T23:59:59Z")))["states"]["Feature A"]
    assert tuesday["current_status"] == "proposed"
    assert ok(call(server, "orgmemory_replay", dataset=DATASET)).startswith("# Organizational memory replay")

    why = ok(call(server, "orgmemory_explain", dataset=DATASET, subject="Feature A"))
    assert "ev-wed-reject" in why and "Feature A is rejected" in why
    assert "unknown subject 'Feature Z'; known: Feature A" in error(
        call(server, "orgmemory_explain", dataset=DATASET, subject="Feature Z"))


@requires_confinement
def test_orgmemory_audit_matches_the_library(root):
    server = create_server(root)
    as_of = "2026-10-31T00:00:00Z"
    report = ok(call(server, "orgmemory_audit", dataset=DATASET, as_of=as_of, format="json"))
    direct = org_audit(Dataset.load(root / DATASET), datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert report == direct.model_dump_json(indent=2)
    assert [(f["kind"], f["severity"]) for f in json.loads(report)["findings"]] == [
        ("stale_reconsideration", "medium"), ("unauthorized_attempts", "medium")]

    markdown = ok(call(server, "orgmemory_audit", dataset=DATASET, as_of=as_of))
    assert markdown.startswith("# Organizational decision audit\n\n| Kind | High | Medium | Low |")
    assert "Feature A is rejected" in markdown and str(root.resolve()) not in markdown
    relaxed = json.loads(ok(call(server, "orgmemory_audit", dataset=DATASET, as_of=as_of, stale_days=60,
                                 format="json")))
    assert (relaxed["stale_days"], relaxed["summary"]) == (60, {"high": 0, "medium": 1, "low": 0})
    # Without as_of, staleness is measured at the current time.
    assert json.loads(ok(call(server, "orgmemory_audit", dataset=DATASET, format="json")))["as_of"] is None


# --- path safety ---------------------------------------------------------------------------

def escapes(root):
    """Paths that name ../outside.md (or its directory) without staying inside the root."""
    (root / "link.md").symlink_to(root.parent / "outside.md")
    (root / "linkdir").symlink_to(root.parent, target_is_directory=True)
    return ["../outside.md", "memfalsify/../../outside.md", str(root.parent / "outside.md"), "link.md",
            "linkdir/outside.md"]


@pytest.mark.parametrize("index", range(5))
@requires_confinement
def test_memory_paths_cannot_escape_the_root(root, index):
    server, path = create_server(root), escapes(root)[index]
    for tool in ("memfalsify_claims", "memfalsify_audit"):
        message = error(call(server, tool, memory_file=path))
        assert "outside the server root" in message and SECRET not in message


@requires_confinement
def test_other_path_arguments_cannot_escape(root):
    server = create_server(root)
    (root / "evil-dataset").mkdir()
    for name in ("events.jsonl", "policy.json", "evidence.jsonl"):
        shutil.copy(root / DATASET / name, root / "evil-dataset" / name)
    (root / "evil-dataset" / "events.jsonl").unlink()
    (root / "evil-dataset" / "events.jsonl").symlink_to(root.parent / "outside.md")

    cases = [
        ("memfalsify_audit", dict(memory_file=MEMORY, evidence_dir="..")),
        ("memfalsify_audit", dict(memory_file=MEMORY, evidence_dir=str(root.parent))),
        ("memfalsify_audit", dict(memory_file=MEMORY, evidence_dir=EVIDENCE, reviews_file="../outside.md")),
        ("orgmemory_validate", dict(dataset="..")),
        ("orgmemory_replay", dict(dataset="/")),
        ("orgmemory_explain", dict(dataset="../root/../..", subject="Feature A")),
        ("orgmemory_audit", dict(dataset="..")),
    ]
    for name, arguments in cases:
        message = error(call(server, name, **arguments))
        assert "outside the server root" in message, (name, arguments)
    message = error(call(server, "orgmemory_validate", dataset="evil-dataset"))
    assert "events.jsonl is a symlink that leaves the server root" in message and SECRET not in message


@requires_confinement
def test_evidence_symlinks_out_of_the_root_are_skipped_not_read(root):
    (root / EVIDENCE / "leak.md").symlink_to(root.parent / "outside.md")
    report = json.loads(ok(call(create_server(root), "memfalsify_audit", memory_file=MEMORY,
                                evidence_dir=EVIDENCE, format="json")))
    assert {"path": "leak.md", "reason": "symlink escapes evidence root"} in report["skipped_evidence"]
    assert SECRET not in json.dumps(report)


@requires_confinement
def test_memory_symlink_swap_cannot_escape_root(root, monkeypatch):
    original = memlab_server.parse_memory

    def swap_then_parse(path):
        path.unlink()
        path.symlink_to(root.parent / "outside.md")
        return original(path)

    monkeypatch.setattr(memlab_server, "parse_memory", swap_then_parse)
    result = call(create_server(root), "memfalsify_claims", memory_file=MEMORY)
    assert SECRET not in text(result)
    error(result)


@requires_confinement
def test_evidence_directory_swap_cannot_list_outside_root(root, monkeypatch):
    outside = root.parent / "outside-evidence"
    outside.mkdir()
    (outside / "private-filename.png").write_bytes(b"synthetic")
    original = memfalsify_corpus.walk_files
    called = []

    def swap_then_walk(path, onerror=None):
        called.append(path)
        path.rename(path.with_name("previous-evidence"))
        path.symlink_to(outside, target_is_directory=True)
        return original(path, onerror=onerror)

    monkeypatch.setattr(memfalsify_corpus, "walk_files", swap_then_walk)
    result = call(create_server(root), "memfalsify_audit", memory_file=MEMORY,
                  evidence_dir=EVIDENCE, format="json")
    assert "private-filename" not in text(result)
    error(result)
    assert called == [root / EVIDENCE]
    assert (root / EVIDENCE).is_symlink()
    assert (root / EVIDENCE).resolve() == outside.resolve()
    assert (root / EVIDENCE).with_name("previous-evidence").is_dir()


@pytest.mark.parametrize("filename", memlab_server.DATASET_FILES)
@pytest.mark.parametrize("tool", ["orgmemory_validate", "orgmemory_audit"])
@requires_confinement
def test_dataset_symlink_swap_cannot_escape_root(root, monkeypatch, filename, tool):
    original = memlab_server.Dataset.load

    def swap_then_load(directory):
        target = directory / filename
        target.unlink()
        target.symlink_to(root.parent / "outside.md")
        return original(directory)

    monkeypatch.setattr(memlab_server.Dataset, "load", swap_then_load)
    result = call(create_server(root), tool, dataset=DATASET)
    assert SECRET not in error(result)


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@requires_confinement
def test_orgmemory_audit_untrusted_text_is_data_in_both_formats(root, mode):
    from markdown_it import MarkdownIt
    from memcore import locate_quote

    directory = root / "untrusted"
    directory.mkdir()
    payload = 'x` <img src=x onerror=alert(1)> [forged](https://evil.invalid) **FORGED**'
    quote = payload + '\n### HIGH forged\x1b[2J\u202e'
    (directory / "note.txt").write_text(quote, encoding="utf-8")
    span = locate_quote(directory, "note.txt", quote)
    entry = dict(id=payload, path=span.path, sha256=span.sha256, start=span.start, end=span.end,
                 quote=quote, source_uri=payload, source_kind="note", captured_at="2026-10-01T00:00:00Z")
    (directory / "evidence.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    policy = dict(actors={payload: ["reviewer"]}, permissions={
        action: ["reviewer"] for action in ("request", "approve", "reject", "raise_risk")})
    (directory / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    events = [dict(id=payload + str(day), timestamp=f"2026-10-0{day}T00:00:00Z", actor=payload,
                   action=action, subject=payload, evidence_ids=[payload], note=quote)
              for day, action in enumerate(("request", "approve", "raise_risk"), 1)]
    (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    before = snapshot(root)
    args = dict(dataset="untrusted", as_of="2026-10-31T00:00:00Z")
    markdown_result, json_result = session(create_server(root), ("orgmemory_audit", args),
                                         ("orgmemory_audit", {**args, "format": "json"}), mode=mode)
    markdown = ok(markdown_result)
    report = json.loads(ok(json_result))
    assert report["findings"][0]["subject"] == payload
    assert report["findings"][0]["events"][0]["evidence"][0]["quote"] == quote
    assert "\x1b" not in markdown and "\u202e" not in markdown and "\n### HIGH forged" not in markdown
    findings = markdown.split("## Findings\n", 1)[1].split("## Notes", 1)[0]
    tokens = MarkdownIt().parse(findings)
    inline = [child for token in tokens for child in (token.children or [])]
    assert not {t.type for t in inline} & {"html_inline", "link_open", "image", "strong_open", "em_open"}
    assert snapshot(root) == before


@requires_confinement
def test_orgmemory_audit_evidence_swap_cannot_escape_root(root, monkeypatch):
    from memcore import evidence as evidence_module

    original = evidence_module.read_file_bytes
    replaced = False
    leaked = []

    def swap_then_read(path):
        nonlocal replaced
        if not replaced:
            path.unlink()
            path.symlink_to(root.parent / "outside.md")
            replaced = True
            # A later error that omits the secret would still hide a successful read.
            data = original(path)
            leaked.append(data)
            return data
        return original(path)

    monkeypatch.setattr(evidence_module, "read_file_bytes", swap_then_read)
    result = call(create_server(root), "orgmemory_audit", dataset=DATASET)
    assert replaced and not leaked and SECRET not in error(result)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs require POSIX")
@requires_confinement
def test_evidence_scan_does_not_open_special_files(root, monkeypatch):
    pipe = root / EVIDENCE / "pipe.txt"
    os.mkfifo(pipe)
    original = Path.read_bytes

    def guarded(path):
        assert path != pipe, "opening this FIFO would hang the tool"
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    report = json.loads(ok(call(create_server(root), "memfalsify_audit", memory_file=MEMORY,
                                evidence_dir=EVIDENCE, format="json")))
    assert {"path": "pipe.txt", "reason": "not a regular file"} in report["skipped_evidence"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs require POSIX")
@requires_confinement
def test_dataset_evidence_does_not_open_special_files(root, monkeypatch):
    pipe = root / DATASET / "pipe.txt"
    os.mkfifo(pipe)
    manifest = root / DATASET / "evidence.jsonl"
    records = [json.loads(line) for line in manifest.read_text().splitlines()]
    records[0]["path"] = "pipe.txt"
    manifest.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    original = Path.read_bytes

    def guarded(path):
        assert path != pipe, "opening this FIFO would hang the tool"
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    message = error(call(create_server(root), "orgmemory_validate", dataset=DATASET))
    assert "invalid evidence" in message


# --- verifiers -------------------------------------------------------------------------------

@pytest.mark.parametrize("verifier", ["llm", "jev", "dual", "cascade"])
@requires_confinement
def test_model_verifiers_refused_without_the_flag(root, monkeypatch, verifier):
    def no_models(*args, **kwargs):
        raise AssertionError("no verifier may be built when model verifiers are disabled")

    monkeypatch.setattr(memlab_server, "build_verifier", no_models)
    message = error(call(create_server(root), "memfalsify_audit", memory_file=MEMORY, evidence_dir=EVIDENCE,
                         verifier=verifier))
    assert "--allow-model-verifiers" in message and f"verifier {verifier!r} is disabled" in message


@pytest.mark.parametrize("verifier", ["llm", "jev", "dual", "cascade"])
@requires_confinement
def test_model_verifiers_allowed_with_the_flag(root, monkeypatch, verifier):
    built = []

    class Offline(LexicalVerifier):
        name = "stand-in"

    def fake_build(choice, llm_model, jev_model, accept_at=0.8):
        built.append(choice.value)
        return Offline()

    monkeypatch.setattr(memlab_server, "build_verifier", fake_build)
    server = create_server(root, allow_model_verifiers=True)
    report = json.loads(ok(call(server, "memfalsify_audit", memory_file=MEMORY, evidence_dir=EVIDENCE,
                                verifier=verifier, format="json")))
    assert built == [verifier] and report["verifier"] == "stand-in"

    async def annotations():
        async with Client(server, mode="legacy") as client:
            return {t.name: t.annotations.open_world_hint for t in (await client.list_tools()).tools}
    assert anyio.run(annotations)["memfalsify_audit"] is True


@requires_confinement
def test_missing_model_sdk_is_a_tool_error(root, monkeypatch):
    def missing(*args, **kwargs):
        raise memlab_server.VerifierError("the LLM verifier needs the anthropic package")

    monkeypatch.setattr(memlab_server, "build_verifier", missing)
    message = error(call(create_server(root, allow_model_verifiers=True), "memfalsify_audit",
                         memory_file=MEMORY, verifier="llm"))
    assert "model verifier failed" in message and "SDK installation" in message


# --- bad input -------------------------------------------------------------------------------

@requires_confinement
def test_rejected_paths_are_not_reflected_in_errors(root):
    outside = str(root.parent / "outside.md")
    for tool, arguments in [
        ("memfalsify_claims", {"memory_file": outside}),
        ("memfalsify_audit", {"memory_file": MEMORY, "evidence_dir": str(root.parent)}),
        ("memfalsify_audit", {"memory_file": MEMORY, "evidence_dir": EVIDENCE, "reviews_file": outside}),
        ("orgmemory_validate", {"dataset": str(root.parent)}),
        # SDK validation must not echo the whole rejected arguments dict either.
        ("memfalsify_claims", {"memory_file": {"path": outside}}),
    ]:
        message = error(call(create_server(root), tool, **arguments))
        assert str(root.parent) not in message


@pytest.mark.parametrize("stage", ["build", "judge"])
@requires_confinement
def test_model_failures_do_not_expose_backend_diagnostics(root, monkeypatch, caplog, stage):
    diagnostic = "synthetic-backend-diagnostic /outside/private-profile"

    def fail(*args, **kwargs):
        raise memlab_server.VerifierError(diagnostic)

    class FailedJudge(LexicalVerifier):
        judge = fail

    monkeypatch.setattr(memlab_server, "build_verifier", fail if stage == "build" else lambda *a: FailedJudge())
    message = error(call(create_server(root, allow_model_verifiers=True), "memfalsify_audit",
                         memory_file=MEMORY, evidence_dir=EVIDENCE, verifier="llm"))
    assert "model verifier failed" in message
    assert diagnostic not in message and diagnostic not in caplog.text


@requires_confinement
def test_unexpected_failures_do_not_log_tracebacks(root, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-private-diagnostic /outside/private-profile")

    monkeypatch.setattr(memlab_server, "parse_memory", fail)
    message = error(call(create_server(root), "memfalsify_claims", memory_file=MEMORY))
    assert "synthetic-private-diagnostic" not in message + caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@requires_confinement
def test_earliest_reference_date_does_not_overflow(root, caplog):
    result = call(create_server(root), "memfalsify_audit", memory_file=MEMORY,
                  as_of="0001-01-01", format="json")
    assert json.loads(ok(result))["as_of"] == "0001-01-01"
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("field", ["evidence_dir", "reviews_file"])
@requires_confinement
def test_empty_optional_paths_are_not_treated_as_omitted(root, field):
    message = error(call(create_server(root), "memfalsify_audit", memory_file=MEMORY, **{field: ""}))
    assert f"{field} is empty" in message

@requires_confinement
def test_bad_input_is_a_clear_tool_error(root):
    server = create_server(root)
    (root / "broken").mkdir()
    for name in ("policy.json", "evidence.jsonl"):
        shutil.copy(root / DATASET / name, root / "broken" / name)
    (root / "broken" / "events.jsonl").write_text('{"id": "x"\n', encoding="utf-8")
    cases = [
        ("memfalsify_claims", dict(memory_file="missing.md"), "does not exist"),
        ("memfalsify_claims", dict(memory_file="memfalsify"), "is not a file"),
        ("memfalsify_claims", dict(memory_file=""), "memory_file is empty"),
        ("memfalsify_audit", dict(memory_file=MEMORY, evidence_dir=MEMORY), "is not a directory"),
        ("memfalsify_audit", dict(memory_file=MEMORY, reviews_file=REVIEWS), "requires evidence_dir"),
        ("memfalsify_audit", dict(memory_file=MEMORY, as_of="last week"), "as_of must be a date"),
        ("memfalsify_audit", dict(memory_file=MEMORY, format="html"), "format"),
        ("memfalsify_audit", dict(memory_file=MEMORY, verifier="gpt"), "verifier"),
        ("orgmemory_replay", dict(dataset=DATASET, as_of="2026-10-06"), "with a timezone"),
        ("orgmemory_validate", dict(dataset="memfalsify"), "has no events.jsonl"),
        ("orgmemory_validate", dict(dataset="broken"), "broken/events.jsonl:1: invalid JSON"),
        ("orgmemory_explain", dict(dataset=DATASET), "subject"),
        ("orgmemory_audit", dict(dataset=DATASET, stale_days=-1), "invalid arguments; check: stale_days"),
        ("orgmemory_audit", dict(dataset=DATASET, as_of="2026-10-31"), "with a timezone"),
        ("orgmemory_audit", dict(dataset="broken"), "broken/events.jsonl:1: invalid JSON"),
    ]
    for name, arguments, expected in cases:
        message = error(call(server, name, **arguments))
        assert expected in message, (name, arguments, message)
        assert str(root.resolve()) not in message


# --- read-only -------------------------------------------------------------------------------

@requires_confinement
def test_no_tool_writes_anything(root):
    before = snapshot(root.parent)
    server = create_server(root)
    results = session(
        server,
        ("memfalsify_claims", {"memory_file": MEMORY}),
        ("memfalsify_audit", {"memory_file": MEMORY, "evidence_dir": EVIDENCE, "reviews_file": REVIEWS}),
        ("memfalsify_audit", {"memory_file": MEMORY, "evidence_dir": EVIDENCE, "format": "json"}),
        ("memfalsify_audit", {"memory_file": MEMORY, "verifier": "llm"}),
        ("orgmemory_validate", {"dataset": DATASET}),
        ("orgmemory_replay", {"dataset": DATASET}),
        ("orgmemory_replay", {"dataset": DATASET, "format": "json", "as_of": "2026-10-07T23:59:59Z"}),
        ("orgmemory_explain", {"dataset": DATASET, "subject": "Feature A"}),
        ("orgmemory_audit", {"dataset": DATASET}),
        ("orgmemory_audit", {"dataset": DATASET, "format": "json", "as_of": "2026-10-31T00:00:00Z"}),
        ("orgmemory_append", {"dataset": DATASET, "event": "{}"}),
    )
    assert [r.is_error for r in results] == [False, False, False, True, False, False, False, False, False, False,
                                             True]
    assert snapshot(root.parent) == before


# --- memory in git ---------------------------------------------------------------------------

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
GIT_MEMORY = "notes/MEMORY.md"
GIT_BASE = """# Memory

## Infra

- The cache uses Redis.
- The main database runs PostgreSQL 15.

## Process

- We use squash merges for pull requests.
- Feature B ships in Q3.
"""
GIT_HEAD = GIT_BASE.replace("PostgreSQL 15", "PostgreSQL 16").replace("squash", "rebase").replace(
    "Feature B ships in Q3.", "The public API rate limit is 100 requests per minute.")
GIT_EVIDENCE = {"evidence/api.md": "The public API rate limit is 60 requests per minute.\n",
                "evidence/db.md": "The main database runs PostgreSQL 16.\n"}
HOSTILE_SUBJECT = "tidy memory \x1b[2J‮ ### FORGED"


@pytest.fixture
def git_root(git_repo):
    """A server root that is a git work tree: two commits of the memory, the head version uncommitted."""
    git_repo.commit("first memory", {GIT_MEMORY: GIT_BASE, **GIT_EVIDENCE})
    git_repo.commit(HOSTILE_SUBJECT, {GIT_MEMORY: GIT_BASE + "- Deploys need a ticket.\n"})
    git_repo.write({GIT_MEMORY: GIT_HEAD})
    return git_repo


def _memfalsify(*args: str) -> str:
    """`memfalsify ARGS` in process, from the current directory; stdout only."""
    from typer.testing import CliRunner
    from memfalsify.cli import app as memfalsify_app

    result = CliRunner().invoke(memfalsify_app, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


@requires_git
@requires_confinement
def test_git_tools_match_the_cli(git_root, monkeypatch):
    from memfalsify.memory import claim_id

    root = git_root.root
    monkeypatch.chdir(root)  # the CLI runs from the server root, with the same relative paths
    before = snapshot(root.parent)
    rebase = claim_id("We use rebase merges for pull requests.")
    diff_args = {"memory_file": GIT_MEMORY, "base": "HEAD~1", "evidence_dir": "evidence", "as_of": "2026-10-10"}
    diff, diff_json, committed, history, history_json, patch, same = session(
        create_server(root),
        ("memfalsify_diff", diff_args),
        ("memfalsify_diff", {**diff_args, "format": "json"}),
        ("memfalsify_diff", {**diff_args, "head": "HEAD", "exclude": ["db.md"]}),
        ("memfalsify_history", {"memory_file": GIT_MEMORY}),
        ("memfalsify_history", {"memory_file": GIT_MEMORY, "max_commits": 1, "format": "json"}),
        ("memfalsify_rollback", {"memory_file": GIT_MEMORY, "claim": rebase, "to": "HEAD~1"}),
        ("memfalsify_rollback", {"memory_file": GIT_MEMORY, "claim": claim_id("The cache uses Redis."),
                                 "to": "HEAD"}),
    )
    cli_diff = ["diff", GIT_MEMORY, "--base", "HEAD~1", "--evidence", "evidence", "--as-of", "2026-10-10"]
    _same_as_cli(_memfalsify(*cli_diff), ok(diff))
    _same_as_cli(_memfalsify(*cli_diff, "--format", "json"), ok(diff_json))
    _same_as_cli(_memfalsify(*cli_diff, "--head", "HEAD", "--exclude", "db.md"), ok(committed))
    _same_as_cli(_memfalsify("history", GIT_MEMORY), ok(history))
    _same_as_cli(_memfalsify("history", GIT_MEMORY, "--max-commits", "1", "--format", "json"), ok(history_json))
    assert ok(patch) == _memfalsify("rollback", GIT_MEMORY, "--claim", rebase, "--to", "HEAD~1")

    report = json.loads(ok(diff_json))
    assert report["summary"] == {"added": 1, "changed": 2, "removed": 1, "unchanged": 1}
    assert (report["memory_file"], report["audit"]["memory_file"]) == (GIT_MEMORY, GIT_MEMORY)
    assert report["audit"]["evidence_root"] == "evidence" and str(root) not in ok(diff) + ok(diff_json)
    assert json.loads(ok(history_json))["commits_scanned"] == 1
    assert "\x1b" not in ok(history) and "‮" not in ok(history) and "\n### FORGED" not in ok(history)
    assert ok(patch).startswith("# MemFalsify rollback patch") and "+- We use squash merges" in ok(patch)
    assert "reads the same" in ok(same) and "nothing to roll back" in ok(same)
    assert snapshot(root.parent) == before  # nothing written, the patch included


@requires_git
@requires_confinement
def test_git_tool_errors_are_clear_and_contained(git_root, tmp_path):
    root = git_root.root
    (root / "plain").mkdir()
    server = create_server(root)
    cases = [
        ("memfalsify_diff", dict(memory_file=GIT_MEMORY, base="-x"), "invalid revision '-x'"),
        ("memfalsify_diff", dict(memory_file=GIT_MEMORY, base="--output=/tmp/x"), "must not start with '-'"),
        ("memfalsify_diff", dict(memory_file=GIT_MEMORY, base="HEAD", head="no-such-branch"), "is not a commit"),
        ("memfalsify_diff", dict(memory_file=GIT_MEMORY, base="HEAD\x1b"), "control characters"),
        ("memfalsify_diff", dict(memory_file=GIT_MEMORY, base=""), "invalid revision"),
        ("memfalsify_diff", dict(memory_file=GIT_MEMORY, base="HEAD", exclude=["*.md"]), "requires evidence_dir"),
        ("memfalsify_diff", dict(memory_file=GIT_MEMORY, base="HEAD~1", evidence_dir="evidence", exclude=["*"]),
         "no evidence passages are left after exclude"),
        ("memfalsify_diff", dict(memory_file=GIT_MEMORY, base="HEAD", reviews_file=GIT_MEMORY),
         "requires evidence_dir"),
        ("memfalsify_diff", dict(memory_file=GIT_MEMORY), "invalid arguments; check: base"),
        ("memfalsify_history", dict(memory_file=GIT_MEMORY, max_commits=0), "invalid arguments; check: max_commits"),
        ("memfalsify_rollback", dict(memory_file=GIT_MEMORY, claim="cache", to="HEAD"), "a claim ID looks like"),
        ("memfalsify_rollback", dict(memory_file=GIT_MEMORY, claim="m-00000000", to="HEAD"),
         "is not a claim of the memory"),
        ("memfalsify_rollback", dict(memory_file=GIT_MEMORY, claim="m-00000000", to="-c"), "invalid revision"),
        ("memfalsify_audit", dict(memory_file=GIT_MEMORY, exclude=["*.md"]), "requires evidence_dir"),
    ]
    results = session(server, *[(name, arguments) for name, arguments, _ in cases])
    for (name, arguments, expected), result in zip(cases, results):
        message = error(result)
        assert expected in message, (name, arguments, message)
        assert str(root) not in message and str(tmp_path) not in message


@requires_git
@requires_confinement
def test_git_tools_refuse_a_memory_outside_any_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    root = tmp_path / "plain"
    root.mkdir()
    (root / "MEMORY.md").write_text("- The cache uses Redis.\n", encoding="utf-8")
    for name in GIT_TOOLS:
        message = error(call(create_server(root), name, memory_file="MEMORY.md", **REQUIRED.get(name, {})))
        # git's own diagnostics (which can name directories outside the root) are not passed on.
        assert message.endswith(": MEMORY.md is not inside a git work tree"), message


def _repository(git_repo, path: Path, *init: str):
    """A repository at ``path``, created with ``git init INIT`` in the git_repo fixture's environment."""
    from conftest import GitRepo

    path.mkdir(parents=True, exist_ok=True)
    created = GitRepo(path, git_repo.hooks)
    created.git("init", "-q", *init)
    created.git("config", "--local", "user.name", "Outside Author")
    created.git("config", "--local", "user.email", "outside@example.com")
    return created


def _git_escapes(git_repo, tmp_path) -> dict[str, Path]:
    """Server roots whose memory is in a git repository that would make git read outside the root."""
    roots = {}
    # 1. A .git file pointing at a git directory outside the root.
    separate = _repository(git_repo, tmp_path / "separate" / "root",
                                   "--separate-git-dir", str(tmp_path / "separate" / "outside.git"))
    separate.commit("secret", {"MEMORY.md": f"- {SECRET}.\n"})
    separate.write({"MEMORY.md": "- Local claim.\n"})  # the secret is now only in the outside git directory
    roots["separate git dir"] = separate.root
    # 2. A worktree inside the root of a repository outside it.
    main = _repository(git_repo, tmp_path / "worktree" / "main")
    main.commit("secret", {"MEMORY.md": f"- {SECRET}.\n"})
    main.git("worktree", "add", "-q", str(tmp_path / "worktree" / "root" / "wt"))
    (tmp_path / "worktree" / "root" / "wt" / "MEMORY.md").write_text("- Local claim.\n", encoding="utf-8")
    roots["worktree of an outside repository"] = tmp_path / "worktree" / "root" / "wt"
    # 3. A root inside a larger repository (the work tree's top level is outside the root).
    roots["subdirectory of a repository"] = main.root / "sub"
    (main.root / "sub").mkdir()
    (main.root / "sub" / "MEMORY.md").write_text("- Local claim.\n", encoding="utf-8")
    # 4. Objects borrowed from an outside repository.
    borrower = _repository(git_repo, tmp_path / "alternates" / "root")
    borrower.commit("memory", {"MEMORY.md": "- Local claim.\n"})
    (borrower.root / ".git" / "objects" / "info" / "alternates").write_text(
        str(main.root / ".git" / "objects") + "\n", encoding="utf-8")
    roots["alternates"] = borrower.root
    # 5. The object store, or something in it, is a symlink.
    for name, target in [("objects symlink", "objects"), ("symlink in objects", "objects/pack/outside")]:
        linked = _repository(git_repo, tmp_path / name / "root")
        linked.commit("memory", {"MEMORY.md": "- Local claim.\n"})
        store = linked.root / ".git" / "objects"
        if target == "objects":
            shutil.move(str(store), str(tmp_path / name / "objects"))
            store.symlink_to(tmp_path / name / "objects", target_is_directory=True)
        else:
            (linked.root / ".git" / target).symlink_to(main.root / ".git" / "objects", target_is_directory=True)
        roots[name] = linked.root
    return roots


@requires_git
@requires_confinement
def test_git_tools_refuse_repositories_that_leave_the_root(git_repo, tmp_path):
    roots = _git_escapes(git_repo, tmp_path)
    expected = {"alternates": "alternates", "symlink in objects": "contains symlinks"}
    for case, root in roots.items():
        memory = "MEMORY.md"
        before = snapshot(tmp_path)
        server = create_server(root)
        for name in GIT_TOOLS:
            message = error(call(server, name, memory_file=memory, **REQUIRED.get(name, {})))
            assert expected.get(case, "not inside the server root") in message, (case, name, message)
            assert SECRET not in message and str(tmp_path) not in message, (case, name, message)
        # Files in the root are still readable by the other tools.
        assert "Local claim" in ok(call(server, "memfalsify_claims", memory_file=memory))
        assert snapshot(tmp_path) == before


@requires_git
@requires_confinement
def test_git_location_variables_cannot_redirect_the_server(git_root, git_repo, tmp_path, monkeypatch):
    outside = _repository(git_repo, tmp_path / "elsewhere")
    outside.commit("secret", {GIT_MEMORY: f"- {SECRET}.\n"})
    monkeypatch.setenv("GIT_DIR", str(outside.root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outside.root))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(outside.root / ".git" / "objects"))
    history = ok(call(create_server(git_root.root), "memfalsify_history", memory_file=GIT_MEMORY))
    assert SECRET not in history and "first memory" in history


@requires_git
@requires_confinement
def test_git_tools_accept_a_worktree_inside_the_root(git_root):
    git_root.commit("head memory")
    git_root.git("worktree", "add", "-q", str(git_root.root / "wt"), "HEAD~1")
    report = json.loads(ok(call(create_server(git_root.root), "memfalsify_diff", memory_file="wt/" + GIT_MEMORY,
                                base="main", format="json")))
    assert report["summary"]["changed"] == 2 and report["memory_file"] == "wt/" + GIT_MEMORY


@pytest.mark.parametrize("verifier", ["llm", "jev", "dual", "cascade"])
@requires_git
@requires_confinement
def test_diff_model_verifiers_follow_the_flag(git_root, monkeypatch, verifier):
    built = []

    class Offline(LexicalVerifier):
        name = "stand-in"

    def fake_build(choice, llm_model, jev_model, accept_at=0.8):
        built.append(choice.value)
        return Offline()

    monkeypatch.setattr(memlab_server, "build_verifier", fake_build)
    arguments = dict(memory_file=GIT_MEMORY, base="HEAD~1", evidence_dir="evidence", verifier=verifier)
    message = error(call(create_server(git_root.root), "memfalsify_diff", **arguments))
    assert f"verifier {verifier!r} is disabled" in message and built == []
    report = json.loads(ok(call(create_server(git_root.root, allow_model_verifiers=True), "memfalsify_diff",
                                format="json", **arguments)))
    assert built == [verifier] and report["audit"]["verifier"] == "stand-in"


@requires_confinement
def test_audit_exclude_matches_the_cli(root):
    patterns = ["media/*", "notes/**"]
    report = json.loads(ok(call(create_server(root), "memfalsify_audit", memory_file=MEMORY, evidence_dir=EVIDENCE,
                                exclude=patterns, as_of="2026-10-10", format="json")))
    direct = audit(root / MEMORY, root / EVIDENCE, as_of=date(2026, 10, 10), exclude=patterns)
    assert report["excluded"] == patterns and report["summary"] == direct.summary
    assert not any(e["span"]["path"].startswith(("media/", "notes/"))
                   for f in report["findings"] for e in f["evidence"])


# --- transcript and OCR sidecars -------------------------------------------------------------------

MEDIA_EXAMPLE = "media-example"


@pytest.fixture
def media_root(root):
    shutil.copytree(EXAMPLES / "memfalsify-media", root / MEDIA_EXAMPLE)
    return root


def media_call(server, **extra):
    return call(server, "memfalsify_audit", memory_file=f"{MEDIA_EXAMPLE}/MEMORY.md",
                evidence_dir=f"{MEDIA_EXAMPLE}/evidence", as_of="2026-10-10", **extra)


@requires_confinement
def test_sidecar_evidence_matches_the_library(media_root):
    base = media_root / MEDIA_EXAMPLE
    server = create_server(media_root)
    report = json.loads(ok(media_call(server, reviews_file=f"{MEDIA_EXAMPLE}/reviews.jsonl", format="json")))
    direct = audit(base / "MEMORY.md", base / "evidence", base / "reviews.jsonl", as_of=date(2026, 10, 10))
    assert report["findings"] == json.loads(direct.model_dump_json())["findings"]
    assert report["skipped_evidence"] == [s.model_dump() for s in direct.skipped_evidence]
    media = [e["span"]["media"] for f in report["findings"] for e in f["evidence"]]
    assert {"kind": "transcript", "path": "recordings/standup-2026-10-06.mp4", "locator": "t=12.5,17",
            "sha256": hashlib.sha256((base / "evidence/recordings/standup-2026-10-06.mp4").read_bytes()).hexdigest()
            } in media
    assert "`recordings/standup-2026-10-06.mp4#t=12.5,17` (00:00:12.500-00:00:17.000)" in ok(media_call(server))


@requires_confinement
def test_a_media_symlink_out_of_the_root_is_never_hashed(media_root):
    media = media_root / MEDIA_EXAMPLE / "evidence" / "scans" / "whiteboard.png"
    outside = media_root.parent / "outside.png"
    outside.write_bytes(media.read_bytes())  # would even match the recorded hash
    media.unlink()
    media.symlink_to(outside)
    report = json.loads(ok(media_call(create_server(media_root), format="json")))
    reasons = {s["path"]: s["reason"] for s in report["skipped_evidence"]}
    assert reasons["scans/whiteboard.png"] == "symlink escapes evidence root"
    assert "whiteboard.png is a symlink that escapes the evidence root" in reasons["scans/whiteboard.png.ocr.json"]
    assert not any(e["span"]["path"].startswith("scans/") for f in report["findings"] for e in f["evidence"])


@requires_confinement
def test_media_swapped_for_an_outward_symlink_before_hashing_cannot_be_read(media_root, monkeypatch):
    from memcore import evidence as memcore_evidence
    original, opened, leaked = memcore_evidence.file_sha256, [], []

    def swap_then_hash(path):
        opened.append(path)
        path.unlink()
        path.symlink_to(media_root.parent / "outside.md")
        # A successful hash is a read, even when the tool later returns an error that omits the secret.
        digest = original(path)
        leaked.append(digest)
        return digest

    monkeypatch.setattr(memcore_evidence, "file_sha256", swap_then_hash)
    result = media_call(create_server(media_root), format="json")
    assert opened and not leaked and SECRET not in error(result)


# Windows asyncio creates a loopback socketpair even for stdio, which the
# no-network fixture rejects. These tests also require the POSIX-only server.
# --- the real server over stdio ----------------------------------------------------------------

@pytest.mark.parametrize("allow_models", [False, True])
@requires_confinement
def test_stdio_round_trip(root, tmp_path, allow_models):
    sources = [REPO / "packages" / name / "src" for name in ("memcore", "memfalsify", "orgmemory", "memlab-mcp")]
    args = ["-m", "memlab_mcp", "--root", str(root)]
    if allow_models:
        args.append("--allow-model-verifiers")
    params = StdioServerParameters(command=sys.executable, args=args,
                                   env={"PYTHONPATH": os.pathsep.join(map(str, sources))}, cwd=str(tmp_path))
    log = tmp_path / "server.log"

    async def main():
        with anyio.fail_after(60), log.open("w", encoding="utf-8") as errlog:
            async with Client(stdio_client(params, errlog=errlog), mode="legacy") as client:
                tools = (await client.list_tools()).tools
                return client.server_info, tools, await client.call_tool(
                    "orgmemory_explain", {"dataset": DATASET, "subject": "Feature A"})

    info, tools, result = anyio.run(main)
    assert info.name == "memlab" and {t.name for t in tools} == TOOLS, log.read_text()
    open_world = {t.name: t.annotations.open_world_hint for t in tools}
    assert open_world["memfalsify_audit"] is open_world["memfalsify_diff"] is allow_models
    assert not any(open_world[name] for name in TOOLS - {"memfalsify_audit", "memfalsify_diff"})
    assert "ev-wed-reject" in ok(result)


@requires_confinement
def test_stdio_security_and_invalid_input(root, tmp_path):
    """Every tool rejects bad inputs without reads outside root, writes or noisy stderr."""
    outside = str(root.parent / "outside.md")
    memory_escapes = escapes(root)
    (root / "evidence-alias").symlink_to(root / EVIDENCE, target_is_directory=True)
    (root / EVIDENCE / "external-dir").symlink_to(root.parent, target_is_directory=True)
    (root / EVIDENCE / "external.md").symlink_to(outside)
    (root / "index.md").write_text(
        f"- [absolute]({outside})\n- [parent](../outside.md)\n- [symlink](link.md)\n- Local safe claim.\n")
    (root / "bad.md").write_bytes(b"\xff\xfe")
    (root / "bad-reviews.jsonl").write_text('{"incomplete":\n')
    (root / "escape-reviews.jsonl").write_text(json.dumps({
        "memory_id": parse_memory(root / MEMORY)[0][0].id, "relation": "supports", "path": outside,
        "quote": SECRET, "reviewer": "offline-test",
    }) + "\n")
    for filename in memlab_server.DATASET_FILES:
        directory = root / f"symlink-{filename}"
        shutil.copytree(root / DATASET, directory)
        (directory / filename).unlink()
        (directory / filename).symlink_to(outside)
    for variant, replacement in [("bad-events", "events.jsonl"), ("bad-policy", "policy.json"),
                                 ("bad-manifest", "evidence.jsonl"), ("escape-manifest", None)]:
        directory = root / variant
        shutil.copytree(root / DATASET, directory)
        if replacement:
            (directory / replacement).write_text('{"incomplete":\n')
        else:
            manifest = directory / "evidence.jsonl"
            records = [json.loads(line) for line in manifest.read_text().splitlines()]
            records[0]["path"] = outside
            manifest.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    cases = []
    for tool in sorted(TOOLS):
        cases.append((tool, {}, "invalid arguments"))
        field = "memory_file" if tool.startswith("memfalsify") else "dataset"
        base = REQUIRED.get(tool, {})
        for value in [None, 17, [], {"path": outside}]:
            cases.append((tool, {**base, field: value}, "invalid arguments"))
        for value in ["", "   ", "missing", "nul\x00path"]:
            cases.append((tool, {**base, field: value}, field))
        for value in memory_escapes:
            cases.append((tool, {**base, field: value}, "outside the server root"))
        if tool.startswith("orgmemory"):
            for variant in ["bad-events", "bad-policy", "bad-manifest", "escape-manifest"]:
                cases.append((tool, {**base, "dataset": variant}, "invalid"))
            for filename in memlab_server.DATASET_FILES:
                cases.append((tool, {**base, "dataset": f"symlink-{filename}"}, "symlink"))

    for field in ["evidence_dir", "reviews_file"]:
        for value in memory_escapes + ["linkdir"]:
            cases.append(("memfalsify_audit", {"memory_file": MEMORY, "evidence_dir": EVIDENCE,
                                               field: value}, "outside the server root"))
        cases.append(("memfalsify_audit", {"memory_file": MEMORY, field: ""}, f"{field} is empty"))
    for verifier in ["llm", "jev", "dual", "cascade"]:
        cases.append(("memfalsify_audit", {"memory_file": MEMORY, "evidence_dir": EVIDENCE,
                                           "verifier": verifier}, "--allow-model-verifiers"))
    cases.extend([
        ("memfalsify_claims", {"memory_file": "bad.md"}, "invalid"),
        ("memfalsify_audit", {"memory_file": MEMORY, "format": "html"}, "format"),
        ("memfalsify_audit", {"memory_file": MEMORY, "verifier": "gpt"}, "verifier"),
        ("memfalsify_audit", {"memory_file": MEMORY, "as_of": "yesterday"}, "as_of"),
        ("memfalsify_audit", {"memory_file": MEMORY, "reviews_file": REVIEWS}, "requires evidence_dir"),
        ("memfalsify_audit", {"memory_file": MEMORY, "evidence_dir": EVIDENCE,
                               "reviews_file": "bad-reviews.jsonl"}, "invalid JSON"),
        ("memfalsify_audit", {"memory_file": MEMORY, "evidence_dir": EVIDENCE,
                               "reviews_file": "escape-reviews.jsonl"}, "invalid evidence"),
        ("orgmemory_replay", {"dataset": DATASET, "format": "html"}, "format"),
        ("orgmemory_replay", {"dataset": DATASET, "as_of": "2026-10-06"}, "timezone"),
        ("orgmemory_explain", {"dataset": DATASET, "subject": "Feature A", "as_of": "tomorrow"}, "as_of"),
        ("orgmemory_explain", {"dataset": DATASET, "subject": "Feature Z"}, "unknown subject"),
        ("orgmemory_audit", {"dataset": DATASET, "stale_days": -1}, "stale_days"),
        ("orgmemory_audit", {"dataset": DATASET, "stale_days": "soon"}, "stale_days"),
        ("orgmemory_audit", {"dataset": DATASET, "as_of": "2026-10-31"}, "timezone"),
        ("orgmemory_audit", {"dataset": DATASET, "format": "html"}, "format"),
        ("orgmemory_append", {"dataset": DATASET, "event": "{}"}, "unknown tool"),
        ("orgmemory_evidence_add", {"dataset": DATASET}, "unknown tool"),
    ])
    before = snapshot(root.parent)
    params = StdioServerParameters(command=sys.executable, args=["-m", "memlab_mcp", "--root", str(root)],
                                   cwd=str(REPO))
    log = tmp_path / "security-server.log"

    async def main():
        with anyio.fail_after(45), log.open("w", encoding="utf-8") as errlog:
            async with Client(stdio_client(params, errlog=errlog), mode="legacy") as client:
                failures = [await client.call_tool(name, arguments) for name, arguments, _ in cases]
                # The same connection remains usable after all the failures.
                successes = [
                    await client.call_tool("memfalsify_claims", {"memory_file": "index.md"}),
                    await client.call_tool("memfalsify_audit", {"memory_file": MEMORY,
                                                               "evidence_dir": "evidence-alias", "format": "json"}),
                    await client.call_tool("memfalsify_audit", {"memory_file": MEMORY,
                                                               "as_of": "0001-01-01", "format": "json"}),
                    await client.call_tool("orgmemory_validate", {"dataset": str(root / DATASET)}),
                ]
                return failures, successes

    failures, successes = anyio.run(main)
    for (tool, arguments, expected), result in zip(cases, failures):
        message = error(result)
        assert expected in message, (tool, arguments, message)
        assert str(root.parent) not in message and SECRET not in message
    assert all(not result.is_error for result in successes)
    assert SECRET not in "".join(ok(result) for result in successes)
    assert json.loads(ok(successes[2]))["as_of"] == "0001-01-01"
    stderr = log.read_text()
    assert "Traceback" not in stderr and outside not in stderr and SECRET not in stderr
    # Only the test's stderr capture file is new, outside the server's root.
    after = snapshot(root.parent)
    after.pop(log)
    assert after == before


def _script(name: str) -> str:
    directory = Path(sys.executable).parent
    if os.name == "nt":
        name += ".exe"
        if directory.name.lower() != "scripts":
            directory /= "Scripts"
    path = directory / name
    assert path.is_file(), f"{name} is not installed in {directory}"
    return str(path)


def _cli(name: str, *args: str) -> str:
    completed = subprocess.run([sys.executable, "-m", name, *args], cwd=EXAMPLES, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _same_as_cli(cli_stdout: str, tool_text: str) -> None:
    """The CLI prints the tool text. Typer's echo adds one newline, except where the command disables it."""
    assert cli_stdout == tool_text or cli_stdout == tool_text + "\n"


@requires_confinement
def test_stdio_examples_end_to_end(tmp_path):
    """`memlab-mcp --root examples` over stdio: initialize, tools/list, then tools/call.

    Each tool's text is the same document as the CLI for the same relative paths.
    `memfalsify claims` prints JSONL; the tool returns those objects as one JSON array.
    """
    before = snapshot(EXAMPLES)
    log = tmp_path / "server.log"
    params = StdioServerParameters(command=_script("memlab-mcp"), args=["--root", "examples"], cwd=str(REPO))
    as_of = "2026-10-31T00:00:00Z"
    audit_args = {"memory_file": "memfalsify/MEMORY.md", "evidence_dir": "memfalsify/evidence",
                  "reviews_file": "memfalsify/reviews.jsonl", "as_of": "2026-10-10"}

    async def main():
        with anyio.fail_after(60), log.open("w", encoding="utf-8") as errlog:
            async with Client(stdio_client(params, errlog=errlog), mode="legacy") as client:
                # Client mode="legacy" runs initialize before this block can list or call.
                info, protocol, instructions = client.server_info, client.protocol_version, client.instructions
                listed = (await client.list_tools()).tools
                validate = await client.call_tool("orgmemory_validate", {"dataset": "orgmemory/feature-a"})
                outputs = {
                    "claims": await client.call_tool("memfalsify_claims", {"memory_file": "memfalsify/MEMORY.md"}),
                    "audit": await client.call_tool("memfalsify_audit", audit_args),
                    "audit_json": await client.call_tool("memfalsify_audit", {**audit_args, "format": "json"}),
                    "replay": await client.call_tool("orgmemory_replay", {"dataset": "orgmemory/feature-a"}),
                    "replay_json": await client.call_tool(
                        "orgmemory_replay", {"dataset": "orgmemory/feature-a", "format": "json"}),
                    "explain": await client.call_tool(
                        "orgmemory_explain", {"dataset": "orgmemory/feature-a", "subject": "Feature A"}),
                    "audit_org": await client.call_tool(
                        "orgmemory_audit", {"dataset": "orgmemory/github-acme-web", "as_of": as_of}),
                    "audit_org_json": await client.call_tool(
                        "orgmemory_audit", {"dataset": "orgmemory/github-acme-web", "as_of": as_of,
                                            "stale_days": 20, "format": "json"}),
                }
                return info, protocol, instructions, listed, validate, outputs

    info, protocol, instructions, listed, validate, outputs = anyio.run(main)
    assert info is not None and info.name == "memlab" and protocol, log.read_text()
    assert instructions and str(EXAMPLES.resolve()) in instructions
    assert {t.name for t in listed} == TOOLS, log.read_text()
    assert ok(validate).startswith("ok: ")

    claims = json.loads(ok(outputs["claims"]))
    cli_claims = [json.loads(line) for line in _cli("memfalsify", "claims", "memfalsify/MEMORY.md").splitlines()]
    assert claims == cli_claims
    _same_as_cli(_cli("memfalsify", "audit", "memfalsify/MEMORY.md", "--evidence", "memfalsify/evidence",
                      "--reviews", "memfalsify/reviews.jsonl", "--as-of", "2026-10-10"), ok(outputs["audit"]))
    _same_as_cli(_cli("memfalsify", "audit", "memfalsify/MEMORY.md", "--evidence", "memfalsify/evidence",
                      "--reviews", "memfalsify/reviews.jsonl", "--as-of", "2026-10-10", "--format", "json"),
                 ok(outputs["audit_json"]))
    _same_as_cli(_cli("orgmemory", "validate", "orgmemory/feature-a"), ok(validate))
    _same_as_cli(_cli("orgmemory", "replay", "orgmemory/feature-a"), ok(outputs["replay"]))
    _same_as_cli(_cli("orgmemory", "replay", "orgmemory/feature-a", "--format", "json"), ok(outputs["replay_json"]))
    _same_as_cli(_cli("orgmemory", "explain", "orgmemory/feature-a", "Feature A"), ok(outputs["explain"]))
    _same_as_cli(_cli("orgmemory", "audit", "orgmemory/github-acme-web", "--as-of", as_of), ok(outputs["audit_org"]))
    _same_as_cli(_cli("orgmemory", "audit", "orgmemory/github-acme-web", "--as-of", as_of, "--stale-days", "20",
                      "--format", "json"), ok(outputs["audit_org_json"]))
    assert snapshot(EXAMPLES) == before
