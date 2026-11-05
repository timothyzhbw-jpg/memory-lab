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

import windows_newlines
from conftest import EXAMPLES, snapshot, write_text_exact
from memfalsify import memory as memory_module
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


@pytest.fixture(autouse=True)
def no_git_location(monkeypatch):
    """to_events.py runs git with the environment it is given, and a git hook running pytest sets these."""
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)


def child_env(**extra: str) -> dict[str, str]:
    """pytest.ini's pythonpath does not reach a child process, and the tests also run without an install."""
    return {**os.environ, "PYTHONPATH": PACKAGE_PATHS, **extra}


def cli(name: str, *args: str, cwd: Path) -> bytes:
    proc = subprocess.run([sys.executable, "-m", name, *args], cwd=cwd, env=child_env(), capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout


# What stdout and stderr do in a child process on Windows, which --simulate-windows-newlines leaves out of reach.
WINDOWS_STREAMS = """import io, sys
for name in ("stdout", "stderr"):
    setattr(sys, name, io.TextIOWrapper(getattr(sys, name).buffer, encoding="utf-8", newline="\\r\\n", write_through=True))
"""


@pytest.fixture(scope="module")
def loop(tmp_path_factory, request):
    """One run of the demo, as the README runs it. Another hash default and time zone must not change a byte,
    and with --simulate-windows-newlines neither must CRLF on the stdout of every child process."""
    root = tmp_path_factory.mktemp("closed-loop")
    env = child_env(GIT_DEFAULT_HASH="sha256", TZ="Asia/Shanghai")
    if request.config.getoption(windows_newlines.OPTION):
        write_text_exact(root / "sitecustomize.py", WINDOWS_STREAMS)
        env["PYTHONPATH"] = os.pathsep.join((str(root), PACKAGE_PATHS))
    work = root / "work"
    proc = subprocess.run([sys.executable, str(DEMO / "run_demo.py"), "--work", str(work)], env=env, capture_output=True)
    return proc, work


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """The demo's own repository, real commands and policy, with an empty log: for a story other than the demo's."""
    monkeypatch.setenv("PYTHONPATH", PACKAGE_PATHS)
    created = run_demo.Demo(tmp_path / "work")
    shutil.copyfile(DEMO / "policy.json", created.dataset / "policy.json")
    write_text_exact(created.dataset / "events.jsonl", "")
    return created


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
    [write] = [e for e in events if e["id"].startswith(f"write-request-{corrected.removeprefix('memory:')}-")]
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
    """The expected reports should not depend on a link above the work directory, like macOS's default temp."""
    (tmp_path / "real").mkdir()
    make_symlink(tmp_path / "link", tmp_path / "real", directory=True)
    assert run_demo.work_directory(tmp_path / "link" / "run") == (tmp_path / "real" / "run").resolve()
    default = run_demo.work_directory(None)
    assert default == default.resolve() and not default.exists()
    shutil.rmtree(default.parent)


# --- stories the demo does not tell, through the real CLIs ------------------------------------------------------

CACHE = "The cache uses Redis 6."
CACHE_DOCS = {"docs/cache.md": "# Cache\n\nThe cache uses Redis 7.\n"}


def state_of(demo, memory: str) -> dict:
    return json.loads(demo.tool("orgmemory", "replay", str(demo.dataset), "--format", "json"))["states"][f"memory:{memory}"]


def findings_of(paths: dict) -> dict:
    return {f["text"]: f for f in json.loads(paths["audit"].read_bytes())["findings"]}


def head_of(demo) -> str:
    return git(demo.repo, "rev-parse", "HEAD").decode("ascii").strip()


def decide(demo, memory: str, action: str, timestamp: str, actor: str = "zhou") -> None:
    demo.append({"id": f"decide-{action}-{memory}", "timestamp": timestamp, "actor": actor, "action": action,
                 "subject": f"memory:{memory}", "evidence_ids": [f"{memory}-{head_of(demo)[:12]}-line"]})


def refuses(demo, paths: dict, capfdbinary, file_name: str) -> None:
    """to_events.py stops, prints no record, and keeps no copy of the file that is not at the commit."""
    arguments = [str(paths["audit"]), str(paths["history"]), str(paths["history_md"]),
                 "--repo", str(demo.repo), "--dataset", str(demo.dataset)]
    assert to_events.main(arguments) == 2
    captured = capfdbinary.readouterr()
    assert captured.out == b"" and b"differs from the commit" in captured.err
    assert list(demo.dataset.rglob(file_name)) == []


def test_a_rejected_line_removed_and_landed_again_is_a_new_request(demo):
    """After a removal, orgmemory has to know about the second landing, or it keeps answering from the first."""
    demo.commit("agent-7", "2026-10-01T09:00:00+00:00", "Remember the cache (approved-by: wu)",
                {"MEMORY.md": run_demo.memory(CACHE), **CACHE_DOCS})
    first = demo.verify("2026-10-02", "first")
    assert {e["event"]["action"] for e in demo.record(first) if e["kind"] == "event"} >= {"request", "approve"}
    memory = findings_of(first)[CACHE]["memory_id"]
    decide(demo, memory, "reject", "2026-10-03T09:00:00+00:00")
    assert state_of(demo, memory)["reconsideration_requested"] is False

    demo.commit("lin", "2026-10-04T09:00:00+00:00", "Remove the cache line", {"MEMORY.md": run_demo.memory()})
    assert demo.record(demo.verify("2026-10-05", "second")) == []
    demo.commit("agent-7", "2026-10-06T09:00:00+00:00", "Put the cache line back (approved-by: wu)",
                {"MEMORY.md": run_demo.memory(CACHE)})
    commit = head_of(demo)
    records = demo.record(demo.verify("2026-10-07", "third"))
    events = [r["event"] for r in records if r["kind"] == "event"]
    assert [(e["id"], e["actor"], e["action"]) for e in events] == [
        (f"write-request-{memory}-{commit[:12]}", "agent-7", "request"),
        (f"write-approve-{memory}-{commit[:12]}", "wu", "approve")]
    quotes = [r["quote"] for r in records if r["kind"] == "evidence"]
    assert any("Put the cache line back" in quote for quote in quotes)

    state = state_of(demo, memory)
    assert state["current_status"] == "rejected"
    assert state["reconsideration_requested"] is True
    assert state["pending_approval"] == ["memory_steward"]
    assert [change["actor"] for change in state["pending_changes"]] == ["wu"]
    demo.tool("orgmemory", "validate", str(demo.dataset))
    assert demo.record(demo.verify("2026-10-07", "third-again")) == []


def test_an_approved_line_removed_and_landed_again_asks_for_a_decision_again(demo):
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache (approved-by: zhou)",
                {"MEMORY.md": run_demo.memory(CACHE), **CACHE_DOCS})
    first = demo.verify("2026-10-02", "first")
    demo.record(first)
    memory = findings_of(first)[CACHE]["memory_id"]
    assert state_of(demo, memory)["current_status"] == "approved"
    demo.commit("lin", "2026-10-03T09:00:00+00:00", "Remove the cache line", {"MEMORY.md": run_demo.memory()})
    demo.commit("agent-7", "2026-10-04T09:00:00+00:00", "Put the cache line back (approved-by: wu)",
                {"MEMORY.md": run_demo.memory(CACHE)})
    demo.record(demo.verify("2026-10-05", "second"))
    state = state_of(demo, memory)
    assert state["current_status"] == "approved"  # zhou's decision stands, and wu's approval changes nothing
    assert state["reconsideration_requested"] is True and state["pending_approval"] == ["memory_steward"]
    assert [change["actor"] for change in state["pending_changes"]] == ["wu"]


def test_a_case_only_edit_keeps_the_id_and_adds_no_event(demo):
    """What docs/architecture.md says of an edit: only a change to the words makes a new subject."""
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache (approved-by: zhou)",
                {"MEMORY.md": run_demo.memory(CACHE), **CACHE_DOCS})
    first = demo.verify("2026-10-02", "first")
    demo.record(first)
    demo.commit("wu", "2026-10-03T09:00:00+00:00", "Shout", {"MEMORY.md": run_demo.memory(CACHE.upper())})
    second = demo.verify("2026-10-04", "second")
    assert demo.record(second) == []
    assert {c["memory_id"] for c in json.loads(second["history"].read_bytes())["claims"]} == {
        findings_of(first)[CACHE]["memory_id"]}


def test_reports_older_than_the_memory_are_refused(demo, capfdbinary):
    """The stale report: old claims, a file that has moved on, and a line that must not be cited for them."""
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache",
                {"MEMORY.md": run_demo.memory(CACHE), **CACHE_DOCS})
    old = demo.verify("2026-10-02", "old")
    write_text_exact(demo.repo / "MEMORY.md", run_demo.memory("The queue depth stays below 40 items."))
    refuses(demo, old, capfdbinary, "MEMORY.md")
    demo.commit("lin", "2026-10-03T09:00:00+00:00", "Remember the queue", {})
    refuses(demo, old, capfdbinary, "MEMORY.md")


def test_an_uncommitted_memory_line_is_refused(demo, capfdbinary):
    """Fresh reports on a dirty tree: the bytes would be filed under a commit that does not hold them."""
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache",
                {"MEMORY.md": run_demo.memory(CACHE), **CACHE_DOCS})
    write_text_exact(demo.repo / "MEMORY.md", run_demo.memory(CACHE, "The queue depth stays below 40 items."))
    refuses(demo, demo.verify("2026-10-02", "dirty"), capfdbinary, "MEMORY.md")


@pytest.mark.parametrize("tracked", [True, False], ids=["changed", "untracked"])
def test_evidence_that_is_not_at_head_is_refused(demo, capfdbinary, tracked):
    """The audit's hash matches, since it read the same working tree. The file is still not at the commit."""
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache",
                {"MEMORY.md": run_demo.memory(CACHE), **(CACHE_DOCS if tracked else {})})
    (demo.repo / "docs").mkdir(exist_ok=True)
    write_text_exact(demo.repo / "docs" / "cache.md", "# Cache\n\nThe cache uses Redis 8.\n")
    paths = demo.verify("2026-10-02", "dirty")
    assert findings_of(paths)[CACHE]["status"] == "candidate_counterevidence"
    refuses(demo, paths, capfdbinary, "cache.md")


def test_crlf_from_git_in_the_working_tree_is_not_a_change(demo):
    """core.autocrlf on Windows: the blob has LF, and the audit hashed CRLF. The stored bytes are what it hashed."""
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache",
                {"MEMORY.md": run_demo.memory(CACHE), **CACHE_DOCS})
    for name in ("MEMORY.md", "docs/cache.md"):
        (demo.repo / name).write_bytes((demo.repo / name).read_bytes().replace(b"\n", b"\r\n"))
    records = demo.record(demo.verify("2026-10-02", "crlf"))
    assert any(r["kind"] == "evidence" and r["path"].endswith("docs/cache.md") for r in records)
    demo.tool("orgmemory", "validate", str(demo.dataset))


def test_a_finding_never_sorts_before_the_commit_it_is_about(demo):
    """An evening commit behind UTC, audited that evening: the end of the as_of day in UTC is before the commit."""
    demo.commit("lin", "2026-11-04T18:30:00-06:00", "Remember the cache (approved-by: zhou)",
                {"MEMORY.md": run_demo.memory(CACHE), **CACHE_DOCS})
    paths = demo.verify("2026-11-04", "evening")
    records = demo.record(paths)
    memory = findings_of(paths)[CACHE]["memory_id"]
    [risk] = [r["event"] for r in records if r["kind"] == "event" and r["event"]["action"] == "raise_risk"]
    assert risk["timestamp"] == "2026-11-04T18:30:00-06:00"
    report = json.loads(demo.tool("orgmemory", "audit", str(demo.dataset), "--format", "json"))
    [finding_] = [f for f in report["findings"] if f["kind"] == "risk_after_approval"]
    assert finding_["subject"] == f"memory:{memory}" and finding_["severity"] == "high"
    assert "after the approval by zhou" in finding_["detail"]


def test_another_lines_commit_date_leaves_a_finding_where_it_was(tmp_path):
    """Author dates are text anyone can write. A date far ahead moves its own line's finding, and only that one."""
    reports = [claim("m-00000001"), {**claim("m-00000002"), "introduced": {**claim("m-00000002")["introduced"],
                                                                           "date": "2099-01-01T00:00:00+00:00"}}]
    findings = [finding("m-00000001", "insufficient_evidence"), finding("m-00000002", "insufficient_evidence")]
    events = events_of(convert(tmp_path, findings=findings, claims=reports,
                               report=history_report("m-00000001", "m-00000002")))
    times = {e["subject"]: e["timestamp"] for e in events if e["action"] == "comment"}
    assert times == {"memory:m-00000001": "2026-10-10T23:59:59+00:00", "memory:m-00000002": "2099-01-01T00:00:00+00:00"}


@pytest.mark.parametrize(("options", "severity"), [((), "high"), (("--audited-at=2026-11-04T14:00:00+00:00",), "medium")],
                         ids=["default", "audited-at"])
def test_audited_at_puts_a_same_day_decision_after_the_finding(demo, options, severity):
    """zhou reads the audit at 14:00 and approves at 15:00. Only the audit's real time can say which came first."""
    demo.commit("agent-7", "2026-11-01T09:00:00+00:00", "Remember the cache",
                {"MEMORY.md": run_demo.memory(CACHE), **CACHE_DOCS})
    paths = demo.verify("2026-11-04", "morning")
    demo.record(paths, *options)
    memory = findings_of(paths)[CACHE]["memory_id"]
    decide(demo, memory, "approve", "2026-11-04T15:00:00+00:00")
    report = json.loads(demo.tool("orgmemory", "audit", str(demo.dataset), "--format", "json"))
    [finding_] = [f for f in report["findings"] if f["kind"] == "risk_after_approval"]
    assert finding_["severity"] == severity


@pytest.mark.parametrize("value", ["2026-11-04", "2026-11-04T14:00:00", "yesterday"])
def test_audited_at_needs_a_time_with_a_zone(tmp_path, capfdbinary, value):
    audit, history = make_reports()
    arguments = save_reports(tmp_path, audit, history, "")
    with pytest.raises(SystemExit) as stopped:
        to_events.main([*arguments, "--repo", str(tmp_path), "--dataset", str(tmp_path), f"--audited-at={value}"])
    assert stopped.value.code == 2 and b"with a zone" in capfdbinary.readouterr().err


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"], ids=["U+2028", "U+2029", "U+0085"])
def test_a_line_separator_in_a_memory_line_leaves_later_conversions_working(demo, separator):
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache",
                {"MEMORY.md": run_demo.memory(f"The cache{separator} uses Redis 6."), **CACHE_DOCS})
    assert demo.record(demo.verify("2026-10-02", "first"))
    assert separator.encode("utf-8") in (demo.dataset / "events.jsonl").read_bytes()
    assert demo.record(demo.verify("2026-10-02", "again")) == []
    demo.commit("lin", "2026-10-03T09:00:00+00:00", "Remember the queue", {
        "MEMORY.md": run_demo.memory(f"The cache{separator} uses Redis 6.", "The queue depth stays below 40 items.")})
    events = [r["event"] for r in demo.record(demo.verify("2026-10-04", "later")) if r["kind"] == "event"]
    assert "The queue depth stays below 40 items." in events[0]["note"]


def test_an_audit_of_the_whole_repository_converts(demo):
    """`--evidence .` is what the README, the skill and the dogfood job show."""
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache",
                {"MEMORY.md": run_demo.memory(CACHE), "cache.md": "# Cache\n\nThe cache uses Redis 7.\n"})
    paths = demo.verify("2026-10-02", "whole", evidence=".")
    assert json.loads(paths["audit"].read_bytes())["evidence_root"] == "."
    records = demo.record(paths)
    head = json.loads(paths["history"].read_bytes())["head"][:12]
    [cited] = [r for r in records if r["kind"] == "evidence" and r["source_kind"] == "evidence_file"]
    assert (cited["path"], cited["source_uri"]) == (f"sources/{head}/cache.md", f"git:{head}:cache.md")
    demo.tool("orgmemory", "validate", str(demo.dataset))


def test_a_sentence_cited_twice_is_one_record_whose_id_agrees_with_it(demo):
    """`orgmemory evidence add` finds the first occurrence, so an id built on the second would disagree with it."""
    sentence = "The cache uses Redis 7."
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache", {
        "MEMORY.md": run_demo.memory(CACHE),
        "docs/cache.md": f"# Old plan\n\n{sentence}\n\n# Current plan\n\n{sentence}\n"})
    paths = demo.verify("2026-10-02", "twice")
    spans = [e["span"] for e in findings_of(paths)[CACHE]["evidence"]]
    assert len({span["start"] for span in spans}) == 2
    records = demo.record(paths)
    [risk] = [r["event"] for r in records if r["kind"] == "event" and r["event"]["action"] == "raise_risk"]
    [cited] = risk["evidence_ids"]
    stored = [json.loads(line) for line in (demo.dataset / "evidence.jsonl").read_bytes().split(b"\n") if line]
    [record] = [entry for entry in stored if entry["id"] == cited]
    assert cited.endswith(f"-{record['start']}") and record["start"] == min(span["start"] for span in spans)


def test_a_later_audit_with_the_same_status_adds_nothing(demo):
    """The docs move from Redis 7 to Redis 8. The event stands, so a new record would be one that nothing cites."""
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache",
                {"MEMORY.md": run_demo.memory(CACHE), **CACHE_DOCS})
    assert demo.record(demo.verify("2026-10-02", "first"))
    demo.commit("lin", "2026-10-03T09:00:00+00:00", "Move the docs on",
                {"docs/cache.md": "# Cache\n\nThe cache uses Redis 8.\n"})
    assert demo.record(demo.verify("2026-10-04", "second")) == []


def test_a_confirmed_contradiction_from_a_reviewer_with_no_role_is_an_ignored_attempt(demo):
    """The rule docs/architecture.md states: such a reviewer's risk is recorded and ignored."""
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache",
                {"MEMORY.md": run_demo.memory(CACHE), **CACHE_DOCS})
    memory = findings_of(demo.verify("2026-10-02", "plain"))[CACHE]["memory_id"]
    write_text_exact(demo.work / "reviews.jsonl", json.dumps({
        "memory_id": memory, "path": "cache.md", "quote": "The cache uses Redis 7.", "relation": "contradicts",
        "reviewer": "stranger"}) + "\n")
    paths = demo.verify("2026-10-02", "reviewed", "--reviews", str(demo.work / "reviews.jsonl"))
    assert findings_of(paths)[CACHE]["status"] == "challenged"
    demo.record(paths)
    assert state_of(demo, memory)["risks"] == []
    report = json.loads(demo.tool("orgmemory", "audit", str(demo.dataset), "--format", "json"))
    assert "stranger" in {f["actor"] for f in report["findings"] if f["kind"] == "unauthorized_attempts"}


def test_a_subject_with_an_approved_by_and_no_clean_name_is_noted(tmp_path, capfdbinary):
    repo, dataset = tmp_path / "repo", tmp_path / "dataset"
    repo.mkdir(), dataset.mkdir()
    write_text_exact(repo / "MEMORY.md", "- The cache uses Redis.\n")
    audit, history = make_reports([], [claim("m-00000001", subject="Remember the cache (approved-by: zhou) ")],
                                  commit_all(repo))
    arguments = save_reports(tmp_path, audit, history, history_report("m-00000001"))
    assert to_events.main([*arguments, "--repo", str(repo), "--dataset", str(dataset)]) == 0
    assert b"note: the commit subject of m-00000001" in capfdbinary.readouterr().err


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
    audit, history = make_reports([finding("m-00000000", "insufficient_evidence", text)], [], commit_all(repo))
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


def make_reports(findings=(), claims=(), head: str = "a" * 40) -> tuple[dict, dict]:
    return ({"tool": "memfalsify", "as_of": "2026-10-10", "memory_file": "MEMORY.md", "evidence_root": "docs",
             "findings": list(findings)},
            {"tool": "memfalsify", "head": head, "claims": list(claims)})


def git(repo: Path, *args: str) -> bytes:
    options = ("-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", "-c", "user.name=test",
               "-c", "user.email=test@example.com")
    return subprocess.run([shutil.which("git"), "-C", str(repo), *options, *args], check=True, capture_output=True,
                          env=run_demo.clean_environment(repo.parent)).stdout


def commit_all(repo: Path) -> str:
    """A real repository holding the files under ``repo``: a cited file must be at HEAD. Returns HEAD."""
    git(repo, "init", "-q")
    if git(repo, "status", "--porcelain"):
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "Add the files")
    return git(repo, "rev-parse", "HEAD").decode("ascii").strip()


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
    audit, history = make_reports(findings, claims, commit_all(repo))
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
    head = commit_all(tmp_path / "repo")[:12]
    assert evidence["quote"] == entry["span"]["quote"]
    assert evidence["path"] == f"sources/{head}/docs/cache.md"
    assert evidence["source_uri"] == f"git:{head}:docs/cache.md"
    assert (tmp_path / "dataset" / evidence["path"]).read_bytes() == (tmp_path / "repo" / "docs" / "cache.md").read_bytes()
    assert events_of(records)[0]["evidence_ids"] == [evidence["id"]]


@pytest.mark.parametrize(("subject", "approver"), [
    ("Remember the cache (approved-by: zhou)", "zhou"),
    ("Remember the cache (approved-by: Bowen Zhang)", "Bowen Zhang"),
    ("Remember the cache (approved-by: \u5468\u4f1f)", "\u5468\u4f1f"),
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
    head = commit_all(tmp_path / "repo")[:12]
    assert request["evidence_ids"] == approve["evidence_ids"] == [f"m-00000001-{head}-line",
                                                                  "m-00000001-bbbbbbbbbbbb-introduced"]


@pytest.mark.parametrize("introduced", [None, {"commit": None, "date": None, "author": None, "subject": "uncommitted"}])
def test_a_line_with_no_commit_to_name_gets_no_write_events(tmp_path, introduced):
    entry = {**claim("m-00000001"), "introduced": introduced}
    records = convert(tmp_path, claims=[entry], findings=[finding("m-00000001", "insufficient_evidence")])
    assert [e["action"] for e in events_of(records)] == ["comment"]


def test_a_line_that_does_not_hold_the_claim_is_refused(tmp_path):
    """The whole-line quote is for markup. An unrelated line must not become evidence for the claim."""
    with pytest.raises(to_events.ConversionError, match="does not hold the claim"):
        convert(tmp_path, findings=[finding("m-00000001", "insufficient_evidence", text="The cache uses Valkey.")])


def test_the_markup_rules_are_memfalsifys():
    assert (to_events.LINK.pattern, to_events.WIKILINK.pattern, to_events.EMPHASIS.pattern) == (
        memory_module.LINK.pattern, memory_module.WIKILINK.pattern, memory_module.EMPHASIS.pattern)


def test_markup_in_a_memory_line_is_quoted_with_the_whole_line(demo):
    """Real markup through the real CLIs: the claim text is in no file, so the record quotes the line it came from."""
    line = "**The cache** uses `Redis 6` and [the queue](https://example.com) stays [[small]] today."
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache (approved-by: zhou)",
                {"MEMORY.md": run_demo.memory(line), **CACHE_DOCS})
    records = demo.record(demo.verify("2026-10-02", "first"))
    quotes = [r["quote"] for r in records if r["kind"] == "evidence" and r["source_kind"] == "memory_file"]
    assert quotes == [f"- {line}"]
    demo.tool("orgmemory", "validate", str(demo.dataset))


def test_a_wrapped_claim_is_found_from_the_line_it_starts_on(demo):
    """MemFalsify reads the lines of a paragraph as one claim; `location` is the first, and the record quotes that line."""
    first = "The cache uses **Redis 6** for sessions and it keeps them for as long as the login lasts, as the"
    demo.commit("lin", "2026-10-01T09:00:00+00:00", "Remember the cache (approved-by: zhou)",
                {"MEMORY.md": run_demo.memory(f"{first}\n  security review asked for."), **CACHE_DOCS})
    records = demo.record(demo.verify("2026-10-02", "first"))
    quotes = [r["quote"] for r in records if r["kind"] == "evidence" and r["source_kind"] == "memory_file"]
    assert quotes == [f"- {first}"]
    notes = [r["event"]["note"] for r in records if r["kind"] == "event" and r["event"]["action"] == "request"]
    assert notes == ["Memory write at MEMORY.md:3: The cache uses Redis 6 for sessions and it keeps them for as long as "
                     "the login lasts, as the security review asked for."]
    demo.tool("orgmemory", "validate", str(demo.dataset))


@pytest.mark.parametrize("lines, text, held", [
    (["- One two", "  three four.", ""], "One two three four.", True),
    (["> One two", "> three **four**.", ""], "One two three four.", True),
    (["[One", "two](x.md) three.", ""], "One two three.", True),
    (["一二三", "四五六。", ""], "一二三四五六。", True),
    (["- One two", "", "  three four."], "One two three four.", False),
    (["- One two", "  three"], "One two three four.", False),
    (["- Something else", "- One two", "  three four."], "One two three four.", False),
])
def test_a_claim_across_lines_stops_at_the_end_of_its_block(lines, text, held):
    assert to_events.holds(lines, text) is held


@pytest.mark.parametrize("memory_file", ["agent/MEMORY.md", "agent\\MEMORY.md"])
def test_a_line_from_a_linked_file_in_a_subdirectory_is_found(tmp_path, memory_file):
    """Reports hold str(Path), so on Windows the memory file is agent\\MEMORY.md."""
    repo, dataset = tmp_path / "repo", tmp_path / "dataset"
    (repo / "agent").mkdir(parents=True)
    dataset.mkdir()
    write_text_exact(repo / "agent" / "notes.md", "# Notes\n\n- The cache uses Redis.\n")
    write_text_exact(repo / "notes.md", "- Another file at the top, with another line.\n")
    head = commit_all(repo)
    audit, history = make_reports([finding("m-00000001", "insufficient_evidence", location="notes.md:3")], [], head)
    audit["memory_file"] = memory_file
    [evidence, _] = to_events.Converter(audit, history, "", repo, dataset).convert()
    assert evidence["path"] == f"sources/{head[:12]}/agent/notes.md"
    assert evidence["source_uri"] == f"git:{head[:12]}:agent/notes.md"
    assert evidence["quote"] == "The cache uses Redis."


def test_an_evidence_root_with_windows_separators_is_found(tmp_path):
    repo, dataset = tmp_path / "repo", tmp_path / "dataset"
    (repo / "docs" / "sub").mkdir(parents=True)
    dataset.mkdir()
    write_text_exact(repo / "docs" / "sub" / "cache.md", "The cache uses Valkey.\n")
    audit, history = make_reports([finding("m-00000001", "candidate_counterevidence", evidence=[span_entry()])], [],
                                  commit_all(repo))
    audit["evidence_root"] = "docs\\sub"
    [evidence, _] = to_events.Converter(audit, history, "", repo, dataset).convert()
    assert evidence["path"].endswith("/docs/sub/cache.md")


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
    yield "windows traversal", {"findings": [finding("m-00000001", "insufficient_evidence",
                                                     location=f"..\\..\\{secret}:1")]}
    yield "windows rooted path", {"findings": [finding("m-00000001", "insufficient_evidence",
                                                       location=f"\\{secret}:1")]}
    yield "evidence path that is only a dot", {"findings": [finding("m-00000001", "candidate_counterevidence",
                                                                    evidence=[span_entry(path=".")])]}
    yield "evidence root that goes up", {"findings": [finding("m-00000001", "candidate_counterevidence",
                                                              evidence=[span_entry()])], "evidence_root": ".."}
    yield "commit id", {"claims": [claim("m-00000001", commit=secret)]}
    yield "quote that is not in the file", {"findings": [finding("m-00000001", "candidate_counterevidence", evidence=[
        {**span_entry(), "span": {**span_entry()["span"], "quote": f"Not in the file {secret}."}}])]}
    yield "an audit date that is no date", {"findings": [], "as_of": secret}
    yield "introduced line of another commit", {"claims": [claim("m-00000001", commit="c" * 40)],
                                                "report": history_report("m-00000001")}
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
    audit, history = make_reports(case.get("findings", ()), case.get("claims", ()), commit_all(repo))
    audit.update({key: case[key] for key in ("as_of", "evidence_root") if key in case})
    arguments = save_reports(tmp_path, audit, history, case.get("report", ""))
    assert to_events.main([*arguments, "--repo", str(repo), "--dataset", str(dataset)]) == 2
    captured = capfdbinary.readouterr()
    assert captured.out == b"" and captured.err.startswith(b"error: ")
    assert b"hostile" not in captured.err and b"\x1b" not in captured.err


@pytest.mark.parametrize("path", ["../x", "a/../../x", "/x", "..\\..\\x", "a\\..\\..\\x", "\\x", "C:x", "C:\\x", ".", ""])
def test_a_path_that_leaves_on_either_os_is_refused(path):
    with pytest.raises(to_events.ConversionError, match="outside the repository"):
        to_events.checked_path(path)


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
    stored = tmp_path / "dataset" / "sources" / commit_all(tmp_path / "repo")[:12] / "MEMORY.md"
    write_text_exact(stored, "- Some other memory.\n")
    with pytest.raises(to_events.ConversionError, match="changed without a new commit"):
        convert(tmp_path, findings=findings)


def test_an_audit_with_no_file_named_gets_a_message_saying_what_to_run(tmp_path):
    (tmp_path / "repo").mkdir(), (tmp_path / "dataset").mkdir()
    audit, history = make_reports()
    audit_set = {"tool": "memfalsify", "files": ["MEMORY.md"], "summary": {}, "reports": [audit]}
    with pytest.raises(to_events.ConversionError, match="memfalsify audit MEMORY.md --format json"):
        to_events.Converter(audit_set, history, "", tmp_path / "repo", tmp_path / "dataset")


def test_reports_from_another_tool_are_refused(tmp_path):
    (tmp_path / "repo").mkdir(), (tmp_path / "dataset").mkdir()
    audit, history = make_reports()
    for bad_audit, bad_history in (({**audit, "tool": "orgmemory"}, history), (audit, {**history, "head": None})):
        with pytest.raises(to_events.ConversionError):
            to_events.Converter(bad_audit, bad_history, "", tmp_path / "repo", tmp_path / "dataset")


@pytest.mark.parametrize("head", ["../../../../", "..", "a" * 39, "A" * 40, "a" * 40 + "\n", "a" * 41, 7, ["a" * 40]],
                         ids=["up four", "up one", "short", "upper case", "newline", "long", "number", "list"])
def test_a_forged_head_is_refused_and_writes_nothing(tmp_path, head):
    root = tmp_path / "a" / "b" / "c"
    repo, dataset = root / "repo", root / "e1" / "dataset"
    repo.mkdir(parents=True), dataset.mkdir(parents=True)
    write_text_exact(repo / "MEMORY.md", "- The cache uses Redis.\n")
    commit_all(repo)
    audit, history = make_reports([finding("m-00000001", "insufficient_evidence")], [], head)
    arguments = save_reports(tmp_path, audit, history, "")
    before = {path for path in tmp_path.rglob("*") if ".git" not in path.parts}
    assert to_events.main([*arguments, "--repo", str(repo), "--dataset", str(dataset)]) == 2
    assert {path for path in tmp_path.rglob("*") if ".git" not in path.parts} == before


def test_a_full_length_head_of_either_hash_is_accepted(tmp_path):
    (tmp_path / "repo").mkdir(), (tmp_path / "dataset").mkdir()
    for head in ("a" * 40, "b" * 64):
        audit, history = make_reports([], [], head)
        assert to_events.Converter(audit, history, "", tmp_path / "repo", tmp_path / "dataset").head == head[:12]


def test_a_symlink_under_the_sources_is_refused_and_nothing_is_written_through_it(tmp_path, make_symlink):
    repo, dataset, outside = tmp_path / "repo", tmp_path / "dataset", tmp_path / "outside"
    repo.mkdir(), dataset.mkdir(), outside.mkdir()
    write_text_exact(repo / "MEMORY.md", "- The cache uses Redis.\n")
    head = commit_all(repo)
    (dataset / "sources").mkdir()
    make_symlink(dataset / "sources" / head[:12], outside, directory=True)
    audit, history = make_reports([finding("m-00000001", "insufficient_evidence")], [], head)
    with pytest.raises(to_events.ConversionError, match="outside the dataset"):
        to_events.Converter(audit, history, "", repo, dataset).convert()
    assert list(outside.iterdir()) == []


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
