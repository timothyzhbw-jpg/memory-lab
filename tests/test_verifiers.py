import importlib.util
import json
from datetime import date
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from memfalsify import llm
from memfalsify.audit import MODEL_NOTE, audit
from memfalsify.cli import app
from memfalsify.jev import JevVerifier
from memfalsify.llm import SYSTEM, LLMVerifier, Verdict
from memfalsify.report import to_markdown
from memfalsify.verify import RELATION_CRITERIA, DualVerifier, VerifierError

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


def jev_answer(state):
    """Stand-in Jev decision: contradiction when the passage is negated, otherwise unrelated."""
    if "not available" in state["passage"]:
        return "contradicts", 0.90, {"supports": 0.03, "contradicts": 0.90, "unrelated": 0.07}
    return "unrelated", 0.85, {"supports": 0.10, "contradicts": 0.05, "unrelated": 0.85}


def mock_jev(decide=jev_answer, requests=None, status=200, threshold=0.5):
    """A JevVerifier on the real TypeSafe SDK, with HTTP answered locally instead of by api.typesafe.ai."""
    typesafe = pytest.importorskip("typesafe_sdk")
    httpx2 = pytest.importorskip("httpx2")

    def handler(request):
        body = json.loads(request.content)
        if requests is not None:
            requests.append((request, body))
        if status != 200:
            return httpx2.Response(status, json={"detail": "denied"})
        choice, confidence, probabilities = decide(body["state"])
        return httpx2.Response(200, json={
            "model": "jev-1.13.0", "usage": {"input_tokens": 60, "output_tokens": 1},
            "answers": {"relation": {"type": "choice", "choice": choice, "confidence": confidence,
                                     "probabilities": probabilities}}})

    client = typesafe.TypeSafeClient(api_key="test-key", transport=httpx2.MockTransport(handler),
                                     retry=typesafe.RetryPolicy(max_retries=0))
    return JevVerifier(client=client, threshold=threshold)


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

def test_jev_request_and_mapping():
    requests = []
    judgment = mock_jev(requests=requests).judge(CLAIM, COUNTER)
    assert judgment.relation == "contradicts" and judgment.confidence == 0.90
    assert judgment.verifier == "jev:jev-latest"
    assert judgment.rationale == "supports 0.03, contradicts 0.90, unrelated 0.07 (jev-1.13.0)"
    request, body = requests[0]
    assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == "jev-latest" and body["state"] == {"claim": CLAIM, "passage": COUNTER}
    question = body["questions"]["relation"]
    assert question["type"] == "choice" and question["criteria"] == RELATION_CRITERIA
    assert question["criteria"] == {line.split(": ", 1)[0][2:]: line.split(": ", 1)[1]
                                    for line in SYSTEM.splitlines() if line.startswith("- ")}


def test_jev_low_confidence_is_unrelated():
    weak = lambda state: ("supports", 0.45, {"supports": 0.45, "contradicts": 0.25, "unrelated": 0.30})
    judgment = mock_jev(weak).judge(CLAIM, COUNTER)
    assert judgment.relation == "unrelated" and "below threshold 0.5" in judgment.rationale


def test_jev_api_error_becomes_verifier_error():
    with pytest.raises(VerifierError, match="401"):
        mock_jev(status=401).judge(CLAIM, COUNTER)


def test_jev_without_api_key_fails_clearly(monkeypatch):
    pytest.importorskip("typesafe_sdk")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(VerifierError, match="TYPESAFE_API_KEY"):
        JevVerifier()


# --- audit with model verifiers ----------------------------------------------------

def test_model_verifier_judges_paraphrases_lexical_skips(invoices):
    args = (invoices / "MEMORY.md", invoices / "evidence")
    assert audit(*args, as_of=date(2026, 10, 10)).findings[0].status == "insufficient_evidence"
    report = audit(*args, verifier=mock_jev(), as_of=date(2026, 10, 10), min_coverage=0.2, max_judged=8)
    finding = report.findings[0]
    assert finding.status == "candidate_counterevidence" and finding.evidence[0].span.quote == COUNTER
    assert report.verifier == "jev:jev-latest" and report.limitations[0] == MODEL_NOTE


def test_max_judged_bounds_model_calls(invoices):
    (invoices / "evidence" / "more.md").write_text(
        "\n".join(f"Customers asked about invoice batch {n}." for n in range(5)) + "\n", encoding="utf-8")
    requests = []
    audit(invoices / "MEMORY.md", invoices / "evidence", verifier=mock_jev(requests=requests),
          min_coverage=0.2, max_judged=2)
    assert len(requests) == 2


def test_dual_llm_jev_disagreement_is_disputed_with_both_opinions(invoices):
    model = LLMVerifier(client=fake_client(verdict=Verdict(relation="unrelated", rationale="CSV is not Excel")))
    dual = DualVerifier(model, mock_jev())
    report = audit(invoices / "MEMORY.md", invoices / "evidence", verifier=dual, min_coverage=0.2, max_judged=8)
    evidence = report.findings[0].evidence[0]
    assert report.findings[0].status == "disputed" and evidence.relation == "disputed"
    assert [o.verifier for o in evidence.opinions] == ["llm:claude-opus-5-5", "jev:jev-latest"]
    markdown = to_markdown(report)
    assert "llm:claude-opus-5-5: unrelated · CSV is not Excel" in markdown
    assert "jev:jev-latest: contradicts · supports 0.03, contradicts 0.90" in markdown


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


@pytest.mark.skipif(importlib.util.find_spec("typesafe_sdk") is not None, reason="typesafe-sdk is installed")
def test_cli_jev_without_dependencies_exits_2(invoices):
    result = runner.invoke(app, ["audit", str(invoices / "MEMORY.md"), "--verifier", "jev"])
    assert result.exit_code == 2 and "memfalsify[jev]" in result.output


def test_cli_jev_without_api_key_exits_2(invoices, monkeypatch):
    pytest.importorskip("typesafe_sdk")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = runner.invoke(app, ["audit", str(invoices / "MEMORY.md"), "--verifier", "jev"])
    assert result.exit_code == 2 and "TYPESAFE_API_KEY" in result.output
