import importlib.util
from datetime import date
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from memfalsify import jev, llm
from memfalsify.audit import MODEL_NOTE, audit
from memfalsify.cli import app
from memfalsify.jev import JevVerifier
from memfalsify.llm import SYSTEM, LLMVerifier, Verdict
from memfalsify.report import to_markdown
from memfalsify.verify import DualVerifier, VerifierError

runner = CliRunner()
CLAIM = "Customers can download every past invoice as an Excel spreadsheet."
COUNTER = "Invoice export to CSV is not available to customers."


class FakeMessages:
    def __init__(self, verdict=None, stop_reason="end_turn", error=None):
        self.verdict, self.stop_reason, self.error, self.calls = verdict, stop_reason, error, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.verdict)


def fake_client(**kwargs):
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(**kwargs)))


def nli(premise, hypothesis):
    """Stand-in judgment model: contradiction when the premise is negated, otherwise neutral."""
    if "not available" in premise:
        return {"ENTAILMENT": 0.03, "NEUTRAL": 0.07, "CONTRADICTION": 0.90}
    return {"entailment": 0.10, "neutral": 0.85, "contradiction": 0.05}


@pytest.fixture
def invoices(tmp_path):
    (tmp_path / "MEMORY.md").write_text(f"- {CLAIM}\n", encoding="utf-8")
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "billing.md").write_text(f"# Billing\n\n{COUNTER}\n", encoding="utf-8")
    return tmp_path


# --- LLM ---------------------------------------------------------------------------

def test_llm_verifier_request_and_mapping():
    client = fake_client(verdict=Verdict(relation="contradicts", rationale="CSV export is not available"))
    judgment = LLMVerifier(client=client).judge(CLAIM, COUNTER)
    assert (judgment.relation, judgment.rationale, judgment.verifier) == \
        ("contradicts", "CSV export is not available", "llm:claude-opus-5-5")
    call = client.beta.messages.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["system"] == SYSTEM and call["output_format"] is Verdict
    assert call["fallbacks"] == "default" and call["betas"] == [llm.FALLBACK_BETA]
    assert CLAIM in call["messages"][0]["content"] and COUNTER in call["messages"][0]["content"]


def test_llm_verifier_skips_fallbacks_for_models_without_them():
    client = fake_client(verdict=Verdict(relation="unrelated", rationale="-"))
    LLMVerifier("claude-haiku-5-5", client=client).judge(CLAIM, COUNTER)
    assert "fallbacks" not in client.beta.messages.calls[0]


def test_llm_refusal_gives_no_verdict():
    judgment = LLMVerifier(client=fake_client(stop_reason="refusal")).judge(CLAIM, COUNTER)
    assert judgment.relation == "unrelated" and "refusal" in judgment.rationale


def test_llm_api_error_becomes_verifier_error():
    anthropic = pytest.importorskip("anthropic")

    class Boom(anthropic.APIError):
        def __init__(self):
            Exception.__init__(self, "boom")

    with pytest.raises(VerifierError, match="boom"):
        LLMVerifier(client=fake_client(error=Boom())).judge(CLAIM, COUNTER)


# --- Jev ---------------------------------------------------------------------------

def test_jev_maps_labels_and_passes_premise_first():
    seen = []

    def classify(premise, hypothesis):
        seen.append((premise, hypothesis))
        return nli(premise, hypothesis)

    judgment = JevVerifier("fake", classify=classify).judge(CLAIM, COUNTER)
    assert seen == [(COUNTER, CLAIM)]
    assert judgment.relation == "contradicts" and judgment.confidence == 0.90
    assert judgment.rationale == "entailment 0.03, contradiction 0.90, neutral 0.07"


def test_jev_low_confidence_is_unrelated():
    weak = lambda p, h: {"entailment": 0.45, "neutral": 0.30, "contradiction": 0.25}
    judgment = JevVerifier("fake", classify=weak).judge(CLAIM, COUNTER)
    assert judgment.relation == "unrelated" and "below threshold" in judgment.rationale


def test_jev_rejects_models_without_nli_labels():
    with pytest.raises(VerifierError, match="lack"):
        JevVerifier("fake", classify=lambda p, h: {"LABEL_0": 0.5, "LABEL_1": 0.5}).judge(CLAIM, COUNTER)


# --- audit with model verifiers ----------------------------------------------------

def test_model_verifier_judges_paraphrases_lexical_skips(invoices):
    args = (invoices / "MEMORY.md", invoices / "evidence")
    assert audit(*args, as_of=date(2026, 10, 10)).findings[0].status == "insufficient_evidence"
    report = audit(*args, verifier=JevVerifier("fake", classify=nli), as_of=date(2026, 10, 10),
                   min_coverage=0.2, max_judged=8)
    finding = report.findings[0]
    assert finding.status == "candidate_counterevidence" and finding.evidence[0].span.quote == COUNTER
    assert report.verifier == "jev:fake" and report.limitations[0] == MODEL_NOTE


def test_max_judged_bounds_model_calls(invoices):
    (invoices / "evidence" / "more.md").write_text(
        "\n".join(f"Customers asked about invoice batch {n}." for n in range(5)) + "\n", encoding="utf-8")
    calls = []
    counting = JevVerifier("fake", classify=lambda p, h: calls.append(p) or nli(p, h))
    audit(invoices / "MEMORY.md", invoices / "evidence", verifier=counting, min_coverage=0.2, max_judged=2)
    assert len(calls) == 2


def test_dual_llm_jev_disagreement_is_disputed_with_both_opinions(invoices):
    model = LLMVerifier(client=fake_client(verdict=Verdict(relation="unrelated", rationale="CSV is not Excel")))
    dual = DualVerifier(model, JevVerifier("fake", classify=nli))
    report = audit(invoices / "MEMORY.md", invoices / "evidence", verifier=dual, min_coverage=0.2, max_judged=8)
    evidence = report.findings[0].evidence[0]
    assert report.findings[0].status == "disputed" and evidence.relation == "disputed"
    assert [o.verifier for o in evidence.opinions] == ["llm:claude-opus-5-5", "jev:fake"]
    markdown = to_markdown(report)
    assert "llm:claude-opus-5-5: unrelated · CSV is not Excel" in markdown
    assert "jev:fake: contradicts · entailment 0.03" in markdown


# --- CLI ---------------------------------------------------------------------------

def test_cli_llm_verifier_uses_looser_preselection(invoices, monkeypatch):
    client = fake_client(verdict=Verdict(relation="contradicts", rationale="not available"))
    real = llm.LLMVerifier
    monkeypatch.setattr(llm, "LLMVerifier", lambda model: real(model, client=client))
    result = runner.invoke(app, ["audit", str(invoices / "MEMORY.md"), "-e", str(invoices / "evidence"),
                                 "--verifier", "llm", "--llm-model", "claude-sonnet-5-5"])
    assert result.exit_code == 0 and "POSSIBLE COUNTEREVIDENCE" in result.output
    assert "Verifier: `llm:claude-sonnet-5-5`" in result.output
    assert len(client.beta.messages.calls) == 1


@pytest.mark.skipif(importlib.util.find_spec("transformers") is not None, reason="transformers is installed")
def test_cli_jev_without_dependencies_exits_2(invoices):
    result = runner.invoke(app, ["audit", str(invoices / "MEMORY.md"), "--verifier", "jev"])
    assert result.exit_code == 2 and "memfalsify[jev]" in result.output
