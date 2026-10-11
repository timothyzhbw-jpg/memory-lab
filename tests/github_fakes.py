"""Offline stand-ins for the GitHub REST API: a recording transport, a capture-backed route table and builders."""
from __future__ import annotations

import json
from datetime import datetime
from email.utils import format_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit

API = "https://api.github.com"
DATE = "Sun, 11 Oct 2026 04:00:00 GMT"
NOW = 1791691200.0  # 2026-10-11T04:00:00Z, the Date above
SHA = "54bb654c9e6025347f57900a4a5c2313a96b8035"
USERS = {"buyer-acme": 5001, "lin-eng": 5002, "zhou-pm": 5003, "wu-sales": 5004, "mallory": 6666, "zhou": 4242,
         "jdoe_acme": 77, "github-actions[bot]": 41898282, "ghost": 10137}


def key(url: str) -> str:
    """"GET /path?sorted-query" for the API origin, "GET https://host/path?..." for any other origin."""
    parts = urlsplit(url if "://" in url else API + url)
    query = "&".join(f"{k}={v}" for k, v in sorted(parse_qsl(parts.query, keep_blank_values=True)))
    origin = "" if f"{parts.scheme}://{parts.netloc}" == API else f"{parts.scheme}://{parts.netloc}"
    return f"GET {origin}{parts.path}" + (f"?{query}" if query else "")


def reply(body=None, status=200, headers=None, link=None) -> tuple[int, dict[str, str], bytes]:
    """One response. A body that is not bytes is sent as JSON; 404/410 default to GitHub's error body."""
    if body is None and status in (404, 410):
        body = {"message": "Not Found" if status == 404 else "Gone"}
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    merged = {"date": DATE, "x-ratelimit-remaining": "4999", "x-ratelimit-reset": str(int(NOW) + 3600)}
    merged.update({k.lower(): v for k, v in (headers or {}).items()})
    if link:
        merged["link"] = link
    return status, merged, data


class FakeTransport:
    """Serves routes keyed "GET /path?sorted-query"; records every request and raises on anything unknown.

    A route value is a reply() tuple (served every time), a list of reply() tuples (served once each, in order),
    or any other JSON value (served as a 200 body).
    """

    def __init__(self, routes: dict):
        self.routes = {self._norm(k): _Replies(v) if _is_sequence(v) else v for k, v in routes.items()}
        self.calls: list[tuple[str, dict[str, str]]] = []

    @staticmethod
    def _norm(route: str) -> str:
        method, _, url = route.partition(" ")
        assert method == "GET", route
        return key(url)

    @property
    def keys(self) -> list[str]:
        return [k for k, _ in self.calls]

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        k = key(url)
        self.calls.append((k, dict(headers)))
        if k not in self.routes:
            raise AssertionError(f"unexpected request: {k}")
        value = self.routes[k]
        if isinstance(value, _Replies):
            if not value:
                raise AssertionError(f"route exhausted: {k}")
            return value.pop(0)
        return value if isinstance(value, tuple) else reply(value)


class _Replies(list):
    """A route's remaining one-shot replies."""


def _is_sequence(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, tuple) for v in value)


class FakeClock:
    """Injected clock and sleep: sleeping advances the clock."""

    def __init__(self, now: float = NOW):
        self.now, self.sleeps = now, []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _paged(routes: dict, first: str, later: str, params: dict[str, str], rows: list, page_size: int,
           date: str) -> None:
    chunks = [rows[i:i + page_size] for i in range(0, len(rows), page_size)] or [[]]

    def url(page: int) -> str:
        return f"{API}{later}?{urlencode({**params, 'page': str(page)})}"

    for number, chunk in enumerate(chunks, 1):
        link = None
        if number < len(chunks):
            link = f'<{url(number + 1)}>; rel="next", <{url(len(chunks))}>; rel="last"'
        path = first if number == 1 else later
        page_params = params if number == 1 else {**params, "page": str(number)}
        routes[key(f"{API}{path}?{urlencode(page_params)}")] = reply(chunk, headers={"date": date}, link=link)


def routes_from_capture(capture: dict, page_size: int = 2) -> dict:
    """Serve a capture (as fetch() returns it) as API responses, with Link next URLs under /repositories/<repo_id>/.

    Registers the repo, the issue listing (with and without the capture's `since`), the desc /pulls scan,
    /issues/{n} and every timeline. Skipped items replay their recorded status ("timeline 404", "issue 410").
    Every response carries the capture's `started_at` as its Date, so fetch() reproduces the whole header.
    """
    repo, rid = capture["repo"], capture["repo_id"]
    started = datetime.fromisoformat(capture.get("started_at", "2026-10-11T04:00:00Z").replace("Z", "+00:00"))
    date = format_datetime(started, usegmt=True)

    def ok(body):
        return reply(body, headers={"date": date})

    routes: dict = {f"GET /repos/{repo}": ok({"id": rid, "full_name": repo, "private": False})}
    issues = {item["number"]: item["issue"] for item in capture["items"]}
    for skip in capture.get("skipped", []):
        n, (where, _, status) = skip["number"], skip["reason"].partition(" ")
        if where == "timeline":
            issues[n] = {"number": n, "state": "open"}
            routes[key(f"/repos/{repo}/issues/{n}/timeline?per_page=100")] = reply(status=int(status),
                                                                                    headers={"date": date})
        else:
            routes[key(f"/repos/{repo}/issues/{n}")] = reply(status=int(status), headers={"date": date})
    listing = [issues[n] for n in sorted(issues)]
    base = {"state": "all", "sort": "created", "direction": "asc", "per_page": "100"}
    _paged(routes, f"/repos/{repo}/issues", f"/repositories/{rid}/issues", base, listing, page_size, date)
    if capture.get("since"):
        _paged(routes, f"/repos/{repo}/issues", f"/repositories/{rid}/issues", {**base, "since": capture["since"]},
               listing, page_size, date)
    prs = sorted(({"number": n, "updated_at": o.get("updated_at") or o.get("created_at") or ""}
                  for n, o in issues.items() if "pull_request" in o), key=lambda p: p["updated_at"], reverse=True)
    _paged(routes, f"/repos/{repo}/pulls", f"/repositories/{rid}/pulls",
           {"state": "all", "sort": "updated", "direction": "desc", "per_page": "100"}, prs, page_size, date)
    for item in capture["items"]:
        n = item["number"]
        routes[key(f"/repos/{repo}/issues/{n}")] = ok(item["issue"])
        _paged(routes, f"/repos/{repo}/issues/{n}/timeline", f"/repositories/{rid}/issues/{n}/timeline",
               {"per_page": "100"}, item["timeline"], page_size, date)
    return routes


# --- builders for inline captures ------------------------------------------------------

def user(login: str, id: int | None = None, type: str | None = None) -> dict:
    return {"login": login, "id": USERS[login] if id is None else id,
            "type": type or ("Bot" if login.endswith("[bot]") else "User")}


def _who(actor) -> dict | None:
    return user(actor) if isinstance(actor, str) else actor


def issue(number: int, author, created_at: str, *, title: str = "", body: str | None = "", pr: bool = False,
          updated_at: str | None = None, **fields) -> dict:
    obj = {"number": number, "title": title, "body": body, "user": _who(author), "state": "open",
           "created_at": created_at, "updated_at": updated_at or created_at, "author_association": "MEMBER",
           "performed_via_github_app": None, **fields}
    if pr:
        obj["pull_request"] = {"url": f"{API}/repos/acme/web/pulls/{number}", "merged_at": None}
    return obj


def comment(id: int, author, created_at: str, body: str = "", updated_at: str | None = None, **fields) -> dict:
    who = _who(author)
    return {"event": "commented", "id": id, "actor": who, "user": who, "created_at": created_at,
            "updated_at": updated_at or created_at, "body": body, "author_association": "MEMBER",
            "performed_via_github_app": None, **fields}


def review(id: int, author, state: str, submitted_at: str | None, body: str = "", commit_id: str = SHA,
           **fields) -> dict:
    """A timeline `reviewed` item: lowercase state, `user` and no `actor`."""
    return {"event": "reviewed", "id": id, "user": _who(author), "state": state, "submitted_at": submitted_at,
            "body": body, "commit_id": commit_id, "author_association": "MEMBER", **fields}


def ev(event: str, id: int, actor, created_at: str, **fields) -> dict:
    """An issue event (closed, merged, labeled, review_dismissed, ...); actor=None gives a null actor."""
    return {"event": event, "id": id, "actor": _who(actor), "created_at": created_at, "commit_id": None,
            "performed_via_github_app": None, **fields}


def item(issue_obj: dict, *timeline: dict) -> dict:
    return {"number": issue_obj["number"], "issue": issue_obj, "timeline": list(timeline)}


def build_capture(*items: dict, repo: str = "acme/web", repo_id: int = 1001, mode: str = "full",
                  since: str | None = None, skipped: list | None = None,
                  started_at: str = "2026-10-11T04:00:00Z") -> dict:
    return {"github_capture": 1, "repo": repo, "repo_id": repo_id, "mode": mode, "since": since,
            "started_at": started_at, "complete": True, "requests": 0, "skipped": skipped or [],
            "items": list(items)}
