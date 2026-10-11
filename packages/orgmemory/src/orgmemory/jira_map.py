"""Pure mapping from a saved Jira Cloud search capture to orgmemory events and evidence captures.

No I/O and no clock: the result depends only on the capture, the events and evidence ids already in the
dataset, and the user's jira.json. Text is never parsed for commands. Each event's actor comes from one
structured field, and a status transition becomes a decision only when jira.json maps its target status id.
See docs/design/jira-import.md.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from memcore import EvidenceSpan, ManifestEntry, make_span, sha256_bytes

from .ingest import Capture, render, text_span
from .models import Event

SITE = re.compile(r"https://[a-z0-9][a-z0-9-]{0,62}\.atlassian\.net")
_KEY = r"[A-Z][A-Z0-9_]{0,49}-[1-9][0-9]{0,17}"
_NUM = r"[1-9][0-9]{0,17}"
KEY = re.compile(_KEY)
NUM = re.compile(_NUM)
ACCOUNT = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,127}")
TIME = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:?[0-9]{2})")
ISSUE_FACT = re.compile(rf"jira:({_KEY})/issue/({_NUM})@[0-9a-f]{{12}}/fact")
COMMENT_EVENT = re.compile(rf"jira:({_KEY})/comment/({_NUM})")
HUMAN = {"atlassian", "customer"}
ACCOUNT_TYPES = HUMAN | {"app", "unknown"}
SEP = "--- text from Jira, flattened to plain text; untrusted ---"
DEC = {"approve", "reject"}
INLINE = {"text", "hardBreak", "mention", "emoji", "inlineCard", "date", "status", "mediaInline", "placeholder",
          "inlineExtension"}
FIELDS = "summary,description,created,creator,reporter,status,comment"


class JiraCaptureError(ValueError):
    """A capture, configuration or dataset problem. Messages are built from constants and validated values
    only, so the command line may print them."""


class JiraConfig(BaseModel):
    """The user's jira.json: target status ids whose transitions are decisions. Names are not accepted,
    because a status can be renamed and two workflows can share a name."""
    model_config = ConfigDict(extra="forbid")

    statuses: dict[str, Literal["approve", "reject", "revoke", "reconsider"]] = {}

    @field_validator("statuses")
    @classmethod
    def _ids(cls, value: dict) -> dict:
        if not all(NUM.fullmatch(key) for key in value):
            raise ValueError("jira.json status keys must be Jira status ids, not names")
        return value


@dataclass(frozen=True)
class Who:
    aid: str
    kind: str

    @property
    def automated(self) -> bool:
        return self.kind not in HUMAN


@dataclass(frozen=True)
class Issue:
    id: int
    key: str
    raw: dict
    fields: dict
    created: datetime
    status: int
    order: tuple[str, int]


@dataclass(frozen=True)
class Planned:
    event: Event
    order: tuple


@dataclass
class JiraPlan:
    site: str
    captured_at: datetime
    pages: int
    issues: int
    total: int
    duplicates: int
    incomplete: list[str]
    events: list[Event]
    captures: list[Capture]
    spans: dict[str, EvidenceSpan]
    facts: dict[str, str]
    present: int
    drift: list[str]
    gone: list[str]
    skipped: list[str]
    unmapped: int
    automated: set[str]
    new_versions: int
    statuses: list[str]

    @property
    def complete(self) -> bool:
        return not self.incomplete


def _ts(value: object, what: str) -> datetime:
    """A Jira timestamp (`2026-10-05T09:00:00.000+0000`, or with `Z` or `+00:00`) in UTC, cut to milliseconds."""
    match = TIME.fullmatch(value) if isinstance(value, str) else None
    if match:
        zone = match[3]
        offset = "+00:00" if zone == "Z" else zone if ":" in zone else f"{zone[:3]}:{zone[3:]}"
        fraction = (match[2] or ".")[1:].ljust(6, "0")
        try:
            when = datetime.fromisoformat(f"{match[1]}.{fraction}{offset}").astimezone(timezone.utc)
        except (ValueError, OverflowError):
            pass
        else:
            return when.replace(microsecond=when.microsecond // 1000 * 1000)
    raise JiraCaptureError(f"malformed capture: {what} is not a timestamp with a timezone")


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z"


def _num(value: object, what: str) -> int:
    """Jira ids are strings of digits."""
    if isinstance(value, str) and NUM.fullmatch(value):
        return int(value)
    raise JiraCaptureError(f"malformed capture: {what} is not a Jira id")


def _text(value: object, what: str) -> str:
    if value is None or isinstance(value, str):
        return value or ""
    raise JiraCaptureError(f"malformed capture: {what} is not a string")


def _scalars(obj: object, *keys: str) -> dict | None:
    """Whitelisted scalar fields, for the record line (auditors only, never used for mapping)."""
    if not isinstance(obj, dict):
        return None
    return {k: obj[k] for k in keys if isinstance(obj.get(k), (str, int, bool))} or None


def _user(user: object) -> dict | None:
    return _scalars(user, "accountId", "accountType", "active", "displayName")


def actor(user: object) -> Who | None:
    """The actor named by one structured user field, or None when Jira names no usable account (deleted or
    anonymous authors, a missing field). A display name is never used."""
    if user is None:
        return None
    if not isinstance(user, dict):
        raise JiraCaptureError("malformed capture: a user is not an object")
    account, kind = user.get("accountId"), user.get("accountType")
    if not isinstance(account, str) or not ACCOUNT.fullmatch(account) or account == "unknown":
        return None
    return Who(f"jira:{account}", kind if isinstance(kind, str) and kind in ACCOUNT_TYPES else "other")


def adf_text(value: object, what: str) -> str:
    """Plain text of an Atlassian Document Format value (or a plain string): text, mentions, emoji and link
    URLs in document order, one line per block. Formatting is dropped and nothing is interpreted."""
    if value is None or isinstance(value, str):
        return value or ""
    if not isinstance(value, dict):
        raise JiraCaptureError(f"malformed capture: {what} is neither a document nor a string")
    out: list[str] = []

    def walk(node: object, depth: int) -> None:
        if not isinstance(node, dict) or depth > 100:
            return
        kind = node.get("type")
        attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
        piece = {"text": node.get("text"), "hardBreak": "\n", "mention": attrs.get("text"),
                 "status": attrs.get("text"), "emoji": attrs.get("text") or attrs.get("shortName"),
                 "inlineCard": attrs.get("url"), "blockCard": attrs.get("url"),
                 "embedCard": attrs.get("url")}.get(kind) if isinstance(kind, str) else None
        if isinstance(piece, str) and piece:
            out.append(piece)
        content = node.get("content")
        for child in content if isinstance(content, list) else ():
            walk(child, depth + 1)
        if kind not in INLINE and out and not out[-1].endswith("\n"):
            out.append("\n")

    walk(value, 0)
    return "".join(out).rstrip("\n")


def _paged(container: object, values: str, what: str, key: str, missing: str) -> list[dict] | str:
    """The complete list inside a Jira paged container, or why it is incomplete."""
    if container is None:
        return missing
    items = container.get(values) if isinstance(container, dict) else None
    start = container.get("startAt", 0) if isinstance(container, dict) else None
    total = container.get("total") if isinstance(container, dict) else None
    if not isinstance(items, list) or not all(isinstance(x, dict) for x in items) \
            or type(start) is not int or type(total) is not int or start < 0 or total < len(items):
        raise JiraCaptureError(f"malformed capture: {what} of {key}")
    if start or total > len(items):
        return f"{what} truncated: {len(items)} of {total}"
    return items


def _header(raw: dict, site: str, page: int) -> Issue:
    """The fields every issue needs before anything else is read: identity, site, creation time, status."""
    key = raw.get("key")
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise JiraCaptureError(f"malformed capture: an issue on page {page} has no valid key")
    issue_id = _num(raw.get("id"), f"the id of {key}")
    if raw.get("self") != f"{site}/rest/api/3/issue/{issue_id}":
        raise JiraCaptureError(f"malformed capture: {key} is not an issue of {site} read through /rest/api/3")
    fields = raw.get("fields")
    if not isinstance(fields, dict):
        raise JiraCaptureError(f"malformed capture: {key} has no fields")
    status = fields.get("status")
    project, number = key.rsplit("-", 1)
    return Issue(issue_id, key, raw, fields, _ts(fields.get("created"), f"the created time of {key}"),
                 _num(status.get("id") if isinstance(status, dict) else None, f"the status of {key}"),
                 (project, int(number)))


class Ctx:
    """State of one convert() run: what the log holds, what this run plans, and what it reports."""

    def __init__(self, site: str, captured_at: datetime, existing: dict[str, Event], evidence_ids: set[str],
                 config: JiraConfig):
        self.site, self.captured_at, self.existing = site, captured_at, existing
        self.evidence_ids, self.config = evidence_ids, config
        self.planned: dict[str, str] = {}
        self.captures: list[Capture] = []
        self.spans: dict[str, EvidenceSpan] = {}
        self.facts: dict[str, str] = {}
        self.drift: list[str] = []
        self.gone: list[str] = []
        self.skipped: list[str] = []
        self.partial: list[str] = []
        self.unmapped = self.new_versions = 0
        self.automated: set[str] = set()
        self.statuses: set[int] = set()
        # Which key each issue id had when this dataset first recorded it, and back.
        self.keys: dict[int, str] = {}
        self.ids: dict[str, int] = {}
        for eid in sorted(evidence_ids):
            if match := ISSUE_FACT.fullmatch(eid):
                self.keys.setdefault(int(match[2]), match[1])
                self.ids.setdefault(match[1], int(match[2]))
        self.comments: dict[str, set[int]] = {}
        for eid in existing:
            if match := COMMENT_EVENT.fullmatch(eid):
                self.comments.setdefault(match[1], set()).add(int(match[2]))

    def action_of(self, eid: str) -> str | None:
        """The log's action for an id, else this run's: the log decides revoke targets."""
        return self.existing[eid].action if eid in self.existing else self.planned.get(eid)

    def skip(self, ref: str, why: str, *, partial: bool = False) -> None:
        line = f"{ref} ({why})"
        target = self.partial if partial else self.skipped
        if line not in target:
            target.append(line)

    def cite(self, issue: Issue, kind: str, oid: int, who: Who, when: datetime, tail: str, record: dict,
             verbatim: str, eid: str) -> list[str]:
        """Render one object version, register its capture if new, and return the evidence ids to cite."""
        key = issue.key
        head = f"Jira issue {key} (id {issue.id}) created" if kind == "issue" else f"Jira {kind} {oid} on {key}"
        fact = f"{head} by {who.aid} ({who.kind}) at {_iso(when)}{tail}"
        base = {"kind": kind, "site": self.site, "issue_id": str(issue.id), "key": key}
        text, off = render(fact, {**base, **{k: v for k, v in record.items() if v is not None}}, verbatim, SEP)
        if kind == "transition":
            ref = f"jira:{key}/transition/{oid}"
        else:
            ref = f"jira:{key}/{kind}/{oid}@{hashlib.sha256(text[off:].encode('utf-8')).hexdigest()[:12]}"
        span = text_span(text[off:])
        ids = [f"{ref}/fact"] + ([f"{ref}/text"] if span else [])
        if self.facts.setdefault(ids[0], fact) != fact:
            raise JiraCaptureError("malformed capture: conflicting facts for one evidence id")
        if all(i in self.evidence_ids or i in self.spans for i in ids):
            return ids
        new_fact = ids[0] not in self.evidence_ids and ids[0] not in self.spans
        uri, source_kind = {
            "issue": (f"{self.site}/browse/{key}", "jira_issue"),
            "comment": (f"{self.site}/browse/{key}?focusedCommentId={oid}", "jira_comment"),
            "transition": (f"{self.site}/rest/api/3/issue/{issue.id}/changelog", "jira_transition"),
        }[kind]
        data = text.encode("utf-8")
        digest = sha256_bytes(data)
        path = f"sources/jira/{key}/{kind}-{oid}-{digest[:12]}.txt"
        ranges = [(0, len(fact))] + ([(off + span[0], off + span[1])] if span else [])
        entries = tuple(ManifestEntry(id=i, path=path, sha256=digest, quote=text[s:e], start=s, end=e, source_uri=uri,
                                      source_kind=source_kind, captured_at=self.captured_at)
                        for i, (s, e) in zip(ids, ranges) if i not in self.evidence_ids and i not in self.spans)
        for entry in entries:
            self.spans[entry.id] = make_span(path, digest, text, entry.start, entry.end, id=entry.id, source_uri=uri,
                                             source_kind=source_kind, captured_at=self.captured_at)
        self.captures.append(Capture(path, data, entries))
        if kind != "transition" and eid in self.existing and new_fact:
            self.new_versions += 1
        return ids

    def observe(self, out: list[Planned], issue: Issue, eid: str, kind: str, oid: int, user: object,
                when: datetime, action: str, note: str, *, record: dict, verbatim: str = "", tail: str = "",
                supersedes: str | None = None) -> None:
        """Capture one object and plan its event, attributed to the one user field that carries the act."""
        who = actor(user)
        if who is None:
            self.skip(eid, "no actor")
            return
        cites = self.cite(issue, kind, oid, who, when, tail, record, verbatim, eid)
        if who.automated and action != "comment":
            note = f"automated actor (account type {who.kind}); recorded as comment instead of {action}"
            action, supersedes = "comment", None
        if eid in self.planned:
            return
        self.planned[eid] = action
        old = self.existing.get(eid)
        if old is not None:
            if (old.action, old.actor, old.subject, old.supersedes) != (action, who.aid, issue.key, supersedes):
                self.drift.append(eid)
            return
        if who.automated:
            self.automated.add(who.aid)
        event = Event(id=eid, timestamp=when, actor=who.aid, action=action, subject=issue.key, evidence_ids=cites,
                      note=note, supersedes=supersedes)
        out.append(Planned(event, (when, issue.order, len(out))))


def _transitions(histories: list[dict], key: str) -> tuple[list[tuple], int]:
    """Status changes as (time, history id, history, item, from id, to id), and the count of other items."""
    seen, found, other = set(), [], 0
    for history in histories:
        hid = _num(history.get("id"), f"a history id on {key}")
        if hid in seen:
            raise JiraCaptureError(f"malformed capture: history {hid} appears twice on {key}")
        seen.add(hid)
        when = _ts(history.get("created"), f"history {hid} on {key}")
        items = history.get("items")
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise JiraCaptureError(f"malformed capture: history {hid} on {key} has no items")
        # Only the system status field: a custom field named "status" has fieldtype "custom".
        status = [i for i in items if i.get("fieldtype") == "jira" and i.get("field") == "status"
                  and i.get("fieldId", "status") == "status"]
        other += len(items) - len(status)
        if len(status) > 1:
            raise JiraCaptureError(f"malformed capture: history {hid} on {key} changes the status twice")
        if status:
            item = status[0]
            found.append((when, hid, history, item, _num(item.get("from"), f"the from status of history {hid}"),
                          _num(item.get("to"), f"the to status of history {hid}")))
    return sorted(found, key=lambda t: (t[0], t[1])), other


def plan_issue(ctx: Ctx, issue: Issue) -> list[Planned]:
    """Plan one issue whole, or skip it with a reason: its creation, then comments and transitions in time order."""
    key, fields, base = issue.key, issue.fields, f"jira:{issue.key}"
    known_key, known_id = ctx.keys.get(issue.id), ctx.ids.get(key)
    if known_key is not None and known_key != key:
        ctx.skip(base, f"issue {issue.id} is {known_key} in this dataset; a moved issue is not followed")
        return []
    if known_id is not None and known_id != issue.id:
        ctx.skip(base, f"{key} is issue {known_id} in this dataset")
        return []
    histories = _paged(issue.raw.get("changelog"), "histories", "changelog", key,
                       "no changelog; fetch with expand=changelog")
    comments = _paged(fields.get("comment"), "comments", "comments", key,
                      "no comment field; fetch with the documented field list")
    for found in (histories, comments):
        if isinstance(found, str):
            ctx.skip(base, found, partial=True)
            return []
    transitions, other = _transitions(histories, key)
    for (_, _, _, _, _, to), (_, hid, _, _, frm, _) in zip(transitions, transitions[1:]):
        if to != frm:
            ctx.skip(base, f"status history has a gap before history {hid}", partial=True)
            return []
    if transitions and transitions[-1][5] != issue.status:
        ctx.skip(base, "changelog does not end at the current status", partial=True)
        return []
    walk, seen = [], set()
    for comment in comments:
        cid = _num(comment.get("id"), f"a comment id on {key}")
        if cid in seen:
            raise JiraCaptureError(f"malformed capture: comment {cid} appears twice on {key}")
        seen.add(cid)
        walk.append((_ts(comment.get("created"), f"comment {cid} on {key}"), 0, cid, comment))
    walk += [(t[0], 1, t[1], t) for t in transitions]
    ctx.unmapped += other
    ctx.statuses.update(status for t in transitions for status in t[4:])
    summary = _text(fields.get("summary"), f"the summary of {key}")
    description = adf_text(fields.get("description"), f"the description of {key}")
    out: list[Planned] = []
    ctx.observe(out, issue, f"{base}/created", "issue", issue.id, fields.get("creator"), issue.created, "request",
                "Jira issue created", verbatim=f"{summary}\n{description}",
                record={"created": fields.get("created"), "creator": _user(fields.get("creator")),
                        "reporter": _user(fields.get("reporter"))})
    last_decision = None
    for when, rank, oid, x in sorted(walk, key=lambda w: w[:3]):
        if rank == 0:
            note = "Jira comment"
            if x.get("updated") is not None and _ts(x["updated"], f"comment {oid} on {key}") > when:
                editor = actor(x.get("updateAuthor"))
                note += "; edited before capture" + (f" by {editor.aid}" if editor else "")
            ctx.observe(out, issue, f"{base}/comment/{oid}", "comment", oid, x.get("author"), when, "comment", note,
                        record={"id": str(oid), "created": x.get("created"), "updated": x.get("updated"),
                                "author": _user(x.get("author")), "updateAuthor": _user(x.get("updateAuthor")),
                                "jsdPublic": x.get("jsdPublic") if isinstance(x.get("jsdPublic"), bool) else None,
                                "visibility": _scalars(x.get("visibility"), "type", "value")},
                        verbatim=adf_text(x.get("body"), f"comment {oid} on {key}"))
            continue
        _, _, history, item, frm, to = x
        eid, mapped, supersedes = f"{base}/transition/{oid}", ctx.config.statuses.get(str(to)), None
        note, action = f"Jira transition from status {frm} to {to}", "comment"
        if mapped is not None:
            note, action = f"{note}, mapped to {mapped} in jira.json", mapped
        if mapped == "revoke" and last_decision is None:
            note, action = f"{note}; no decision to withdraw", "comment"
        elif mapped == "revoke":
            note, supersedes = f"{note}; withdraws {last_decision}", last_decision
        ctx.observe(out, issue, eid, "transition", oid, history.get("author"), when, action, note,
                    tail=f": status {frm} to {to}", supersedes=supersedes,
                    record={"id": str(oid), "created": history.get("created"), "author": _user(history.get("author")),
                            "status": _scalars(item, "from", "fromString", "to", "toString")})
        if ctx.action_of(eid) in DEC:
            last_decision = eid
    ctx.gone += [f"{base}/comment/{c}" for c in sorted(ctx.comments.get(key, set()) - seen)]
    return out


def convert(capture: object, existing: dict[str, Event], evidence_ids: set[str], config: JiraConfig) -> JiraPlan:
    """Plan the events and evidence of a capture. Pure: equal inputs always give an equal plan."""
    if not isinstance(capture, dict) or type(capture.get("jira_capture")) is not int \
            or capture["jira_capture"] != 1:
        raise JiraCaptureError("malformed capture: not a jira_capture 1 document")
    site = capture.get("site")
    site = site.lower().removesuffix("/") if isinstance(site, str) and site.isascii() else ""
    if not SITE.fullmatch(site):
        raise JiraCaptureError("malformed capture: site is not https://<name>.atlassian.net")
    captured_at = _ts(capture.get("captured_at"), "captured_at")
    pages = capture.get("pages")
    if not isinstance(pages, list) or not pages or not all(isinstance(p, dict) for p in pages):
        raise JiraCaptureError("malformed capture: pages is not a non-empty list of search responses")
    totals: set[int] = set()
    positions: dict[int, int] = {}
    issues: dict[int, Issue] = {}
    keys: dict[str, int] = {}
    duplicates, reordered = 0, False
    for number, page in enumerate(pages, 1):
        start, total, found = page.get("startAt"), page.get("total"), page.get("issues")
        if type(start) is not int or type(total) is not int or start < 0 or total < 0 \
                or not isinstance(found, list) or not all(isinstance(i, dict) for i in found):
            raise JiraCaptureError(f"malformed capture: page {number} is not a /rest/api/3/search response")
        totals.add(total)
        for offset, raw in enumerate(found):
            issue = _header(raw, site, number)
            if keys.setdefault(issue.key, issue.id) != issue.id:
                raise JiraCaptureError(f"malformed capture: {issue.key} names two issue ids")
            reordered |= positions.setdefault(start + offset, issue.id) != issue.id
            first = issues.setdefault(issue.id, issue)
            if first is not issue:
                if first.raw != raw:
                    raise JiraCaptureError(f"malformed capture: {issue.key} appears twice with different content; "
                                           "save every page of one search run")
                duplicates += 1
    total = max(totals)
    covered = sum(1 for p in positions if p < total)
    incomplete = (["pages disagree on the result total"] if len(totals) > 1 else []) \
        + ([f"pages cover {covered} of {total} results"] if covered < total else []) \
        + (["pages extend past the result total"] if len(positions) > covered else []) \
        + (["the result order changed between pages"] if reordered else [])
    ctx = Ctx(site, captured_at, existing, evidence_ids, config)
    planned = sorted((p for issue in sorted(issues.values(), key=lambda i: i.order) for p in plan_issue(ctx, issue)),
                     key=lambda p: p.order)
    return JiraPlan(site=site, captured_at=captured_at, pages=len(pages), issues=len(issues), total=total,
                    duplicates=duplicates, incomplete=incomplete + ctx.partial, events=[p.event for p in planned],
                    captures=ctx.captures, spans=ctx.spans, facts=ctx.facts,
                    present=sum(eid in existing for eid in ctx.planned), drift=ctx.drift, gone=ctx.gone,
                    skipped=ctx.skipped, unmapped=ctx.unmapped, automated=ctx.automated,
                    new_versions=ctx.new_versions, statuses=[str(s) for s in sorted(ctx.statuses)])
