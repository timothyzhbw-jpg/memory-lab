"""OpenAI-compatible verifier: a model behind any Chat Completions API reads the passage and explains its judgment.

One backend serves OpenAI and every provider with an OpenAI-compatible endpoint. ``PRESETS`` fills in the base
URL, the name of the key's environment variable and the response format for OpenAI, xAI Grok, DeepSeek, Google
Gemini and a local Ollama server; an explicit base URL, key variable or response format overrides the preset.
There is no default model, because model names differ between providers and change over time.

Install with ``pip install 'memfalsify[openai]'``. Memory claims and evidence passages are sent to the
configured endpoint.

Threat model: the key must stay out of everything the provider or the model controls (error bodies, the answer,
response headers in the SDK's debug log), and out of reports and diagnostics. The promise: the literal key and its
once-encoded forms (``decoded_once``: percent, ``\\uXXXX``/``\\xXX``/``\\/`` escapes, HTML references) never
appear. A provider that hides the key it received under deeper encodings is out of scope: it already holds the
key. Other code running in the same Python process is trusted: it can read the environment, and a library that
puts the key into log records or replaces the logging record factory is out of scope.

Transport and secrets:

- The base URL must use https; plain http is accepted only for localhost, 127.0.0.1 and ::1. A base URL with a
  user name, password, query or fragment is refused, and error messages never repeat it.
- Requests go only there: the HTTP client follows no redirect (a 3xx is an error) and ignores proxy settings
  from the environment (``trust_env=False``). Certificates are verified the way the SDK and httpx2 ship it:
  through the platform's trust store (``truststore``); on Linux, OpenSSL honours ``SSL_CERT_FILE`` and
  ``SSL_CERT_DIR``, which are the user's own configuration.
- The key is read from the environment variable the caller names, never taken as an argument, and must be at
  least ``MIN_KEY_LENGTH`` characters of those keys use (``KEY_CHARS``). The provider's free text is never
  shown: an error is reported as its HTTP status, the error's type, code and param when each is a plain token
  (``TOKEN``) that shares no run of ``SHARED_RUN`` characters with the key, and a fixed hint for the status.
  The model's rationale is the only free text kept: the key in it is redacted (``secret_forms``), a run that
  shows part of the key once decoded is elided (``elide_secret_runs``), and the rationale is withheld if it
  still shows the key once decoded (``shows_secret``). Token counts above ``MAX_TOKENS_PER_REQUEST`` count as
  missing. No ``VerifierError`` keeps the SDK's exception as its cause or context, and the log records of the
  SDK and the HTTP stack (``OPENAI_LOG=debug``) are cleaned as they are made (``guard_logs``).
- Only the arguments configure the client. The SDK would otherwise read ``OPENAI_BASE_URL``,
  ``OPENAI_ORG_ID``, ``OPENAI_PROJECT_ID`` and ``OPENAI_CUSTOM_HEADERS`` from the environment and send what they
  hold to whatever endpoint is configured; ``isolated_headers`` removes those headers from every request.

What of a provider's response can reach output (reports in every format, stderr diagnostics, MCP results, the
Action's summary, log and outputs, SDK log records), and how:

=============================================================  =================================================
Response field                                                 Treatment
=============================================================  =================================================
choices[0].message.content: the verdict's ``relation``         fixed text: one of ``RELATION_CRITERIA``
choices[0].message.content: the verdict's ``rationale``        rationale-filtered (``OpenAICompatVerifier._rationale``)
choices[0].message.content when it is not a verdict            dropped: fixed no-verdict reason
choices[0].message.refusal                                     dropped: fixed "the model refused"
choices[0].finish_reason                                       dropped: compared with "stop"; fixed reason
choices[0] tool_calls, function_call, audio, annotations,      dropped: never read
logprobs, index; choices[1:]
usage.prompt_tokens, usage.completion_tokens                   numeric-capped: whole numbers up to
                                                               ``MAX_TOKENS_PER_REQUEST``, else missing
usage.total_tokens and the *_tokens_details                    dropped: never read
id, object, created, model, system_fingerprint, service_tier   dropped: never read (the verifier name uses the
                                                               configured model)
HTTP status                                                    numeric: the transport's status code
error.type, error.code, error.param                            token-filtered (``TOKEN``, ``shares_run``)
error.message, error.detail, any other error field, a non-     dropped: fixed hint per status
JSON error body
Location of a 3xx answer                                       dropped: fixed redirect text
connection, timeout and SDK exceptions                         dropped: fixed text per exception class
response headers (x-request-id, ...) in SDK debug logs         log-filtered: redacted, withheld if they still
and anything else an SDK or HTTP log record holds              show the key once decoded (``redact_record``)
=============================================================  =================================================

Structured output: requests ask for the verdict in the configured ``response_format`` (``FORMATS``): a strict
JSON schema, any JSON object, or nothing (the instructions ask for the same JSON object every time). There is no
automatic fallback: an endpoint that rejects the format answers HTTP 400 or 422, and the error suggests a
simpler format. Only an answer that ended normally (``finish_reason`` ``stop``) is read. An answer that did not,
a malformed response, one that is not a JSON verdict (invalid JSON, a repeated or unknown key, a relation
outside the schema), an empty answer and a refusal are all recorded as no verdict (``unrelated``, with a fixed
reason as the rationale), never as support.
"""
from __future__ import annotations

import html
import logging
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from pydantic import ConfigDict

from memcore.jsonutil import loads

from .gitstore import printable
from .llm import SYSTEM, Verdict
from .verify import RELATION_CRITERIA, Judgment, UsageMeter, VerifierError, VerifierStats


class Provider(str, Enum):
    """Presets for the base URL, the key's environment variable and the response format."""
    openai = "openai"
    grok = "grok"
    deepseek = "deepseek"
    gemini = "gemini"
    ollama = "ollama"


class ResponseFormat(str, Enum):
    """What a request asks the model to answer in: a strict JSON schema, any JSON object, or plain text."""
    json_schema = "json_schema"
    json_object = "json_object"
    text = "text"


FORMATS = tuple(f.value for f in ResponseFormat)


@dataclass(frozen=True)
class Preset:
    base_url: str
    api_key_env: str | None  # None: the endpoint needs no key
    response_format: str = ResponseFormat.json_schema.value


PRESETS: dict[Provider, Preset] = {
    Provider.openai: Preset("https://api.openai.com/v1", "OPENAI_API_KEY"),
    Provider.grok: Preset("https://api.x.ai/v1", "XAI_API_KEY"),
    # DeepSeek documents response_format type "text" or "json_object" only:
    # https://api-docs.deepseek.com/api/create-chat-completion (and https://api-docs.deepseek.com/guides/json_mode)
    Provider.deepseek: Preset("https://api.deepseek.com", "DEEPSEEK_API_KEY", ResponseFormat.json_object.value),
    # https://ai.google.dev/gemini-api/docs/openai
    Provider.gemini: Preset("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
    Provider.ollama: Preset("http://localhost:11434/v1", None),
}
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Sent as the bearer token when the endpoint takes no key (Ollama ignores it; the SDK requires one).
NO_KEY = "unused"
# Headers the SDK fills from OPENAI_ORG_ID and OPENAI_PROJECT_ID.
SDK_ENV_HEADERS = ("OpenAI-Organization", "OpenAI-Project")
REDACTED = "[redacted]"
# The characters real API keys use (OpenAI, xAI, DeepSeek, Gemini, base64 and JWT tokens). A key with others
# (a backslash or a quote, which a JSON or Python repr would escape) is refused.
KEY_CHARS = re.compile(r"[A-Za-z0-9._~+/=-]+")
# Real keys are longer; with this floor, no accepted key fits under MAX_TOKENS_PER_REQUEST as a number.
MIN_KEY_LENGTH = 16
# A token count above this, for one request, is not a count: it counts as missing (an all-digit key would
# otherwise reach the report as a number).
MAX_TOKENS_PER_REQUEST = 10_000_000
# An error's type, code or param is shown only when it is a plain token like this, and shares no run of
# ``SHARED_RUN`` characters with the key (case-insensitively); the message never is.
TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,40}")
SHARED_RUN = 8
# Retries the SDK makes after a connection error, 408, 409, 429 or 5xx (its default).
MAX_RETRIES = 2
# The only finish_reason whose answer is used: the normal end of an answer for OpenAI, DeepSeek ("the model hit
# a natural stop point", https://api-docs.deepseek.com/api/create-chat-completion), xAI (the examples in
# https://docs.x.ai/developers/rest-api-reference/inference/chat-completions) and Ollama (openai/openai.go
# passes its done_reason, "stop" at a natural end). Google's OpenAI-compatibility page shows no response, so
# Gemini is assumed to follow OpenAI. Any other value, and none at all, is no verdict, even when the text parses.
FINISHED = "stop"
WITHHELD_RATIONALE = "the rationale was withheld because it contained the API key"
LOG_WITHHELD = "[withheld: contained the API key]"
# In a rationale, a run of these characters (those of a key, and those its once-encoded forms add) that shows
# part of the key once decoded is elided; every other text, any script and any length, is kept.
CANDIDATE_RUN = re.compile(r"[A-Za-z0-9._~+/=%&#;\\-]{8,}")
ELIDED = "[\u2026]"
_ESCAPED = re.compile(r"\\(?:u([0-9A-Fa-f]{4})|x([0-9A-Fa-f]{2})|(/))")


@dataclass(frozen=True)
class Endpoint:
    """Where requests go, and the name of the environment variable that holds the key (None: no key)."""
    provider: Provider
    base_url: str
    api_key_env: str | None
    preset_key: bool = True  # api_key_env is the provider's default rather than a name the caller passed
    preset_url: bool = True  # base_url is the provider's default rather than one the caller passed

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).netloc

    @property
    def response_format(self) -> str:
        """The default format: the preset's for the preset's URL, the strict JSON schema for any other URL."""
        return PRESETS[self.provider].response_format if self.preset_url else ResponseFormat.json_schema.value


@dataclass(frozen=True)
class OpenAISettings:
    """The options of ``--verifier openai`` as given: model, provider preset, and the optional overrides."""
    model: str | None = None
    provider: str = Provider.openai.value
    base_url: str | None = None
    api_key_env: str | None = None
    response_format: str | None = None

    def endpoint(self) -> Endpoint:
        """The resolved endpoint; ``ValueError`` for an unknown provider, a refused base URL or key name, or an
        unknown response format."""
        if self.response_format is not None:
            check_response_format(self.response_format)
        return resolve_endpoint(self.provider, self.base_url, self.api_key_env)


def check_base_url(url: str) -> str:
    """``url`` if requests may go there: https, or http to a loopback host; no credentials, query or fragment.

    Raises ``ValueError`` with a message that does not repeat the URL, which could hold a secret."""
    if not url or any(ch.isspace() or not ch.isprintable() for ch in url):
        raise ValueError("the base URL is empty or contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018  (raises ValueError for a port that is not a number in range)
    except ValueError:
        raise ValueError("the base URL is not a valid URL") from None
    if parts.scheme not in ("https", "http") or not host:
        raise ValueError("the base URL must be an https:// URL with a host "
                         "(http:// only for localhost, 127.0.0.1 or ::1)")
    if "@" in parts.netloc or parts.query or parts.fragment or "?" in url or "#" in url:
        raise ValueError("the base URL must not contain a user name, password, query or fragment; "
                         "the key comes from the environment variable you name")
    if parts.scheme == "http" and host not in LOOPBACK:
        raise ValueError("the base URL must use https; plain http is allowed only for localhost, 127.0.0.1 or ::1")
    return url


def check_key_env(name: str) -> str:
    """``name`` if it can be an environment variable's name. The message never repeats it: a caller who passed
    the key itself by mistake must not see it echoed."""
    if not ENV_NAME.fullmatch(name):
        raise ValueError("the API key option takes the name of an environment variable (letters, digits and "
                         "underscores), never the key itself")
    return name


def check_model(model: str | None) -> str:
    if not model:
        raise ValueError("--verifier openai needs --openai-model: model names differ between providers, "
                         "so there is no default")
    if any(ch.isspace() or not ch.isprintable() for ch in model):
        raise ValueError("the model name must not contain whitespace or control characters")
    return model


def check_response_format(value: str) -> str:
    if value not in FORMATS:
        raise ValueError("the response format must be one of: " + ", ".join(FORMATS))
    return value


def resolve_endpoint(provider: str = Provider.openai.value, base_url: str | None = None,
                     api_key_env: str | None = None) -> Endpoint:
    """The provider's preset, with ``base_url`` and ``api_key_env`` overriding it when given."""
    try:
        chosen = Provider(provider)
    except ValueError:
        raise ValueError("unknown provider; choose one of: " + ", ".join(p.value for p in Provider)) from None
    preset = PRESETS[chosen]
    url = check_base_url(preset.base_url if base_url is None else base_url)
    key_env = preset.api_key_env if api_key_env is None else check_key_env(api_key_env)
    return Endpoint(chosen, url, key_env, preset_key=api_key_env is None, preset_url=base_url is None)


def secret_forms(secret: str) -> list[str]:
    """The key as is, percent-encoded once (``%2B``, ``%2F``, ``%3D``, in either case) and JSON-escaped once
    (``\\/``). Keys hold only ``KEY_CHARS``, so a JSON string or Python repr cannot escape them otherwise."""
    encoded = quote(secret, safe="")
    forms = [secret, encoded, re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), encoded),
             secret.replace("/", "\\/")]
    return list(dict.fromkeys(form for form in forms if form))


def redact(text: str, *secrets: str | None) -> str:
    """``text`` with each secret, in each of its ``secret_forms``, replaced by ``[redacted]``; longest first."""
    forms = sorted({form for secret in secrets if secret for form in secret_forms(secret)}, key=len, reverse=True)
    for form in forms:
        text = text.replace(form, REDACTED)
    return text


def _unescape_once(text: str) -> str:
    """JSON-style escapes undone once: ``\\uXXXX``, ``\\xXX`` and ``\\/``."""
    def one(match: re.Match[str]) -> str:
        code = match.group(1) or match.group(2)
        return chr(int(code, 16)) if code else "/"

    return _ESCAPED.sub(one, text)


def decoded_once(text: str) -> list[str]:
    """``text`` and how it reads with one layer of encoding undone: percent-decoded (``unquote``, escapes in
    any case), JSON-style unescaped, HTML-unescaped, and the three applied once in sequence."""
    percent, escaped, entities = unquote(text, errors="replace"), _unescape_once(text), html.unescape(text)
    return [text, percent, escaped, entities, html.unescape(_unescape_once(percent))]


def shows_secret(text: str, *secrets: str | None) -> bool:
    """Whether ``text``, as is or decoded once (``decoded_once``), contains a secret (case-sensitively)."""
    views = decoded_once(text)
    return any(secret in view for secret in secrets if secret for view in views)


def elide_secret_runs(text: str, secret: str | None) -> str:
    """``text`` with each ``CANDIDATE_RUN`` that shares ``SHARED_RUN`` characters with ``secret`` in one of its
    ``decoded_once`` views (case-insensitively) replaced by ``[…]``; nothing else changes."""
    if not secret:
        return text

    def one(match: re.Match[str]) -> str:
        run = match.group(0)
        return ELIDED if any(shares_run(view, secret) for view in decoded_once(run)) else run

    return CANDIDATE_RUN.sub(one, text)


def shares_run(value: str, secret: str | None, run: int = SHARED_RUN) -> bool:
    """Whether ``value``, as is or percent-decoded once, shares ``run`` or more consecutive characters with
    ``secret``, compared case-insensitively."""
    if not secret:
        return False
    pieces = {secret.lower()[i:i + run] for i in range(len(secret) - run + 1)} if len(secret) >= run \
        else {secret.lower()}
    return any(piece in view.lower() for view in (value, unquote(value, errors="replace")) for piece in pieces)


def _clean_log_text(text: str, secrets: list[str]) -> str:
    """Log text with the key redacted, or withheld whole if it still shows the key once decoded."""
    cleaned = redact(text, *secrets)
    return LOG_WITHHELD if shows_secret(cleaned, *secrets) else cleaned


def redact_record(record: logging.LogRecord, secrets: list[str]) -> None:
    """Keep every secret out of ``record``: the message (with its arguments interpolated), the exception and the
    stack each have the key redacted (``secret_forms``), and are withheld whole if they still show it once
    decoded (``shows_secret``). An exception is rendered to ``exc_text`` and ``exc_info`` dropped, so no
    handler formats the live exception, whose attributes (an SDK error's body) the rendered text does not show."""
    if not secrets:
        return
    try:
        message = record.getMessage()
    except Exception:  # a malformed record: clean what it would print at best
        message = f"{record.msg} {record.args}"
    cleaned = _clean_log_text(message, secrets)
    if cleaned != message:
        record.msg, record.args = cleaned, None
    if record.exc_info:
        try:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        except Exception:
            record.exc_text = "[exception could not be rendered]"
        record.exc_info = None
    if record.exc_text:
        record.exc_text = _clean_log_text(record.exc_text, secrets)
    if record.stack_info:
        record.stack_info = _clean_log_text(record.stack_info, secrets)


class _KeyFilter(logging.Filter):
    """The registered keys, and their redaction from log records of the SDK and its HTTP stack.

    With ``OPENAI_LOG=debug`` the SDK logs, among other things, a response's ``x-request-id`` header as the
    endpoint sent it; an endpoint that echoes the key there would put it in the log."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._secrets: list[str] = []

    def add(self, secret: str) -> None:
        with self._lock:
            if secret not in self._secrets:
                self._secrets.append(secret)

    def secrets(self) -> list[str]:
        with self._lock:
            return list(self._secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        redact_record(record, self.secrets())
        return True


_LOG_FILTER = _KeyFilter()
# Loggers of the SDK and the HTTP libraries under it (httpx2 and httpcore2 in openai 3.x).
LOGGER_ROOTS = ("openai", "httpx", "httpx2", "httpcore", "httpcore2")
# The record factory this module installed last; recognised by identity, never by an attribute a wrapper copies.
_FACTORY: Callable[..., logging.LogRecord] | None = None


def _installed(factory: Callable[..., Any] | None) -> bool:
    """Whether ``factory`` is our record factory, or wraps it (``functools.wraps`` sets ``__wrapped__``)."""
    seen = set()
    while factory is not None and id(factory) not in seen:
        if factory is _FACTORY:
            return True
        seen.add(id(factory))
        factory = getattr(factory, "__wrapped__", None)
    return False


def guard_logs(secret: str | None) -> None:
    """Redact ``secret`` from the log records of the SDK and the HTTP stack, for the rest of the process.

    Two layers: the log record factory (chained to whatever factory was set before) redacts every new record of
    a logger under ``LOGGER_ROOTS`` as it is made, which covers loggers created at any time; and a filter on
    those loggers redacts records made some other way when they are handled. Every request calls this again,
    which re-installs the factory when it is no longer in the chain."""
    global _FACTORY
    if secret:
        _LOG_FILTER.add(secret)
    current = logging.getLogRecordFactory()
    if not _installed(current):
        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = current(*args, **kwargs)
            if str(record.name).split(".", 1)[0] in LOGGER_ROOTS:
                redact_record(record, _LOG_FILTER.secrets())
            return record

        _FACTORY = factory
        logging.setLogRecordFactory(factory)
    names = list(logging.Logger.manager.loggerDict)
    for name in [*LOGGER_ROOTS, *names]:
        if name.split(".", 1)[0] in LOGGER_ROOTS:
            logger = logging.getLogger(name)
            if _LOG_FILTER not in logger.filters:
                logger.addFilter(_LOG_FILTER)


def isolated_headers(api_key: str, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """``default_headers`` that carry ``api_key`` and omit every header the SDK takes from the environment.

    The SDK adds ``OpenAI-Organization`` and ``OpenAI-Project`` from ``OPENAI_ORG_ID`` and ``OPENAI_PROJECT_ID``,
    and each ``Name: value`` line of ``OPENAI_CUSTOM_HEADERS``; an ``Authorization`` line there would replace the
    key. An ``Omit`` value removes a header (the names are those the SDK parses, in any case), and the explicit
    ``Authorization`` comes last, so it is the one sent."""
    from openai import Omit

    environ = os.environ if environ is None else environ
    names = list(SDK_ENV_HEADERS)
    for line in environ.get("OPENAI_CUSTOM_HEADERS", "").split("\n"):
        if ":" in line:
            names.append(line[:line.find(":")].strip())
    headers: dict[str, Any] = {name: Omit() for name in names if name != "Authorization"}
    headers["Authorization"] = f"Bearer {api_key}"
    return headers


# Verdict's JSON schema in the form strict structured outputs accept: every property required, no others.
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relation": {"type": "string", "enum": list(RELATION_CRITERIA)},
        "rationale": {"type": "string", "description": Verdict.model_fields["rationale"].description},
    },
    "required": ["relation", "rationale"],
    "additionalProperties": False,
}
ANSWER = ('Reply with only a JSON object with two keys: "relation", which is "supports", "contradicts" or '
          '"unrelated", and "rationale", one sentence naming the words in the passage that decide the relation.')
INSTRUCTIONS = SYSTEM + "\n\n" + ANSWER
_FENCE = re.compile(r"\A```[A-Za-z]*[ \t]*\r?\n(.*?)\r?\n?```\Z", re.DOTALL)


class StrictVerdict(Verdict):
    """A verdict with exactly the schema's keys."""
    model_config = ConfigDict(extra="forbid")


def prompt(claim: str, passage: str) -> list[dict[str, str]]:
    """The messages for one judgment: the instructions, then the claim and the passage as quoted data."""
    return [{"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": f"<claim>\n{claim}\n</claim>\n\n<passage>\n{passage}\n</passage>"}]


def request_format(mode: str) -> dict[str, Any]:
    """Keyword arguments that ask for ``mode``: a strict JSON schema, any JSON object, or nothing (plain text)."""
    if mode == ResponseFormat.json_schema.value:
        return {"response_format": {"type": "json_schema",
                                    "json_schema": {"name": "verdict", "strict": True, "schema": SCHEMA}}}
    if mode == ResponseFormat.json_object.value:
        return {"response_format": {"type": "json_object"}}
    return {}


def parse_verdict(text: str) -> StrictVerdict | None:
    """The verdict in an answer, or None. One surrounding Markdown code fence is allowed, nothing else."""
    text = text.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return StrictVerdict.model_validate(loads(text))
    except (ValueError, RecursionError):  # invalid JSON, a repeated key, or not the schema (ValidationError)
        return None


def _get(obj: Any, name: str) -> Any:
    """An attribute of an SDK object, or a key of a mapping (the SDK leaves malformed parts as plain data)."""
    return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)


def _count(value: Any) -> int | None:
    """A token count, or None: anything but a whole number from 0 to ``MAX_TOKENS_PER_REQUEST`` is not one."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_TOKENS_PER_REQUEST:
        return value
    return None


def _usage(usage: Any) -> Any:
    """Chat Completions token counts under the names ``UsageMeter`` reads; a count that is not a whole number
    counts as missing. ``prompt_tokens`` includes any tokens the provider served from its own cache; the cache
    counters stay null for this backend."""
    if usage is None:
        return None
    return SimpleNamespace(input_tokens=_count(_get(usage, "prompt_tokens")),
                           output_tokens=_count(_get(usage, "completion_tokens")))


def _token(value: Any, secret: str | None) -> str | None:
    """An error field as a plain token (``TOKEN``) that shares no run with the key (``shares_run``), or None:
    anything else is dropped whole, never shown in part."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not TOKEN.fullmatch(value) or shares_run(value, secret):
        return None
    return value


class OpenAICompatVerifier:
    """Ask a model behind an OpenAI-compatible Chat Completions API for a verdict on one claim/passage pair."""

    def __init__(self, model: str, provider: str = Provider.openai.value, base_url: str | None = None,
                 api_key_env: str | None = None, client: Any = None, *,
                 response_format: str | None = None) -> None:
        """``provider`` picks the preset base URL, key variable and response format (``PRESETS``); ``base_url``,
        ``api_key_env`` (the variable's name, never the key) and ``response_format`` override it. A custom
        ``base_url`` defaults to the strict JSON schema."""
        problem = None
        try:
            self.model = check_model(model)
            self.endpoint = resolve_endpoint(provider, base_url, api_key_env)
            self.response_format = check_response_format(response_format or self.endpoint.response_format)
        except ValueError as exc:
            problem = str(exc)
        if problem is not None:
            raise VerifierError(problem)
        self.name = f"openai:{model}@{self.endpoint.host}"
        try:
            import openai
        except ImportError:
            openai = None
        if openai is None and client is None:
            raise VerifierError("the OpenAI-compatible verifier needs the openai package: "
                                "pip install 'memfalsify[openai]'")
        self._sdk = openai
        self._api_error: type[Exception] | tuple = openai.OpenAIError if openai is not None else ()
        self._secret = self._key(required=client is None)
        if openai is not None:
            guard_logs(self._secret)
        if client is None:
            client = self._client()
        self.client = client
        self.meter = UsageMeter(self.name)

    def _client(self) -> Any:
        """The SDK client: our own HTTP client, which follows no redirect (a 307 or 308 would resend the claim,
        the evidence and the key to wherever the endpoint points) and ignores proxy settings from the
        environment (``trust_env=False``), so requests go only to the configured base URL. Certificate
        verification is the SDK's default."""
        openai = self._sdk
        key = self._secret or NO_KEY
        failure = None
        try:
            http = openai.DefaultHttpxClient(follow_redirects=False, trust_env=False)
            return openai.OpenAI(api_key=key, base_url=self.endpoint.base_url,
                                 default_headers=isolated_headers(key), http_client=http, max_retries=MAX_RETRIES)
        except openai.OpenAIError as exc:
            failure = f"{self.name}: the OpenAI SDK could not be set up ({type(exc).__name__})"
        raise VerifierError(failure)

    def usage(self) -> list[VerifierStats]:
        return [self.meter.snapshot()]

    def judge(self, claim: str, passage: str) -> Judgment:
        guard_logs(None)  # loggers created since the last request, and a replaced record factory
        failure = None
        try:
            response = self.client.chat.completions.create(model=self.model, messages=prompt(claim, passage),
                                                           **request_format(self.response_format))
        except self._api_error as exc:
            # Described inside the except block and raised after it, so the VerifierError carries the SDK's
            # exception (and the body it holds) neither as its cause nor as its context.
            failure = f"{self.name}: {self._failure(exc)}"
        if failure is not None:
            raise VerifierError(failure)
        self.meter.record(_usage(getattr(response, "usage", None)))
        return self._judgment(response)

    def _failure(self, exc: Exception) -> str:
        """What went wrong, from the error's status and its type, code and param when they are plain tokens,
        with a fixed hint for the status. The provider's message is never shown."""
        status = getattr(exc, "status_code", None)
        if not isinstance(status, int):
            return self._without_response(exc)
        if 300 <= status < 400:
            return f"HTTP {status}: the endpoint answered with a redirect, which is not followed; check the base URL"
        body = getattr(exc, "body", None)
        fields = ({name: _token(body.get(name), self._secret) for name in ("type", "code", "param")}
                  if isinstance(body, Mapping) else {})
        shown = ", ".join(f"{name} {value}" for name, value in fields.items() if value is not None)
        return f"HTTP {status}" + (f" ({shown})" if shown else "") + f": {self._hint(status)}"

    def _hint(self, status: int) -> str:
        if status in (401, 403):
            name = self.endpoint.api_key_env
            if name is None:
                return "the endpoint refused the request, which carries no key; name one with --api-key-env"
            where = name if self.endpoint.preset_key else "the environment variable you named"
            return f"the key in {where} was refused"
        if status == 404:
            return "the model or the endpoint was not found; check --openai-model and --base-url"
        if status == 429:
            return "rate limited or out of quota"
        if status in (400, 422):
            hint = "the endpoint rejected the request"
            if self.response_format == ResponseFormat.json_schema.value:
                hint += (". If this endpoint does not support JSON schema output, try --openai-response-format "
                         "json_object (or text).")
            elif self.response_format == ResponseFormat.json_object.value:
                hint += ". If this endpoint does not support JSON object output, try --openai-response-format text."
            return hint
        if status >= 500:
            return "the provider had an error; try again"
        return "the provider returned an error"

    def _without_response(self, exc: Exception) -> str:
        """A failure without an HTTP status: the SDK's fixed description of its kind, never its message."""
        sdk = self._sdk
        if sdk is not None and isinstance(exc, sdk.APITimeoutError):
            return "the request timed out"
        if sdk is not None and isinstance(exc, sdk.APIConnectionError):
            return "could not connect to the endpoint"
        if sdk is not None and isinstance(exc, sdk.APIResponseValidationError):
            return "the response does not have the shape of a chat completion"
        return f"the request failed ({type(exc).__name__})"

    def _judgment(self, response: Any) -> Judgment:
        """A verdict from a well-formed, normally finished completion; no verdict, with a fixed reason, for
        anything else. Nothing from the envelope (finish_reason, refusal, ...) is echoed.

        The SDK does not validate responses, so a malformed envelope (choices that are not a list, a choice
        without a message, content or finish_reason of the wrong type) arrives as it was sent; it counts as no
        verdict, like a completion without choices or with an empty answer."""
        malformed = "the response is not a well-formed chat completion"
        if isinstance(response, (str, bytes, int, float, list)):
            return self._no_verdict(malformed)
        choices = _get(response, "choices")
        if choices is None or (isinstance(choices, list) and not choices):
            return self._no_verdict("the response has no answer")
        if not isinstance(choices, list):
            return self._no_verdict(malformed)
        choice = choices[0]
        message = _get(choice, "message")
        finish = _get(choice, "finish_reason")
        if isinstance(choice, (str, bytes, int, float, list)) or isinstance(message, (str, bytes, int, float, list)):
            return self._no_verdict(malformed)
        refusal, content = _get(message, "refusal"), _get(message, "content")
        if not all(value is None or isinstance(value, str) for value in (finish, refusal, content)):
            return self._no_verdict(malformed)
        if refusal:
            return self._no_verdict("the model refused")
        if finish != FINISHED:
            return self._no_verdict("the answer did not finish normally")
        if not content or not content.strip():
            return self._no_verdict("the answer is empty")
        verdict = parse_verdict(content)
        if verdict is None:
            return self._no_verdict("the answer is not a JSON verdict")
        return Judgment(relation=verdict.relation, rationale=self._rationale(verdict.rationale), verifier=self.name)

    def _rationale(self, text: str) -> str:
        """The model's rationale, the only free text kept: the key redacted (``secret_forms``), each run that
        shows part of the key once decoded elided (``elide_secret_runs``), and the whole rationale withheld if
        it still shows the key once decoded (``shows_secret``); then one inert line."""
        cleaned = elide_secret_runs(redact(text, self._secret), self._secret)
        if shows_secret(cleaned, self._secret):
            cleaned = WITHHELD_RATIONALE
        return printable(cleaned)

    def _no_verdict(self, why: str) -> Judgment:
        return Judgment(relation="unrelated", verifier=self.name, rationale=f"no verdict ({why})")

    def _key(self, required: bool) -> str | None:
        """The key from the named environment variable; None when the endpoint takes none."""
        name = self.endpoint.api_key_env
        if name is None:
            return None
        key = os.environ.get(name, "").strip()
        if not key:
            if not required:
                return None
            where = (f"{name} (the {self.endpoint.provider.value} default) is not set or is empty"
                     if self.endpoint.preset_key else "the environment variable you named is not set or is empty")
            raise VerifierError(f"{self.name}: no API key: {where}; set it, or name another variable with "
                                "--api-key-env")
        if not KEY_CHARS.fullmatch(key):
            raise VerifierError(f"{self.name}: the API key holds characters API keys do not use; only letters, "
                                "digits and . _ ~ + / = - are accepted")
        if len(key) < MIN_KEY_LENGTH:
            raise VerifierError(f"{self.name}: the API key is shorter than {MIN_KEY_LENGTH} characters, which no "
                                "real key is")
        return key
