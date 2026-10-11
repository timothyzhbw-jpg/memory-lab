"""orgmemory benchmark: replay labeled datasets and compare state, trail, audit and validation with the labels.

    python benchmarks/orgmemory/run.py [--case ID]... [--json OUT] [--check]

The case and label format is defined in README.md next to this file. The labels say what the replay
should produce; this script measures the implementation against them and never the other way round.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Literal, Optional, get_args

ROOT = Path(__file__).resolve().parents[2]
for _package in ("memcore", "orgmemory"):  # benchmark this checkout, not whatever copy is installed
    if (_src := str(ROOT / "packages" / _package / "src")) not in sys.path:
        sys.path.insert(0, _src)

import typer  # noqa: E402
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator  # noqa: E402

from memcore import EvidenceError, resolve_inside  # noqa: E402
from orgmemory import __version__  # noqa: E402
from orgmemory.audit import audit  # noqa: E402
from orgmemory.replay import Dataset, replay  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"
# The label vocabulary is defined here, not imported, so a change in the implementation cannot redefine it.
Status = Literal["proposed", "approved", "rejected"]
Outcome = Literal["applied", "unauthorized", "invalid_supersession", "not_replayed"]
Kind = Literal["decision_reversed", "invalid_supersession", "risk_after_approval", "self_approval",
               "simultaneous_decisions", "stale_pending_approval", "stale_reconsideration",
               "unauthorized_attempts", "unresolved_dependency"]
Severity = Literal["high", "medium", "low"]
CheckKind = Literal["validation", "subjects", "state", "trail", "order", "audit"]
CHECK_KINDS: tuple[str, ...] = get_args(CheckKind)
STATE_FIELDS = ("status", "pending_approval", "reconsideration_requested", "risks", "dependencies",
                "last_authorized_decision")
DECISIONS = {"approve", "reject", "revoke"}
VARIANT_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Cc covers CR/LF/ESC, Cf bidi and zero-width, Zl/Zp Unicode line and paragraph separators.
_LINE_BREAK = {"Cc", "Zl", "Zp"}


class BenchmarkError(ValueError):
    """A case or the case selection is malformed; the message lists every problem found."""


def _breaks_line(text: str) -> bool:
    return any(unicodedata.category(char) in _LINE_BREAK for char in text)


def _note() -> Any:
    return Field(min_length=1, description="One line on why the label is right")


class _WithNote(BaseModel):
    """Label text is data. A note stays one physical line so it cannot open a new record."""
    note: str = _note()

    @model_validator(mode="after")
    def _note_is_one_line(self) -> "_WithNote":
        if _breaks_line(self.note):
            raise ValueError("note is one line")
        return self


class StateLabel(_WithNote):
    """A subject's replayed state. All six fields are required, so a label states the whole state."""
    model_config = ConfigDict(extra="forbid")

    status: Status
    pending_approval: list[str]
    reconsideration_requested: bool
    risks: list[str]                      # ids of the raise_risk events on record
    dependencies: list[str]
    last_authorized_decision: Optional[str]


class TrailLabel(_WithNote):
    model_config = ConfigDict(extra="forbid")

    outcome: Outcome


class FindingLabel(_WithNote):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    severity: Severity
    subject: Optional[str] = None
    actor: Optional[str] = None
    note: str = _note()

    @model_validator(mode="after")
    def _who(self) -> "FindingLabel":
        if self.kind == "unauthorized_attempts":
            if self.actor is None or self.subject is not None:
                raise ValueError("an unauthorized_attempts finding names an actor and no subject")
        elif self.kind == "self_approval":
            if self.actor is None or self.subject is None:
                raise ValueError("a self_approval finding names a subject and an actor")
        elif self.subject is None or self.actor is not None:
            raise ValueError(f"a {self.kind} finding names a subject and no actor")
        return self

    @property
    def key(self) -> tuple[str, str, str]:
        return self.kind, self.subject or "", self.actor or ""


class AuditLabel(BaseModel):
    """Every finding `orgmemory audit` should report at the snapshot's as_of; anything else is unexpected."""
    model_config = ConfigDict(extra="forbid")

    stale_days: int = Field(14, ge=0)
    findings: list[FindingLabel]


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: Optional[AwareDatetime] = None
    states: Optional[dict[str, StateLabel]] = None  # every subject the replay knows at as_of
    trail: dict[str, TrailLabel] = {}
    order: list[str] = []
    audit: Optional[AuditLabel] = None

    @model_validator(mode="after")
    def _shape(self) -> "Snapshot":
        if self.states is None and not self.trail and not self.order and self.audit is None:
            raise ValueError("a snapshot needs states, trail, order or audit")
        if self.audit is not None and self.as_of is None:
            raise ValueError("an audit snapshot needs as_of: it is the reference time for staleness")
        if self.order and (len(self.order) < 2 or len(set(self.order)) != len(self.order)):
            raise ValueError("order lists at least two distinct event ids")
        return self


class LoadLabel(_WithNote):
    """Load the dataset with these files; it must fail with an error containing ``error``, or load if null."""
    model_config = ConfigDict(extra="forbid")

    name: str
    events: str = "events.jsonl"
    policy: str = "policy.json"
    evidence: str = "evidence.jsonl"
    error: Optional[str] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _name(self) -> "LoadLabel":
        if not VARIANT_NAME.fullmatch(self.name):
            raise ValueError("name is lowercase words joined by hyphens")
        return self


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    validation: list[LoadLabel] = []
    snapshots: list[Snapshot] = []

    @model_validator(mode="after")
    def _shape(self) -> "Expected":
        if _breaks_line(self.description):
            raise ValueError("description is one line")
        if not self.validation and not self.snapshots:
            raise ValueError("a case needs validation labels or snapshots")
        return self


class Case(BaseModel):
    id: str
    root: Path
    expected: Expected


class Check(BaseModel):
    case: str
    when: str             # "load", "end of log" or the snapshot's as_of
    kind: CheckKind
    target: str           # variant name, subject, event id, finding key, or "*"
    field: Optional[str] = None
    expected: Any
    actual: Any
    passed: bool
    note: str

    @property
    def id(self) -> str:
        return " | ".join([self.case, self.when, self.kind, self.target] + ([self.field] if self.field else []))


# --- loading and --check ------------------------------------------------------------------------------

def _errors(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'record'}: {e['msg']}" for e in exc.errors())


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict:
    keys = [k for k, _ in pairs]
    duplicated = sorted({k for k in keys if keys.count(k) > 1})
    if duplicated:
        raise ValueError(f"duplicate key(s) {', '.join(map(repr, duplicated))}")
    return dict(pairs)


def _log(path: Path) -> tuple[dict[str, dict], list[str]]:
    """Event id -> record, read as plain JSON (not through orgmemory), for checking the labels' references."""
    events, problems = {}, []
    for number, line in enumerate(path.read_bytes().decode("utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"events.jsonl:{number}: not JSON; a case with snapshots needs a readable log")
            continue
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            events.setdefault(record["id"], record)
    return events, problems


def _references(expected: Expected, events: dict[str, dict]) -> list[str]:
    problems = []
    subjects = {e.get("subject") for e in events.values()}
    actors = {e.get("actor") for e in events.values()}
    seen = set()
    for index, snapshot in enumerate(expected.snapshots):
        where = f"snapshots[{index}]"
        if snapshot.as_of in seen:
            problems.append(f"{where}: another snapshot has the same as_of")
        seen.add(snapshot.as_of)
        for subject, state in (snapshot.states or {}).items():
            if subject not in subjects:
                problems.append(f"{where}.states: {subject!r} is not a subject of events.jsonl")
            for risk in state.risks:
                record = events.get(risk, {})
                if record.get("action") != "raise_risk" or record.get("subject") != subject:
                    problems.append(f"{where}.states[{subject!r}].risks: {risk!r} is not a raise_risk event "
                                    f"on this subject")
            decision = state.last_authorized_decision
            if decision is not None:
                record = events.get(decision, {})
                if record.get("action") not in DECISIONS or record.get("subject") != subject:
                    problems.append(f"{where}.states[{subject!r}].last_authorized_decision: {decision!r} is not "
                                    f"an approve, reject or revoke event on this subject")
        for event_id in [*snapshot.trail, *snapshot.order]:
            if event_id not in events:
                problems.append(f"{where}: {event_id!r} is not an event id of events.jsonl")
        for finding in snapshot.audit.findings if snapshot.audit else []:
            if finding.subject is not None and finding.subject not in subjects:
                problems.append(f"{where}.audit: {finding.subject!r} is not a subject of events.jsonl")
            if finding.actor is not None and finding.actor not in actors:
                problems.append(f"{where}.audit: {finding.actor!r} is not an actor of events.jsonl")
    return problems


def load_case(root: Path) -> Case:
    """Read one case's expected.json and check its labels against the case's files."""
    root = Path(root)
    path = root / "expected.json"
    if not path.is_file():
        raise BenchmarkError(f"case {root.name}:\n  - missing expected.json")
    problems: list[str] = []
    expected = None
    try:
        expected = Expected.model_validate(json.loads(path.read_bytes().decode("utf-8"),
                                                      object_pairs_hook=_unique_keys))
    except ValidationError as exc:
        problems.append(f"expected.json: {_errors(exc)}")
    except (ValueError, UnicodeDecodeError) as exc:
        problems.append(f"expected.json: {exc}")
    if expected is not None:
        names = [label.name for label in expected.validation]
        problems += [f"validation: name {n!r} is used twice" for n in sorted({n for n in names if names.count(n) > 1})]
        for label in expected.validation:
            for name in (label.events, label.policy, label.evidence):
                try:
                    if not resolve_inside(root, name).is_file():
                        problems.append(f"validation {label.name!r}: missing file {name}")
                except EvidenceError as exc:
                    problems.append(f"validation {label.name!r}: {exc}")
        if expected.snapshots:
            missing = [n for n in ("events.jsonl", "policy.json", "evidence.jsonl") if not (root / n).is_file()]
            problems += [f"snapshots need {n}" for n in missing]
            if "events.jsonl" not in missing:
                events, unreadable = _log(root / "events.jsonl")
                problems += unreadable or _references(expected, events)
    if problems:
        raise BenchmarkError(f"case {root.name}:\n" + "\n".join(f"  - {p}" for p in problems))
    return Case(id=root.name, root=root, expected=expected)


def load_cases(cases_dir: Path, ids: list[str] | None = None) -> list[Case]:
    """Load the selected cases (all by default), reporting the problems of every case at once."""
    cases_dir = Path(cases_dir)
    available = sorted(p.name for p in cases_dir.iterdir()
                       if p.is_dir() and not p.name.startswith(".")) if cases_dir.is_dir() else []
    if not available:
        raise BenchmarkError(f"no cases in {cases_dir}")
    unknown = [i for i in ids or [] if i not in available]
    if unknown:
        raise BenchmarkError(f"unknown case(s): {', '.join(unknown)} (available: {', '.join(available)})")
    cases, problems = [], []
    for case_id in dict.fromkeys(ids or available):
        try:
            cases.append(load_case(cases_dir / case_id))
        except BenchmarkError as exc:
            problems.append(str(exc))
    if problems:
        raise BenchmarkError("\n".join(problems))
    return cases


def count_labels(expected: Expected) -> tuple[int, int]:
    """(labels, checks): every label is checked once, except a state label (one check per field). Each snapshot
    with states adds a check that no subject is missing or extra, and each audit one that no finding is unexpected."""
    labels = checks = len(expected.validation)
    for snapshot in expected.snapshots:
        states = snapshot.states or {}
        labels += len(states) + len(snapshot.trail) + bool(snapshot.order)
        checks += len(states) * len(STATE_FIELDS) + (snapshot.states is not None) + len(snapshot.trail) \
            + bool(snapshot.order)
        if snapshot.audit is not None:
            labels += len(snapshot.audit.findings)
            checks += len(snapshot.audit.findings) + 1
    return labels, checks


# --- measuring -------------------------------------------------------------------------------------------

def _message(exc: BaseException, root: Path) -> str:
    """One deterministic line: no pydantic documentation links, case paths shown as <case>."""
    lines = [line for line in str(exc).splitlines() if not line.strip().startswith("For further information")]
    text = " ".join(" ".join(lines).split())
    for prefix in sorted({str(root.resolve()), str(root)}, key=len, reverse=True):
        text = text.replace(repr(prefix)[1:-1], "<case>").replace(prefix, "<case>")
    if not isinstance(exc, (ValueError, OSError)):
        text = f"{type(exc).__name__}: {text}"
    return text


def _load(case: Case, label: LoadLabel | None = None) -> Dataset:
    label = label or LoadLabel(name="main", error=None, note="-")
    return Dataset.load(case.root, events=case.root / label.events, policy=case.root / label.policy,
                        evidence=case.root / label.evidence)


def _validation(case: Case) -> list[Check]:
    checks = []
    for label in case.expected.validation:
        try:
            _load(case, label)
            actual = "loads"
        except Exception as exc:  # noqa: BLE001 - any failure is a measurement, not a harness error
            actual = _message(exc, case.root)
        passed = actual == "loads" if label.error is None else actual != "loads" and label.error in actual
        checks.append(Check(case=case.id, when="load", kind="validation", target=label.name,
                            expected="loads" if label.error is None else f"error containing {label.error!r}",
                            actual=actual, passed=passed, note=label.note))
    return checks


def _state_actual(state) -> dict:
    decision = state.last_authorized_decision
    return {"status": state.current_status, "pending_approval": sorted(state.pending_approval),
            "reconsideration_requested": state.reconsideration_requested,
            "risks": sorted(risk.event_id for risk in state.risks), "dependencies": sorted(state.dependencies),
            "last_authorized_decision": decision.event_id if decision else None}


def _state_expected(label: StateLabel) -> dict:
    return {"status": label.status, "pending_approval": sorted(label.pending_approval),
            "reconsideration_requested": label.reconsideration_requested, "risks": sorted(label.risks),
            "dependencies": sorted(set(label.dependencies)), "last_authorized_decision": label.last_authorized_decision}


def _finding(f) -> str:
    who = " ".join(x for x in (f.subject, f.actor) if x is not None)
    return f"{f.kind} {who} ({f.severity})"


def _audit_checks(case: Case, when: str, label: AuditLabel, report) -> list[Check]:
    """Match labeled findings to reported ones by (kind, subject, actor); then compare severities."""
    checks = []
    actual: dict[tuple, list] = defaultdict(list)
    for finding in report.findings:
        actual[(finding.kind, finding.subject or "", finding.actor or "")].append(finding)
    groups: dict[tuple, list[FindingLabel]] = defaultdict(list)
    for finding in label.findings:
        groups[finding.key].append(finding)
    for key, wanted in groups.items():
        found = actual.get(key, [])
        matched = [False] * len(found)
        results: list[Optional[str]] = [None] * len(wanted)
        for i, finding in enumerate(wanted):  # exact severity first, so order within a group does not matter
            for j, other in enumerate(found):
                if not matched[j] and other.severity == finding.severity:
                    matched[j], results[i] = True, other.severity
                    break
        for i in range(len(wanted)):
            if results[i] is None:
                j = next((j for j, used in enumerate(matched) if not used), None)
                if j is not None:
                    matched[j], results[i] = True, found[j].severity
        actual[key] = [other for j, other in enumerate(found) if not matched[j]]
        for i, finding in enumerate(wanted):
            target = " ".join(x for x in key if x) + (f" #{i + 1}" if len(wanted) > 1 else "")
            checks.append(Check(case=case.id, when=when, kind="audit", target=target, field="severity",
                                expected=finding.severity, actual=results[i] or "not reported",
                                passed=results[i] == finding.severity, note=finding.note))
    extra = sorted(_finding(f) for rest in actual.values() for f in rest)
    checks.append(Check(case=case.id, when=when, kind="audit", target="*", field="unexpected findings",
                        expected=[], actual=extra, passed=not extra,
                        note="the labeled findings are all the audit should report"))
    return checks


def _snapshot(case: Case, dataset: Optional[Dataset], failure: str, snapshot: Snapshot) -> list[Check]:
    when = snapshot.as_of.isoformat() if snapshot.as_of else "end of log"
    checks: list[Check] = []

    def add(kind, target, field, expected, actual, note):
        checks.append(Check(case=case.id, when=when, kind=kind, target=target, field=field, expected=expected,
                            actual=actual, passed=expected == actual, note=note))

    result, error = None, failure
    if dataset is not None:
        try:
            result = replay(dataset, snapshot.as_of)
        except Exception as exc:  # noqa: BLE001
            error = f"replay failed: {_message(exc, case.root)}"
    if snapshot.states is not None:
        add("subjects", "*", None, sorted(snapshot.states),
            sorted(result.states) if result else error, "every subject the replay knows at this time")
        for subject, label in snapshot.states.items():
            state = result.states.get(subject) if result else None
            got = _state_actual(state) if state is not None else None
            for name, value in _state_expected(label).items():
                add("state", subject, name, value,
                    got[name] if got else (error if result is None else "subject not replayed"), label.note)
    if snapshot.trail or snapshot.order:
        outcomes = {entry.event_id: entry.outcome for entry in result.audit_log} if result else {}
        for event_id, label in snapshot.trail.items():
            add("trail", event_id, "outcome", label.outcome,
                outcomes.get(event_id, "not_replayed") if result else error, label.note)
        if snapshot.order:
            wanted = set(snapshot.order)
            add("order", " < ".join(snapshot.order), None, snapshot.order,
                [e.event_id for e in result.audit_log if e.event_id in wanted] if result else error,
                "replay order: effective time, ties in file order")
    if snapshot.audit is not None:
        report = None
        if dataset is not None:
            try:
                report = audit(dataset, snapshot.as_of, snapshot.audit.stale_days)
            except Exception as exc:  # noqa: BLE001
                error = f"audit failed: {_message(exc, case.root)}"
        if report is not None:
            checks += _audit_checks(case, when, snapshot.audit, report)
        else:
            checks += [Check(case=case.id, when=when, kind="audit",
                             target=" ".join(x for x in f.key if x), field="severity", expected=f.severity,
                             actual=error, passed=False, note=f.note) for f in snapshot.audit.findings]
            checks.append(Check(case=case.id, when=when, kind="audit", target="*", field="unexpected findings",
                                expected=[], actual=error, passed=False, note="the audit did not run"))
    return checks


def measure(case: Case) -> list[Check]:
    checks = _validation(case)
    if case.expected.snapshots:
        dataset, failure = None, ""
        try:
            dataset = _load(case)
        except Exception as exc:  # noqa: BLE001
            failure = f"dataset did not load: {_message(exc, case.root)}"
        for snapshot in case.expected.snapshots:
            checks += _snapshot(case, dataset, failure, snapshot)
    return checks


def _tally(checks: list[Check]) -> dict:
    passed = sum(c.passed for c in checks)
    return {"checks": len(checks), "passed": passed, "failed": len(checks) - passed}


def evaluate(cases: list[Case]) -> dict:
    """Measure every case and summarize: per case, per check kind, and every check with its label's note."""
    per_case, every = [], []
    for case in cases:
        checks = measure(case)
        labels, _ = count_labels(case.expected)
        per_case.append({"id": case.id, "description": case.expected.description, "labels": labels,
                         **_tally(checks)})
        every += checks
    by_kind = {kind: _tally([c for c in every if c.kind == kind]) for kind in CHECK_KINDS}
    return {"orgmemory_version": __version__, "cases": per_case,
            "totals": {"cases": len(cases), "labels": sum(c["labels"] for c in per_case), **_tally(every)},
            "by_kind": by_kind, "checks": [{"id": c.id, **c.model_dump()} for c in every]}


# --- output ------------------------------------------------------------------------------------------------

def _one_line(text: str) -> str:
    """Untrusted text on one line: control, bidi, zero-width and separator characters are escaped.

    Same character classes as ``orgmemory.report._one_line``. The benchmark prints case descriptions,
    notes, subjects, actors and exception text; those bytes are data, never terminal or markdown syntax.
    """
    return "".join(char if unicodedata.category(char) not in ("Cc", "Cf", "Zl", "Zp")
                   else f"\\u{ord(char):04x}" if ord(char) <= 0xFFFF else f"\\U{ord(char):08x}"
                   for char in text)


def _cell(value: Any) -> str:
    """One markdown table cell. Escaping matches ``orgmemory.report._safe`` so the cell stays literal."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = escape(_one_line(text), quote=False)
    text = re.sub(r"([\\`*_{}\[\]#|~])", r"\\\1", text)
    text = re.sub(r"^(\s*)([-+=])", r"\1\\\2", text)
    return re.sub(r"^(\s*\d+)([.)])(?=\s)", r"\1\\\2", text)


def _terminal(text: str) -> str:
    """Stderr stays multi-line, but each line is inert if a runner or terminal prints it."""
    return "\n".join(_one_line(line) for line in text.splitlines())


def to_markdown(result: dict) -> str:
    totals = result["totals"]
    out = ["# orgmemory benchmark", "",
           f"- orgmemory {result['orgmemory_version']} · cases: {totals['cases']} · labels: {totals['labels']} · "
           f"checks: {totals['checks']}",
           f"- Result: {totals['passed']}/{totals['checks']} checks pass, {totals['failed']} fail", "",
           "## By check kind", "", "| Kind | Checks | Pass | Fail |", "|---|---|---|---|"]
    out += [f"| {_cell(kind)} | {t['checks']} | {t['passed']} | {t['failed']} |"
            for kind, t in result["by_kind"].items() if t["checks"]]
    out += ["", "## By case", "", "| Case | Labels | Checks | Pass | Fail | What it tests |",
            "|---|---|---|---|---|---|"]
    out += [f"| {_cell(c['id'])} | {c['labels']} | {c['checks']} | {c['passed']} | {c['failed']} | {_cell(c['description'])} |"
            for c in result["cases"]]
    failed = [c for c in result["checks"] if not c["passed"]]
    out += ["", "## Mismatches", ""]
    if failed:
        out += ["| Case | When | Check | Expected | Actual | Label note |", "|---|---|---|---|---|---|"]
        out += [f"| {_cell(c['case'])} | {_cell(c['when'])} | {_cell(' '.join([c['kind'], c['target']] + ([c['field']] if c['field'] else [])))} "
                f"| {_cell(c['expected'])} | {_cell(c['actual'])} | {_cell(c['note'])} |" for c in failed]
    else:
        out.append("None: every check matches its label.")
    return "\n".join(out) + "\n"


app = typer.Typer(add_completion=False, help="Measure orgmemory replay, audit and validation against labeled cases.")


@app.command()
def main(
    case: Optional[list[str]] = typer.Option(None, "--case", help="Run only this case ID (repeatable)"),
    json_out: Optional[Path] = typer.Option(None, "--json", dir_okay=False,
                                            help="Also write every check as JSON to this file; '-' prints JSON "
                                                 "instead of the markdown report"),
    cases_dir: Path = typer.Option(CASES, "--cases-dir", file_okay=False, show_default="benchmarks/orgmemory/cases",
                                   help="Directory holding the cases"),
    check: bool = typer.Option(False, "--check", help="Only validate the expectation files; replay nothing"),
) -> None:
    """Validate the labels, replay every case, and report each check. Exit 1 on any mismatch."""
    try:
        cases = load_cases(cases_dir, case)
    except (ValueError, OSError) as exc:
        typer.echo(f"error: {_terminal(str(exc))}", err=True)
        raise typer.Exit(2)
    if check:
        labels, checks = map(sum, zip(*(count_labels(c.expected) for c in cases)))
        typer.echo(f"{len(cases)} case(s), {labels} labels, {checks} checks: expectations OK")
        return
    result = evaluate(cases)
    # ASCII JSON: bidi and homoglyphs in subjects, actors and notes cannot reorder a terminal.
    # json.loads restores the original characters.
    data = json.dumps(result, indent=2, ensure_ascii=True, default=str) + "\n"
    if json_out is not None and str(json_out) == "-":
        typer.echo(data, nl=False)
    else:
        typer.echo(to_markdown(result), nl=False)
        if json_out is not None:
            json_out.write_bytes(data.encode("utf-8"))
            typer.echo(f"wrote {_one_line(str(json_out))}", err=True)
    if result["totals"]["failed"]:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
