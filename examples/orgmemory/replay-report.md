# Organizational memory replay

- Replayed 5 of 5 events, as of end of log
- Mode: **read_only** (the event log was not modified)

## Feature A

```yaml
subject: Feature A
current_status: rejected
reconsideration_requested: true
pending_approval: [product_manager]
dependencies: []
open_risks: ["Bulk export reads the billing schema, which is mid-migration"]
last_authorized_decision: ev-wed-reject (reject by zhou)
unauthorized_change_attempts: [ev-thu-sales-approve]
```

**Why this is the current status**

- `ev-wed-reject`: zhou (roles: product_manager) chose **reject** at 2026-10-07T15:30:00+00:00
  - evidence `wed` · `sources/2026-10-07-decision-log.txt:3` · synthetic://decision-log/2026-10-07 · sha256 `fb85f034a99f`
    > Feature A is rejected for the November release until the billing migration lands.

**Ignored: actor lacked authority**

- `ev-thu-sales-approve`: wu tried to approve; actor roles ['account_manager'] may not approve

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T09:12:00+00:00 | `ev-mon-request` | buyer@acme (customer) | request | applied | proposed → proposed |
| 2026-10-06T10:00:00+00:00 | `ev-tue-risk` | lin (engineer) | raise_risk | applied | proposed → proposed |
| 2026-10-07T15:30:00+00:00 | `ev-wed-reject` | zhou (product_manager) | reject | applied | proposed → rejected |
| 2026-10-08T08:45:00+00:00 | `ev-thu-request` | buyer@acme (customer) | request | applied | rejected → rejected |
| 2026-10-08T11:20:00+00:00 | `ev-thu-sales-approve` | wu (account_manager) | approve | unauthorized | rejected → rejected |

