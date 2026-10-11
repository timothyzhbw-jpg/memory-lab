# Organizational decision changes

| Subject | Status before | Status after | Changes |
|---|---|---|---|
| Feature A | proposed | rejected | `decision` ×1, `pending_approval_opened` ×1, `pending_approval_resolved` ×1, `reconsideration_opened` ×1, `risk_raised` ×1, `unauthorized_attempt` ×1 |
| **Total** | | | `decision` ×1, `pending_approval_opened` ×1, `pending_approval_resolved` ×1, `reconsideration_opened` ×1, `risk_raised` ×1, `unauthorized_attempt` ×1 |

- Period: after 2026-10-05T23:59:59+00:00, up to and including 2026-10-08T23:59:59+00:00
- 4 of 5 events fall in the period, on 1 subject(s); 1 subject(s) changed
- Mode: **read_only** (the event log was not modified)

## Feature A

Status: proposed → rejected

### decision

proposed → rejected

- `ev-wed-reject` at 2026-10-07T15:30:00+00:00: zhou (roles: product\_manager) · reject · applied
  - evidence `wed` · `sources/2026-10-07-decision-log.txt:3` · synthetic://decision-log/2026-10-07 · sha256 `fb85f034a99f`
    > Feature A is rejected for the November release until the billing migration lands.

### pending_approval_opened

pending approval from product\_manager since 2026-10-08T08:45:00+00:00; still open at the end of the period

- `ev-thu-request` at 2026-10-08T08:45:00+00:00: buyer@acme (roles: customer) · request · applied
  - evidence `thu` · `sources/2026-10-08-customer-followup.txt:4` · synthetic://email/acme/2 · sha256 `20c93afab5f3`
    > Please reconsider Feature A; the audit deadline has not moved.

### pending_approval_resolved

pending approval from product\_manager since 2026-10-05T09:12:00+00:00; closed by reject by zhou at 2026-10-07T15:30:00+00:00

- `ev-mon-request` at 2026-10-05T09:12:00+00:00: buyer@acme (roles: customer) · request · applied
  - evidence `mon` · `sources/2026-10-05-customer-email.txt:4` · synthetic://email/acme/1 · sha256 `eabb15142ea3`
    > We need Feature A (bulk export of order history) before our November audit.
- `ev-wed-reject` at 2026-10-07T15:30:00+00:00: zhou (roles: product\_manager) · reject · applied
  - evidence `wed` · `sources/2026-10-07-decision-log.txt:3` · synthetic://decision-log/2026-10-07 · sha256 `fb85f034a99f`
    > Feature A is rejected for the November release until the billing migration lands.

### reconsideration_opened

reconsideration requested since 2026-10-08T08:45:00+00:00; still open at the end of the period

- `ev-thu-request` at 2026-10-08T08:45:00+00:00: buyer@acme (roles: customer) · request · applied
  - evidence `thu` · `sources/2026-10-08-customer-followup.txt:4` · synthetic://email/acme/2 · sha256 `20c93afab5f3`
    > Please reconsider Feature A; the audit deadline has not moved.

### risk_raised

raised by lin: "Bulk export reads the billing schema, which is mid-migration"

- `ev-tue-risk` at 2026-10-06T10:00:00+00:00: lin (roles: engineer) · raise_risk · applied
  - evidence `tue` · `sources/2026-10-06-standup.txt:3` · synthetic://notes/standup/2026-10-06 · sha256 `53623f3b9f24`
    > Feature A is risky: bulk export reads the billing schema, which is mid-migration.

### unauthorized_attempt

ignored: roles account\_manager may not approve

- `ev-thu-sales-approve` at 2026-10-08T11:20:00+00:00: wu (roles: account\_manager) · approve · unauthorized
  - evidence `thu-sales` · `sources/2026-10-08-sales-chat.txt:3` · synthetic://chat/sales/2026-10-08 · sha256 `ae8240ffb8d4`
    > I told Acme Feature A is approved, let's just ship it.

## Notes

- Changes restate two replays of the log, at the start and at the end of the period, and the audit trail between them. An event at exactly the start time belongs to the earlier replay.
- Only what replay applied changes state: an event the policy ignored is listed as an `unauthorized_attempt`, and a `comment` is not listed.
