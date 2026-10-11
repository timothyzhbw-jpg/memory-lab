import importlib.util
import json
import re
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
from memfalsify.gitstore import printable
from memfalsify.llm import SYSTEM, LLMVerifier, Verdict
from memfalsify.report import to_markdown, usage_lines
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


# Newline, a Markdown breakout, a terminal color, a bidi override and a workflow-command marker.
HOSTILE = "seen\n## pwned\x1b[31m\u202e::set-output"


def _one_inert_line(text: str) -> None:
    assert text == printable(text)
    assert "\n" not in text and "\r" not in text and "\x1b" not in text and "\u202e" not in text


def test_model_text_stays_one_inert_line(two_claims):
    client = scripted_client(decide=lambda claim, passage: (Verdict(relation="contradicts", rationale=HOSTILE), "end_turn"))
    report = model_audit(two_claims, LLMVerifier(client=client))
    rationales = [evidence.rationale for finding in report.findings for evidence in finding.evidence]
    assert rationales and all(rationale == printable(HOSTILE) for rationale in rationales)
    markdown = to_markdown(report)
    assert "\x1b" not in markdown and "\u202e" not in markdown
    assert not any(line.startswith("## pwned") or line.startswith("::set-output") for line in markdown.splitlines())
    refused = LLMVerifier(client=fake_client(stop_reason=HOSTILE)).judge(CLAIM, COUNTER)
    _one_inert_line(refused.rationale)
    assert refused.rationale == printable(f"no verdict (stop_reason: {HOSTILE})")
    with pytest.raises(VerifierError) as caught:
        LLMVerifier(client=fake_client(error=TypeError(HOSTILE))).judge(CLAIM, COUNTER)
    assert str(caught.value) == printable(f"llm:claude-opus-5-5: {HOSTILE}")


def test_usage_line_verifier_name_stays_inside_one_code_span():
    from memfalsify.verify import VerifierStats
    hostile = "m`\n- pwned\x1b[0m\u202e"
    report = SimpleNamespace(verifier_stats=[VerifierStats(verifier=hostile, requests=1, output_tokens=2)])
    [line] = usage_lines(report)
    shown = printable(hostile).replace("`", "'")
    assert line.startswith(f"- API usage of `{shown}`: 1 request(s)")
    assert line.count("`") == 2 and "\n" not in line and "\x1b" not in line and "\u202e" not in line


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


# --- Message Batches -----------------------------------------------------------------

class FakeBatches:
    """Message Batches stand-in. A batch ends after ``polls`` retrievals; results come back in reverse order.
    ``outcome(custom_id)`` picks the result type; ``decide`` answers succeeded requests (a str is sent as raw text)."""

    def __init__(self, decide=decide, polls=2, outcome=lambda custom_id: "succeeded", usage=None):
        self.decide, self.polls, self.outcome, self.usage = decide, polls, outcome, usage
        self.created, self.retrieved, self.canceled = [], [], []

    def create(self, requests):
        batch = SimpleNamespace(id=f"msgbatch_{len(self.created)}", processing_status="in_progress")
        self.created.append((batch.id, list(requests)))
        return batch

    def retrieve(self, batch_id):
        self.retrieved.append(batch_id)
        ended = self.retrieved.count(batch_id) >= self.polls
        return SimpleNamespace(id=batch_id, processing_status="ended" if ended else "in_progress")

    def results(self, batch_id):
        for request in reversed(dict(self.created)[batch_id]):
            kind = self.outcome(request["custom_id"])
            if kind != "succeeded":
                error = SimpleNamespace(error=SimpleNamespace(type="overloaded_error")) if kind == "errored" else None
                yield SimpleNamespace(custom_id=request["custom_id"], result=SimpleNamespace(type=kind, error=error))
                continue
            verdict, stop_reason = self.decide(*unwrap(request["params"]))
            text = verdict if isinstance(verdict, str) else verdict.model_dump_json() if verdict else None
            content = [SimpleNamespace(type="text", text=text)] if text is not None else []
            message = SimpleNamespace(stop_reason=stop_reason, content=content, usage=self.usage)
            yield SimpleNamespace(custom_id=request["custom_id"], result=SimpleNamespace(type="succeeded",
                                                                                        message=message))

    def cancel(self, batch_id):
        self.canceled.append(batch_id)


def batch_client(batches=None, messages=None):
    return SimpleNamespace(messages=SimpleNamespace(batches=batches or FakeBatches()),
                           beta=SimpleNamespace(messages=messages or ScriptedMessages()))


def batched(client, model=llm.DEFAULT_MODEL, delays=None, progress=None):
    pytest.importorskip("anthropic")
    return LLMVerifier(model, client=client, batch=True, sleep=(delays if delays is not None else []).append,
                       progress=progress)


def test_batch_verdicts_match_direct_requests(two_claims):
    direct = model_audit(two_claims, LLMVerifier(client=scripted_client()))
    client, delays, lines = batch_client(FakeBatches(usage=usage(write=0, read=30))), [], []
    report = model_audit(two_claims, batched(client, delays=delays, progress=lines.append))
    assert report.findings == direct.findings  # results came back in reverse order, mapped by custom_id
    assert [f.status for f in report.findings] == ["candidate_counterevidence", "candidate_support"]
    assert client.beta.messages.calls == [] and delays == [5.0, 10.0]
    [stats] = report.verifier_stats
    assert (stats.requests, stats.batched_requests, stats.cache_read_input_tokens) == (4, 4, 120)
    assert "4 request(s) (4 through the Message Batches API)" in to_markdown(report)
    assert lines[0].startswith("llm:claude-opus-5-5: submitted Message Batch msgbatch_0 with 4 request(s)")


def test_batch_request_shape(two_claims):
    anthropic = pytest.importorskip("anthropic")
    client = batch_client()
    model_audit(two_claims, batched(client))
    [(_, requests)] = client.messages.batches.created
    assert [r["custom_id"] for r in requests] == ["pair-0", "pair-1", "pair-2", "pair-3"]
    hour = {"type": "ephemeral", "ttl": "1h"}
    for request in requests:
        params = request["params"]
        assert set(params) == {"model", "max_tokens", "system", "messages", "output_config"}  # no fallbacks
        assert params["model"] == "claude-opus-5-5" and params["max_tokens"] == llm.MAX_TOKENS
        assert params["system"] == [{"type": "text", "text": SYSTEM, "cache_control": hour}]
        assert params["output_config"] == {"effort": "low", "format": {
            "type": "json_schema", "schema": anthropic.transform_schema(Verdict.model_json_schema())}}
        claim_block = params["messages"][0]["content"][0]
        assert claim_block.get("cache_control") == (hour if unwrap(params)[0] == CSV_CLAIM else None)


def test_batch_polling_backs_off_to_a_minute():
    delays = []
    verifier = batched(batch_client(FakeBatches(polls=8)), delays=delays)
    assert verifier.judge_many([(CLAIM, COUNTER)])[0].relation == "contradicts"
    assert delays == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 60.0, 60.0]


@pytest.mark.parametrize("outcome, detail", [("expired", "1 expired"), ("errored", "1 errored (overloaded_error)"),
                                             ("canceled", "1 canceled")])
def test_batch_failures_fail_closed(two_claims, outcome, detail):
    client = batch_client(FakeBatches(outcome=lambda custom_id: outcome if custom_id == "pair-2" else "succeeded"))
    with pytest.raises(VerifierError, match=rf"1 of 4 batch requests did not succeed \({re.escape(detail)}\) "
                                            r"in msgbatch_0; no partial report"):
        model_audit(two_claims, batched(client))


def test_batch_missing_or_unknown_results_fail_closed():
    class Lossy(FakeBatches):
        def results(self, batch_id):
            yield from list(super().results(batch_id))[1:]

    with pytest.raises(VerifierError, match=r"1 missing"):
        batched(batch_client(Lossy())).judge_many([(CLAIM, COUNTER), (CLAIM, REDIS_CLAIM)])

    class Stranger(FakeBatches):
        def results(self, batch_id):
            yield SimpleNamespace(custom_id="pair-99", result=SimpleNamespace(type="succeeded"))

    with pytest.raises(VerifierError, match="unexpected result 'pair-99'"):
        batched(batch_client(Stranger())).judge_many([(CLAIM, COUNTER)])


def test_batch_unreadable_verdict_fails_closed():
    client = batch_client(FakeBatches(decide=lambda claim, passage: ("not json", "max_tokens")))
    with pytest.raises(VerifierError, match=r"batch result pair-0 is not a verdict \(stop_reason: max_tokens\)"):
        batched(client).judge_many([(CLAIM, COUNTER)])


def test_batch_refusal_is_asked_again_with_the_server_side_fallback():
    refuse = lambda claim, passage: (None, "refusal") if "not available" in passage else decide(claim, passage)
    pairs = [(CLAIM, COUNTER), (REDIS_CLAIM, REDIS_CLAIM)]
    client = batch_client(FakeBatches(decide=refuse))
    verifier = batched(client)
    judgments = verifier.judge_many(pairs)
    assert [j.relation for j in judgments] == ["contradicts", "supports"]
    [retry] = client.beta.messages.calls  # only the refused pair, directly, with the fallback
    assert unwrap(retry) == pairs[0] and retry["fallbacks"] == "default"
    assert (verifier.usage()[0].requests, verifier.usage()[0].batched_requests) == (3, 2)
    # A model without the fallback keeps the refusal as no verdict, as a direct request would.
    client = batch_client(FakeBatches(decide=refuse))
    judgments = batched(client, model="claude-haiku-5-5").judge_many(pairs)
    assert judgments[0].relation == "unrelated" and "refusal" in judgments[0].rationale
    assert client.beta.messages.calls == []


def test_batch_provider_text_is_one_inert_line():
    pytest.importorskip("anthropic")
    lines = []

    class NastyId(FakeBatches):
        def create(self, requests):
            batch = SimpleNamespace(id=HOSTILE, processing_status="in_progress")
            self.created.append((batch.id, list(requests)))
            return batch

    LLMVerifier(client=batch_client(NastyId(polls=1)), batch=True, sleep=lambda _seconds: None,
                progress=lines.append).judge_many([(CLAIM, COUNTER)])
    assert lines and all(line == printable(line) for line in lines)
    assert printable(HOSTILE) in lines[0]

    class NastyError(FakeBatches):
        def results(self, batch_id):
            error = SimpleNamespace(error=SimpleNamespace(type=HOSTILE))
            yield SimpleNamespace(custom_id="pair-0", result=SimpleNamespace(type="errored", error=error))

    with pytest.raises(VerifierError) as caught:
        LLMVerifier(client=batch_client(NastyError()), batch=True, sleep=lambda _seconds: None).judge_many([(CLAIM, COUNTER)])
    assert str(caught.value) == printable(str(caught.value)) and printable(HOSTILE) in str(caught.value)

    class NastyCancel(FakeBatches):
        def cancel(self, batch_id):
            raise RuntimeError(HOSTILE)

    lines.clear()

    def interrupt(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        LLMVerifier(client=batch_client(NastyCancel(polls=5)), batch=True, sleep=interrupt,
                    progress=lines.append).judge_many([(CLAIM, COUNTER)])
    assert any(printable(HOSTILE) in line for line in lines)
    assert all(line == printable(line) for line in lines)

    client = batch_client(FakeBatches(decide=lambda claim, passage: ("not json", HOSTILE)))
    with pytest.raises(VerifierError) as caught:
        LLMVerifier(client=client, batch=True, sleep=lambda _seconds: None).judge_many([(CLAIM, COUNTER)])
    message = str(caught.value)
    assert message == printable(message) and "stop_reason:" in message and printable(HOSTILE) in message


def test_batch_interrupt_cancels_the_batch():
    pytest.importorskip("anthropic")
    batches = FakeBatches(polls=5)

    def interrupt(seconds):
        raise KeyboardInterrupt

    verifier = LLMVerifier(client=batch_client(batches), batch=True, sleep=interrupt)
    with pytest.raises(KeyboardInterrupt):
        verifier.judge_many([(CLAIM, COUNTER)])
    assert batches.canceled == ["msgbatch_0"]


def test_large_batches_are_split(monkeypatch):
    monkeypatch.setattr(llm, "MAX_BATCH_REQUESTS", 3)
    batches = FakeBatches()
    pairs = [(CLAIM, f"Invoices are not available in region {n}.") for n in range(4)] + [(REDIS_CLAIM, REDIS_CLAIM)]
    judgments = batched(batch_client(batches)).judge_many(pairs)
    assert [len(requests) for _, requests in batches.created] == [3, 2]
    assert [j.relation for j in judgments] == ["contradicts"] * 4 + ["supports"]
    assert batched(batch_client(batches)).judge_many([]) == [] and len(batches.created) == 2


def test_cascade_batches_only_the_escalated_pairs():
    unsure = lambda state: (("contradicts", 0.9, {"supports": 0.0, "contradicts": 0.9, "unrelated": 0.1})
                            if "not available" in state["passage"]
                            else ("unrelated", 0.5, {"supports": 0.2, "contradicts": 0.3, "unrelated": 0.5}))
    batches = FakeBatches()
    cascade = CascadeVerifier(mock_jev(unsure), batched(batch_client(batches)))
    judgments = cascade.judge_many([(CLAIM, COUNTER), (REDIS_CLAIM, REDIS_CLAIM)], max_concurrency=2)
    assert [j.relation for j in judgments] == ["contradicts", "supports"]
    [(_, requests)] = batches.created
    assert [unwrap(r["params"]) for r in requests] == [(REDIS_CLAIM, REDIS_CLAIM)]


def test_cli_batch(two_claims, monkeypatch):
    pytest.importorskip("anthropic")
    client = batch_client()
    real = llm.LLMVerifier
    monkeypatch.setattr(llm, "LLMVerifier", lambda model, **kw: real(model, client=client, sleep=lambda s: None, **kw))
    args = ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"), "--batch"]
    result = runner.invoke(app, args + ["--verifier", "llm"])
    assert result.exit_code == 0 and "POSSIBLE COUNTEREVIDENCE" in result.stdout
    assert "(4 through the Message Batches API)" in result.stdout
    assert "submitted Message Batch msgbatch_0" in result.stderr
    for verifier in ("lexical", "jev"):
        result = runner.invoke(app, args + ["--verifier", verifier])
        assert result.exit_code == 2 and "use it with --verifier llm, dual or cascade" in result.output


def test_cli_batch_failure_exits_2_without_a_report(two_claims, monkeypatch, tmp_path):
    pytest.importorskip("anthropic")
    client = batch_client(FakeBatches(outcome=lambda custom_id: "expired"))
    real = llm.LLMVerifier
    monkeypatch.setattr(llm, "LLMVerifier", lambda model, **kw: real(model, client=client, sleep=lambda s: None, **kw))
    out = tmp_path / "report.md"
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"),
                                 "--verifier", "llm", "--batch", "-o", str(out)])
    assert result.exit_code == 2 and "4 of 4 batch requests did not succeed (4 expired)" in result.output
    assert not out.exists()
