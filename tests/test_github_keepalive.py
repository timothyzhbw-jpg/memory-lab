"""Real stdlib HTTP parsing over injected sockets: no DNS, network, TLS peer or proxy needed."""
import http.client
import inspect
import io
import ssl
from urllib.error import URLError

import pytest
from typer.testing import CliRunner

from conftest import snapshot
from github_fakes import API, DATE, FakeClock, FakeTransport, build_capture, reply, routes_from_capture
from orgmemory import github_apply, github_fetch
from orgmemory.cli import app
from orgmemory.github_fetch import Client, GitHubError, PersistentTransport

TOKEN = "ghp_TESTTOKEN"
URL = f"{API}/repos/acme/web"


def wire(body=b"{}", status=200, headers=b""):
    return (f"HTTP/1.1 {status} {http.client.responses[status]}\r\nDate: {DATE}\r\n"
            f"Content-Length: {len(body)}\r\n".encode() + headers + b"\r\n" + body)


class ScriptedReader(io.RawIOBase):
    """Return fragments or fail on the next raw read, even inside a buffered readline."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.bytes_read = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        if not self.steps:
            return 0
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        count = min(len(step), len(buffer))
        buffer[:count] = step[:count]
        self.bytes_read += count
        if count < len(step):
            self.steps.insert(0, step[count:])
        return count


class ScriptedSocket:
    def __init__(self, responses, *, send_error=None):
        self.responses, self.send_error = list(responses), send_error
        self.sent, self.readers, self.timeouts = [], [], []
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)
        if self.send_error is not None:
            raise self.send_error

    def makefile(self, mode, buffering=0):
        assert mode == "rb" and buffering == 0
        response = self.responses.pop(0)
        reader = ScriptedReader([response] if isinstance(response, bytes) else response)
        self.readers.append(reader)
        return reader

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def close(self):
        self.closed = True


class Connections:
    """Inject real HTTPSConnection objects whose connect method supplies a scripted socket."""

    def __init__(self, *sockets):
        self.sockets, self.calls, self.connections = list(sockets), [], []

    def __call__(self, host, **kwargs):
        self.calls.append((host, kwargs))
        next_socket = self.sockets.pop(0)

        class Connection(http.client.HTTPSConnection):
            def connect(self):
                if isinstance(next_socket, Exception):
                    raise next_socket
                self.sock = next_socket
                self.sock.settimeout(self.timeout)

        connection = Connection(host, **kwargs)
        self.connections.append(connection)
        return connection


def persistent(factory, **kwargs):
    return PersistentTransport(connection_factory=factory, env={}, **kwargs)


def test_reuses_one_verified_connection_and_resets_timeout_per_request():
    sock = ScriptedSocket([wire()] * 5)
    factory = Connections(sock)
    with persistent(factory, timeout=7) as transport:
        client = Client(transport, TOKEN)
        for n in range(5):
            assert client.get_json(f"{URL}?page={n}")[:2] == (200, {})
        assert len(factory.calls) == 1 and not sock.closed
        assert len(sock.sent) == client.requests == 5
        assert all(b"Authorization: Bearer ghp_TESTTOKEN\r\n" in request for request in sock.sent)
        assert all(b"Host: api.github.com\r\n" in request for request in sock.sent)
        assert sock.timeouts == [7] * 5
        host, options = factory.calls[0]
        assert host == "api.github.com" and options["timeout"] == 7
        assert options["context"].verify_mode == ssl.CERT_REQUIRED
        assert options["context"].check_hostname is True
    assert sock.closed and all(reader.closed for reader in sock.readers)
    assert factory.connections[0].sock is None
    transport.close()  # idempotent
    with pytest.raises(ValueError, match="closed"):
        transport(URL, {})


def test_server_closed_idle_connection_reconnects_once_without_client_backoff():
    stale = ScriptedSocket([wire(), b""])
    fresh = ScriptedSocket([wire(), wire()])
    factory, clock = Connections(stale, fresh), FakeClock()
    with persistent(factory) as transport:
        client = Client(transport, TOKEN, sleep=clock.sleep, clock=clock.clock)
        for _ in range(3):
            assert client.get_json(URL)[:2] == (200, {})
        assert len(factory.calls) == 2 and stale.closed
        assert len(stale.sent) == len(fresh.sent) == 2
        assert client.requests == 3 and clock.sleeps == []
    assert fresh.closed


@pytest.mark.parametrize("failure", ["connect", "send", "read"])
def test_error_before_any_response_bytes_reconnects_once(failure):
    error = TimeoutError("offline")
    first = (error if failure == "connect" else ScriptedSocket(
        [[error]], send_error=error if failure == "send" else None))
    second = ScriptedSocket([wire()])
    factory = Connections(first, second)
    with persistent(factory) as transport:
        assert transport(URL, {})[0] == 200
        assert len(factory.calls) == 2
        if failure != "connect":
            assert first.closed
    assert second.closed


def test_reconnect_failure_surfaces_as_urlerror_after_at_most_one_retry():
    first, second = ScriptedSocket([b""]), ScriptedSocket([[ConnectionResetError()]])
    factory = Connections(first, second)
    with persistent(factory) as transport:
        with pytest.raises(URLError):
            transport(URL, {})
        assert len(factory.calls) == 2 and first.closed and second.closed
        assert transport._connection is None


@pytest.mark.parametrize("response", [
    [b"HTTP/1.", TimeoutError()],  # bytes buffered inside an unfinished status line
    [b"nonsense\r\n"],
    [b"HTTP/1.1 200 OK\r\nX-Partial: ", TimeoutError()],
    [b"HTTP/1.1 100 Continue\r\n\r\n", ConnectionResetError()],
    [b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\n{}"],
    [b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\n{}", TimeoutError()],
    [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nbad\r\n{}"],
])
def test_no_transport_retry_after_any_response_bytes(response):
    failed, fresh = ScriptedSocket([response]), ScriptedSocket([wire()])
    factory = Connections(failed, fresh)
    with persistent(factory) as transport:
        with pytest.raises(URLError):
            transport(URL, {})
        assert len(factory.calls) == 1 and failed.closed
        assert all(reader.closed for reader in failed.readers)
        # A later logical request may open a fresh connection, without reusing the failed one.
        assert transport(URL, {})[0] == 200
        assert len(factory.calls) == 2


def test_connection_close_response_is_returned_and_next_request_opens_fresh():
    first = ScriptedSocket([wire(headers=b"Connection: close\r\n")])
    second = ScriptedSocket([wire()])
    factory = Connections(first, second)
    with persistent(factory) as transport:
        assert transport(URL, {})[0] == 200 and first.closed
        assert transport(URL, {})[0] == 200
        assert len(factory.calls) == 2


@pytest.mark.parametrize("chunked", [False, True])
def test_full_large_binary_body_and_chunked_framing_unchanged(chunked):
    body = bytes(range(256)) * 1024
    response = wire(body)
    if chunked:
        response = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" +
                    f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
    sock = ScriptedSocket([response, wire()])
    factory = Connections(sock)
    with persistent(factory) as transport:
        assert transport(URL, {})[2] == body
        assert transport(URL, {})[2] == b"{}"
        assert len(factory.calls) == 1


@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
@pytest.mark.parametrize("status", [200, 403, 404, 410])
def test_persistent_response_size_cap_stops_reading_early(framing, status):
    cap = 8 * 1024 * 1024
    body = b'x' * (cap * 2)
    response = wire(body, status=status)
    if framing == "chunked":
        response = (f"HTTP/1.1 {status} OK\r\nTransfer-Encoding: chunked\r\n\r\n".encode() +
                    f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
    elif framing == "eof":
        response = f"HTTP/1.1 {status} OK\r\nConnection: close\r\n\r\n".encode() + body
    sock, fresh = ScriptedSocket([response]), ScriptedSocket([wire()])
    factory, clock = Connections(sock, fresh), FakeClock()
    with persistent(factory) as transport:
        with pytest.raises(GitHubError, match="response body exceeds") as raised:
            Client(transport, TOKEN, sleep=clock.sleep, clock=clock.clock).get(URL)
        assert raised.value.status is None
        assert cap < sock.readers[0].bytes_read < cap + 16 * 1024 < len(response)
        assert sock.readers[0].steps  # The unread tail was not drained on close.
        assert sock.closed and sock.readers[0].closed and transport._connection is None
        assert len(factory.calls) == 1 and clock.sleeps == []
        assert transport(URL, {})[2] == b"{}"  # Oversized responses cannot contaminate a reused connection.
        assert len(factory.calls) == 2


@pytest.mark.parametrize("chunked", [False, True])
def test_persistent_response_at_size_cap_is_accepted(chunked):
    body = b'x' * (8 * 1024 * 1024)
    response = wire(body)
    if chunked:
        response = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" +
                    f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
    sock = ScriptedSocket([response, wire()])
    factory = Connections(sock)
    with persistent(factory) as transport:
        assert transport(URL, {})[2] == body
        assert transport(URL, {})[2] == b"{}" and len(factory.calls) == 1


@pytest.mark.parametrize("status", [200, 404, 410])
def test_cli_oversized_timeline_is_an_error_without_writes(github_dataset, tmp_path, monkeypatch, status):
    sock = ScriptedSocket([wire(reply({"id": 1001, "full_name": "acme/web"})[2]),
                           wire(reply({"number": 5})[2]), wire(b'x' * (16 * 1024 * 1024), status=status)])
    transport = persistent(Connections(sock))
    monkeypatch.setattr(github_fetch, "PersistentTransport", lambda: transport)
    monkeypatch.setattr(github_fetch, "token_from_env", lambda: TOKEN)
    before = snapshot(github_dataset)
    capture = tmp_path / "oversized-capture.json"
    result = CliRunner().invoke(app, ["github", "sync", str(github_dataset), "acme/web", "--issue", "5",
                                    "--save-capture", str(capture)])
    assert result.exit_code == 2 and "response body exceeds" in result.output
    assert TOKEN not in result.output
    assert snapshot(github_dataset) == before and not capture.exists()
    assert sock.closed and all(reader.closed for reader in sock.readers)


def test_partial_response_error_uses_existing_client_backoff():
    first = ScriptedSocket([[b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\n{}", TimeoutError()]])
    second = ScriptedSocket([wire()])
    factory, clock = Connections(first, second), FakeClock()
    with persistent(factory) as transport:
        client = Client(transport, TOKEN, sleep=clock.sleep, clock=clock.clock)
        assert client.get_json(URL)[:2] == (200, {})
        assert clock.sleeps == [1] and client.requests == 1
        assert len(factory.calls) == 2 and first.closed


def test_rate_limits_and_server_retries_remain_in_client():
    sock = ScriptedSocket([wire(status=429, headers=b"Retry-After: 2\r\n"),
                           wire(status=502), wire()])
    factory, clock = Connections(sock), FakeClock()
    with persistent(factory) as transport:
        client = Client(transport, TOKEN, sleep=clock.sleep, clock=clock.clock)
        assert client.get_json(URL)[:2] == (200, {})
        assert clock.sleeps == [2, 2] and client.requests == 3
        assert len(factory.calls) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 404, 429, 502])
def test_status_headers_and_body_returned_without_transport_retries_or_redirects(status):
    body = b'{"message":"test"}'
    sock = ScriptedSocket([wire(body, status, headers=(
        b"Location: https://evil.example/steal\r\nX-RateLimit-Remaining: 41\r\n"))])
    factory = Connections(sock)
    with persistent(factory) as transport:
        got_status, headers, got_body = transport(URL, {"Authorization": f"Bearer {TOKEN}"})
        assert (got_status, got_body) == (status, body)
        assert headers == {"date": DATE, "content-length": str(len(body)),
                           "location": "https://evil.example/steal", "x-ratelimit-remaining": "41"}
        assert len(factory.calls) == len(sock.sent) == 1


@pytest.mark.parametrize("url", ["https://evil.example/x", "http://api.github.com/x",
                                 "https://api.github.com.evil.example/x", "https://api.github.com:443/x",
                                 "https://user@api.github.com/x", "//evil.example/x",
                                 f"{API}/x\r\nX-Evil: 1"])
def test_refuses_other_origins_before_connection_or_authorization(url):
    factory = Connections()
    with persistent(factory) as transport:
        with pytest.raises(GitHubError, match="refusing a request"):
            transport(url, {"Authorization": f"Bearer {TOKEN}"})
    assert factory.calls == []


@pytest.mark.parametrize("url", [f"{API}//evil.example/x", f"{API}/\\evil.example/x"])
def test_refuses_network_path_targets(url):
    factory = Connections()
    with persistent(factory) as transport:
        with pytest.raises(GitHubError, match="refusing a request"):
            transport(url, {"Authorization": f"Bearer {TOKEN}"})
    assert factory.calls == []


@pytest.mark.parametrize("name", ["Host", "host", "HOST"])
def test_refuses_caller_supplied_host_header(name):
    factory = Connections()
    with persistent(factory) as transport:
        with pytest.raises(GitHubError, match="Host header"):
            transport(URL, {name: "evil.example", "Authorization": f"Bearer {TOKEN}"})
    assert factory.calls == []


def test_redirect_policy_remains_in_client_and_never_sends_auth_elsewhere():
    sock = ScriptedSocket([wire(status=307, headers=b"Location: /repos/acme/next\r\n"), wire(),
                           wire(status=302, headers=b"Location: https://evil.example/steal\r\n")])
    factory = Connections(sock)
    with persistent(factory) as transport:
        client = Client(transport, TOKEN)
        assert client.get_json(URL)[:2] == (200, {})
        with pytest.raises(GitHubError, match="refusing a redirect away"):
            client.get_json(URL)
        assert len(factory.calls) == 1 and len(sock.sent) == 3
        assert sock.sent[1].startswith(b"GET /repos/acme/next HTTP/1.1\r\n")
        assert all(b"Authorization: Bearer ghp_TESTTOKEN\r\n" in data for data in sock.sent)


@pytest.mark.parametrize("where", ["raw-body", "escaped-body", "header", "percent-body", "percent-header",
                                   "header-name", "percent-header-name"])
def test_credential_reflection_still_rejected_by_client(where):
    body, headers = b"{}", b""
    if where == "raw-body":
        body = ('{"message":"' + TOKEN + '"}').encode()
    elif where == "escaped-body":
        body = ('{"message":"' + ''.join('\\u%04x' % ord(ch) for ch in TOKEN) + '"}').encode()
    elif where == "header":
        headers = f"X-Reflected: {TOKEN}\r\n".encode()
    else:
        encoded = ''.join(f'%{ord(ch):02X}' for ch in TOKEN)
        if where == "percent-body":
            body = ('{"body":"' + encoded + '"}').encode()
        elif where == "percent-header":
            headers = f"X-Reflected: {encoded}\r\n".encode()
        else:
            headers = f"{TOKEN if where == 'header-name' else encoded}: ok\r\n".encode()
    sock = ScriptedSocket([wire(body, headers=headers)])
    with persistent(Connections(sock)) as transport:
        with pytest.raises(GitHubError, match="reflected the credential") as exc:
            Client(transport, TOKEN).get_json(URL)
        assert TOKEN not in str(exc.value)
    assert sock.closed


@pytest.mark.parametrize("name", ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"])
def test_proxy_selects_urllib_fallback_with_same_timeout_and_origin_guard(monkeypatch, name):
    calls = []
    fake = FakeTransport({"GET /repos/acme/web": reply({})})

    def fallback(**kwargs):
        calls.append(kwargs)
        return fake

    monkeypatch.setattr(github_fetch, "urllib_transport", fallback)
    factory = Connections()
    with PersistentTransport(timeout=9, connection_factory=factory, env={name: "http://proxy.invalid"}) as transport:
        assert Client(transport, TOKEN).get_json(URL)[:2] == (200, {})
        with pytest.raises(GitHubError):
            transport("https://evil.example/x", {"Authorization": f"Bearer {TOKEN}"})
    assert calls == [{"timeout": 9}] and factory.calls == []
    assert len(fake.calls) == 1 and fake.calls[0][1]["Authorization"] == f"Bearer {TOKEN}"


def test_empty_proxy_settings_keep_direct_connection():
    factory = Connections(ScriptedSocket([wire()]))
    with PersistentTransport(connection_factory=factory, env={"HTTPS_PROXY": "", "ALL_PROXY": ""}) as transport:
        assert transport(URL, {})[0] == 200
    assert len(factory.calls) == 1


def test_omitted_env_reads_process_environment_without_capturing_it(monkeypatch):
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    with PersistentTransport(connection_factory=Connections()) as transport:
        assert transport._fallback is None
    monkeypatch.setenv("https_proxy", "http://proxy.invalid")
    with PersistentTransport(connection_factory=Connections(), env=None) as transport:
        assert transport._fallback is not None
    default = inspect.signature(PersistentTransport.__init__).parameters["env"].default
    assert default is None
    assert "proxy.invalid" not in str(inspect.signature(PersistentTransport.__init__))


def test_context_manager_closes_on_error():
    sock = ScriptedSocket([wire()])
    with pytest.raises(RuntimeError, match="run failed"):
        with persistent(Connections(sock)) as transport:
            transport(URL, {})
            raise RuntimeError("run failed")
    assert sock.closed


@pytest.mark.parametrize("outcome,exit_code", [("complete", 0), ("partial", 3), ("error", 2),
                                             ("invalid-token", 2), ("interrupted", None)])
def test_cli_closes_transport_on_every_exit(tmp_path, monkeypatch, outcome, exit_code):
    capture = build_capture()

    class ClosingTransport(FakeTransport):
        closed = False

        def close(self):
            self.closed = True

    fake = ClosingTransport(routes_from_capture(capture))
    if outcome == "partial":
        listing = "GET /repos/acme/web/issues?direction=asc&per_page=100&sort=created&state=all"
        fake.routes[listing] = reply({}, status=429, headers={"retry-after": "9999"})
    elif outcome == "error":
        fake.routes["GET /repos/acme/web"] = reply({}, status=403)
    elif outcome == "interrupted":
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt()
        monkeypatch.setattr(github_apply, "run_sync", interrupt)
    monkeypatch.setattr(github_fetch, "PersistentTransport", lambda: fake)
    monkeypatch.setattr(github_fetch, "token_from_env", lambda: "invalid\nTOKEN" if outcome == "invalid-token" else TOKEN)
    result = CliRunner().invoke(app, ["github", "sync", str(tmp_path), "acme/web"])
    if exit_code is None:
        assert result.exit_code != 0  # Click/Typer versions differ in the interrupt exit code.
    else:
        assert result.exit_code == exit_code, result.output
    assert fake.closed
