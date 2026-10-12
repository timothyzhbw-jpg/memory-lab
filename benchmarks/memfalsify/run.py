"""MemFalsify benchmark: audit labeled cases and score each memory's status against its label.

    python benchmarks/memfalsify/run.py [--verifier lexical|llm|jev|dual|cascade|openai] [--case ID]... [--json OUT]
    python benchmarks/memfalsify/run.py --verifier openai --provider ollama --openai-model MODEL

The case format and every metric are defined in README.md next to this file.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import date
from difflib import get_close_matches
from pathlib import Path
from typing import Literal, Optional, get_args

ROOT = Path(__file__).resolve().parents[2]
for _package in ("memcore", "memfalsify"):  # benchmark this checkout, not whatever copy is installed
    if (_src := str(ROOT / "packages" / _package / "src")) not in sys.path:
        sys.path.insert(0, _src)

import typer  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field, ValidationError  # noqa: E402

from memcore import EvidenceError, locate_quote, resolve_inside  # noqa: E402
from memcore.jsonutil import loads  # noqa: E402
from memfalsify import __version__, jev, llm, openai_compat  # noqa: E402
from memfalsify import AuditReport, Issue, audit  # noqa: E402
from memfalsify.cli import MAX_JUDGED, VerifierChoice, build_verifier, preselection  # noqa: E402
from memfalsify import parse_memory  # noqa: E402
from memfalsify import CascadeVerifier, Judgment, Verifier, VerifierError  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"
Truth = Literal["contradicted", "supported", "unsupported"]
Predicted = Literal["contradicted", "supported", "unsupported", "disputed"]
IssueKind = Issue.model_fields["kind"].annotation
Tag = Literal["paraphrase", "numeric", "antonym", "negation", "negation_trap", "distractor", "temporal",
              "chinese", "repo", "multi_sentence"]
TRUTHS: tuple[str, ...] = get_args(Truth)
PREDICTED: tuple[str, ...] = get_args(Predicted)
ISSUES: tuple[str, ...] = get_args(IssueKind)
TAGS: tuple[str, ...] = get_args(Tag)
# Audit status -> predicted relation. Reviews (challenged/supported/conflicted) are never given by the benchmark.
RELATION = {"candidate_counterevidence": "contradicted", "challenged": "contradicted",
            "candidate_support": "supported", "supported": "supported",
            "insufficient_evidence": "unsupported", "disputed": "disputed", "conflicted": "disputed"}
FLAGGED = {"contradicted", "disputed"}  # the predictions whose statuses make `memfalsify audit --strict` fail
CITED = {"contradicted": {"contradicts", "disputed"}, "supported": {"supports"}}


class BenchmarkError(ValueError):
    """A case or the case selection is malformed; the message lists every problem found."""


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    truth: Truth
    issues: list[IssueKind]
    tags: list[Tag]
    evidence: Optional[str]
    note: str = Field(min_length=1)


class CaseInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    description: str = Field(min_length=1)


class Case(BaseModel):
    id: str
    root: Path
    info: CaseInfo
    claims: list[str]
    labels: dict[str, Label]
    anchors: dict[str, tuple[str, int, int]]  # claim text -> (path, start, end) of its labeled passage


class Prediction(BaseModel):
    case: str
    memory_id: str
    text: str
    truth: Truth
    predicted: Predicted
    status: str
    tags: list[str]
    issues: list[str]
    predicted_issues: list[str]
    cites_labeled_passage: Optional[bool]
    evidence: list[dict]


def _errors(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'record'}: {e['msg']}" for e in exc.errors())


def load_case(root: Path) -> Case:
    """Read one case and check its labels against MEMORY.md and the evidence directory."""
    root = Path(root)
    problems = [f"missing {name}" for name in ("MEMORY.md", "labels.jsonl", "case.json")
                if not (root / name).is_file()]
    evidence = root / "evidence"
    if not evidence.is_dir():
        problems.append("missing evidence/ directory")
    if problems:
        raise BenchmarkError(f"case {root.name}:\n" + "\n".join(f"  - {p}" for p in problems))
    info = None
    case_text = (root / "case.json").read_text(encoding="utf-8")
    # loads() rejects a repeated key. model_validate_json reports every other problem,
    # and it is only called after that check, because it would keep the last value.
    duplicate_case_key = False
    try:
        loads(case_text)
    except json.JSONDecodeError as exc:
        if exc.msg == "duplicate key":
            problems.append(f"case.json:{exc.lineno}: invalid JSON: duplicate key")
            duplicate_case_key = True
    if not duplicate_case_key:
        try:
            info = CaseInfo.model_validate_json(case_text)
        except ValidationError as exc:
            problems.append(f"case.json: {_errors(exc)}")
    claims = [c.text for c in parse_memory(root / "MEMORY.md")[0]]
    labels: dict[str, Label] = {}
    anchors: dict[str, tuple[str, int, int]] = {}
    for number, line in enumerate((root / "labels.jsonl").read_text(encoding="utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        where = f"labels.jsonl:{number}"
        try:
            loads(line)
        except json.JSONDecodeError as exc:
            if exc.msg == "duplicate key":
                problems.append(f"{where}: invalid JSON: duplicate key")
                continue
        try:
            label = Label.model_validate_json(line)
        except ValidationError as exc:
            problems.append(f"{where}: {_errors(exc)}")
            continue
        if label.text in labels:
            problems.append(f"{where}: claim labeled twice: {label.text!r}")
            continue
        if label.text not in claims:
            close = get_close_matches(label.text, claims, n=1)
            problems.append(f"{where}: not a claim of MEMORY.md: {label.text!r}"
                            + (f" (did you mean {close[0]!r}?)" if close else ""))
        labels[label.text] = label
        if label.truth == "unsupported":
            if label.evidence is not None:
                problems.append(f"{where}: evidence must be null for an unsupported claim")
            continue
        path, _, quote = (label.evidence or "").partition(": ")
        if not path or not quote:
            problems.append(f'{where}: a {label.truth} claim needs evidence "<path>: <exact quote>"')
            continue
        try:
            span = locate_quote(evidence, path, quote)
            anchors[label.text] = (resolve_inside(evidence, path).relative_to(evidence.resolve()).as_posix(),
                                   span.start, span.end)
        except EvidenceError as exc:
            problems.append(f"{where}: {exc}")
        except OSError as exc:
            problems.append(f"{where}: cannot read evidence/{path}: {exc.strerror}")
    problems += [f"MEMORY.md: claim has no label: {text!r}" for text in claims if text not in labels]
    if problems:
        raise BenchmarkError(f"case {root.name}:\n" + "\n".join(f"  - {p}" for p in problems))
    return Case(id=root.name, root=root, info=info, claims=claims, labels=labels, anchors=anchors)


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


class CountingVerifier:
    """Pass judgments through, counting judge() calls and judgments that carry two opinions."""

    def __init__(self, inner: Verifier):
        self.inner, self.name = inner, inner.name
        self.calls = self.two_opinions = 0

    def judge(self, claim: str, passage: str) -> Judgment:
        self.calls += 1
        judgment = self.inner.judge(claim, passage)
        self.two_opinions += len(judgment.opinions) == 2
        return judgment


def predict(case: Case, report: AuditReport) -> list[Prediction]:
    out = []
    for finding in sorted(report.findings, key=lambda f: case.claims.index(f.text)):
        label = case.labels[finding.text]
        predicted = RELATION[finding.status]
        detected = predicted in FLAGGED if label.truth == "contradicted" else predicted == label.truth
        cites = None
        if detected and label.truth in CITED:
            path, start, end = case.anchors[finding.text]
            cites = any(e.relation in CITED[label.truth] and e.span.path == path
                        and e.span.start < end and start < e.span.end for e in finding.evidence)
        out.append(Prediction(
            case=case.id, memory_id=finding.memory_id, text=finding.text, truth=label.truth, predicted=predicted,
            status=finding.status, tags=label.tags, issues=label.issues,
            predicted_issues=list(dict.fromkeys(i.kind for i in finding.issues)), cites_labeled_passage=cites,
            evidence=[{"relation": e.relation, "location": e.span.location, "quote": e.span.quote,
                       "rationale": e.rationale, "opinions": [o.model_dump(exclude={"opinions"}) for o in e.opinions]}
                      for e in finding.evidence]))
    return out


def _ratio(hits: int, total: int) -> float | None:
    return hits / total if total else None


def prf(tp: int, fp: int, fn: int) -> dict:
    """Precision, recall and F1 = 2TP / (2TP + FP + FN); None where the denominator is zero."""
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
            "f1": _ratio(2 * tp, 2 * tp + fp + fn)}


def _rate(predictions: list[Prediction], hit) -> dict:
    hits = sum(1 for p in predictions if hit(p))
    return {"hits": hits, "n": len(predictions), "rate": _ratio(hits, len(predictions))}


def score(predictions: list[Prediction]) -> dict:
    """Every metric README.md defines, from (truth, prediction) pairs alone."""
    pairs = Counter((p.truth, p.predicted) for p in predictions)
    confusion = {t: {q: pairs[t, q] for q in PREDICTED} for t in TRUTHS}
    row = confusion["contradicted"]
    contradiction = prf(row["contradicted"] + row["disputed"],
                        sum(confusion[t][q] for t in TRUTHS if t != "contradicted" for q in FLAGGED),
                        row["supported"] + row["unsupported"])
    contradiction["disputed"] = {t: confusion[t]["disputed"] for t in TRUTHS}
    support = prf(confusion["supported"]["supported"],
                  sum(confusion[t]["supported"] for t in TRUTHS if t != "supported"),
                  sum(n for q, n in confusion["supported"].items() if q != "supported"))
    correct = sum(confusion[t][t] for t in TRUTHS)
    issues = {}
    for kind in ISSUES:
        found = Counter((kind in p.issues, kind in p.predicted_issues) for p in predictions)
        issues[kind] = prf(found[True, True], found[False, True], found[True, False])
    tags = {}
    for tag in TAGS:
        tagged = [p for p in predictions if tag in p.tags]
        if tagged:
            tags[tag] = {
                "claims": len(tagged),
                "contradiction_recall": _rate([p for p in tagged if p.truth == "contradicted"],
                                              lambda p: p.predicted in FLAGGED),
                "support_recall": _rate([p for p in tagged if p.truth == "supported"],
                                        lambda p: p.predicted == "supported"),
                "false_flags": _rate([p for p in tagged if p.truth != "contradicted"],
                                     lambda p: p.predicted in FLAGGED),
            }
    citations = {truth: _rate([p for p in predictions if p.truth == truth and p.cites_labeled_passage is not None],
                              lambda p: p.cites_labeled_passage) for truth in CITED}
    return {"claims": len(predictions), "contradiction": contradiction, "support": support,
            "accuracy": {"correct": correct, "total": len(predictions), "rate": _ratio(correct, len(predictions))},
            "confusion": confusion, "issues": issues, "tags": tags, "citations": citations}


def evaluate(cases: list[Case], verifier: Verifier, min_coverage: float = 0.5,
             max_judged: int | None = None) -> dict:
    """Audit every case with ``verifier`` (no reviews) and score the statuses against the labels."""
    counter = CountingVerifier(verifier)
    cascade = isinstance(verifier, CascadeVerifier)
    predictions, per_case = [], []
    started = time.monotonic()
    for case in cases:
        calls, escalations = counter.calls, counter.two_opinions
        report = audit(case.root / "MEMORY.md", case.root / "evidence", verifier=counter, as_of=case.info.as_of,
                       min_coverage=min_coverage, max_judged=max_judged)
        found = predict(case, report)
        predictions += found
        per_case.append({"id": case.id, "description": case.info.description, "as_of": case.info.as_of.isoformat(),
                         "judge_calls": counter.calls - calls,
                         "escalations": counter.two_opinions - escalations if cascade else None,
                         "metrics": score(found)})
    return {"memfalsify_version": __version__, "verifier": verifier.name,
            "settings": {"min_coverage": min_coverage, "max_judged": max_judged},
            "cost": {"judge_calls": counter.calls, "escalations": counter.two_opinions if cascade else None,
                     "seconds": round(time.monotonic() - started, 3)},
            "metrics": score(predictions), "cases": per_case,
            "predictions": [p.model_dump() for p in predictions]}


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _frac(rate: dict) -> str:
    return "–" if not rate["n"] else f"{rate['hits']}/{rate['n']} ({_num(rate['rate'])})"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(result: dict) -> str:
    m, cost, settings = result["metrics"], result["cost"], result["settings"]
    c, s, a = m["contradiction"], m["support"], m["accuracy"]
    spend = f"{cost['judge_calls']} judge() calls"
    if cost["escalations"] is not None:
        spend += f", {cost['escalations']} escalated to the second verifier"
    out = ["# MemFalsify benchmark", "",
           f"- Verifier: `{result['verifier']}` · min_coverage {settings['min_coverage']} · "
           f"max_judged {settings['max_judged'] if settings['max_judged'] is not None else 'unbounded'}"
           + (f" · accept_at {settings['accept_at']}" if "accept_at" in settings else ""),
           f"- Cases: {len(result['cases'])} ({', '.join(x['id'] for x in result['cases'])}) · claims: {m['claims']}",
           f"- Cost: {spend} · {cost['seconds']:.1f} s", "",
           "## Contradiction detection", "",
           "Flagged = predicted contradicted or disputed (the statuses that fail `--strict`); "
           "positive = labeled contradicted.", "",
           "| Precision | Recall | F1 | TP | FP | FN |", "|---|---|---|---|---|---|",
           f"| {_num(c['precision'])} | {_num(c['recall'])} | {_num(c['f1'])} | {c['tp']} | {c['fp']} | {c['fn']} |",
           "", "Disputed predictions by label: "
           + ", ".join(f"{t} {n}" for t, n in c["disputed"].items()) + f" (total {sum(c['disputed'].values())})", "",
           "## Support detection", "",
           "| Precision | Recall | F1 | TP | FP | FN |", "|---|---|---|---|---|---|",
           f"| {_num(s['precision'])} | {_num(s['recall'])} | {_num(s['f1'])} | {s['tp']} | {s['fp']} | {s['fn']} |",
           "", "## 3-way accuracy", "",
           f"{_num(a['rate'])} ({a['correct']}/{a['total']}; disputed counts as wrong)", "",
           "| Label \\ predicted | " + " | ".join(PREDICTED) + " |", "|---" * (len(PREDICTED) + 1) + "|"]
    out += [f"| {t} | " + " | ".join(str(m["confusion"][t][q]) for q in PREDICTED) + " |" for t in TRUTHS]
    out += ["", "Detections citing the labeled passage: "
            + ", ".join(f"{truth} {_frac(rate)}" for truth, rate in m["citations"].items()), "",
            "## Issue detection", "", "| Issue | Labeled | Predicted | Precision | Recall |", "|---|---|---|---|---|"]
    out += [f"| {kind} | {i['tp'] + i['fn']} | {i['tp'] + i['fp']} | {_num(i['precision'])} | {_num(i['recall'])} |"
            for kind, i in m["issues"].items()]
    out += ["", "## By tag", "",
            "| Tag | Claims | Contradiction recall | Support recall | False flags |", "|---|---|---|---|---|"]
    out += [f"| {tag} | {t['claims']} | {_frac(t['contradiction_recall'])} | {_frac(t['support_recall'])} | "
            f"{_frac(t['false_flags'])} |" for tag, t in m["tags"].items()]
    out += ["", "## By case", "",
            "| Case | Claims | Accuracy | Contradiction P / R | Support P / R | judge() calls |",
            "|---|---|---|---|---|---|"]
    for x in result["cases"]:
        cm = x["metrics"]
        out.append(f"| {x['id']} | {cm['claims']} | {_num(cm['accuracy']['rate'])} | "
                   f"{_num(cm['contradiction']['precision'])} / {_num(cm['contradiction']['recall'])} | "
                   f"{_num(cm['support']['precision'])} / {_num(cm['support']['recall'])} | {x['judge_calls']} |")
    wrong = [p for p in result["predictions"] if p["predicted"] != p["truth"]]
    if wrong:
        out += ["", "## Misclassified claims", "", "| Case | Label | Predicted | Tags | Claim |",
                "|---|---|---|---|---|"]
        out += [f"| {p['case']} | {p['truth']} | {p['predicted']} | {', '.join(p['tags'])} | {_cell(p['text'])} |"
                for p in wrong]
    return "\n".join(out) + "\n"


app = typer.Typer(add_completion=False, help="Score memfalsify verifiers on the labeled benchmark cases.")


@app.command()
def main(
    verifier: VerifierChoice = typer.Option(VerifierChoice.lexical, "--verifier",
                                            help="lexical (offline), llm, jev, dual (llm + jev), "
                                                 "cascade (jev, escalating to llm) or openai (an "
                                                 "OpenAI-compatible Chat Completions API)"),
    llm_model: str = typer.Option(llm.DEFAULT_MODEL, help="Claude model for --verifier llm/dual/cascade"),
    jev_model: str = typer.Option(jev.DEFAULT_MODEL, help="TypeSafe model for --verifier jev/dual/cascade"),
    openai_model: Optional[str] = typer.Option(None, "--openai-model",
                                               help="--verifier openai: the model, as the provider names it "
                                                    "(required; no default)"),
    provider: openai_compat.Provider = typer.Option(
        openai_compat.Provider.openai, "--provider",
        help="--verifier openai: preset base URL and key variable (openai, grok, deepseek, gemini, ollama)"),
    base_url: Optional[str] = typer.Option(None, "--base-url", metavar="URL",
                                           help="--verifier openai: base URL overriding the provider's (https, "
                                                "or http only for localhost, 127.0.0.1 or ::1)"),
    api_key_env: Optional[str] = typer.Option(None, "--api-key-env", metavar="NAME",
                                              help="--verifier openai: name of the environment variable holding "
                                                   "the API key (never the key itself)"),
    openai_response_format: Optional[openai_compat.ResponseFormat] = typer.Option(
        None, "--openai-response-format",
        help="--verifier openai: json_schema, json_object or text (default: json_object for deepseek, json_schema "
             "otherwise)"),
    accept_at: float = typer.Option(0.8, min=0.0, max=1.0, help="--verifier cascade: accept Jev at this confidence"),
    case: Optional[list[str]] = typer.Option(None, "--case", help="Run only this case ID (repeatable)"),
    json_out: Optional[Path] = typer.Option(None, "--json", dir_okay=False,
                                            help="Also write all numbers and per-claim predictions here"),
    cases_dir: Path = typer.Option(CASES, "--cases-dir", file_okay=False, show_default="benchmarks/memfalsify/cases",
                                   help="Directory holding the cases"),
    max_judged: int = typer.Option(MAX_JUDGED, min=1, help="Model verifiers: passages judged per memory"),
    min_coverage: Optional[float] = typer.Option(None, min=0.0, max=1.0,
                                                 help="Passage pre-selection (default 0.5 lexical, 0.2 models)"),
    check: bool = typer.Option(False, "--check", help="Only validate the cases' labels; call no verifier"),
) -> None:
    """Validate the labels, audit every case, and print a markdown report."""
    try:
        cases = load_cases(cases_dir, case)
        if check:
            typer.echo(f"{len(cases)} case(s), {sum(len(c.claims) for c in cases)} labeled claims: labels OK")
            return
        if verifier is VerifierChoice.openai:
            judge = build_verifier(verifier, llm_model, jev_model, accept_at, openai=openai_compat.OpenAISettings(
                openai_model, provider.value, base_url, api_key_env,
                openai_response_format.value if openai_response_format else None))
        else:
            judge = build_verifier(verifier, llm_model, jev_model, accept_at)
        result = evaluate(cases, judge, **preselection(verifier, min_coverage, max_judged))
    except (ValueError, OSError, VerifierError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2)
    result["settings"]["verifier"] = verifier.value  # model names are part of result["verifier"]
    if verifier is VerifierChoice.cascade:
        result["settings"]["accept_at"] = accept_at
    if verifier is VerifierChoice.openai:  # the model and host are part of result["verifier"]
        result["settings"]["provider"] = provider.value
        result["settings"]["response_format"] = judge.response_format
    typer.echo(to_markdown(result), nl=False)
    if json_out:
        json_out.write_bytes((json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        typer.echo(f"wrote {json_out}", err=True)


if __name__ == "__main__":
    app()
