# Organizational decision audit

| Kind | High | Medium | Low |
|---|---|---|---|
| `decision_reversed` | 0 | 0 | 0 |
| `invalid_supersession` | 0 | 0 | 0 |
| `risk_after_approval` | 1 | 0 | 0 |
| `self_approval` | 0 | 0 | 0 |
| `simultaneous_decisions` | 0 | 0 | 0 |
| `stale_pending_approval` | 0 | 0 | 0 |
| `stale_reconsideration` | 0 | 0 | 0 |
| `unauthorized_attempts` | 0 | 2 | 0 |
| `unresolved_dependency` | 0 | 0 | 0 |
| **Total** | 1 | 2 | 0 |

- Audited 11 of 11 events on 4 subject(s), as of 2026-10-11T00:00:00+00:00
- Stale after 14 days, measured at 2026-10-11T00:00:00+00:00
- Mode: **read_only** (the event log was not modified)

## Findings

### HIGH · risk_after_approval · memory:m-f0a6035e

risk raised by memfalsify after the approval by zhou: "candidate\_counterevidence at MEMORY.md:4: The main database runs PostgreSQL 15."

- `write-approve-m-f0a6035e-66b9cc9e87db` at 2026-10-06T09:00:00+00:00: zhou (roles: memory\_steward) · approve · applied
  - evidence `m-f0a6035e-b17a3549d0ee-line` · `sources/b17a3549d0ee/MEMORY.md:4` · git:b17a3549d0ee:MEMORY.md · sha256 `31d7d8e93a46`
    > The main database runs PostgreSQL 15.
  - evidence `m-f0a6035e-66b9cc9e87db-introduced` · `sources/b17a3549d0ee/memfalsify-history.md:24` · memfalsify-history:b17a3549d0ee · sha256 `771de722e7b1`
    > Introduced: \`66b9cc9e87db\` 2026-10-06 · lin · Remember the database version (approved-by: zhou)
- `finding-candidate_counterevidence-m-f0a6035e` at 2026-10-10T23:59:59+00:00: memfalsify (roles: auditor) · raise_risk · applied
  - evidence `m-f0a6035e-78e1f22d-12` · `sources/b17a3549d0ee/docs/database.md:3` · git:b17a3549d0ee:docs/database.md · sha256 `78e1f22d39c6`
    > The main database runs PostgreSQL 16.

### MEDIUM · unauthorized_attempts · actor agent-7

1 attempt(s) the policy ignored (approve ×1) on 1 subject(s): memory:m-314c50bc; roles: agent

- `write-approve-m-314c50bc-ca1ca91a0789` on memory:m-314c50bc at 2026-10-08T09:00:00+00:00: agent-7 (roles: agent) · approve · unauthorized
  - evidence `m-314c50bc-b17a3549d0ee-line` · `sources/b17a3549d0ee/MEMORY.md:6` · git:b17a3549d0ee:MEMORY.md · sha256 `31d7d8e93a46`
    > The nightly export job finishes before 04:00 UTC.
  - evidence `m-314c50bc-ca1ca91a0789-introduced` · `sources/b17a3549d0ee/memfalsify-history.md:42` · memfalsify-history:b17a3549d0ee · sha256 `771de722e7b1`
    > Introduced: \`ca1ca91a0789\` 2026-10-08 · agent-7 · Remember when the nightly export finishes

### MEDIUM · unauthorized_attempts · actor wu

1 attempt(s) the policy ignored (approve ×1) on 1 subject(s): memory:m-9efe3be5; roles: account\_manager

- `write-approve-m-9efe3be5-7ed0c4c7f4b1` on memory:m-9efe3be5 at 2026-10-07T09:00:00+00:00: wu (roles: account\_manager) · approve · unauthorized
  - evidence `m-9efe3be5-b17a3549d0ee-line` · `sources/b17a3549d0ee/MEMORY.md:5` · git:b17a3549d0ee:MEMORY.md · sha256 `31d7d8e93a46`
    > The public API rate limit is 100 requests per minute.
  - evidence `m-9efe3be5-7ed0c4c7f4b1-introduced` · `sources/b17a3549d0ee/memfalsify-history.md:33` · memfalsify-history:b17a3549d0ee · sha256 `771de722e7b1`
    > Introduced: \`7ed0c4c7f4b1\` 2026-10-07 · agent-7 · Remember the API rate limit (approved-by: wu)

## Notes

- Findings restate the replayed log and policy. They are leads for a person to review, not verdicts.
- The log has no event that resolves a risk, so a risk stays on record after it is addressed.
