# Organizational decision audit

| Kind | High | Medium | Low |
|---|---|---|---|
| `decision_reversed` | 0 | 0 | 0 |
| `invalid_supersession` | 0 | 0 | 0 |
| `risk_after_approval` | 0 | 0 | 0 |
| `role_changed_after_decision` | 0 | 0 | 1 |
| `self_approval` | 0 | 0 | 0 |
| `simultaneous_decisions` | 0 | 0 | 0 |
| `stale_pending_approval` | 0 | 0 | 0 |
| `stale_reconsideration` | 0 | 1 | 0 |
| `unauthorized_attempts` | 0 | 1 | 0 |
| `unresolved_dependency` | 0 | 0 | 0 |
| **Total** | 0 | 2 | 1 |

- Audited 5 of 5 events on 1 subject(s), as of 2026-10-31T00:00:00+00:00
- Stale after 14 days, measured at 2026-10-31T00:00:00+00:00
- Roles: policy.json from the beginning of the log, then 2 role change(s) from policy-log.jsonl; each event is judged by the roles its actor held at its time
- Mode: **read_only** (the event log was not modified)

## Findings

### MEDIUM · stale_reconsideration · Feature A

reconsideration awaiting a decision from product\_manager since 2026-10-08T08:45:00+00:00 (status rejected): 22d 15h at 2026-10-31T00:00:00+00:00, more than 14 days

- `ev-wed-reject` at 2026-10-07T15:30:00+00:00: zhou (roles: product\_manager) · reject · applied
  - evidence `wed` · `sources/2026-10-07-decision-log.txt:3` · synthetic://decision-log/2026-10-07 · sha256 `fb85f034a99f`
    > Feature A is rejected for the November release until the billing migration lands.
- `ev-thu-request` at 2026-10-08T08:45:00+00:00: buyer@acme (roles: customer) · request · applied
  - evidence `thu` · `sources/2026-10-08-customer-followup.txt:4` · synthetic://email/acme/2 · sha256 `20c93afab5f3`
    > Please reconsider Feature A; the audit deadline has not moved.

### MEDIUM · unauthorized_attempts · actor wu

1 attempt(s) the policy ignored (approve ×1) on 1 subject(s): Feature A; roles: account\_manager

- `ev-thu-sales-approve` on Feature A at 2026-10-08T11:20:00+00:00: wu (roles: account\_manager) · approve · unauthorized
  - evidence `thu-sales` · `sources/2026-10-08-sales-chat.txt:3` · synthetic://chat/sales/2026-10-08 · sha256 `ae8240ffb8d4`
    > I told Acme Feature A is approved, let's just ship it.

### LOW · role_changed_after_decision · Feature A

reject ev-wed-reject by zhou (roles: product\_manager) is the current decision; zhou later lost product\_manager (pl-zhou-leaves at 2026-10-12T09:00:00+00:00); roles as of 2026-10-31T00:00:00+00:00: none. The decision stands: it is judged by the roles held when it was made

- `ev-wed-reject` at 2026-10-07T15:30:00+00:00: zhou (roles: product\_manager) · reject · applied
  - evidence `wed` · `sources/2026-10-07-decision-log.txt:3` · synthetic://decision-log/2026-10-07 · sha256 `fb85f034a99f`
    > Feature A is rejected for the November release until the billing migration lands.
- role change `pl-zhou-leaves` at 2026-10-12T09:00:00+00:00: chen (roles: org\_admin) removed product\_manager from zhou; roles of zhou: product\_manager → none; note: "moved to the platform team"
  - evidence `org-zhou` · `sources/2026-10-09-org-change.txt:4` · synthetic://org/notice/2026-10-09 · sha256 `671357a1fd14`
    > Zhou leaves the product manager role on 2026-10-12 09:00 UTC to join the platform team.

## Role changes

- role change `pl-wu-pm` at 2026-10-09T09:00:00+00:00: chen (roles: org\_admin) granted product\_manager to wu; roles of wu: account\_manager → account\_manager, product\_manager
  - evidence `org-wu` · `sources/2026-10-09-org-change.txt:3` · synthetic://org/notice/2026-10-09 · sha256 `671357a1fd14`
    > Wu is also product manager for Feature A from 2026-10-09 09:00 UTC.
- role change `pl-zhou-leaves` at 2026-10-12T09:00:00+00:00: chen (roles: org\_admin) removed product\_manager from zhou; roles of zhou: product\_manager → none; note: "moved to the platform team"
  - evidence `org-zhou` · `sources/2026-10-09-org-change.txt:4` · synthetic://org/notice/2026-10-09 · sha256 `671357a1fd14`
    > Zhou leaves the product manager role on 2026-10-12 09:00 UTC to join the platform team.

## Notes

- Findings restate the replayed log and policy. They are leads for a person to review, not verdicts.
- The log has no event that resolves a risk, so a risk stays on record after it is addressed.
- Roles are effective-dated: a role granted or removed later does not change an earlier event's outcome, and `role_changed_after_decision` is informational.
