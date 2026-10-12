"""The OpenAI-compatible verifier, offline: a fake Chat Completions client, and the real openai SDK on an
``httpx2.MockTransport`` where the request itself (URL, headers, body) or the SDK's errors matter."""
import importlib.util
import json
import shutil
import threading
import time
from datetime import date
from types import SimpleNamespace
from typing import get_args

import pytest
from typer.testing import CliRunner

from memfalsify import openai_compat
from memfalsify.audit import audit
from memfalsify.cli import app
from memfalsify.gitstore import printable
from memfalsify.llm import SYSTEM, Verdict
from memfalsify.openai_compat import (FORMATS, INSTRUCTIONS, PRESETS, SCHEMA, OpenAICompatVerifier, Provider,
                                      check_base_url, parse_verdict, redact, resolve_endpoint)
from memfalsify.report import to_markdown
from memfalsify.verify import RELATION_CRITERIA, VerifierError, judge_all

runner = CliRunner()
CLAIM = "Customers can download every past invoice as an Excel spreadsheet."
COUNTER = "Invoice export to CSV is not available to customers."
SECRET = "sk-test-0123456789abcdefSECRETxyz"


def answer(content=None, finish_reason="stop", refusal=None, usage=None, choices=True):
    message = SimpleNamespace(role="assistant", content=content, refusal=refusal)
    listed = [SimpleNamespace(index=0, finish_reason=finish_reason, message=message)] if choices else []
    return SimpleNamespace(choices=listed, usage=usage)


def verdict(relation, rationale="-"):
    return json.dumps({"relation": relation, "rationale": rationale})


def status_error(status, message="refused"):
    """The SDK's own exception for an HTTP status (BadRequestError for 400, and so on)."""
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")
    request = httpx2.Request("POST", "https://api.example.test/v1/chat/completions")
    response = httpx2.Response(status, request=request)
    kinds = {400: openai.BadRequestError, 401: openai.AuthenticationError, 422: openai.UnprocessableEntityError,
             429: openai.RateLimitError}
    return kinds.get(status, openai.APIStatusError)(message, response=response, body=None)


def decide(claim, passage):
    """Stand-in model: a negated passage contradicts, an identical one supports, anything else is unrelated."""
    if "not available" in passage:
        return verdict("contradicts", "not available")
    if passage == claim:
        return verdict("supports", "same words")
    return verdict("unrelated")


def unwrap(messages):
    """The claim and passage out of the user message built by openai_compat.prompt()."""
    text = messages[1]["content"]
    claim, passage = text.split("\n</claim>\n\n<passage>\n")
    return claim.removeprefix("<claim>\n"), passage.removesuffix("\n</passage>")


class FakeCompletions:
    """``create()`` answers ``decide(claim, passage)``. Formats in ``refuse`` get ``refuse_status``; ``error`` is
    raised for every request. Records every call and how many ran at once."""

    def __init__(self, decide=decide, refuse=(), refuse_status=400, error=None, usage=None,
                 delay=lambda claim, passage: 0.0, raw=None):
        self.decide, self.refuse, self.refuse_status, self.error = decide, refuse, refuse_status, error
        self.usage, self.delay, self.raw = usage, delay, raw
        self.calls, self.lock, self.active, self.peak = [], threading.Lock(), 0, 0

    def create(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            claim, passage = unwrap(kwargs["messages"])
            time.sleep(self.delay(claim, passage))
            if self.error is not None:
                raise self.error
            mode = kwargs.get("response_format", {}).get("type", "text")
            if mode in self.refuse:
                raise status_error(self.refuse_status, f"response_format {mode} is not supported")
            if self.raw is not None:
                return self.raw
            return answer(self.decide(claim, passage), usage=self.usage)
        finally:
            with self.lock:
                self.active -= 1


def fake_client(**kwargs):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(**kwargs)))


def verifier(client=None, model="test-model", progress=None, **endpoint):
    return OpenAICompatVerifier(model, client=client or fake_client(), progress=progress, **endpoint)


@pytest.fixture
def invoices(tmp_path):
    (tmp_path / "MEMORY.md").write_text(f"- {CLAIM}\n", encoding="utf-8")
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "billing.md").write_text(f"# Billing\n\n{COUNTER}\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def two_claims(tmp_path):
    """The CSV claim shares terms with three evidence sentences, the Redis claim with one."""
    (tmp_path / "MEMORY.md").write_text("- Invoice export to CSV is available to customers.\n"
                                        "- The cache uses Redis.\n", encoding="utf-8")
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "billing.md").write_text(
        "Invoice export to CSV is not available to customers.\n\nCustomers ask about invoice export to CSV.\n\n"
        "Invoice export to CSV is available to admins.\n", encoding="utf-8")
    (tmp_path / "evidence" / "infra.md").write_text("The cache uses Redis.\n", encoding="utf-8")
    return tmp_path


def model_audit(root, judge, **kwargs):
    return audit(root / "MEMORY.md", root / "evidence", verifier=judge, as_of=date(2026, 10, 10),
                 min_coverage=0.2, max_judged=8, **kwargs)


# --- providers, overrides and the transport rule ----------------------------------------------

def test_presets_fill_base_url_and_key_variable():
    assert {p.value: (resolve_endpoint(p.value).base_url, resolve_endpoint(p.value).api_key_env) for p in Provider} == {
        "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
        "grok": ("https://api.x.ai/v1", "XAI_API_KEY"),
        "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
        # Google's documented OpenAI-compatible endpoint: https://ai.google.dev/gemini-api/docs/openai
        "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
        "ollama": ("http://localhost:11434/v1", None),
    }
    assert set(PRESETS) == set(Provider)
    assert resolve_endpoint().provider is Provider.openai


def test_explicit_base_url_and_key_variable_override_the_preset():
    url = resolve_endpoint("deepseek", base_url="https://gateway.example.test/v1")
    assert (url.base_url, url.api_key_env, url.preset_key) == ("https://gateway.example.test/v1", "DEEPSEEK_API_KEY",
                                                               True)
    key = resolve_endpoint("gemini", api_key_env="TEAM_GEMINI_KEY")
    assert (key.base_url, key.api_key_env, key.preset_key) == (PRESETS[Provider.gemini].base_url, "TEAM_GEMINI_KEY",
                                                               False)
    both = resolve_endpoint("ollama", "http://127.0.0.1:8080/v1", "LOCAL_KEY")
    assert (both.base_url, both.api_key_env, both.host) == ("http://127.0.0.1:8080/v1", "LOCAL_KEY", "127.0.0.1:8080")
    with pytest.raises(ValueError, match="unknown provider; choose one of: openai, grok, deepseek, gemini, ollama"):
        resolve_endpoint("azure")


@pytest.mark.parametrize("url", ["https://api.example.test/v1", "https://API.example.test:8443/openai/",
                                 "http://localhost:11434/v1", "http://LOCALHOST/v1", "http://127.0.0.1:8000/v1",
                                 "http://[::1]:11434/v1"])
def test_https_or_loopback_http_is_accepted(url):
    assert check_base_url(url) == url


@pytest.mark.parametrize("url, reason", [
    ("http://api.example.test/v1", "must use https"),
    ("http://localhost.evil.test/v1", "must use https"),
    ("http://127.0.0.2/v1", "must use https"),
    ("http://0.0.0.0:11434/v1", "must use https"),
    ("ftp://api.example.test/v1", "must be an https:// URL"),
    ("api.example.test/v1", "must be an https:// URL"),
    ("https:///v1", "must be an https:// URL"),
    ("https://user:hunter2-password@api.example.test/v1", "user name, password, query or fragment"),
    ("https://api.example.test/v1?key=AIzaSECRETvalue", "user name, password, query or fragment"),
    ("https://api.example.test/v1#AIzaSECRETvalue", "user name, password, query or fragment"),
    ("https://api.example.test:99999/v1", "not a valid URL"),
    ("https://api.example.test/v1\n", "whitespace or control"),
    ("", "empty"),
])
def test_other_base_urls_are_refused_without_repeating_them(url, reason):
    with pytest.raises(ValueError, match=reason) as caught:
        check_base_url(url)
    assert "SECRET" not in str(caught.value) and "hunter2" not in str(caught.value)
    assert "example" not in str(caught.value)


@pytest.mark.parametrize("name", [SECRET, "sk proj", "", "1PASSWORD", "KEY=value"])
def test_key_variable_must_be_a_name_and_is_never_echoed(name):
    with pytest.raises(ValueError, match="never the key itself") as caught:
        resolve_endpoint("openai", api_key_env=name)
    assert not name or name not in str(caught.value)


def test_model_is_required_and_has_no_default():
    with pytest.raises(VerifierError, match="needs --openai-model"):
        OpenAICompatVerifier("", client=fake_client())
    with pytest.raises(VerifierError, match="whitespace"):
        OpenAICompatVerifier("gpt x", client=fake_client())


def test_missing_key_is_a_verifier_error_naming_only_the_preset_variable(monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(VerifierError, match=r"no API key: XAI_API_KEY \(the grok default\) is not set"):
        OpenAICompatVerifier("grok-model", "grok")
    monkeypatch.delenv("MY_TEAM_KEY", raising=False)
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("grok-model", "grok", api_key_env="MY_TEAM_KEY")
    assert "the environment variable you named is not set" in str(caught.value)
    monkeypatch.setenv("MY_TEAM_KEY", "two words")
    with pytest.raises(VerifierError, match="whitespace") as caught:
        OpenAICompatVerifier("grok-model", "grok", api_key_env="MY_TEAM_KEY")
    assert "two words" not in str(caught.value)


@pytest.mark.skipif(importlib.util.find_spec("openai") is not None, reason="openai is installed")
def test_missing_sdk_is_a_verifier_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    with pytest.raises(VerifierError, match=r"memfalsify\[openai\]"):
        OpenAICompatVerifier("gpt-model")


# --- the request and structured output ---------------------------------------------------------

def test_request_asks_for_the_verdict_schema():
    client = fake_client()
    judgment = verifier(client).judge(CLAIM, COUNTER)
    assert (judgment.relation, judgment.rationale, judgment.verifier) == \
        ("contradicts", "not available", "openai:test-model@api.openai.com")
    [call] = client.chat.completions.calls
    assert set(call) == {"model", "messages", "response_format"}  # no token limit or temperature to trip a provider
    assert call["model"] == "test-model"
    assert call["messages"] == [
        {"role": "system", "content": INSTRUCTIONS},
        {"role": "user", "content": f"<claim>\n{CLAIM}\n</claim>\n\n<passage>\n{COUNTER}\n</passage>"}]
    # The Claude verifier's instructions, plus the answer's shape; json_object mode needs the word JSON.
    assert INSTRUCTIONS.startswith(SYSTEM) and "JSON" in INSTRUCTIONS
    assert call["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "verdict", "strict": True, "schema": SCHEMA}}


def test_schema_is_the_verdict_schema_in_strict_form():
    model = Verdict.model_json_schema()
    assert SCHEMA["required"] == model["required"] == list(SCHEMA["properties"])
    assert SCHEMA["additionalProperties"] is False
    assert SCHEMA["properties"]["relation"]["enum"] == list(get_args(Verdict.model_fields["relation"].annotation)) \
        == list(RELATION_CRITERIA)
    assert SCHEMA["properties"]["rationale"]["description"] == model["properties"]["rationale"]["description"]


@pytest.mark.parametrize("status", [400, 422])
def test_refused_formats_fall_back_to_json_object_then_plain_text(status):
    lines = []
    client = fake_client(refuse=("json_schema", "json_object"), refuse_status=status)
    judge = verifier(client, progress=lines.append)
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    assert [c.get("response_format", {}).get("type") for c in client.chat.completions.calls] == \
        ["json_schema", "json_object", None]
    assert "response_format" not in client.chat.completions.calls[-1]
    assert judge.response_format == "text"
    assert lines == [
        f"openai:test-model@api.openai.com: HTTP {status} for a request with response_format json_schema; asking "
        "with response_format json_object from now on",
        f"openai:test-model@api.openai.com: HTTP {status} for a request with response_format json_object; asking "
        "with no response_format (plain-text JSON) from now on"]
    # The accepted format is kept: the next pair costs one request.
    assert judge.judge(CLAIM, CLAIM).relation == "supports" and len(client.chat.completions.calls) == 4
    [usage] = judge.usage()
    assert usage.requests == 2  # refused requests return no answer and no usage


def test_json_object_is_kept_when_only_the_schema_is_refused():
    client = fake_client(refuse=("json_schema",))
    judge = verifier(client)
    judgments = judge_all(judge, [(CLAIM, COUNTER), (CLAIM, CLAIM)], max_concurrency=1)
    assert [j.relation for j in judgments] == ["contradicts", "supports"]
    assert [c["response_format"]["type"] for c in client.chat.completions.calls] == \
        ["json_schema", "json_object", "json_object"]


def test_fallback_happens_once_across_threads():
    lines = []
    client = fake_client(refuse=("json_schema",), delay=lambda claim, passage: 0.02)
    judge = verifier(client, progress=lines.append)
    pairs = [(CLAIM, f"{COUNTER} {n}") for n in range(6)]
    assert [j.relation for j in judge_all(judge, pairs, max_concurrency=3)] == ["contradicts"] * 6
    assert len(lines) == 1 and judge.response_format == "json_object"


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_other_api_errors_do_not_fall_back(status):
    client = fake_client(error=status_error(status, "denied"))
    with pytest.raises(VerifierError, match="openai:test-model@api.openai.com: denied"):
        verifier(client).judge(CLAIM, COUNTER)
    assert len(client.chat.completions.calls) == 1


def test_a_refused_plain_text_request_is_an_error():
    client = fake_client(refuse=FORMATS)
    with pytest.raises(VerifierError, match="response_format text is not supported"):
        verifier(client).judge(CLAIM, COUNTER)
    assert len(client.chat.completions.calls) == 3


GARBAGE = [
    "Sure! The passage contradicts the claim.",
    'Sure: {"relation": "contradicts", "rationale": "x"}',
    '{"relation": "supports"}',
    '{"rationale": "the passage agrees"}',
    '{"relation": "Supports", "rationale": "x"}',
    '{"relation": "agrees", "rationale": "x"}',
    '{"relation": "supports", "rationale": 1}',
    '{"relation": "supports", "rationale": "x", "confidence": 0.99}',
    '{"relation": "contradicts", "relation": "supports", "rationale": "x"}',
    '{"relation": "supports", "rationale": "the passage',
    '["supports", "x"]',
    '"supports"',
    "null",
    "[" * 5000 + "]" * 5000,
    "```json\n{\"relation\": \"supports\", \"rationale\": \"x\"}\n``` and more",
]


@pytest.mark.parametrize("content", GARBAGE)
def test_unusable_answers_are_no_verdict_never_support(content):
    judgment = verifier(fake_client(decide=lambda claim, passage: content)).judge(CLAIM, CLAIM)
    assert judgment.relation == "unrelated"
    assert judgment.rationale == "no verdict (finish_reason: stop; the answer is not a JSON verdict)"


@pytest.mark.parametrize("response, reason", [
    (answer(None, refusal="I can't help with that."), "finish_reason: stop; the model refused"),
    (answer(verdict("supports"), refusal="no"), "finish_reason: stop; the model refused"),
    (answer(None), "finish_reason: stop; the answer is empty"),
    (answer("   "), "finish_reason: stop; the answer is empty"),
    (answer('{"relation": "supports", "rationale": "cut', finish_reason="length"),
     "finish_reason: length; the answer is not a JSON verdict"),
    (answer(None, finish_reason="content_filter"), "finish_reason: content_filter; the answer is empty"),
    (answer(choices=False), "finish_reason: None; the response has no answer"),
    (SimpleNamespace(), "finish_reason: None; the response has no answer"),
])
def test_refusals_and_empty_responses_are_no_verdict(response, reason):
    judgment = verifier(fake_client(raw=response)).judge(CLAIM, CLAIM)
    assert (judgment.relation, judgment.rationale) == ("unrelated", f"no verdict ({reason})")


def test_a_fenced_json_answer_is_read():
    for text in ['```json\n{"relation": "supports", "rationale": "same"}\n```',
                 '\n```\n{"relation": "supports", "rationale": "same"}\n```\n',
                 '  {"relation": "supports", "rationale": "same"}  ']:
        assert parse_verdict(text).relation == "supports"


# Newline, a Markdown breakout, a terminal color, a bidi override and a workflow-command marker.
HOSTILE = "seen\n## pwned\x1b[31m\u202e::set-output"


def test_model_and_provider_text_stays_one_inert_line(two_claims):
    client = fake_client(decide=lambda claim, passage: verdict("contradicts", HOSTILE))
    report = model_audit(two_claims, verifier(client))
    rationales = [e.rationale for f in report.findings for e in f.evidence]
    assert rationales and all(r == printable(HOSTILE) for r in rationales)
    markdown = to_markdown(report)
    assert "\x1b" not in markdown and "\u202e" not in markdown
    assert not any(line.startswith(("## pwned", "::set-output")) for line in markdown.splitlines())
    finished = verifier(fake_client(raw=answer("nope", finish_reason=HOSTILE))).judge(CLAIM, COUNTER)
    assert finished.rationale == printable(f"no verdict (finish_reason: {HOSTILE}; the answer is not a JSON verdict)")
    with pytest.raises(VerifierError) as caught:
        verifier(fake_client(error=status_error(500, HOSTILE))).judge(CLAIM, COUNTER)
    assert str(caught.value) == printable(f"openai:test-model@api.openai.com: {HOSTILE}")


# --- the key stays secret -----------------------------------------------------------------------

def test_redact_hides_the_key_and_long_runs_of_it():
    text = f"key {SECRET}; masked sk-test-0123****SECRETxyz; tail ...abcdefSECRETxyz; short abc"
    shown = redact(text, SECRET)
    assert SECRET not in shown and "0123456789" not in shown and "SECRETxyz" not in shown
    assert shown.startswith("key [redacted]; masked [redacted]") and shown.endswith("short abc")
    assert redact(text, None) == text and redact("sk-x", "sk-x") == "[redacted]"


def test_api_error_text_never_carries_the_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    leak = f"Incorrect API key provided: {SECRET}. Partial: {SECRET[:12]}...{SECRET[-10:]}"
    judge = verifier(fake_client(error=status_error(401, leak)))
    with pytest.raises(VerifierError) as caught:
        judge.judge(CLAIM, COUNTER)
    message = str(caught.value)
    assert "Incorrect API key provided: [redacted]" in message
    assert all(SECRET[i:i + 8] not in message for i in range(len(SECRET) - 7))


# --- usage and concurrency -----------------------------------------------------------------------

def test_report_counts_requests_and_tokens_without_cache_counters(two_claims):
    client = fake_client(usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150))
    report = model_audit(two_claims, verifier(client))
    assert len(client.chat.completions.calls) == 4
    [stats] = report.verifier_stats
    assert stats.model_dump() == {"verifier": "openai:test-model@api.openai.com", "requests": 4,
                                  "batched_requests": 0, "input_tokens": 480, "output_tokens": 120,
                                  "cache_creation_input_tokens": None, "cache_read_input_tokens": None}
    assert ("- API usage of `openai:test-model@api.openai.com`: 4 request(s) · input tokens: 480 · "
            "output tokens: 120") in to_markdown(report)
    assert [f.status for f in report.findings] == ["candidate_counterevidence", "candidate_support"]


def test_concurrent_audit_matches_serial_audit(two_claims):
    serial = model_audit(two_claims, verifier(fake_client()))
    # Earlier passages answer slower, so requests finish out of order.
    slow = fake_client(delay=lambda claim, passage: 0.08 if "not available" in passage else 0.02)
    parallel = model_audit(two_claims, verifier(slow), max_concurrency=3)
    assert parallel.model_dump() == serial.model_dump()
    assert 1 < slow.chat.completions.peak <= 3


# --- the real SDK on a mock transport --------------------------------------------------------------

def completion(content=verdict("contradicts", "not available")):
    return {"id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "served-model",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 9, "total_tokens": 59}}


@pytest.fixture
def mock_sdk(monkeypatch):
    """Route the real ``openai.OpenAI`` client through ``httpx2.MockTransport``; ``handle(request)`` answers."""
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")
    seen = []
    state = {"handle": lambda request: httpx2.Response(200, json=completion())}

    def handler(request):
        seen.append(request)
        return state["handle"](request)

    real = openai.OpenAI
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: real(
        **kwargs, max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    return SimpleNamespace(seen=seen, state=state, httpx2=httpx2)


def test_sdk_request_goes_only_where_configured(mock_sdk, monkeypatch):
    """The SDK's own environment variables would redirect the request or add headers; none of them apply."""
    monkeypatch.setenv("XAI_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient-openai-key-0000")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://ambient.example.test/v1")
    monkeypatch.setenv("OPENAI_ORG_ID", "org-ambient")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-ambient")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "admin-ambient")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "authorization: Bearer ambient-gateway-token\nX-Gateway: gw-ambient")
    judgment = OpenAICompatVerifier("grok-model", "grok").judge(CLAIM, COUNTER)
    assert (judgment.relation, judgment.verifier) == ("contradicts", "openai:grok-model@api.x.ai")
    [request] = mock_sdk.seen
    assert str(request.url) == "https://api.x.ai/v1/chat/completions"
    assert request.headers.get_list("authorization") == [f"Bearer {SECRET}"]
    sent = "\n".join(f"{name}: {value}" for name, value in request.headers.items())
    for ambient in ("org-ambient", "proj-ambient", "admin-ambient", "gw-ambient", "ambient-gateway-token",
                    "sk-ambient-openai-key"):
        assert ambient not in sent
    body = json.loads(request.content)
    assert body["model"] == "grok-model" and body["response_format"]["type"] == "json_schema"


def test_sdk_ollama_needs_no_key_and_stays_on_localhost(mock_sdk, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    judge = OpenAICompatVerifier("llama-local", "ollama")
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    [request] = mock_sdk.seen
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {openai_compat.NO_KEY}"
    [stats] = judge.usage()
    assert (stats.requests, stats.input_tokens, stats.output_tokens) == (1, 50, 9)


def test_sdk_format_refusal_falls_back(mock_sdk, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)

    def handle(request):
        kind = json.loads(request.content).get("response_format", {}).get("type")
        if kind == "json_schema":
            return mock_sdk.httpx2.Response(400, json={"error": {"message": "This response_format type is "
                                                                            "unavailable now"}})
        return mock_sdk.httpx2.Response(200, json=completion())

    mock_sdk.state["handle"] = handle
    judge = OpenAICompatVerifier("deepseek-model", "deepseek")
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    kinds = [json.loads(r.content).get("response_format", {}).get("type") for r in mock_sdk.seen]
    assert kinds == ["json_schema", "json_object"]


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_sdk_errors_become_verifier_errors_without_the_key(mock_sdk, monkeypatch, status):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(
        status, json={"error": {"message": f"Incorrect API key provided: {SECRET[:10]}****{SECRET[-8:]} "
                                           f"(full: {SECRET})"}})
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model").judge(CLAIM, COUNTER)
    message = str(caught.value)
    assert message.startswith("openai:gpt-model@api.openai.com: ") and str(status) in message
    assert all(SECRET[i:i + 8] not in message for i in range(len(SECRET) - 7))


def test_sdk_connection_error_is_a_verifier_error(mock_sdk, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)

    def refuse(request):
        raise mock_sdk.httpx2.ConnectError("connection refused")

    mock_sdk.state["handle"] = refuse
    with pytest.raises(VerifierError, match="openai:gpt-model@api.openai.com: Connection error"):
        OpenAICompatVerifier("gpt-model").judge(CLAIM, COUNTER)


# --- CLI ------------------------------------------------------------------------------------------

def cli_with_fake(monkeypatch, client):
    """Make the CLI's OpenAICompatVerifier use ``client``; returns the arguments it was built with."""
    built = []
    real = openai_compat.OpenAICompatVerifier

    def make(model, provider, base_url, api_key_env, **kwargs):
        built.append((model, provider, base_url, api_key_env))
        return real(model, provider, base_url, api_key_env, client=client, **kwargs)

    monkeypatch.setattr(openai_compat, "OpenAICompatVerifier", make)
    return built


def test_cli_openai_verifier(two_claims, monkeypatch):
    client = fake_client()
    built = cli_with_fake(monkeypatch, client)
    from memfalsify import cli
    seen, real_audit = [], cli.audit
    monkeypatch.setattr(cli, "audit", lambda *a, **k: seen.append(k) or real_audit(*a, **k))
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"),
                                 "--verifier", "openai", "--openai-model", "grok-model", "--provider", "grok",
                                 "--base-url", "https://gateway.example.test/v1", "--api-key-env", "GATEWAY_KEY"])
    assert result.exit_code == 0, result.output
    assert built == [("grok-model", "grok", "https://gateway.example.test/v1", "GATEWAY_KEY")]
    assert "Verifier: `openai:grok-model@gateway.example.test`" in result.stdout
    assert "POSSIBLE COUNTEREVIDENCE" in result.stdout
    assert seen[0]["max_concurrency"] == 4 and seen[0]["max_judged"] == 8 and seen[0]["min_coverage"] == 0.2


def test_cli_openai_falls_back_and_says_so_on_stderr(two_claims, monkeypatch):
    cli_with_fake(monkeypatch, fake_client(refuse=("json_schema",)))
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"),
                                 "--verifier", "openai", "--openai-model", "m", "--provider", "ollama",
                                 "--max-concurrency", "1"])
    assert result.exit_code == 0 and "POSSIBLE COUNTEREVIDENCE" in result.stdout
    assert result.stderr.count("HTTP 400 for a request with response_format json_schema") == 1


@pytest.mark.parametrize("extra, message", [
    ([], "--verifier openai needs --openai-model"),
    (["--openai-model", "m", "--batch"], "--batch is not supported with --verifier openai in this version"),
    (["--openai-model", "m", "--base-url", "http://api.example.test/v1"], "must use https"),
    (["--openai-model", "m", "--api-key-env", SECRET], "never the key itself"),
])
def test_cli_openai_configuration_errors_exit_2(invoices, extra, message):
    result = runner.invoke(app, ["audit", str(invoices / "MEMORY.md"), "-e", str(invoices / "evidence"),
                                 "--verifier", "openai", *extra])
    assert result.exit_code == 2 and message in result.output
    assert SECRET not in result.output


def test_cli_openai_without_a_key_exits_2(invoices, monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    result = runner.invoke(app, ["audit", str(invoices / "MEMORY.md"), "--verifier", "openai",
                                 "--openai-model", "deepseek-model", "--provider", "deepseek"])
    assert result.exit_code == 2 and "DEEPSEEK_API_KEY (the deepseek default) is not set" in result.output


@pytest.mark.parametrize("fmt", ["markdown", "json", "html"])
def test_cli_api_error_exits_2_and_never_shows_the_key(two_claims, mock_sdk, monkeypatch, tmp_path, fmt):
    monkeypatch.setenv("TEAM_KEY", SECRET)
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(
        401, json={"error": {"message": f"Incorrect API key provided: {SECRET}", "type": "invalid_request_error"}})
    out = tmp_path / "report"
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"),
                                 "--verifier", "openai", "--openai-model", "gpt-model", "--api-key-env", "TEAM_KEY",
                                 "--format", fmt, "-o", str(out)])
    assert result.exit_code == 2 and not out.exists()
    assert result.output.startswith("error: openai:gpt-model@api.openai.com: Error code: 401")
    assert "[redacted]" in result.output and "Traceback" not in result.output
    assert all(SECRET[i:i + 8] not in result.output for i in range(len(SECRET) - 7))


@pytest.mark.parametrize("fmt", ["markdown", "json", "html"])
def test_cli_reports_never_carry_the_key(two_claims, mock_sdk, monkeypatch, fmt):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"),
                                 "--verifier", "openai", "--openai-model", "gpt-model", "--format", fmt])
    assert result.exit_code == 0 and mock_sdk.seen
    assert SECRET[:8] not in result.output and SECRET[-8:] not in result.output
    assert all(r.headers["authorization"] == f"Bearer {SECRET}" for r in mock_sdk.seen)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_cli_diff_takes_the_openai_options(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo.root)
    base = git_repo.commit("base", {"AGENTS.md": "- The database runs PostgreSQL 15.\n",
                                    "docs/api.md": "Invoice export to CSV is not available to customers.\n"})
    git_repo.write({"AGENTS.md": "- The database runs PostgreSQL 15.\n"
                                 "- Invoice export to CSV is available to customers.\n"})
    client = fake_client()
    built = cli_with_fake(monkeypatch, client)
    result = runner.invoke(app, ["diff", "AGENTS.md", "--base", base, "-e", ".", "--verifier", "openai",
                                 "--openai-model", "gemini-model", "--provider", "gemini", "--format", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert built == [("gemini-model", "gemini", None, None)]
    assert report["audit"]["verifier"] == "openai:gemini-model@generativelanguage.googleapis.com"
    assert len(client.chat.completions.calls) == 1  # only the added claim was judged
