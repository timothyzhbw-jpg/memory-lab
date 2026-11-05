"""The real-repository benchmark: its labels' format, offline. CI has no clones, so the scoring runs on a made-up one."""
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import write_text_exact

REPO = Path(__file__).resolve().parent.parent
WILD = REPO / "benchmarks" / "memfalsify" / "wild"
_spec = importlib.util.spec_from_file_location("memfalsify_wild_benchmark", WILD / "run.py")
wild = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = wild
_spec.loader.exec_module(wild)
runner = CliRunner()


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_labels_hold_metadata_and_hashes_only():
    rows = [json.loads(line) for line in (WILD / "labels.jsonl").read_bytes().decode("utf-8").splitlines()]
    allowed = {"id", "repo", "sha", "memory_file", "location", "source_path", "source_line", "claim_sha256",
               "quote_sha256", "rule", "label", "why", "tier"}
    assert rows and all(set(row) - {"claim_dropped"} == allowed for row in rows)
    for row in rows:
        assert re.fullmatch(r"[0-9a-f]{64}", row["claim_sha256"]) and re.fullmatch(r"[0-9a-f]{64}", row["quote_sha256"])
        for name in ("repo", "memory_file", "location", "source_path"):  # names, not sentences
            assert " " not in row[name], row


def test_labels_load_and_stay_in_a_fixed_order():
    rows = wild.load_labels()
    assert len({row.id for row in rows}) == len(rows)
    keys = [(row.repo, row.memory_file, row.location, row.source_path, row.source_line or 0, row.id) for row in rows]
    assert keys == sorted(keys)
    assert {row.label for row in rows} == {"real", "not_real"}
    assert {row.rule for row in rows} == {"negation", "opposing", "numbers"}
    assert all(row.label != "real" or row.why == "real" for row in rows)


def test_labels_file_has_exact_lines():
    raw = (WILD / "labels.jsonl").read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw


def test_check_needs_no_clones():
    shown = runner.invoke(wild.app, ["--check"])
    assert shown.exit_code == 0 and "rows" in shown.stdout


@pytest.mark.parametrize("line", ['{"id": "x"}', "not json"])
def test_a_bad_row_is_refused(tmp_path, line):
    bad = tmp_path / "labels.jsonl"
    write_text_exact(bad, line + "\n")
    assert runner.invoke(wild.app, ["--check", "--labels", str(bad)]).exit_code != 0


def test_extra_text_in_a_row_is_refused(tmp_path):
    row = json.loads((WILD / "labels.jsonl").read_bytes().decode("utf-8").splitlines()[0])
    bad = tmp_path / "labels.jsonl"
    write_text_exact(bad, json.dumps({**row, "quote": "third-party text"}) + "\n")
    assert runner.invoke(wild.app, ["--check", "--labels", str(bad)]).exit_code != 0


MEMORY = """# Facts

- retry_limit (default: 3): How many times a call may be retried.
- The cache holds 100 entries for each tenant.
- Tests live in the suite directory for this project.
"""
SOURCES = {
    "docs/a.md": "retry_limit: int = 5\nThe cache holds 100 entries for each tenant.\n",
    "docs/b.md": "Tests live in the suite directory of this project.\n",
    "tests/test_c.py": "retry_limit = 9  # How many times a call may be retried.\n",
    "docs/d.md": "The cache holds 200 entries for each tenant.\n",
}


def row(id_, claim, path, line, quote, label, rule="numbers"):
    return {"id": id_, "repo": "acme/widgets", "sha": "a" * 40, "memory_file": "MEMORY.md", "location": "MEMORY.md:3",
            "source_path": path, "source_line": line, "claim_sha256": sha(claim), "quote_sha256": sha(quote),
            "rule": rule, "label": label, "why": "real" if label == "real" else "different_subject", "tier": "docs"}


@pytest.fixture
def clones(tmp_path):
    clone = tmp_path / "clones" / "acme__widgets"
    clone.mkdir(parents=True)
    write_text_exact(clone / "MEMORY.md", MEMORY)
    for name, text in SOURCES.items():
        (clone / name).parent.mkdir(parents=True, exist_ok=True)
        write_text_exact(clone / name, text)
    (clone / ".git" / "refs" / "heads").mkdir(parents=True)
    write_text_exact(clone / ".git" / "HEAD", "ref: refs/heads/main\n")
    write_text_exact(clone / ".git" / "refs" / "heads" / "main", "a" * 40 + "\n")
    return tmp_path / "clones"


@pytest.fixture
def labels(tmp_path):
    named = "retry_limit (default: 3): How many times a call may be retried."
    rows = [row("0000000000000001", named, "docs/a.md", 1, "retry_limit: int = 5", "real"),
            row("0000000000000002", "The cache holds 100 entries for each tenant.", "docs/d.md", 1,
                "The cache holds 200 entries for each tenant.", "not_real"),
            row("0000000000000003", "Tests live in the suite directory for this project.", "docs/b.md", 1,
                "Tests live in the suite directory of this project.", "real", "negation"),
            row("0000000000000004", "A line that is not in the memory file at all.", "docs/a.md", 2,
                "The cache holds 100 entries for each tenant.", "not_real")]
    path = tmp_path / "labels.jsonl"
    write_text_exact(path, "".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_scoring_on_a_made_up_clone(clones, labels, tmp_path):
    out = tmp_path / "out.json"
    shown = runner.invoke(wild.app, ["--clones", str(clones), "--labels", str(labels), "--json", str(out)])
    assert shown.exit_code == 0, shown.output
    result = json.loads(out.read_bytes().decode("utf-8"))
    labeled = result["labeled"]
    assert labeled["total"]["labeled"] == 4 and labeled["total"]["labeled_real"] == 2
    assert labeled["total"]["real"] == 1 and labeled["total"]["not_real"] == 1
    assert labeled["precision"] == 0.5
    assert [r["id"] for r in labeled["real_lost"]] == ["0000000000000003"]
    assert labeled["unresolved"] == ["0000000000000004"]
    assert result["stale_commits"] == []
    assert result["audits"]["flagged_pairs"] == 2 and result["audits"]["with_a_label"] == 2
    assert result["audits"]["without_a_label"] == 0
    assert "Real rows lost: 1" in shown.stdout


def test_no_audit_skips_the_full_audits(clones, labels, tmp_path):
    out = tmp_path / "out.json"
    assert runner.invoke(wild.app, ["--clones", str(clones), "--labels", str(labels), "--no-audit",
                                    "--json", str(out)]).exit_code == 0
    assert json.loads(out.read_bytes().decode("utf-8"))["audits"] is None


def test_a_clone_at_another_commit_is_named(clones, labels, tmp_path):
    write_text_exact(clones / "acme__widgets" / ".git" / "refs" / "heads" / "main", "b" * 40 + "\n")
    shown = runner.invoke(wild.app, ["--clones", str(clones), "--labels", str(labels), "--no-audit"])
    assert "commits that differ from the labeled ones: 1" in shown.stdout


def test_clones_are_required_unless_checking():
    assert runner.invoke(wild.app, []).exit_code == 2


def test_the_holdout_labels_hold_metadata_and_hashes_only():
    rows = wild.load_labels(wild.HOLDOUT)
    assert len(rows) == 282 and sum(row.label == "real" for row in rows) == 1
    raw = wild.HOLDOUT.read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw
    assert all(set(json.loads(line)) <= set(wild.Row.model_fields) for line in raw.decode("utf-8").splitlines())
    assert not {row.repo for row in rows} & {row.repo for row in wild.load_labels()}
