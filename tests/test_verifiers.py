import importlib.util
import json
import threading
import time
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
from memfalsify.verify import (RELATION_CRITERIA, CascadeVerifier, DualVerifier, Judgment, LexicalVerifier,
                               VerifierError, concurrently, judge_all)

runner = CliRunner()
CLAIM = "Customers can download every past invoice as an Excel spreadsheet."
COUNTER = "Invoice export to CSV is not available to customers."


class FakeMessages:
    def __init__(self, verdict=None, stop_reason="end_turn", error=None, usage=None):
        self.verdict, self.stop_reason, self.error, self.usage, self.calls = verdict, stop_reason, error, usage, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.verdict, usage=self.usage)


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
    assert call["model"] == "claude-opus-5-5" and call["output_format"] is Verdict
    assert call["output_config"] == {"effort": "low"} and call["max_tokens"] == llm.MAX_TOKENS
    assert call["fallbacks"] == "default" and call["betas"] == [llm.FALLBACK_BETA]
    # The instructions are the cached prefix; claim and passage follow it, in that order.
    assert call["system"] == [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
    assert call["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": f"<claim>\n{CLAIM}\n</claim>"},
        {"type": "text", "text": f"<passage>\n{COUNTER}\n</passage>"}]}]


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


def test_llm_missing_credentials_becomes_verifier_error():
    # anthropic raises a plain TypeError at the first request when no credentials resolve.
    missing = TypeError('"Could not resolve authentication method. Expected one of api_key, auth_token, ..."')
    with pytest.raises(VerifierError, match="Could not resolve authentication method"):
        LLMVerifier(client=fake_client(error=missing)).judge(CLAIM, COUNTER)


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


# --- cascade -----------------------------------------------------------------------

def test_cascade_accepts_confident_jev_without_calling_llm():
    client = fake_client(verdict=Verdict(relation="supports", rationale="-"))
    judgment = CascadeVerifier(mock_jev(), LLMVerifier(client=client)).judge(CLAIM, COUNTER)
    assert judgment.relation == "contradicts" and judgment.confidence == 0.90
    assert judgment.verifier == "jev:jev-latest>llm:claude-opus-5-5" and len(judgment.opinions) == 1
    assert client.beta.messages.calls == []


def test_cascade_escalates_unsure_jev_to_llm():
    unsure = lambda state: ("supports", 0.60, {"supports": 0.60, "contradicts": 0.10, "unrelated": 0.30})
    client = fake_client(verdict=Verdict(relation="unrelated", rationale="CSV is not Excel"))
    judgment = CascadeVerifier(mock_jev(unsure), LLMVerifier(client=client), accept_at=0.8).judge(CLAIM, COUNTER)
    assert judgment.relation == "unrelated" and len(client.beta.messages.calls) == 1
    assert [o.relation for o in judgment.opinions] == ["supports", "unrelated"]
    assert "(confidence 0.60 < 0.8), escalated; llm:claude-opus-5-5: CSV is not Excel" in judgment.rationale


def test_cascade_escalates_judgments_without_confidence():
    client = fake_client(verdict=Verdict(relation="contradicts", rationale="not available"))
    judgment = CascadeVerifier(LexicalVerifier(), LLMVerifier(client=client)).judge(CLAIM, COUNTER)
    assert judgment.relation == "contradicts" and "no confidence" in judgment.rationale


def test_cli_cascade(invoices, monkeypatch):
    unsure = lambda state: ("unrelated", 0.55, {"supports": 0.05, "contradicts": 0.40, "unrelated": 0.55})
    client = fake_client(verdict=Verdict(relation="contradicts", rationale="not available"))
    real = llm.LLMVerifier
    monkeypatch.setattr(llm, "LLMVerifier", lambda model: real(model, client=client))
    monkeypatch.setattr(jev, "JevVerifier", lambda model: mock_jev(unsure))
    result = runner.invoke(app, ["audit", str(invoices / "MEMORY.md"), "-e", str(invoices / "evidence"),
                                 "--verifier", "cascade", "--accept-at", "0.6"])
    assert result.exit_code == 0 and "POSSIBLE COUNTEREVIDENCE" in result.output
    assert "Verifier: `jev:jev-latest>llm:claude-opus-5-5`" in result.output
    assert "(confidence 0.55 < 0.6), escalated" in result.output


# --- API usage, prompt caching and concurrency ---------------------------------------

CSV_CLAIM = "Invoice export to CSV is available to customers."
REDIS_CLAIM = "The cache uses Redis."


def usage(input_tokens=100, output_tokens=20, write=0, read=0):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                           cache_creation_input_tokens=write, cache_read_input_tokens=read)


def unwrap(params):
    """The claim and passage out of a request built by memfalsify.llm.prompt()."""
    claim, passage = (block["text"] for block in params["messages"][0]["content"])
    return claim.removeprefix("<claim>\n").removesuffix("\n</claim>"), \
        passage.removeprefix("<passage>\n").removesuffix("\n</passage>")


def decide(claim, passage):
    """Stand-in Claude: a negated passage contradicts, an identical one supports, anything else is unrelated."""
    if "not available" in passage:
        return Verdict(relation="contradicts", rationale="not available"), "end_turn"
    if passage == claim:
        return Verdict(relation="supports", rationale="same words"), "end_turn"
    return Verdict(relation="unrelated", rationale="-"), "end_turn"


class ScriptedMessages:
    """``parse()`` answers with ``decide(claim, passage)``; records every call and how many ran at once."""

    def __init__(self, decide=decide, delay=lambda claim, passage: 0.0, usage=None):
        self.decide, self.delay, self.usage = decide, delay, usage
        self.calls, self.events = [], []
        self.lock = threading.Lock()
        self.active = self.peak = 0

    def parse(self, **kwargs):
        claim, passage = unwrap(kwargs)
        with self.lock:
            self.calls.append(kwargs)
            self.events.append(("start", claim, passage))
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.delay(claim, passage))
        with self.lock:
            self.active -= 1
            self.events.append(("end", claim, passage))
        verdict, stop_reason = self.decide(claim, passage)
        return SimpleNamespace(stop_reason=stop_reason, parsed_output=verdict, usage=self.usage)


def scripted_client(**kwargs):
    return SimpleNamespace(beta=SimpleNamespace(messages=ScriptedMessages(**kwargs)))


@pytest.fixture
def two_claims(tmp_path):
    """The CSV claim shares terms with three evidence sentences, the Redis claim with one."""
    (tmp_path / "MEMORY.md").write_text(f"- {CSV_CLAIM}\n- {REDIS_CLAIM}\n", encoding="utf-8")
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "billing.md").write_text(
        "Invoice export to CSV is not available to customers.\n\nCustomers ask about invoice export to CSV.\n\n"
        "Invoice export to CSV is available to admins.\n", encoding="utf-8")
    (tmp_path / "evidence" / "infra.md").write_text(f"{REDIS_CLAIM}\n", encoding="utf-8")
    return tmp_path


def model_audit(root, verifier, **kwargs):
    return audit(root / "MEMORY.md", root / "evidence", verifier=verifier, as_of=date(2026, 10, 10),
                 min_coverage=0.2, max_judged=8, **kwargs)


def test_report_counts_api_requests_and_cache_tokens(two_claims):
    client = scripted_client(usage=usage(input_tokens=90, output_tokens=25, write=600, read=40))
    report = model_audit(two_claims, LLMVerifier(client=client))
    assert len(client.beta.messages.calls) == 4
    [stats] = report.verifier_stats
    assert stats.model_dump() == {"verifier": "llm:claude-opus-5-5", "requests": 4, "batched_requests": 0,
                                  "input_tokens": 360, "output_tokens": 100,
                                  "cache_creation_input_tokens": 2400, "cache_read_input_tokens": 160}
    assert ("- API usage of `llm:claude-opus-5-5`: 4 request(s) · input tokens: 360 uncached, "
            "2400 written to the prompt cache, 160 read from it · output tokens: 100") in to_markdown(report)
    lexical = audit(two_claims / "MEMORY.md", two_claims / "evidence")
    assert lexical.verifier_stats == [] and "API usage" not in to_markdown(lexical)


def test_usage_is_counted_per_audit(two_claims):
    verifier = LLMVerifier(client=scripted_client(usage=usage()))
    first, second = model_audit(two_claims, verifier), model_audit(two_claims, verifier)
    assert first.verifier_stats == second.verifier_stats and second.verifier_stats[0].requests == 4


def test_cascade_reports_both_backends(two_claims):
    unsure = lambda state: ("unrelated", 0.55, {"supports": 0.05, "contradicts": 0.40, "unrelated": 0.55})
    cascade = CascadeVerifier(mock_jev(unsure), LLMVerifier(client=scripted_client(usage=usage())))
    report = model_audit(two_claims, cascade)
    jev_stats, llm_stats = report.verifier_stats
    assert (jev_stats.verifier, jev_stats.requests, jev_stats.input_tokens, jev_stats.output_tokens) == \
        ("jev:jev-latest", 4, 240, 4)
    assert jev_stats.cache_read_input_tokens is None and llm_stats.requests == 4
    markdown = to_markdown(report)
    assert "- API usage of `jev:jev-latest`: 4 request(s) · input tokens: 240 · output tokens: 4" in markdown


def test_claim_prefix_is_cached_only_when_judged_more_than_once(two_claims):
    client = scripted_client()
    model_audit(two_claims, LLMVerifier(client=client))
    calls = client.beta.messages.calls
    assert all(c["system"][0]["cache_control"] == {"type": "ephemeral"} for c in calls)
    marked = {unwrap(c)[0]: "cache_control" in c["messages"][0]["content"][0] for c in calls}
    assert marked == {CSV_CLAIM: True, REDIS_CLAIM: False}
    assert all("cache_control" not in c["messages"][0]["content"][1] for c in calls)  # the passage is never cached


def test_concurrent_audit_matches_serial_audit(two_claims):
    serial = model_audit(two_claims, LLMVerifier(client=scripted_client()))
    # Earlier passages answer slower, so requests finish out of order.
    slow = scripted_client(delay=lambda claim, passage: 0.08 if "not available" in passage else 0.02)
    parallel = model_audit(two_claims, LLMVerifier(client=slow), max_concurrency=3)
    assert parallel.model_dump() == serial.model_dump()
    assert 1 < slow.beta.messages.peak <= 3


def test_requests_sharing_a_cached_claim_wait_for_the_first(two_claims):
    client = scripted_client(delay=lambda claim, passage: 0.03)
    model_audit(two_claims, LLMVerifier(client=client), max_concurrency=4)
    csv = [(kind, passage) for kind, claim, passage in client.beta.messages.events if claim == CSV_CLAIM]
    assert csv[0][0] == "start" and csv[1] == ("end", csv[0][1])  # nothing else for this claim ran meanwhile
    assert len(csv) == 6


def test_concurrently_keeps_index_order_and_bound():
    lock, state = threading.Lock(), {"active": 0, "peak": 0}

    def square(i):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.01 * (8 - i))
        with lock:
            state["active"] -= 1
        return i * i

    assert concurrently(square, 8, max_concurrency=3) == [i * i for i in range(8)]
    assert state["peak"] == 3


def test_concurrently_starts_nothing_after_a_failure():
    started = []

    def call(i):
        started.append(i)
        if i == 1:
            raise VerifierError("boom")
        time.sleep(0.05)
        return i

    with pytest.raises(VerifierError, match="boom"):
        concurrently(call, 10, max_concurrency=2)
    assert sorted(started) == [0, 1]


def test_concurrently_runs_followers_after_their_lead():
    order = []
    concurrently(lambda i: order.append(i) or time.sleep(0.01), 5, max_concurrency=4, after={0: [3, 4]})
    assert order.index(0) < order.index(3) and order.index(0) < order.index(4)
    order.clear()
    concurrently(order.append, 5, max_concurrency=1, after={0: [3, 4]})
    assert order == [0, 1, 2, 3, 4]


def test_composite_judge_many_matches_judge():
    pairs = [(CLAIM, COUNTER), (CLAIM, "Customers download invoices as PDF."), (REDIS_CLAIM, REDIS_CLAIM)]
    unsure = lambda state: (("contradicts", 0.9, {"supports": 0.0, "contradicts": 0.9, "unrelated": 0.1})
                            if "not available" in state["passage"]
                            else ("unrelated", 0.5, {"supports": 0.2, "contradicts": 0.3, "unrelated": 0.5}))
    for make in (lambda c: DualVerifier(LLMVerifier(client=c), mock_jev(unsure)),
                 lambda c: CascadeVerifier(mock_jev(unsure), LLMVerifier(client=c))):
        one_by_one = [make(scripted_client()).judge(*pair) for pair in pairs]
        client = scripted_client()
        assert judge_all(make(client), pairs, max_concurrency=2) == one_by_one
    assert len(client.beta.messages.calls) == 2  # cascade asked Claude only about the unsure pairs


def test_judge_all_calls_plain_verifiers_once_per_pair():
    seen = []

    class Plain:
        name = "plain"

        def judge(self, claim, passage):
            seen.append(passage)
            return Judgment(relation="unrelated", rationale="-", verifier=self.name)

    judgments = judge_all(Plain(), [("c", str(i)) for i in range(5)], max_concurrency=2)
    assert len(judgments) == 5 and sorted(seen) == ["0", "1", "2", "3", "4"]


def test_cli_max_concurrency_applies_to_model_verifiers_only(two_claims, monkeypatch):
    from memfalsify import cli
    seen, real_audit = [], cli.audit
    monkeypatch.setattr(cli, "audit", lambda *a, **k: seen.append(k["max_concurrency"]) or real_audit(*a, **k))
    client = scripted_client()
    real = llm.LLMVerifier
    monkeypatch.setattr(llm, "LLMVerifier", lambda model: real(model, client=client))
    args = ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence")]
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, args + ["--max-concurrency", "6"]).exit_code == 0
    assert runner.invoke(app, args + ["--verifier", "llm"]).exit_code == 0
    assert runner.invoke(app, args + ["--verifier", "llm", "--max-concurrency", "2"]).exit_code == 0
    assert seen == [1, 1, 4, 2]
    assert runner.invoke(app, args + ["--max-concurrency", "0"]).exit_code == 2
