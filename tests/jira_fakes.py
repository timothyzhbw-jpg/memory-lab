"""Builders for small synthetic Jira Cloud search captures (the shapes of GET /rest/api/3/search?expand=changelog)."""
from __future__ import annotations

SITE = "https://acme.atlassian.net"
ACCOUNTS = {
    "buyer": "qm:4a6e0c2f-8d1b-4e3a-9c5f-7b2d1e0a3c4b:5e7f9a1b-2c3d-4e5f-8a9b-0c1d2e3f4a5b",
    "lin": "5f8e7d6c5b4a39281706f5e4",
    "zhou": "61a0c3e5f2b1d40069a1b2c3",
    "wu": "712020:3c9d1e2f-4a5b-4c6d-8e7f-90a1b2c3d4e5",
    "mallory": "5d3c2b1a0f9e8d7c6b5a4f3e",
    "automation": "557058:f58131cb-b67d-43c7-b30d-6b58d40bd077",
    "former": "5a1b2c3d4e5f60718293a4b5",
}
KINDS = {"buyer": "customer", "automation": "app"}
T0, T1, T2, T3, T4, T5 = [f"2026-10-0{n}T09:00:00.000+0000" for n in range(4, 10)]


def aid(name: str) -> str:
    return f"jira:{ACCOUNTS[name]}"


def user(name: str, **fields) -> dict:
    """A Jira user object; fields override (e.g. accountType, active, displayName)."""
    return {"accountId": ACCOUNTS.get(name, name), "displayName": name.title(), "active": True,
            "accountType": KINDS.get(name, "atlassian"), **fields}


def doc(*lines: str) -> dict:
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in lines]}


def status(frm: str, to: str, **fields) -> dict:
    return {"field": "status", "fieldtype": "jira", "fieldId": "status", "from": frm, "fromString": f"S{frm}",
            "to": to, "toString": f"S{to}", **fields}


def history(hid: int, who, when: str, *items: dict) -> dict:
    """`who` is a builder name, a user object, or None for a history without an author."""
    h = {"id": str(hid), "created": when, "items": list(items)}
    if who is not None:
        h["author"] = user(who) if isinstance(who, str) else who
    return h


def comment(cid: int, who, when: str, body="A comment.", **fields) -> dict:
    author = user(who) if isinstance(who, str) else who
    return {"id": str(cid), "author": author, "updateAuthor": author, "body": doc(body) if isinstance(body, str)
            else body, "created": when, "updated": when, **fields}


def issue(number: int, *, creator="lin", created=T0, current: str | None = None, histories=(), comments=(),
          project: str = "ACME", **fields) -> dict:
    """An issue whose current status defaults to where its last status change went (or 10000)."""
    histories = list(histories)
    if current is None:
        moves = [i["to"] for h in histories for i in h["items"] if i.get("field") == "status"
                 and i.get("fieldtype") == "jira"]
        current = moves[-1] if moves else "10000"
    issue_id = str(10000 + number)
    return {"id": issue_id, "self": f"{SITE}/rest/api/3/issue/{issue_id}", "key": f"{project}-{number}",
            "changelog": {"startAt": 0, "maxResults": 100, "total": len(histories), "histories": histories},
            "fields": {"summary": f"Issue {number}", "description": None, "created": created,
                       "creator": user(creator) if isinstance(creator, str) else creator,
                       "reporter": user(creator) if isinstance(creator, str) else creator,
                       "status": {"id": current, "name": f"S{current}"},
                       "comment": {"comments": list(comments), "maxResults": 100, "total": len(comments),
                                   "startAt": 0}, **fields}}


def page(*issues: dict, start: int = 0, total: int | None = None) -> dict:
    return {"startAt": start, "maxResults": 50, "total": len(issues) + start if total is None else total,
            "issues": list(issues)}


def capture(*pages: dict, at: str = "2026-10-09T12:00:00Z", site: str = SITE) -> dict:
    return {"jira_capture": 1, "site": site, "captured_at": at, "pages": list(pages)}


def single(*histories: dict, comments=(), **kw) -> dict:
    """A capture of one issue, ACME-1, created by lin."""
    return capture(page(issue(1, histories=histories, comments=comments, **kw)))
