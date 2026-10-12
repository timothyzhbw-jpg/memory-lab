"""Effective-dated roles: policy-log.jsonl, policy.json as_of, and how replay, explain, audit and the CLI use them."""
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from conftest import EXAMPLES, snapshot
from memcore import EvidenceError, ManifestEntry, locate_quote
from orgmemory.audit import KINDS, audit, report_kinds
from orgmemory.authority import POLICY_LOG
from orgmemory.cli import app
from orgmemory.models import Event, Policy, PolicyChange
from orgmemory.replay import Dataset, replay, validation_summary
from orgmemory.report import audit_markdown, explain, to_markdown

runner = CliRunner()
EVERYONE = ["customer", "account_manager", "engineer", "product_manager"]
POLICY = {
    "actors": {"pm": ["product_manager"], "eng": ["engineer"], "cust": ["customer"], "sales": ["account_manager"],
               "admin": ["org_admin"]},
    "permissions": {"request": EVERYONE, "reconsider": EVERYONE, "comment": EVERYONE,
                    "raise_risk": ["engineer", "product_manager"], "dependency": ["engineer", "product_manager"],
                    "approve": ["product_manager"], "reject": ["product_manager"], "revoke": ["product_manager"],
                    "manage_roles": ["org_admin"]},
}
DATED_EXAMPLE = EXAMPLES / "orgmemory" / "feature-a-dated"
UNDATED_CLI = json.loads((Path(__file__).parent / "fixtures/orgmemory/undated-cli-main.json").read_text())


def t(day, hour=9):
    return f"2026-10-{day:02d}T{hour:02d}:00:00Z"


def at(day, hour=9):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


def ev(id, day, actor, action, subject="S", hour=9, **fields):
    return {"id": id, "timestamp": t(day, hour), "actor": actor, "action": action, "subject": subject,
            "evidence_ids": ["e"], **fields}


def grant(id, day, subject_actor, role="product_manager", actor="admin", hour=9, **fields):
    return {"id": id, "timestamp": t(day, hour), "actor": actor, "action": "grant", "subject_actor": subject_actor,
            "role": role, "evidence_ids": ["e"], **fields}


def revoke(id, day, subject_actor, role="product_manager", actor="admin", hour=9, **fields):
    return {**grant(id, day, subject_actor, role, actor, hour, **fields), "action": "revoke_role"}


def lines(records):
    return "".join(json.dumps(r) + "\n" for r in records).encode("utf-8")


def build(root, events, changes=None, policy=POLICY):
    """A minimal dataset; ``changes=None`` writes no policy-log.jsonl at all."""
    root.mkdir(parents=True)
    (root / "note.txt").write_bytes(b"Synthetic note for the dated-policy tests.\n")
    span = locate_quote(root, "note.txt", "Synthetic note")
    entry = ManifestEntry(id="e", path=span.path, sha256=span.sha256, quote=span.quote, start=span.start,
                          end=span.end, source_uri="synthetic://note", source_kind="note",
                          captured_at="2026-10-01T00:00:00Z")
    (root / "evidence.jsonl").write_bytes(lines([entry.model_dump(mode="json")]))
    (root / "policy.json").write_bytes(json.dumps(policy).encode("utf-8"))
    (root / "events.jsonl").write_bytes(lines(events))
    if changes is not None:
        (root / POLICY_LOG).write_bytes(lines(changes))
    return root


def load(tmp_path, events, changes=None, policy=POLICY):
    return Dataset.load(build(tmp_path / "data", events, changes, policy))


def outcomes(result):
    return {e.event_id: (e.outcome, e.roles) for e in result.audit_log}


# --- backward compatibility -------------------------------------------------------------------------

@pytest.mark.parametrize("case", UNDATED_CLI["cases"], ids=lambda c: c["dataset"] + " " + " ".join(c["args"]))
def test_undated_cli_bytes_match_main(case, windows_console):
    """Digests captured by running the code without dated roles (revision in the fixture), not the new
    implementation. That revision is the integration branch just before dated-policy: release 0.3's output,
    plus the ``media`` field that sidecar evidence added to every JSON evidence span."""
    args = case["args"]
    result = runner.invoke(app, [args[0], str(EXAMPLES / "orgmemory" / case["dataset"]), *args[1:]])
    assert result.exit_code == case["exit_code"]
    assert hashlib.sha256(result.stdout_bytes).hexdigest() == case["sha256"]
    # Also exercise the Windows encoder/newline layer on POSIX. Keep hashing raw
    # bytes; normalizing result.output would hide a change to the golden output.
    assert windows_console[0].getvalue() == result.stdout_bytes


@pytest.mark.parametrize("name, replay_golden, audit_golden", [
    ("feature-a", "replay-report.md", "audit-report.md"),
    ("github-acme-web", "github-acme-web/replay-report.md", "github-acme-web/audit-report.md"),
])
def test_examples_without_a_policy_log_are_unchanged(name, replay_golden, audit_golden):
    data = Dataset.load(EXAMPLES / "orgmemory" / name)
    assert data.policy_log is None and not data.authority.dated
    result = replay(data)
    assert to_markdown(result) + "\n" == (EXAMPLES / "orgmemory" / replay_golden).read_text(encoding="utf-8")
    report = audit(data, at(31, 0))
    assert audit_markdown(report) == (EXAMPLES / "orgmemory" / audit_golden).read_text(encoding="utf-8")
    # The JSON keeps its old shape: no dated fields and no dated kinds.
    assert "dated_policy" not in json.loads(result.model_dump_json())
    as_json = json.loads(report.model_dump_json())
    assert "dated_policy" not in as_json and list(as_json["kinds"]) == list(report_kinds(False))
    assert all("role_changes" not in finding for finding in as_json["findings"])
    assert "role_changed_after_decision" not in audit_markdown(report)


def test_only_the_dated_example_has_a_policy_log():
    logs = sorted(p.parent.name for p in (EXAMPLES / "orgmemory").rglob(POLICY_LOG))
    assert logs == ["feature-a-dated"]


def test_undated_roles_match_the_plain_policy(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("x", 2, "sales", "approve"), ev("a", 3, "pm", "approve")]
    plain = replay(load(tmp_path / "plain", events))
    # An empty policy log dates the policy without changing any outcome.
    empty = replay(load(tmp_path / "empty", events, changes=[]))
    assert empty.states == plain.states and outcomes(empty) == outcomes(plain)
    assert empty.dated_policy.role_changes == [] and plain.dated_policy is None


# --- replay uses the roles held at each event's time --------------------------------------------------

def test_a_later_grant_does_not_authorize_an_earlier_event(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("x", 2, "sales", "approve"), ev("y", 4, "sales", "approve")]
    result = replay(load(tmp_path, events, [grant("g", 3, "sales")]))
    assert outcomes(result)["x"] == ("unauthorized", ["account_manager"])
    assert outcomes(result)["y"] == ("applied", ["account_manager", "product_manager"])
    state = result.states["S"]
    assert state.current_status == "approved" and state.last_authorized_decision.event_id == "y"
    assert state.last_authorized_decision.roles == ["account_manager", "product_manager"]


def test_a_later_revoke_role_keeps_an_earlier_decision(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("j", 2, "pm", "reject"), ev("a", 5, "pm", "approve")]
    result = replay(load(tmp_path, events, [revoke("v", 3, "pm")]))
    state = result.states["S"]
    assert state.current_status == "rejected" and state.last_authorized_decision.event_id == "j"
    assert state.last_authorized_decision.roles == ["product_manager"]
    assert outcomes(result)["a"] == ("unauthorized", [])
    assert [c.event_id for c in state.pending_changes] == ["a"]
    assert state.pending_changes[0].reason == "actor roles none may not approve"


def test_editing_policy_json_instead_rewrites_history(org_demo):
    """Why the log exists: putting the same grant into policy.json changes Thursday's outcome."""
    data = Dataset.load(org_demo)
    promoted = data.policy.model_copy(update={"actors": {**data.policy.actors,
                                                         "wu": ["account_manager", "product_manager"]}})
    assert replay(Dataset(data.events, promoted, data.evidence)).states["Feature A"].current_status == "approved"
    dated = Dataset.load(DATED_EXAMPLE)
    assert dated.authority.roles("wu") == ["account_manager", "product_manager"]
    assert replay(dated).states["Feature A"].current_status == "rejected"


def test_same_time_change_applies_to_events_at_that_time(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("x", 3, "sales", "approve"), ev("j", 4, "pm", "reject")]
    result = replay(load(tmp_path, events, [grant("g", 3, "sales"), revoke("v", 4, "pm")]))
    assert outcomes(result)["x"][0] == "applied" and outcomes(result)["j"][0] == "unauthorized"


def test_same_time_changes_apply_in_file_order(tmp_path):
    # admin makes sales a manager, who then grants eng a role, at the same effective time.
    chained = [grant("g1", 3, "sales", role="org_admin"), grant("g2", 3, "eng", actor="sales")]
    data = load(tmp_path / "ok", [ev("x", 3, "eng", "approve")], chained)
    assert outcomes(replay(data))["x"] == ("applied", ["engineer", "product_manager"])
    with pytest.raises(ValueError, match="g2 .*sales \\(roles: account_manager\\) may not manage_roles"):
        load(tmp_path / "bad", [ev("x", 3, "eng", "approve")], list(reversed(chained)))


def test_changes_are_ordered_by_effective_time_not_file_order(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("x", 4, "sales", "approve"), ev("y", 6, "sales", "reject")]
    changes = [grant("g", 3, "sales"), revoke("v", 5, "sales")]
    forward = replay(load(tmp_path / "a", events, changes))
    backward = replay(load(tmp_path / "b", events, list(reversed(changes))))
    assert outcomes(forward) == outcomes(backward)
    assert [c.change_id for c in backward.dated_policy.role_changes] == ["g", "v"]
    assert outcomes(forward)["x"][0] == "applied" and outcomes(forward)["y"][0] == "unauthorized"


def test_as_of_cutoff_applies_changes_up_to_and_including_it(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("x", 3, "sales", "approve"), ev("y", 5, "sales", "reject")]
    data = load(tmp_path, events, [grant("g", 3, "sales"), revoke("v", 4, "sales")])
    early = replay(data, at(2, 23))
    assert early.dated_policy.role_changes == [] and early.events_replayed == 1
    exact = replay(data, at(3))
    assert [c.change_id for c in exact.dated_policy.role_changes] == ["g"]
    assert outcomes(exact)["x"][0] == "applied" and exact.states["S"].current_status == "approved"
    later = replay(data, at(4, 12))
    assert [c.change_id for c in later.dated_policy.role_changes] == ["g", "v"]
    assert [(c.roles_before, c.roles_after) for c in later.dated_policy.role_changes] == [
        (["account_manager"], ["account_manager", "product_manager"]),
        (["account_manager", "product_manager"], ["account_manager"])]
    assert later.states["S"].current_status == "approved"


def test_role_change_records_who_changed_it_and_the_evidence(tmp_path):
    data = load(tmp_path, [ev("r", 1, "cust", "request")], [grant("g", 3, "sales", note="promoted")])
    change, = replay(data).dated_policy.role_changes
    assert (change.actor, change.actor_roles, change.action, change.subject_actor, change.role, change.note) == (
        "admin", ["org_admin"], "grant", "sales", "product_manager", "promoted")
    assert change.evidence[0].quote == "Synthetic note"


def test_bootstrap_chain_of_managers(tmp_path):
    changes = [grant("g1", 2, "pm", role="org_admin"), revoke("v1", 3, "admin", role="org_admin", actor="pm"),
               grant("g2", 4, "eng", actor="pm")]
    data = load(tmp_path / "ok", [ev("x", 5, "eng", "approve")], changes)
    assert data.authority.roles("admin") == [] and outcomes(replay(data))["x"][0] == "applied"
    # admin removed their own manager role on day 3, so a later entry by admin is refused.
    with pytest.raises(ValueError, match="g3 .*admin \\(roles: none\\) may not manage_roles"):
        load(tmp_path / "bad", [ev("x", 5, "eng", "approve")], [*changes, grant("g3", 6, "sales", actor="admin")])


# --- policy.json as_of ----------------------------------------------------------------------------------

def test_policy_baseline_as_of(tmp_path):
    policy = {**POLICY, "as_of": "2026-10-03T00:00:00Z"}
    events = [ev("r", 1, "cust", "request"), ev("j", 2, "pm", "reject"), ev("a", 4, "pm", "approve")]
    data = load(tmp_path / "ok", events, policy=policy)
    result = replay(data)
    assert outcomes(result) == {"r": ("unauthorized", []), "j": ("unauthorized", []),
                                "a": ("applied", ["product_manager"])}
    assert replay(data, at(3, 1)).states["S"].pending_changes[0].reason == (
        "before the policy baseline 2026-10-03T00:00:00+00:00 no actor holds a role, so pm may not reject")
    assert result.dated_policy.baseline == at(3, 0) and result.dated_policy.role_changes == []
    assert validation_summary(data)[1:] == [
        "roles: policy.json as of 2026-10-03T00:00:00+00:00, then 0 role change(s) in policy-log.jsonl",
        "note: 2 event(s) before the policy baseline 2026-10-03T00:00:00+00:00, when no actor held a role"]
    at_baseline = load(tmp_path / "at", events, [grant("g", 3, "sales", hour=0)], policy)
    assert at_baseline.authority.roles("sales", at(3, 0)) == ["account_manager", "product_manager"]
    with pytest.raises(ValueError, match="takes effect before the policy baseline"):
        load(tmp_path / "bad", events, [grant("g", 2, "sales")], policy)


def test_policy_as_of_needs_a_timezone(tmp_path):
    with pytest.raises(ValidationError):
        Policy.model_validate({**POLICY, "as_of": "2026-10-03T00:00:00"})


@pytest.mark.parametrize("value", [0, 20_000_000_001, 1_790_000_000.5, "20000000001"])
@pytest.mark.parametrize("field", ["timestamp", "as_of"])
def test_dated_policy_times_refuse_implicit_epoch_units(tmp_path, value, field):
    """Pydantic's seconds/milliseconds heuristic must not silently backdate authority."""
    events = [ev("x", 4, "sales", "approve")]
    changes = [grant("g", 3, "sales", timestamp=value)] if field == "timestamp" else []
    policy = {**POLICY, "as_of": value} if field == "as_of" else POLICY
    root = build(tmp_path / "data", events, changes, policy)
    before = snapshot(root)
    result = runner.invoke(app, ["replay", str(root), "--as-of", t(4)])
    assert result.exit_code == 2 and "ISO 8601" in result.output
    assert snapshot(root) == before


def test_policy_times_accept_aware_library_datetimes():
    assert Policy.model_validate({**POLICY, "as_of": at(3)}).as_of == at(3)
    assert PolicyChange.model_validate(grant("g", 3, "sales", timestamp=at(3))).timestamp == at(3)


def test_policy_command_refuses_numeric_timestamp_without_writing(tmp_path):
    root = build(tmp_path / "data", [ev("a", 4, "sales", "approve")])
    before = snapshot(root)
    result = policy_cli("grant", root, grant("g", 3, "sales", timestamp="20000000001"))
    assert result.exit_code == 2 and "ISO 8601" in result.output
    assert snapshot(root) == before


# --- validation fails closed --------------------------------------------------------------------------

@pytest.mark.parametrize("changes, message", [
    ([grant("g", 3, "sales", actor="pm")], "pm \\(roles: product_manager\\) may not manage_roles"),
    ([grant("g", 3, "sales", actor="nobody")], "nobody \\(roles: none\\) may not manage_roles"),
    ([grant("g", 3, "pm")], "pm already holds product_manager"),
    ([revoke("v", 3, "sales")], "sales does not hold product_manager"),
    ([revoke("v", 3, "pm"), revoke("w", 4, "pm")], "w at .*pm does not hold product_manager"),
    ([grant("g", 3, "sales", role="product_manger")], "unknown role 'product_manger'"),
    ([grant("g", 3, "sales"), grant("g", 4, "eng")], "duplicate policy-log id: g"),
    ([grant("r", 3, "sales")], "policy-log id r is also an event id"),
    ([grant("gh:acme/web#1/opened", 3, "sales")], "gh: prefix is reserved"),
])
def test_invalid_policy_log_is_rejected(tmp_path, changes, message):
    with pytest.raises(ValueError, match=message):
        load(tmp_path, [ev("r", 1, "cust", "request")], changes)


def test_policy_log_cites_known_evidence(tmp_path):
    with pytest.raises(EvidenceError, match="policy-log entry g cites unknown evidence: nope"):
        load(tmp_path, [ev("r", 1, "cust", "request")], [grant("g", 3, "sales", evidence_ids=["nope"])])


@pytest.mark.parametrize("bad", [
    {"action": "promote"},
    {"timestamp": "2026-10-03T09:00:00"},       # naive time
    {"evidence_ids": []},
    {"role": ""},
    {"subject_actor": ""},
    {"extra": "field"},
])
def test_malformed_policy_log_entries_rejected(bad):
    with pytest.raises(ValidationError):
        PolicyChange.model_validate({**grant("g", 3, "sales"), **bad})


def test_malformed_line_is_named_and_unknown_permission_rejected(tmp_path):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request")], [grant("g", 3, "sales")])
    with (root / POLICY_LOG).open("ab") as handle:
        handle.write(b'{"id": "h"}\n')
    with pytest.raises(ValueError, match=f"{POLICY_LOG}:2"):
        Dataset.load(root)
    with pytest.raises(ValidationError):
        Policy.model_validate({**POLICY, "permissions": {**POLICY["permissions"], "manage_everything": ["x"]}})


def test_whole_log_is_validated_even_after_the_cutoff(tmp_path):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request")], [grant("g", 30, "sales", actor="pm")])
    result = runner.invoke(app, ["replay", str(root), "--as-of", t(2)])
    assert result.exit_code == 2 and "may not manage_roles" in result.output


def test_dangling_policy_log_symlink_fails_closed(tmp_path):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request")])
    try:
        (root / POLICY_LOG).symlink_to(tmp_path / "missing.jsonl")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    with pytest.raises(OSError):
        Dataset.load(root)


# --- explain ------------------------------------------------------------------------------------------

def test_explain_shows_roles_at_the_time_and_role_changes(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("x", 2, "sales", "approve"), ev("j", 3, "pm", "reject")]
    changes = [grant("g", 4, "sales"), revoke("v", 5, "pm", note="left\nthe team"), grant("o", 5, "cust")]
    result = replay(load(tmp_path, events, changes))
    text = explain(result, "S")
    assert "- `j`: pm (roles: product_manager) chose **reject**" in text
    assert ("  - pm's roles changed later: lost product_manager at 2026-10-05T09:00:00+00:00 (`v`). "
            "The decision stands: it is judged by the roles held when it was made.") in text
    assert "**Role changes of these actors**" in text
    assert ("- `g` at 2026-10-04T09:00:00+00:00: admin (roles: org_admin) granted product_manager to sales; "
            "roles of sales: account_manager → account_manager, product_manager") in text
    assert '; roles of pm: product_manager → none; note: "left\\nthe team"' in text
    assert "| `x` | sales (account_manager) | approve | unauthorized |" in text
    # Only the changes of actors in this subject's history, up to the cutoff.
    assert "`o`" in text
    other = replay(load(tmp_path / "other", [*events, ev("q", 6, "eng", "comment", subject="T")], changes))
    assert "Role changes" not in explain(other, "T")
    assert "`v`" not in explain(replay(load(tmp_path / "cut", events, changes), at(4, 12)), "S")


def test_replay_report_header_names_the_role_source(tmp_path):
    data = load(tmp_path, [ev("r", 1, "cust", "request")], [grant("g", 3, "sales")])
    assert ("- Roles: policy.json from the beginning of the log, then 1 role change(s) from policy-log.jsonl; "
            "each event is judged by the roles its actor held at its time") in to_markdown(replay(data))


def test_dated_example_matches_its_reports():
    data = Dataset.load(DATED_EXAMPLE)
    assert to_markdown(replay(data)) + "\n" == (DATED_EXAMPLE / "replay-report.md").read_text(encoding="utf-8")
    report = audit(data, at(31, 0))
    assert audit_markdown(report) == (DATED_EXAMPLE / "audit-report.md").read_text(encoding="utf-8")
    assert [(f.kind, f.severity) for f in report.findings] == [
        ("stale_reconsideration", "medium"), ("unauthorized_attempts", "medium"),
        ("role_changed_after_decision", "low")]


# --- audit --------------------------------------------------------------------------------------------

def test_role_changed_after_decision(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("j", 2, "pm", "reject")]
    data = load(tmp_path, events, [revoke("v", 4, "pm", note="moved")])
    report = audit(data, at(5))
    finding, = [f for f in report.findings if f.kind == "role_changed_after_decision"]
    assert (finding.severity, finding.subject, finding.actor, finding.event_ids) == ("low", "S", "pm", ["j"])
    assert [c.change_id for c in finding.role_changes] == ["v"]
    assert finding.role_changes[0].evidence[0].quote == "Synthetic note"
    assert finding.detail == (
        "reject j by pm (roles: product_manager) is the current decision; pm later lost product_manager "
        "(v at 2026-10-04T09:00:00+00:00); roles as of 2026-10-05T09:00:00+00:00: none. The decision stands: "
        "it is judged by the roles held when it was made")
    assert report.kinds["role_changed_after_decision"] == 1 and list(report.kinds) == list(KINDS)
    data_json = json.loads(report.model_dump_json())
    assert data_json["dated_policy"]["role_changes"][0]["change_id"] == "v"
    assert [f.get("role_changes", []) != [] for f in data_json["findings"]].count(True) == 1
    markdown = audit_markdown(report)
    assert "| `role_changed_after_decision` | 0 | 0 | 1 |" in markdown
    assert "### LOW · role_changed_after_decision · S" in markdown
    assert "- role change `v` at 2026-10-04T09:00:00+00:00: admin (roles: org\\_admin) removed" in markdown
    assert "## Role changes" in markdown and "informational" in markdown
    # Before the change takes effect, there is nothing to report.
    assert not [f for f in audit(data, at(3)).findings if f.kind == "role_changed_after_decision"]


@pytest.mark.parametrize("events, changes", [
    # The lost role did not authorize the decision.
    ([ev("j", 2, "pm", "reject")], [grant("g", 1, "pm", role="engineer"), revoke("v", 3, "pm", role="engineer")]),
    # The decision is no longer current: someone else decided later.
    ([ev("j", 2, "pm", "reject"), ev("a", 4, "sales", "approve")], [grant("g", 3, "sales"), revoke("v", 5, "pm")]),
    # The role was lost before the decision, which was then unauthorized.
    ([ev("j", 4, "pm", "reject")], [revoke("v", 3, "pm")]),
])
def test_role_changed_after_decision_only_for_a_current_authorizing_role(tmp_path, events, changes):
    report = audit(load(tmp_path, [ev("r", 1, "cust", "request"), *events], changes), at(10))
    assert report.kinds["role_changed_after_decision"] == 0


def test_role_changed_after_decision_also_covers_a_revoke(tmp_path):
    events = [ev("a", 2, "pm", "approve"), ev("u", 3, "pm", "revoke", supersedes="a")]
    report = audit(load(tmp_path, [ev("r", 1, "cust", "request"), *events], [revoke("v", 4, "pm")]), at(5))
    finding, = [f for f in report.findings if f.kind == "role_changed_after_decision"]
    assert finding.event_ids == ["u"] and finding.detail.startswith("revoke u by pm")


def test_unauthorized_attempts_show_roles_at_each_time(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("x", 2, "eng", "approve"), ev("y", 4, "eng", "reject")]
    report = audit(load(tmp_path, events, [revoke("v", 3, "eng", role="engineer")]), at(5))
    finding, = [f for f in report.findings if f.kind == "unauthorized_attempts"]
    assert finding.detail.endswith("roles at the time: engineer, then none")
    assert [e.roles for e in finding.events] == [["engineer"], []]


# --- CLI ------------------------------------------------------------------------------------------------

def policy_cli(command, root, change, *extra):
    args = ["policy", command, str(root), "--id", change["id"], "--timestamp", change["timestamp"],
            "--actor", change["actor"], "--subject-actor", change["subject_actor"], "--role", change["role"]]
    for evidence_id in change["evidence_ids"]:
        args += ["--evidence-id", evidence_id]
    return runner.invoke(app, [*args, *extra])


def test_policy_grant_and_revoke_validate_then_append(tmp_path):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request"), ev("x", 4, "sales", "approve")])
    others = snapshot(root)
    result = policy_cli("grant", root, grant("g", 3, "sales"), "--note", "promoted")
    assert result.exit_code == 0 and result.output == f"appended g to {POLICY_LOG}\n"
    first = (root / POLICY_LOG).read_bytes()
    assert json.loads(first) == {**grant("g", 3, "sales"), "note": "promoted"}
    assert policy_cli("revoke", root, revoke("v", 5, "sales")).exit_code == 0
    log = (root / POLICY_LOG).read_bytes()
    assert log.startswith(first) and log.count(b"\n") == 2 and b'"action": "revoke_role"' in log
    # Nothing else in the dataset changed.
    assert {p: b for p, b in snapshot(root).items() if p.name != POLICY_LOG} == others
    replayed = runner.invoke(app, ["replay", str(root), "--format", "json"])
    assert json.loads(replayed.output)["audit_log"][1]["outcome"] == "applied"


@pytest.mark.parametrize("command, change, message", [
    ("grant", grant("bad", 6, "eng", actor="pm"), "may not manage_roles"),
    ("revoke", revoke("bad", 6, "eng"), "eng does not hold product_manager"),
    ("grant", grant("g", 6, "eng"), "duplicate policy-log id: g"),
    ("grant", grant("x", 6, "eng"), "also an event id"),
    ("grant", {**grant("bad", 6, "eng"), "timestamp": "2026-10-06T09:00:00"}, "timezone"),
    # Backdated: removing admin's manager role before g would make g unauthorized.
    ("revoke", revoke("bad", 2, "admin", role="org_admin"), "g at .*admin \\(roles: none\\) may not manage_roles"),
])
def test_policy_commands_refuse_invalid_changes_without_writing(tmp_path, command, change, message):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request"), ev("x", 4, "sales", "approve")],
                 [grant("g", 3, "sales")])
    before = snapshot(root)
    result = policy_cli(command, root, change)
    assert result.exit_code == 2 and re.search(message, result.output)
    assert snapshot(root) == before


def test_append_refuses_an_event_id_used_by_the_policy_log(tmp_path):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request")], [grant("g", 3, "sales")])
    before = snapshot(root)
    result = runner.invoke(app, ["append", str(root), "--event", json.dumps(ev("g", 5, "pm", "approve"))])
    assert result.exit_code == 2 and "also an event id" in result.output and snapshot(root) == before


def test_validate_reports_the_dated_policy(tmp_path):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request"), ev("x", 2, "newbie", "comment"),
                                     ev("y", 2, "ghost", "comment")], [grant("g", 3, "newbie")])
    result = runner.invoke(app, ["validate", str(root)])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "ok: 3 events, 1 evidence records",
        "roles: policy.json from the beginning of the log, then 1 role change(s) in policy-log.jsonl",
        "note: actors without any role (their events cannot change state): ghost"]


def test_read_only_commands_write_nothing(tmp_path):
    root = tmp_path / "dated"
    shutil.copytree(DATED_EXAMPLE, root)
    before = snapshot(root)
    for args in (["replay"], ["replay", "-f", "json"], ["explain", "Feature A"], ["validate"],
                 ["audit", "--as-of", "2026-10-31T00:00:00Z"], ["audit", "-f", "json"]):
        assert runner.invoke(app, [args[0], str(root), *args[1:]]).exit_code == 0
    assert snapshot(root) == before


def test_policy_log_is_not_an_event_log(tmp_path):
    """A policy-log line is not a valid event, and an event is not a valid policy-log line."""
    with pytest.raises(ValidationError):
        Event.model_validate(grant("g", 3, "sales"))
    with pytest.raises(ValidationError):
        PolicyChange.model_validate(ev("r", 1, "cust", "request"))


# --- adversarial authority review --------------------------------------------------------------------

@pytest.mark.parametrize("role", ["org_admin", "product_manager"])
def test_non_manager_cannot_self_grant_any_authority(tmp_path, role):
    with pytest.raises(ValueError, match="eng .*may not manage_roles"):
        load(tmp_path, [ev("a", 3, "eng", "approve")],
             [grant("self", 2, "eng", role=role, actor="eng")])


def test_circular_and_future_manager_grants_cannot_bootstrap(tmp_path):
    for name, changes in [
        ("circular", [grant("g1", 2, "sales", role="org_admin", actor="eng"),
                      grant("g2", 2, "eng", role="org_admin", actor="sales")]),
        ("future", [grant("self", 2, "eng", actor="eng"),
                    grant("manager", 3, "eng", role="org_admin")]),
    ]:
        with pytest.raises(ValueError, match="eng .*may not manage_roles"):
            load(tmp_path / name, [ev("a", 4, "eng", "approve")], changes)


def test_manager_self_grant_is_the_documented_permission(tmp_path):
    data = load(tmp_path, [ev("a", 3, "admin", "approve")], [grant("self", 3, "admin")])
    assert outcomes(replay(data))["a"] == ("applied", ["org_admin", "product_manager"])
    assert replay(data).dated_policy.role_changes[0].actor_roles == ["org_admin"]


def test_self_revocation_prevents_self_restoration_at_the_same_instant(tmp_path):
    changes = [revoke("v", 3, "admin", role="org_admin")]
    data = load(tmp_path / "ok", [ev("a", 3, "admin", "approve")], changes)
    assert outcomes(replay(data))["a"] == ("unauthorized", [])
    with pytest.raises(ValueError, match="restore .*admin .*may not manage_roles"):
        load(tmp_path / "bad", [], [*changes, grant("restore", 3, "admin", role="org_admin")])


def test_revoked_manager_cannot_use_final_same_time_roles_to_authorize_a_change(tmp_path):
    g = grant("g", 3, "eng")
    v = revoke("v", 3, "admin", role="org_admin")
    with pytest.raises(ValueError, match="g .*admin .*may not manage_roles"):
        load(tmp_path / "bad", [], [v, g])
    data = load(tmp_path / "ok", [ev("a", 3, "eng", "approve")], [g, v])
    assert outcomes(replay(data))["a"][0] == "applied"
    assert data.authority.roles("admin", at(3)) == []


def test_offset_equivalent_ties_and_subsecond_cutoffs(tmp_path):
    changes = [grant("manager", 3, "sales", role="org_admin"),
               grant("approval", 3, "eng", actor="sales", timestamp="2026-10-03T04:00:00-05:00"),
               revoke("v", 3, "eng", timestamp="2026-10-03T18:00:00.000001+09:00")]
    events = [ev("a", 3, "eng", "approve", timestamp="2026-10-03T18:00:00+09:00"),
              ev("x", 3, "eng", "reject", timestamp="2026-10-03T09:00:00.000001Z")]
    data = load(tmp_path, events, changes)
    just_before = replay(data, at(3).replace(hour=8, minute=59, second=59, microsecond=999999))
    assert just_before.events_replayed == 0 and just_before.dated_policy.role_changes == []
    exact = replay(data, at(3))
    assert outcomes(exact)["a"] == ("applied", ["engineer", "product_manager"])
    assert [c.change_id for c in exact.dated_policy.role_changes] == ["manager", "approval"]
    full = replay(data, at(3).replace(microsecond=1))
    assert outcomes(full)["x"] == ("unauthorized", ["engineer"])
    assert full.states["S"].last_authorized_decision.event_id == "a"


@pytest.mark.parametrize("action, fields", [
    ("request", {}), ("reconsider", {}), ("approve", {}), ("reject", {}), ("revoke", {"supersedes": "a"}),
    ("raise_risk", {"note": "crafted risk"}), ("dependency", {"dependency": "T"}), ("comment", {}),
])
def test_no_action_can_mutate_authorized_state_after_same_time_role_revocation(tmp_path, action, fields):
    data = load(tmp_path, [ev("a", 2, "pm", "approve"), ev("x", 3, "pm", action, **fields)],
                [revoke("v", 3, "pm")])
    result = replay(data)
    assert outcomes(result)["x"] == ("unauthorized", [])
    state = result.states["S"]
    assert state.current_status == "approved" and state.last_authorized_decision.event_id == "a"
    assert state.last_applied_event == "a" and state.decision_history == ["a"]
    assert not state.reconsideration_requested and not state.pending_approval
    assert not state.risks and not state.dependencies


def test_revoke_cannot_target_a_policy_change_id(tmp_path):
    with pytest.raises(ValueError, match="supersedes unknown event g"):
        load(tmp_path, [ev("x", 4, "pm", "revoke", supersedes="g")], [grant("g", 3, "sales")])


@pytest.mark.parametrize("command, actor, original, changed", [
    ("grant", "sales", "unauthorized", "applied"), ("revoke", "pm", "applied", "unauthorized"),
])
def test_backdated_appends_recompute_past_outcomes_and_past_cutoffs(tmp_path, command, actor, original, changed):
    root = build(tmp_path / "data", [ev("a", 4, actor, "approve")])
    before = snapshot(root)
    assert outcomes(replay(Dataset.load(root), at(4)))["a"][0] == original
    change = grant("backdated", 3, actor) if command == "grant" else revoke("backdated", 3, actor)
    assert policy_cli(command, root, change).exit_code == 0
    result = replay(Dataset.load(root), at(4))
    assert outcomes(result)["a"][0] == changed
    assert (result.states["S"].current_status == "approved") == (changed == "applied")
    assert {p: b for p, b in snapshot(root).items() if p.name != POLICY_LOG} == before
