"""The OpenAI-compatible verifier, offline: a fake Chat Completions client, and the real openai SDK on an
``httpx2.MockTransport`` where the request itself (URL, headers, body) or the SDK's errors matter."""
import importlib.util
import json
import logging
import shutil
import ssl
import sys
import threading
import time
import traceback
from datetime import date
from types import SimpleNamespace
from typing import get_args
from urllib.parse import quote

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


def status_error(status, message="refused", **fields):
    """The SDK's own exception for an HTTP status (BadRequestError for 400, and so on), as the SDK builds it:
    ``body`` is the error object, and the exception's text holds the body's repr."""
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")
    request = httpx2.Request("POST", "https://api.example.test/v1/chat/completions")
    response = httpx2.Response(status, request=request)
    kinds = {400: openai.BadRequestError, 401: openai.AuthenticationError, 422: openai.UnprocessableEntityError,
             429: openai.RateLimitError}
    body = {"message": message, "type": "invalid_request_error", **fields}
    return kinds.get(status, openai.APIStatusError)(f"Error code: {status} - {body!r}", response=response, body=body)


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
                raise status_error(self.refuse_status, f"response_format {mode} is not supported",
                                   param="response_format")
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
    with pytest.raises(VerifierError, match="characters API keys do not use") as caught:
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
        f"openai:test-model@api.openai.com: the endpoint refused response_format json_schema (HTTP {status} (type "
        "invalid_request_error, param response_format): response_format json_schema is not supported); asking "
        "with response_format json_object from now on",
        f"openai:test-model@api.openai.com: the endpoint refused response_format json_object (HTTP {status} (type "
        "invalid_request_error, param response_format): response_format json_object is not supported); asking "
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
    assert len(client.chat.completions.calls) == 7  # the six pairs, plus one refused request


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_other_api_errors_do_not_fall_back(status):
    client = fake_client(error=status_error(status, "denied"))
    with pytest.raises(VerifierError, match=f"^openai:test-model@api.openai.com: HTTP {status} "
                                            r"\(type invalid_request_error\): denied$"):
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
     "finish_reason: length; the answer did not end normally (only finish_reason stop is used)"),
    (answer(None, finish_reason="content_filter"),
     "finish_reason: content_filter; the answer did not end normally (only finish_reason stop is used)"),
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
    assert finished.rationale == printable(f"no verdict (finish_reason: {HOSTILE}; the answer did not end normally "
                                           "(only finish_reason stop is used))")
    with pytest.raises(VerifierError) as caught:
        verifier(fake_client(error=status_error(500, HOSTILE, code=HOSTILE))).judge(CLAIM, COUNTER)
    assert str(caught.value) == printable(f"openai:test-model@api.openai.com: HTTP 500 (type invalid_request_error, "
                                          f"code {HOSTILE}): {HOSTILE}")


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
    assert message == ("openai:test-model@api.openai.com: HTTP 401 (type invalid_request_error): "
                       f"{openai_compat.WITHHELD}")
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
    """The verifier's own client construction (``openai.DefaultHttpxClient`` with the verifier's options, then
    ``openai.OpenAI``), with only the transport replaced by ``httpx2.MockTransport``; ``handle(request)`` answers.
    The SDK's retries are off, so an error answer costs one request."""
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")
    seen, options = [], []
    state = {"handle": lambda request: httpx2.Response(200, json=completion())}

    def handler(request):
        seen.append(request)
        return state["handle"](request)

    real = openai.DefaultHttpxClient

    def client(**kwargs):
        options.append(kwargs)
        return real(**kwargs, transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(openai, "DefaultHttpxClient", client)
    monkeypatch.setattr(openai_compat, "MAX_RETRIES", 0)
    return SimpleNamespace(seen=seen, state=state, options=options, httpx2=httpx2)


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
    with pytest.raises(VerifierError, match="^openai:gpt-model@api.openai.com: could not connect to the endpoint$"):
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
    assert result.stderr.count("the endpoint refused response_format json_schema") == 1


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
    assert result.output.startswith("error: openai:gpt-model@api.openai.com: HTTP 401 (type invalid_request_error): "
                                    "the provider's message was withheld because it contained the API key")
    assert "Traceback" not in result.output
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


# --- security review regressions --------------------------------------------------------------------

def no_key(text, key=SECRET):
    """Neither the key, nor its URL-encoded or escaped forms, nor any 8-character run of them is in ``text``."""
    for form in openai_compat.secret_forms(key):
        assert all(form[i:i + 8] not in text for i in range(len(form) - 7)), form


def quote_key(key):
    return quote(key, safe="")


@pytest.mark.parametrize("key", ["sk-ab\\cd-efgh123456", 'sk-ab"cd-efgh123456', "sk-ab'cd-efgh123456",
                                 "sk-ab cd-efgh123456", "sk-abcd-efgh12345é", "sk-ab%cd-efgh123456",
                                 "sk-ab\x1bcd-efgh123456"])
def test_keys_with_characters_keys_do_not_use_are_refused(monkeypatch, key):
    pytest.importorskip("openai")
    monkeypatch.setenv("TEAM_KEY", key)
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    message = str(caught.value)
    assert "characters API keys do not use" in message and "TEAM_KEY" not in message and key.strip() not in message
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("key", ["sk-abc123def456ghi789jkl", "sk+abc/def=ghi~jkl.mno_pqr", "AIzaSyA-b_c0123456789xyz",
                                 "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl"])
def test_real_key_shapes_are_accepted(monkeypatch, key):
    pytest.importorskip("openai")
    monkeypatch.setenv("TEAM_KEY", key)
    assert OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")._secret == key


KEY = "sk+test/0123456789=abcdefSECRET"


def test_redact_finds_url_encoded_and_json_escaped_forms():
    text = (f"raw {KEY}; url {quote_key(KEY)}; json-escaped {KEY.replace('/', chr(92) + '/')}; "
            f"head {KEY[:12]}; short abc")
    shown = redact(text, KEY)
    no_key(shown, KEY)
    assert shown == "raw [redacted]; url [redacted]; json-escaped [redacted]; head [redacted]; short abc"


def test_provider_errors_are_built_from_fields_not_from_the_body_repr(mock_sdk, monkeypatch):
    """The message names status, type, code, param and the message text, each redacted; nested data and the
    body's repr never reach it, so an escaped or encoded copy of the key cannot slip past redaction."""
    monkeypatch.setenv("TEAM_KEY", KEY)
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(401, json={"error": {
        "message": f"Incorrect API key provided: {quote_key(KEY)} ({KEY.replace('/', chr(92) + '/')})",
        "type": "invalid_request_error", "code": "invalid_api_key", "param": None,
        "metadata": {"raw": KEY, "header": f"Bearer {KEY}"}}})
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY").judge(CLAIM, COUNTER)
    message = str(caught.value)
    assert message == ("openai:gpt-model@api.openai.com: HTTP 401 (type invalid_request_error, code "
                       "invalid_api_key): the provider's message was withheld because it contained the API key")
    no_key(message, KEY)


@pytest.mark.parametrize("body", ['{"error": "plain text error with sk+test/0123456789=abcdefSECRET"}',
                                  "<html>Bad gateway sk+test%2F0123456789%3DabcdefSECRET</html>", ""])
def test_provider_errors_without_an_error_object(mock_sdk, monkeypatch, body):
    monkeypatch.setenv("TEAM_KEY", KEY)
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(502, text=body)
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY").judge(CLAIM, COUNTER)
    assert str(caught.value).startswith("openai:gpt-model@api.openai.com: HTTP 502")
    no_key(str(caught.value), KEY)


def test_long_provider_messages_are_cut():
    judge = verifier(fake_client(error=status_error(500, "x" * 5000)))
    with pytest.raises(VerifierError) as caught:
        judge.judge(CLAIM, COUNTER)
    assert len(str(caught.value)) < 700 and str(caught.value).endswith("x...")


def test_tracebacks_of_verifier_errors_hold_no_sdk_exception(mock_sdk, monkeypatch):
    monkeypatch.setenv("TEAM_KEY", KEY)
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(
        401, json={"error": {"message": f"Incorrect API key provided: {KEY}"}})
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY").judge(CLAIM, COUNTER)
    error = caught.value
    assert error.__cause__ is None and error.__context__ is None
    shown = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    no_key(shown, KEY)
    assert "AuthenticationError" not in shown


def test_construction_errors_hold_no_exception_chain(monkeypatch):
    openai = pytest.importorskip("openai")
    monkeypatch.setenv("TEAM_KEY", KEY)

    def broken(**kwargs):
        raise openai.OpenAIError(f"cannot configure the client for {KEY}")

    monkeypatch.setattr(openai, "OpenAI", broken)
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    no_key(str(caught.value), KEY)
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", base_url="http://gateway.example.test/v1")
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_sdk_debug_logs_never_carry_the_key(mock_sdk, monkeypatch, caplog):
    """OPENAI_LOG=debug makes the SDK log the response's x-request-id header unfiltered; an endpoint that echoes
    the key there must not get it into the log, nor may a logger created after the verifier."""
    from openai._utils._logs import setup_logging
    monkeypatch.setenv("OPENAI_LOG", "debug")
    monkeypatch.setenv("TEAM_KEY", KEY)
    sdk_logger = logging.getLogger("openai")
    monkeypatch.setattr(sdk_logger, "level", sdk_logger.level)  # setup_logging changes it; put it back afterwards
    setup_logging()
    caplog.set_level(logging.DEBUG)
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(
        200, json=completion(), headers={"x-request-id": f"req-{KEY}"})
    judge = OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    late = logging.getLogger("openai.created_later")
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    late.debug("late logger sees %s and %s", KEY, quote_key(KEY))
    logged = caplog.text + "".join(record.getMessage() for record in caplog.records)
    assert logged.count("[log text withheld because it contained the API key]") >= 2
    no_key(logged, KEY)


def test_production_client_follows_no_redirect_and_ignores_proxies(mock_sdk, monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://proxy.example.test:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    for provider in ("openai", "ollama"):
        assert OpenAICompatVerifier("m", provider).judge(CLAIM, COUNTER).relation == "contradicts"
    # With trust_env, the proxy settings would route both requests away from the transport under test.
    assert [str(r.url) for r in mock_sdk.seen] == ["https://api.openai.com/v1/chat/completions",
                                                   "http://localhost:11434/v1/chat/completions"]
    assert [{k: v for k, v in o.items() if k != "verify"} for o in mock_sdk.options] == \
        [{"follow_redirects": False, "trust_env": False}] * 2
    assert all(isinstance(o["verify"], ssl.SSLContext) for o in mock_sdk.options)


@pytest.mark.parametrize("status, location", [
    (307, "https://elsewhere.example.test/v1/chat/completions"),
    (308, "http://elsewhere.example.test/v1/chat/completions"),
    (301, "http://localhost:11434/v1/chat/completions"),
    (302, "https://api.openai.com/v2/chat/completions"),
])
def test_redirects_are_errors_and_never_followed(mock_sdk, monkeypatch, status, location):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)

    def handle(request):
        if request.url.host == "api.openai.com" and request.url.path == "/v1/chat/completions":
            return mock_sdk.httpx2.Response(status, headers={"location": location})
        return mock_sdk.httpx2.Response(200, json=completion())

    mock_sdk.state["handle"] = handle
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model").judge(CLAIM, COUNTER)
    assert str(caught.value) == (f"openai:gpt-model@api.openai.com: HTTP {status}: the endpoint answered with a "
                                 "redirect, which is not followed; check the base URL")
    assert [str(r.url) for r in mock_sdk.seen] == ["https://api.openai.com/v1/chat/completions"]


@pytest.mark.parametrize("finish", ["length", "content_filter", "tool_calls", "insufficient_system_resource",
                                    "end_turn", "STOP", None])
def test_only_a_finished_answer_is_a_verdict(finish):
    judgment = verifier(fake_client(raw=answer(verdict("supports", "same words"), finish_reason=finish))).judge(
        CLAIM, CLAIM)
    assert judgment.relation == "unrelated"
    assert judgment.rationale == (f"no verdict (finish_reason: {finish}; the answer did not end normally (only "
                                  "finish_reason stop is used))")


@pytest.mark.parametrize("error", [
    {"status": 400, "message": "The model `gpt-nope` does not exist", "param": "model", "code": "model_not_found"},
    {"status": 400, "message": "This model's maximum context length is 8192 tokens", "param": "messages"},
    {"status": 422, "message": "Input should be a valid string", "param": "messages"},
])
def test_other_bad_requests_fail_at_once_without_a_fallback(error):
    lines = []
    error = dict(error)
    status = error.pop("status")
    client = fake_client(error=status_error(status, **error))
    judge = verifier(client, progress=lines.append)
    with pytest.raises(VerifierError) as caught:
        judge.judge(CLAIM, COUNTER)
    assert f"HTTP {status}" in str(caught.value) and error["message"] in str(caught.value)
    assert len(client.chat.completions.calls) == 1 and lines == [] and judge.response_format == "json_schema"


@pytest.mark.parametrize("fields", [
    {"message": "This response_format type is unavailable now"},  # DeepSeek's wording
    {"message": "Invalid parameter: 'response_format' of type 'json_schema' is not supported with this model.",
     "param": "response_format"},
    {"message": "'response_format.type' must be 'json_schema' or 'text'"},
    {"message": "invalid request", "param": "response_format.json_schema"},
    {"message": "JSON_SCHEMA output is not available for this model"},
])
def test_a_format_refusal_is_recognized_by_param_or_message(fields):
    class Refusing(FakeCompletions):
        def create(self, **kwargs):
            if kwargs.get("response_format", {}).get("type") == "json_schema":
                self.calls.append(kwargs)
                raise status_error(400, **fields)
            return super().create(**kwargs)

    judge = verifier(SimpleNamespace(chat=SimpleNamespace(completions=Refusing())))
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts" and judge.response_format == "json_object"


def test_format_negotiation_is_serialized_under_concurrency():
    lines = []
    client = fake_client(refuse=("json_schema", "json_object"), delay=lambda claim, passage: 0.02)
    judge = verifier(client, progress=lines.append)
    pairs = [(CLAIM, f"{COUNTER} {n}") for n in range(8)]
    assert [j.relation for j in judge_all(judge, pairs, max_concurrency=4)] == ["contradicts"] * 8
    kinds = [c.get("response_format", {}).get("type") for c in client.chat.completions.calls]
    assert kinds == ["json_schema", "json_object"] + [None] * 8
    assert len(lines) == 2 and judge.response_format == "text"


def test_cli_max_concurrency_4_costs_one_request_per_refused_format(two_claims, monkeypatch):
    client = fake_client(refuse=("json_schema", "json_object"), delay=lambda claim, passage: 0.02)
    cli_with_fake(monkeypatch, client)
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"),
                                 "--verifier", "openai", "--openai-model", "m", "--max-concurrency", "4"])
    assert result.exit_code == 0, result.output
    assert len(client.chat.completions.calls) == 4 + 2  # four pairs, plus one request per refused format
    assert result.stderr.count("the endpoint refused") == 2


# --- security review, round 3 -------------------------------------------------------------------------

WITHHELD = openai_compat.WITHHELD
LOG_WITHHELD = openai_compat.LOG_WITHHELD


def percent(text, lower=False, mixed=False):
    out = []
    for n, ch in enumerate(text):
        if ch.isalnum() and ch not in "+/=":
            out.append(ch)
            continue
        code = f"%{ord(ch):02X}"
        if lower or (mixed and n % 2):
            code = code.lower()
        out.append(code)
    return "".join(out)


def uescape(text, every=1):
    return "".join(f"\\u{ord(ch):04x}" if n % every == 0 else ch for n, ch in enumerate(text))


ENCODED = {
    "lowercase percent": percent(KEY, lower=True),
    "mixed-case percent": percent(KEY, mixed=True),
    "every character percent-escaped": "".join(f"%{ord(ch):02x}" for ch in KEY),
    "unicode escapes": uescape(KEY),
    "partly unicode-escaped": uescape(KEY, every=3),
    "partly percent-escaped": KEY[:10] + percent(KEY[10:], lower=True),
    "percent and unicode mixed": uescape(KEY[:9]) + percent(KEY[9:], lower=True),
    "escaped slash and lowercase percent": KEY.replace("/", "\\/").replace("+", "%2b").replace("=", "%3d"),
    "double percent-encoding": percent(percent(KEY, lower=True), lower=True),
    "html references": "".join(f"&#{ord(ch)};" for ch in KEY),
}


@pytest.mark.parametrize("encoded", list(ENCODED.values()), ids=list(ENCODED))
def test_holds_secret_sees_through_encodings(encoded):
    assert encoded != KEY and KEY not in encoded
    assert openai_compat.holds_secret(f"Incorrect API key provided: {encoded}.", KEY)
    assert not openai_compat.holds_secret("The model `gpt-x` does not exist", KEY)


@pytest.mark.parametrize("encoded", list(ENCODED.values()), ids=list(ENCODED))
def test_sdk_401_diagnostics_with_an_encoded_key_are_withheld(mock_sdk, monkeypatch, encoded):
    """The provider's own message, as real SDK diagnostics carry it, with the key escaped in some way."""
    monkeypatch.setenv("TEAM_KEY", KEY)
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(401, json={"error": {
        "message": f"Incorrect API key provided: {encoded}.", "type": "invalid_request_error",
        "code": "invalid_api_key"}})
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY").judge(CLAIM, COUNTER)
    assert str(caught.value) == ("openai:gpt-model@api.openai.com: HTTP 401 (type invalid_request_error, code "
                                 f"invalid_api_key): {WITHHELD}")


def test_an_encoded_key_in_any_error_field_is_withheld(team_key):
    error = status_error(400, "fine", code=percent(KEY, lower=True), param=uescape(KEY, every=2))
    with pytest.raises(VerifierError) as caught:
        verifier(fake_client(error=error), api_key_env="TEAM_KEY").judge(CLAIM, COUNTER)
    assert str(caught.value) == (f"openai:test-model@api.openai.com: HTTP 400 (type invalid_request_error, code "
                                 f"{WITHHELD}, param {WITHHELD}): fine")


@pytest.fixture
def team_key(monkeypatch):
    monkeypatch.setenv("TEAM_KEY", KEY)
    return KEY


def test_messages_without_the_key_are_kept(mock_sdk, team_key):
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(404, json={"error": {
        "message": "The model `gpt-nope` does not exist", "type": "invalid_request_error",
        "code": "model_not_found"}})
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY").judge(CLAIM, COUNTER)
    assert str(caught.value).endswith("(type invalid_request_error, code model_not_found): The model `gpt-nope` "
                                      "does not exist")


def test_a_logger_created_during_a_request_cannot_log_the_key(mock_sdk, team_key, caplog):
    caplog.set_level(logging.DEBUG)

    def handle(request):
        name = f"httpx2.created_mid_request_{len(mock_sdk.seen)}"
        assert name not in logging.Logger.manager.loggerDict  # created here, after every per-request filter pass
        logging.getLogger(name).warning("auth header Bearer %s (%s)", KEY, percent(KEY, lower=True))
        return mock_sdk.httpx2.Response(200, json=completion())

    mock_sdk.state["handle"] = handle
    assert OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY").judge(CLAIM, COUNTER).relation == "contradicts"
    [record] = [r for r in caplog.records if r.name.startswith("httpx2.created_mid_request")]
    assert record.getMessage() == LOG_WITHHELD and record.args is None
    no_key(caplog.text, KEY)


def test_exceptions_and_stacks_in_sdk_log_records_are_withheld(mock_sdk, team_key, caplog):
    caplog.set_level(logging.DEBUG)
    OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    logger = logging.getLogger("openai.round3")
    try:
        raise RuntimeError(f"request failed for {uescape(KEY)}")
    except RuntimeError:
        logger.exception("plain message")
    logger.warning("with a stack", stack_info=True)
    stack = logger.makeRecord(logger.name, logging.WARNING, __file__, 1, "made by hand", None, None,
                              sinfo=f"Stack (most recent call last):\n  token={percent(KEY, lower=True)}")
    logger.handle(stack)
    sdk_logger = logging.getLogger("openai._base_client")  # filtered since the verifier was built
    direct = logging.LogRecord(sdk_logger.name, logging.ERROR, __file__, 1, "direct %s", (KEY,),
                               (RuntimeError, RuntimeError(KEY), None), sinfo=f"stack {KEY}")
    sdk_logger.handle(direct)  # made without the record factory: the logger's filter sanitizes it
    records = [r for r in caplog.records if r.name in ("openai.round3", "openai._base_client")]
    assert [r.getMessage() for r in records] == ["plain message", "with a stack", "made by hand", LOG_WITHHELD]
    assert records[0].exc_info is None and records[0].exc_text == LOG_WITHHELD
    assert records[1].stack_info and "with a stack" in records[1].stack_info  # no key there: kept
    assert records[2].stack_info == LOG_WITHHELD
    assert records[3].exc_info is None and records[3].exc_text == LOG_WITHHELD and records[3].stack_info == LOG_WITHHELD
    no_key(caplog.text, KEY)


def test_a_replaced_record_factory_is_wrapped_again(mock_sdk, team_key, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    judge = OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    plain = logging.LogRecord
    monkeypatch.setattr(logging, "_logRecordFactory", plain)  # another library resets the factory
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"  # each request re-installs the guard
    assert getattr(logging.getLogRecordFactory(), "_memfalsify_guard", False)
    logging.getLogger("httpcore2.round3_after_reset").warning("key %s", KEY)
    no_key(caplog.text, KEY)


@pytest.mark.parametrize("error", [
    {"param": "model", "code": "model_not_found", "message": "The model 'json_schema-not-found' does not exist"},
    {"code": "model_not_found", "message": "The model 'json_schema-not-found' does not exist"},
    {"message": "The model 'json_schema-not-found' does not exist"},
    {"message": "The model `json_object` does not exist or you do not have access to it."},
    {"code": "context_length_exceeded", "message": "response_format and messages exceed the context"},
    {"param": "messages", "message": "json_schema is not supported for this message format"},
])
def test_a_format_word_inside_a_model_name_or_another_param_does_not_downgrade(error):
    lines = []
    client = fake_client(error=status_error(400, **error))
    judge = verifier(client, progress=lines.append)
    with pytest.raises(VerifierError):
        judge.judge(CLAIM, COUNTER)
    assert len(client.chat.completions.calls) == 1 and lines == [] and judge.response_format == "json_schema"


@pytest.mark.parametrize("fields", [
    {"code": "invalid_request_error", "message": "This response_format type is unavailable now"},
    {"code": "unsupported_parameter", "message": "json_schema is not supported with this model"},
    {"code": "json_schema_not_supported", "message": "Unsupported."},
    {"code": "400", "message": "'json_object' is not supported"},
])
def test_a_generic_or_format_code_still_lets_the_message_decide(fields):
    class Refusing(FakeCompletions):
        def create(self, **kwargs):
            if kwargs.get("response_format", {}).get("type") == "json_schema":
                self.calls.append(kwargs)
                raise status_error(400, **fields)
            return super().create(**kwargs)

    judge = verifier(SimpleNamespace(chat=SimpleNamespace(completions=Refusing())))
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts" and judge.response_format == "json_object"


def test_tls_context_on_linux_ignores_the_certificate_variables(monkeypatch, tmp_path):
    """On the OpenSSL path, the CA locations compiled into OpenSSL are loaded; SSL_CERT_FILE and SSL_CERT_DIR are
    not, and neither is anything through OpenSSL's default paths (which read those variables)."""
    system = tmp_path / "system-bundle.pem"
    system.write_text("system", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "attacker.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    monkeypatch.setattr(openai_compat.sys, "platform", "linux")
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: ssl.DefaultVerifyPaths(
        str(tmp_path / "attacker.pem"), str(tmp_path), "SSL_CERT_FILE", str(system), "SSL_CERT_DIR",
        str(tmp_path / "no-such-dir")))
    made = []

    def create(cafile=None, capath=None, cadata=None, **kwargs):
        made.append((cafile, capath, cadata))
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    def env_paths(self):
        raise AssertionError("OpenSSL's default paths read SSL_CERT_FILE and SSL_CERT_DIR")

    monkeypatch.setattr(ssl, "create_default_context", create)
    monkeypatch.setattr(ssl.SSLContext, "set_default_verify_paths", env_paths)
    monkeypatch.setattr(ssl.SSLContext, "load_default_certs", env_paths)
    assert isinstance(openai_compat.tls_context(), ssl.SSLContext)
    assert made == [(str(system), None, None)]
    system.unlink()  # no compiled-in bundle: the usual bundle paths, and else no CA at all (https fails closed)
    monkeypatch.setattr(openai_compat, "CA_FILES", (str(tmp_path / "missing.pem"),))
    context = openai_compat.tls_context()
    assert made == [(str(system), None, None)] and context.cert_store_stats()["x509_ca"] == 0
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


@pytest.mark.skipif(sys.platform == "win32", reason="the control relies on OpenSSL reading SSL_CERT_FILE, as on "
                                                    "Linux and macOS")
def test_tls_context_on_linux_does_not_load_a_ca_named_by_ssl_cert_file(monkeypatch, tmp_path):
    """A real CA certificate in the file SSL_CERT_FILE names: Python's default context loads it, ours does not."""
    pytest.importorskip("cryptography")
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "memfalsify test CA from SSL_CERT_FILE")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    bundle = tmp_path / "env-ca.pem"
    bundle.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    monkeypatch.setattr(openai_compat.sys, "platform", "linux")

    def subjects(context):
        return {dict(item[0] for item in ca["subject"]).get("commonName") for ca in context.get_ca_certs()}

    assert "memfalsify test CA from SSL_CERT_FILE" in subjects(ssl.create_default_context())  # the variable works
    assert "memfalsify test CA from SSL_CERT_FILE" not in subjects(openai_compat.tls_context())


def test_tls_context_on_macos_and_windows_is_the_system_trust_store(monkeypatch):
    truststore = pytest.importorskip("truststore")
    for platform in ("darwin", "win32"):
        monkeypatch.setattr(openai_compat.sys, "platform", platform)
        assert isinstance(openai_compat.tls_context(), truststore.SSLContext)


MALFORMED = {
    "choices is a number": {"choices": 1},
    "choices is a mapping": {"choices": {"a": 1}},
    "a choice is a number": {"choices": [5]},
    "the message is text": {"choices": [{"index": 0, "finish_reason": "stop", "message": "text"}]},
    "content is a number": {"choices": [{"index": 0, "finish_reason": "stop", "message": {"content": 5}}]},
    "content is a list": {"choices": [{"index": 0, "finish_reason": "stop",
                                       "message": {"content": [verdict("supports")]}}]},
    "finish_reason is a number": {"choices": [{"index": 0, "finish_reason": 7,
                                               "message": {"content": verdict("supports")}}]},
    "the body is a list": [1, 2],
    "the body is text": "just text",
}


@pytest.mark.parametrize("body", list(MALFORMED.values()), ids=list(MALFORMED))
def test_malformed_completions_are_no_verdict(mock_sdk, team_key, body):
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(200, json=body)
    judge = OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    judgment = judge.judge(CLAIM, CLAIM)
    assert judgment.relation == "unrelated"
    assert judgment.rationale == ("no verdict (finish_reason: None; the response is not a well-formed chat "
                                  "completion)")
    assert judge.usage()[0].requests == 1


def test_malformed_usage_counts_as_missing(mock_sdk, team_key):
    body = {**completion(), "usage": {"prompt_tokens": "lots", "completion_tokens": True, "total_tokens": []}}
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(200, json=body)
    judge = OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    [stats] = judge.usage()
    assert (stats.requests, stats.input_tokens, stats.output_tokens) == (1, 0, 0)


@pytest.mark.parametrize("body", [{"choices": 1}, {"choices": {"a": 1}}], ids=["number", "mapping"])
def test_cli_reports_malformed_completions_as_no_verdict(two_claims, mock_sdk, team_key, body):
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(200, json=body)
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"),
                                 "--verifier", "openai", "--openai-model", "gpt-model", "--api-key-env", "TEAM_KEY",
                                 "--format", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert [f["status"] for f in report["findings"]] == ["insufficient_evidence", "insufficient_evidence"]
    assert report["verifier_stats"][0]["requests"] == 4
    strict = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"),
                                 "--verifier", "openai", "--openai-model", "gpt-model", "--api-key-env", "TEAM_KEY",
                                 "--strict"])
    assert strict.exit_code == 0 and "Traceback" not in strict.output
