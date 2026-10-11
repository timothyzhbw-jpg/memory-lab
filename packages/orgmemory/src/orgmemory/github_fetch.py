"""GitHub REST fetch for `orgmemory github sync`: serial GETs to the API origin only, returning a raw capture dict.

Nothing here maps or writes anything. The token is sent only to https://api.github.com, as an unredirected header,
and redirects are never followed automatically.
"""
from __future__ import annotations

import http.client
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit

from pydantic import AwareDatetime, TypeAdapter, ValidationError

API = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "orgmemory/0.2"
MAX_PAGES = 1000
MAX_HOPS = 3
ATTEMPTS = 5
REPO = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}/[a-z0-9._-]{1,100}")
PRINTABLE = re.compile(r"[\x21-\x7e]+")  # visible ASCII: tokens, Link and Location URLs
NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')

Transport = Callable[[str, dict[str, str]], tuple[int, dict[str, str], bytes]]
_TIME = TypeAdapter(AwareDatetime)


class GitHubError(Exception):
    """Permanent failure: the run stops and appends nothing (exit 2)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Stopped(Exception):
    """Transient stop (rate limit beyond --max-wait, exhausted retries): the capture becomes partial."""


def token_from_env(env: Mapping[str, str] | None = None) -> str | None:
    """The first non-empty GITHUB_TOKEN or GH_TOKEN. Omit env to read os.environ at call time."""
    if env is None:
        env = os.environ
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = (env.get(name) or "").strip()
        if value:
            return value
    return None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _path(url: str) -> str:
    """The URL path only, percent-encoded: error messages never show queries, headers or the token."""
    return quote(urlsplit(url).path, safe="/%")


def _no_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


def _message(body: bytes) -> str:
    try:
        data = json.loads(body, parse_constant=_no_constant)
    except ValueError:
        data = None
    message = data.get("message") if isinstance(data, dict) else None
    return json.dumps(message[:300] if isinstance(message, str) else None)


def _contains_token(value: Any, token: str) -> bool:
    if isinstance(value, str):
        return token in value
    if isinstance(value, dict):
        return any(_contains_token(k, token) or _contains_token(v, token) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_token(v, token) for v in value)
    return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface every 3xx as an HTTPError instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_transport(opener: urllib.request.OpenerDirector | None = None, timeout: float = 30) -> Transport:
    """A Transport on urllib (honours HTTPS_PROXY and the system CA store) that never follows redirects."""
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect)
    elif any(isinstance(h, urllib.request.HTTPRedirectHandler) and not isinstance(h, NoRedirect)
             for h in opener.handlers):
        raise ValueError("the opener must not follow redirects; build it with NoRedirect")

    def transport(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(url, method="GET")
        for name, value in headers.items():
            if name.lower() != "authorization":
                request.add_header(name, value)
            elif _origin(url) == API:
                request.add_unredirected_header(name, value)
        try:
            try:
                response = opener.open(request, timeout=timeout)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, _lower(response.headers), response.read()
        except URLError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise URLError(exc) from exc

    return transport


class _ResponseReader(io.RawIOBase):
    """Count socket bytes before buffering, including an incomplete status line before a timeout."""

    def __init__(self, raw, received: Callable[[], None]):
        self.raw, self.received = raw, received

    def readable(self) -> bool:
        return True

    def readinto(self, buffer):
        count = self.raw.readinto(buffer)
        if count:
            self.received()
        return count

    def close(self) -> None:
        try:
            self.raw.close()
        finally:
            super().close()


class _ResponseSocket:
    """HTTPResponse only needs makefile; keep its usual buffering over a byte-counting reader."""

    def __init__(self, sock, received: Callable[[], None]):
        self.sock, self.received = sock, received

    def makefile(self, mode):
        return io.BufferedReader(_ResponseReader(self.sock.makefile(mode, buffering=0), self.received))


class PersistentTransport:
    """Serial API-only GET transport with one verified HTTPS connection per run.

    HTTPS_PROXY/https_proxy or ALL_PROXY/all_proxy select urllib instead (no custom CONNECT tunnel).
    Only a failure before any response bytes gets one immediate reconnect; Client owns all other retries.
    Close explicitly or use as a context manager. The connection factory is injectable for offline tests.
    """

    def __init__(self, timeout: float = 30, *, connection_factory: Callable[..., Any] | None = None,
                 env: Mapping[str, str] | None = None) -> None:
        # None reads the process environment at construction. Binding os.environ as the default
        # would embed the live mapping, including tokens, in inspect.signature and help().
        if env is None:
            env = os.environ
        self.timeout = timeout
        self._factory = connection_factory or http.client.HTTPSConnection
        self._connection = None
        self._closed = False
        self._fallback = urllib_transport(timeout=timeout) if any(
            env.get(name) for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")) else None
        self._context = ssl.create_default_context()

    def _disconnect(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def close(self) -> None:
        self._closed = True
        self._disconnect()

    def __enter__(self) -> PersistentTransport:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        if self._closed:
            raise ValueError("the GitHub transport is closed")
        if not PRINTABLE.fullmatch(url) or _origin(url) != API:
            raise GitHubError(f"refusing a request outside {API}")
        parts = urlsplit(url)
        if parts.path.startswith(("//", "/\\")):
            raise GitHubError(f"refusing a request outside {API}: ambiguous path")
        if any(name.lower() == "host" for name in headers):
            raise GitHubError("refusing a request with a caller-supplied Host header")
        if self._fallback is not None:
            return self._fallback(url, headers)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        for attempt in range(2):
            received = False

            def note_bytes() -> None:
                nonlocal received
                received = True

            def response_class(sock, *args, **kwargs):
                return http.client.HTTPResponse(_ResponseSocket(sock, note_bytes), *args, **kwargs)

            try:
                if self._connection is None:
                    self._connection = self._factory("api.github.com", timeout=self.timeout, context=self._context)
                connection = self._connection
                connection.timeout = self.timeout
                if connection.sock is not None:
                    connection.sock.settimeout(self.timeout)
                connection.response_class = response_class
                connection.request("GET", target, headers=headers)
                with connection.getresponse() as response:
                    result = response.status, _lower(response.headers), response.read()
                    if response.will_close:
                        self._disconnect()
                    return result
            except (OSError, http.client.HTTPException) as exc:
                self._disconnect()
                if received or attempt:
                    raise URLError(exc) from exc
            except BaseException:
                self._disconnect()
                raise
        raise AssertionError("unreachable")


def _lower(headers) -> dict[str, str]:
    return {name.lower(): value for name, value in (headers.items() if headers else [])}


def _http_date(value: str | None) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _next_url(link: str, repo: str, repo_id: int, path: str) -> str:
    parts = urlsplit(link)
    segments = unquote(parts.path).split("/")
    if (PRINTABLE.fullmatch(link) and parts.scheme == "https" and parts.netloc == "api.github.com"
            and not {".", ".."} & set(segments)
            and (parts.path.lower().startswith(f"/repos/{repo.lower()}/")
                 or parts.path.startswith(f"/repositories/{repo_id}/"))):
        return link
    raise GitHubError(f"GET {path}: refusing a Link next URL outside {API}/repos/{repo}/")


class FetchProgress:
    """Throttled stderr output from counts and times only; no API text is interpolated.

    The monotonic clock is independent of Client's wall clock used for rate-limit resets.
    Short runs stay quiet. Call update at request boundaries and after each fully fetched item.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, stream: TextIO | None = None) -> None:
        self.clock = clock
        self.stream = sys.stderr if stream is None else stream
        self.last = clock()
        self.items = 0

    def update(self, requests: int, remaining: str | None, *, items: int | None = None) -> None:
        if items is not None:
            self.items = items
        now = self.clock()
        if now - self.last < 5:
            return
        self.last = now
        # Treat the header as a numeric counter, never as terminal text. Bound its size too.
        quota = str(int(remaining)) if remaining and re.fullmatch(r"[0-9]{1,20}", remaining) else "unknown"
        print(f"fetching: {self.items} issues/PRs, {requests} requests, {quota} remaining",
              file=self.stream, flush=True)

    def rate_limit(self, seconds: float, until: float) -> None:
        try:
            timestamp = _iso(datetime.fromtimestamp(until, timezone.utc))
        except (OverflowError, OSError, ValueError):
            timestamp = "unknown"  # A numeric reset may be outside the platform's date range.
        print(f"rate limit: sleeping {seconds:g} s, until {timestamp}", file=self.stream, flush=True)


class Client:
    """Serial GET client following GitHub's rate-limit rules, with injected sleep and clock."""

    def __init__(self, transport: Transport, token: str | None, *, max_wait: float = 3600,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time,
                 progress: FetchProgress | None = None) -> None:
        if token is not None and not PRINTABLE.fullmatch(token):
            raise GitHubError("the GitHub token contains characters that cannot be sent in an HTTP header")
        self.transport, self.token, self.max_wait, self.sleep, self.clock = transport, token, max_wait, sleep, clock
        self.progress = progress
        self.requests = 0
        self.first_date: datetime | None = None
        self.remaining: str | None = None
        self.reset = 0
        self.stopped: str | None = None

    def _headers(self, url: str) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION,
                   "User-Agent": USER_AGENT}
        if self.token and _origin(url) == API:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _wait(self, seconds: float, path: str) -> None:
        if seconds > self.max_wait:
            raise Stopped(f"GET {path}: rate limited for {seconds:.0f} s, more than --max-wait {self.max_wait:.0f} s")
        if self.progress is not None:
            self.progress.rate_limit(seconds, self.clock() + seconds)
        self.sleep(seconds)

    def _note(self, headers: dict[str, str]) -> None:
        self.requests += 1
        self.first_date = self.first_date or _http_date(headers.get("date"))
        self.remaining = headers.get("x-ratelimit-remaining")
        try:
            self.reset = int(headers.get("x-ratelimit-reset", 0))
        except ValueError:
            self.reset = 0

    def _rate_wait(self, status: int, headers: dict[str, str], body: bytes, attempt: int) -> float | None:
        if "retry-after" in headers:
            try:
                return max(int(headers["retry-after"]), 0)
            except ValueError:
                return 60 * 2 ** attempt
        if headers.get("x-ratelimit-remaining") == "0":
            return max(self.reset - self.clock() + 1, 1)
        if status == 429 or b"rate limit" in body.lower():
            return 60 * 2 ** attempt
        return None

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        """One logical GET: waits out rate limits, retries 5xx and network errors, follows same-origin 302/307."""
        hops = 0
        for attempt in range(ATTEMPTS):
            path = _path(url)
            if self.remaining == "0" and self.reset > self.clock():
                self._wait(self.reset - self.clock() + 1, path)
            try:
                status, headers, body = self.transport(url, self._headers(url))
            except URLError:
                status, headers, body = None, {}, b""
            else:
                # A server or proxy must not reflect the credential into errors,
                # saved captures, redirect paths or pagination URLs.
                if self.token:
                    try:
                        decoded = json.loads(body, parse_constant=_no_constant)
                    except ValueError:
                        decoded = None
                    if self.token.encode("ascii") in body or _contains_token(decoded, self.token) \
                            or any(self.token in value for value in headers.values()):
                        raise GitHubError("GitHub response reflected the credential; refused", status)
                self._note(headers)
            if self.progress is not None:
                self.progress.update(self.requests, self.remaining)
            if status is None or status >= 500:
                if attempt < 3:
                    self.sleep(2 ** attempt)
                    continue
                raise Stopped(f"GET {path}: {status or 'network error'} after retries")
            if status in (403, 429):
                wait = self._rate_wait(status, headers, body, attempt)
                if wait is None:
                    raise GitHubError(f"GET {path}: {status} {_message(body)}", status)
                self._wait(wait, path)
                continue
            if status in (301, 308):
                raise GitHubError(f"GET {path}: moved permanently (repository renamed or transferred); "
                                  "re-ingest deliberately", status)
            if status in (302, 307):
                location = headers.get("location") or ""
                target = urljoin(url, location)
                if not PRINTABLE.fullmatch(location) or _origin(target) != API:
                    raise GitHubError(f"GET {path}: refusing a redirect away from {API}", status)
                hops += 1
                if hops > MAX_HOPS:
                    raise GitHubError(f"GET {path}: more than {MAX_HOPS} redirects", status)
                url = target
                continue
            return status, headers, body
        raise Stopped(f"GET {_path(url)}: gave up after {ATTEMPTS} attempts")

    def get_json(self, url: str) -> tuple[int, Any, dict[str, str]]:
        """200 gives parsed JSON; 404 and 410 give (status, None, headers); anything else raises GitHubError."""
        status, headers, body = self.get(url)
        if status in (404, 410):
            return status, None, headers
        if status != 200:
            raise GitHubError(f"GET {_path(url)}: {status} {_message(body)}", status)
        try:
            return status, json.loads(body, parse_constant=_no_constant), headers
        except ValueError:
            raise GitHubError(f"GET {_path(url)}: malformed JSON") from None

    def pages(self, path: str, params: dict[str, str], *, repo: str, repo_id: int) -> Iterator[list[dict]]:
        """Yield each page of a listing, following only validated rel="next" links."""
        url = f"{API}{path}?{urlencode(params)}" if params else f"{API}{path}"
        for _ in range(MAX_PAGES):
            status, data, headers = self.get_json(url)
            if status != 200:
                raise GitHubError(f"GET {_path(url)}: {status}", status)
            if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
                raise GitHubError(f"GET {_path(url)}: malformed API data (expected a list of objects)")
            yield data
            match = NEXT.search(headers.get("link", ""))
            if not match:
                return
            url = _next_url(match.group(1), repo, repo_id, _path(url))
        raise GitHubError(f"GET {path}: more than {MAX_PAGES} pages")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value: Any, what: str = "number") -> int:
    if type(value) is not int or value < 1:
        raise GitHubError(f"malformed API data: {what} is not a positive integer")
    return value


def _when(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return _TIME.validate_python(value)
        except ValidationError:
            pass
    raise GitHubError("malformed API data: updated_at is not an ISO 8601 time with timezone")


def _select(client: Client, repo: str, repo_id: int, since: datetime | None) -> dict[int, dict | None]:
    """Numbers to refresh: listed issues/PRs (with their objects) plus PRs found only by the desc /pulls scan."""
    found: dict[int, dict | None] = {}
    listing = {"state": "all", "sort": "created", "direction": "asc", "per_page": "100"}
    if since:
        listing["since"] = _iso(since)
    for page in client.pages(f"/repos/{repo}/issues", listing, repo=repo, repo_id=repo_id):
        for obj in page:
            found[_number(obj.get("number"))] = obj
    if since:
        scan = {"state": "all", "sort": "updated", "direction": "desc", "per_page": "100"}
        for page in client.pages(f"/repos/{repo}/pulls", scan, repo=repo, repo_id=repo_id):
            older = False
            for pr in page:
                if _when(pr.get("updated_at")) >= since:
                    found.setdefault(_number(pr.get("number")), None)
                else:
                    older = True
            if older:
                break
    return found


def fetch(client: Client, repo: str, *, since: datetime | None, issues: list[int],
          pinned_repo_id: int | None) -> dict:
    """Fetch a raw capture dict. `issues` makes a targeted run (since is then ignored); since=None is a full run.

    A Stopped fetch returns complete=False with only the items whose issue and full timeline were fetched.
    """
    repo = repo.lower()
    if not REPO.fullmatch(repo) or repo.split("/")[1] in (".", ".."):
        raise ValueError("invalid repository; expected OWNER/REPO")
    if since is not None and since.tzinfo is None:
        raise ValueError("since must be timezone-aware")
    if any(type(n) is not int or n < 1 for n in issues):
        raise ValueError("issue numbers must be positive integers")
    targets = sorted(set(issues))
    since = None if targets or since is None else since.astimezone(timezone.utc).replace(microsecond=0)
    try:
        status, meta, _ = client.get_json(f"{API}/repos/{repo}")
    except Stopped as exc:
        raise GitHubError(f"{exc}; nothing was fetched") from None
    if status != 200:
        raise GitHubError(f"GET /repos/{repo}: not found (or private and the token lacks access)", status)
    if not isinstance(meta, dict) or not isinstance(meta.get("full_name"), str):
        raise GitHubError(f"GET /repos/{repo}: malformed API data")
    repo_id = _number(meta.get("id"), "repository id")
    if meta["full_name"].lower() != repo:
        raise GitHubError(f"GET /repos/{repo}: repository identity changed (GitHub returned another full name)")
    if pinned_repo_id is not None and repo_id != pinned_repo_id:
        raise GitHubError(f"repository identity changed: {repo} is repo id {repo_id}, "
                          f"but github-sync.jsonl pins {pinned_repo_id}")
    if client.first_date is None:  # started_at must precede the listing, or the next cursor could skip updates
        raise GitHubError("GitHub sent no valid Date header; cannot record when this run started")
    items: list[dict] = []
    skipped: list[dict] = []
    complete = True
    try:
        selected = dict.fromkeys(targets) if targets else _select(client, repo, repo_id, since)
        for n in sorted(selected):
            issue = selected[n]
            if issue is None:
                status, issue, _ = client.get_json(f"{API}/repos/{repo}/issues/{n}")
                if status != 200:
                    skipped.append({"number": n, "reason": f"issue {status}"})
                    continue
                if not isinstance(issue, dict) or _number(issue.get("number")) != n:
                    raise GitHubError(f"GET /repos/{repo}/issues/{n}: malformed API data")
            timeline: list[dict] = []
            try:
                for page in client.pages(f"/repos/{repo}/issues/{n}/timeline", {"per_page": "100"},
                                         repo=repo, repo_id=repo_id):
                    timeline.extend(page)
            except GitHubError as exc:
                if exc.status not in (404, 410):
                    raise
                skipped.append({"number": n, "reason": f"timeline {exc.status}"})
                continue
            items.append({"number": n, "issue": issue, "timeline": timeline})
            if client.progress is not None:
                client.progress.update(client.requests, client.remaining, items=len(items))
    except Stopped as exc:
        complete, client.stopped = False, str(exc)
    return {"github_capture": 1, "repo": repo, "repo_id": repo_id,
            "mode": "targeted" if targets else "incremental" if since else "full",
            "since": _iso(since) if since else None, "started_at": _iso(client.first_date),
            "complete": complete, "requests": client.requests, "skipped": skipped, "items": items}
