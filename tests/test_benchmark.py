import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from memfalsify import llm
from memfalsify.llm import Verdict
from memfalsify.verify import CascadeVerifier, DualVerifier, Judgment, LexicalVerifier, VerifierError

REPO = Path(__file__).resolve().parent.parent
RUN = REPO / "benchmarks" / "memfalsify" / "run.py"
_spec = importlib.util.spec_from_file_location("memfalsify_benchmark", RUN)
bench = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)
runner = CliRunner()

MEMORY = """# Service facts

- The public API rate limit is 100 requests per minute.
- Deployments always go through the staging cluster.
- Bulk export depends on the billing schema migration.
- The team prefers tabs for indentation.
- Priya owns the payments integration.
- We probably ship the bulk export next week.
- The cache uses Redis.
- Bulk export ships in November.
"""
EVIDENCE = {
    "api.md": "# API\n\nThe public API rate limit is 500 requests per minute.\n",
    "notes/decisions.md": "# Decision log\n\n"
                          "Bulk export depends on the billing schema migration.\n"
                          "Hotfix deployments skip the staging cluster.\n"
                          "Indentation uses tab characters by team convention.\n"
                          "The session store does not use Redis; the cache uses Redis.\n"
                          "Bulk export was moved to the January release.\n",
}
# (text, truth, issues, tags, evidence). With the lexical verifier the last two are misclassified:
# a negated distractor sentence flags the Redis claim, and the November paraphrase shares too few terms.
LABELS = [
    ("The public API rate limit is 100 requests per minute.", "contradicted", [], ["numeric"],
     "api.md: The public API rate limit is 500 requests per minute."),
    ("Deployments always go through the staging cluster.", "contradicted", ["overgeneralization"], ["negation"],
     "notes/decisions.md: Hotfix deployments skip the staging cluster."),
    ("Bulk export depends on the billing schema migration.", "supported", [], [],
     "notes/decisions.md: Bulk export depends on the billing schema migration."),
    ("The team prefers tabs for indentation.", "supported", [], ["paraphrase"],
     "notes/decisions.md: Indentation uses tab characters by team convention."),
    ("Priya owns the payments integration.", "unsupported", [], [], None),
    ("We probably ship the bulk export next week.", "unsupported", ["relative_time", "inference_marker"],
     ["temporal"], None),
    ("The cache uses Redis.", "supported", [], ["negation_trap", "distractor"],
     "notes/decisions.md: the cache uses Redis."),
    ("Bulk export ships in November.", "contradicted", [], ["paraphrase", "temporal"],
     "notes/decisions.md: Bulk export was moved to the January release."),
]


def label_records(labels=LABELS):
    return [{"text": text, "truth": truth, "issues": issues, "tags": tags, "evidence": evidence, "note": "synthetic"}
            for text, truth, issues, tags, evidence in labels]


def write_case(root: Path, records=None, as_of="2026-10-10") -> Path:
    for name, text in EVIDENCE.items():
        (root / "evidence" / name).parent.mkdir(parents=True, exist_ok=True)
        (root / "evidence" / name).write_text(text, encoding="utf-8")
    (root / "MEMORY.md").write_text(MEMORY, encoding="utf-8")
    (root / "case.json").write_text(json.dumps({"as_of": as_of, "description": "tiny synthetic case"}),
                                    encoding="utf-8")
    lines = [r if isinstance(r, str) else json.dumps(r) for r in (records or label_records())]
    (root / "labels.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def cases(tmp_path):
    write_case(tmp_path / "cases" / "tiny")
    return tmp_path / "cases"


class Fake:
    def __init__(self, name, decide):
        self.name, self.decide, self.calls = name, decide, []

    def judge(self, claim, passage):
        self.calls.append((claim, passage))
        relation, confidence = self.decide(claim, passage)
        return Judgment(relation=relation, rationale="fake", verifier=self.name, confidence=confidence)


# --- label validation ----------------------------------------------------------

def test_valid_case_loads_in_memory_order(cases):
    (case,) = bench.load_cases(cases)
    assert case.id == "tiny" and case.claims == [text for text, *_ in LABELS]
    assert str(case.info.as_of) == "2026-10-10"
    assert case.anchors["The cache uses Redis."][0] == "notes/decisions.md"
    assert "Priya owns the payments integration." not in case.anchors


def test_validation_lists_every_problem(tmp_path):
    records = label_records()
    records[0]["evidence"] = "api.md: The public API rate limit is 900 requests per minute."
    records[1]["evidence"] = "missing.md: Hotfix deployments skip the staging cluster."
    records[2]["text"] = "Bulk export depends on the billing migration."
    records[3]["evidence"] = None
    records[4]["evidence"] = "api.md: The public API"
    records[5]["truth"] = "maybe"
    records[6]["tags"] = ["vibes"]
    records[7]["evidence"] = "../../outside.md: secret"
    records.append(dict(records[7]))
    records.append("not json")
    write_case(tmp_path / "cases" / "broken", records)
    with pytest.raises(bench.BenchmarkError) as caught:
        bench.load_cases(tmp_path / "cases")
    message = str(caught.value)
    expected = [
        "case broken:",
        "labels.jsonl:1: quote not found verbatim in api.md",
        "labels.jsonl:2: cannot read evidence/missing.md",
        "labels.jsonl:3: not a claim of MEMORY.md: 'Bulk export depends on the billing migration.' "
        "(did you mean 'Bulk export depends on the billing schema migration.'?)",
        'labels.jsonl:4: a supported claim needs evidence "<path>: <exact quote>"',
        "labels.jsonl:5: evidence must be null for an unsupported claim",
        "labels.jsonl:6: truth: Input should be 'contradicted', 'supported' or 'unsupported'",
        "labels.jsonl:7: tags.0: Input should be 'paraphrase'",
        "labels.jsonl:8: evidence path escapes its root",
        "labels.jsonl:9: claim labeled twice: 'Bulk export ships in November.'",
        "labels.jsonl:10: record: Invalid JSON",
        "MEMORY.md: claim has no label: 'Bulk export depends on the billing schema migration.'",
        "MEMORY.md: claim has no label: 'We probably ship the bulk export next week.'",
        "MEMORY.md: claim has no label: 'The cache uses Redis.'",
    ]
    for fragment in expected:
        assert fragment in message
    assert message.count("\n  - ") == len(expected) - 1


def test_validation_of_case_files_and_selection(tmp_path):
    with pytest.raises(bench.BenchmarkError, match="no cases in"):
        bench.load_cases(tmp_path / "nowhere")
    (tmp_path / "cases" / "empty").mkdir(parents=True)
    with pytest.raises(bench.BenchmarkError) as caught:
        bench.load_cases(tmp_path / "cases")
    assert all(f"missing {name}" in str(caught.value)
               for name in ("MEMORY.md", "labels.jsonl", "case.json", "evidence/ directory"))
    with pytest.raises(bench.BenchmarkError, match=r"unknown case\(s\): nope \(available: empty\)"):
        bench.load_cases(tmp_path / "cases", ["nope"])
    shutil.rmtree(tmp_path / "cases" / "empty")
    write_case(tmp_path / "cases" / "dated")
    (tmp_path / "cases" / "dated" / "case.json").write_text('{"as_of": "2026-13-01", "description": "x", "x": 1}')
    with pytest.raises(bench.BenchmarkError) as caught:
        bench.load_cases(tmp_path / "cases")
    assert "case dated:\n  - case.json: " in str(caught.value)
    assert "as_of: Input should be a valid date" in str(caught.value) and "x: Extra inputs" in str(caught.value)


def test_duplicate_keys_in_case_and_labels_exit_2(cases):
    root = cases / "tiny"
    (root / "case.json").write_text(
        '{\n  "as_of": "2026-10-10",\n  "description": "a",\n  "description": "b"\n}\n', encoding="utf-8")
    result = runner.invoke(bench.app, ["--cases-dir", str(cases), "--check"])
    assert result.exit_code == 2, result.output
    assert "case.json:4: invalid JSON: duplicate key" in result.output

    (root / "case.json").write_text(
        '{\n  "as_of": "2026-10-10",\n  "description": {"text": "a", "text": "b"}\n}\n', encoding="utf-8")
    result = runner.invoke(bench.app, ["--cases-dir", str(cases), "--check"])
    assert result.exit_code == 2, result.output
    assert "case.json:3: invalid JSON: duplicate key" in result.output

    (root / "case.json").write_text(json.dumps({"as_of": "2026-10-10", "description": "tiny synthetic case"}),
                                    encoding="utf-8")
    (root / "labels.jsonl").write_text('{"text": "a", "text": "b"}\n', encoding="utf-8")
    result = runner.invoke(bench.app, ["--cases-dir", str(cases), "--check"])
    assert result.exit_code == 2, result.output
    assert "labels.jsonl:1: invalid JSON: duplicate key" in result.output

    (root / "labels.jsonl").write_text('{"text": "a", "note": {"x": 1, "x": 2}}\n', encoding="utf-8")
    result = runner.invoke(bench.app, ["--cases-dir", str(cases), "--check"])
    assert result.exit_code == 2, result.output
    assert "labels.jsonl:1: invalid JSON: duplicate key" in result.output


def test_unlabeled_and_unknown_claims_are_errors(tmp_path):
    write_case(tmp_path / "cases" / "partial", label_records(LABELS[:-1]))
    with pytest.raises(bench.BenchmarkError, match="claim has no label: 'Bulk export ships in November.'"):
        bench.load_cases(tmp_path / "cases")


# --- metric arithmetic ------------------------------------------------------------

def pred(truth, predicted, tags=(), issues=(), found=()):
    return bench.Prediction(case="c", memory_id="m", text=f"{truth} -> {predicted}", truth=truth,
                            predicted=predicted, status="", tags=list(tags), issues=list(issues),
                            predicted_issues=list(found), cites_labeled_passage=None, evidence=[])


HAND = [
    pred("contradicted", "contradicted", ["numeric"], ["overgeneralization"], ["overgeneralization"]),
    pred("contradicted", "disputed", ["antonym"]),
    pred("contradicted", "unsupported", ["paraphrase"], ["relative_time"]),
    pred("supported", "supported", ["paraphrase"]),
    pred("supported", "contradicted", ["negation_trap"]),
    pred("unsupported", "unsupported", [], [], ["inference_marker"]),
    pred("unsupported", "supported", ["distractor"], ["duplicate"], ["duplicate", "relative_time"]),
    pred("unsupported", "disputed"),
]


def test_metric_arithmetic_on_hand_computed_case():
    m = bench.score(HAND)
    # flagged = {1, 2, 5, 8}, labeled contradicted = {1, 2, 3}
    c = m["contradiction"]
    assert (c["tp"], c["fp"], c["fn"]) == (2, 2, 1)
    assert c["precision"] == 0.5 and c["recall"] == pytest.approx(2 / 3) and c["f1"] == pytest.approx(4 / 7)
    assert c["disputed"] == {"contradicted": 1, "supported": 0, "unsupported": 1}
    s = m["support"]
    assert (s["tp"], s["fp"], s["fn"], s["precision"], s["recall"], s["f1"]) == (1, 1, 1, 0.5, 0.5, 0.5)
    assert m["accuracy"] == {"correct": 3, "total": 8, "rate": 0.375}
    assert m["confusion"] == {
        "contradicted": {"contradicted": 1, "supported": 0, "unsupported": 1, "disputed": 1},
        "supported": {"contradicted": 1, "supported": 1, "unsupported": 0, "disputed": 0},
        "unsupported": {"contradicted": 0, "supported": 1, "unsupported": 1, "disputed": 1},
    }
    i = m["issues"]
    assert (i["overgeneralization"]["precision"], i["overgeneralization"]["recall"]) == (1.0, 1.0)
    assert (i["relative_time"]["tp"], i["relative_time"]["fp"], i["relative_time"]["fn"]) == (0, 1, 1)
    assert (i["relative_time"]["precision"], i["relative_time"]["recall"]) == (0.0, 0.0)
    assert (i["inference_marker"]["precision"], i["inference_marker"]["recall"]) == (0.0, None)
    assert (i["duplicate"]["precision"], i["duplicate"]["recall"]) == (1.0, 1.0)
    assert i["possibly_stale"] == {"tp": 0, "fp": 0, "fn": 0, "precision": None, "recall": None, "f1": None}


def test_breakdown_by_tag():
    tags = bench.score(HAND)["tags"]
    assert list(tags) == ["paraphrase", "numeric", "antonym", "negation_trap", "distractor"]
    rate = lambda hits, n: {"hits": hits, "n": n, "rate": hits / n if n else None}
    assert tags["paraphrase"] == {"claims": 2, "contradiction_recall": rate(0, 1), "support_recall": rate(1, 1),
                                  "false_flags": rate(0, 1)}
    assert tags["antonym"]["contradiction_recall"] == rate(1, 1)  # disputed counts as flagged
    assert tags["negation_trap"]["support_recall"] == rate(0, 1)
    assert tags["negation_trap"]["false_flags"] == rate(1, 1)
    assert tags["distractor"]["support_recall"] == rate(0, 0) and tags["distractor"]["false_flags"] == rate(0, 1)


def test_empty_rates_render_as_not_applicable():
    assert bench.prf(0, 0, 3) == {"tp": 0, "fp": 0, "fn": 3, "precision": None, "recall": 0.0, "f1": 0.0}
    assert bench._frac({"hits": 0, "n": 0, "rate": None}) == "–" and bench._num(None) == "n/a"


# --- call counting -----------------------------------------------------------------

def test_counting_verifier_counts_calls_and_two_opinion_judgments():
    first = Fake("first", lambda c, p: ("supports", 0.9 if p == "clear" else 0.3))
    second = Fake("second", lambda c, p: ("unrelated", None))
    counter = bench.CountingVerifier(CascadeVerifier(first, second, accept_at=0.8))
    for passage in ("clear", "vague", "clear", "vague"):
        counter.judge("claim", passage)
    assert (counter.name, counter.calls, counter.two_opinions) == ("first>second", 4, 2)


def test_cascade_cost_matches_backend_calls(cases):
    shutil.copytree(cases / "tiny", cases / "tiny-copy")
    unsure = ("staging", "Redis")
    first = Fake("first", lambda c, p: ("supports", 0.4) if any(w in p for w in unsure) else ("unrelated", 0.9))
    lexical = LexicalVerifier()
    second = Fake("second", lambda c, p: (lexical.judge(c, p).relation, None))
    choice = bench.VerifierChoice.cascade
    result = bench.evaluate(bench.load_cases(cases), CascadeVerifier(first, second), **bench.preselection(choice))
    assert result["verifier"] == "first>second"
    assert result["settings"] == {"min_coverage": 0.2, "max_judged": 8}
    assert result["cost"]["judge_calls"] == len(first.calls) > 0
    assert 0 < result["cost"]["escalations"] == len(second.calls) < len(first.calls)
    per_case = [(x["judge_calls"], x["escalations"]) for x in result["cases"]]
    assert per_case[0] == per_case[1] and sum(n for n, _ in per_case) == len(first.calls)


def test_dual_disagreements_are_flagged_and_counted_as_disputed(cases):
    dual = DualVerifier(LexicalVerifier(), Fake("never", lambda c, p: ("unrelated", None)))
    result = bench.evaluate(bench.load_cases(cases), dual, **bench.preselection(bench.VerifierChoice.lexical))
    m = result["metrics"]
    assert result["cost"]["escalations"] is None
    # Every lexical verdict is disputed: both supported claims it got right are now (false) flags too.
    assert (m["contradiction"]["tp"], m["contradiction"]["fp"], m["contradiction"]["fn"]) == (2, 3, 1)
    assert m["contradiction"]["disputed"] == {"contradicted": 2, "supported": 3, "unsupported": 0}
    assert m["support"]["precision"] is None and m["support"]["recall"] == 0.0
    assert m["accuracy"]["correct"] == 2
    assert m["citations"]["contradicted"] == {"hits": 2, "n": 2, "rate": 1.0}
    opinions = result["predictions"][0]["evidence"][0]["opinions"]
    assert [(o["verifier"], o["relation"]) for o in opinions] == [("lexical", "contradicts"), ("never", "unrelated")]


# --- end to end ---------------------------------------------------------------------

EXPECTED_LEXICAL = {
    "The public API rate limit is 100 requests per minute.": ("contradicted", "candidate_counterevidence"),
    "Deployments always go through the staging cluster.": ("contradicted", "candidate_counterevidence"),
    "Bulk export depends on the billing schema migration.": ("supported", "candidate_support"),
    "The team prefers tabs for indentation.": ("supported", "candidate_support"),
    "Priya owns the payments integration.": ("unsupported", "insufficient_evidence"),
    "We probably ship the bulk export next week.": ("unsupported", "insufficient_evidence"),
    "The cache uses Redis.": ("contradicted", "candidate_counterevidence"),
    "Bulk export ships in November.": ("unsupported", "insufficient_evidence"),
}


def test_run_script_end_to_end_with_lexical_verifier(cases, tmp_path):
    out = tmp_path / "result.json"
    done = subprocess.run([sys.executable, str(RUN), "--cases-dir", str(cases), "--json", str(out)],
                          cwd=REPO, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    markdown = done.stdout
    assert markdown.startswith("# MemFalsify benchmark")
    assert "- Verifier: `lexical` · min_coverage 0.5 · max_judged unbounded" in markdown
    assert "| 0.67 | 0.67 | 0.67 | 2 | 1 | 1 |" in markdown  # contradiction detection
    assert "| 1.00 | 0.67 | 0.80 | 2 | 0 | 1 |" in markdown  # support detection
    assert "0.75 (6/8; disputed counts as wrong)" in markdown
    assert "| supported | 1 | 2 | 0 | 0 |" in markdown
    assert "| negation_trap | 1 | – | 0/1 (0.00) | 1/1 (1.00) |" in markdown
    assert "| tiny | supported | contradicted | negation_trap, distractor | The cache uses Redis. |" in markdown
    result = json.loads(out.read_text(encoding="utf-8"))
    assert out.read_bytes() == (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    assert result["settings"] == {"min_coverage": 0.5, "max_judged": None, "verifier": "lexical"}
    assert {p["text"]: (p["predicted"], p["status"]) for p in result["predictions"]} == EXPECTED_LEXICAL
    assert [p["text"] for p in result["predictions"]] == [text for text, *_ in LABELS]
    assert result["metrics"]["citations"] == {"contradicted": {"hits": 2, "n": 2, "rate": 1.0},
                                              "supported": {"hits": 2, "n": 2, "rate": 1.0}}
    issues = result["metrics"]["issues"]
    assert all(issues[k]["precision"] == issues[k]["recall"] == 1.0
               for k in ("overgeneralization", "relative_time", "inference_marker"))
    assert result["cost"]["judge_calls"] == result["cases"][0]["judge_calls"] > 0
    assert result["cost"]["escalations"] is None


def test_cli_check_selection_and_errors(cases):
    write_case(cases / "other")
    result = runner.invoke(bench.app, ["--cases-dir", str(cases), "--case", "other", "--verifier", "jev", "--check"])
    assert result.exit_code == 0 and result.output == "1 case(s), 8 labeled claims: labels OK\n"
    (cases / "other" / "labels.jsonl").write_text("", encoding="utf-8")
    result = runner.invoke(bench.app, ["--cases-dir", str(cases), "--case", "tiny"])
    assert result.exit_code == 0 and "- Cases: 1 (tiny) · claims: 8" in result.output
    result = runner.invoke(bench.app, ["--cases-dir", str(cases)])
    assert result.exit_code == 2 and "case other:" in result.output and "claim has no label" in result.output


def test_cli_model_verifier_uses_cli_preselection(cases, tmp_path, monkeypatch):
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=Verdict(relation="unrelated", rationale="-"))

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    real = llm.LLMVerifier
    monkeypatch.setattr(llm, "LLMVerifier", lambda model: real(model, client=client))
    out = tmp_path / "llm.json"
    result = runner.invoke(bench.app, ["--cases-dir", str(cases), "--verifier", "llm", "--llm-model",
                                       "claude-sonnet-5-5", "--json", str(out)])
    assert result.exit_code == 0, result.output
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["verifier"] == "llm:claude-sonnet-5-5"
    assert saved["settings"] == {"min_coverage": 0.2, "max_judged": 8, "verifier": "llm"}
    assert saved["cost"]["judge_calls"] == len(calls) > 0
    assert saved["metrics"]["confusion"]["contradicted"]["unsupported"] == 3


def test_cli_openai_verifier_takes_the_endpoint_options(cases, tmp_path, monkeypatch):
    from memfalsify import openai_compat

    calls, built = [], []

    def create(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(content='{"relation": "unrelated", "rationale": "-"}', refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)], usage=None)

    real = openai_compat.OpenAICompatVerifier

    def make(model, provider, base_url, api_key_env, **kwargs):
        built.append((model, provider, base_url, api_key_env))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return real(model, provider, base_url, api_key_env, client=client, **kwargs)

    monkeypatch.setattr(openai_compat, "OpenAICompatVerifier", make)
    out = tmp_path / "openai.json"
    result = runner.invoke(bench.app, ["--cases-dir", str(cases), "--verifier", "openai", "--provider", "ollama",
                                       "--openai-model", "llama-local", "--base-url", "http://127.0.0.1:8080/v1",
                                       "--json", str(out)])
    assert result.exit_code == 0, result.output
    assert built == [("llama-local", "ollama", "http://127.0.0.1:8080/v1", None)]
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["verifier"] == "openai:llama-local@127.0.0.1:8080"
    assert saved["settings"] == {"min_coverage": 0.2, "max_judged": 8, "verifier": "openai", "provider": "ollama",
                                 "response_format": "json_schema"}
    assert saved["cost"]["judge_calls"] == len(calls) > 0
    result = runner.invoke(bench.app, ["--cases-dir", str(cases), "--verifier", "openai"])
    assert result.exit_code == 2 and "needs --openai-model" in result.output


def test_cli_verifier_errors_exit_2(cases, monkeypatch):
    def broken(*args):
        raise VerifierError("jev: set TYPESAFE_API_KEY")

    monkeypatch.setattr(bench, "build_verifier", broken)
    result = runner.invoke(bench.app, ["--cases-dir", str(cases), "--verifier", "jev"])
    assert result.exit_code == 2 and "error: jev: set TYPESAFE_API_KEY" in result.output
