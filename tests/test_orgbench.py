"""The orgmemory benchmark: label validation, how the harness compares, and the measured results on the real cases."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memcore import ManifestEntry, locate_quote
from orgmemory.github_apply import run_convert

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmarks" / "orgmemory"
RUN = BENCH / "run.py"
_spec = importlib.util.spec_from_file_location("orgmemory_benchmark", RUN)
bench = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)
runner = CliRunner()

# Labels the implementation does not meet, as RESULTS.md explains them. If this set changes, the measured behavior
# changed: update RESULTS.md (and this set) to match. Never change a label to make it pass.
KNOWN_MISMATCHES = {
    # Fixed: a repeated JSON key now fails closed, so duplicate-key-event and duplicate-key-policy pass.
    # The case gives roles only in policy.json (today's roles, no policy-log.jsonl), so a role change still
    # applies to every past event. Dated roles fix it only when the change is recorded (see RESULTS.md).
    "role-change | 2026-10-09T00:00:00+00:00 | trail | c02 | outcome",
    "role-change | 2026-10-09T00:00:00+00:00 | trail | c04 | outcome",
    "role-change | 2026-10-20T00:00:00+00:00 | state | Audit log export | status",
    "role-change | 2026-10-20T00:00:00+00:00 | state | Audit log export | pending_approval",
    "role-change | 2026-10-20T00:00:00+00:00 | state | Audit log export | last_authorized_decision",
    "role-change | 2026-10-20T00:00:00+00:00 | audit | unauthorized_attempts zhou | severity",
    "role-change | 2026-10-20T00:00:00+00:00 | audit | unauthorized_attempts lin | severity",
    "role-change | 2026-10-20T00:00:00+00:00 | audit | * | unexpected findings",
}


@pytest.fixture(scope="module")
def measured():
    return bench.evaluate(bench.load_cases(bench.CASES))


# --- the real cases ------------------------------------------------------------------------------------------

def test_cases_meet_the_benchmark_requirements():
    cases = bench.load_cases(bench.CASES)
    labels = sum(bench.count_labels(c.expected)[0] for c in cases)
    assert len(cases) >= 8 and labels >= 40
    readme = (BENCH / "README.md").read_text(encoding="utf-8")
    table = readme.split("\n## Cases\n", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `([a-z0-9-]+)` \| \S", table, re.MULTILINE)
    assert len(rows) == len(set(rows))
    assert set(rows) == {c.id for c in cases}, "every case needs exactly one row in the README's Cases table"
    snapshots = [s for c in cases for s in c.expected.snapshots]
    used_kinds = {f.kind for s in snapshots if s.audit for f in s.audit.findings}
    assert used_kinds == set(bench.Kind.__args__), "every audit kind should be labeled somewhere"
    assert {t.outcome for s in snapshots for t in s.trail.values()} == set(bench.Outcome.__args__)
    assert {l.error is None for c in cases for l in c.expected.validation} == {True, False}
    assert any(c.expected.snapshots and not c.expected.validation for c in cases)
    assert any(c.expected.validation and not c.expected.snapshots for c in cases)  # a case whose own data is invalid


def test_measured_mismatches_are_the_known_findings(measured):
    failed = {c["id"] for c in measured["checks"] if not c["passed"]}
    assert failed == KNOWN_MISMATCHES
    totals = measured["totals"]
    assert totals["failed"] == len(KNOWN_MISMATCHES) and totals["passed"] + totals["failed"] == totals["checks"]
    assert all(measured["by_kind"][kind]["checks"] for kind in bench.CHECK_KINDS)


def test_results_md_holds_the_current_report(measured):
    """RESULTS.md quotes the harness report verbatim (without its title), so it cannot go stale silently."""
    report = bench.to_markdown(measured).split("\n", 2)[2]
    assert report in (BENCH / "RESULTS.md").read_text(encoding="utf-8"), \
        "regenerate RESULTS.md: python benchmarks/orgmemory/run.py"


def test_run_script_exit_codes_and_json(tmp_path):
    out = tmp_path / "results.json"
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}  # the harness itself must guarantee UTF-8/LF
    done = subprocess.run([sys.executable, str(RUN), "--json", str(out)], cwd=REPO, capture_output=True,
                          timeout=300, env=env)
    assert done.returncode == 1, done.stderr.decode("utf-8", "replace")
    assert done.stdout.decode("utf-8").startswith("# orgmemory benchmark\n")
    result = json.loads(out.read_bytes().decode("utf-8"))
    assert {c["id"] for c in result["checks"] if not c["passed"]} == KNOWN_MISMATCHES
    check = subprocess.run([sys.executable, str(RUN), "--check"], cwd=REPO, capture_output=True, timeout=300,
                           env=env)
    assert check.returncode == 0 and check.stdout.decode("utf-8").endswith("checks: expectations OK\n")
    selected = runner.invoke(bench.app, ["--case", "revoke-chains", "--json", "-"])
    assert selected.exit_code == 0, selected.output
    assert json.loads(selected.output)["totals"]["cases"] == 1


@pytest.mark.parametrize("subprocess_encoding", [None, "cp1252"])
def test_github_case_is_the_importers_output(tmp_path, subprocess_encoding):
    """github-acme-api is exactly what `orgmemory github convert` makes of its capture."""
    case = BENCH / "cases" / "github-acme-api"
    for name in ("policy.json", "github.json"):
        shutil.copyfile(case / name, tmp_path / name)
    if subprocess_encoding is None:
        run_convert(tmp_path, case / "capture.json")
    else:
        env = {**os.environ, "PYTHONIOENCODING": subprocess_encoding, "PYTHONUTF8": "0",
               "PYTHONPATH": os.pathsep.join(str(REPO / "packages" / package / "src")
                                             for package in ("memcore", "orgmemory"))}
        code = ("import sys; from pathlib import Path; from orgmemory.github_apply import run_convert; "
                "run_convert(Path(sys.argv[1]), Path(sys.argv[2]))")
        done = subprocess.run([sys.executable, "-c", code, str(tmp_path), str(case / "capture.json")],
                              cwd=REPO, capture_output=True, timeout=300, env=env)
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    made = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    kept = sorted(p.relative_to(case).as_posix() for p in case.rglob("*") if p.is_file()
                  and p.name not in ("capture.json", "expected.json"))
    assert made == kept
    for name in made:
        assert (tmp_path / name).read_bytes() == (case / name).read_bytes(), name


def test_case_data_is_byte_exact_in_git():
    attributes = (REPO / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "benchmarks/orgmemory/cases/** -text" in attributes
    crlf = BENCH / "cases" / "forged-evidence" / "sources" / "email-crlf.txt"
    assert crlf.read_bytes().count(b"\r\n") == 4


# --- the harness, on a tiny case ----------------------------------------------------------------------------

LOG = b"Tiny decision log\nLin asks for X.\nZhou approves X.\nWu approves Y.\n"
EVENTS = [
    {"id": "e1", "timestamp": "2026-10-01T09:00:00Z", "actor": "lin", "action": "request", "subject": "X",
     "evidence_ids": ["q1"]},
    {"id": "e2", "timestamp": "2026-10-02T09:00:00Z", "actor": "zhou", "action": "approve", "subject": "X",
     "evidence_ids": ["q2"]},
    {"id": "e3", "timestamp": "2026-10-03T09:00:00Z", "actor": "wu", "action": "approve", "subject": "Y",
     "evidence_ids": ["q3"]},
]
POLICY = {"actors": {"lin": ["engineer"], "zhou": ["product_manager"], "wu": ["account_manager"]},
          "permissions": {"request": ["engineer"], "approve": ["product_manager"], "reject": ["product_manager"]}}


def state(status, decision=None, pending=(), note="tiny"):
    return {"status": status, "pending_approval": list(pending), "reconsideration_requested": False, "risks": [],
            "dependencies": [], "last_authorized_decision": decision, "note": note}


def expected():
    return {
        "description": "tiny synthetic case",
        "validation": [{"name": "main", "error": None, "note": "loads"}],
        "snapshots": [{
            "as_of": "2026-10-20T00:00:00Z",
            "states": {"X": state("approved", "e2"), "Y": state("proposed")},
            "trail": {"e3": {"outcome": "unauthorized", "note": "account_manager may not approve"}},
            "order": ["e1", "e2"],
            "audit": {"findings": [{"kind": "unauthorized_attempts", "severity": "medium", "actor": "wu",
                                    "note": "one ignored approve"}]},
        }],
    }


def write_case(root: Path, labels=None) -> Path:
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "log.txt").write_bytes(LOG)
    lines = []
    for eid, quote in (("q1", "Lin asks for X."), ("q2", "Zhou approves X."), ("q3", "Wu approves Y.")):
        span = locate_quote(root, "sources/log.txt", quote)
        lines.append(json.dumps(ManifestEntry(
            id=eid, path=span.path, sha256=span.sha256, quote=span.quote, start=span.start, end=span.end,
            source_uri="synthetic://tiny", source_kind="note", captured_at="2026-10-01T00:00:00Z",
        ).model_dump(mode="json")))
    (root / "evidence.jsonl").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    (root / "policy.json").write_bytes(json.dumps(POLICY).encode("utf-8"))
    (root / "events.jsonl").write_bytes("".join(json.dumps(e) + "\n" for e in EVENTS).encode("utf-8"))
    labels = expected() if labels is None else labels
    text = labels if isinstance(labels, str) else json.dumps(labels)
    (root / "expected.json").write_bytes(text.encode("utf-8"))
    return root


def failures(result):
    return {c["id"]: (c["expected"], c["actual"]) for c in result["checks"] if not c["passed"]}


def test_matching_labels_pass(tmp_path):
    write_case(tmp_path / "cases" / "tiny")
    (case,) = bench.load_cases(tmp_path / "cases")
    assert bench.count_labels(case.expected) == (6, 18)
    result = bench.evaluate([case])
    assert result["totals"] == {"cases": 1, "labels": 6, "checks": 18, "passed": 18, "failed": 0}
    out = runner.invoke(bench.app, ["--cases-dir", str(tmp_path / "cases")])
    assert out.exit_code == 0 and "None: every check matches its label." in out.output
    assert "- Result: 18/18 checks pass, 0 fail" in out.output


def test_each_mismatch_is_reported_with_both_values(tmp_path):
    labels = expected()
    snapshot = labels["snapshots"][0]
    snapshot["states"]["X"] = state("rejected", "e2", note="wrong on purpose")
    del snapshot["states"]["Y"]
    snapshot["trail"]["e3"]["outcome"] = "applied"
    snapshot["order"] = ["e2", "e1"]
    snapshot["audit"]["findings"] = [
        {"kind": "unauthorized_attempts", "severity": "high", "actor": "wu", "note": "wrong severity"},
        {"kind": "decision_reversed", "severity": "medium", "subject": "X", "note": "never happened"},
    ]
    write_case(tmp_path / "cases" / "tiny", labels)
    when = "tiny | 2026-10-20T00:00:00+00:00"
    assert failures(bench.evaluate(bench.load_cases(tmp_path / "cases"))) == {
        f"{when} | subjects | *": (["X"], ["X", "Y"]),
        f"{when} | state | X | status": ("rejected", "approved"),
        f"{when} | trail | e3 | outcome": ("applied", "unauthorized"),
        f"{when} | order | e2 < e1": (["e2", "e1"], ["e1", "e2"]),
        f"{when} | audit | unauthorized_attempts wu | severity": ("high", "medium"),
        f"{when} | audit | decision_reversed X | severity": ("medium", "not reported"),
    }
    out = runner.invoke(bench.app, ["--cases-dir", str(tmp_path / "cases")])
    assert out.exit_code == 1
    assert "| tiny | 2026-10-20T00:00:00+00:00 | state X status | rejected | approved | wrong on purpose |" in out.output


def test_unlabeled_findings_are_unexpected(tmp_path):
    labels = expected()
    labels["snapshots"][0]["audit"]["findings"] = []
    write_case(tmp_path / "cases" / "tiny", labels)
    assert failures(bench.evaluate(bench.load_cases(tmp_path / "cases"))) == {
        "tiny | 2026-10-20T00:00:00+00:00 | audit | * | unexpected findings":
            ([], ["unauthorized_attempts wu (medium)"]),
    }


def test_validation_labels_compare_the_error(tmp_path):
    root = write_case(tmp_path / "cases" / "tiny")
    forged = (root / "evidence.jsonl").read_bytes().replace(b'"sha256": "', b'"sha256": "0', 1)
    (root / "evidence.forged.jsonl").write_bytes(forged)
    (root / "evidence.missing.jsonl").write_bytes(
        (root / "evidence.jsonl").read_bytes().replace(b"sources/log.txt", b"sources/gone.txt"))
    labels = expected()
    labels["validation"] = [
        {"name": "hash", "evidence": "evidence.forged.jsonl", "error": "evidence hash mismatch: q1", "note": "-"},
        {"name": "wrong-error", "evidence": "evidence.forged.jsonl", "error": "quote does not match", "note": "-"},
        {"name": "should-load", "evidence": "evidence.forged.jsonl", "error": None, "note": "-"},
        {"name": "should-fail", "error": "anything", "note": "-"},
        {"name": "missing", "evidence": "evidence.missing.jsonl", "error": "No such file", "note": "-"},
    ]
    write_case(tmp_path / "other", labels)
    shutil.copyfile(root / "evidence.forged.jsonl", tmp_path / "other" / "evidence.forged.jsonl")
    shutil.copyfile(root / "evidence.missing.jsonl", tmp_path / "other" / "evidence.missing.jsonl")
    (case,) = bench.load_cases(tmp_path, ["other"])
    checks = {c.target: c for c in bench.measure(case) if c.kind == "validation"}
    assert checks["hash"].passed and checks["missing"].passed
    assert not checks["wrong-error"].passed and checks["wrong-error"].actual == "evidence hash mismatch: q1"
    assert not checks["should-load"].passed and not checks["should-fail"].passed
    assert checks["should-fail"].actual == "loads"
    missing = checks["missing"].actual
    assert "<case>" in missing and str(tmp_path) not in missing and "\n" not in missing


def test_check_lists_every_problem(tmp_path):
    labels = expected()
    snapshot = labels["snapshots"][0]
    snapshot["states"]["X"]["risks"] = ["e1"]
    snapshot["states"]["X"]["last_authorized_decision"] = "e1"
    snapshot["states"]["Nope"] = state("proposed")
    snapshot["trail"]["e9"] = {"outcome": "applied", "note": "-"}
    snapshot["audit"]["findings"].append({"kind": "decision_reversed", "severity": "low", "subject": "Ghost",
                                          "note": "-"})
    labels["snapshots"].append({"as_of": "2026-10-20T00:00:00Z", "order": ["e1", "e2"]})
    labels["validation"].append({"name": "escape", "evidence": "../outside.jsonl", "error": "x", "note": "-"})
    labels["validation"].append({"name": "absent", "policy": "policy.absent.json", "error": "x", "note": "-"})
    labels["validation"].append({"name": "main", "error": None, "note": "-"})
    write_case(tmp_path / "cases" / "tiny", labels)
    with pytest.raises(bench.BenchmarkError) as caught:
        bench.load_cases(tmp_path / "cases")
    message = str(caught.value)
    expected_problems = [
        "case tiny:",
        "validation: name 'main' is used twice",
        "validation 'escape': evidence path escapes its root",
        "validation 'absent': missing file policy.absent.json",
        "snapshots[1]: another snapshot has the same as_of",
        "snapshots[0].states: 'Nope' is not a subject of events.jsonl",
        "snapshots[0].states['X'].risks: 'e1' is not a raise_risk event on this subject",
        "snapshots[0].states['X'].last_authorized_decision: 'e1' is not an approve, reject or revoke event",
        "snapshots[0]: 'e9' is not an event id of events.jsonl",
        "snapshots[0].audit: 'Ghost' is not a subject of events.jsonl",
    ]
    for fragment in expected_problems:
        assert fragment in message
    assert message.count("\n  - ") == len(expected_problems) - 1
    out = runner.invoke(bench.app, ["--cases-dir", str(tmp_path / "cases"), "--check"])
    assert out.exit_code == 2 and "case tiny:" in out.output


@pytest.mark.parametrize("mutate, problem", [
    (lambda e: e["snapshots"][0].pop("as_of"), "an audit snapshot needs as_of"),
    (lambda e: e["snapshots"][0]["audit"]["findings"][0].update(subject="X"),
     "an unauthorized_attempts finding names an actor and no subject"),
    (lambda e: e["snapshots"][0]["audit"]["findings"].append(
        {"kind": "self_approval", "severity": "low", "subject": "X", "note": "-"}),
     "a self_approval finding names a subject and an actor"),
    (lambda e: e["snapshots"][0]["states"]["X"].pop("risks"), "states.X.risks: Field required"),
    (lambda e: e["snapshots"][0]["trail"]["e3"].update(outcome="ignored"), "trail.e3.outcome: Input should be"),
    (lambda e: e["snapshots"][0].update(order=["e1"]), "order lists at least two distinct event ids"),
    (lambda e: e["validation"][0].update(name="Main"), "name is lowercase words joined by hyphens"),
    (lambda e: e.update(description="two\nlines"), "description is one line"),
    (lambda e: e.update(description="two\rlines"), "description is one line"),
    (lambda e: e.update(description="two\u2028lines"), "description is one line"),
    (lambda e: e.update(description="bad\x1b[2J"), "description is one line"),
    (lambda e: e["validation"][0].update(note="bad\nnote"), "note is one line"),
    (lambda e: e.update(validation=[], snapshots=[]), "a case needs validation labels or snapshots"),
    (lambda e: e.update(extra=1), "extra: Extra inputs are not permitted"),
])
def test_label_schema_errors(tmp_path, mutate, problem):
    labels = expected()
    mutate(labels)
    write_case(tmp_path / "cases" / "tiny", labels)
    with pytest.raises(bench.BenchmarkError, match=re.escape(problem)):
        bench.load_cases(tmp_path / "cases")


def test_duplicate_keys_in_labels_are_refused(tmp_path):
    text = json.dumps(expected()).replace('"trail": {', '"trail": {"e3": {"outcome": "applied", "note": "-"}, ', 1)
    write_case(tmp_path / "cases" / "tiny", text)
    with pytest.raises(bench.BenchmarkError, match="duplicate key"):
        bench.load_cases(tmp_path / "cases")


def test_case_selection_errors(tmp_path):
    with pytest.raises(bench.BenchmarkError, match="no cases in"):
        bench.load_cases(tmp_path / "nowhere")
    write_case(tmp_path / "cases" / "tiny")
    with pytest.raises(bench.BenchmarkError, match=r"unknown case\(s\): nope \(available: tiny\)"):
        bench.load_cases(tmp_path / "cases", ["nope"])
    (tmp_path / "cases" / "empty").mkdir()
    with pytest.raises(bench.BenchmarkError, match="case empty:\n  - missing expected.json"):
        bench.load_cases(tmp_path / "cases")


def test_implementation_errors_fail_checks_instead_of_the_run(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    write_case(tmp_path / "cases" / "tiny")
    monkeypatch.setattr(bench, "replay", boom)
    monkeypatch.setattr(bench, "audit", boom)
    result = bench.evaluate(bench.load_cases(tmp_path / "cases"))
    assert result["totals"]["passed"] == 1  # only the validation label
    actual = {c["actual"] for c in result["checks"] if not c["passed"]}
    assert actual == {"replay failed: RuntimeError: boom", "audit failed: RuntimeError: boom"}
    (tmp_path / "cases" / "tiny" / "policy.json").write_bytes(b"{}")
    monkeypatch.undo()
    result = bench.evaluate(bench.load_cases(tmp_path / "cases"))
    assert result["totals"]["passed"] == 0
    first, *rest = result["checks"]
    assert first["kind"] == "validation" and "validation errors for Policy" in first["actual"]
    assert all(c["actual"] == f"dataset did not load: {first['actual']}" for c in rest)


def _columns(row: str) -> int:
    """Table columns, not counting a pipe that was escaped as part of a cell."""
    pipes, index = 0, 0
    while index < len(row):
        if row[index] == "\\":
            index += 2
            continue
        pipes += row[index] == "|"
        index += 1
    return pipes - 1


def test_variant_symlink_outside_the_case_is_not_read(tmp_path):
    root = write_case(tmp_path / "cases" / "tiny")
    secret = tmp_path / "secret.jsonl"
    secret.write_bytes(b"SECRET-TOKEN\n")
    (root / "evidence.linked.jsonl").symlink_to(secret)
    labels = expected()
    labels["validation"].append({"name": "linked", "evidence": "evidence.linked.jsonl", "error": "x", "note": "-"})
    (root / "expected.json").write_bytes(json.dumps(labels).encode("utf-8"))
    with pytest.raises(bench.BenchmarkError) as caught:
        bench.load_cases(tmp_path / "cases")
    message = str(caught.value)
    assert "evidence path escapes its root" in message
    assert "SECRET-TOKEN" not in message and secret.read_bytes() == b"SECRET-TOKEN\n"


def test_untrusted_text_stays_inert_in_markdown_and_json(tmp_path, windows_console):
    """Subjects, notes and descriptions are data: they cannot change the terminal, HTML or the table."""
    payload = "Evil\x1b[2J\u202e\n| <img src=x onerror=alert(1)> [forged](https://evil.invalid) **FORGED** \\|"
    markup = "\u202e <img src=x onerror=alert(1)> [forged](https://evil.invalid) **FORGED** \\|"
    root = write_case(tmp_path / "cases" / "tiny")
    events = [dict(event) for event in EVENTS]
    for event in events:
        if event["subject"] == "X":
            event["subject"] = payload
    (root / "events.jsonl").write_bytes("".join(json.dumps(event) + "\n" for event in events).encode("utf-8"))
    labels = expected()
    labels["description"] = "desc " + markup
    state = labels["snapshots"][0]["states"].pop("X")
    state["status"] = "rejected"
    state["note"] = "note " + markup
    labels["snapshots"][0]["states"][payload] = state
    (root / "expected.json").write_bytes(json.dumps(labels).encode("utf-8"))
    case, = bench.load_cases(tmp_path / "cases")
    result = bench.evaluate([case])
    failed = [c for c in result["checks"] if not c["passed"]]
    assert any(c["target"] == payload and c["expected"] == "rejected" and c["actual"] == "approved" for c in failed)
    report = bench.to_markdown(result)
    rendered = runner.invoke(bench.app, ["--cases-dir", str(tmp_path / "cases")])
    listed = runner.invoke(bench.app, ["--cases-dir", str(tmp_path / "cases"), "--json", "-"])
    assert rendered.exit_code == 1 and listed.exit_code == 1, listed.output
    assert windows_console[0].getvalue() == rendered.stdout_bytes + listed.stdout_bytes
    parsed = json.loads(listed.output)
    assert any(c["target"] == payload and c["actual"] == "approved" for c in parsed["checks"])
    for text in (report, rendered.output):
        assert "\x1b" not in text and "\u202e" not in text
        assert "<img" not in text and "**FORGED**" not in text and "[forged](https://evil.invalid)" not in text
        assert "FORGED" in text and "u001b" in text and "u202e" in text
    assert "\x1b" not in listed.output and "\u202e" not in listed.output
    assert "\\u001b" in listed.output and "\\u202e" in listed.output
    rows = [line for line in report.splitlines() if line.startswith("| tiny |")]
    assert rows and all(_columns(row) == 6 for row in rows)
    assert all("\x1b" not in line and "\u202e" not in line for line in report.splitlines())


def test_console_output_is_utf8_even_for_nonwestern_text(windows_console):
    for err in (False, True):
        bench._echo_utf8("北京\n", err=err)
        assert windows_console[int(err)].getvalue() == "北京\n".encode("utf-8")


def test_rejected_label_text_cannot_control_the_terminal(tmp_path):
    labels = expected()
    labels["description"] = "bad\x1b[2J\u202e"
    write_case(tmp_path / "cases" / "tiny", labels)
    out = runner.invoke(bench.app, ["--cases-dir", str(tmp_path / "cases"), "--check"])
    assert out.exit_code == 2
    assert "\x1b" not in out.output and "\u202e" not in out.output
    assert "description is one line" in out.output
