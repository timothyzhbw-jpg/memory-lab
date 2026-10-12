"""The OpenAI-compatible verifier, offline: a fake Chat Completions client, and the real openai SDK on an
``httpx2.MockTransport`` where the request itself (URL, headers, body), the SDK's errors or its logging matter."""
import functools
import importlib.util
import json
import logging
import re
import shutil
import threading
import time
import traceback
from datetime import date
from pathlib import Path
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
KEY = "sk+test/0123456789=abcdefSECRET"  # with the characters percent-encoding and JSON escaping change


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
    """``create()`` answers ``decide(claim, passage)``, or ``raw`` as is; ``error`` is raised for every request.
    Records every call and how many ran at once."""

    def __init__(self, decide=decide, error=None, usage=None, delay=lambda claim, passage: 0.0, raw=None):
        self.decide, self.error, self.usage, self.delay, self.raw = decide, error, usage, delay, raw
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
            if self.raw is not None:
                return self.raw
            return answer(self.decide(claim, passage), usage=self.usage)
        finally:
            with self.lock:
                self.active -= 1


def fake_client(**kwargs):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(**kwargs)))


def verifier(client=None, model="test-model", **options):
    return OpenAICompatVerifier(model, client=client or fake_client(), **options)


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


@pytest.fixture
def team_key(monkeypatch):
    monkeypatch.setenv("TEAM_KEY", KEY)
    return KEY


def model_audit(root, judge, **kwargs):
    return audit(root / "MEMORY.md", root / "evidence", verifier=judge, as_of=date(2026, 10, 10),
                 min_coverage=0.2, max_judged=8, **kwargs)


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


def no_key(text, key=KEY):
    """Neither the key nor one of its percent-encoded or JSON-escaped forms is in ``text``."""
    for form in openai_compat.secret_forms(key):
        assert form not in text, form


def percent(text, times=1, lower=False):
    """``text`` percent-encoded ``times`` times; ``lower`` writes the escapes' hex digits in lowercase."""
    for _ in range(times):
        text = quote(text, safe="")
        if lower:
            text = re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), text)
    return text


# --- providers, overrides and the transport rule ----------------------------------------------

def test_presets_fill_base_url_key_variable_and_response_format():
    assert {p.value: (resolve_endpoint(p.value).base_url, resolve_endpoint(p.value).api_key_env,
                      resolve_endpoint(p.value).response_format) for p in Provider} == {
        "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "json_schema"),
        "grok": ("https://api.x.ai/v1", "XAI_API_KEY", "json_schema"),
        # DeepSeek documents text and json_object only: https://api-docs.deepseek.com/api/create-chat-completion
        "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY", "json_object"),
        # Google's documented OpenAI-compatible endpoint: https://ai.google.dev/gemini-api/docs/openai
        "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "json_schema"),
        "ollama": ("http://localhost:11434/v1", None, "json_schema"),
    }
    assert set(PRESETS) == set(Provider)
    assert resolve_endpoint().provider is Provider.openai


def test_explicit_base_url_and_key_variable_override_the_preset():
    url = resolve_endpoint("deepseek", base_url="https://gateway.example.test/v1")
    assert (url.base_url, url.api_key_env, url.preset_key, url.response_format) == \
        ("https://gateway.example.test/v1", "DEEPSEEK_API_KEY", True, "json_schema")  # a custom URL: json_schema
    key = resolve_endpoint("gemini", api_key_env="TEAM_GEMINI_KEY")
    assert (key.base_url, key.api_key_env, key.preset_key) == (PRESETS[Provider.gemini].base_url, "TEAM_GEMINI_KEY",
                                                               False)
    both = resolve_endpoint("ollama", "http://127.0.0.1:8080/v1", "LOCAL_KEY")
    assert (both.base_url, both.api_key_env, both.host) == ("http://127.0.0.1:8080/v1", "LOCAL_KEY", "127.0.0.1:8080")
    assert resolve_endpoint("deepseek", api_key_env="DS_KEY").response_format == "json_object"
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


@pytest.mark.parametrize("key", ["sk-ab\\cd-efgh123456", 'sk-ab"cd-efgh123456', "sk-ab'cd-efgh123456",
                                 "sk-ab cd-efgh123456", "sk-abcd-efgh12345\u00e9", "sk-ab%cd-efgh123456",
                                 "sk-ab\x1bcd-efgh123456"])
def test_keys_with_characters_keys_do_not_use_are_refused(monkeypatch, key):
    pytest.importorskip("openai")
    monkeypatch.setenv("TEAM_KEY", key)
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    message = str(caught.value)
    assert "characters API keys do not use" in message and "TEAM_KEY" not in message and key.strip() not in message
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("key", ["sk-short", "123456789012345", "a" * 15])
def test_keys_shorter_than_16_characters_are_refused(monkeypatch, key):
    pytest.importorskip("openai")
    monkeypatch.setenv("TEAM_KEY", key)
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    assert str(caught.value) == ("openai:gpt-model@api.openai.com: the API key is shorter than 16 characters, which "
                                 "no real key is")
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("key", ["sk-abc123def456ghi789jkl", KEY, "AIzaSyA-b_c0123456789xyz", "1234567890123456",
                                 "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl"])
def test_real_key_shapes_are_accepted(monkeypatch, key):
    pytest.importorskip("openai")
    monkeypatch.setenv("TEAM_KEY", key)
    assert OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")._secret == key


@pytest.mark.skipif(importlib.util.find_spec("openai") is not None, reason="openai is installed")
def test_missing_sdk_is_a_verifier_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    with pytest.raises(VerifierError, match=r"memfalsify\[openai\]"):
        OpenAICompatVerifier("gpt-model")


# --- the request and the response format -------------------------------------------------------

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


@pytest.mark.parametrize("options, sent", [
    ({}, "json_schema"),
    ({"provider": "deepseek"}, "json_object"),
    ({"provider": "deepseek", "base_url": "https://gateway.example.test/v1"}, "json_schema"),
    ({"provider": "deepseek", "response_format": "json_schema"}, "json_schema"),
    ({"provider": "ollama", "response_format": "json_object"}, "json_object"),
    ({"provider": "gemini", "response_format": "text"}, None),
])
def test_the_response_format_is_the_option_or_the_provider_default(options, sent):
    client = fake_client()
    judge = verifier(client, **options)
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    [call] = client.chat.completions.calls
    assert call.get("response_format", {}).get("type") == sent
    assert judge.response_format == (sent or "text")
    assert FORMATS == ("json_schema", "json_object", "text")


def test_an_unknown_response_format_is_refused():
    with pytest.raises(VerifierError, match="the response format must be one of: json_schema, json_object, text"):
        verifier(response_format="json")


@pytest.mark.parametrize("fields", [
    {"message": "This response_format type is unavailable now"},
    {"message": "The requested model with ID 'json_schema' was not found."},
    {"message": "Invalid model: json_schema.", "param": "model"},
    {"message": "invalid request", "param": "response_format"},
])
@pytest.mark.parametrize("status", [400, 422])
def test_a_rejected_request_is_never_retried_in_another_format(fields, status):
    client = fake_client(error=status_error(status, **fields))
    judge = verifier(client)
    with pytest.raises(VerifierError) as caught:
        judge.judge(CLAIM, COUNTER)
    assert len(client.chat.completions.calls) == 1 and judge.response_format == "json_schema"
    assert str(caught.value).endswith(
        "the endpoint rejected the request. If this endpoint does not support JSON schema output, try "
        "--openai-response-format json_object (or text).")


@pytest.mark.parametrize("response_format, advice", [
    ("json_object", ". If this endpoint does not support JSON object output, try --openai-response-format text."),
    ("text", ""),
])
def test_the_rejection_hint_suggests_the_next_simpler_format(response_format, advice):
    judge = verifier(fake_client(error=status_error(400)), response_format=response_format)
    with pytest.raises(VerifierError) as caught:
        judge.judge(CLAIM, COUNTER)
    assert str(caught.value).endswith(f"the endpoint rejected the request{advice}")


# --- answers -------------------------------------------------------------------------------------

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
@pytest.mark.parametrize("response_format", FORMATS)
def test_unusable_answers_are_no_verdict_never_support(content, response_format):
    judge = verifier(fake_client(decide=lambda claim, passage: content), response_format=response_format)
    judgment = judge.judge(CLAIM, CLAIM)
    assert (judgment.relation, judgment.rationale) == ("unrelated", "no verdict (the answer is not a JSON verdict)")


@pytest.mark.parametrize("response, reason", [
    (answer(None, refusal="I can't help with that."), "the model refused"),
    (answer(verdict("supports"), refusal="no"), "the model refused"),
    (answer(None), "the answer is empty"),
    (answer("   "), "the answer is empty"),
    (answer('{"relation": "supports", "rationale": "cut', finish_reason="length"), "the answer did not finish normally"),
    (answer(None, finish_reason="content_filter"), "the answer did not finish normally"),
    (answer(choices=False), "the response has no answer"),
    (SimpleNamespace(), "the response has no answer"),
])
def test_refusals_and_empty_responses_are_no_verdict(response, reason):
    judgment = verifier(fake_client(raw=response)).judge(CLAIM, CLAIM)
    assert (judgment.relation, judgment.rationale) == ("unrelated", f"no verdict ({reason})")


@pytest.mark.parametrize("finish", ["length", "content_filter", "tool_calls", "insufficient_system_resource",
                                    "end_turn", "STOP", None, f"stop {KEY}",
                                    "".join(f"\\u{ord(ch):04x}" for ch in KEY)])
def test_only_a_finished_answer_is_a_verdict_and_finish_reason_is_never_echoed(finish):
    judgment = verifier(fake_client(raw=answer(verdict("supports", "same words"), finish_reason=finish))).judge(
        CLAIM, CLAIM)
    assert (judgment.relation, judgment.rationale) == ("unrelated", "no verdict (the answer did not finish normally)")


def test_a_fenced_json_answer_is_read():
    for text in ['```json\n{"relation": "supports", "rationale": "same"}\n```',
                 '\n```\n{"relation": "supports", "rationale": "same"}\n```\n',
                 '  {"relation": "supports", "rationale": "same"}  ']:
        assert parse_verdict(text).relation == "supports"


# Newline, a Markdown breakout, a terminal color, a bidi override and a workflow-command marker.
HOSTILE = "seen\n## pwned\x1b[31m\u202e::set-output"


def test_model_text_stays_one_inert_line(two_claims):
    client = fake_client(decide=lambda claim, passage: verdict("contradicts", HOSTILE))
    report = model_audit(two_claims, verifier(client))
    rationales = [e.rationale for f in report.findings for e in f.evidence]
    assert rationales and all(r == printable(HOSTILE) for r in rationales)
    markdown = to_markdown(report)
    assert "\x1b" not in markdown and "\u202e" not in markdown
    assert not any(line.startswith(("## pwned", "::set-output")) for line in markdown.splitlines())


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
    assert (judgment.relation, judgment.rationale) == \
        ("unrelated", "no verdict (the response is not a well-formed chat completion)")
    assert judge.usage()[0].requests == 1


@pytest.mark.parametrize("prompt, completion_tokens, counted", [
    (10_000_000, 9, (10_000_000, 9)),
    (10_000_001, 9, (0, 9)),
    (12345678901234567890, 12345678901234567890, (0, 0)),
    (-5, 2**63, (0, 0)),
])
def test_token_counts_are_capped_per_request(mock_sdk, team_key, prompt, completion_tokens, counted):
    body = {**completion(), "usage": {"prompt_tokens": prompt, "completion_tokens": completion_tokens}}
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(200, json=body)
    judge = OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    [stats] = judge.usage()
    assert (stats.requests, stats.input_tokens, stats.output_tokens) == (1, *counted)


def test_malformed_usage_counts_as_missing(mock_sdk, team_key):
    body = {**completion(), "usage": {"prompt_tokens": "lots", "completion_tokens": True, "total_tokens": []}}
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(200, json=body)
    judge = OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    [stats] = judge.usage()
    assert (stats.requests, stats.input_tokens, stats.output_tokens) == (1, 0, 0)


# --- the rationale, the only free text kept --------------------------------------------------------

def test_redact_replaces_the_key_and_its_single_level_encodings():
    text = (f"raw {KEY}; url {percent(KEY)}; url lowercase {percent(KEY, lower=True)}; "
            f"json {KEY.replace('/', chr(92) + '/')}; twice {percent(KEY, times=2)}")
    shown = redact(text, KEY)
    assert shown == (f"raw [redacted]; url [redacted]; url lowercase [redacted]; json [redacted]; "
                     f"twice {percent(KEY, times=2)}")  # a second encoding is not undone: see the threat model


def test_the_key_in_a_rationale_is_redacted(team_key):
    rationale = f"the passage quotes {KEY} and {percent(KEY, lower=True)}"
    judge = verifier(fake_client(decide=lambda claim, passage: verdict("supports", rationale)),
                     api_key_env="TEAM_KEY")
    judgment = judge.judge(CLAIM, CLAIM)
    assert judgment.rationale == "the passage quotes [redacted] and [redacted]"


@pytest.mark.parametrize("rationale", [
    "The passage says CSV export is not available to customers.",
    "docs/billing.md says the limit is 60 requests per minute, not 100.",
    "See /usr/local/share/memfalsify/evidence/billing-notes.md for the decision.",
    "https://wiki.example.test/decisions/2026/billing says otherwise.",
    # Codex's examples: a Chinese sentence without spaces, and a decisive JSON flag.
    "\u6bb5\u843d\u5199\u660e\u5ba2\u6237\u4e0d\u80fd\u628a\u53d1\u7968\u5bfc\u51fa\u4e3aCSV\u683c\u5f0f"
    "\uff0c\u6240\u4ee5\u8fd9\u6761\u8bb0\u5fc6\u88ab\u53cd\u9a73\u4e86\u3002",
    'The config sets {"customer_invoice_export_enabled":false}, so export is off.',
])
def test_a_rationale_without_the_key_is_kept_whole(team_key, rationale):
    judge = verifier(fake_client(decide=lambda claim, passage: verdict("supports", rationale)), api_key_env="TEAM_KEY")
    assert judge.judge(CLAIM, CLAIM).rationale == rationale


def test_a_chinese_rationale_loses_only_the_key_run(team_key):
    rationale = ("\u6bb5\u843d\u5f15\u7528\u4e86sk%2btest%2F0123456789%3dabcdefSECRET\u8fd9\u4e2a\u4ee4\u724c"
                 "\uff0c\u5ba2\u6237\u4e0d\u80fd\u5bfc\u51faCSV\u3002")
    judge = verifier(fake_client(decide=lambda claim, passage: verdict("supports", rationale)),
                     api_key_env="TEAM_KEY")
    assert judge.judge(CLAIM, CLAIM).rationale == rationale.replace("sk%2btest%2F0123456789%3dabcdefSECRET",
                                                                    "[\u2026]")


@pytest.mark.parametrize("encoded", ["sk%2btest%2F0123456789%3dabcdefSECRET", "sk+test%2f0123456789=abcdefSECRET",
                                     "%73k+test/0123456789=abcdefSECRET", "sk+test\\u002f0123456789=abcdefSECRET",
                                     "sk+test&#47;0123456789&#61;abcdefSECRET", "sk+test\\x2f0123456789=abcdefSECRET",
                                     "sk%2Btest%2F0123", "0123456789%3dabcd"])
def test_a_run_showing_part_of_the_key_once_decoded_is_elided(team_key, encoded):
    rationale = f"the passage quotes {encoded} as the token"
    judge = verifier(fake_client(decide=lambda claim, passage: verdict("supports", rationale)), api_key_env="TEAM_KEY")
    assert judge.judge(CLAIM, CLAIM).rationale == "the passage quotes [\u2026] as the token"


def test_a_word_shared_with_a_pathological_key_is_the_only_casualty(monkeypatch):
    monkeypatch.setenv("TEAM_KEY", "sk-xxcustomersxx")  # a key holding an 8-letter word
    rationale = "the customers can export invoices to CSV"
    judge = verifier(fake_client(decide=lambda claim, passage: verdict("supports", rationale)),
                     api_key_env="TEAM_KEY")
    assert judge.judge(CLAIM, CLAIM).rationale == "the [\u2026] can export invoices to CSV"


def test_the_whole_text_check_withholds_what_the_run_elision_misses(team_key, monkeypatch):
    monkeypatch.setattr(openai_compat, "elide_secret_runs", lambda text, secret: text)  # a run rule that missed it
    mixed = "sk%2btest%2F0123456789%3dabcdefSECRET"  # not one of secret_forms, so the literal redaction misses it
    judge = verifier(fake_client(decide=lambda claim, passage: verdict("supports", f"token {mixed}")),
                     api_key_env="TEAM_KEY")
    assert judge.judge(CLAIM, CLAIM).rationale == openai_compat.WITHHELD_RATIONALE


# --- provider errors: status, plain-token fields and a fixed hint -----------------------------------

@pytest.mark.parametrize("status, hint", [
    (401, "the key in OPENAI_API_KEY was refused"),
    (403, "the key in OPENAI_API_KEY was refused"),
    (404, "the model or the endpoint was not found; check --openai-model and --base-url"),
    (429, "rate limited or out of quota"),
    (500, "the provider had an error; try again"),
    (503, "the provider had an error; try again"),
    (409, "the provider returned an error"),
    (418, "the provider returned an error"),
])
def test_errors_get_a_fixed_hint_for_their_status(monkeypatch, status, hint):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    judge = verifier(fake_client(error=status_error(status, f"details about {SECRET}", code="some_code")))
    with pytest.raises(VerifierError) as caught:
        judge.judge(CLAIM, COUNTER)
    assert str(caught.value) == (f"openai:test-model@api.openai.com: HTTP {status} (type invalid_request_error, "
                                 f"code some_code): {hint}")


def test_a_refused_key_names_only_a_preset_variable(monkeypatch):
    monkeypatch.setenv("MY_TEAM_KEY", SECRET)
    judge = verifier(fake_client(error=status_error(401)), api_key_env="MY_TEAM_KEY")
    with pytest.raises(VerifierError, match="the key in the environment variable you named was refused$"):
        judge.judge(CLAIM, COUNTER)
    judge = verifier(fake_client(error=status_error(401)), provider="ollama")
    with pytest.raises(VerifierError, match="carries no key; name one with --api-key-env$"):
        judge.judge(CLAIM, COUNTER)


@pytest.mark.parametrize("fields, shown", [
    ({"code": "invalid_api_key", "param": "messages.0.content"},
     "(type invalid_request_error, code invalid_api_key, param messages.0.content)"),
    ({"code": 400}, "(type invalid_request_error, code 400)"),
    ({"type": "bad request", "code": "x" * 65}, ""),
    ({"type": f"key {KEY}", "code": percent(KEY, lower=True), "param": None}, ""),
    ({"type": True, "code": ["model_not_found"], "param": {"name": "model"}}, ""),
    ({"type": "", "code": "a\nb", "param": "response_format\u202e"}, ""),
])
def test_error_fields_are_shown_only_as_plain_tokens(fields, shown):
    judge = verifier(fake_client(error=status_error(429, **fields)))
    with pytest.raises(VerifierError) as caught:
        judge.judge(CLAIM, COUNTER)
    expected = "HTTP 429" + (f" {shown}" if shown else "") + ": rate limited or out of quota"
    assert str(caught.value) == f"openai:test-model@api.openai.com: {expected}"


ENCODED = {
    "as is": KEY,
    "percent-encoded once": percent(KEY),
    "percent-encoded five times": percent(KEY, times=5),
    "lowercase percent": percent(KEY, lower=True),
    "unicode escapes": "".join(f"\\u{ord(ch):04x}" for ch in KEY),
    "JSON-escaped": KEY.replace("/", "\\/"),
    "HTML references": "".join(f"&#{ord(ch)};" for ch in KEY),
}


@pytest.mark.parametrize("encoded", list(ENCODED.values()), ids=list(ENCODED))
def test_the_providers_message_is_never_shown(mock_sdk, team_key, encoded):
    """Whatever the provider writes, and however it encodes the key, its text never reaches the error."""
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(401, json={"error": {
        "message": f"Incorrect API key provided: {encoded}.", "type": "invalid_request_error",
        "code": "invalid_api_key", "metadata": {"raw": KEY}}})
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY").judge(CLAIM, COUNTER)
    message = str(caught.value)
    assert message == ("openai:gpt-model@api.openai.com: HTTP 401 (type invalid_request_error, code "
                       "invalid_api_key): the key in the environment variable you named was refused")
    assert "Incorrect" not in message and encoded not in message


@pytest.mark.parametrize("body", ['{"error": "plain text error with sk+test/0123456789=abcdefSECRET"}',
                                  "<html>Bad gateway sk+test%2F0123456789%3DabcdefSECRET</html>", ""])
def test_errors_without_an_error_object(mock_sdk, team_key, body):
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(502, text=body)
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY").judge(CLAIM, COUNTER)
    assert str(caught.value) == "openai:gpt-model@api.openai.com: HTTP 502: the provider had an error; try again"


def test_connection_errors_are_verifier_errors(mock_sdk, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)

    def refuse(request):
        raise mock_sdk.httpx2.ConnectError(f"connection refused for {SECRET}")

    mock_sdk.state["handle"] = refuse
    with pytest.raises(VerifierError, match="^openai:gpt-model@api.openai.com: could not connect to the endpoint$"):
        OpenAICompatVerifier("gpt-model").judge(CLAIM, COUNTER)


def test_tracebacks_of_verifier_errors_hold_no_sdk_exception(mock_sdk, team_key):
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(
        401, json={"error": {"message": f"Incorrect API key provided: {KEY}"}})
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY").judge(CLAIM, COUNTER)
    error = caught.value
    assert error.__cause__ is None and error.__context__ is None
    shown = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    no_key(shown)
    assert "AuthenticationError" not in shown and "Incorrect" not in shown


def test_construction_errors_hold_no_exception_chain_and_no_sdk_text(team_key, monkeypatch):
    openai = pytest.importorskip("openai")

    def broken(**kwargs):
        raise openai.OpenAIError(f"cannot configure the client for {KEY}")

    monkeypatch.setattr(openai, "OpenAI", broken)
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    assert str(caught.value) == "openai:gpt-model@api.openai.com: the OpenAI SDK could not be set up (OpenAIError)"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    with pytest.raises(VerifierError) as caught:
        OpenAICompatVerifier("gpt-model", base_url="http://gateway.example.test/v1")
    assert caught.value.__cause__ is None and caught.value.__context__ is None


# --- the transport ---------------------------------------------------------------------------------

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


def test_sdk_deepseek_is_asked_for_a_json_object(mock_sdk, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    assert OpenAICompatVerifier("deepseek-model", "deepseek").judge(CLAIM, COUNTER).relation == "contradicts"
    [request] = mock_sdk.seen
    assert json.loads(request.content)["response_format"] == {"type": "json_object"}


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
    # Certificate verification stays the SDK's default: no verify argument, no CA handling of our own.
    assert mock_sdk.options == [{"follow_redirects": False, "trust_env": False}] * 2


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


# --- logs ------------------------------------------------------------------------------------------

def test_sdk_debug_logs_never_carry_the_key(mock_sdk, team_key, monkeypatch, caplog):
    """OPENAI_LOG=debug makes the SDK log the response's x-request-id header unfiltered; an endpoint that echoes
    the key there must not get it into the log."""
    from openai._utils._logs import setup_logging
    monkeypatch.setenv("OPENAI_LOG", "debug")
    sdk_logger = logging.getLogger("openai")
    monkeypatch.setattr(sdk_logger, "level", sdk_logger.level)  # setup_logging changes it; put it back afterwards
    setup_logging()
    caplog.set_level(logging.DEBUG)
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(
        200, json=completion(), headers={"x-request-id": f"req-{KEY}"})
    judge = OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    assert "request_id: req-[redacted]" in caplog.text
    no_key(caplog.text)


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
    assert record.getMessage() == "auth header Bearer [redacted] ([redacted])" and record.args is None
    no_key(caplog.text)


def test_exceptions_and_stacks_in_sdk_log_records_are_redacted(mock_sdk, team_key, caplog):
    caplog.set_level(logging.DEBUG)
    OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    logger = logging.getLogger("openai.round4")
    try:
        raise RuntimeError(f"request failed for {KEY}")
    except RuntimeError:
        logger.exception("plain message")
    stack = logger.makeRecord(logger.name, logging.WARNING, __file__, 1, "made by hand", None, None,
                              sinfo=f"Stack (most recent call last):\n  token={percent(KEY)}")
    logger.handle(stack)
    sdk_logger = logging.getLogger("openai._base_client")  # filtered since the verifier was built
    direct = logging.LogRecord(sdk_logger.name, logging.ERROR, __file__, 1, "direct %s", (KEY,),
                               (RuntimeError, RuntimeError(KEY), None), sinfo=f"stack {KEY}")
    sdk_logger.handle(direct)  # made without the record factory: the logger's filter redacts it
    records = [r for r in caplog.records if r.name in ("openai.round4", "openai._base_client")]
    assert [r.getMessage() for r in records] == ["plain message", "made by hand", "direct [redacted]"]
    assert records[0].exc_info is None and "request failed for [redacted]" in records[0].exc_text
    assert records[1].stack_info.endswith("token=[redacted]")
    assert records[2].exc_info is None and "RuntimeError: [redacted]" in records[2].exc_text
    assert records[2].stack_info == "stack [redacted]"
    no_key(caplog.text)


def test_the_record_factory_is_recognised_by_identity(mock_sdk, team_key, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    judge = OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    ours = logging.getLogRecordFactory()
    assert ours is openai_compat._FACTORY

    # A library that wraps it with functools.wraps (and calls it) keeps it in the chain: not installed twice.
    @functools.wraps(ours)
    def wrapping(*args, **kwargs):
        return ours(*args, **kwargs)

    monkeypatch.setattr(logging, "_logRecordFactory", wrapping)
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    assert logging.getLogRecordFactory() is wrapping

    # A replacement that only looks like it (a copied attribute, no __wrapped__ to ours) is wrapped again.
    def lookalike(*args, **kwargs):
        return logging.LogRecord(*args, **kwargs)

    lookalike.__dict__.update(ours.__dict__, _memfalsify_guard=True, __name__=ours.__name__)
    monkeypatch.setattr(logging, "_logRecordFactory", lookalike)
    assert judge.judge(CLAIM, COUNTER).relation == "contradicts"
    assert logging.getLogRecordFactory() is openai_compat._FACTORY is not ours
    logging.getLogger("httpcore2.after_replacement").warning("key %s", KEY)
    no_key(caplog.text)


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


def test_concurrent_requests_all_use_the_configured_format():
    client = fake_client(delay=lambda claim, passage: 0.01)
    judge = verifier(client, response_format="json_object")
    pairs = [(CLAIM, f"{COUNTER} {n}") for n in range(8)]
    assert [j.relation for j in judge_all(judge, pairs, max_concurrency=4)] == ["contradicts"] * 8
    assert [c["response_format"] for c in client.chat.completions.calls] == [{"type": "json_object"}] * 8


# --- CLI ------------------------------------------------------------------------------------------

def cli_with_fake(monkeypatch, client):
    """Make the CLI's OpenAICompatVerifier use ``client``; returns the arguments it was built with."""
    built = []
    real = openai_compat.OpenAICompatVerifier

    def make(model, provider, base_url, api_key_env, **kwargs):
        built.append((model, provider, base_url, api_key_env, kwargs.get("response_format")))
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
                                 "--base-url", "https://gateway.example.test/v1", "--api-key-env", "GATEWAY_KEY",
                                 "--openai-response-format", "json_object"])
    assert result.exit_code == 0, result.output
    assert built == [("grok-model", "grok", "https://gateway.example.test/v1", "GATEWAY_KEY", "json_object")]
    assert "Verifier: `openai:grok-model@gateway.example.test`" in result.stdout
    assert "POSSIBLE COUNTEREVIDENCE" in result.stdout
    assert {c["response_format"]["type"] for c in client.chat.completions.calls} == {"json_object"}
    assert seen[0]["max_concurrency"] == 4 and seen[0]["max_judged"] == 8 and seen[0]["min_coverage"] == 0.2


@pytest.mark.parametrize("extra, message", [
    ([], "--verifier openai needs --openai-model"),
    (["--openai-model", "m", "--batch"], "--batch is not supported with --verifier openai in this version"),
    (["--openai-model", "m", "--base-url", "http://api.example.test/v1"], "must use https"),
    (["--openai-model", "m", "--api-key-env", SECRET], "never the key itself"),
    (["--openai-model", "m", "--openai-response-format", "json"], "Invalid value for '--openai-response-format'"),
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
    assert result.output == ("error: openai:gpt-model@api.openai.com: HTTP 401 (type invalid_request_error): the key "
                             "in the environment variable you named was refused\n")


def test_cli_a_rejected_format_points_at_the_option(two_claims, mock_sdk, team_key):
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(
        400, json={"error": {"message": "response_format json_schema is not supported", "param": "response_format",
                             "type": "invalid_request_error"}})
    args = ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"), "--verifier", "openai",
            "--openai-model", "m", "--api-key-env", "TEAM_KEY"]
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert "try --openai-response-format json_object (or text)" in result.output
    assert len({json.loads(r.content)["response_format"]["type"] for r in mock_sdk.seen}) == 1


@pytest.mark.parametrize("fmt", ["markdown", "json", "html"])
def test_cli_reports_never_carry_the_key(two_claims, mock_sdk, monkeypatch, fmt):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"),
                                 "--verifier", "openai", "--openai-model", "gpt-model", "--format", fmt])
    assert result.exit_code == 0 and mock_sdk.seen
    assert SECRET not in result.output
    assert all(r.headers["authorization"] == f"Bearer {SECRET}" for r in mock_sdk.seen)


@pytest.mark.parametrize("body", [{"choices": 1}, {"choices": {"a": 1}}], ids=["number", "mapping"])
def test_cli_reports_malformed_completions_as_no_verdict(two_claims, mock_sdk, team_key, body):
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(200, json=body)
    args = ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"), "--verifier", "openai",
            "--openai-model", "gpt-model", "--api-key-env", "TEAM_KEY"]
    result = runner.invoke(app, [*args, "--format", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert [f["status"] for f in report["findings"]] == ["insufficient_evidence", "insufficient_evidence"]
    assert report["verifier_stats"][0]["requests"] == 4
    strict = runner.invoke(app, [*args, "--strict"])
    assert strict.exit_code == 0 and "Traceback" not in strict.output


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
                                 "--openai-model", "gemini-model", "--provider", "gemini",
                                 "--openai-response-format", "text", "--format", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert built == [("gemini-model", "gemini", None, None, "text")]
    assert report["audit"]["verifier"] == "openai:gemini-model@generativelanguage.googleapis.com"
    [call] = client.chat.completions.calls  # only the added claim was judged
    assert "response_format" not in call


# --- round 5: error tokens and once-encoded keys ------------------------------------------------------

@pytest.mark.parametrize("value", [SECRET, SECRET.upper(), SECRET.lower(), "sk-test-0123456789", "x0123456789abcdefx",
                                   "SECRETxyz-abcdef", "a" * 41])
def test_an_error_field_sharing_the_key_or_too_long_is_dropped(monkeypatch, value):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    judge = verifier(fake_client(error=status_error(429, type=value, code=value, param="messages")))
    with pytest.raises(VerifierError) as caught:
        judge.judge(CLAIM, COUNTER)
    assert str(caught.value) == ("openai:test-model@api.openai.com: HTTP 429 (param messages): rate limited or out of "
                                 "quota")


def test_short_error_fields_without_a_shared_run_are_kept(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    judge = verifier(fake_client(error=status_error(401, type="invalid_request_error", code="invalid_api_key",
                                                    param="sk-test")))  # 7 shared characters: kept
    with pytest.raises(VerifierError) as caught:
        judge.judge(CLAIM, COUNTER)
    assert str(caught.value) == ("openai:test-model@api.openai.com: HTTP 401 (type invalid_request_error, code "
                                 "invalid_api_key, param sk-test): the key in OPENAI_API_KEY was refused")


# Codex's probes: the key once-encoded in ways the literal redaction does not list.
PROBES = {
    "mixed-case percent": "sk%2btest%2F0123456789%3dabcdefSECRET",
    "fully percent-encoded": "".join(f"%{ord(ch):02X}" for ch in KEY),
    "unicode-escaped": "".join(f"\\u{ord(ch):04x}" for ch in KEY),
    "HTML references": "".join(f"&#{ord(ch)};" for ch in KEY),
}


def assert_hidden(text, key=KEY):
    """Neither the key nor any once-decoded reading of ``text`` shows it."""
    assert not openai_compat.shows_secret(text, key)
    for probe in PROBES.values():
        assert probe not in text


@pytest.mark.parametrize("probe", list(PROBES.values()), ids=list(PROBES))
@pytest.mark.parametrize("fmt", ["json", "markdown", "html"])
def test_once_encoded_keys_in_a_rationale_never_reach_a_report(two_claims, mock_sdk, team_key, probe, fmt):
    content = verdict("contradicts", f"the passage cites the token {probe} here")
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(200, json=completion(content))
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"),
                                 "--verifier", "openai", "--openai-model", "gpt-model", "--api-key-env", "TEAM_KEY",
                                 "--format", fmt])
    assert result.exit_code == 0, result.output
    assert_hidden(result.stdout)
    if fmt == "json":
        rationales = {e["rationale"] for f in json.loads(result.stdout)["findings"] for e in f["evidence"]}
        assert rationales == {"the passage cites the token [\u2026] here"}


@pytest.mark.parametrize("probe", list(PROBES.values()), ids=list(PROBES))
def test_once_encoded_keys_in_an_sdk_debug_log_line_are_withheld(mock_sdk, team_key, monkeypatch, caplog, probe):
    from openai._utils._logs import setup_logging
    monkeypatch.setenv("OPENAI_LOG", "debug")
    sdk_logger = logging.getLogger("openai")
    monkeypatch.setattr(sdk_logger, "level", sdk_logger.level)
    setup_logging()
    caplog.set_level(logging.DEBUG)
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(
        200, json=completion(), headers={"x-request-id": f"req-{probe}"})
    assert OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY").judge(CLAIM, COUNTER).relation == "contradicts"
    sdk_lines = [r.getMessage() for r in caplog.records if r.name == "openai._base_client"]
    assert openai_compat.LOG_WITHHELD in sdk_lines  # the request_id line (older SDKs log the headers too)
    assert not any(line.startswith("request_id") for line in sdk_lines)
    assert_hidden(caplog.text)


def test_once_encoded_keys_in_log_exceptions_and_stacks_are_withheld(mock_sdk, team_key, caplog):
    caplog.set_level(logging.DEBUG)
    OpenAICompatVerifier("gpt-model", api_key_env="TEAM_KEY")
    logger = logging.getLogger("openai.round5")
    try:
        raise RuntimeError(f"failed for {PROBES['mixed-case percent']}")
    except RuntimeError:
        logger.exception("plain message")
    logger.handle(logger.makeRecord(logger.name, logging.WARNING, __file__, 1, "with a stack", None, None,
                                    sinfo=f"Stack:\n  token={PROBES['HTML references']}"))
    records = [r for r in caplog.records if r.name == "openai.round5"]
    assert [r.getMessage() for r in records] == ["plain message", "with a stack"]
    assert records[0].exc_text == openai_compat.LOG_WITHHELD and records[1].stack_info == openai_compat.LOG_WITHHELD
    assert_hidden(caplog.text)


# --- every response field at once: the key reaches no output ------------------------------------------

DIGIT_KEY = "12345678901234567890"  # all digits: what a token count could carry


def key_forms(key):
    """The key as is and in every once-encoded form a provider could send it."""
    return {"literal": key,
            "percent": quote(key, safe=""),
            "mixed-case percent": "".join(f"%{ord(ch):02x}" if n % 2 else f"%{ord(ch):02X}"
                                          for n, ch in enumerate(key)),
            "unicode escapes": "".join(f"\\u{ord(ch):04x}" for ch in key),
            "HTML references": "".join(f"&#{ord(ch)};" for ch in key),
            "escaped slash": key.replace("/", "\\/")}


def assert_no_key(text, key):
    assert not openai_compat.shows_secret(text, key), text[:300]
    for name, form in key_forms(key).items():
        assert form not in text, name


def poisoned(key, index):
    """A response with the key, literally and once-encoded, in every field a provider controls. Requests rotate
    through a verdict (with the forms in its rationale), a non-stop finish_reason, a refusal and a non-verdict."""
    forms = list(key_forms(key).values())
    every = " ".join(forms)
    number = int(key) if key.isdigit() else every
    kind = index % 4
    content = {0: verdict("contradicts", f"the passage cites {every} here"), 1: verdict("supports", "-"),
               2: None, 3: every}[kind]
    first = {"index": 0, "finish_reason": forms[index % len(forms)] if kind == 1 else "stop",
             "message": {"role": "assistant", "content": content, "refusal": every if kind == 2 else None,
                         "tool_calls": [{"id": every, "type": "function",
                                         "function": {"name": every, "arguments": every}}],
                         "annotations": [{"type": every}], "audio": {"id": every, "transcript": every}},
             "logprobs": {"content": [{"token": every, "logprob": 0, "bytes": [], "top_logprobs": []}]}}
    body = {"id": every, "object": "chat.completion", "created": number if key.isdigit() else 0, "model": every,
            "system_fingerprint": every, "service_tier": every,
            "choices": [first, {"index": 1, "finish_reason": every, "message": {"content": every}}],
            "usage": {"prompt_tokens": number, "completion_tokens": number, "total_tokens": number,
                      "prompt_tokens_details": {"cached_tokens": number},
                      "completion_tokens_details": {"reasoning_tokens": number}}}
    headers = {"x-request-id": f"req-{forms[index % len(forms)]}", "openai-model": forms[0],
               "x-ratelimit-remaining-requests": forms[1], "openai-organization": forms[2]}
    return body, headers


def poisoned_error(key):
    every = " ".join(key_forms(key).values())
    return {"error": {"message": every, "type": key, "code": key_forms(key)["percent"], "param": key[:20],
                      "detail": every, "metadata": {"raw": every}}}


@pytest.fixture(params=[KEY, DIGIT_KEY], ids=["mixed", "digits"])
def poison(request, mock_sdk, monkeypatch, caplog):
    """The real SDK answering with ``poisoned`` responses, the key in TEAM_KEY, and the SDK's debug log on."""
    from openai._utils._logs import setup_logging
    key = request.param
    monkeypatch.setenv("TEAM_KEY", key)
    monkeypatch.setenv("OPENAI_LOG", "debug")
    sdk_logger = logging.getLogger("openai")
    monkeypatch.setattr(sdk_logger, "level", sdk_logger.level)
    setup_logging()
    caplog.set_level(logging.DEBUG)

    def handle(request):
        body, headers = poisoned(key, len(mock_sdk.seen) - 1)
        return mock_sdk.httpx2.Response(200, json=body, headers=headers)

    mock_sdk.state["handle"] = handle
    return SimpleNamespace(key=key, sdk=mock_sdk, caplog=caplog)


AUDIT = ["--verifier", "openai", "--openai-model", "gpt-model", "--api-key-env", "TEAM_KEY"]


@pytest.mark.parametrize("fmt", ["json", "markdown", "html"])
def test_the_key_in_every_response_field_reaches_no_report_diagnostic_or_log(two_claims, poison, fmt):
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"), *AUDIT,
                                 "--format", fmt])
    assert result.exit_code == 0, result.output
    assert len(poison.sdk.seen) == 4
    for text in (result.stdout, result.stderr, poison.caplog.text):
        assert_no_key(text, poison.key)
    if fmt == "json":
        report = json.loads(result.stdout)
        assert report["verifier_stats"][0]["input_tokens"] == report["verifier_stats"][0]["output_tokens"] == 0
        rationales = {e["rationale"] for f in report["findings"] for e in f["evidence"]}
        [cited] = [r for r in rationales if r.startswith("the passage cites ")]
        words = cited.removeprefix("the passage cites ").removesuffix(" here").split()
        assert len(words) == len(key_forms(poison.key)) and set(words) <= {"[redacted]", "[\u2026]"}


def test_the_key_in_every_error_field_reaches_no_diagnostic_or_log(two_claims, poison):
    poison.sdk.state["handle"] = lambda request: poison.sdk.httpx2.Response(
        401, json=poisoned_error(poison.key), headers=poisoned(poison.key, 0)[1])
    result = runner.invoke(app, ["audit", str(two_claims / "MEMORY.md"), "-e", str(two_claims / "evidence"), *AUDIT])
    assert result.exit_code == 2
    assert result.output == ("error: openai:gpt-model@api.openai.com: HTTP 401: the key in the environment variable "
                             "you named was refused\n")
    for text in (result.output, poison.caplog.text):
        assert_no_key(text, poison.key)


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="the MCP SDK is not installed")
def test_the_key_in_every_response_field_reaches_no_mcp_result(two_claims, poison):
    import anyio
    from mcp import Client

    from memcore.reading import confinement_supported
    from memlab_mcp.server import create_server
    if not confinement_supported():
        pytest.skip("the MCP server needs POSIX confined reads")
    server = create_server(two_claims, allow_model_verifiers=True, openai_model="gpt-model",
                           openai_api_key_env="TEAM_KEY")

    async def main():
        async with Client(server, mode="legacy") as client:
            return [await client.call_tool("memfalsify_audit", {"memory_file": "MEMORY.md", "evidence_dir": "evidence",
                                                                "verifier": "openai", "format": fmt})
                    for fmt in ("json", "markdown")]

    results = anyio.run(main)
    assert not any(r.is_error for r in results)
    for result in results:
        assert_no_key(result.content[0].text, poison.key)
    assert_no_key(poison.caplog.text, poison.key)


def test_the_key_in_every_response_field_reaches_no_action_output(two_claims, poison, monkeypatch, capsys, tmp_path):
    spec = importlib.util.spec_from_file_location("memfalsify_action_poison",
                                                  Path(__file__).resolve().parent.parent / "action" /
                                                  "memfalsify_action.py")
    action = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(action)
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()
    env = {"RUNNER_TEMP": str(runner_dir), "GITHUB_STEP_SUMMARY": str(runner_dir / "summary.md"),
           "GITHUB_OUTPUT": str(runner_dir / "output"), "MEMFALSIFY_MEMORY": "MEMORY.md",
           "MEMFALSIFY_EVIDENCE": "evidence", "MEMFALSIFY_STRICT": "false", "MEMFALSIFY_VERIFIER": "openai",
           "MEMFALSIFY_OPENAI_MODEL": "gpt-model", "MEMFALSIFY_API_KEY_ENV": "TEAM_KEY"}
    for name in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT"):
        Path(env[name]).write_text("", encoding="utf-8")
    monkeypatch.chdir(two_claims)
    assert action.main(env) == 0
    outputs = Path(env["GITHUB_OUTPUT"]).read_text(encoding="utf-8")
    report = Path(dict(line.split("=", 1) for line in outputs.splitlines())["report"]).read_text(encoding="utf-8")
    texts = [Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8"), outputs, report, capsys.readouterr().out,
             poison.caplog.text] + [p.read_text(encoding="utf-8") for p in runner_dir.rglob("*.json")]
    for text in texts:
        assert_no_key(text, poison.key)


@pytest.mark.parametrize("fmt", ["json", "markdown", "html"])
def test_a_token_count_holding_an_all_digit_key_is_not_reported(invoices, mock_sdk, monkeypatch, fmt):
    """One request, so the report's totals would be the count itself."""
    monkeypatch.setenv("TEAM_KEY", DIGIT_KEY)
    body, headers = poisoned(DIGIT_KEY, 0)
    mock_sdk.state["handle"] = lambda request: mock_sdk.httpx2.Response(200, json=body, headers=headers)
    result = runner.invoke(app, ["audit", str(invoices / "MEMORY.md"), "-e", str(invoices / "evidence"), *AUDIT,
                                 "--format", fmt])
    assert result.exit_code == 0, result.output
    assert len(mock_sdk.seen) == 1
    assert_no_key(result.output, DIGIT_KEY)
