# Organizational decision audit

| Kind | High | Medium | Low |
|---|---|---|---|
| `decision_reversed` | 0 | 0 | 0 |
| `invalid_supersession` | 0 | 0 | 0 |
| `risk_after_approval` | 0 | 0 | 0 |
| `self_approval` | 0 | 0 | 0 |
| `simultaneous_decisions` | 0 | 0 | 0 |
| `stale_pending_approval` | 0 | 0 | 0 |
| `stale_reconsideration` | 0 | 1 | 0 |
| `unauthorized_attempts` | 0 | 1 | 0 |
| `unresolved_dependency` | 0 | 0 | 0 |
| **Total** | 0 | 2 | 0 |

- Audited 5 of 5 events on 1 subject(s), as of 2026-10-31T00:00:00+00:00
- Stale after 14 days, measured at 2026-10-31T00:00:00+00:00
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

## Notes

- Findings restate the replayed log and policy. They are leads for a person to review, not verdicts.
- The log has no event that resolves a risk, so a risk stays on record after it is addressed.
