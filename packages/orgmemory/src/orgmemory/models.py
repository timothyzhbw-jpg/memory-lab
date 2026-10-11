"""Event, policy and state models. Every event is validated before any replay starts."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from memcore import EvidenceSpan

Action = Literal["request", "reconsider", "raise_risk", "dependency", "approve", "reject", "revoke", "comment"]
DECISIONS = {"approve", "reject", "revoke"}


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
    """Trusted configuration: who holds which role, and which roles may perform which action."""
    model_config = ConfigDict(extra="forbid")

    actors: dict[str, list[str]]
    permissions: dict[Action, list[str]]

    def roles(self, actor: str) -> list[str]:
        return self.actors.get(actor, [])

    def allows(self, actor: str, action: str) -> bool:
        return bool(set(self.roles(actor)) & set(self.permissions.get(action, [])))

    @property
    def decision_roles(self) -> list[str]:
        """Roles that can both approve and reject: whoever must review an open request."""
        return sorted(set(self.permissions.get("approve", [])) & set(self.permissions.get("reject", [])))


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
