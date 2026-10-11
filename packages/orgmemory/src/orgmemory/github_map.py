"""Pure mapping from a GitHub capture to orgmemory events and evidence captures.

No I/O and no clock: the result depends only on the capture, the events and
evidence ids already in the dataset, and the user's github.json. Text is never
parsed for commands; each event's actor comes from one structured field.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter, ValidationError, model_validator

from memcore import EvidenceSpan, ManifestEntry, make_span, sha256_bytes

from . import ingest
from .ingest import SURROGATE, UNSAFE, Capture, text_span  # noqa: F401  (re-exported for callers and tests)
from .models import Event

REPO = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}/[a-z0-9._-]{1,100}")
LOGIN = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}(\[bot\])?")
ENUM = re.compile(r"[a-z_-]{1,40}")
SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,99}")
SKIP_REASON = re.compile(r"(issue|timeline) [0-9]{3}")
COMMENT_ID = re.compile(r"gh:(.+)/comment/([0-9]+)")
TYPES = {"User", "Bot", "Organization", "Mannequin", "Enterprise"}
SEP = "--- verbatim from GitHub; untrusted ---"
DEC = {"approve", "reject"}
VERDICTS = {"approved": "approve", "changes_requested": "reject", "commented": "comment"}
HANDLED = {"commented", "reviewed", "review_dismissed", "review_requested", "closed", "merged", "reopened", "labeled",
           "unlabeled", "blocked_by_added", "blocked_by_removed", "blocking_added", "blocking_removed"}
_TIME = TypeAdapter(AwareDatetime)


class GithubConfig(BaseModel):
    """The user's github.json: labels whose application is a decision. Keys match label names case-insensitively."""
    model_config = ConfigDict(extra="forbid")

    labels: dict[str, Literal["approve", "reject", "raise_risk"]] = {}

    @model_validator(mode="after")
    def _distinct(self) -> "GithubConfig":
        if len({k.casefold() for k in self.labels}) != len(self.labels):
            raise ValueError("github.json: two label keys differ only in case")
        return self

    def key(self, name: object) -> str | None:
        if isinstance(name, str):
            for key in self.labels:
                if key.casefold() == name.casefold():
                    return key
        return None


@dataclass(frozen=True)
class Planned:
    event: Event
    order: tuple


@dataclass
class Plan:
    events: list[Event]
    captures: list[Capture]
    spans: dict[str, EvidenceSpan]
    drift: list[str]
    gone: list[str]
    skipped: list[str]
    unmapped: int
    automated: set[str]
    new_versions: int
    facts: dict[str, str]


def _int(value: object, what: str = "id") -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"malformed capture: {what} is not a positive integer")
    return value


def _ts(value: object, what: str = "time") -> datetime:
    try:
        if isinstance(value, str):
            return _TIME.validate_python(value).astimezone(timezone.utc)
    except ValidationError:
        pass
    raise ValueError(f"malformed capture: {what} is not an ISO 8601 time with timezone")


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: object) -> str:
    if value is None or isinstance(value, str):
        return value or ""
    raise ValueError("malformed capture: text field is not a string")


def _lower(value: object) -> str:
    """ASCII only, so that no Unicode case mapping (e.g. the Kelvin sign) can produce a valid login or repo."""
    return value.lower() if isinstance(value, str) and value.isascii() else ""


def _enum(value: object) -> str:
    return value if isinstance(value, str) and ENUM.fullmatch(value) else "unknown"


def _sha12(value: object) -> str:
    return value[:12] if isinstance(value, str) and SHA.fullmatch(value) else "unknown"


def _slug(app: object) -> str:
    slug = app.get("slug") if isinstance(app, dict) else None
    return slug if isinstance(slug, str) and SLUG.fullmatch(slug) else "unknown"


def _type(user: dict) -> str:
    kind = user.get("type")
    return kind if isinstance(kind, str) and kind in TYPES else "Other"


def _repo_ok(repo: str) -> bool:
    return bool(REPO.fullmatch(repo)) and repo.split("/")[1] not in (".", "..")


def _event(x: dict) -> str:
    return x["event"] if isinstance(x.get("event"), str) else ""


def _when(x: dict) -> datetime | None:
    value = x.get("submitted_at" if _event(x) == "reviewed" else "created_at")
    return None if value is None else _ts(value)


def _rid(x: dict) -> int:
    dr = x.get("dismissed_review")
    if not isinstance(dr, dict):
        raise ValueError("malformed capture: review_dismissed without dismissed_review")
    rid = dr.get("review_id")
    if isinstance(rid, str) and rid.isascii() and rid.isdigit():  # the docs table says string, the schema integer
        rid = int(rid)
    return _int(rid, "review_id")


def _dep(x: dict) -> str | None:
    blocked = x.get("blocked_by") if isinstance(x.get("blocked_by"), dict) else {}
    repo = blocked.get("repository") if isinstance(blocked.get("repository"), dict) else {}
    name, number = _lower(repo.get("full_name")), blocked.get("number")
    return f"{name}#{number}" if _repo_ok(name) and type(number) is int and number > 0 else None


def _scalar(value: object) -> object:
    return value if isinstance(value, (str, int, float, bool)) else None


def _pick(obj: object, *keys: str) -> dict | None:
    if not isinstance(obj, dict):
        return None
    return {k: obj[k] for k in keys if _scalar(obj.get(k)) is not None} or None


def _record(base: dict, x: dict) -> dict:
    """Whitelisted fields of a GitHub object, for auditors (§8.3). Never used for mapping."""
    rec = dict(base)
    for k in ("id", "event", "created_at", "updated_at", "submitted_at", "state", "state_reason", "commit_id",
              "draft", "author_association"):
        rec[k] = _scalar(x.get(k))
    for k in ("user", "actor", "requested_reviewer"):
        rec[k] = _pick(x.get(k), "login", "id", "type")
    rec["dismissed_review"] = _pick(x.get("dismissed_review"), "state", "review_id", "dismissal_commit_id")
    rec["label"] = (_pick(x.get("label"), "name") or {}).get("name")
    rec["requested_team"] = (_pick(x.get("requested_team"), "slug") or {}).get("slug")
    if isinstance(x.get("blocked_by"), dict):
        repo = x["blocked_by"].get("repository")
        rec["blocked_by"] = _pick({"number": x["blocked_by"].get("number"),
                                   "repository": repo.get("full_name") if isinstance(repo, dict) else None},
                                  "number", "repository")
    if x.get("performed_via_github_app") is not None:
        rec["performed_via_github_app"] = _slug(x["performed_via_github_app"])
    if x.get("intent") is not None:
        rec["intent"] = _pick(x["intent"], "confidence") or {}
    if x.get("minimized"):
        reason = x["minimized"].get("reason") if isinstance(x["minimized"], dict) else x["minimized"]
        rec["minimized"] = _scalar(reason) or True
    return {k: v for k, v in rec.items() if v is not None}


def actor(user: dict | None, *, app: dict | None = None, intent: object = None) -> tuple[str, bool, str] | None:
    """(actor id, automated, why) for a GitHub user object, or None for a null user (G1, G2, §5)."""
    if user is None:
        return None
    if not isinstance(user, dict):
        raise ValueError("malformed capture: a user is not an object")
    login = _lower(user.get("login"))
    aid = f"gh:{login if LOGIN.fullmatch(login) else '~'}:{_int(user.get('id'), 'user id')}"
    why = [] if user.get("type") == "User" else [f"type {_type(user)}"]
    raw_login = user.get("login")
    why += ["bot login"] if isinstance(raw_login, str) and raw_login.lower().endswith("[bot]") else []
    why += ["ghost account"] if login == "ghost" else []
    why += [f"via app {_slug(app)}"] if app is not None else []
    why += ["agent intent"] if intent is not None else []
    return aid, bool(why), ", ".join(why)


def render(fact: str, record: dict, verbatim: str) -> tuple[str, int]:
    """Capture text with the GitHub separator (see `ingest.render`)."""
    return ingest.render(fact, record, verbatim, SEP)


class Ctx:
    """State of one convert() run: what the log holds, what this run plans, and what it reports."""

    def __init__(self, repo: str, repo_id: int, captured_at: datetime, existing: dict[str, Event],
                 evidence_ids: set[str], config: GithubConfig):
        self.repo, self.repo_id, self.captured_at = repo, repo_id, captured_at
        self.existing, self.evidence_ids, self.config = existing, evidence_ids, config
        self.planned: dict[str, str] = {}
        self.captures: list[Capture] = []
        self.spans: dict[str, EvidenceSpan] = {}
        self.facts: dict[str, str] = {}
        self.drift: list[str] = []
        self.gone: list[str] = []
        self.skipped: list[str] = []
        self.unmapped = self.new_versions = 0
        self.automated: set[str] = set()
        self.comments: dict[str, set[int]] = {}
        for eid in existing:
            if match := COMMENT_ID.fullmatch(eid):
                self.comments.setdefault(match[1], set()).add(int(match[2]))

    def action_of(self, eid: str) -> str | None:
        """The log's action for an id, else this run's (G5)."""
        return self.existing[eid].action if eid in self.existing else self.planned.get(eid)

    def skip(self, ref: str, why: str) -> None:
        line = f"{ref} ({why})"
        if line not in self.skipped:
            self.skipped.append(line)

    def cite(self, n: int, kind: str, oid: int, x: dict, who: tuple, user: dict, when: datetime, tail: str,
             verbatim: str, *, pr: bool = False, eid: str = "") -> list[str]:
        """Render one object version (§8), register its capture if new, and return the evidence ids to cite."""
        subject, web = f"{self.repo}#{n}", f"https://github.com/{self.repo}"
        head = f"GitHub {'pull request' if pr else 'issue'} {subject} opened" if kind == "issue" \
            else f"GitHub {kind} {oid} on {subject}"
        fact = f"{head} by {who[0]} ({_type(user)}) at {_iso(when)}{tail}"
        app = x.get("performed_via_github_app")
        fact += f" via app {_slug(app)}" if app is not None else ""
        fact += " with agent intent" if x.get("intent") is not None else ""
        base = {"kind": kind, "repo": self.repo, "repo_id": self.repo_id, "number": n}
        text, off = render(fact, _record({**base, "pull_request": pr} if kind == "issue" else base, x), verbatim)
        if kind == "event":
            ref = f"gh:{subject}/event/{oid}"
        else:
            ref = f"gh:{subject}/{'issue' if kind == 'issue' else f'{kind}/{oid}'}@" \
                  f"{hashlib.sha256(text[off:].encode('utf-8')).hexdigest()[:12]}"
        span = None if x.get("minimized") else text_span(text[off:])
        ids = [f"{ref}/fact"] + ([f"{ref}/text"] if span else [])
        if ids[0] in self.facts and self.facts[ids[0]] != fact:
            raise ValueError("malformed capture: conflicting facts for one evidence id")
        self.facts[ids[0]] = fact
        if all(i in self.evidence_ids or i in self.spans for i in ids):
            return ids
        new_fact = ids[0] not in self.evidence_ids and ids[0] not in self.spans
        uri, source_kind = {
            "issue": (f"{web}/{'pull' if pr else 'issues'}/{n}", "github_pull_request" if pr else "github_issue"),
            "comment": (f"{web}/issues/{n}#issuecomment-{oid}", "github_issue_comment"),
            "review": (f"{web}/pull/{n}#pullrequestreview-{oid}", "github_pull_request_review"),
            "event": (f"https://api.github.com/repos/{self.repo}/issues/events/{oid}", "github_issue_event"),
        }[kind]
        data = text.encode("utf-8")
        digest = sha256_bytes(data)
        path = f"sources/github/{self.repo}/{n}/{kind}-{oid}-{digest[:12]}.txt"
        ranges = [(0, len(fact))] + ([(off + span[0], off + span[1])] if span else [])
        entries = tuple(ManifestEntry(id=i, path=path, sha256=digest, quote=text[s:e], start=s, end=e, source_uri=uri,
                                      source_kind=source_kind, captured_at=self.captured_at)
                        for i, (s, e) in zip(ids, ranges) if i not in self.evidence_ids and i not in self.spans)
        for entry in entries:
            self.spans[entry.id] = make_span(path, digest, text, entry.start, entry.end, id=entry.id, source_uri=uri,
                                             source_kind=source_kind, captured_at=self.captured_at)
        self.captures.append(Capture(path, data, entries))
        if kind != "event" and eid in self.existing and new_fact:
            self.new_versions += 1
        return ids

    def emit(self, out: list[Planned], n: int, eid: str, when: datetime, who: tuple, action: str, note: str,
             cites: list[str], *, supersedes: str | None = None, dependency: str | None = None) -> None:
        """Plan one event (G1), or compare it with the logged line of the same id (drift)."""
        aid, automated, why = who
        if automated:
            note = f"automated actor ({why}); recorded as comment instead of {action}"
            action, supersedes, dependency = "comment", None, None
        if eid in self.planned:
            return
        self.planned[eid] = action
        subject = f"{self.repo}#{n}"
        old = self.existing.get(eid)
        if old is not None:
            if (old.action, old.actor, old.subject, old.supersedes, old.dependency) != \
                    (action, aid, subject, supersedes, dependency):
                self.drift.append(eid)
            return
        if automated:
            self.automated.add(aid)
        event = Event(id=eid, timestamp=when, actor=aid, action=action, subject=subject, evidence_ids=cites,
                      note=note, supersedes=supersedes, dependency=dependency)
        out.append(Planned(event, (when, n, len(out))))

    def versioned(self, out: list[Planned], n: int, eid: str, kind: str, oid: int, x: dict, user: object,
                  when: datetime, action: str | None, note: str, *, tail: str = "", verbatim: str = "", pr: bool = False,
                  extra: list[str] | None = None, fact_only: bool = False) -> None:
        """An issue, comment or review: its current text version is captured even when its event is logged."""
        who = actor(user, app=x.get("performed_via_github_app"), intent=x.get("intent"))
        if who is None:
            self.skip(eid, "no actor")
            return
        cites = self.cite(n, kind, oid, x, who, user, when, tail, verbatim, pr=pr, eid=eid)
        if action is not None:
            self.emit(out, n, eid, when, who, action, note, (cites[:1] if fact_only else cites) + (extra or []))

    def event_cite(self, n: int, x: dict) -> list[str]:
        """Evidence ids for an issue event's own capture (empty for a null actor)."""
        who = actor(x.get("actor"), app=x.get("performed_via_github_app"), intent=x.get("intent"))
        if who is None:
            return []
        verbatim = _text(x["dismissed_review"].get("dismissal_message")) if _event(x) == "review_dismissed" else ""
        return self.cite(n, "event", _int(x.get("id")), x, who, x["actor"], _ts(x.get("created_at")),
                         f": {_enum(_event(x))}{self.detail(x)}", verbatim)

    def event(self, out: list[Planned], n: int, x: dict, action: str, note: str, *, suffix: str = "",
              supersedes: str | None = None, dependency: str | None = None) -> None:
        eid = f"gh:{self.repo}#{n}/event/{_int(x.get('id'))}"
        who = actor(x.get("actor"), app=x.get("performed_via_github_app"), intent=x.get("intent"))
        if who is None:
            self.skip(eid, "no actor")
            return
        cites = self.event_cite(n, x)
        if action != "revoke" or _event(x) != "review_dismissed":
            cites = cites[:1]
        self.emit(out, n, eid + suffix, _ts(x.get("created_at")), who, action, note, cites,
                  supersedes=supersedes, dependency=dependency)

    def detail(self, x: dict) -> str:
        """The `<detail>` part of an issue event's fact line (§8.2)."""
        e = _event(x)
        if e == "closed":
            reason, commit = x.get("state_reason"), x.get("commit_id")
            return f" (state_reason {'null' if reason is None else _enum(reason)}, " \
                   f"commit {'none' if commit is None else _sha12(commit)})"
        if e == "merged":
            return f" (commit {_sha12(x.get('commit_id'))})"
        if e == "review_dismissed":
            dr = x["dismissed_review"]
            by = f", by commit {_sha12(dr['dismissal_commit_id'])}" if dr.get("dismissal_commit_id") is not None else ""
            return f" review {_rid(x)} (was {_enum(_lower(dr.get('state')))}{by})"
        if e == "review_requested":
            who = actor(x.get("requested_reviewer"))
            return f" reviewer {who[0] if who else 'none'}"
        if e in ("labeled", "unlabeled"):
            return f" label {json.dumps(self.config.key(_label(x)), ensure_ascii=True)}"
        if e in ("blocked_by_added", "blocked_by_removed"):
            return f" {_dep(x) or 'unknown'}"
        return ""


def _label(x: dict) -> object:
    return x["label"].get("name") if isinstance(x.get("label"), dict) else None


def _edited(x: dict) -> bool:
    return x.get("updated_at") is not None and _ts(x["updated_at"]) > _ts(x.get("created_at"))


def plan_item(ctx: Ctx, item: dict) -> list[Planned]:
    """Plan one issue or PR: its opening, then one walk over its timeline in time order (§7)."""
    n = _int(item.get("number"), "item number")
    issue, timeline = item.get("issue"), item.get("timeline")
    if not isinstance(issue, dict) or type(issue.get("number")) is not int or issue.get("number") != n \
            or not isinstance(timeline, list) or not all(isinstance(x, dict) for x in timeline):
        raise ValueError(f"malformed capture: item {n}")
    pr = "pull_request" in issue
    if pr:
        marker = issue["pull_request"]
        if not isinstance(marker, dict) or not isinstance(marker.get("url"), str) or not marker["url"]:
            raise ValueError("malformed capture: pull_request is not a PR object")
    base, out = f"gh:{ctx.repo}#{n}", []
    kind = "pull request" if pr else "issue"
    ctx.versioned(out, n, f"{base}/opened", "issue", n, issue, issue.get("user"), _ts(issue.get("created_at")),
                  "request", f"GitHub {kind} opened", pr=pr,
                  verbatim=f"{_text(issue.get('title'))}\n{_text(issue.get('body'))}")
    ctx.unmapped += sum(_event(x) not in HANDLED for x in timeline)
    walk = sorted(((when, x) for x in timeline if (when := _when(x)) is not None), key=lambda p: p[0])
    # Even a quarantined merge prevents attributing its automatic close as a rejection.
    # Its time supplies no approval authority; M27 still suppresses the merge event itself.
    merged_at = min((when for when, x in walk if _event(x) == "merged"), default=None)
    review_positions = {_int(x.get("id")): i for i, (_, x) in enumerate(walk)
                        if _event(x) == "reviewed" and _lower(x.get("state")) != "pending"}
    dismissals: dict[int, dict] = {}
    for i, (_, x) in enumerate(walk):
        if _event(x) == "review_dismissed":
            if "blocked_by" in x:
                raise ValueError("malformed capture: conflicting dismissal and dependency shapes")
            rid = _rid(x)
            if rid in review_positions and review_positions[rid] > i:
                raise ValueError("malformed capture: dismissal precedes its submitted review")
            dismissals[rid] = x
    present = {_int(x.get("id")) for x in timeline if _event(x) == "commented"}
    last_close, last_label, standing = None, {}, {}
    for when, x in walk:
        e = _event(x)
        if "blocked_by" in x and e not in ("blocked_by_added", "blocked_by_removed"):
            continue
        if e == "commented":
            cid = _int(x.get("id"))
            note = "GitHub comment" + ("; edited before capture; editor not exposed by REST" if _edited(x) else "")
            ctx.versioned(out, n, f"{base}/comment/{cid}", "comment", cid, x, x.get("user"), when, "comment", note,
                          verbatim=_text(x.get("body")))
        elif e == "reviewed":
            rid, state = _int(x.get("id")), _lower(x.get("state"))
            if state == "pending":
                continue
            dismissal = dismissals.get(rid) if state == "dismissed" else None
            verdict = _lower(dismissal["dismissed_review"].get("state")) if dismissal else state
            reviewer = x.get("user")
            if isinstance(reviewer, dict):
                standing[_int(reviewer.get("id"), "user id")] = (rid, verdict)
            blank = verdict == "commented" and not _text(x.get("body")).strip()
            sha = _sha12(x.get("commit_id"))
            action = VERDICTS.get(verdict)
            note = f"GitHub review {verdict} on commit {sha}" + ("; dismissed later" if dismissal else "") \
                if action else f"GitHub review state {_enum(verdict)}"
            ctx.versioned(out, n, f"{base}/review/{rid}", "review", rid, x, reviewer, when, None if blank else action or "comment", note,
                          tail=f": {_enum(state)} on commit {sha}", verbatim=_text(x.get("body")),
                          extra=ctx.event_cite(n, dismissal)[:1] if dismissal else [], fact_only=action is None)
        elif e == "review_dismissed":
            rid, dr = _rid(x), x["dismissed_review"]
            for uid, (r, _) in list(standing.items()):
                if r == rid:
                    standing[uid] = (rid, "dismissed")
            target, was = f"{base}/review/{rid}", _enum(_lower(dr.get("state")))
            if dr.get("dismissal_commit_id") is not None:
                ctx.event(out, n, x, "comment", f"GitHub review {rid} dismissed by commit "
                          f"{_sha12(dr['dismissal_commit_id'])} (stale review)")
            elif ctx.action_of(target) in DEC:
                ctx.event(out, n, x, "revoke", f"GitHub review {rid} dismissed (was {was})", supersedes=target)
            else:
                ctx.event(out, n, x, "comment", f"GitHub review {rid} dismissed (was {was})")
        elif e == "review_requested":
            reviewer = x.get("requested_reviewer")
            if isinstance(reviewer, dict) and \
                    standing.get(_int(reviewer.get("id"), "user id"), (0, None))[1] == "changes_requested":
                ctx.event(out, n, x, "request", f"GitHub review re-requested from {actor(reviewer)[0]} "
                          "after changes requested")
        elif e == "closed":
            if pr and merged_at is not None and when >= merged_at:
                continue
            reason, commit = x.get("state_reason"), x.get("commit_id")
            if pr:
                ctx.event(out, n, x, "reject", "GitHub pull request closed without merging")
            elif reason == "not_planned" and commit is None:
                ctx.event(out, n, x, "reject", "GitHub issue closed as not_planned")
            else:
                as_ = "unspecified" if reason is None else _enum(reason)
                ctx.event(out, n, x, "comment", f"GitHub issue closed as {as_}"
                          + (f" by commit {_sha12(commit)}" if commit is not None else ""))
            last_close = f"{base}/event/{_int(x.get('id'))}"
        elif e == "merged":
            ctx.event(out, n, x, "approve", f"GitHub pull request merged at {_sha12(x.get('commit_id'))}")
        elif e == "reopened":
            who = actor(x.get("actor"), app=x.get("performed_via_github_app"), intent=x.get("intent"))
            if who and not who[1] and last_close and ctx.action_of(last_close) in DEC:
                ctx.event(out, n, x, "revoke", f"GitHub {kind} reopened; withdraws {last_close}", supersedes=last_close)
                ctx.event(out, n, x, "request", f"GitHub {kind} reopened", suffix="/request")
            else:
                ctx.event(out, n, x, "request", f"GitHub {kind} reopened")
            last_close = None
        elif e in ("labeled", "unlabeled"):
            key = ctx.config.key(_label(x))
            if key is None:
                continue
            eid, label = f"{base}/event/{_int(x.get('id'))}", json.dumps(key, ensure_ascii=True)
            if e == "labeled":
                ctx.event(out, n, x, ctx.config.labels[key], f"GitHub label {label} added")
                last_label[key] = eid
            elif (target := last_label.pop(key, None)) and ctx.action_of(target) in DEC:
                ctx.event(out, n, x, "revoke", f"GitHub label {label} removed", supersedes=target)
            else:
                ctx.event(out, n, x, "comment", f"GitHub label {label} removed")
        elif e == "blocked_by_added":
            dependency = _dep(x)
            if dependency is None:
                ctx.skip(f"{base}/event/{_int(x.get('id'))}", "malformed blocked-by link")
            else:
                ctx.event(out, n, x, "dependency", "GitHub blocked-by link added", dependency=dependency)
        elif e == "blocked_by_removed":
            ctx.event(out, n, x, "comment", "GitHub blocked-by link removed")
    for x in timeline:
        if _event(x) not in ("blocked_by_added", "blocked_by_removed", "blocking_added", "blocking_removed") \
                and "blocked_by" in x:
            ref = f"{base}/event/{x['id']}" if type(x.get("id")) is int and x["id"] > 0 else base
            ctx.skip(ref, f"unrecognized dependency event {_enum(_event(x))}")
    ctx.gone += [f"{base}/comment/{c}" for c in sorted(ctx.comments.get(f"{ctx.repo}#{n}", set()) - present)]
    return out


def convert(capture: dict, existing: dict[str, Event], evidence_ids: set[str], config: GithubConfig) -> Plan:
    """Plan the events and evidence of a capture (§6.7). Pure: equal inputs always give an equal Plan."""
    if not isinstance(capture, dict) or type(capture.get("github_capture")) is not int \
            or capture["github_capture"] != 1:
        raise ValueError("malformed capture: not a github_capture 1 document")
    if type(capture.get("complete")) is not bool or capture.get("mode") not in ("full", "incremental", "targeted") \
            or type(capture.get("requests")) is not int or capture["requests"] < 0:
        raise ValueError("malformed capture: invalid run metadata")
    if capture.get("since") is not None:
        _ts(capture["since"], "since")
    repo = _lower(capture.get("repo"))
    if not _repo_ok(repo):
        raise ValueError("malformed capture: invalid repo")
    items, skipped = capture.get("items"), capture.get("skipped", [])
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items) \
            or not isinstance(skipped, list) or not all(isinstance(s, dict) for s in skipped):
        raise ValueError("malformed capture: items and skipped must be lists of objects")
    ctx = Ctx(repo, _int(capture.get("repo_id"), "repo_id"), _ts(capture.get("started_at"), "started_at"),
              existing, evidence_ids, config)
    for skip in skipped:
        reason = skip.get("reason")
        ctx.skip(f"gh:{repo}#{_int(skip.get('number'), 'skipped number')}",
                 reason if isinstance(reason, str) and SKIP_REASON.fullmatch(reason) else "unknown")
    numbers = [item.get("number") for item in items]
    if len(set(map(repr, numbers))) != len(numbers):
        raise ValueError("malformed capture: an item number appears twice")
    planned = sorted((p for item in items for p in plan_item(ctx, item)), key=lambda p: p.order)
    return Plan(events=[p.event for p in planned], captures=ctx.captures, spans=ctx.spans, drift=ctx.drift,
                gone=ctx.gone, skipped=ctx.skipped, unmapped=ctx.unmapped, automated=ctx.automated,
                new_versions=ctx.new_versions, facts=ctx.facts)
