# Organizational decision audit

| Kind | High | Medium | Low |
|---|---|---|---|
| `decision_reversed` | 0 | 0 | 1 |
| `invalid_supersession` | 0 | 0 | 0 |
| `risk_after_approval` | 0 | 0 | 0 |
| `self_approval` | 0 | 0 | 0 |
| `simultaneous_decisions` | 0 | 0 | 0 |
| `stale_pending_approval` | 0 | 2 | 0 |
| `stale_reconsideration` | 0 | 0 | 0 |
| `unauthorized_attempts` | 0 | 3 | 0 |
| `unresolved_dependency` | 0 | 0 | 0 |
| **Total** | 0 | 5 | 1 |

- Audited 30 of 30 events on 7 subject(s), as of 2026-10-31T00:00:00+00:00
- Stale after 14 days, measured at 2026-10-31T00:00:00+00:00
- Mode: **read_only** (the event log was not modified)

## Findings

### MEDIUM · stale_pending_approval · ACME-4

awaiting a decision from product\_manager since 2026-10-10T09:00:00+00:00 (status proposed): 20d 15h at 2026-10-31T00:00:00+00:00, more than 14 days

- `jira:ACME-4/transition/20402` at 2026-10-10T09:00:00+00:00: jira:61a0c3e5f2b1d40069a1b2c3 (roles: product\_manager) · revoke · applied
  - evidence `jira:ACME-4/transition/20402/fact` · `sources/jira/ACME-4/transition-20402-6b0934847273.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10004/changelog · sha256 `6b0934847273`
    > Jira transition 20402 on ACME-4 by jira:61a0c3e5f2b1d40069a1b2c3 (atlassian) at 2026-10-10T09:00:00.000Z: status 10003 to 10005

### MEDIUM · stale_pending_approval · ACME-6

awaiting a decision from product\_manager since 2026-10-06T12:00:00+00:00 (status proposed): 24d 12h at 2026-10-31T00:00:00+00:00, more than 14 days

- `jira:ACME-6/created` at 2026-10-06T12:00:00+00:00: jira:5f8e7d6c5b4a39281706f5e4 (roles: engineer) · request · applied
  - evidence `jira:ACME-6/issue/10006@08608240514f/fact` · `sources/jira/ACME-6/issue-10006-8a27f83ee7fb.txt:1` · https://acme.atlassian.net/browse/ACME-6 · sha256 `8a27f83ee7fb`
    > Jira issue ACME-6 (id 10006) created by jira:5f8e7d6c5b4a39281706f5e4 (atlassian) at 2026-10-06T12:00:00.000Z
  - evidence `jira:ACME-6/issue/10006@08608240514f/text` · `sources/jira/ACME-6/issue-10006-8a27f83ee7fb.txt:4` · https://acme.atlassian.net/browse/ACME-6 · sha256 `8a27f83ee7fb`
    > Archive old reports

### MEDIUM · unauthorized_attempts · actor jira:5a1b2c3d4e5f60718293a4b5

1 attempt(s) the policy ignored (approve ×1) on 1 subject(s): ACME-6; roles: none

- `jira:ACME-6/transition/20601` on ACME-6 at 2026-10-06T13:00:00+00:00: jira:5a1b2c3d4e5f60718293a4b5 (roles: none) · approve · unauthorized
  - evidence `jira:ACME-6/transition/20601/fact` · `sources/jira/ACME-6/transition-20601-0ac5eb376975.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10006/changelog · sha256 `0ac5eb376975`
    > Jira transition 20601 on ACME-6 by jira:5a1b2c3d4e5f60718293a4b5 (atlassian) at 2026-10-06T13:00:00.000Z: status 10000 to 10003

### MEDIUM · unauthorized_attempts · actor jira:5d3c2b1a0f9e8d7c6b5a4f3e

2 attempt(s) the policy ignored (approve ×1, request ×1) on 1 subject(s): ACME-3; roles: none

- `jira:ACME-3/created` on ACME-3 at 2026-10-05T11:00:00+00:00: jira:5d3c2b1a0f9e8d7c6b5a4f3e (roles: none) · request · unauthorized
  - evidence `jira:ACME-3/issue/10003@f2b7419dccda/fact` · `sources/jira/ACME-3/issue-10003-35ab3f918c44.txt:1` · https://acme.atlassian.net/browse/ACME-3 · sha256 `35ab3f918c44`
    > Jira issue ACME-3 (id 10003) created by jira:5d3c2b1a0f9e8d7c6b5a4f3e (atlassian) at 2026-10-05T11:00:00.000Z
  - evidence `jira:ACME-3/issue/10003@f2b7419dccda/text` · `sources/jira/ACME-3/issue-10003-35ab3f918c44.txt:4` · https://acme.atlassian.net/browse/ACME-3 · sha256 `35ab3f918c44`
    > Change the export format
- `jira:ACME-3/transition/20302` on ACME-3 at 2026-10-05T11:25:00+00:00: jira:5d3c2b1a0f9e8d7c6b5a4f3e (roles: none) · approve · unauthorized
  - evidence `jira:ACME-3/transition/20302/fact` · `sources/jira/ACME-3/transition-20302-fef038805ecb.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10003/changelog · sha256 `fef038805ecb`
    > Jira transition 20302 on ACME-3 by jira:5d3c2b1a0f9e8d7c6b5a4f3e (atlassian) at 2026-10-05T11:25:00.000Z: status 10000 to 10003

### MEDIUM · unauthorized_attempts · actor jira:712020:3c9d1e2f-4a5b-4c6d-8e7f-90a1b2c3d4e5

1 attempt(s) the policy ignored (approve ×1) on 1 subject(s): ACME-1; roles: account\_manager

- `jira:ACME-1/transition/20103` on ACME-1 at 2026-10-08T10:00:00+00:00: jira:712020:3c9d1e2f-4a5b-4c6d-8e7f-90a1b2c3d4e5 (roles: account\_manager) · approve · unauthorized
  - evidence `jira:ACME-1/transition/20103/fact` · `sources/jira/ACME-1/transition-20103-488bee13f0f9.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10001/changelog · sha256 `488bee13f0f9`
    > Jira transition 20103 on ACME-1 by jira:712020:3c9d1e2f-4a5b-4c6d-8e7f-90a1b2c3d4e5 (atlassian) at 2026-10-08T10:00:00.000Z: status 10004 to 10003

### LOW · decision_reversed · ACME-1

reject by jira:61a0c3e5f2b1d40069a1b2c3 then approve by jira:61a0c3e5f2b1d40069a1b2c3; the same actor changed the decision

- `jira:ACME-1/transition/20102` at 2026-10-07T15:00:00+00:00: jira:61a0c3e5f2b1d40069a1b2c3 (roles: product\_manager) · reject · applied
  - evidence `jira:ACME-1/transition/20102/fact` · `sources/jira/ACME-1/transition-20102-3468ff2d8fed.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10001/changelog · sha256 `3468ff2d8fed`
    > Jira transition 20102 on ACME-1 by jira:61a0c3e5f2b1d40069a1b2c3 (atlassian) at 2026-10-07T15:00:00.000Z: status 10002 to 10004
- `jira:ACME-1/transition/20105` at 2026-10-10T16:00:00+00:00: jira:61a0c3e5f2b1d40069a1b2c3 (roles: product\_manager) · approve · applied
  - evidence `jira:ACME-1/transition/20105/fact` · `sources/jira/ACME-1/transition-20105-35b880d158a7.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10001/changelog · sha256 `35b880d158a7`
    > Jira transition 20105 on ACME-1 by jira:61a0c3e5f2b1d40069a1b2c3 (atlassian) at 2026-10-10T16:00:00.000Z: status 10007 to 10003

## Notes

- Findings restate the replayed log and policy. They are leads for a person to review, not verdicts.
- The log has no event that resolves a risk, so a risk stays on record after it is addressed.
