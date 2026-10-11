"""The MCP server, exercised through the SDK's own client: in memory, and once over real stdio."""
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("mcp")
import anyio  # noqa: E402  (an mcp dependency)
from mcp import Client, StdioServerParameters, stdio_client  # noqa: E402

from conftest import EXAMPLES, snapshot  # noqa: E402
from memfalsify.audit import audit  # noqa: E402
from memfalsify.memory import parse_memory  # noqa: E402
from memfalsify.verify import LexicalVerifier  # noqa: E402
from memlab_mcp import server as memlab_server  # noqa: E402
from memlab_mcp.server import create_server  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TOOLS = {"memfalsify_claims", "memfalsify_audit", "orgmemory_validate", "orgmemory_replay", "orgmemory_explain"}
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

@pytest.mark.parametrize("mode", ["legacy", "auto"])
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
        assert "untrusted" in tool.description
    assert ok(result).startswith("ok: ")


def test_claims_match_the_library(root):
    claims = json.loads(ok(call(create_server(root), "memfalsify_claims", memory_file=MEMORY)))
    expected, _ = parse_memory(root / MEMORY)
    assert claims == [{"memory_id": c.id, "location": c.location, "text": c.text} for c in expected]


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


def test_audit_without_evidence_and_absolute_path_inside_root(root):
    report = json.loads(ok(call(create_server(root), "memfalsify_audit",
                                memory_file=str(root / MEMORY), format="json")))
    assert report["evidence_root"] is None
    assert {f["status"] for f in report["findings"]} == {"insufficient_evidence"}


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


# --- path safety ---------------------------------------------------------------------------

def escapes(root):
    """Paths that name ../outside.md (or its directory) without staying inside the root."""
    (root / "link.md").symlink_to(root.parent / "outside.md")
    (root / "linkdir").symlink_to(root.parent, target_is_directory=True)
    return ["../outside.md", "memfalsify/../../outside.md", str(root.parent / "outside.md"), "link.md",
            "linkdir/outside.md"]


@pytest.mark.parametrize("index", range(5))
def test_memory_paths_cannot_escape_the_root(root, index):
    server, path = create_server(root), escapes(root)[index]
    for tool in ("memfalsify_claims", "memfalsify_audit"):
        message = error(call(server, tool, memory_file=path))
        assert "outside the server root" in message and SECRET not in message


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
    ]
    for name, arguments in cases:
        message = error(call(server, name, **arguments))
        assert "outside the server root" in message, (name, arguments)
    message = error(call(server, "orgmemory_validate", dataset="evil-dataset"))
    assert "events.jsonl is a symlink that leaves the server root" in message and SECRET not in message


def test_evidence_symlinks_out_of_the_root_are_skipped_not_read(root):
    (root / EVIDENCE / "leak.md").symlink_to(root.parent / "outside.md")
    report = json.loads(ok(call(create_server(root), "memfalsify_audit", memory_file=MEMORY,
                                evidence_dir=EVIDENCE, format="json")))
    assert {"path": "leak.md", "reason": "symlink escapes evidence root"} in report["skipped_evidence"]
    assert SECRET not in json.dumps(report)


# --- verifiers -------------------------------------------------------------------------------

@pytest.mark.parametrize("verifier", ["llm", "jev", "dual", "cascade"])
def test_model_verifiers_refused_without_the_flag(root, monkeypatch, verifier):
    def no_models(*args, **kwargs):
        raise AssertionError("no verifier may be built when model verifiers are disabled")

    monkeypatch.setattr(memlab_server, "build_verifier", no_models)
    message = error(call(create_server(root), "memfalsify_audit", memory_file=MEMORY, evidence_dir=EVIDENCE,
                         verifier=verifier))
    assert "--allow-model-verifiers" in message and f"verifier {verifier!r} is disabled" in message


def test_model_verifiers_allowed_with_the_flag(root, monkeypatch):
    built = []

    class Offline(LexicalVerifier):
        name = "stand-in"

    def fake_build(choice, llm_model, jev_model, accept_at=0.8):
        built.append(choice.value)
        return Offline()

    monkeypatch.setattr(memlab_server, "build_verifier", fake_build)
    server = create_server(root, allow_model_verifiers=True)
    report = json.loads(ok(call(server, "memfalsify_audit", memory_file=MEMORY, evidence_dir=EVIDENCE,
                                verifier="dual", format="json")))
    assert built == ["dual"] and report["verifier"] == "stand-in"

    async def annotations():
        async with Client(server, mode="legacy") as client:
            return {t.name: t.annotations.open_world_hint for t in (await client.list_tools()).tools}
    assert anyio.run(annotations)["memfalsify_audit"] is True


def test_missing_model_sdk_is_a_tool_error(root, monkeypatch):
    def missing(*args, **kwargs):
        raise memlab_server.VerifierError("the LLM verifier needs the anthropic package")

    monkeypatch.setattr(memlab_server, "build_verifier", missing)
    message = error(call(create_server(root, allow_model_verifiers=True), "memfalsify_audit",
                         memory_file=MEMORY, verifier="llm"))
    assert "needs the anthropic package" in message


# --- bad input -------------------------------------------------------------------------------

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
    ]
    for name, arguments, expected in cases:
        message = error(call(server, name, **arguments))
        assert expected in message, (name, arguments, message)
        assert str(root.resolve()) not in message


# --- read-only -------------------------------------------------------------------------------

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
        ("orgmemory_append", {"dataset": DATASET, "event": "{}"}),
    )
    assert [r.is_error for r in results] == [False, False, False, True, False, False, False, False, True]
    assert snapshot(root.parent) == before


# --- the real server over stdio ----------------------------------------------------------------

def test_stdio_round_trip(root, tmp_path):
    sources = [REPO / "packages" / name / "src" for name in ("memcore", "memfalsify", "orgmemory", "memlab-mcp")]
    params = StdioServerParameters(command=sys.executable, args=["-m", "memlab_mcp", "--root", str(root)],
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
    assert "ev-wed-reject" in ok(result)
