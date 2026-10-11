"""Offline progress checks: timing, rate waits, safe terminal text, and CLI stdout."""
import io
import json
from datetime import datetime, timezone
from urllib.error import URLError

import pytest
from typer.testing import CliRunner

from conftest import snapshot
from github_fakes import API, NOW, FakeClock, FakeTransport, build_capture, issue, item, reply, routes_from_capture
from orgmemory import github_fetch
from orgmemory.cli import app
from orgmemory.github_fetch import Client, FetchProgress, Stopped, fetch

REPO = "GET /repos/acme/web"
LISTING = "GET /repos/acme/web/issues?direction=asc&per_page=100&sort=created&state=all"
T1 = "GET /repos/acme/web/issues/1/timeline?per_page=100"
T2 = T1.replace("/1/", "/2/")
TOKEN = "ghp_TESTTOKEN"


def test_progress_throttles_by_elapsed_time_even_during_request_bursts():
    clock, stream = FakeClock(), io.StringIO()
    progress = FetchProgress(clock=clock.clock, stream=stream)
    for requests in range(1, 101):
        progress.update(requests, "3500", items=12)
    clock.now += 4.99
    progress.update(101, "3500")
    assert stream.getvalue() == ""
    clock.now = NOW + 5
    progress.update(102, "03500")
    for requests in range(103, 201):
        progress.update(requests, "3500", items=20)
    clock.now = NOW + 9.99
    progress.update(201, "3500")
    assert stream.getvalue() == "fetching: 12 issues/PRs, 102 requests, 3500 remaining\n"
    clock.now = NOW + 10
    progress.update(202, None)
    assert stream.getvalue().splitlines() == [
        "fetching: 12 issues/PRs, 102 requests, 3500 remaining",
        "fetching: 20 issues/PRs, 202 requests, unknown remaining",
    ]


def test_fetch_progress_counts_only_items_with_complete_timelines():
    i1 = issue(1, "buyer-acme", "2026-10-05T09:00:00Z")
    i2 = issue(2, "lin-eng", "2026-10-06T09:00:00Z", pr=True)
    page2 = "GET /repositories/1001/issues/1/timeline?page=2&per_page=100"
    routes = {REPO: {"id": 1001, "full_name": "acme/web"}, LISTING: [i1, i2],
              T1: reply([], link=f'<{API}{page2[4:]}>; rel="next"'), page2: [], T2: []}
    clock, stream = FakeClock(), io.StringIO()
    transport = FakeTransport(routes)

    def slow_transport(url, headers):
        clock.now += 1
        return transport(url, headers)

    progress = FetchProgress(clock=clock.clock, stream=stream)
    client = Client(slow_transport, TOKEN, clock=clock.clock, sleep=clock.sleep, progress=progress)
    capture = fetch(client, "acme/web", since=None, issues=[], pinned_repo_id=None)
    assert stream.getvalue() == "fetching: 1 issues/PRs, 5 requests, 4999 remaining\n"
    assert progress.items == len(capture["items"]) == 2
    quiet_transport = FakeTransport(routes)
    quiet = fetch(Client(quiet_transport, TOKEN), "acme/web", since=None, issues=[], pinned_repo_id=None)
    assert capture == quiet and transport.calls == quiet_transport.calls


@pytest.mark.parametrize("kind,durations", [
    ("proactive", [3]), ("primary", [3]), ("retry-after", [2]), ("secondary", [60, 120]),
])
def test_rate_limit_line_precedes_every_sleep_even_inside_throttle_interval(kind, durations):
    clock, stream = FakeClock(), io.StringIO()
    if kind in ("proactive", "primary"):
        limited = reply({}, status=200 if kind == "proactive" else 403, headers={
            "x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 2)})
    elif kind == "retry-after":
        limited = reply({"message": "rate limit"}, status=429, headers={"retry-after": "2"})
    else:
        limited = reply({"message": "secondary rate limit"}, status=403)
    responses = [limited] * len(durations) + [reply({})]
    transport = FakeTransport({REPO: responses})
    announcements = []

    def sleep(seconds):
        timestamp = datetime.fromtimestamp(clock.now + seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        expected = f"rate limit: sleeping {seconds:g} s, until {timestamp}"
        assert stream.getvalue().splitlines()[-1] == expected
        announcements.append(expected)
        clock.sleep(seconds)

    client = Client(transport, TOKEN, clock=clock.clock, sleep=sleep,
                    progress=FetchProgress(clock=clock.clock, stream=stream))
    client.get_json(f"{API}/repos/acme/web")
    if kind == "proactive":
        client.get_json(f"{API}/repos/acme/web")
    assert clock.sleeps == durations
    assert [line for line in stream.getvalue().splitlines() if line.startswith("rate limit:")] == announcements
    assert len(transport.calls) == len(durations) + 1


def test_wait_above_max_wait_has_no_sleep_announcement():
    clock, stream = FakeClock(), io.StringIO()
    transport = FakeTransport({REPO: reply({}, status=429, headers={"retry-after": "2"})})
    client = Client(transport, TOKEN, max_wait=1, clock=clock.clock, sleep=clock.sleep,
                    progress=FetchProgress(clock=clock.clock, stream=stream))
    with pytest.raises(Stopped):
        client.get_json(f"{API}/repos/acme/web")
    assert clock.sleeps == [] and stream.getvalue() == ""


def test_network_retry_backoff_is_not_reported_as_rate_limit():
    clock, stream = FakeClock(), io.StringIO()
    calls = []

    def transport(url, headers):
        calls.append((url, headers))
        if len(calls) == 1:
            raise URLError("offline")
        return reply({})

    client = Client(transport, TOKEN, clock=clock.clock, sleep=clock.sleep,
                    progress=FetchProgress(clock=clock.clock, stream=stream))
    client.get_json(f"{API}/repos/acme/web")
    assert clock.sleeps == [1] and len(calls) == 2
    assert client.requests == 1 and stream.getvalue() == ""


@pytest.mark.parametrize("remaining", ["5000\nINJECTED", "\x1b[31mINJECTED", "\u202eINJECTED",
                                      "INJECTED", "-1", "9" * 100, "１２", None])
def test_progress_never_prints_untrusted_body_header_or_path_text(remaining):
    clock, stream = FakeClock(), io.StringIO()
    hostile = "INJECTED\n\x1b[31m\u202e"
    headers = {"date": hostile, "x-ratelimit-reset": hostile}
    if remaining is not None:
        headers["x-ratelimit-remaining"] = remaining
    response = reply({"title": hostile, "body": hostile}, headers=headers)
    if remaining is None:
        response[1].pop("x-ratelimit-remaining")
    transport = FakeTransport({"GET /INJECTED?private=INJECTED": response})
    progress = FetchProgress(clock=clock.clock, stream=stream)
    client = Client(transport, TOKEN, clock=clock.clock, sleep=clock.sleep, progress=progress)
    clock.now += 5
    client.get_json(f"{API}/INJECTED?private=INJECTED")
    assert stream.getvalue() == "fetching: 0 issues/PRs, 1 requests, unknown remaining\n"


@pytest.mark.parametrize("huge_reset", [False, True])
def test_sleep_progress_uses_numeric_times_never_raw_rate_limit_text(huge_reset):
    clock, stream = FakeClock(), io.StringIO()
    hostile = "INJECTED\n\x1b[31m\u202e"
    headers = {"x-ratelimit-reset": hostile, "retry-after": hostile}
    seconds = 60
    if huge_reset:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(10 ** 25)}
        seconds = 10 ** 25 - NOW + 1
    transport = FakeTransport({REPO: [reply({"message": hostile + " rate limit"}, status=403,
                                           headers=headers), reply({})]})
    client = Client(transport, TOKEN, max_wait=10 ** 30, clock=clock.clock, sleep=clock.sleep,
                    progress=FetchProgress(clock=clock.clock, stream=stream))
    client.get_json(f"{API}/repos/acme/web")
    timestamp = "unknown" if huge_reset else "2026-10-11T04:01:00Z"
    assert stream.getvalue().splitlines()[0] == f"rate limit: sleeping {seconds:g} s, until {timestamp}"
    assert "INJECTED" not in stream.getvalue() and "\x1b" not in stream.getvalue()
    assert clock.sleeps == [seconds]


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_progress_and_sleep_leave_stdout_bytes_unchanged(tmp_path, monkeypatch, dry_run):
    capture = build_capture(item(issue(1, "buyer-acme", "2026-10-05T09:00:00Z", title="Private text")))
    reporter = FetchProgress
    outputs, transports = [], []
    monkeypatch.setattr(github_fetch, "token_from_env", lambda: TOKEN)
    # Run once with the original silent client behavior, then with progress enabled.
    for enabled in (False, True):
        clock = FakeClock()
        routes = routes_from_capture(capture)
        routes[T1] = [reply({"message": "secondary rate limit"}, status=429, headers={"retry-after": "2"}),
                      routes[T1]]
        fake = FakeTransport(routes)

        def transport(url, headers):
            clock.now += 5
            return fake(url, headers)

        real_client = Client

        def make_client(transport, token, **kwargs):
            return real_client(transport, token, max_wait=kwargs["max_wait"], clock=clock.clock,
                               sleep=clock.sleep, progress=kwargs["progress"])

        monkeypatch.setattr(github_fetch, "Client", make_client)
        monkeypatch.setattr(github_fetch, "PersistentTransport", lambda: transport)
        monkeypatch.setattr(github_fetch, "FetchProgress", lambda: reporter(clock=clock.clock) if enabled else None)
        root = tmp_path / str(enabled)
        root.mkdir()
        result = CliRunner().invoke(app, ["github", "sync", str(root), "acme/web"] +
                                   (["--dry-run"] if dry_run else []))
        assert result.exit_code == 0, result.output
        if enabled:
            assert "fetching:" in result.stderr and "rate limit: sleeping 2 s, until" in result.stderr
        else:
            assert result.stderr == ""
        assert "fetching:" not in result.stdout and "rate limit:" not in result.stdout
        outputs.append(result.stdout_bytes)
        transports.append(fake.calls)
        if dry_run:
            assert snapshot(root) == {}
            assert any(json.loads(line)["action"] == "request" for line in result.stdout.splitlines()
                       if line.startswith("{"))
    assert outputs[0] == outputs[1] and transports[0] == transports[1]
