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
- The key is read from the environment variable the caller names, never taken as an argument. It is never
  shown: errors from the SDK pass through ``redact`` before they become a ``VerifierError``.
- Only the arguments configure the client. The SDK would otherwise read ``OPENAI_BASE_URL``,
  ``OPENAI_ORG_ID``, ``OPENAI_PROJECT_ID`` and ``OPENAI_CUSTOM_HEADERS`` from the environment and send what they
  hold to whatever endpoint is configured; ``isolated_headers`` removes those headers from every request.

Structured output: a request asks for a JSON verdict with ``response_format`` ``json_schema`` (strict). An
endpoint that rejects the request with HTTP 400 or 422 is asked again with ``json_object``, then with no
``response_format`` at all; the instructions ask for the same JSON object every time. The first format the
endpoint accepts is kept for the rest of the run. An answer that is not a JSON verdict (invalid JSON, a
repeated or unknown key, a relation outside the schema), an empty answer and a refusal are all recorded as no
verdict (``unrelated``, with the reason in the rationale), never as support.
"""
from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

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


def redact(text: str, secret: str | None) -> str:
    """``text`` with ``secret``, and every run of ``REDACT_RUN`` or more of its consecutive characters, replaced.

    The run rule covers an error body that quotes part of a key, or the key cut at a line end."""
    if not secret:
        return text
    text = text.replace(secret, REDACTED)
    if len(secret) < REDACT_RUN:
        return text
    pieces = {secret[i:i + REDACT_RUN] for i in range(len(secret) - REDACT_RUN + 1)}
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


def _usage(usage: Any) -> Any:
    """Chat Completions token counts under the names ``UsageMeter`` reads. ``prompt_tokens`` includes any
    tokens the provider served from its own cache; the cache counters stay null for this backend."""
    if usage is None:
        return None
    return SimpleNamespace(input_tokens=getattr(usage, "prompt_tokens", None),
                           output_tokens=getattr(usage, "completion_tokens", None))


class OpenAICompatVerifier:
    """Ask a model behind an OpenAI-compatible Chat Completions API for a verdict on one claim/passage pair."""

    def __init__(self, model: str, provider: str = Provider.openai.value, base_url: str | None = None,
                 api_key_env: str | None = None, client: Any = None, *,
                 progress: Callable[[str], None] | None = None) -> None:
        """``provider`` picks the preset base URL and key variable (``PRESETS``); ``base_url`` and
        ``api_key_env`` (the variable's name, never the key) override it. ``progress`` receives a line
        whenever the endpoint refuses a response format and the verifier falls back to the next one."""
        try:
            self.model = check_model(model)
            self.endpoint = resolve_endpoint(provider, base_url, api_key_env)
        except ValueError as exc:
            raise VerifierError(str(exc)) from exc
        self.name = f"openai:{model}@{self.endpoint.host}"
        try:
            import openai
            self._api_error: type[Exception] | tuple = openai.OpenAIError
        except ImportError as exc:
            if client is None:
                raise VerifierError("the OpenAI-compatible verifier needs the openai package: "
                                    "pip install 'memfalsify[openai]'") from exc
            self._api_error = ()
        self._secret = self._key(required=client is None)
        if client is None:
            try:
                key = self._secret or NO_KEY
                client = openai.OpenAI(api_key=key, base_url=self.endpoint.base_url,
                                       default_headers=isolated_headers(key))
            except openai.OpenAIError as exc:
                raise VerifierError(self._shown(f"{self.name}: {exc}")) from exc
        self.client = client
        self.meter = UsageMeter(self.name)
        self._format = 0  # index into FORMATS: the first one the endpoint has not refused
        self._lock = threading.Lock()
        self._progress = progress or (lambda message: None)

    def usage(self) -> list[VerifierStats]:
        return [self.meter.snapshot()]

    @property
    def response_format(self) -> str:
        """The format requests currently ask for (see ``FORMATS``)."""
        return FORMATS[self._format]

    def judge(self, claim: str, passage: str) -> Judgment:
        messages = prompt(claim, passage)
        while True:
            index = self._format
            try:
                response = self.client.chat.completions.create(model=self.model, messages=messages,
                                                               **request_format(FORMATS[index]))
            except self._api_error as exc:
                if index + 1 < len(FORMATS) and getattr(exc, "status_code", None) in (400, 422):
                    self._fall_back(index, exc.status_code)
                    continue
                raise VerifierError(self._shown(f"{self.name}: {exc}")) from exc
            self.meter.record(_usage(getattr(response, "usage", None)))
            return self._judgment(response)

    def _fall_back(self, index: int, status: int) -> None:
        """A request with ``FORMATS[index]`` got HTTP 400 or 422: use the next format from now on. Several threads
        can see the same refusal; only the first moves on (and reports it)."""
        with self._lock:
            if self._format == index:
                self._format = index + 1
                self._progress(self._shown(
                    f"{self.name}: HTTP {status} for a request with {_FORMAT_NAMES[FORMATS[index]]}; "
                    f"asking with {_FORMAT_NAMES[FORMATS[index + 1]]} from now on"))

    def _judgment(self, response: Any) -> Judgment:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return self._no_verdict(None, "the response has no answer")
        choice = choices[0]
        finish = getattr(choice, "finish_reason", None)
        message = getattr(choice, "message", None)
        if getattr(message, "refusal", None):
            return self._no_verdict(finish, "the model refused")
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            return self._no_verdict(finish, "the answer is empty")
        verdict = parse_verdict(content)
        if verdict is None:
            return self._no_verdict(finish, "the answer is not a JSON verdict")
        return Judgment(relation=verdict.relation, rationale=self._shown(verdict.rationale), verifier=self.name)

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
        if not key.isascii() or any(ch.isspace() or not ch.isprintable() for ch in key):
            raise VerifierError(f"{self.name}: the API key contains whitespace, control or non-ASCII characters")
        return key

    def _shown(self, text: object) -> str:
        """Provider errors and model text on one line, without terminal or bidi controls, and without the key."""
        return printable(redact(str(text), self._secret))
