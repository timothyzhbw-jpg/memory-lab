# Organizational memory replay

- Replayed 30 of 30 events, as of end of log
- Mode: **read_only** (the event log was not modified)

## ACME-1

```yaml
subject: ACME-1
current_status: approved
reconsideration_requested: false
pending_approval: []
dependencies: []
open_risks: []
last_authorized_decision: "jira:ACME-1/transition/20105 (approve by jira:61a0c3e5f2b1d40069a1b2c3)"
unauthorized_change_attempts: []
```

**Why this is the current status**

- `jira:ACME-1/transition/20105`: jira:61a0c3e5f2b1d40069a1b2c3 (roles: product_manager) chose **approve** at 2026-10-10T16:00:00+00:00
  - evidence `jira:ACME-1/transition/20105/fact` · `sources/jira/ACME-1/transition-20105-35b880d158a7.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10001/changelog · sha256 `35b880d158a7`
    > Jira transition 20105 on ACME-1 by jira:61a0c3e5f2b1d40069a1b2c3 (atlassian) at 2026-10-10T16:00:00.000Z: status 10007 to 10003

**Conflicts**

- decision_reversed ['jira:ACME-1/transition/20102', 'jira:ACME-1/transition/20105']: reject by jira:61a0c3e5f2b1d40069a1b2c3 then approve by jira:61a0c3e5f2b1d40069a1b2c3

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T09:00:00+00:00 | `jira:ACME-1/created` | jira:qm:4a6e0c2f-8d1b-4e3a-9c5f-7b2d1e0a3c4b:5e7f9a1b-2c3d-4e5f-8a9b-0c1d2e3f4a5b (customer) | request | applied | proposed → proposed |
| 2026-10-06T10:00:00+00:00 | `jira:ACME-1/comment/10100` | jira:5f8e7d6c5b4a39281706f5e4 (engineer) | comment | applied | proposed → proposed |
| 2026-10-06T11:00:00+00:00 | `jira:ACME-1/transition/20101` | jira:5f8e7d6c5b4a39281706f5e4 (engineer) | comment | applied | proposed → proposed |
| 2026-10-07T15:00:00+00:00 | `jira:ACME-1/transition/20102` | jira:61a0c3e5f2b1d40069a1b2c3 (product_manager) | reject | applied | proposed → rejected |
| 2026-10-08T09:00:00+00:00 | `jira:ACME-1/comment/10101` | jira:qm:4a6e0c2f-8d1b-4e3a-9c5f-7b2d1e0a3c4b:5e7f9a1b-2c3d-4e5f-8a9b-0c1d2e3f4a5b (customer) | comment | applied | rejected → rejected |
| 2026-10-08T10:00:00+00:00 | `jira:ACME-1/transition/20103` | jira:712020:3c9d1e2f-4a5b-4c6d-8e7f-90a1b2c3d4e5 (account_manager) | approve | unauthorized | rejected → rejected |
| 2026-10-08T11:00:00+00:00 | `jira:ACME-1/transition/20104` | jira:qm:4a6e0c2f-8d1b-4e3a-9c5f-7b2d1e0a3c4b:5e7f9a1b-2c3d-4e5f-8a9b-0c1d2e3f4a5b (customer) | reconsider | applied | rejected → rejected |
| 2026-10-10T16:00:00+00:00 | `jira:ACME-1/transition/20105` | jira:61a0c3e5f2b1d40069a1b2c3 (product_manager) | approve | applied | rejected → approved |

## ACME-2

```yaml
subject: ACME-2
current_status: approved
reconsideration_requested: false
pending_approval: []
dependencies: []
open_risks: []
last_authorized_decision: "jira:ACME-2/transition/20204 (approve by jira:61a0c3e5f2b1d40069a1b2c3)"
unauthorized_change_attempts: []
```

**Why this is the current status**

- `jira:ACME-2/transition/20204`: jira:61a0c3e5f2b1d40069a1b2c3 (roles: product_manager) chose **approve** at 2026-10-07T09:00:00+00:00
  - evidence `jira:ACME-2/transition/20204/fact` · `sources/jira/ACME-2/transition-20204-c8eebafb7ae4.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10002/changelog · sha256 `c8eebafb7ae4`
    > Jira transition 20204 on ACME-2 by jira:61a0c3e5f2b1d40069a1b2c3 (atlassian) at 2026-10-07T09:00:00.000Z: status 10002 to 10003

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T10:00:00+00:00 | `jira:ACME-2/created` | jira:5f8e7d6c5b4a39281706f5e4 (engineer) | request | applied | proposed → proposed |
| 2026-10-06T09:00:00+00:00 | `jira:ACME-2/transition/20201` | jira:5f8e7d6c5b4a39281706f5e4 (engineer) | comment | applied | proposed → proposed |
| 2026-10-06T09:00:30.250000+00:00 | `jira:ACME-2/transition/20202` | jira:557058:f58131cb-b67d-43c7-b30d-6b58d40bd077 (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-06T16:00:00+00:00 | `jira:ACME-2/transition/20203` | jira:61a0c3e5f2b1d40069a1b2c3 (product_manager) | comment | applied | proposed → proposed |
| 2026-10-07T09:00:00+00:00 | `jira:ACME-2/transition/20204` | jira:61a0c3e5f2b1d40069a1b2c3 (product_manager) | approve | applied | proposed → approved |

## ACME-3

```yaml
subject: ACME-3
current_status: proposed
reconsideration_requested: false
pending_approval: []
dependencies: []
open_risks: []
last_authorized_decision: null
unauthorized_change_attempts: ["jira:ACME-3/transition/20302"]
```

No authorized decision yet.

**Ignored: actor lacked authority**

- `jira:ACME-3/transition/20302`: jira:5d3c2b1a0f9e8d7c6b5a4f3e tried to approve; actor roles none may not approve

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T11:00:00+00:00 | `jira:ACME-3/created` | jira:5d3c2b1a0f9e8d7c6b5a4f3e (no role) | request | unauthorized | proposed → proposed |
| 2026-10-05T11:05:00+00:00 | `jira:ACME-3/comment/10300` | jira:5d3c2b1a0f9e8d7c6b5a4f3e (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-05T11:10:00+00:00 | `jira:ACME-3/comment/10301` | jira:5d3c2b1a0f9e8d7c6b5a4f3e (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-05T11:15:00+00:00 | `jira:ACME-3/comment/10302` | jira:5d3c2b1a0f9e8d7c6b5a4f3e (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-05T11:25:00+00:00 | `jira:ACME-3/transition/20302` | jira:5d3c2b1a0f9e8d7c6b5a4f3e (no role) | approve | unauthorized | proposed → proposed |
| 2026-10-10T09:00:00+00:00 | `jira:ACME-3/comment/10303` | jira:5f8e7d6c5b4a39281706f5e4 (engineer) | comment | applied | proposed → proposed |

## ACME-4

```yaml
subject: ACME-4
current_status: proposed
reconsideration_requested: false
pending_approval: [product_manager]
dependencies: []
open_risks: []
last_authorized_decision: "jira:ACME-4/transition/20402 (revoke by jira:61a0c3e5f2b1d40069a1b2c3)"
unauthorized_change_attempts: []
```

**Why this is the current status**

- `jira:ACME-4/transition/20402`: jira:61a0c3e5f2b1d40069a1b2c3 (roles: product_manager) chose **revoke** at 2026-10-10T09:00:00+00:00
  - evidence `jira:ACME-4/transition/20402/fact` · `sources/jira/ACME-4/transition-20402-6b0934847273.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10004/changelog · sha256 `6b0934847273`
    > Jira transition 20402 on ACME-4 by jira:61a0c3e5f2b1d40069a1b2c3 (atlassian) at 2026-10-10T09:00:00.000Z: status 10003 to 10005

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-06T08:00:00+00:00 | `jira:ACME-4/created` | jira:5f8e7d6c5b4a39281706f5e4 (engineer) | request | applied | proposed → proposed |
| 2026-10-08T14:00:00+00:00 | `jira:ACME-4/transition/20401` | jira:61a0c3e5f2b1d40069a1b2c3 (product_manager) | approve | applied | proposed → approved |
| 2026-10-10T09:00:00+00:00 | `jira:ACME-4/transition/20402` | jira:61a0c3e5f2b1d40069a1b2c3 (product_manager) | revoke | applied | approved → proposed |

## ACME-5

```yaml
subject: ACME-5
current_status: rejected
reconsideration_requested: false
pending_approval: []
dependencies: []
open_risks: []
last_authorized_decision: "jira:ACME-5/transition/20501 (reject by jira:61a0c3e5f2b1d40069a1b2c3)"
unauthorized_change_attempts: []
```

**Why this is the current status**

- `jira:ACME-5/transition/20501`: jira:61a0c3e5f2b1d40069a1b2c3 (roles: product_manager) chose **reject** at 2026-10-07T10:00:00+00:00
  - evidence `jira:ACME-5/transition/20501/fact` · `sources/jira/ACME-5/transition-20501-fa4ab53ea648.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10005/changelog · sha256 `fa4ab53ea648`
    > Jira transition 20501 on ACME-5 by jira:61a0c3e5f2b1d40069a1b2c3 (atlassian) at 2026-10-07T10:00:00.000Z: status 10000 to 10004

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-06T09:00:00+00:00 | `jira:ACME-5/created` | jira:712020:3c9d1e2f-4a5b-4c6d-8e7f-90a1b2c3d4e5 (account_manager) | request | applied | proposed → proposed |
| 2026-10-07T10:00:00+00:00 | `jira:ACME-5/transition/20501` | jira:61a0c3e5f2b1d40069a1b2c3 (product_manager) | reject | applied | proposed → rejected |
| 2026-10-10T10:00:00+00:00 | `jira:ACME-5/comment/10500` | jira:712020:3c9d1e2f-4a5b-4c6d-8e7f-90a1b2c3d4e5 (account_manager) | comment | applied | rejected → rejected |

## ACME-6

```yaml
subject: ACME-6
current_status: proposed
reconsideration_requested: false
pending_approval: [product_manager]
dependencies: []
open_risks: []
last_authorized_decision: null
unauthorized_change_attempts: ["jira:ACME-6/transition/20601"]
```

No authorized decision yet.

**Ignored: actor lacked authority**

- `jira:ACME-6/transition/20601`: jira:5a1b2c3d4e5f60718293a4b5 tried to approve; actor roles none may not approve

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-06T12:00:00+00:00 | `jira:ACME-6/created` | jira:5f8e7d6c5b4a39281706f5e4 (engineer) | request | applied | proposed → proposed |
| 2026-10-06T12:30:00+00:00 | `jira:ACME-6/comment/10600` | jira:5a1b2c3d4e5f60718293a4b5 (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-06T13:00:00+00:00 | `jira:ACME-6/transition/20601` | jira:5a1b2c3d4e5f60718293a4b5 (no role) | approve | unauthorized | proposed → proposed |

## ACME-7

```yaml
subject: ACME-7
current_status: rejected
reconsideration_requested: false
pending_approval: []
dependencies: []
open_risks: []
last_authorized_decision: "jira:ACME-7/transition/20701 (reject by jira:61a0c3e5f2b1d40069a1b2c3)"
unauthorized_change_attempts: []
```

**Why this is the current status**

- `jira:ACME-7/transition/20701`: jira:61a0c3e5f2b1d40069a1b2c3 (roles: product_manager) chose **reject** at 2026-10-10T12:00:00+00:00
  - evidence `jira:ACME-7/transition/20701/fact` · `sources/jira/ACME-7/transition-20701-05e21c792d71.txt:1` · https://acme.atlassian.net/rest/api/3/issue/10007/changelog · sha256 `05e21c792d71`
    > Jira transition 20701 on ACME-7 by jira:61a0c3e5f2b1d40069a1b2c3 (atlassian) at 2026-10-10T12:00:00.000Z: status 10000 to 10004

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-10T08:00:00+00:00 | `jira:ACME-7/created` | jira:qm:4a6e0c2f-8d1b-4e3a-9c5f-7b2d1e0a3c4b:5e7f9a1b-2c3d-4e5f-8a9b-0c1d2e3f4a5b (customer) | request | applied | proposed → proposed |
| 2026-10-10T12:00:00+00:00 | `jira:ACME-7/transition/20701` | jira:61a0c3e5f2b1d40069a1b2c3 (product_manager) | reject | applied | proposed → rejected |

