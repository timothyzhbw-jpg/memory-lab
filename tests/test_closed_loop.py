"""examples/closed-loop: the whole loop from scratch with the real CLIs, and the conversion in to_events.py.

The demo's own comparison with expected/ covers the wording of every report. The tests below also state, from the
JSON reports, what must stay true whatever the wording: who is named, what was ignored, and what stayed read-only.
"""
import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from conftest import EXAMPLES, snapshot, write_text_exact
from memfalsify.audit import SEVERITY
from orgmemory.models import DECISIONS

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

ROOT = EXAMPLES.parent
DEMO = EXAMPLES / "closed-loop"
FILES = ["1-memfalsify-audit.md", "2-memfalsify-history.md", "3-orgmemory-audit.md", "4-orgmemory-explain.md",
         "5-orgmemory-explain-resolved.md", "6-memfalsify-audit-corrected.md", "7-orgmemory-explain-corrected.md",
         "8-orgmemory-audit-final.md", "events.jsonl", "evidence.jsonl"]
PACKAGE_PATHS = os.pathsep.join(str(ROOT / "packages" / name / "src") for name in ("memcore", "memfalsify", "orgmemory"))


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"closed_loop_{name}", DEMO / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_demo = load("run_demo")
to_events = load("to_events")


def child_env(**extra: str) -> dict[str, str]:
    """pytest.ini's pythonpath does not reach a child process, and the tests also run without an install."""
    return {**os.environ, "PYTHONPATH": PACKAGE_PATHS, **extra}


def cli(name: str, *args: str, cwd: Path) -> bytes:
    proc = subprocess.run([sys.executable, "-m", name, *args], cwd=cwd, env=child_env(), capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout


@pytest.fixture(scope="module")
def loop(tmp_path_factory):
    """One run of the demo, as the README runs it. Another hash default and time zone must not change a byte."""
    work = tmp_path_factory.mktemp("closed-loop") / "work"
    proc = subprocess.run([sys.executable, str(DEMO / "run_demo.py"), "--work", str(work)],
                          env=child_env(GIT_DEFAULT_HASH="sha256", TZ="Asia/Shanghai"), capture_output=True)
    return proc, work


def org_json(work: Path, *args: str) -> dict:
    return json.loads(cli("orgmemory", *args[:1], str(work / "dataset"), *args[1:], "--format", "json", cwd=work))


def memory_id(work: Path, text: str, name: str = "audit-first.json") -> str:
    report = json.loads((work / "reports" / name).read_bytes())
    return next(f["memory_id"] for f in report["findings"] if f["text"] == text)


# --- the whole loop ------------------------------------------------------------------------------------------

def test_the_loop_runs_from_scratch_and_matches_the_committed_reports(loop):
    proc, work = loop
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace") + proc.stdout.decode("utf-8", "replace")
    assert f"All {len(FILES)} files match expected/.".encode() in proc.stdout
    assert sorted(path.name for path in (DEMO / "expected").iterdir()) == sorted(FILES)
    produced = {name: work / ("dataset" if name.endswith(".jsonl") else "reports") / name for name in FILES}
    for name, path in produced.items():
        assert path.read_bytes() == (DEMO / "expected" / name).read_bytes(), name


def test_history_and_audit_name_each_line(loop):
    _, work = loop
    audit = json.loads((work / "reports" / "audit-first.json").read_bytes())
    history = json.loads((work / "reports" / "history-first.json").read_bytes())
    assert {f["text"]: f["status"] for f in audit["findings"]} == {
        run_demo.REFUNDS: "candidate_support", run_demo.DATABASE: "candidate_counterevidence",
        run_demo.RATE_LIMIT: "candidate_counterevidence", run_demo.EXPORT: "insufficient_evidence"}
    assert {c["text"]: c["introduced"]["author"] for c in history["claims"]} == {
        run_demo.REFUNDS: "lin", run_demo.DATABASE: "lin", run_demo.RATE_LIMIT: "agent-7", run_demo.EXPORT: "agent-7"}


def test_the_audit_says_the_approval_came_from_someone_without_the_permission(loop):
    _, work = loop
    report = org_json(work, "audit", "--as-of", run_demo.FIRST_CUTOFF)
    rate_limit = memory_id(work, run_demo.RATE_LIMIT)
    export = memory_id(work, run_demo.EXPORT)
    attempts = {f["actor"]: f for f in report["findings"] if f["kind"] == "unauthorized_attempts"}
    assert set(attempts) == {"wu", "agent-7"}
    assert attempts["wu"]["subjects"] == [f"memory:{rate_limit}"]
    assert attempts["agent-7"]["subjects"] == [f"memory:{export}"]
    [event] = attempts["wu"]["events"]
    assert (event["action"], event["outcome"], event["roles"]) == ("approve", "unauthorized", ["account_manager"])
    # What the finding cites says who proposed and who was named: the memory line and the commit that landed it.
    quotes = [span["quote"] for span in event["evidence"]]
    assert quotes[0] == run_demo.RATE_LIMIT and "agent-7" in quotes[1] and "(approved-by: wu)" in quotes[1]
    # The other counterevidence is on a line zhou approved: the same finding, a different next question.
    [risk] = [f for f in report["findings"] if f["kind"] == "risk_after_approval"]
    assert risk["subject"] == f"memory:{memory_id(work, run_demo.DATABASE)}" and risk["severity"] == "high"
    assert [e["actor"] for e in risk["events"]] == ["zhou", "memfalsify"]


def test_a_decision_and_a_correction_close_the_loop(loop):
    _, work = loop
    before = org_json(work, "replay", "--as-of", run_demo.FIRST_CUTOFF)["states"]
    after = org_json(work, "replay")["states"]
    rate_limit = f"memory:{memory_id(work, run_demo.RATE_LIMIT)}"
    corrected = f"memory:{memory_id(work, run_demo.CORRECTED_RATE_LIMIT, 'audit-second.json')}"
    export = f"memory:{memory_id(work, run_demo.EXPORT)}"
    assert before[rate_limit]["current_status"] == "proposed"
    assert before[rate_limit]["pending_approval"] == ["memory_steward"]
    assert [r["actor"] for r in before[rate_limit]["risks"]] == ["memfalsify"]
    assert after[rate_limit]["current_status"] == "rejected"
    assert after[rate_limit]["last_authorized_decision"]["actor"] == "zhou"
    assert after[corrected]["current_status"] == "approved"
    assert after[export]["current_status"] == "proposed"
    events = [json.loads(line) for line in (work / "dataset" / "events.jsonl").read_bytes().splitlines()]
    [write] = [e for e in events if e["id"] == f"write-request-{corrected.removeprefix('memory:')}"]
    assert f"(edits {rate_limit.removeprefix('memory:')})" in write["note"]


def test_the_reports_leave_the_memory_and_the_log_as_they_are(loop):
    _, work = loop
    repo, dataset = work / "repo", work / "dataset"

    def files() -> dict:
        return {**{p: data for p, data in snapshot(repo).items() if ".git" not in p.parts}, **snapshot(dataset)}

    before = files()
    subject = f"memory:{memory_id(work, run_demo.RATE_LIMIT)}"
    cli("memfalsify", "audit", "MEMORY.md", "--evidence", "docs", cwd=repo)
    cli("memfalsify", "history", "MEMORY.md", cwd=repo)
    for args in (("replay", str(dataset)), ("audit", str(dataset)), ("explain", str(dataset), subject),
                 ("validate", str(dataset))):
        cli("orgmemory", *args, cwd=work)
    assert files() == before


def test_converting_the_same_reports_again_adds_nothing(loop, capfdbinary):
    _, work = loop
    before = snapshot(work / "dataset")
    args = [str(work / "reports" / name) for name in ("audit-second.json", "history-second.json", "history-second.md")]
    assert to_events.main([*args, "--repo", str(work / "repo"), "--dataset", str(work / "dataset")]) == 0
    assert capfdbinary.readouterr().out == b""
    assert snapshot(work / "dataset") == before


def test_cli_stdout_with_crlf_is_normalized_before_it_is_hashed():
    assert run_demo.lf(b"a\r\nb\nc\rd\r\n") == b"a\nb\nc\rd\n"


def test_the_work_directory_goes_through_no_symlink(tmp_path, make_symlink):
    """`orgmemory append` refuses a path through a symlink, and the default temporary directory on macOS is one."""
    (tmp_path / "real").mkdir()
    make_symlink(tmp_path / "link", tmp_path / "real", directory=True)
    assert run_demo.work_directory(tmp_path / "link" / "run") == (tmp_path / "real" / "run").resolve()
    default = run_demo.work_directory(None)
    assert default == default.resolve() and not default.exists()
    shutil.rmtree(default.parent)


# --- hostile text --------------------------------------------------------------------------------------------

def assert_inert(output: bytes) -> None:
    for line in output.decode("utf-8").split("\n"):
        assert not line.startswith(("::", "##[", "# forged")), line
        assert all(unicodedata.category(char) not in ("Cc", "Cf", "Zl", "Zp") for char in line), repr(line)


def test_hostile_commits_and_memory_lines_stay_inert_all_the_way_through(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", PACKAGE_PATHS)
    demo = run_demo.Demo(tmp_path / "work")
    shutil.copyfile(DEMO / "policy.json", demo.dataset / "policy.json")
    write_text_exact(demo.dataset / "events.jsonl", "")
    line = "::warning::The cache\x1b[2J stores \u202esessions in Redis 6."
    demo.commit("evil\x1b]0;owned\x07 author", "2026-10-05T09:00:00+00:00",
                "::error::forged\x1b[31m \u202esubject (approved-by: zhou\x1b[0m)",
                {"MEMORY.md": f"# Memory\n\n- {line}\n", "docs/cache.md": "The cache stores sessions in Redis 7.\n"})
    paths = demo.verify("2026-10-10", "hostile")
    records = demo.record(paths)
    assert_inert(paths["history_md"].read_bytes())
    assert_inert(demo.tool("memfalsify", "audit", "MEMORY.md", "--evidence", "docs", "--as-of", "2026-10-10"))
    audit = json.loads(paths["audit"].read_bytes())
    subject = f"memory:{audit['findings'][0]['memory_id']}"
    assert_inert(demo.tool("orgmemory", "audit", str(demo.dataset), "--as-of", "2026-10-11T00:00:00+00:00"))
    assert_inert(demo.tool("orgmemory", "explain", str(demo.dataset), subject))
    assert_inert(demo.tool("orgmemory", "replay", str(demo.dataset), "--as-of", "2026-10-11T00:00:00+00:00"))
    # A subject whose approver is not cleanly at its end names no approver: the author stays responsible.
    approvals = [r["event"] for r in records if r["kind"] == "event" and r["event"]["action"] == "approve"]
    assert [a["actor"] for a in approvals] == [c["introduced"]["author"]
                                               for c in json.loads(paths["history"].read_bytes())["claims"]]
    assert all(a["actor"] != "zhou" for a in approvals)
    # The author holds no role, so the log records the write and ignores it.
    state = json.loads(demo.tool("orgmemory", "replay", str(demo.dataset), "--format", "json"))["states"][subject]
    assert state["current_status"] == "proposed" and state["pending_approval"] == []


def test_the_converter_prints_ascii_json_only(tmp_path, capfdbinary):
    repo, dataset = tmp_path / "repo", tmp_path / "dataset"
    repo.mkdir(), dataset.mkdir()
    text = "::error::The c\u00e4che\x1b[2J uses \u202eRedis."
    write_text_exact(repo / "MEMORY.md", f"- {text}\n")
    audit, history = make_reports(findings=[finding("m-00000000", "insufficient_evidence", text)], claims=[])
    arguments = save_reports(tmp_path, audit, history, "")
    assert to_events.main([*arguments, "--repo", str(repo), "--dataset", str(dataset)]) == 0
    output = capfdbinary.readouterr().out
    assert output.isascii() and b"\x1b" not in output and output.count(b"\n") == 2
    assert all(line.startswith(b'{"kind": ') for line in output.splitlines())


# --- the conversion, one rule at a time ----------------------------------------------------------------------

def finding(memory: str, status: str, text: str = "The cache uses Redis.", evidence=(), location="MEMORY.md:1") -> dict:
    return {"memory_id": memory, "text": text, "location": location, "status": status, "evidence": list(evidence)}


def span_entry(relation: str = "contradicts", reviewer: str | None = None, data: bytes = b"The cache uses Valkey.\n",
               path: str = "cache.md") -> dict:
    quote = data.decode("utf-8").strip()
    return {"relation": relation, "confirmed": reviewer is not None, "reviewer": reviewer,
            "span": {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "start": 0, "end": len(quote),
                     "quote": quote}}


def make_reports(findings=(), claims=()) -> tuple[dict, dict]:
    return ({"tool": "memfalsify", "as_of": "2026-10-10", "memory_file": "MEMORY.md", "evidence_root": "docs",
             "findings": list(findings)},
            {"tool": "memfalsify", "head": "a" * 40, "claims": list(claims)})


def save_reports(root: Path, audit: dict, history: dict, report: str) -> list[str]:
    for name, data in (("audit.json", json.dumps(audit)), ("history.json", json.dumps(history)), ("history.md", report)):
        write_text_exact(root / name, data)
    return [str(root / name) for name in ("audit.json", "history.json", "history.md")]


def claim(memory: str, author: str | None = "lin", subject: str = "Remember the cache", commit: str | None = "b" * 40,
          text: str = "The cache uses Redis.") -> dict:
    return {"memory_id": memory, "text": text, "location": "MEMORY.md:1", "replaced": None,
            "introduced": {"commit": commit, "date": "2026-10-05T09:00:00+00:00", "author": author, "subject": subject}}


def history_report(*memory_ids: str) -> str:
    return "".join(f"### `{m}`\n\n> The cache uses Redis.\n\n- Introduced: `bbbbbbbbbbbb` 2026-10-05 · lin · Remember\n"
                   f"- Last changed: same as introduced\n\n" for m in memory_ids)


def convert(tmp_path: Path, findings=(), claims=(), report: str = "") -> list[dict]:
    repo, dataset = tmp_path / "repo", tmp_path / "dataset"
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    dataset.mkdir(exist_ok=True)
    write_text_exact(repo / "MEMORY.md", "- The cache uses Redis.\n")
    write_text_exact(repo / "docs" / "cache.md", "The cache uses Valkey.\n")
    audit, history = make_reports(findings, claims)
    return to_events.Converter(audit, history, report, repo, dataset).convert()


def events_of(records: list[dict]) -> list[dict]:
    return [r["event"] for r in records if r["kind"] == "event"]


# Every status MemFalsify has: the event it becomes (None: no event), and who stands behind that event.
STATUS_EVENTS = {
    "challenged": ("raise_risk", "lin", [span_entry(reviewer="lin")]),
    "conflicted": ("raise_risk", "lin", [span_entry("supports", reviewer="lin"), span_entry(reviewer="lin")]),
    "candidate_counterevidence": ("raise_risk", "memfalsify", [span_entry()]),
    "disputed": ("raise_risk", "memfalsify", [span_entry("disputed")]),
    "insufficient_evidence": ("comment", "memfalsify", []),
    "candidate_support": (None, None, [span_entry("supports")]),
    "supported": (None, None, [span_entry("supports", reviewer="lin")]),
}


def test_every_memfalsify_status_has_a_rule_and_the_doc_lists_it():
    assert sorted(STATUS_EVENTS) == sorted(SEVERITY)
    for name in ("architecture.md", "architecture.zh-CN.md"):
        document = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert all(f"`{status}`" in document for status in SEVERITY), name


@pytest.mark.parametrize("status", sorted(STATUS_EVENTS))
def test_a_finding_becomes_an_event_and_never_a_decision(tmp_path, status):
    action, actor, evidence = STATUS_EVENTS[status]
    events = events_of(convert(tmp_path, findings=[finding("m-00000001", status, evidence=evidence)]))
    if action is None:
        assert events == []
        return
    [event] = events
    assert (event["action"], event["actor"], event["subject"]) == (action, actor, "memory:m-00000001")
    assert event["action"] not in DECISIONS
    assert status in event["note"] and "MEMORY.md:1" in event["note"] and "The cache uses Redis." in event["note"]
    assert event["timestamp"] == "2026-10-10T23:59:59+00:00"


def test_an_evidence_record_keeps_the_span_of_the_finding(tmp_path):
    entry = span_entry()
    records = convert(tmp_path, findings=[finding("m-00000001", "candidate_counterevidence", evidence=[entry])])
    [evidence] = [r for r in records if r["kind"] == "evidence"]
    assert evidence["quote"] == entry["span"]["quote"]
    assert evidence["path"] == "sources/aaaaaaaaaaaa/docs/cache.md"
    assert evidence["source_uri"] == "git:aaaaaaaaaaaa:docs/cache.md"
    assert (tmp_path / "dataset" / evidence["path"]).read_bytes() == (tmp_path / "repo" / "docs" / "cache.md").read_bytes()
    assert events_of(records)[0]["evidence_ids"] == [evidence["id"]]


@pytest.mark.parametrize(("subject", "approver"), [
    ("Remember the cache (approved-by: zhou)", "zhou"),
    ("Remember the cache", "lin"),
    ("Remember (approved-by: zhou) the cache", "lin"),
    ("Remember the cache (approved-by: zhou\\u001b)", "lin"),
    ("Remember the cache (approved-by: zhou) ", "lin"),
])
def test_only_a_clean_name_at_the_end_of_the_subject_is_an_approver(tmp_path, subject, approver):
    records = convert(tmp_path, claims=[claim("m-00000001", subject=subject)], report=history_report("m-00000001"))
    [request, approve] = events_of(records)
    assert (request["action"], request["actor"]) == ("request", "lin")
    assert (approve["action"], approve["actor"]) == ("approve", approver)
    assert request["timestamp"] == approve["timestamp"] == "2026-10-05T09:00:00+00:00"
    assert request["evidence_ids"] == approve["evidence_ids"] == ["m-00000001-line", "m-00000001-introduced"]


@pytest.mark.parametrize("introduced", [None, {"commit": None, "date": None, "author": None, "subject": "uncommitted"}])
def test_a_line_with_no_commit_to_name_gets_no_write_events(tmp_path, introduced):
    entry = {**claim("m-00000001"), "introduced": introduced}
    records = convert(tmp_path, claims=[entry], findings=[finding("m-00000001", "insufficient_evidence")])
    assert [e["action"] for e in events_of(records)] == ["comment"]


def test_markup_in_a_memory_line_is_quoted_with_the_whole_line(tmp_path):
    records = convert(tmp_path, findings=[finding("m-00000001", "insufficient_evidence", text="The cache uses Valkey.")])
    assert [r["quote"] for r in records if r["kind"] == "evidence"] == ["- The cache uses Redis."]


def test_a_line_from_a_linked_file_in_a_subdirectory_is_found(tmp_path):
    repo, dataset = tmp_path / "repo", tmp_path / "dataset"
    (repo / "agent").mkdir(parents=True)
    dataset.mkdir()
    write_text_exact(repo / "agent" / "notes.md", "# Notes\n\n- The cache uses Redis.\n")
    audit, history = make_reports([finding("m-00000001", "insufficient_evidence", location="notes.md:3")])
    audit["memory_file"] = "agent/MEMORY.md"
    [evidence, _] = to_events.Converter(audit, history, "", repo, dataset).convert()
    assert evidence["path"] == "sources/aaaaaaaaaaaa/agent/notes.md"
    assert evidence["source_uri"] == "git:aaaaaaaaaaaa:agent/notes.md"
    assert evidence["quote"] == "The cache uses Redis."


def test_records_already_in_the_dataset_are_left_out(tmp_path):
    finding_ = finding("m-00000001", "insufficient_evidence")
    first = convert(tmp_path, findings=[finding_])
    assert [r["kind"] for r in first] == ["evidence", "event"]
    dataset = tmp_path / "dataset"
    write_text_exact(dataset / "evidence.jsonl", json.dumps({"id": first[0]["id"]}) + "\n")
    write_text_exact(dataset / "events.jsonl", json.dumps({"id": first[1]["event"]["id"]}) + "\n")
    assert convert(tmp_path, findings=[finding_]) == []


def bad_input_cases():
    secret = "\x1b[2Jm-hostile"
    yield "memory id", {"findings": [finding(secret, "insufficient_evidence")]}
    yield "history id", {"claims": [claim(secret)]}
    yield "memory path", {"findings": [finding("m-00000001", "insufficient_evidence", location=f"../{secret}:1")]}
    yield "absolute memory path", {"findings": [finding("m-00000001", "insufficient_evidence", location=f"/{secret}:1")]}
    yield "windows path", {"findings": [finding("m-00000001", "insufficient_evidence", location=f"C:\\{secret}:1")]}
    yield "line number", {"findings": [finding("m-00000001", "insufficient_evidence", location="MEMORY.md:99")]}
    yield "evidence path", {"findings": [finding("m-00000001", "candidate_counterevidence", evidence=[
        span_entry(path=f"../../{secret}")])]}
    yield "changed evidence", {"findings": [finding("m-00000001", "candidate_counterevidence", evidence=[
        span_entry(data=f"Not what is on disk {secret}.\n".encode())])]}
    yield "NUL in a quote", {"findings": [finding("m-00000001", "candidate_counterevidence", evidence=[
        {**span_entry(), "span": {**span_entry()["span"], "quote": f"a\0{secret}"}}])]}
    yield "no introduced line", {"claims": [claim("m-00000001", subject=secret)], "report": secret}


@pytest.mark.parametrize(("name", "case"), list(bad_input_cases()), ids=[name for name, _ in bad_input_cases()])
def test_bad_input_stops_the_conversion_and_the_message_holds_none_of_it(tmp_path, capfdbinary, name, case):
    repo, dataset = tmp_path / "repo", tmp_path / "dataset"
    (repo / "docs").mkdir(parents=True)
    dataset.mkdir()
    write_text_exact(repo / "MEMORY.md", "- The cache uses Redis.\n")
    write_text_exact(repo / "docs" / "cache.md", "The cache uses Valkey.\n")
    audit, history = make_reports(case.get("findings", ()), case.get("claims", ()))
    arguments = save_reports(tmp_path, audit, history, case.get("report", ""))
    assert to_events.main([*arguments, "--repo", str(repo), "--dataset", str(dataset)]) == 2
    captured = capfdbinary.readouterr()
    assert captured.out == b"" and captured.err.startswith(b"error: ")
    assert b"hostile" not in captured.err and b"\x1b" not in captured.err


def test_a_symlink_out_of_the_repository_is_refused(tmp_path, make_symlink):
    (tmp_path / "repo" / "docs").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    write_text_exact(outside, "The cache uses Valkey.\n")
    make_symlink(tmp_path / "repo" / "docs" / "link.md", outside)
    (tmp_path / "dataset").mkdir()
    entry = span_entry(path="link.md")
    audit, history = make_reports([finding("m-00000001", "candidate_counterevidence", evidence=[entry])])
    with pytest.raises(to_events.ConversionError, match="outside the repository"):
        to_events.Converter(audit, history, "", tmp_path / "repo", tmp_path / "dataset").convert()


def test_a_cited_file_that_changed_under_the_same_head_is_refused(tmp_path):
    findings = [finding("m-00000001", "insufficient_evidence")]
    convert(tmp_path, findings=findings)
    stored = tmp_path / "dataset" / "sources" / "aaaaaaaaaaaa" / "MEMORY.md"
    write_text_exact(stored, "- Some other memory.\n")
    with pytest.raises(to_events.ConversionError, match="changed without a new commit"):
        convert(tmp_path, findings=findings)


def test_reports_from_another_tool_are_refused(tmp_path):
    (tmp_path / "repo").mkdir(), (tmp_path / "dataset").mkdir()
    audit, history = make_reports()
    for bad_audit, bad_history in (({**audit, "tool": "orgmemory"}, history), (audit, {**history, "head": None})):
        with pytest.raises(to_events.ConversionError):
            to_events.Converter(bad_audit, bad_history, "", tmp_path / "repo", tmp_path / "dataset")


# --- what the docs promise -----------------------------------------------------------------------------------

def test_the_scripts_use_only_the_standard_library(tmp_path):
    for name in ("to_events.py", "run_demo.py"):
        tree = ast.parse((DEMO / name).read_bytes())
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert imported <= set(sys.stdlib_module_names) | {"__future__"}, name
    # -I -S: no site-packages and no PYTHONPATH.
    proc = subprocess.run([sys.executable, "-I", "-S", str(DEMO / "to_events.py"), "--help"], capture_output=True)
    assert proc.returncode == 0 and b"--dataset" in proc.stdout


def test_neither_tool_imports_the_other():
    for package, other in (("memfalsify", "orgmemory"), ("orgmemory", "memfalsify")):
        for path in (ROOT / "packages" / package / "src" / package).glob("*.py"):
            tree = ast.parse(path.read_bytes())
            imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                        for alias in node.names}
            imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                         if isinstance(node, ast.ImportFrom) and node.module and not node.level}
            assert other not in imported, path


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"^```(?:bash|powershell)\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)


def test_both_languages_give_the_same_commands():
    english = (DEMO / "README.md").read_text(encoding="utf-8")
    chinese = (DEMO / "README.zh-CN.md").read_text(encoding="utf-8")
    blocks = _bash_blocks(english)
    assert blocks == _bash_blocks(chinese)
    assert "examples/closed-loop/run_demo.py" in blocks[0] and "packages/orgmemory" in blocks[0]
    assert len(blocks[0].strip().splitlines()) == 1  # one command from a fresh checkout


def test_the_architecture_doc_opens_with_one_sentence_in_both_languages():
    for name, end in (("architecture.md", "."), ("architecture.zh-CN.md", "。")):
        lines = [line for line in (ROOT / "docs" / name).read_text(encoding="utf-8").split("\n") if line.strip()]
        assert lines[0] == "# Architecture" or lines[0].startswith("# ")
        opening = next(line for line in lines[1:] if line.startswith("**"))
        assert opening.endswith(f"{end}**") and opening.count(end) == 1


@pytest.mark.parametrize("document", ["architecture.md", "architecture.zh-CN.md"])
def test_the_worked_example_shows_real_events(document):
    logged = (DEMO / "expected" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    text = (ROOT / "docs" / document).read_text(encoding="utf-8")
    shown = [line for line in text.splitlines() if line.startswith('{"id": ')]
    assert len(shown) == 3 and all(line in logged for line in shown)


@pytest.mark.parametrize("document", ["docs/architecture.md", "docs/architecture.zh-CN.md",
                                      "examples/closed-loop/README.md", "examples/closed-loop/README.zh-CN.md"])
def test_relative_links_resolve(document):
    path = ROOT / document
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", path.read_text(encoding="utf-8")):
        if "://" not in target:
            assert (path.parent / target).exists(), target
