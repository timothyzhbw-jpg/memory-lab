"""Event, policy and state models. Every event and policy-log entry is validated before any replay starts."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_serializer, model_validator

from memcore import EvidenceSpan

Action = Literal["request", "reconsider", "raise_risk", "dependency", "approve", "reject", "revoke", "comment"]
DECISIONS = {"approve", "reject", "revoke"}
MANAGE_ROLES = "manage_roles"
# Permissions name event actions, plus manage_roles: who may record a role change in policy-log.jsonl.
PolicyAction = Union[Action, Literal["manage_roles"]]
RoleAction = Literal["grant", "revoke_role"]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    timestamp: AwareDatetime = Field(description="When the event took effect, with timezone")
    actor: str = Field(min_length=1)
    action: Action
    subject: str = Field(min_length=1, description="What the event is about, e.g. 'Feature A'")
    evidence_ids: list[str] = Field(min_length=1)
    note: str | None = None
    dependency: str | None = None
    supersedes: str | None = None

    @model_validator(mode="after")
    def _action_fields(self) -> "Event":
        if self.action == "dependency" and not self.dependency:
            raise ValueError("a dependency event requires 'dependency'")
        if self.action == "raise_risk" and not self.note:
            raise ValueError("a raise_risk event requires 'note'")
        if self.action == "revoke" and not self.supersedes:
            raise ValueError("a revoke event requires 'supersedes'")
        if self.action != "revoke" and self.supersedes:
            raise ValueError("only revoke events may set 'supersedes'")
        return self


class Policy(BaseModel):
    """Trusted configuration: who holds which role, and which roles may perform which action.

    With a policy log, ``actors`` is the baseline: the roles held from ``as_of`` (or from the beginning of
    time when it is absent) until policy-log.jsonl changes them. ``permissions`` are not dated.
    """
    model_config = ConfigDict(extra="forbid")

    actors: dict[str, list[str]]
    permissions: dict[PolicyAction, list[str]]
    as_of: Optional[AwareDatetime] = Field(
        default=None, description="When the roles in 'actors' took effect; before it no actor holds a role")

    def roles(self, actor: str) -> list[str]:
        """Baseline roles, ignoring any policy log."""
        return self.actors.get(actor, [])

    def allows(self, actor: str, action: str) -> bool:
        return bool(set(self.roles(actor)) & set(self.permissions.get(action, [])))

    def permitted(self, action: str) -> list[str]:
        """Roles that may perform ``action``."""
        return self.permissions.get(action, [])

    @property
    def known_roles(self) -> set[str]:
        return {role for roles in [*self.actors.values(), *self.permissions.values()] for role in roles}

    @property
    def decision_roles(self) -> list[str]:
        """Roles that can both approve and reject: whoever must review an open request."""
        return sorted(set(self.permissions.get("approve", [])) & set(self.permissions.get("reject", [])))


class PolicyChange(BaseModel):
    """One line of policy-log.jsonl: a role granted to, or removed from, an actor at an effective time."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    timestamp: AwareDatetime = Field(description="When the change took effect, with timezone")
    actor: str = Field(min_length=1, description="Who made the change; must hold a role permitted to manage_roles")
    action: RoleAction
    subject_actor: str = Field(min_length=1, description="Whose roles change")
    role: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    note: Optional[str] = None


class RoleChange(BaseModel):
    """A policy-log entry as replay applied it, with the roles before and after and its evidence."""
    change_id: str
    timestamp: datetime
    actor: str
    actor_roles: list[str]          # the roles ``actor`` held at that time, including one permitting manage_roles
    action: RoleAction
    subject_actor: str
    role: str
    roles_before: list[str]
    roles_after: list[str]
    note: Optional[str] = None
    evidence: list[EvidenceSpan]


class DatedPolicy(BaseModel):
    """Present only for a dataset with policy-log.jsonl or a policy.json ``as_of``."""
    baseline: Optional[datetime]    # policy.json as_of; None: its roles hold from the beginning of time
    role_changes: list[RoleChange]  # applied up to the cutoff, in effective order


def omit_none(fields: tuple[str, ...]):
    """A wrap serializer dropping ``fields`` when they are None or empty, so output for a dataset without a
    policy log stays byte-identical to what it was before dated policy existed."""
    def serialize(self, handler):
        data = handler(self)
        for name in fields:
            if getattr(self, name) in (None, []):
                data.pop(name, None)
        return data
    return model_serializer(mode="wrap")(serialize)


class Decision(BaseModel):
    event_id: str
    actor: str
    roles: list[str]
    action: Literal["approve", "reject", "revoke"]
    timestamp: datetime
    evidence: list[EvidenceSpan]


class Risk(BaseModel):
    event_id: str
    actor: str
    note: str


class PendingChange(BaseModel):
    event_id: str
    actor: str
    action: str
    reason: str


class Conflict(BaseModel):
    kind: Literal["simultaneous_decisions", "invalid_supersession", "decision_reversed"]
    event_ids: list[str]
    detail: str


class SubjectState(BaseModel):
    subject: str
    current_status: Literal["proposed", "approved", "rejected"] = "proposed"
    reconsideration_requested: bool = False
    pending_approval: list[str] = []
    dependencies: list[str] = []
    risks: list[Risk] = []
    last_authorized_decision: Decision | None = None
    decision_history: list[str] = []
    pending_changes: list[PendingChange] = []
    conflicts: list[Conflict] = []
    last_applied_event: str | None = None


class TrailEntry(BaseModel):
    event_id: str
    timestamp: datetime
    actor: str
    roles: list[str]
    action: str
    subject: str
    outcome: Literal["applied", "unauthorized", "invalid_supersession"]
    status_before: str
    status_after: str
    evidence_ids: list[str]


class ReplayResult(BaseModel):
    tool: str = "orgmemory"
    mode: Literal["read_only"] = "read_only"
    as_of: datetime | None
    events_total: int
    events_replayed: int
    states: dict[str, SubjectState]
    audit_log: list[TrailEntry]
    dated_policy: Optional[DatedPolicy] = None

    _serialize = omit_none(("dated_policy",))
