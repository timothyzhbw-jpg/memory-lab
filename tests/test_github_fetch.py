import http.client
import io
import json
import urllib.request
import urllib.response
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest

from github_fakes import API, DATE, NOW, FakeClock, FakeTransport, build_capture, comment, ev, issue, item, key, \
    reply, review, routes_from_capture
from orgmemory.github_fetch import Client, GitHubError, NoRedirect, Stopped, fetch, token_from_env, urllib_transport

TOKEN = "ghp_TESTTOKEN"
REPO = "GET /repos/acme/web"
LISTING = "GET /repos/acme/web/issues?direction=asc&per_page=100&sort=created&state=all"
PULLS = "GET /repos/acme/web/pulls?direction=desc&per_page=100&sort=updated&state=all"
S = datetime(2026, 10, 9, 11, 55, tzinfo=timezone.utc)



def timeline(n, page=None):
    if page is None:
        return f"GET /repos/acme/web/issues/{n}/timeline?per_page=100"
    return f"GET /repositories/1001/issues/{n}/timeline?page={page}&per_page=100"


def link(route):
    """A Link header whose rel="next" is the URL of a route key."""
    return f'<{API}{route.removeprefix("GET ")}>; rel="next"'


def client(routes, token=TOKEN, max_wait=3600):
    transport, clock = FakeTransport({REPO: {"id": 1001, "full_name": "acme/web"}, **routes}), FakeClock()
    return Client(transport, token, max_wait=max_wait, sleep=clock.sleep, clock=clock.clock), transport, clock


def run(routes, since=None, issues=(), pinned=None, **kw):
    c, transport, clock = client(routes, **kw)
    return fetch(c, "acme/web", since=since, issues=list(issues), pinned_repo_id=pinned), transport, clock


def numbers(capture):
    return [x["number"] for x in capture["items"]]


def limited(reset_in=50, status=403, message="API rate limit exceeded for user ID 1."):
    return reply({"message": message}, status=status,
                 headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + reset_in)})


I1 = issue(1, "buyer-acme", "2026-10-05T09:00:00Z", title="Export", body="Please add export.")
I2 = issue(2, "lin-eng", "2026-10-06T09:00:00Z", pr=True, updated_at="2026-10-07T09:30:00Z")
I3 = issue(3, "mallory", "2026-10-06T10:00:00Z")
C101 = comment(101, "lin-eng", "2026-10-05T10:00:00Z", "Depends on the billing migration.")


# --- request URLs ----------------------------------------------------------------------

def test_request_urls_first_run_has_no_since_and_no_pulls_scan():
    capture, transport, _ = run({LISTING: [I1, I2], timeline(1): [C101], timeline(2): []})
    assert transport.keys == [REPO, LISTING, timeline(1), timeline(2)]
    assert capture == {"github_capture": 1, "repo": "acme/web", "repo_id": 1001, "mode": "full", "since": None,
                       "started_at": "2026-10-11T04:00:00Z", "complete": True, "requests": 4, "skipped": [],
                       "items": [{"number": 1, "issue": I1, "timeline": [C101]},
                                 {"number": 2, "issue": I2, "timeline": []}]}


def test_request_headers_carry_api_version_and_the_token():
    _, transport, _ = run({LISTING: [I1], timeline(1): []})
    for _, headers in transport.calls:
        assert headers == {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                           "User-Agent": "orgmemory/0.2", "Authorization": f"Bearer {TOKEN}"}
    _, anonymous, _ = run({LISTING: [I1], timeline(1): []}, token=None)
    assert all("Authorization" not in headers for _, headers in anonymous.calls)


def test_request_urls_incremental_run_desc_pulls_scan_stops_at_first_older_page():
    listing = LISTING.replace("&sort=", "&since=2026-10-09T11:55:00Z&sort=")
    pulls_2 = "GET /repositories/1001/pulls?direction=desc&page=2&per_page=100&sort=updated&state=all"
    pulls_3 = pulls_2.replace("page=2", "page=3")
    i3 = issue(3, "lin-eng", "2026-10-09T12:00:00Z", pr=True, updated_at="2026-10-10T08:00:00Z")
    i4 = issue(4, "lin-eng", "2026-10-01T09:00:00Z", pr=True, updated_at="2026-10-09T11:55:00Z")
    i5 = issue(5, "lin-eng", "2026-10-02T09:00:00Z", pr=True, updated_at="2026-10-10T12:00:00Z")
    routes = {
        listing: [I1, i3],
        PULLS: reply([{"number": 5, "updated_at": "2026-10-10T12:00:00Z"},
                      {"number": 3, "updated_at": "2026-10-10T08:00:00Z"}], link=link(pulls_2)),
        pulls_2: reply([{"number": 4, "updated_at": "2026-10-09T11:55:00Z"},
                        {"number": 2, "updated_at": "2026-10-09T11:54:59Z"}], link=link(pulls_3)),
        "GET /repos/acme/web/issues/4": i4, "GET /repos/acme/web/issues/5": i5,
        timeline(1): [], timeline(3): [], timeline(4): [], timeline(5): [],
    }
    since = S.replace(microsecond=500000)  # truncated to the second, for the query and the scan alike
    capture, transport, _ = run(routes, since=since, pinned=1001)
    assert transport.keys == [REPO, listing, PULLS, pulls_2, timeline(1), timeline(3),
                              "GET /repos/acme/web/issues/4", timeline(4), "GET /repos/acme/web/issues/5", timeline(5)]
    assert pulls_3 not in transport.keys
    assert (capture["mode"], capture["since"], capture["complete"]) == ("incremental", "2026-10-09T11:55:00Z", True)
    assert numbers(capture) == [1, 3, 4, 5]
    assert capture["items"][2]["issue"] == i4


def test_request_urls_targeted_run_fetches_only_the_named_numbers():
    i5 = issue(5, "lin-eng", "2026-10-02T09:00:00Z")
    routes = {"GET /repos/acme/web/issues/5": i5, timeline(5): [C101]}
    capture, transport, _ = run(routes, since=S, issues=[5, 5], pinned=1001)
    assert transport.keys == [REPO, "GET /repos/acme/web/issues/5", timeline(5)]
    assert (capture["mode"], capture["since"], numbers(capture)) == ("targeted", None, [5])

    capture, transport, _ = run({"GET /repos/acme/web/issues/9": reply(status=404)}, issues=[9])
    assert transport.keys == [REPO, "GET /repos/acme/web/issues/9"]
    assert (capture["items"], capture["skipped"]) == ([], [{"number": 9, "reason": "issue 404"}])


@pytest.mark.parametrize("bad", [
    {"repo": "acme"}, {"repo": "acme/.."}, {"repo": "-acme/web"}, {"repo": "acme/web/x"}, {"repo": "acme/we b"},
    {"since": datetime(2026, 10, 9, 11, 55)}, {"issues": [0]}, {"issues": ["5"]},
])
def test_invalid_arguments_are_refused_before_any_request(bad):
    c, transport, _ = client({})
    args = {"repo": "acme/web", "since": None, "issues": [], **bad}
    repo = args.pop("repo")
    with pytest.raises(ValueError):
        fetch(c, repo, pinned_repo_id=None, **args)
    assert transport.calls == []


def test_repository_checks_fail_closed():
    c, transport, _ = client({REPO: reply(status=404)})
    with pytest.raises(GitHubError, match="not found"):
        fetch(c, "acme/web", since=None, issues=[], pinned_repo_id=None)
    for meta, pinned in (({"id": 1002, "full_name": "acme/web"}, 1001), ({"id": 1001, "full_name": "acme/www"}, None),
                         ({"id": "1001", "full_name": "acme/web"}, None)):
        c, transport, _ = client({REPO: meta})
        with pytest.raises(GitHubError, match="repository identity changed|malformed"):
            fetch(c, "acme/web", since=None, issues=[], pinned_repo_id=pinned)
        assert transport.keys == [REPO]


@pytest.mark.parametrize("routes", [
    {LISTING: {"message": "not a list"}},
    {LISTING: [{"number": "1"}]},
    {LISTING: reply(b"<html>")},
    {LISTING: reply(b'[{"number": NaN}]')},
    {LISTING: reply(b'[{"number": 1, "number": 2}]')},
    {LISTING: reply(b'[{"number": 1, "user": {"login": "a", "login": "b"}}]')},
    {"GET /repos/acme/web/issues/5": {"number": 6}},
])
def test_malformed_api_data_is_an_error(routes):
    with pytest.raises(GitHubError, match="malformed"):
        run({**routes, timeline(1): []}, issues=[5] if "GET /repos/acme/web/issues/5" in routes else [])


def test_started_at_is_the_first_server_date_and_requests_are_counted():
    later = {"date": "Sun, 11 Oct 2026 04:07:00 GMT"}
    capture, transport, _ = run({LISTING: reply([I1], headers=later), timeline(1): reply([], headers=later)})
    assert capture["started_at"] == "2026-10-11T04:00:00Z"
    assert capture["requests"] == len(transport.calls) == 3


def test_started_at_must_come_from_a_response_before_the_listing():
    meta = json.dumps({"id": 1001, "full_name": "acme/web"}).encode()
    c, transport, _ = client({REPO: (200, {"date": "not a date"}, meta), LISTING: [I1]})
    with pytest.raises(GitHubError, match="no valid Date header"):
        fetch(c, "acme/web", since=None, issues=[], pinned_repo_id=None)
    assert transport.keys == [REPO]

    retried = [(502, {}, b""), reply({"id": 1001, "full_name": "acme/web"}, headers={
        "date": "Sun, 11 Oct 2026 04:00:05 GMT"})]
    capture, _, _ = run({REPO: retried, LISTING: [], timeline(1): []})
    assert (capture["started_at"], capture["requests"]) == ("2026-10-11T04:00:05Z", 3)


# --- pagination ------------------------------------------------------------------------

FIRST_PAGE = "GET /repos/acme/web/issues?state=all"
CLICK_NEXT_LINK = (
    '<https://api.github.com/repositories/19103692/issues?state=all&sort=updated&direction=asc'
    '&since=2026-09-01T00%3A00%3A00Z&per_page=100'
    '&after=Y3Vyc29yOnYyOpLPAAABoMaGLAjPAAAAAUAtgH8%3D&page=2>; rel="next"'
)


@pytest.mark.parametrize("named_path", [False, True])
def test_pagination_accepts_real_click_cursor_link(named_path):
    header = CLICK_NEXT_LINK
    if named_path:
        header = header.replace("/repositories/19103692/", "/repos/pallets/click/")
    params = {"state": "all", "sort": "updated", "direction": "asc",
              "since": "2026-09-01T00:00:00Z", "per_page": "100"}
    first = f"GET /repos/pallets/click/issues?{urlencode(params)}"
    second = "GET " + header.split("<", 1)[1].split(">", 1)[0]
    c, transport, _ = client({first: reply([{"n": 1}], link=header), second: [{"n": 2}]})
    assert list(c.pages("/repos/pallets/click/issues", params, repo="pallets/click", repo_id=19103692)) == [
        [{"n": 1}], [{"n": 2}]]
    assert transport.keys == [key(first.removeprefix("GET ")), key(second.removeprefix("GET "))]


@pytest.mark.parametrize("query", [
    "after=A", "after=" + "A" * 512, "after=AZaz09%2B%2F%3D_-", "after=A&page=0002",
])
def test_pagination_accepts_bounded_cursor_with_optional_page(query):
    second = f"GET /repositories/1001/issues?state=all&{query}"
    c, transport, _ = client({FIRST_PAGE: reply([{"n": 1}], link=link(second)), second: [{"n": 2}]})
    assert list(c.pages("/repos/acme/web/issues", {"state": "all"}, repo="acme/web", repo_id=1001)) == [
        [{"n": 1}], [{"n": 2}]]
    assert transport.keys == [FIRST_PAGE, key(second.removeprefix("GET "))]


@pytest.mark.parametrize("query", [
    "state=open&after=A&page=2", "state=all&sort=updated&after=A&page=2",
    "after=A&page=2", "state=all&state=all&after=A&page=2",
    "state=all&after=A&after=B&page=2", "state=all&after=A&%61fter=A&page=2",
    "state=all&after=&page=2", "state=all&after=" + "A" * 513 + "&page=2",
    "state=all&after=A%21&page=2", "state=all&after=A%20B&page=2",
    "state=all&after=A+B&page=2", "state=all&after=A%0AB&page=2",
    "state=all&after=%252F&page=2", "state=all&after=%C3%A9&page=2",
    "state=all&after=A&page=0", "state=all&after=A&page=2&page=3",
    "state=all&before=A&page=2",
])
def test_pagination_refuses_invalid_cursor_or_changed_filters(query):
    second = f"GET /repositories/1001/issues?{query}"
    c, transport, _ = client({FIRST_PAGE: reply([], link=link(second)), second: []})
    pages = c.pages("/repos/acme/web/issues", {"state": "all"}, repo="acme/web", repo_id=1001)
    assert next(pages) == []
    with pytest.raises(GitHubError, match="Link next URL"):
        next(pages)
    assert transport.keys == [FIRST_PAGE]


@pytest.mark.parametrize("where", ["json-value", "json-key", "escaped-json", "header-value", "header-name",
                                   "encoded-header-name"])
@pytest.mark.parametrize("status", [200, 403])
def test_percent_encoded_credential_reflection_is_rejected(where, status):
    encoded = ''.join(f'%{ord(ch):02X}' for ch in TOKEN)
    body, headers = {"safe": "ok"}, {}
    if where == "json-value":
        body = {"nested": [{"body": f"prefix {encoded} suffix"}]}
    elif where == "json-key":
        body = {encoded: "ok"}
    elif where == "escaped-json":
        body = ('{"body":"' + ''.join('\\u%04x' % ord(ch) for ch in encoded) + '"}').encode()
    elif where == "header-value":
        headers = {"X-Reflected": encoded}
    else:
        headers = {TOKEN if where == "header-name" else encoded: "ok"}
    c, transport, clock = client({REPO: reply(body, status=status, headers=headers)})
    with pytest.raises(GitHubError, match="reflected the credential") as raised:
        c.get_json(f"{API}/repos/acme/web")
    assert TOKEN not in str(raised.value) and encoded not in str(raised.value)
    assert transport.keys == [REPO] and clock.sleeps == []


@pytest.mark.parametrize("path,target", [
    ("issues", "contents/private"), ("issues", "pulls"), ("pulls", "issues"),
    ("issues/5/timeline", "issues/6/timeline"), ("issues/5/timeline", "issues/5/comments"),
    ("issues/5/comments", "issues/6/comments"), ("pulls/5/reviews", "pulls/6/reviews"),
    ("issues", "issues/5/timeline"), ("issues", "issues/"), ("issues", "%69ssues"),
])
@pytest.mark.parametrize("prefix", ["/repos/acme/web/", "/repositories/1001/"])
def test_pagination_next_cannot_leave_requested_listing(path, target, prefix):
    first = f"GET /repos/acme/web/{path}?per_page=100"
    escaped = f"GET {prefix}{target}?page=2&per_page=100"
    c, transport, _ = client({first: reply([], link=link(escaped)), escaped: []})
    pages = c.pages(f"/repos/acme/web/{path}", {"per_page": "100"}, repo="acme/web", repo_id=1001)
    assert next(pages) == []
    with pytest.raises(GitHubError, match="Link next URL"):
        next(pages)
    assert transport.keys == [first]


@pytest.mark.parametrize("query", [
    "page=2", "page=2&state=open", "page=2&state=all&sort=updated",
    "page=2&state=all&state=open", "page=2&page=3&state=all", "page=0&state=all",
    "page=-1&state=all", "page=abc&state=all", "state=all", "page=2&state=all#fragment",
])
def test_pagination_next_preserves_query_semantics(query):
    target = f"GET /repositories/1001/issues?{query}"
    c, transport, _ = client({FIRST_PAGE: reply([], link=link(target)), target: []})
    pages = c.pages("/repos/acme/web/issues", {"state": "all"}, repo="acme/web", repo_id=1001)
    assert next(pages) == []
    with pytest.raises(GitHubError, match="Link next URL"):
        next(pages)
    assert transport.keys == [FIRST_PAGE]


@pytest.mark.parametrize("path", ["issues", "pulls", "issues/5/timeline", "issues/5/comments", "pulls/5/reviews"])
def test_pagination_accepts_same_listing_and_equivalent_queries(path):
    params = {"per_page": "100", "since": "2026-10-09T11:55:00Z"}
    first = f"GET /repos/acme/web/{path}?{urlencode(params)}"
    second = f"GET /repositories/1001/{path}?since=2026-10-09T11%3a55%3a00Z&page=2&per_page=100"
    c, transport, _ = client({first: reply([{"n": 1}], link=link(second)), second: [{"n": 2}]})
    assert list(c.pages(f"/repos/acme/web/{path}", params, repo="acme/web", repo_id=1001)) == [
        [{"n": 1}], [{"n": 2}]]
    assert transport.keys == [key(first.removeprefix("GET ")), key(second.removeprefix("GET "))]


def test_pagination_link_validation_follows_repo_links():
    page_2 = "GET /repositories/1001/issues?page=2&state=all"
    page_3 = "GET /repos/Acme/Web/issues?page=3&state=all"
    c, transport, _ = client({FIRST_PAGE: reply([{"n": 1}], link=link(page_2)),
                              page_2: reply([{"n": 2}], link=f'<{API}/x>; rel="last", ' + link(page_3)),
                              page_3: reply([{"n": 3}])})
    pages = list(c.pages("/repos/acme/web/issues", {"state": "all"}, repo="acme/web", repo_id=1001))
    assert pages == [[{"n": 1}], [{"n": 2}], [{"n": 3}]]
    assert transport.keys == [FIRST_PAGE, page_2, page_3]


@pytest.mark.parametrize("next_url", [
    "https://evil.example/repositories/1001/issues?page=2",
    "http://api.github.com/repositories/1001/issues?page=2",
    "https://api.github.com:8443/repositories/1001/issues?page=2",
    "https://user@api.github.com/repositories/1001/issues?page=2",
    "https://api.github.com.evil.example/repositories/1001/issues?page=2",
    "https://api.github.com/repos/acme/other/issues?page=2",
    "https://api.github.com/repos/acme/web-evil/issues?page=2",
    "https://api.github.com/repositories/1002/issues?page=2",
    "https://api.github.com/repositories/10011/issues?page=2",
    "https://api.github.com/repositories/1001/../1002/issues?page=2",
    "https://api.github.com/repositories/1001/%2e%2e/1002/issues?page=2",
    "/repositories/1001/issues?page=2",
    "https://api.github.com/reposi\ttories/1001/issues?page=2",
    "https://api.github.com/repositories/1001/issues?page=2\r\nX-Evil: 1",
])
def test_pagination_link_validation_refuses_links_outside_the_repo(next_url):
    first = reply([{"number": 1}], link=f'<{next_url}>; rel="next"')
    c, transport, _ = client({FIRST_PAGE: first})
    pages = c.pages("/repos/acme/web/issues", {"state": "all"}, repo="acme/web", repo_id=1001)
    assert next(pages) == [{"number": 1}]
    with pytest.raises(GitHubError, match="Link next URL") as raised:
        next(pages)
    assert transport.keys == [FIRST_PAGE]
    assert "evil" not in str(raised.value) and "1002" not in str(raised.value)


def test_pagination_link_validation_fails_the_whole_fetch():
    routes = {LISTING: [I1], timeline(1): reply([], link='<https://evil.example/t?page=2>; rel="next"')}
    with pytest.raises(GitHubError, match="Link next URL"):
        run(routes)


def test_pagination_link_validation_stops_at_page_limit():
    looping = "GET /repositories/1001/issues?page=2"
    c, transport, _ = client({"GET /repos/acme/web/issues": reply([], link=link(looping)),
                              looping: reply([], link=link(looping))})
    with pytest.raises(GitHubError, match="more than 1000 pages"):
        list(c.pages("/repos/acme/web/issues", {}, repo="acme/web", repo_id=1001))
    assert len(transport.calls) == 1000


# --- the real urllib transport, on a fake HTTPS handler (no sockets) -------------------

class FakeHTTPS(urllib.request.HTTPSHandler):
    """Answers https requests from a table (a list is served once per entry) and keeps every Request it saw."""

    def __init__(self, table):
        super().__init__()
        self.table, self.seen, self.readers = table, [], []

    def https_open(self, req):
        self.seen.append(req)
        answer = self.table[req.full_url]
        answer = answer.pop(0) if isinstance(answer, list) else answer
        if isinstance(answer, Exception):
            raise answer
        status, headers, body = answer
        message = http.client.HTTPMessage()
        for name, value in {"Date": DATE, **headers}.items():
            message[name] = value
        reader = TrackingBody(body)
        self.readers.append(reader)
        response = urllib.response.addinfourl(reader, message, req.full_url, status)
        response.msg = http.client.responses.get(status, "")
        return response


class TrackingBody(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.bytes_read = 0

    def read(self, size=-1):
        data = super().read(size)
        self.bytes_read += len(data)
        return data


@pytest.mark.parametrize("status", [200, 302, 403, 404, 410, 502])
def test_urllib_response_size_cap_stops_reading_early(status):
    cap = 8 * 1024 * 1024
    body = b'x' * (cap * 2)
    url = f"{API}/repos/acme/web"
    transport, https = real_transport({url: (status, {}, body)})
    clock = FakeClock()
    with pytest.raises(GitHubError, match="response body exceeds") as raised:
        Client(transport, TOKEN, sleep=clock.sleep, clock=clock.clock).get(url)
    assert raised.value.status is None
    assert https.readers[0].bytes_read == cap + 1 < len(body)
    assert https.readers[0].closed and len(https.seen) == 1 and clock.sleeps == []


def test_urllib_response_at_size_cap_is_accepted():
    body = b'x' * (8 * 1024 * 1024)
    url = f"{API}/repos/acme/web"
    transport, https = real_transport({url: (200, {}, body)})
    assert transport(url, {})[2] == body
    assert https.readers[0].bytes_read == len(body) and https.readers[0].closed


def real_transport(table):
    https = FakeHTTPS(table)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect, https)
    return urllib_transport(opener), https


def test_authorization_only_to_api_origin():
    transport, https = real_transport({f"{API}/repos/acme/web": (200, {}, b'{"id": 1001}')})
    assert Client(transport, TOKEN).get_json(f"{API}/repos/acme/web")[:2] == (200, {"id": 1001})
    sent = https.seen[0]
    assert sent.unredirected_hdrs["Authorization"] == f"Bearer {TOKEN}"
    assert "Authorization" not in sent.headers
    assert (sent.get_header("Accept"), sent.get_header("X-github-api-version"), sent.get_header("User-agent")) == \
        ("application/vnd.github+json", "2022-11-28", "orgmemory/0.2")
    # CPython's redirect handler copies req.headers to the new host; the unredirected token is not among them.
    copy = urllib.request.HTTPRedirectHandler().redirect_request(sent, io.BytesIO(), 302, "Found", {},
                                                                  "https://evil.example/")
    assert not copy.has_header("Authorization")

    Client(transport, None).get_json(f"{API}/repos/acme/web")
    assert not https.seen[1].has_header("Authorization")


@pytest.mark.parametrize("url", ["https://evil.example/x", "https://api.github.com.evil.example/x",
                                 "https://api.github.com:443/x", "https://user@api.github.com/x"])
def test_token_never_sent_to_another_origin(url):
    transport, https = real_transport({url: (200, {}, b"{}")})
    transport(url, {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"})
    assert not https.seen[0].has_header("Authorization")
    c, fake, _ = client({f"GET {url}": {}})
    c.get_json(url)
    assert "Authorization" not in fake.calls[0][1]


def test_urllib_transport_returns_errors_and_redirects_without_following():
    repo = f"{API}/repos/acme/web"
    transport, https = real_transport({
        repo: (302, {"Location": "https://evil.example/steal"}, b""),
        f"{API}/repos/acme/gone": (404, {"X-RateLimit-Remaining": "41"}, b'{"message": "Not Found"}'),
    })
    status, headers, body = transport(repo, {"Authorization": f"Bearer {TOKEN}"})
    assert (status, headers["location"], body) == (302, "https://evil.example/steal", b"")
    assert transport(f"{API}/repos/acme/gone", {})[:2] == (404, {"date": DATE, "x-ratelimit-remaining": "41"})
    with pytest.raises(GitHubError, match="refusing a redirect away"):
        Client(transport, TOKEN).get_json(repo)
    assert [r.full_url for r in https.seen] == [repo, f"{API}/repos/acme/gone", repo]
    with pytest.raises(ValueError, match="redirects"):
        urllib_transport(urllib.request.build_opener())


def test_network_errors_are_retried_like_server_errors():
    url = f"{API}/repos/acme/web"
    transport, https = real_transport({url: [ConnectionResetError(), TimeoutError(), (200, {}, b"{}")]})
    clock = FakeClock()
    c = Client(transport, TOKEN, sleep=clock.sleep, clock=clock.clock)
    assert c.get_json(url)[:2] == (200, {})
    assert (clock.sleeps, c.requests, len(https.seen)) == ([1, 2], 1, 3)


# --- redirects -------------------------------------------------------------------------

@pytest.mark.parametrize("status", [301, 308])
def test_redirect_policy_permanent_redirect_fails_closed(status):
    moved = reply({"message": "Moved Permanently"}, status=status, headers={"location": f"{API}/repositories/1001"})
    c, transport, _ = client({REPO: moved})
    with pytest.raises(GitHubError, match="moved permanently"):
        fetch(c, "acme/web", since=None, issues=[], pinned_repo_id=None)
    assert transport.keys == [REPO]


def test_redirect_policy_same_origin_307_is_followed_with_auth():
    c, transport, _ = client({"GET /repos/acme/web/issues/5": reply(status=307, headers={
        "location": "/repositories/1001/issues/5"}), "GET /repositories/1001/issues/5": {"number": 5}})
    assert c.get_json(f"{API}/repos/acme/web/issues/5")[:2] == (200, {"number": 5})
    assert transport.keys == ["GET /repos/acme/web/issues/5", "GET /repositories/1001/issues/5"]
    assert transport.calls[1][1]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("location", ["https://evil.example/repos/acme/web", "http://api.github.com/repos/acme/web",
                                      "//evil.example/x", "https://api.github.com/repo\ts/acme/web", None])
def test_redirect_policy_cross_origin_or_missing_location_is_refused(location):
    headers = {"location": location} if location else {}
    c, transport, _ = client({REPO: reply(status=302, headers=headers)})
    with pytest.raises(GitHubError, match="refusing a redirect"):
        fetch(c, "acme/web", since=None, issues=[], pinned_repo_id=None)
    assert transport.keys == [REPO]


def test_redirect_policy_chains_are_bounded():
    c, transport, _ = client({"GET /a": reply(status=307, headers={"location": "/b"}),
                              "GET /b": reply(status=302, headers={"location": "/a"})})
    with pytest.raises(GitHubError, match="more than 3 redirects"):
        c.get_json(f"{API}/a")
    assert transport.keys == ["GET /a", "GET /b", "GET /a", "GET /b"]


# --- rate limits -----------------------------------------------------------------------

def test_rate_limits_remaining_zero_waits_until_reset_before_the_next_request():
    c, transport, clock = client({REPO: limited(status=200, reset_in=100), "GET /repos/acme/web/issues/1": {}})
    c.get_json(f"{API}/repos/acme/web")
    assert clock.sleeps == []
    c.get_json(f"{API}/repos/acme/web/issues/1")
    assert clock.sleeps == [101] and len(transport.calls) == 2


def test_rate_limits_primary_403_waits_for_reset_then_retries():
    c, transport, clock = client({REPO: [limited(reset_in=50), reply({"id": 1001})]})
    assert c.get_json(f"{API}/repos/acme/web")[:2] == (200, {"id": 1001})
    assert (clock.sleeps, c.requests) == ([51], 2)


@pytest.mark.parametrize("status", [403, 429])
def test_rate_limits_retry_after_is_honored(status):
    throttled = reply({"message": "You have exceeded a secondary rate limit."}, status=status,
                      headers={"retry-after": "7"})
    c, _, clock = client({REPO: [throttled, reply({"id": 1001})]})
    assert c.get_json(f"{API}/repos/acme/web")[1] == {"id": 1001}
    assert clock.sleeps == [7]


def test_rate_limits_secondary_403_backs_off_60_then_120():
    secondary = reply({"message": "You have exceeded a secondary rate limit. Please wait a few minutes."}, status=403)
    c, _, clock = client({REPO: [secondary, secondary, reply({"id": 1001})]})
    c.get_json(f"{API}/repos/acme/web")
    assert clock.sleeps == [60, 120]


def test_rate_limits_plain_403_fails_at_once_without_leaking_query_or_token():
    url = f"{API}/repos/acme/web/issues?since=2026-10-09T11:55:00Z&state=all"
    denied = reply({"message": "Resource not accessible by personal access token\u202e"}, status=403)
    c, transport, clock = client({key(url): denied})
    with pytest.raises(GitHubError) as raised:
        c.get_json(url)
    message = str(raised.value)
    assert message == 'GET /repos/acme/web/issues: 403 "Resource not accessible by personal access token\\u202e"'
    assert (len(transport.calls), clock.sleeps) == (1, [])
    for leak in (TOKEN, "since", "state=all", "\u202e"):
        assert leak not in message


@pytest.mark.parametrize("status", [401, 422])
def test_other_client_errors_are_permanent(status):
    c, transport, _ = client({LISTING: reply({"message": "Bad credentials"}, status=status)})
    with pytest.raises(GitHubError, match=f"{status} \"Bad credentials\""):
        fetch(c, "acme/web", since=None, issues=[], pinned_repo_id=None)
    assert transport.keys == [REPO, LISTING]


def test_rate_limits_wait_above_max_wait_gives_partial_capture_with_only_complete_items():
    routes = {LISTING: [I1, I2, I3], timeline(1): [C101],
              timeline(2): reply([C101], link=link(timeline(2, page=2))),
              timeline(2, page=2): limited(reset_in=7200)}
    capture, transport, clock = run(routes, max_wait=3600)
    assert (capture["complete"], numbers(capture), clock.sleeps) == (False, [1], [])
    assert transport.keys == [REPO, LISTING, timeline(1), timeline(2), timeline(2, page=2)]
    assert capture["requests"] == 5 and capture["started_at"] == "2026-10-11T04:00:00Z"


def test_rate_limits_proactive_wait_above_max_wait_stops_before_sending():
    exhausted = reply([I1], headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 7200)})
    capture, transport, clock = run({LISTING: exhausted}, max_wait=3600)
    assert (capture["complete"], capture["items"], clock.sleeps) == (False, [], [])
    assert transport.keys == [REPO, LISTING]


def test_rate_limits_retry_after_above_max_wait_stops():
    throttled = reply({"message": "secondary rate limit"}, status=429, headers={"retry-after": "601"})
    capture, _, clock = run({LISTING: [I1], timeline(1): throttled}, max_wait=600)
    assert (capture["complete"], capture["items"], clock.sleeps) == (False, [], [])


def test_rate_limits_on_the_repo_request_are_an_error_not_a_partial_capture():
    c, _, _ = client({REPO: limited(reset_in=7200)}, max_wait=60)
    with pytest.raises(GitHubError, match="nothing was fetched"):
        fetch(c, "acme/web", since=None, issues=[], pinned_repo_id=None)


def test_rate_limits_retries_are_bounded():
    secondary = reply({"message": "secondary rate limit"}, status=403)
    c, transport, clock = client({REPO: secondary}, max_wait=10 ** 6)
    with pytest.raises(Stopped):
        c.get_json(f"{API}/repos/acme/web")
    assert (len(transport.calls), clock.sleeps) == (5, [60, 120, 240, 480, 960])


# --- server errors and missing timelines -----------------------------------------------

def test_server_errors_retry_then_partial():
    routes = {LISTING: [I1, I2, I3], timeline(1): [C101], timeline(2): [reply(status=502)] * 4}
    capture, transport, clock = run(routes)
    assert clock.sleeps == [1, 2, 4]
    assert (capture["complete"], numbers(capture)) == (False, [1])
    assert transport.keys.count(timeline(2)) == 4 and timeline(3) not in transport.keys


def test_server_error_recovers_within_retries():
    capture, _, clock = run({LISTING: [I1], timeline(1): [reply(status=502), reply(status=503), reply([C101])]})
    assert (clock.sleeps, capture["complete"], capture["items"][0]["timeline"]) == ([1, 2], True, [C101])


@pytest.mark.parametrize("status", [404, 410])
def test_timeline_404_skips_item(status):
    i14 = issue(14, "buyer-acme", "2026-10-08T09:00:00Z")
    capture, _, _ = run({LISTING: [I1, i14], timeline(1): [C101], timeline(14): reply(status=status)})
    assert capture["complete"] is True
    assert numbers(capture) == [1]
    assert capture["skipped"] == [{"number": 14, "reason": f"timeline {status}"}]


# --- the fakes serve a capture faithfully ----------------------------------------------

def capture_fixture(since=None):
    pr = issue(2, "lin-eng", "2026-10-06T09:00:00Z", pr=True, updated_at="2026-10-10T09:00:00Z")
    return build_capture(
        item(I1, C101, comment(102, "buyer-acme", "2026-10-05T11:00:00Z", "Thanks"),
             ev("closed", 103, "zhou-pm", "2026-10-07T09:00:00Z", state_reason="not_planned")),
        item(pr, review(201, "zhou-pm", "changes_requested", "2026-10-06T10:00:00Z", "Wait for billing."),
             ev("review_requested", 202, "lin-eng", "2026-10-06T11:00:00Z", requested_reviewer={
                 "login": "zhou-pm", "id": 5003, "type": "User"}),
             review(203, "zhou-pm", "approved", "2026-10-07T09:00:00Z"),
             ev("merged", 204, "zhou-pm", "2026-10-07T10:00:00Z", commit_id="54bb654c9e6025347f57900a4a5c2313a96b8035"),
             ev("closed", 205, "zhou-pm", "2026-10-07T10:00:00Z")),
        item(I3),
        mode="incremental" if since else "full", since=since, skipped=[{"number": 14, "reason": "timeline 404"}],
        started_at="2026-10-09T12:00:00Z")


@pytest.mark.parametrize("since", [None, "2026-10-09T11:55:00Z"])
def test_fetch_round_trip_over_routes_from_capture(since):
    capture = capture_fixture(since)
    c, transport, _ = client(routes_from_capture(capture, page_size=2))
    when = datetime.fromisoformat(since.replace("Z", "+00:00")) if since else None
    got = fetch(c, "acme/web", since=when, issues=[], pinned_repo_id=1001)
    assert {**got, "requests": 0} == capture
    assert got["requests"] == len(transport.calls)
    assert timeline(1, page=2) in transport.keys and timeline(2, page=3) in transport.keys
    assert (PULLS in transport.keys) == bool(since)


def test_fake_transport_refuses_unknown_requests():
    c, transport, _ = client({})
    with pytest.raises(AssertionError, match="unexpected request: GET /repos/acme/web/issues/1"):
        c.get_json(f"{API}/repos/acme/web/issues/1")


# --- token -----------------------------------------------------------------------------

def test_token_from_env():
    assert token_from_env({"GITHUB_TOKEN": "a", "GH_TOKEN": "b"}) == "a"
    assert token_from_env({"GITHUB_TOKEN": "  ", "GH_TOKEN": "b"}) == "b"
    assert token_from_env({"PATH": "/bin"}) is None


def test_token_that_cannot_be_a_header_is_refused_without_echo():
    with pytest.raises(GitHubError) as raised:
        Client(FakeTransport({}), "ghp_x\r\nX-Evil: 1")
    assert "ghp_x" not in str(raised.value)
