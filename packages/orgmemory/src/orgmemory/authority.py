"""Effective-dated authority: which roles an actor held at a given time.

``policy.json`` is the baseline. Its ``actors`` hold their roles from the beginning of time, or from its
optional ``as_of`` (before that no actor holds a role). The optional ``policy-log.jsonl`` changes roles
over time: each entry grants a role to, or removes a role from, one actor at its effective time. Rules
(docs/design/dated-policy.md):

- Entries apply in effective-time order; entries with the same time apply in file order.
- An entry applies to every event at or after its time: at equal times, role changes come before events.
- An entry's actor must hold a role permitted to ``manage_roles`` at the entry's time, counting the entries
  before it. Bootstrap: the first managers are the ones policy.json names.
- A grant names a role policy.json already mentions and that the subject does not hold at that time; a
  ``revoke_role`` removes a role the subject holds. No entry may take effect before ``as_of``.

Every rule is checked over the whole log, including entries after any ``--as-of`` cutoff, and a violation
rejects the dataset: the log is trusted configuration, so an entry that does not hold is an error, never
an ignored attempt. ``permissions`` are not dated.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime
from typing import Optional, Sequence

from memcore import EvidenceSpan

from .models import MANAGE_ROLES, DatedPolicy, Policy, PolicyChange, RoleChange

POLICY_LOG = "policy-log.jsonl"


def _names(roles: Sequence[str]) -> str:
    return ", ".join(roles) or "none"


class Authority:
    def __init__(self, policy: Policy, changes: Optional[Sequence[PolicyChange]] = None):
        self.policy = policy
        # Dated: a policy log exists (even an empty one) or policy.json sets as_of.
        self.dated = changes is not None or policy.as_of is not None
        ordered = [c for _, c in sorted(enumerate(changes or ()), key=lambda pair: (pair[1].timestamp, pair[0]))]
        self._times: dict[str, list[datetime]] = {}
        self._roles: dict[str, list[list[str]]] = {}
        # (change, roles of its actor, roles of its subject before, after), in effective order
        self.applied: list[tuple[PolicyChange, list[str], list[str], list[str]]] = []
        known = policy.known_roles
        for change in ordered:
            where = f"policy-log entry {change.id} at {change.timestamp.isoformat()}"
            if policy.as_of is not None and change.timestamp < policy.as_of:
                raise ValueError(f"{where}: takes effect before the policy baseline (policy.json as_of "
                                 f"{policy.as_of.isoformat()})")
            manager = self.roles(change.actor, change.timestamp)
            if not set(manager) & set(policy.permitted(MANAGE_ROLES)):
                raise ValueError(f"{where}: {change.actor} (roles: {_names(manager)}) may not {MANAGE_ROLES}")
            before = self.roles(change.subject_actor, change.timestamp)
            if change.action == "grant":
                if change.role not in known:
                    raise ValueError(f"{where}: unknown role {change.role!r}; policy.json does not name it")
                if change.role in before:
                    raise ValueError(f"{where}: {change.subject_actor} already holds {change.role}")
                after = [*before, change.role]
            else:
                if change.role not in before:
                    raise ValueError(f"{where}: {change.subject_actor} does not hold {change.role}")
                after = [role for role in before if role != change.role]
            self._times.setdefault(change.subject_actor, []).append(change.timestamp)
            self._roles.setdefault(change.subject_actor, []).append(after)
            self.applied.append((change, manager, before, after))

    @property
    def baseline(self) -> Optional[datetime]:
        return self.policy.as_of

    def before_baseline(self, at: datetime) -> bool:
        return self.policy.as_of is not None and at < self.policy.as_of

    def roles(self, actor: str, at: Optional[datetime] = None) -> list[str]:
        """Roles held at ``at``: the baseline plus every change effective at or before it (None: all changes)."""
        if at is not None and self.before_baseline(at):
            return []
        times = self._times.get(actor, [])
        index = len(times) if at is None else bisect_right(times, at)
        return list(self._roles[actor][index - 1]) if index else list(self.policy.roles(actor))

    def allows(self, actor: str, action: str, at: datetime) -> bool:
        return bool(set(self.roles(actor, at)) & set(self.policy.permitted(action)))

    def granted(self) -> set[str]:
        """Actors the policy log grants a role to."""
        return {change.subject_actor for change, *_ in self.applied if change.action == "grant"}

    def summary(self, evidence: dict[str, EvidenceSpan], as_of: Optional[datetime] = None) -> Optional[DatedPolicy]:
        """The baseline and the changes applied up to ``as_of``, with their evidence; None when not dated."""
        if not self.dated:
            return None
        return DatedPolicy(baseline=self.policy.as_of, role_changes=[
            RoleChange(change_id=change.id, timestamp=change.timestamp, actor=change.actor, actor_roles=manager,
                       action=change.action, subject_actor=change.subject_actor, role=change.role,
                       roles_before=before, roles_after=after, note=change.note,
                       evidence=[evidence[i] for i in change.evidence_ids])
            for change, manager, before, after in self.applied if as_of is None or change.timestamp <= as_of])
