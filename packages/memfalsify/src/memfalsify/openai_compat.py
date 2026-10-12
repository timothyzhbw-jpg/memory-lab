"""OpenAI-compatible verifier: a model behind any Chat Completions API reads the passage and explains its judgment.

One backend serves OpenAI and every provider with an OpenAI-compatible endpoint. ``PRESETS`` fills in the base
URL and the name of the key's environment variable for OpenAI, xAI Grok, DeepSeek, Google Gemini and a local
Ollama server; an explicit base URL or key variable overrides the preset. There is no default model, because
model names differ between providers and change over time.

Install with ``pip install 'memfalsify[openai]'``. Memory claims and evidence passages are sent to the
configured endpoint.

Transport and secrets:

- The base URL must use https; plain http is accepted only for localhost, 127.0.0.1 and ::1. A base URL with a
  user name, password, query or fragment is refused, and error messages never repeat it.
- Requests go only there: the HTTP client follows no redirect (a 3xx is an error), ignores proxy settings from
  the environment (``HTTP_PROXY``, ``HTTPS_PROXY``, ``ALL_PROXY``, ...) and verifies certificates with
  ``tls_context``, which never reads ``SSL_CERT_FILE`` or ``SSL_CERT_DIR``.
- The key is read from the environment variable the caller names, never taken as an argument, and must consist
  of the characters keys use (``KEY_CHARS``). It is never shown: error messages are built from the fields of the
  provider's error (status, type, code, param, message), and a field that holds the key in any encoding
  (``holds_secret``: percent-escaped in either case, backslash-escaped, HTML references, or a mix) is withheld
  whole; no ``VerifierError`` keeps the SDK's exception as its cause or context; and the log records of the SDK
  and the HTTP stack (``OPENAI_LOG=debug``) are sanitized as they are made (``guard_logs``).
- Only the arguments configure the client. The SDK would otherwise read ``OPENAI_BASE_URL``,
  ``OPENAI_ORG_ID``, ``OPENAI_PROJECT_ID`` and ``OPENAI_CUSTOM_HEADERS`` from the environment and send what they
  hold to whatever endpoint is configured; ``isolated_headers`` removes those headers from every request.

Structured output: a request asks for a JSON verdict with ``response_format`` ``json_schema`` (strict). An
endpoint that answers HTTP 400 or 422 with an error about the format (``names_format``: by its param, else by a
code naming the format, else by its message, never by a model that happens to be named that way) is asked again
with ``json_object``, then with no ``response_format`` at all; the instructions ask for the same JSON object
every time. Any other error ends the audit at once. The first request settles the format while concurrent ones
wait, so each refused format costs one extra request per run. Only an answer that ended normally
(``finish_reason`` ``stop``) is read. An answer that did not, a malformed response, one that is not a JSON
verdict (invalid JSON, a repeated or unknown key, a relation outside the schema), an empty answer and a refusal
are all recorded as no verdict (``unrelated``, with the reason in the rationale), never as support.
"""
from __future__ import annotations

import html
import logging
import os
import re
import ssl
import sys
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
    """Presets for the base URL and the key's environment variable."""
    openai = "openai"
    grok = "grok"
    deepseek = "deepseek"
    gemini = "gemini"
    ollama = "ollama"


@dataclass(frozen=True)
class Preset:
    base_url: str
    api_key_env: str | None  # None: the endpoint needs no key


PRESETS: dict[Provider, Preset] = {
    Provider.openai: Preset("https://api.openai.com/v1", "OPENAI_API_KEY"),
    Provider.grok: Preset("https://api.x.ai/v1", "XAI_API_KEY"),
    Provider.deepseek: Preset("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
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
# Error text is searched for runs of this many characters of the key.
REDACT_RUN = 8
REDACTED = "[redacted]"
# The characters real API keys use (OpenAI, xAI, DeepSeek, Gemini, base64 and JWT tokens). A key with others
# (a backslash or a quote, which a JSON or Python repr would escape) is refused, so redaction can find it.
KEY_CHARS = re.compile(r"[A-Za-z0-9._~+/=-]+")
# Retries the SDK makes after a connection error, 408, 409, 429 or 5xx (its default).
MAX_RETRIES = 2
# The only finish_reason whose answer is used: the normal end of an answer for OpenAI, DeepSeek ("the model hit
# a natural stop point", https://api-docs.deepseek.com/api/create-chat-completion), xAI (the examples in
# https://docs.x.ai/developers/rest-api-reference/inference/chat-completions) and Ollama (openai/openai.go
# passes its done_reason, "stop" at a natural end). Google's OpenAI-compatibility page shows no response, so
# Gemini is assumed to follow OpenAI. length, content_filter, tool_calls, insufficient_system_resource, any
# other value and none at all are no verdict, even when the text parses.
FINISHED = "stop"
# A 400 or 422 refuses the format, not the request, when its error names the format: by ``param``
# (response_format or response_format.<field>) when there is one; else by a ``code`` naming the format (a code
# naming anything else, such as model_not_found, settles it the other way; generic codes say nothing); else by
# the message, where the words must stand alone, not inside a quoted model name such as 'json_schema-test'.
_FORMAT_ERROR = re.compile(r"response_format|json_schema|json_object", re.IGNORECASE)
_FORMAT_WORD = re.compile(r"(?<![\w-])(?:response_format|json_schema|json_object)(?![\w-])", re.IGNORECASE)
_QUOTED_MODEL = re.compile(r"\bmodels?\s*:?\s*(?:'[^']*'|\"[^\"]*\"|`[^`]*`)", re.IGNORECASE)
_GENERIC_CODE = re.compile(r"(?:invalid|bad|unsupported)_(?:request|parameter|params?|value|argument)s?(?:_error)?"
                           r"|\d+", re.IGNORECASE)
# What replaces provider text, or a log record's text, that contains the key in any encoding.
WITHHELD = "the provider's message was withheld because it contained the API key"
LOG_WITHHELD = "[log text withheld because it contained the API key]"
_ESCAPE = re.compile(r"\\(?:u([0-9A-Fa-f]{4})|U([0-9A-Fa-f]{8})|x([0-9A-Fa-f]{2})|([^\w\s]))")
# Where OpenSSL builds look for CA certificates when the compiled-in defaults hold none (truststore's list).
CA_FILES = ("/etc/ssl/cert.pem", "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", "/etc/pki/tls/cert.pem",
            "/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/ca-bundle.pem")


@dataclass(frozen=True)
class Endpoint:
    """Where requests go, and the name of the environment variable that holds the key (None: no key)."""
    provider: Provider
    base_url: str
    api_key_env: str | None
    preset_key: bool = True  # api_key_env is the provider's default rather than a name the caller passed

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).netloc


@dataclass(frozen=True)
class OpenAISettings:
    """The options of ``--verifier openai`` as given: model, provider preset, and the optional overrides."""
    model: str | None = None
    provider: str = Provider.openai.value
    base_url: str | None = None
    api_key_env: str | None = None

    def endpoint(self) -> Endpoint:
        """The resolved endpoint; ``ValueError`` for an unknown provider, a refused base URL or key name."""
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


def resolve_endpoint(provider: str = Provider.openai.value, base_url: str | None = None,
                     api_key_env: str | None = None) -> Endpoint:
    """The provider's preset, with ``base_url`` and ``api_key_env`` overriding it when given."""
    try:
        chosen = Provider(provider)
    except ValueError:
        raise ValueError("unknown provider; choose one of: " + ", ".join(p.value for p in Provider)) from None
    preset = PRESETS[chosen]
    url = check_base_url(preset.base_url if base_url is None else base_url)
    if api_key_env is None:
        return Endpoint(chosen, url, preset.api_key_env)
    return Endpoint(chosen, url, check_key_env(api_key_env), preset_key=False)


def secret_forms(secret: str) -> list[str]:
    """How a key can appear in provider text: as is, URL-encoded (``+``, ``/`` and ``=`` as ``%2B``, ``%2F`` and
    ``%3D``), and JSON-escaped with ``\\/``. Keys hold only ``KEY_CHARS``, so a JSON or Python string repr
    cannot escape them any other way."""
    forms = [secret, quote(secret, safe=""), secret.replace("/", "\\/")]
    return list(dict.fromkeys(form for form in forms if form))


def redact(text: str, *secrets: str | None) -> str:
    """``text`` with each secret (in each of its ``secret_forms``), and every run of ``REDACT_RUN`` or more
    consecutive characters of one of those forms, replaced by ``[redacted]``.

    The run rule covers an error body that quotes part of a key, or the key cut at a line end. Whole forms of
    every secret go first, longest first, so a key that shares a run with another is still replaced whole.
    Encodings other than ``secret_forms`` are ``holds_secret``'s business."""
    forms = sorted({form for secret in secrets if secret for form in secret_forms(secret)}, key=len, reverse=True)
    for form in forms:
        text = text.replace(form, REDACTED)
    pieces = {form[i:i + REDACT_RUN] for form in forms for i in range(len(form) - REDACT_RUN + 1)}
    if not pieces:
        return text
    hidden = [False] * len(text)
    for i in range(len(text) - REDACT_RUN + 1):
        if text[i:i + REDACT_RUN] in pieces:
            hidden[i:i + REDACT_RUN] = [True] * REDACT_RUN
    out, i = [], 0
    while i < len(text):
        if hidden[i]:
            while i < len(text) and hidden[i]:
                i += 1
            out.append(REDACTED)
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _unescape(text: str) -> str:
    """Backslash escapes undone: ``\\uXXXX``, ``\\UXXXXXXXX``, ``\\xXX`` and an escaped punctuation character
    such as ``\\/``."""
    def one(match: re.Match[str]) -> str:
        code = match.group(1) or match.group(2) or match.group(3)
        if code is None:
            return match.group(4)
        value = int(code, 16)
        return chr(value) if value <= 0x10FFFF else match.group(0)

    return _ESCAPE.sub(one, text)


def decodings(text: str, rounds: int = 4) -> list[str]:
    """``text``, and what it reads as once the encodings a provider may apply to quoted text are undone, again
    and again: percent-escapes (``%2f`` or ``%2F``), backslash escapes (``\\u0073``, ``\\/``) and HTML character
    references. Each round undoes all three, so a key escaped in parts, or twice, comes out whole."""
    forms = [text]
    for _ in range(rounds):
        decoded = html.unescape(_unescape(unquote(forms[-1], errors="replace")))
        if decoded == forms[-1]:
            break
        forms.append(decoded)
    return forms


def holds_secret(text: str, *secrets: str | None) -> bool:
    """Whether ``text``, in any of its ``decodings``, contains a secret or a run of ``REDACT_RUN`` of its
    characters."""
    runs = set()
    for secret in secrets:
        if secret:
            runs |= ({secret} if len(secret) < REDACT_RUN else
                     {secret[i:i + REDACT_RUN] for i in range(len(secret) - REDACT_RUN + 1)})
    if not runs:
        return False
    return any(run in form for form in decodings(text) for run in runs)


def sanitize_record(record: logging.LogRecord, secrets: list[str]) -> None:
    """Withhold every part of ``record`` that holds a secret: the message (with its arguments), the exception
    and the stack. An exception is rendered to ``exc_text`` and ``exc_info`` dropped either way, so no handler
    formats the live exception, whose attributes (an SDK error's body) the text does not show."""
    if not secrets:
        return
    try:
        message = record.getMessage()
    except Exception:  # a malformed record: judge what it would print at best
        message = f"{record.msg} {record.args}"
    if holds_secret(message, *secrets):
        record.msg, record.args = LOG_WITHHELD, None
    if record.exc_info:
        try:
            text = logging.Formatter().formatException(record.exc_info)
        except Exception:
            text = "[exception could not be rendered]"
        record.exc_text, record.exc_info = text, None
    if record.exc_text and holds_secret(record.exc_text, *secrets):
        record.exc_text = LOG_WITHHELD
    if record.stack_info and holds_secret(record.stack_info, *secrets):
        record.stack_info = LOG_WITHHELD


class _KeyFilter(logging.Filter):
    """Registered keys, and their withholding from log records of the SDK and its HTTP stack.

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
        sanitize_record(record, self.secrets())
        return True


_LOG_FILTER = _KeyFilter()
# Loggers of the SDK and the HTTP libraries under it (httpx2 and httpcore2 in openai 3.x).
LOGGER_ROOTS = ("openai", "httpx", "httpx2", "httpcore", "httpcore2")


def _guarded_factory(previous: Callable[..., logging.LogRecord]) -> Callable[..., logging.LogRecord]:
    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if str(record.name).split(".", 1)[0] in LOGGER_ROOTS:
            sanitize_record(record, _LOG_FILTER.secrets())
        return record

    factory._memfalsify_guard = True  # type: ignore[attr-defined]
    return factory


def guard_logs(secret: str | None) -> None:
    """Withhold ``secret`` from the log records of the SDK and the HTTP stack, for the rest of the process.

    Two layers: the log record factory (chained to whatever factory was set before) sanitizes every new record
    of a logger under ``LOGGER_ROOTS`` as it is made, which covers loggers created at any time, even during a
    request; and a filter on those loggers sanitizes records made some other way when they are handled. A
    filter only sees its own logger's records, so it goes on each existing logger of those names. Every
    request calls this again, which re-installs the factory if someone replaced it."""
    if secret:
        _LOG_FILTER.add(secret)
    current = logging.getLogRecordFactory()
    if not getattr(current, "_memfalsify_guard", False):
        logging.setLogRecordFactory(_guarded_factory(current))
    names = list(logging.Logger.manager.loggerDict)
    for name in [*LOGGER_ROOTS, *names]:
        if name.split(".", 1)[0] in LOGGER_ROOTS:
            logger = logging.getLogger(name)
            if _LOG_FILTER not in logger.filters:
                logger.addFilter(_LOG_FILTER)


def _holds_hashed_certs(directory: str) -> bool:
    """Whether ``directory`` holds certificates under OpenSSL's hashed names (``1a2b3c4d.0``)."""
    try:
        return any(re.fullmatch(r"[0-9a-fA-F]{8}\.[0-9]", name) for name in os.listdir(directory))
    except OSError:
        return False


def tls_context() -> ssl.SSLContext:
    """The TLS context for https endpoints, independent of the environment.

    On macOS and Windows, ``truststore`` verifies through the system's own trust store, which no environment
    variable changes. On Linux and other OpenSSL platforms, truststore and Python's default context both load
    CA certificates through OpenSSL's default paths, which ``SSL_CERT_FILE`` and ``SSL_CERT_DIR`` override; this
    context loads the locations compiled into OpenSSL instead (or the first usual bundle in ``CA_FILES``), never
    those variables. With no CA certificates found, every https certificate fails verification."""
    if sys.platform in ("darwin", "win32"):
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    paths = ssl.get_default_verify_paths()  # openssl_cafile and openssl_capath are the compiled-in locations
    cafile = paths.openssl_cafile if paths.openssl_cafile and os.path.isfile(paths.openssl_cafile) else None
    capath = paths.openssl_capath if paths.openssl_capath and _holds_hashed_certs(paths.openssl_capath) else None
    if cafile is None and capath is None:
        cafile = next((candidate for candidate in CA_FILES if os.path.isfile(candidate)), None)
    if cafile is None and capath is None:
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # verifies against nothing: https fails closed
    return ssl.create_default_context(cafile=cafile, capath=capath)


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


FORMATS = ("json_schema", "json_object", "text")
_FORMAT_NAMES = {"json_schema": "response_format json_schema", "json_object": "response_format json_object",
                 "text": "no response_format (plain-text JSON)"}

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
    if mode == "json_schema":
        return {"response_format": {"type": "json_schema",
                                    "json_schema": {"name": "verdict", "strict": True, "schema": SCHEMA}}}
    if mode == "json_object":
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
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage(usage: Any) -> Any:
    """Chat Completions token counts under the names ``UsageMeter`` reads; a count that is not a whole number
    counts as missing. ``prompt_tokens`` includes any tokens the provider served from its own cache; the cache
    counters stay null for this backend."""
    if usage is None:
        return None
    return SimpleNamespace(input_tokens=_count(_get(usage, "prompt_tokens")),
                           output_tokens=_count(_get(usage, "completion_tokens")))


def _scalar(value: Any) -> str | None:
    """A string, number or boolean from an error body as text; None for anything else (no repr of a body)."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, (bool, int, float)):
        return str(value)
    return None


def names_format(param: str | None, code: str | None, message: str | None) -> bool:
    """Whether an error's param, code or message names the response format (see ``_FORMAT_WORD``)."""
    if param is not None:
        return param == "response_format" or param.startswith("response_format.")
    if code is not None and not _GENERIC_CODE.fullmatch(code):
        return bool(_FORMAT_ERROR.search(code))
    return bool(message and _FORMAT_WORD.search(_QUOTED_MODEL.sub(" ", message)))


@dataclass(frozen=True)
class Failure:
    """A request that did not produce a response, described without the exception that reported it."""
    message: str
    detail: str = ""
    format_refused: bool = False


class OpenAICompatVerifier:
    """Ask a model behind an OpenAI-compatible Chat Completions API for a verdict on one claim/passage pair."""

    def __init__(self, model: str, provider: str = Provider.openai.value, base_url: str | None = None,
                 api_key_env: str | None = None, client: Any = None, *,
                 progress: Callable[[str], None] | None = None) -> None:
        """``provider`` picks the preset base URL and key variable (``PRESETS``); ``base_url`` and
        ``api_key_env`` (the variable's name, never the key) override it. ``progress`` receives a line
        whenever the endpoint refuses a response format and the verifier falls back to the next one."""
        problem = None
        try:
            self.model = check_model(model)
            self.endpoint = resolve_endpoint(provider, base_url, api_key_env)
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
        self._format = 0  # index into FORMATS: the first one the endpoint has not refused
        self._settled = False  # a request has succeeded with FORMATS[self._format]
        self._negotiating = threading.Lock()
        self._progress = progress or (lambda message: None)

    def _client(self) -> Any:
        """The SDK client: our own HTTP client, which follows no redirect (a 307 or 308 would resend the claim,
        the evidence and the key to wherever the endpoint points), ignores proxy settings from the environment
        (``trust_env=False``) and verifies certificates with ``tls_context``, so requests go only to the
        configured base URL."""
        openai = self._sdk
        key = self._secret or NO_KEY
        failure = None
        try:
            http = openai.DefaultHttpxClient(follow_redirects=False, trust_env=False, verify=tls_context())
            return openai.OpenAI(api_key=key, base_url=self.endpoint.base_url,
                                 default_headers=isolated_headers(key), http_client=http, max_retries=MAX_RETRIES)
        except openai.OpenAIError as exc:  # the SDK's own configuration errors: fixed texts, no response body
            failure = f"{self.name}: {self._field(str(exc), 500)}"
        raise VerifierError(failure)

    def usage(self) -> list[VerifierStats]:
        return [self.meter.snapshot()]

    @property
    def response_format(self) -> str:
        """The format requests currently ask for (see ``FORMATS``)."""
        return FORMATS[self._format]

    def judge(self, claim: str, passage: str) -> Judgment:
        messages = prompt(claim, passage)
        if not self._settled:
            # The first request settles the format; concurrent judgments wait for it, so each refused format
            # costs one extra request per run, however many requests are in flight.
            with self._negotiating:
                if not self._settled:
                    return self._negotiate(messages)
        response, failure = self._send(messages, FORMATS[self._format])
        if failure is not None:
            raise VerifierError(failure.message)
        return self._judgment(response)

    def _negotiate(self, messages: list[dict[str, str]]) -> Judgment:
        """Ask in the current format, falling back to the next one while the endpoint refuses the format."""
        while True:
            mode = FORMATS[self._format]
            response, failure = self._send(messages, mode)
            if failure is None:
                self._settled = True
                return self._judgment(response)
            if not failure.format_refused or self._format + 1 == len(FORMATS):
                raise VerifierError(failure.message)
            self._format += 1
            self._progress(self._shown(
                f"{self.name}: the endpoint refused {_FORMAT_NAMES[mode]} ({failure.detail}); asking with "
                f"{_FORMAT_NAMES[FORMATS[self._format]]} from now on"))

    def _send(self, messages: list[dict[str, str]], mode: str) -> tuple[Any, Failure | None]:
        """One request. A failure is described inside the ``except`` block and raised by the caller after it,
        so no ``VerifierError`` carries the SDK's exception (and the body it may quote) as cause or context."""
        guard_logs(None)  # loggers created since the last request
        failure = None
        try:
            response = self.client.chat.completions.create(model=self.model, messages=messages,
                                                           **request_format(mode))
        except self._api_error as exc:
            failure = self._failure(exc, mode)
        if failure is not None:
            return None, failure
        self.meter.record(_usage(getattr(response, "usage", None)))
        return response, None

    def _failure(self, exc: Exception, mode: str) -> Failure:
        """What went wrong, built from the error's fields (status, type, code, param, message), each redacted;
        never from ``str()`` or ``repr()`` of a response body."""
        status = getattr(exc, "status_code", None)
        if not isinstance(status, int):
            return Failure(f"{self.name}: {self._without_response(exc)}")
        if 300 <= status < 400:
            return Failure(f"{self.name}: HTTP {status}: the endpoint answered with a redirect, which is not "
                           "followed; check the base URL")
        body = getattr(exc, "body", None)
        fields: dict[str, str] = {}
        message = None
        if isinstance(body, Mapping):
            for name in ("type", "code", "param"):
                value = _scalar(body.get(name))
                if value is not None:
                    fields[name] = value
            message = _scalar(body.get("message")) or _scalar(body.get("detail"))
        else:
            message = _scalar(body)
        text = f"HTTP {status}"
        if fields:
            text += " (" + ", ".join(f"{name} {self._field(value, 100)}" for name, value in fields.items()) + ")"
        if message:
            text += ": " + self._field(message, 500)
        # Only an error that names the format means "ask in another format"; any other 400 (an unknown model,
        # a context that is too long) is reported at once.
        refused = mode != "text" and status in (400, 422) and names_format(fields.get("param"), fields.get("code"),
                                                                            message)
        return Failure(f"{self.name}: {text}", detail=text, format_refused=refused)

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

    def _field(self, value: str, limit: int) -> str:
        """Provider text for a diagnostic: withheld whole if it holds the key in any encoding, else cut to
        ``limit`` characters and made one inert line."""
        if holds_secret(value, self._secret):
            return WITHHELD
        return self._shown(value[:limit] + ("..." if len(value) > limit else ""))

    def _judgment(self, response: Any) -> Judgment:
        """A verdict from a well-formed, normally finished completion; no verdict for anything else.

        The SDK does not validate responses, so a malformed envelope (choices that are not a list, a choice
        without a message, content or finish_reason of the wrong type) arrives as it was sent; it counts as no
        verdict, like a completion without choices or with an empty answer."""
        malformed = "the response is not a well-formed chat completion"
        if isinstance(response, (str, bytes, int, float, list)):
            return self._no_verdict(None, malformed)
        choices = _get(response, "choices")
        if choices is None or (isinstance(choices, list) and not choices):
            return self._no_verdict(None, "the response has no answer")
        if not isinstance(choices, list):
            return self._no_verdict(None, malformed)
        choice = choices[0]
        message = _get(choice, "message")
        finish = _get(choice, "finish_reason")
        if isinstance(choice, (str, bytes, int, float, list)) or isinstance(message, (str, bytes, int, float, list)):
            return self._no_verdict(None, malformed)
        refusal, content = _get(message, "refusal"), _get(message, "content")
        if not all(value is None or isinstance(value, str) for value in (finish, refusal, content)):
            return self._no_verdict(None, malformed)
        if refusal:
            return self._no_verdict(finish, "the model refused")
        if finish != FINISHED:
            return self._no_verdict(finish, "the answer did not end normally (only finish_reason stop is used)")
        if not content or not content.strip():
            return self._no_verdict(finish, "the answer is empty")
        verdict = parse_verdict(content)
        if verdict is None:
            return self._no_verdict(finish, "the answer is not a JSON verdict")
        return Judgment(relation=verdict.relation, verifier=self.name,
                        rationale=self._shown(WITHHELD if holds_secret(verdict.rationale, self._secret)
                                              else verdict.rationale))

    def _no_verdict(self, finish: Any, why: str) -> Judgment:
        return Judgment(relation="unrelated", verifier=self.name,
                        rationale=self._shown(f"no verdict (finish_reason: {finish}; {why})"))

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
        return key

    def _shown(self, text: object) -> str:
        """Provider errors and model text on one line, without terminal or bidi controls, and without the key."""
        return printable(redact(str(text), self._secret))
