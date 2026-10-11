"""The MCP server, exercised through the SDK's own client: in memory, and over real stdio."""
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
TOOLS = {"memfalsify_claims", "memfalsify_audit", "orgmemory_validate", "orgmemory_replay", "orgmemory_explain",
         "orgmemory_audit"}
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
@requires_confinement
def test_dataset_symlink_swap_cannot_escape_root(root, monkeypatch, filename):
    original = memlab_server.Dataset.load

    def swap_then_load(directory):
        target = directory / filename
        target.unlink()
        target.symlink_to(root.parent / "outside.md")
        return original(directory)

    monkeypatch.setattr(memlab_server.Dataset, "load", swap_then_load)
    result = call(create_server(root), "orgmemory_validate", dataset=DATASET)
    assert SECRET not in error(result)


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
    assert {t.name: t.annotations.open_world_hint for t in tools}["memfalsify_audit"] is allow_models
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
        base = {"subject": "Feature A"} if tool == "orgmemory_explain" else {}
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
