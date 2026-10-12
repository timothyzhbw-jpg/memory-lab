# Organizational memory replay

- Replayed 59 of 59 events, as of end of log
- Mode: **read_only** (the event log was not modified)

## acme/web#1

```yaml
subject: "acme/web#1"
current_status: rejected
reconsideration_requested: true
pending_approval: [product_manager]
dependencies: ["acme/billing#3"]
open_risks: []
last_authorized_decision: "gh:acme/web#1/event/105 (reject by gh:zhou-pm:5003)"
unauthorized_change_attempts: ["gh:acme/web#1/event/107", "gh:acme/web#1/event/108"]
```

**Why this is the current status**

- `gh:acme/web#1/event/105`: gh:zhou-pm:5003 (roles: product_manager) chose **reject** at 2026-10-07T15:30:00+00:00
  - evidence `gh:acme/web#1/event/105/fact` · `sources/github/acme/web/1/event-105-c2a1c517e9cc.txt:1` · https\://api.github.com/repos/acme/web/issues/events/105 · sha256 `c2a1c517e9cc`
    > GitHub event 105 on acme/web#1 by gh:zhou-pm:5003 (User) at 2026-10-07T15:30:00Z: closed (state_reason not_planned, commit none)

**Ignored: actor lacked authority**

- `gh:acme/web#1/event/107`: gh:wu-sales:5004 tried to approve; actor roles ['account_manager'] may not approve
- `gh:acme/web#1/event/108`: gh:buyer-acme:5001 tried to revoke; actor roles ['customer'] may not revoke

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T09:00:00+00:00 | `gh:acme/web#1/opened` | gh:buyer-acme:5001 (customer) | request | applied | proposed → proposed |
| 2026-10-05T14:00:00+00:00 | `gh:acme/web#1/comment/102` | gh:lin-eng:5002 (engineer) | comment | applied | proposed → proposed |
| 2026-10-06T10:00:00+00:00 | `gh:acme/web#1/event/103` | gh:lin-eng:5002 (engineer) | dependency | applied | proposed → proposed |
| 2026-10-07T15:30:00+00:00 | `gh:acme/web#1/event/105` | gh:zhou-pm:5003 (product_manager) | reject | applied | proposed → rejected |
| 2026-10-08T10:00:00+00:00 | `gh:acme/web#1/comment/106` | gh:buyer-acme:5001 (customer) | comment | applied | rejected → rejected |
| 2026-10-08T16:00:00+00:00 | `gh:acme/web#1/event/107` | gh:wu-sales:5004 (account_manager) | approve | unauthorized | rejected → rejected |
| 2026-10-10T09:00:00+00:00 | `gh:acme/web#1/event/108` | gh:buyer-acme:5001 (customer) | revoke | unauthorized | rejected → rejected |
| 2026-10-10T09:00:00+00:00 | `gh:acme/web#1/event/108/request` | gh:buyer-acme:5001 (customer) | request | applied | rejected → rejected |

## acme/web#2

```yaml
subject: "acme/web#2"
current_status: approved
reconsideration_requested: false
pending_approval: []
dependencies: []
open_risks: []
last_authorized_decision: "gh:acme/web#2/event/207 (approve by gh:zhou-pm:5003)"
unauthorized_change_attempts: []
```

**Why this is the current status**

- `gh:acme/web#2/event/207`: gh:zhou-pm:5003 (roles: product_manager) chose **approve** at 2026-10-07T10:00:00+00:00
  - evidence `gh:acme/web#2/event/207/fact` · `sources/github/acme/web/2/event-207-b9890b2ca38e.txt:1` · https\://api.github.com/repos/acme/web/issues/events/207 · sha256 `b9890b2ca38e`
    > GitHub event 207 on acme/web#2 by gh:zhou-pm:5003 (User) at 2026-10-07T10:00:00Z: merged (commit 54bb654c9e60)

**Conflicts**

- decision_reversed ['gh:acme/web#2/review/203', 'gh:acme/web#2/review/206']: reject by gh:zhou-pm:5003 then approve by gh:zhou-pm:5003

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T10:00:00+00:00 | `gh:acme/web#2/opened` | gh:lin-eng:5002 (engineer) | request | applied | proposed → proposed |
| 2026-10-06T09:00:00+00:00 | `gh:acme/web#2/review/203` | gh:zhou-pm:5003 (product_manager) | reject | applied | proposed → rejected |
| 2026-10-06T15:00:00+00:00 | `gh:acme/web#2/event/204` | gh:lin-eng:5002 (engineer) | request | applied | rejected → rejected |
| 2026-10-06T16:00:00+00:00 | `gh:acme/web#2/review/205` | gh:mallory:6666 (no role) | approve | unauthorized | rejected → rejected |
| 2026-10-07T09:00:00+00:00 | `gh:acme/web#2/review/206` | gh:zhou-pm:5003 (product_manager) | approve | applied | rejected → approved |
| 2026-10-07T10:00:00+00:00 | `gh:acme/web#2/event/207` | gh:zhou-pm:5003 (product_manager) | approve | applied | approved → approved |

## acme/web#3

```yaml
subject: "acme/web#3"
current_status: proposed
reconsideration_requested: false
pending_approval: []
dependencies: []
open_risks: []
last_authorized_decision: null
unauthorized_change_attempts: []
```

No authorized decision yet.

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T11:00:00+00:00 | `gh:acme/web#3/opened` | gh:mallory:6666 (no role) | request | unauthorized | proposed → proposed |
| 2026-10-06T11:00:00+00:00 | `gh:acme/web#3/comment/301` | gh:mallory:6666 (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-06T11:05:00+00:00 | `gh:acme/web#3/comment/302` | gh:mallory:6666 (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-06T11:10:00+00:00 | `gh:acme/web#3/comment/303` | gh:mallory:6666 (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-10T08:30:00+00:00 | `gh:acme/web#3/comment/304` | gh:mallory:6666 (no role) | comment | unauthorized | proposed → proposed |

## acme/web#4

```yaml
subject: "acme/web#4"
current_status: proposed
reconsideration_requested: false
pending_approval: [product_manager]
dependencies: []
open_risks: []
last_authorized_decision: null
unauthorized_change_attempts: []
```

No authorized decision yet.

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T12:00:00+00:00 | `gh:acme/web#4/opened` | gh:wu-sales:5004 (account_manager) | request | applied | proposed → proposed |
| 2026-10-06T12:00:00+00:00 | `gh:acme/web#4/event/401` | gh:zhou-pm:5003 (product_manager) | comment | applied | proposed → proposed |
| 2026-10-06T13:00:00+00:00 | `gh:acme/web#4/event/402` | gh:zhou-pm:5003 (product_manager) | request | applied | proposed → proposed |
| 2026-10-07T12:00:00+00:00 | `gh:acme/web#4/event/403` | gh:zhou-pm:5003 (product_manager) | comment | applied | proposed → proposed |
| 2026-10-07T13:00:00+00:00 | `gh:acme/web#4/event/404` | gh:wu-sales:5004 (account_manager) | request | applied | proposed → proposed |
| 2026-10-08T12:00:00+00:00 | `gh:acme/web#4/event/405` | gh:zhou-pm:5003 (product_manager) | comment | applied | proposed → proposed |

## acme/web#5

```yaml
subject: "acme/web#5"
current_status: proposed
reconsideration_requested: false
pending_approval: [product_manager]
dependencies: []
open_risks: []
last_authorized_decision: "gh:acme/web#5/event/502 (revoke by gh:zhou-pm:5003)"
unauthorized_change_attempts: []
```

**Why this is the current status**

- `gh:acme/web#5/event/502`: gh:zhou-pm:5003 (roles: product_manager) chose **revoke** at 2026-10-10T10:00:00+00:00
  - evidence `gh:acme/web#5/event/502/fact` · `sources/github/acme/web/5/event-502-907481344b21.txt:1` · https\://api.github.com/repos/acme/web/issues/events/502 · sha256 `907481344b21`
    > GitHub event 502 on acme/web#5 by gh:zhou-pm:5003 (User) at 2026-10-10T10:00:00Z: review_dismissed review 501 (was approved)
  - evidence `gh:acme/web#5/event/502/text` · `sources/github/acme/web/5/event-502-907481344b21.txt:4` · https\://api.github.com/repos/acme/web/issues/events/502 · sha256 `907481344b21`
    > Approved too early: the billing migration slipped to November.

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T13:00:00+00:00 | `gh:acme/web#5/opened` | gh:lin-eng:5002 (engineer) | request | applied | proposed → proposed |
| 2026-10-06T14:00:00+00:00 | `gh:acme/web#5/review/501` | gh:zhou-pm:5003 (product_manager) | approve | applied | proposed → approved |
| 2026-10-10T10:00:00+00:00 | `gh:acme/web#5/event/502` | gh:zhou-pm:5003 (product_manager) | revoke | applied | approved → proposed |

## acme/web#6

```yaml
subject: "acme/web#6"
current_status: rejected
reconsideration_requested: false
pending_approval: []
dependencies: []
open_risks: []
last_authorized_decision: "gh:acme/web#6/review/601 (reject by gh:zhou-pm:5003)"
unauthorized_change_attempts: ["gh:acme/web#6/event/602"]
```

**Why this is the current status**

- `gh:acme/web#6/review/601`: gh:zhou-pm:5003 (roles: product_manager) chose **reject** at 2026-10-06T10:00:00+00:00
  - evidence `gh:acme/web#6/review/601@a8490d7f6e90/fact` · `sources/github/acme/web/6/review-601-ad3111cafa43.txt:1` · https\://github.com/acme/web/pull/6#pullrequestreview-601 · sha256 `ad3111cafa43`
    > GitHub review 601 on acme/web#6 by gh:zhou-pm:5003 (User) at 2026-10-06T10:00:00Z: changes_requested on commit c0ffee00c0ff
  - evidence `gh:acme/web#6/review/601@a8490d7f6e90/text` · `sources/github/acme/web/6/review-601-ad3111cafa43.txt:4` · https\://github.com/acme/web/pull/6#pullrequestreview-601 · sha256 `ad3111cafa43`
    > Not until the billing migration lands.

**Ignored: actor lacked authority**

- `gh:acme/web#6/event/602`: gh:lin-eng:5002 tried to approve; actor roles ['engineer'] may not approve

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T14:00:00+00:00 | `gh:acme/web#6/opened` | gh:lin-eng:5002 (engineer) | request | applied | proposed → proposed |
| 2026-10-06T10:00:00+00:00 | `gh:acme/web#6/review/601` | gh:zhou-pm:5003 (product_manager) | reject | applied | proposed → rejected |
| 2026-10-07T11:00:00+00:00 | `gh:acme/web#6/event/602` | gh:lin-eng:5002 (engineer) | approve | unauthorized | rejected → rejected |

## acme/web#7

```yaml
subject: "acme/web#7"
current_status: approved
reconsideration_requested: false
pending_approval: []
dependencies: []
open_risks: []
last_authorized_decision: "gh:acme/web#7/review/701 (approve by gh:zhou-pm:5003)"
unauthorized_change_attempts: []
```

**Why this is the current status**

- `gh:acme/web#7/review/701`: gh:zhou-pm:5003 (roles: product_manager) chose **approve** at 2026-10-06T12:00:00+00:00
  - evidence `gh:acme/web#7/review/701@55f1d3e9d8ec/fact` · `sources/github/acme/web/7/review-701-2681090de1bd.txt:1` · https\://github.com/acme/web/pull/7#pullrequestreview-701 · sha256 `2681090de1bd`
    > GitHub review 701 on acme/web#7 by gh:zhou-pm:5003 (User) at 2026-10-06T12:00:00Z: dismissed on commit 7a7a7a7a7a7a
  - evidence `gh:acme/web#7/review/701@55f1d3e9d8ec/text` · `sources/github/acme/web/7/review-701-2681090de1bd.txt:4` · https\://github.com/acme/web/pull/7#pullrequestreview-701 · sha256 `2681090de1bd`
    > Looks good.
  - evidence `gh:acme/web#7/event/702/fact` · `sources/github/acme/web/7/event-702-500d1312635a.txt:1` · https\://api.github.com/repos/acme/web/issues/events/702 · sha256 `500d1312635a`
    > GitHub event 702 on acme/web#7 by gh:lin-eng:5002 (User) at 2026-10-07T08:00:00Z: review_dismissed review 701 (was approved, by commit 7b7b7b7b7b7b)

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T15:00:00+00:00 | `gh:acme/web#7/opened` | gh:lin-eng:5002 (engineer) | request | applied | proposed → proposed |
| 2026-10-06T12:00:00+00:00 | `gh:acme/web#7/review/701` | gh:zhou-pm:5003 (product_manager) | approve | applied | proposed → approved |
| 2026-10-07T08:00:00+00:00 | `gh:acme/web#7/event/702` | gh:lin-eng:5002 (engineer) | comment | applied | approved → approved |

## acme/web#8

```yaml
subject: "acme/web#8"
current_status: proposed
reconsideration_requested: false
pending_approval: [product_manager]
dependencies: []
open_risks: []
last_authorized_decision: null
unauthorized_change_attempts: []
```

No authorized decision yet.

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T16:00:00+00:00 | `gh:acme/web#8/opened` | gh:buyer-acme:5001 (customer) | request | applied | proposed → proposed |
| 2026-10-06T08:00:00+00:00 | `gh:acme/web#8/event/801` | gh:github-actions[bot]:41898282 (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-06T09:00:00+00:00 | `gh:acme/web#8/event/802` | gh:zhou-pm:5003 (product_manager) | comment | applied | proposed → proposed |
| 2026-10-06T10:00:00+00:00 | `gh:acme/web#8/event/803` | gh:ghost:10137 (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-06T11:00:00+00:00 | `gh:acme/web#8/event/804` | gh:zhou-pm:5003 (product_manager) | comment | applied | proposed → proposed |

## acme/web#9

```yaml
subject: "acme/web#9"
current_status: proposed
reconsideration_requested: false
pending_approval: [product_manager]
dependencies: []
open_risks: []
last_authorized_decision: null
unauthorized_change_attempts: []
```

No authorized decision yet.

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T17:00:00+00:00 | `gh:acme/web#9/opened` | gh:buyer-acme:5001 (customer) | request | applied | proposed → proposed |

## acme/web#10

```yaml
subject: "acme/web#10"
current_status: proposed
reconsideration_requested: false
pending_approval: [product_manager]
dependencies: []
open_risks: []
last_authorized_decision: null
unauthorized_change_attempts: []
```

No authorized decision yet.

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T18:00:00+00:00 | `gh:acme/web#10/opened` | gh:buyer-acme:5001 (customer) | request | applied | proposed → proposed |
| 2026-10-06T08:00:00+00:00 | `gh:acme/web#10/comment/1011` | gh:buyer-acme:5001 (customer) | comment | applied | proposed → proposed |
| 2026-10-06T08:10:00+00:00 | `gh:acme/web#10/comment/1012` | gh:lin-eng:5002 (engineer) | comment | applied | proposed → proposed |
| 2026-10-06T08:20:00+00:00 | `gh:acme/web#10/comment/1013` | gh:lin-eng:5002 (engineer) | comment | applied | proposed → proposed |
| 2026-10-06T08:30:00+00:00 | `gh:acme/web#10/comment/1014` | gh:lin-eng:5002 (engineer) | comment | applied | proposed → proposed |
| 2026-10-06T08:40:00+00:00 | `gh:acme/web#10/comment/1015` | gh:lin-eng:5002 (engineer) | comment | applied | proposed → proposed |
| 2026-10-06T08:50:00+00:00 | `gh:acme/web#10/comment/1016` | gh:lin-eng:5002 (engineer) | comment | applied | proposed → proposed |

## acme/web#11

```yaml
subject: "acme/web#11"
current_status: proposed
reconsideration_requested: false
pending_approval: [product_manager]
dependencies: []
open_risks: []
last_authorized_decision: null
unauthorized_change_attempts: ["gh:acme/web#11/review/1101"]
```

No authorized decision yet.

**Ignored: actor lacked authority**

- `gh:acme/web#11/review/1101`: gh:zhou:4242 tried to approve; actor roles none may not approve

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T19:00:00+00:00 | `gh:acme/web#11/opened` | gh:lin-eng:5002 (engineer) | request | applied | proposed → proposed |
| 2026-10-06T13:00:00+00:00 | `gh:acme/web#11/review/1101` | gh:zhou:4242 (no role) | approve | unauthorized | proposed → proposed |
| 2026-10-06T13:30:00+00:00 | `gh:acme/web#11/review/1102` | gh:wu-sales:5004 (account_manager) | comment | applied | proposed → proposed |
| 2026-10-07T10:00:00+00:00 | `gh:acme/web#11/review/1105` | gh:github-actions[bot]:41898282 (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-07T11:00:00+00:00 | `gh:acme/web#11/event/1106` | gh:zhou-pm:5003 (product_manager) | comment | applied | proposed → proposed |

## acme/web#12

```yaml
subject: "acme/web#12"
current_status: proposed
reconsideration_requested: false
pending_approval: []
dependencies: []
open_risks: []
last_authorized_decision: null
unauthorized_change_attempts: []
```

No authorized decision yet.

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T20:00:00+00:00 | `gh:acme/web#12/opened` | gh:jdoe_acme:77 (no role) | request | unauthorized | proposed → proposed |
| 2026-10-08T00:00:00+00:00 | `gh:acme/web#12/event/1201` | gh:github-actions[bot]:41898282 (no role) | comment | unauthorized | proposed → proposed |
| 2026-10-08T09:00:00+00:00 | `gh:acme/web#12/comment/1202` | gh:jdoe_acme:77 (no role) | comment | unauthorized | proposed → proposed |

## acme/web#13

```yaml
subject: "acme/web#13"
current_status: proposed
reconsideration_requested: false
pending_approval: [product_manager]
dependencies: []
open_risks: []
last_authorized_decision: "gh:acme/web#13/event/1302 (revoke by gh:zhou-pm:5003)"
unauthorized_change_attempts: []
```

**Why this is the current status**

- `gh:acme/web#13/event/1302`: gh:zhou-pm:5003 (roles: product_manager) chose **revoke** at 2026-10-10T11:00:00+00:00
  - evidence `gh:acme/web#13/event/1302/fact` · `sources/github/acme/web/13/event-1302-96770770ecf3.txt:1` · https\://api.github.com/repos/acme/web/issues/events/1302 · sha256 `96770770ecf3`
    > GitHub event 1302 on acme/web#13 by gh:zhou-pm:5003 (User) at 2026-10-10T11:00:00Z: reopened

**Event history**

| Time | Event | Actor | Action | Outcome | Status |
|---|---|---|---|---|---|
| 2026-10-05T21:00:00+00:00 | `gh:acme/web#13/opened` | gh:buyer-acme:5001 (customer) | request | applied | proposed → proposed |
| 2026-10-08T09:00:00+00:00 | `gh:acme/web#13/event/1301` | gh:zhou-pm:5003 (product_manager) | reject | applied | proposed → rejected |
| 2026-10-10T11:00:00+00:00 | `gh:acme/web#13/event/1302` | gh:zhou-pm:5003 (product_manager) | revoke | applied | rejected → proposed |
| 2026-10-10T11:00:00+00:00 | `gh:acme/web#13/event/1302/request` | gh:zhou-pm:5003 (product_manager) | request | applied | proposed → proposed |

