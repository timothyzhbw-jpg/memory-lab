"""`orgmemory export`: ADRs and CSV, file names, create-only writes outside the dataset, and escaping."""
import csv
import io
import json
import os
import re
import sys

import pytest
from typer.testing import CliRunner

from conftest import EXAMPLES, snapshot
from memcore import ManifestEntry, locate_quote
from orgmemory.cli import app
from orgmemory.export import CSV_COLUMNS, adr_markdown, decision_records, decisions_csv, file_name, slug
from orgmemory.replay import Dataset

runner = CliRunner()
EVERYONE = ["customer", "account_manager", "engineer", "product_manager"]
POLICY = {
    "actors": {"pm": ["product_manager"], "eng": ["engineer"], "cust": ["customer"], "sales": ["account_manager"]},
    "permissions": {"request": EVERYONE, "reconsider": EVERYONE, "comment": EVERYONE,
                    "raise_risk": ["engineer", "product_manager"], "dependency": ["engineer", "product_manager"],
                    "approve": ["product_manager"], "reject": ["product_manager"], "revoke": ["product_manager"]},
}
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{12}\.md")


def ev(id, day, actor, action, subject="S", **fields):
    return {"id": id, "timestamp": f"2026-10-{day:02d}T09:00:00Z", "actor": actor, "action": action,
            "subject": subject, "evidence_ids": ["e"], **fields}


def build(root, events, policy=POLICY, text="Synthetic note for the export tests.\n", quote="Synthetic note",
          name="note.txt", **entry_fields):
    """Write a dataset: one source file, one evidence record (id ``e`` unless overridden), policy, events."""
    root.mkdir(parents=True)
    (root / name).write_bytes(text.encode("utf-8"))
    span = locate_quote(root, name, quote)
    entry = {"id": "e", "path": span.path, "sha256": span.sha256, "quote": span.quote, "start": span.start,
             "end": span.end, "source_uri": "synthetic://note", "source_kind": "note",
             "captured_at": "2026-10-01T00:00:00Z", **entry_fields}
    ManifestEntry.model_validate(entry)
    (root / "evidence.jsonl").write_bytes((json.dumps(entry) + "\n").encode("utf-8"))
    (root / "policy.json").write_bytes(json.dumps(policy).encode("utf-8"))
    (root / "events.jsonl").write_bytes("".join(json.dumps(e) + "\n" for e in events).encode("utf-8"))
    return root


def export(root, *args):
    return runner.invoke(app, ["export", str(root), *args])


def files(directory):
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


MIXED = [ev("q", 1, "cust", "request", subject="Approved"), ev("a", 2, "pm", "approve", subject="Approved"),
         ev("r", 2, "pm", "reject", subject="Rejected"),
         ev("v1", 1, "pm", "approve", subject="Revoked"), ev("v2", 3, "pm", "revoke", subject="Revoked", supersedes="v1"),
         ev("p", 1, "cust", "request", subject="Open")]


# --- the examples ------------------------------------------------------------------------------------

@pytest.mark.parametrize("dataset", ["feature-a", "github-acme-web"])
def test_example_exports_match_the_golden_files(tmp_path, dataset):
    root, golden = EXAMPLES / "orgmemory" / dataset, EXAMPLES / "orgmemory" / "adr"
    before = snapshot(root)
    result = export(root, "--out", str(tmp_path / "adr"))
    assert result.exit_code == 0, result.output
    assert files(tmp_path / "adr") == files(golden / dataset)
    assert export(root, "--format", "csv", "--out", str(tmp_path / "d.csv")).exit_code == 0
    assert (tmp_path / "d.csv").read_bytes() == (golden / f"{dataset}.csv").read_bytes()
    assert export(root, "--format", "csv").output == (golden / f"{dataset}.csv").read_text(encoding="utf-8")
    assert snapshot(root) == before


def test_feature_a_record():
    subjects, (record,) = decision_records(Dataset.load(EXAMPLES / "orgmemory" / "feature-a"))
    markdown = adr_markdown(record)
    assert subjects == 1 and record.file_name == "feature-a-5481a84ec80d.md"
    assert (record.status, record.current_status, record.decision) == ("rejected", "rejected", "ev-wed-reject")
    assert markdown.startswith("# Feature A\n\nDate: 2026-10-07\n\n## Status\n\nRejected\n\n")
    assert [line for line in markdown.splitlines() if line.startswith("## ")] == [
        "## Status", "## Context", "## Decision", "## Consequences", "## Evidence"]
    assert "A reconsideration has been requested (`ev-thu-request`); it awaits a decision from product\\_manager." \
        in markdown
    assert ("- `ev-wed-reject` · 2026-10-07T15:30:00+00:00 · zhou (roles: product\\_manager) · **rejected** · "
            "evidence `wed`") in markdown
    assert "`request` (after the decision) · evidence `thu`" in markdown
    assert "- `ev-thu-sales-approve` · 2026-10-08T11:20:00+00:00 · wu (roles: account\\_manager) · `approve`" in markdown
    # Every cited span: id, location, characters, the full hash, the source URI and the exact quote.
    assert ("- `wed` · `sources/2026-10-07-decision-log.txt:3` · characters 57–138 · sha256 "
            "`fb85f034a99fde2c00e7173add0e9ab03bc23283d8a9983c3b6cac99f59ea21f` · source "
            "`synthetic://decision-log/2026-10-07` · decision\\_log · captured 2026-10-07T15:30:00+00:00 · cited by "
            "`ev-wed-reject`\n  > Feature A is rejected for the November release until the billing migration lands.\n"
            ) in markdown
    assert markdown.count("\n  > ") == 5 and markdown.endswith("*\n")


# --- which subjects, which status ----------------------------------------------------------------------

def test_one_record_per_subject_with_an_authorized_decision(tmp_path):
    subjects, records = decision_records(Dataset.load(build(tmp_path / "data", MIXED)))
    assert subjects == 4
    assert [(r.subject, r.status, r.current_status, r.decision) for r in records] == [
        ("Approved", "accepted", "approved", "a"), ("Revoked", "proposed", "proposed", "v2"),
        ("Rejected", "rejected", "rejected", "r")]
    revoked = adr_markdown(records[1])
    assert "\n\nProposed\n\n" in revoked and "It awaits a decision from product\\_manager, open since `v2`." in revoked
    assert "· **revoked** `v1` (`approve` by pm at 2026-10-01T09:00:00+00:00) ·" in revoked
    assert "Earlier authorized decisions on this subject, oldest first:\n\n- `v1` ·" in revoked
    assert "No decision is in force: the next authorized `approve`" in revoked
    assert "The subject stays accepted until an authorized `reject` (roles: product\\_manager) or a `revoke` of `a`" \
        in adr_markdown(records[0])


def test_context_dependencies_risks_and_conflicts(tmp_path):
    events = [ev("q", 1, "cust", "request"), ev("k", 2, "eng", "raise_risk", note="Billing is mid-migration"),
              ev("d", 2, "eng", "dependency", dependency="Billing"), ev("d2", 2, "eng", "dependency", dependency="Gone"),
              ev("x", 3, "pm", "reject"), ev("y", 4, "pm", "approve"), ev("k2", 5, "eng", "raise_risk", note="Late"),
              ev("s", 6, "sales", "reject"), ev("b", 1, "pm", "reject", subject="Billing")]
    _, records = decision_records(Dataset.load(build(tmp_path / "data", events)))
    record = next(r for r in records if r.subject == "S")
    markdown = adr_markdown(record)
    assert record.context == ["q", "k", "k2"] and record.earlier_decisions == ["x"]
    assert "`raise_risk`: Billing is mid-migration · evidence `e`" in markdown
    assert "`raise_risk`: Late (after the decision)" in markdown
    assert "- Billing, which is rejected; declared by `d`" in markdown
    assert "- Gone, which has no events in this log; declared by `d2`" in markdown
    assert "- `s` · 2026-10-06T09:00:00+00:00 · sales (roles: account\\_manager) · `reject` (after the decision)" in markdown
    assert "- `decision_reversed` (`x`, `y`): reject by pm then approve by pm" in markdown
    # One evidence entry, cited by every event the record names, each once.
    assert markdown.count("\n  > Synthetic note\n") == 1
    assert "cited by `q`, `k`, `d`, `d2`, `x`, `y`, `k2`, `s`\n" in markdown


def test_records_and_files_are_deterministic(tmp_path):
    root = build(tmp_path / "data", MIXED)
    for out in ("one", "two"):
        assert export(root, "--out", str(tmp_path / out)).exit_code == 0
    assert files(tmp_path / "one") == files(tmp_path / "two")
    assert all(NAME.fullmatch(name) for name in files(tmp_path / "one"))


# --- file names --------------------------------------------------------------------------------------

@pytest.mark.parametrize("subject, prefix", [
    ("Feature A", "feature-a-"), ("acme/web#12", "acme-web-12-"), ("Café crème", "cafe-creme-"),
    ("功能 A", "a-"), ("功能", "subject-"), ("../../etc/passwd", "etc-passwd-"), ("CON", "con-"),
    ('a<b>c:d"e|f?g*h\\i', "a-b-c-d-e-f-g-h-i-"), ("x" * 300, "x" * 48 + "-"), ("-- --", "subject-"),
])
def test_file_names_are_portable(subject, prefix):
    name = file_name(subject)
    assert name.startswith(prefix) and NAME.fullmatch(name) and len(name) <= 48 + 1 + 12 + 3
    assert name == file_name(subject)


def test_subjects_with_the_same_slug_get_different_files():
    assert slug("Feature A") == slug("feature-a") == "feature-a"
    assert file_name("Feature A") != file_name("feature-a")


# --- writing: create-only, outside the dataset -----------------------------------------------------------

def test_adr_needs_out(tmp_path):
    result = export(build(tmp_path / "data", MIXED))
    assert result.exit_code == 2 and "--format adr needs --out DIR" in result.output


def test_create_only_refuses_before_writing_anything(tmp_path):
    root, out = build(tmp_path / "data", MIXED), tmp_path / "adr"
    first = export(root, "--out", str(out))
    assert first.exit_code == 0 and first.output.count("wrote ") == 3
    assert "exported 3 decision(s) of 4 subject(s), as of end of log" in first.output
    names = sorted(files(out))
    (out / names[0]).unlink()
    (out / names[1]).write_bytes(b"edited by hand\n")
    before = snapshot(out)
    result = export(root, "--out", str(out))
    assert result.exit_code == 2 and "refusing to overwrite 2 existing file(s)" in result.output
    assert "--force" in result.output and snapshot(out) == before  # the missing file was not created either


def test_force_replaces_regular_files(tmp_path):
    root, out = build(tmp_path / "data", MIXED), tmp_path / "adr"
    assert export(root, "--out", str(out)).exit_code == 0
    expected = files(out)
    name = sorted(expected)[0]
    (out / name).write_bytes(b"edited by hand\n")
    (out / "notes.md").write_bytes(b"mine\n")
    assert export(root, "--out", str(out), "--force").exit_code == 0
    assert files(out) == {**expected, "notes.md": b"mine\n"}


def test_csv_file_is_create_only_too(tmp_path):
    root, target = build(tmp_path / "data", MIXED), tmp_path / "out" / "decisions.csv"
    assert export(root, "--format", "csv", "--out", str(target)).exit_code == 0
    target.write_bytes(b"mine\n")
    result = export(root, "--format", "csv", "--out", str(target))
    assert result.exit_code == 2 and target.read_bytes() == b"mine\n"
    assert export(root, "--format", "csv", "--out", str(target), "--force").exit_code == 0
    assert target.read_text(encoding="utf-8") == decisions_csv(decision_records(Dataset.load(root))[1])


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs extra privileges on Windows")
def test_force_never_replaces_or_follows_a_symlink(tmp_path):
    root, out = build(tmp_path / "data", MIXED), tmp_path / "adr"
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"keep\n")
    out.mkdir()
    (out / file_name("Approved")).symlink_to(victim)
    result = export(root, "--out", str(out), "--force")
    assert result.exit_code == 2 and "not a regular file" in result.output
    assert victim.read_bytes() == b"keep\n" and sorted(files(out)) == [file_name("Approved")]


def test_force_never_writes_through_a_hard_link(tmp_path):
    root, out = build(tmp_path / "data", MIXED), tmp_path / "adr"
    out.mkdir()
    try:
        os.link(root / "events.jsonl", out / file_name("Approved"))
    except OSError:
        pytest.skip("hard links are not supported here")
    before = snapshot(root)
    assert export(root, "--out", str(out), "--force").exit_code == 0
    assert snapshot(root) == before
    assert (out / file_name("Approved")).read_bytes().startswith(b"# Approved\n")


@pytest.mark.parametrize("target", ["", "adr", "sources/adr", "sources/../adr", "sources/../../data/adr"])
def test_never_writes_into_the_dataset(tmp_path, target):
    root = build(tmp_path / "data", MIXED)
    (root / "sources").mkdir()
    before = snapshot(tmp_path)
    for args in (["--out", str(root / target)], ["--format", "csv", "--out", str(root / target / "d.csv")]):
        result = export(root, *args)
        assert result.exit_code == 2 and "inside the dataset directory" in result.output
        assert snapshot(tmp_path) == before
    assert not (root / "adr").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs extra privileges on Windows")
def test_a_symlink_cannot_lead_into_the_dataset(tmp_path):
    root = build(tmp_path / "data", MIXED)
    (tmp_path / "link").symlink_to(root, target_is_directory=True)
    (tmp_path / "events-link.csv").symlink_to(root / "events.jsonl")
    before = snapshot(root)
    for args in (["--out", str(tmp_path / "link")], ["--out", str(tmp_path / "link" / "adr")],
                 ["--format", "csv", "--out", str(tmp_path / "events-link.csv"), "--force"]):
        result = export(root, *args)
        assert result.exit_code == 2 and "inside the dataset directory" in result.output
    assert snapshot(root) == before


def test_case_variant_of_the_dataset_is_the_dataset(tmp_path):
    root = build(tmp_path / "data", MIXED)
    alias = tmp_path / "DATA"
    if not alias.exists():
        pytest.skip("the file system is case-sensitive")
    result = export(root, "--out", str(alias / "adr"))
    assert result.exit_code == 2 and "inside the dataset directory" in result.output
    assert not (root / "adr").exists()


def test_out_must_be_a_directory(tmp_path):
    root = build(tmp_path / "data", MIXED)
    (tmp_path / "file").write_bytes(b"x")
    result = export(root, "--out", str(tmp_path / "file"))
    assert result.exit_code == 2 and "not a directory" in result.output


def test_invalid_dataset_exits_2_and_writes_nothing(tmp_path):
    root = build(tmp_path / "data", MIXED)
    (root / "note.txt").write_bytes(b"Tampered note.\n")
    for args in (["--out", str(tmp_path / "adr")], ["--format", "csv", "--out", str(tmp_path / "d.csv")]):
        result = export(root, *args)
        assert result.exit_code == 2 and "hash mismatch" in result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data"]


# --- --as-of -----------------------------------------------------------------------------------------

def test_as_of(tmp_path):
    root = EXAMPLES / "orgmemory" / "feature-a"
    result = export(root, "--out", str(tmp_path / "wed"), "--as-of", "2026-10-07T23:59:59Z")
    assert result.exit_code == 0 and "as of 2026-10-07T23:59:59+00:00" in result.output
    (markdown,) = (p.read_text(encoding="utf-8") for p in (tmp_path / "wed").iterdir())
    assert "replayed as of 2026-10-07T23:59:59+00:00 (3 of 5 events)" in markdown
    assert "reconsideration" not in markdown and "ev-thu" not in markdown
    early = export(root, "--out", str(tmp_path / "mon"), "--as-of", "2026-10-06T00:00:00Z")
    assert early.exit_code == 0 and "no subject has an authorized decision as of 2026-10-06T00:00:00+00:00" in early.output
    assert not (tmp_path / "mon").exists()
    assert export(root, "--format", "csv", "--as-of", "2026-10-06T00:00:00Z").output == ",".join(CSV_COLUMNS) + "\n"
    csv_wed = export(root, "--format", "csv", "--as-of", "2026-10-07T23:59:59Z").output
    assert csv_wed.splitlines()[1].endswith(",2026-10-07T23:59:59+00:00")


@pytest.mark.parametrize("value, message", [("2026-10-07", "timezone"), ("yesterday", "datetime")])
def test_invalid_as_of_exits_2(tmp_path, value, message):
    result = export(EXAMPLES / "orgmemory" / "feature-a", "--out", str(tmp_path / "adr"), "--as-of", value)
    assert result.exit_code == 2 and message in result.output.lower() and not (tmp_path / "adr").exists()


# --- untrusted text ------------------------------------------------------------------------------------

PAYLOAD = 'x` <img src=x onerror=alert(1)> [forged](https://evil.invalid) **FORGED** ## H'


def hostile(tmp_path, field, payload=PAYLOAD):
    """The MIXED-like log with ``payload`` in one field of every event or of the evidence record."""
    events = [ev("q", 1, "cust", "request", note="n"), ev("k", 1, "eng", "raise_risk", note="risk"),
              ev("d", 1, "eng", "dependency", dependency="D"), ev("a", 2, "pm", "approve"),
              ev("s", 3, "sales", "reject")]
    policy = json.loads(json.dumps(POLICY))
    entry = {}
    text, quote, name = "Synthetic note for the export tests.\n", "Synthetic note", "note.txt"
    if field in ("subject", "note", "dependency"):
        for event in events:
            if field in event or field == "subject":
                event[field] = payload
    elif field == "actor":
        events[3]["actor"] = payload
        policy["actors"][payload] = ["product_manager"]
    elif field == "role":
        policy["actors"]["pm"] = [payload]
        policy["permissions"]["approve"] = [payload]
    elif field == "event_id":
        events[3]["id"] = payload
    elif field == "evidence_id":
        entry["id"] = payload
        for event in events:
            event["evidence_ids"] = [payload]
    elif field == "path":
        name = payload.replace("/", "_")
    elif field in ("source_uri", "source_kind"):
        entry[field] = payload
    elif field == "quote":
        text = quote = payload
    return build(tmp_path / field, events, policy=policy, text=text, quote=quote, name=name, **entry)


def tokens(markdown):
    from markdown_it import MarkdownIt
    parsed = MarkdownIt().parse(markdown)
    return parsed, [child for token in parsed for child in (token.children or [])]


FIELDS = ["subject", "actor", "role", "note", "dependency", "event_id", "evidence_id",
          pytest.param("path", marks=pytest.mark.skipif(sys.platform == "win32",
                                                        reason="the hostile file name uses characters Windows forbids")),
          "source_uri", "source_kind", "quote"]


@pytest.mark.parametrize("field", FIELDS)
def test_untrusted_fields_render_as_literal_text(tmp_path, field):
    baseline_blocks, baseline_inline = tokens(adr_markdown(decision_records(
        Dataset.load(hostile(tmp_path / "plain", field, "plain")))[1][0]))
    (record,) = decision_records(Dataset.load(hostile(tmp_path, field)))[1]
    blocks, inline = tokens(adr_markdown(record))
    assert [t.type for t in blocks] == [t.type for t in baseline_blocks]
    assert not {t.type for t in inline} & {"html_inline", "link_open", "image"}
    assert sum(t.type == "strong_open" for t in inline) == sum(t.type == "strong_open" for t in baseline_inline)
    literal = PAYLOAD.replace("/", "_") if field == "path" else PAYLOAD
    assert any(literal in t.content for t in inline if t.type in ("text", "code_inline"))


@pytest.mark.parametrize("payload", ["# HIGH forged", "- forged", "1. forged", "---", "```html", "    indented",
                                     "<h3>forged</h3>", "&#10;# forged", "line\n## Decision\nforged", "> quoted",
                                     "Evil\x1b[2J‮ x"])
def test_untrusted_text_cannot_add_blocks_or_terminal_codes(tmp_path, payload):
    for index, field in enumerate(("subject", "note", "quote")):
        root = hostile(tmp_path / str(index), field, payload)
        baseline = tokens(adr_markdown(decision_records(Dataset.load(hostile(tmp_path / f"p{index}", field, "plain")))[1][0]))[0]
        markdown = adr_markdown(decision_records(Dataset.load(root))[1][0])
        assert [t.type for t in tokens(markdown)[0]] == [t.type for t in baseline], (field, payload)
        assert not set(markdown) & {"\x1b", "‮", " "}
        assert [line for line in markdown.splitlines() if line.startswith("## ")] == [
            "## Status", "## Context", "## Decision", "## Consequences", "## Evidence"]


# --- CSV ---------------------------------------------------------------------------------------------

def test_csv_columns_and_lossless_lists(tmp_path):
    note = "risk \x1b[2J ‮ \U000e0001   end"
    events = [ev("k", 1, "eng", "raise_risk", note=note, subject="=HYPERLINK(\"https://evil.invalid\")"),
              ev("a", 2, "pm", "approve", subject="=HYPERLINK(\"https://evil.invalid\")"),
              ev("r", 2, "pm", "reject", subject="+cmd"), ev("t", 2, "pm", "reject", subject="tab\there"),
              ev("m", 2, "pm", "reject", subject="-1"), ev("w", 2, "pm", "reject", subject="@SUM(A1)")]
    text = decisions_csv(decision_records(Dataset.load(build(tmp_path / "data", events)))[1])
    lines = text.split("\n")
    assert lines[-1] == "" and len(lines) == 6 + 1  # header, five rows, final newline: one line per row
    assert not set(text) & {"\x1b", "‮", "\U000e0001", " ", "\t", "\r"}
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert [row["subject"] for row in rows] == ["'=HYPERLINK(\"https://evil.invalid\")", "'+cmd", "tab\\u0009here",
                                                "'-1", "'@SUM(A1)"]
    first = rows[0]
    assert json.loads(first["open_risks"]) == [note]  # JSON list cells decode to the exact text
    assert (first["status"], first["current_status"], first["decision_event"], first["decided_by"]) == (
        "accepted", "approved", "a", "pm")
    assert json.loads(first["decided_by_roles"]) == ["product_manager"]
    assert json.loads(first["evidence_quotes"]) == ["Synthetic note"]
    assert first["adr_file"] == file_name('=HYPERLINK("https://evil.invalid")') and first["as_of"] == ""


def test_csv_goes_to_stdout_without_out(tmp_path):
    root = build(tmp_path / "data", MIXED)
    before = snapshot(tmp_path)
    result = export(root, "--format", "csv")
    assert result.exit_code == 0 and result.output == decisions_csv(decision_records(Dataset.load(root))[1])
    assert snapshot(tmp_path) == before
