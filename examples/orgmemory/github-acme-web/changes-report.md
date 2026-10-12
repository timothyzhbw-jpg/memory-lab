# Organizational decision changes

| Subject | Status before | Status after | Changes |
|---|---|---|---|
| acme/web\#1 | proposed | rejected | `decision` ×1, `pending_approval_opened` ×1, `pending_approval_resolved` ×1, `reconsideration_opened` ×1, `dependency_added` ×1, `unauthorized_attempt` ×2 |
| acme/web\#11 | proposed | proposed | `unauthorized_attempt` ×1 |
| acme/web\#13 | proposed | proposed | `decision` ×2, `pending_approval_opened` ×1, `pending_approval_resolved` ×1 |
| acme/web\#2 | proposed | approved | `decision` ×3, `pending_approval_opened` ×1, `pending_approval_resolved` ×2, `reconsideration_opened` ×1, `reconsideration_resolved` ×1, `conflict` ×1, `unauthorized_attempt` ×1 |
| acme/web\#5 | proposed | proposed | `decision` ×2, `pending_approval_opened` ×1, `pending_approval_resolved` ×1 |
| acme/web\#6 | proposed | rejected | `decision` ×1, `pending_approval_resolved` ×1, `unauthorized_attempt` ×1 |
| acme/web\#7 | proposed | approved | `decision` ×1, `pending_approval_resolved` ×1 |
| **Total** | | | `decision` ×10, `pending_approval_opened` ×4, `pending_approval_resolved` ×7, `reconsideration_opened` ×2, `reconsideration_resolved` ×1, `dependency_added` ×1, `conflict` ×1, `unauthorized_attempt` ×5 |

- Period: after 2026-10-05T23:59:59+00:00, up to and including 2026-10-10T23:59:59+00:00
- 45 of 59 events fall in the period, on 12 subject(s); 7 subject(s) changed
- Mode: **read_only** (the event log was not modified)

## acme/web\#1

Status: proposed → rejected

### decision

proposed → rejected

- `gh:acme/web#1/event/105` at 2026-10-07T15:30:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · reject · applied
  - evidence `gh:acme/web#1/event/105/fact` · `sources/github/acme/web/1/event-105-c2a1c517e9cc.txt:1` · https\://api.github.com/repos/acme/web/issues/events/105 · sha256 `c2a1c517e9cc`
    > GitHub event 105 on acme/web\#1 by gh:zhou-pm:5003 (User) at 2026-10-07T15:30:00Z: closed (state\_reason not\_planned, commit none)

### pending_approval_opened

pending approval from product\_manager since 2026-10-10T09:00:00+00:00; still open at the end of the period

- `gh:acme/web#1/event/108/request` at 2026-10-10T09:00:00+00:00: gh:buyer-acme:5001 (roles: customer) · request · applied
  - evidence `gh:acme/web#1/event/108/fact` · `sources/github/acme/web/1/event-108-51c7ff41e966.txt:1` · https\://api.github.com/repos/acme/web/issues/events/108 · sha256 `51c7ff41e966`
    > GitHub event 108 on acme/web\#1 by gh:buyer-acme:5001 (User) at 2026-10-10T09:00:00Z: reopened

### pending_approval_resolved

pending approval from product\_manager since 2026-10-05T09:00:00+00:00; closed by reject by gh:zhou-pm:5003 at 2026-10-07T15:30:00+00:00

- `gh:acme/web#1/opened` at 2026-10-05T09:00:00+00:00: gh:buyer-acme:5001 (roles: customer) · request · applied
  - evidence `gh:acme/web#1/issue@2b1774b8fa89/fact` · `sources/github/acme/web/1/issue-1-130160cbdad9.txt:1` · https\://github.com/acme/web/issues/1 · sha256 `130160cbdad9`
    > GitHub issue acme/web\#1 opened by gh:buyer-acme:5001 (User) at 2026-10-05T09:00:00Z
  - evidence `gh:acme/web#1/issue@2b1774b8fa89/text` · `sources/github/acme/web/1/issue-1-130160cbdad9.txt:4` · https\://github.com/acme/web/issues/1 · sha256 `130160cbdad9`
    > Feature A: CSV export for the quarterly report
- `gh:acme/web#1/event/105` at 2026-10-07T15:30:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · reject · applied
  - evidence `gh:acme/web#1/event/105/fact` · `sources/github/acme/web/1/event-105-c2a1c517e9cc.txt:1` · https\://api.github.com/repos/acme/web/issues/events/105 · sha256 `c2a1c517e9cc`
    > GitHub event 105 on acme/web\#1 by gh:zhou-pm:5003 (User) at 2026-10-07T15:30:00Z: closed (state\_reason not\_planned, commit none)

### reconsideration_opened

reconsideration requested since 2026-10-10T09:00:00+00:00; still open at the end of the period

- `gh:acme/web#1/event/108/request` at 2026-10-10T09:00:00+00:00: gh:buyer-acme:5001 (roles: customer) · request · applied
  - evidence `gh:acme/web#1/event/108/fact` · `sources/github/acme/web/1/event-108-51c7ff41e966.txt:1` · https\://api.github.com/repos/acme/web/issues/events/108 · sha256 `51c7ff41e966`
    > GitHub event 108 on acme/web\#1 by gh:buyer-acme:5001 (User) at 2026-10-10T09:00:00Z: reopened

### dependency_added

now depends on acme/billing\#3, which has no events in this log up to the end of the period

- `gh:acme/web#1/event/103` at 2026-10-06T10:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · dependency · applied
  - evidence `gh:acme/web#1/event/103/fact` · `sources/github/acme/web/1/event-103-224d0933ae8a.txt:1` · https\://api.github.com/repos/acme/web/issues/events/103 · sha256 `224d0933ae8a`
    > GitHub event 103 on acme/web\#1 by gh:lin-eng:5002 (User) at 2026-10-06T10:00:00Z: blocked\_by\_added acme/billing\#3

### unauthorized_attempt

ignored: roles account\_manager may not approve

- `gh:acme/web#1/event/107` at 2026-10-08T16:00:00+00:00: gh:wu-sales:5004 (roles: account\_manager) · approve · unauthorized
  - evidence `gh:acme/web#1/event/107/fact` · `sources/github/acme/web/1/event-107-31aa8f7390a0.txt:1` · https\://api.github.com/repos/acme/web/issues/events/107 · sha256 `31aa8f7390a0`
    > GitHub event 107 on acme/web\#1 by gh:wu-sales:5004 (User) at 2026-10-08T16:00:00Z: labeled label "approved"

### unauthorized_attempt

ignored: roles customer may not revoke

- `gh:acme/web#1/event/108` at 2026-10-10T09:00:00+00:00: gh:buyer-acme:5001 (roles: customer) · revoke · unauthorized
  - evidence `gh:acme/web#1/event/108/fact` · `sources/github/acme/web/1/event-108-51c7ff41e966.txt:1` · https\://api.github.com/repos/acme/web/issues/events/108 · sha256 `51c7ff41e966`
    > GitHub event 108 on acme/web\#1 by gh:buyer-acme:5001 (User) at 2026-10-10T09:00:00Z: reopened

## acme/web\#11

Status: proposed → proposed

### unauthorized_attempt

ignored: an actor without a role may not approve

- `gh:acme/web#11/review/1101` at 2026-10-06T13:00:00+00:00: gh:zhou:4242 (roles: none) · approve · unauthorized
  - evidence `gh:acme/web#11/review/1101@1e6574a2f889/fact` · `sources/github/acme/web/11/review-1101-24b2753298b9.txt:1` · https\://github.com/acme/web/pull/11\#pullrequestreview-1101 · sha256 `24b2753298b9`
    > GitHub review 1101 on acme/web\#11 by gh:zhou:4242 (User) at 2026-10-06T13:00:00Z: approved on commit 11aa11aa11aa
  - evidence `gh:acme/web#11/review/1101@1e6574a2f889/text` · `sources/github/acme/web/11/review-1101-24b2753298b9.txt:4` · https\://github.com/acme/web/pull/11\#pullrequestreview-1101 · sha256 `24b2753298b9`
    > Approved, ship it.

## acme/web\#13

Status: proposed → proposed

### decision

proposed → rejected

- `gh:acme/web#13/event/1301` at 2026-10-08T09:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · reject · applied
  - evidence `gh:acme/web#13/event/1301/fact` · `sources/github/acme/web/13/event-1301-ec38c17d9b0a.txt:1` · https\://api.github.com/repos/acme/web/issues/events/1301 · sha256 `ec38c17d9b0a`
    > GitHub event 1301 on acme/web\#13 by gh:zhou-pm:5003 (User) at 2026-10-08T09:00:00Z: closed (state\_reason not\_planned, commit none)

### decision

rejected → proposed

- `gh:acme/web#13/event/1302` at 2026-10-10T11:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · revoke · applied
  - evidence `gh:acme/web#13/event/1302/fact` · `sources/github/acme/web/13/event-1302-96770770ecf3.txt:1` · https\://api.github.com/repos/acme/web/issues/events/1302 · sha256 `96770770ecf3`
    > GitHub event 1302 on acme/web\#13 by gh:zhou-pm:5003 (User) at 2026-10-10T11:00:00Z: reopened

### pending_approval_opened

pending approval from product\_manager since 2026-10-10T11:00:00+00:00; still open at the end of the period

- `gh:acme/web#13/event/1302` at 2026-10-10T11:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · revoke · applied
  - evidence `gh:acme/web#13/event/1302/fact` · `sources/github/acme/web/13/event-1302-96770770ecf3.txt:1` · https\://api.github.com/repos/acme/web/issues/events/1302 · sha256 `96770770ecf3`
    > GitHub event 1302 on acme/web\#13 by gh:zhou-pm:5003 (User) at 2026-10-10T11:00:00Z: reopened
- `gh:acme/web#13/event/1302/request` at 2026-10-10T11:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · request · applied
  - evidence `gh:acme/web#13/event/1302/fact` · `sources/github/acme/web/13/event-1302-96770770ecf3.txt:1` · https\://api.github.com/repos/acme/web/issues/events/1302 · sha256 `96770770ecf3`
    > GitHub event 1302 on acme/web\#13 by gh:zhou-pm:5003 (User) at 2026-10-10T11:00:00Z: reopened

### pending_approval_resolved

pending approval from product\_manager since 2026-10-05T21:00:00+00:00; closed by reject by gh:zhou-pm:5003 at 2026-10-08T09:00:00+00:00

- `gh:acme/web#13/opened` at 2026-10-05T21:00:00+00:00: gh:buyer-acme:5001 (roles: customer) · request · applied
  - evidence `gh:acme/web#13/issue@fba49b94c49d/fact` · `sources/github/acme/web/13/issue-13-7eff57ed1523.txt:1` · https\://github.com/acme/web/issues/13 · sha256 `7eff57ed1523`
    > GitHub issue acme/web\#13 opened by gh:buyer-acme:5001 (User) at 2026-10-05T21:00:00Z
  - evidence `gh:acme/web#13/issue@fba49b94c49d/text` · `sources/github/acme/web/13/issue-13-7eff57ed1523.txt:4` · https\://github.com/acme/web/issues/13 · sha256 `7eff57ed1523`
    > Feature F: scheduled exports
- `gh:acme/web#13/event/1301` at 2026-10-08T09:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · reject · applied
  - evidence `gh:acme/web#13/event/1301/fact` · `sources/github/acme/web/13/event-1301-ec38c17d9b0a.txt:1` · https\://api.github.com/repos/acme/web/issues/events/1301 · sha256 `ec38c17d9b0a`
    > GitHub event 1301 on acme/web\#13 by gh:zhou-pm:5003 (User) at 2026-10-08T09:00:00Z: closed (state\_reason not\_planned, commit none)

## acme/web\#2

Status: proposed → approved

### decision

proposed → rejected

- `gh:acme/web#2/review/203` at 2026-10-06T09:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · reject · applied
  - evidence `gh:acme/web#2/review/203@c9068cf0646a/fact` · `sources/github/acme/web/2/review-203-1b76d52c07b0.txt:1` · https\://github.com/acme/web/pull/2\#pullrequestreview-203 · sha256 `1b76d52c07b0`
    > GitHub review 203 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-06T09:00:00Z: changes\_requested on commit 0c5a1f3e9b7d
  - evidence `gh:acme/web#2/review/203@c9068cf0646a/text` · `sources/github/acme/web/2/review-203-1b76d52c07b0.txt:4` · https\://github.com/acme/web/pull/2\#pullrequestreview-203 · sha256 `1b76d52c07b0`
    > Hold this until the billing migration (acme/billing\#3) lands.

### decision

rejected → approved

- `gh:acme/web#2/review/206` at 2026-10-07T09:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · approve · applied
  - evidence `gh:acme/web#2/review/206@67c4aa9e1b3a/fact` · `sources/github/acme/web/2/review-206-b326a7cde4c0.txt:1` · https\://github.com/acme/web/pull/2\#pullrequestreview-206 · sha256 `b326a7cde4c0`
    > GitHub review 206 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-07T09:00:00Z: approved on commit 54bb654c9e60
  - evidence `gh:acme/web#2/review/206@67c4aa9e1b3a/text` · `sources/github/acme/web/2/review-206-b326a7cde4c0.txt:4` · https\://github.com/acme/web/pull/2\#pullrequestreview-206 · sha256 `b326a7cde4c0`
    > Approved now that export waits for the billing migration.

### decision

approved → approved; the status is unchanged, and this is now the authorizing decision

- `gh:acme/web#2/event/207` at 2026-10-07T10:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · approve · applied
  - evidence `gh:acme/web#2/event/207/fact` · `sources/github/acme/web/2/event-207-b9890b2ca38e.txt:1` · https\://api.github.com/repos/acme/web/issues/events/207 · sha256 `b9890b2ca38e`
    > GitHub event 207 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-07T10:00:00Z: merged (commit 54bb654c9e60)

### pending_approval_opened

pending approval from product\_manager since 2026-10-06T15:00:00+00:00; closed by approve by gh:zhou-pm:5003 at 2026-10-07T09:00:00+00:00

- `gh:acme/web#2/event/204` at 2026-10-06T15:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · request · applied
  - evidence `gh:acme/web#2/event/204/fact` · `sources/github/acme/web/2/event-204-b8feaa4b8834.txt:1` · https\://api.github.com/repos/acme/web/issues/events/204 · sha256 `b8feaa4b8834`
    > GitHub event 204 on acme/web\#2 by gh:lin-eng:5002 (User) at 2026-10-06T15:00:00Z: review\_requested reviewer gh:zhou-pm:5003

### pending_approval_resolved

pending approval from product\_manager since 2026-10-05T10:00:00+00:00; closed by reject by gh:zhou-pm:5003 at 2026-10-06T09:00:00+00:00

- `gh:acme/web#2/opened` at 2026-10-05T10:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · request · applied
  - evidence `gh:acme/web#2/issue@748f0ccd2264/fact` · `sources/github/acme/web/2/issue-2-61a12a9d218f.txt:1` · https\://github.com/acme/web/pull/2 · sha256 `61a12a9d218f`
    > GitHub pull request acme/web\#2 opened by gh:lin-eng:5002 (User) at 2026-10-05T10:00:00Z
  - evidence `gh:acme/web#2/issue@748f0ccd2264/text` · `sources/github/acme/web/2/issue-2-61a12a9d218f.txt:4` · https\://github.com/acme/web/pull/2 · sha256 `61a12a9d218f`
    > Add CSV export endpoint
- `gh:acme/web#2/review/203` at 2026-10-06T09:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · reject · applied
  - evidence `gh:acme/web#2/review/203@c9068cf0646a/fact` · `sources/github/acme/web/2/review-203-1b76d52c07b0.txt:1` · https\://github.com/acme/web/pull/2\#pullrequestreview-203 · sha256 `1b76d52c07b0`
    > GitHub review 203 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-06T09:00:00Z: changes\_requested on commit 0c5a1f3e9b7d
  - evidence `gh:acme/web#2/review/203@c9068cf0646a/text` · `sources/github/acme/web/2/review-203-1b76d52c07b0.txt:4` · https\://github.com/acme/web/pull/2\#pullrequestreview-203 · sha256 `1b76d52c07b0`
    > Hold this until the billing migration (acme/billing\#3) lands.

### pending_approval_resolved

pending approval from product\_manager since 2026-10-06T15:00:00+00:00; closed by approve by gh:zhou-pm:5003 at 2026-10-07T09:00:00+00:00

- `gh:acme/web#2/event/204` at 2026-10-06T15:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · request · applied
  - evidence `gh:acme/web#2/event/204/fact` · `sources/github/acme/web/2/event-204-b8feaa4b8834.txt:1` · https\://api.github.com/repos/acme/web/issues/events/204 · sha256 `b8feaa4b8834`
    > GitHub event 204 on acme/web\#2 by gh:lin-eng:5002 (User) at 2026-10-06T15:00:00Z: review\_requested reviewer gh:zhou-pm:5003
- `gh:acme/web#2/review/206` at 2026-10-07T09:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · approve · applied
  - evidence `gh:acme/web#2/review/206@67c4aa9e1b3a/fact` · `sources/github/acme/web/2/review-206-b326a7cde4c0.txt:1` · https\://github.com/acme/web/pull/2\#pullrequestreview-206 · sha256 `b326a7cde4c0`
    > GitHub review 206 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-07T09:00:00Z: approved on commit 54bb654c9e60
  - evidence `gh:acme/web#2/review/206@67c4aa9e1b3a/text` · `sources/github/acme/web/2/review-206-b326a7cde4c0.txt:4` · https\://github.com/acme/web/pull/2\#pullrequestreview-206 · sha256 `b326a7cde4c0`
    > Approved now that export waits for the billing migration.

### reconsideration_opened

reconsideration requested since 2026-10-06T15:00:00+00:00; closed by approve by gh:zhou-pm:5003 at 2026-10-07T09:00:00+00:00

- `gh:acme/web#2/event/204` at 2026-10-06T15:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · request · applied
  - evidence `gh:acme/web#2/event/204/fact` · `sources/github/acme/web/2/event-204-b8feaa4b8834.txt:1` · https\://api.github.com/repos/acme/web/issues/events/204 · sha256 `b8feaa4b8834`
    > GitHub event 204 on acme/web\#2 by gh:lin-eng:5002 (User) at 2026-10-06T15:00:00Z: review\_requested reviewer gh:zhou-pm:5003

### reconsideration_resolved

reconsideration requested since 2026-10-06T15:00:00+00:00; closed by approve by gh:zhou-pm:5003 at 2026-10-07T09:00:00+00:00

- `gh:acme/web#2/event/204` at 2026-10-06T15:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · request · applied
  - evidence `gh:acme/web#2/event/204/fact` · `sources/github/acme/web/2/event-204-b8feaa4b8834.txt:1` · https\://api.github.com/repos/acme/web/issues/events/204 · sha256 `b8feaa4b8834`
    > GitHub event 204 on acme/web\#2 by gh:lin-eng:5002 (User) at 2026-10-06T15:00:00Z: review\_requested reviewer gh:zhou-pm:5003
- `gh:acme/web#2/review/206` at 2026-10-07T09:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · approve · applied
  - evidence `gh:acme/web#2/review/206@67c4aa9e1b3a/fact` · `sources/github/acme/web/2/review-206-b326a7cde4c0.txt:1` · https\://github.com/acme/web/pull/2\#pullrequestreview-206 · sha256 `b326a7cde4c0`
    > GitHub review 206 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-07T09:00:00Z: approved on commit 54bb654c9e60
  - evidence `gh:acme/web#2/review/206@67c4aa9e1b3a/text` · `sources/github/acme/web/2/review-206-b326a7cde4c0.txt:4` · https\://github.com/acme/web/pull/2\#pullrequestreview-206 · sha256 `b326a7cde4c0`
    > Approved now that export waits for the billing migration.

### conflict

decision\_reversed: reject by gh:zhou-pm:5003 then approve by gh:zhou-pm:5003

- `gh:acme/web#2/review/203` at 2026-10-06T09:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · reject · applied
  - evidence `gh:acme/web#2/review/203@c9068cf0646a/fact` · `sources/github/acme/web/2/review-203-1b76d52c07b0.txt:1` · https\://github.com/acme/web/pull/2\#pullrequestreview-203 · sha256 `1b76d52c07b0`
    > GitHub review 203 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-06T09:00:00Z: changes\_requested on commit 0c5a1f3e9b7d
  - evidence `gh:acme/web#2/review/203@c9068cf0646a/text` · `sources/github/acme/web/2/review-203-1b76d52c07b0.txt:4` · https\://github.com/acme/web/pull/2\#pullrequestreview-203 · sha256 `1b76d52c07b0`
    > Hold this until the billing migration (acme/billing\#3) lands.
- `gh:acme/web#2/review/206` at 2026-10-07T09:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · approve · applied
  - evidence `gh:acme/web#2/review/206@67c4aa9e1b3a/fact` · `sources/github/acme/web/2/review-206-b326a7cde4c0.txt:1` · https\://github.com/acme/web/pull/2\#pullrequestreview-206 · sha256 `b326a7cde4c0`
    > GitHub review 206 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-07T09:00:00Z: approved on commit 54bb654c9e60
  - evidence `gh:acme/web#2/review/206@67c4aa9e1b3a/text` · `sources/github/acme/web/2/review-206-b326a7cde4c0.txt:4` · https\://github.com/acme/web/pull/2\#pullrequestreview-206 · sha256 `b326a7cde4c0`
    > Approved now that export waits for the billing migration.

### unauthorized_attempt

ignored: an actor without a role may not approve

- `gh:acme/web#2/review/205` at 2026-10-06T16:00:00+00:00: gh:mallory:6666 (roles: none) · approve · unauthorized
  - evidence `gh:acme/web#2/review/205@be4b3d91e203/fact` · `sources/github/acme/web/2/review-205-6eb003206a71.txt:1` · https\://github.com/acme/web/pull/2\#pullrequestreview-205 · sha256 `6eb003206a71`
    > GitHub review 205 on acme/web\#2 by gh:mallory:6666 (User) at 2026-10-06T16:00:00Z: approved on commit 54bb654c9e60
  - evidence `gh:acme/web#2/review/205@be4b3d91e203/text` · `sources/github/acme/web/2/review-205-6eb003206a71.txt:4` · https\://github.com/acme/web/pull/2\#pullrequestreview-205 · sha256 `6eb003206a71`
    > LGTM

## acme/web\#5

Status: proposed → proposed

### decision

proposed → approved

- `gh:acme/web#5/review/501` at 2026-10-06T14:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · approve · applied
  - evidence `gh:acme/web#5/review/501@7e0019a3769b/fact` · `sources/github/acme/web/5/review-501-ada739a0b43f.txt:1` · https\://github.com/acme/web/pull/5\#pullrequestreview-501 · sha256 `ada739a0b43f`
    > GitHub review 501 on acme/web\#5 by gh:zhou-pm:5003 (User) at 2026-10-06T14:00:00Z: approved on commit a1b2c3d4e5f6
  - evidence `gh:acme/web#5/review/501@7e0019a3769b/text` · `sources/github/acme/web/5/review-501-ada739a0b43f.txt:4` · https\://github.com/acme/web/pull/5\#pullrequestreview-501 · sha256 `ada739a0b43f`
    > Approved.

### decision

approved → proposed

- `gh:acme/web#5/event/502` at 2026-10-10T10:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · revoke · applied
  - evidence `gh:acme/web#5/event/502/fact` · `sources/github/acme/web/5/event-502-907481344b21.txt:1` · https\://api.github.com/repos/acme/web/issues/events/502 · sha256 `907481344b21`
    > GitHub event 502 on acme/web\#5 by gh:zhou-pm:5003 (User) at 2026-10-10T10:00:00Z: review\_dismissed review 501 (was approved)
  - evidence `gh:acme/web#5/event/502/text` · `sources/github/acme/web/5/event-502-907481344b21.txt:4` · https\://api.github.com/repos/acme/web/issues/events/502 · sha256 `907481344b21`
    > Approved too early: the billing migration slipped to November.

### pending_approval_opened

pending approval from product\_manager since 2026-10-10T10:00:00+00:00; still open at the end of the period

- `gh:acme/web#5/event/502` at 2026-10-10T10:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · revoke · applied
  - evidence `gh:acme/web#5/event/502/fact` · `sources/github/acme/web/5/event-502-907481344b21.txt:1` · https\://api.github.com/repos/acme/web/issues/events/502 · sha256 `907481344b21`
    > GitHub event 502 on acme/web\#5 by gh:zhou-pm:5003 (User) at 2026-10-10T10:00:00Z: review\_dismissed review 501 (was approved)
  - evidence `gh:acme/web#5/event/502/text` · `sources/github/acme/web/5/event-502-907481344b21.txt:4` · https\://api.github.com/repos/acme/web/issues/events/502 · sha256 `907481344b21`
    > Approved too early: the billing migration slipped to November.

### pending_approval_resolved

pending approval from product\_manager since 2026-10-05T13:00:00+00:00; closed by approve by gh:zhou-pm:5003 at 2026-10-06T14:00:00+00:00

- `gh:acme/web#5/opened` at 2026-10-05T13:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · request · applied
  - evidence `gh:acme/web#5/issue@ffc5b70275bc/fact` · `sources/github/acme/web/5/issue-5-26f3db23fe05.txt:1` · https\://github.com/acme/web/pull/5 · sha256 `26f3db23fe05`
    > GitHub pull request acme/web\#5 opened by gh:lin-eng:5002 (User) at 2026-10-05T13:00:00Z
  - evidence `gh:acme/web#5/issue@ffc5b70275bc/text` · `sources/github/acme/web/5/issue-5-26f3db23fe05.txt:4` · https\://github.com/acme/web/pull/5 · sha256 `26f3db23fe05`
    > Rate-limit the export endpoint
- `gh:acme/web#5/review/501` at 2026-10-06T14:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · approve · applied
  - evidence `gh:acme/web#5/review/501@7e0019a3769b/fact` · `sources/github/acme/web/5/review-501-ada739a0b43f.txt:1` · https\://github.com/acme/web/pull/5\#pullrequestreview-501 · sha256 `ada739a0b43f`
    > GitHub review 501 on acme/web\#5 by gh:zhou-pm:5003 (User) at 2026-10-06T14:00:00Z: approved on commit a1b2c3d4e5f6
  - evidence `gh:acme/web#5/review/501@7e0019a3769b/text` · `sources/github/acme/web/5/review-501-ada739a0b43f.txt:4` · https\://github.com/acme/web/pull/5\#pullrequestreview-501 · sha256 `ada739a0b43f`
    > Approved.

## acme/web\#6

Status: proposed → rejected

### decision

proposed → rejected

- `gh:acme/web#6/review/601` at 2026-10-06T10:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · reject · applied
  - evidence `gh:acme/web#6/review/601@a8490d7f6e90/fact` · `sources/github/acme/web/6/review-601-ad3111cafa43.txt:1` · https\://github.com/acme/web/pull/6\#pullrequestreview-601 · sha256 `ad3111cafa43`
    > GitHub review 601 on acme/web\#6 by gh:zhou-pm:5003 (User) at 2026-10-06T10:00:00Z: changes\_requested on commit c0ffee00c0ff
  - evidence `gh:acme/web#6/review/601@a8490d7f6e90/text` · `sources/github/acme/web/6/review-601-ad3111cafa43.txt:4` · https\://github.com/acme/web/pull/6\#pullrequestreview-601 · sha256 `ad3111cafa43`
    > Not until the billing migration lands.

### pending_approval_resolved

pending approval from product\_manager since 2026-10-05T14:00:00+00:00; closed by reject by gh:zhou-pm:5003 at 2026-10-06T10:00:00+00:00

- `gh:acme/web#6/opened` at 2026-10-05T14:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · request · applied
  - evidence `gh:acme/web#6/issue@56b4b96ede59/fact` · `sources/github/acme/web/6/issue-6-704720cc2e74.txt:1` · https\://github.com/acme/web/pull/6 · sha256 `704720cc2e74`
    > GitHub pull request acme/web\#6 opened by gh:lin-eng:5002 (User) at 2026-10-05T14:00:00Z
  - evidence `gh:acme/web#6/issue@56b4b96ede59/text` · `sources/github/acme/web/6/issue-6-704720cc2e74.txt:4` · https\://github.com/acme/web/pull/6 · sha256 `704720cc2e74`
    > Ship CSV export behind a flag
- `gh:acme/web#6/review/601` at 2026-10-06T10:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · reject · applied
  - evidence `gh:acme/web#6/review/601@a8490d7f6e90/fact` · `sources/github/acme/web/6/review-601-ad3111cafa43.txt:1` · https\://github.com/acme/web/pull/6\#pullrequestreview-601 · sha256 `ad3111cafa43`
    > GitHub review 601 on acme/web\#6 by gh:zhou-pm:5003 (User) at 2026-10-06T10:00:00Z: changes\_requested on commit c0ffee00c0ff
  - evidence `gh:acme/web#6/review/601@a8490d7f6e90/text` · `sources/github/acme/web/6/review-601-ad3111cafa43.txt:4` · https\://github.com/acme/web/pull/6\#pullrequestreview-601 · sha256 `ad3111cafa43`
    > Not until the billing migration lands.

### unauthorized_attempt

ignored: roles engineer may not approve

- `gh:acme/web#6/event/602` at 2026-10-07T11:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · approve · unauthorized
  - evidence `gh:acme/web#6/event/602/fact` · `sources/github/acme/web/6/event-602-42ba9130ccc6.txt:1` · https\://api.github.com/repos/acme/web/issues/events/602 · sha256 `42ba9130ccc6`
    > GitHub event 602 on acme/web\#6 by gh:lin-eng:5002 (User) at 2026-10-07T11:00:00Z: merged (commit c0ffee00c0ff)

## acme/web\#7

Status: proposed → approved

### decision

proposed → approved

- `gh:acme/web#7/review/701` at 2026-10-06T12:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · approve · applied
  - evidence `gh:acme/web#7/review/701@55f1d3e9d8ec/fact` · `sources/github/acme/web/7/review-701-2681090de1bd.txt:1` · https\://github.com/acme/web/pull/7\#pullrequestreview-701 · sha256 `2681090de1bd`
    > GitHub review 701 on acme/web\#7 by gh:zhou-pm:5003 (User) at 2026-10-06T12:00:00Z: dismissed on commit 7a7a7a7a7a7a
  - evidence `gh:acme/web#7/review/701@55f1d3e9d8ec/text` · `sources/github/acme/web/7/review-701-2681090de1bd.txt:4` · https\://github.com/acme/web/pull/7\#pullrequestreview-701 · sha256 `2681090de1bd`
    > Looks good.
  - evidence `gh:acme/web#7/event/702/fact` · `sources/github/acme/web/7/event-702-500d1312635a.txt:1` · https\://api.github.com/repos/acme/web/issues/events/702 · sha256 `500d1312635a`
    > GitHub event 702 on acme/web\#7 by gh:lin-eng:5002 (User) at 2026-10-07T08:00:00Z: review\_dismissed review 701 (was approved, by commit 7b7b7b7b7b7b)

### pending_approval_resolved

pending approval from product\_manager since 2026-10-05T15:00:00+00:00; closed by approve by gh:zhou-pm:5003 at 2026-10-06T12:00:00+00:00

- `gh:acme/web#7/opened` at 2026-10-05T15:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · request · applied
  - evidence `gh:acme/web#7/issue@aaed60457ea2/fact` · `sources/github/acme/web/7/issue-7-1c47c47a97b6.txt:1` · https\://github.com/acme/web/pull/7 · sha256 `1c47c47a97b6`
    > GitHub pull request acme/web\#7 opened by gh:lin-eng:5002 (User) at 2026-10-05T15:00:00Z
  - evidence `gh:acme/web#7/issue@aaed60457ea2/text` · `sources/github/acme/web/7/issue-7-1c47c47a97b6.txt:4` · https\://github.com/acme/web/pull/7 · sha256 `1c47c47a97b6`
    > Export: add column headers
- `gh:acme/web#7/review/701` at 2026-10-06T12:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · approve · applied
  - evidence `gh:acme/web#7/review/701@55f1d3e9d8ec/fact` · `sources/github/acme/web/7/review-701-2681090de1bd.txt:1` · https\://github.com/acme/web/pull/7\#pullrequestreview-701 · sha256 `2681090de1bd`
    > GitHub review 701 on acme/web\#7 by gh:zhou-pm:5003 (User) at 2026-10-06T12:00:00Z: dismissed on commit 7a7a7a7a7a7a
  - evidence `gh:acme/web#7/review/701@55f1d3e9d8ec/text` · `sources/github/acme/web/7/review-701-2681090de1bd.txt:4` · https\://github.com/acme/web/pull/7\#pullrequestreview-701 · sha256 `2681090de1bd`
    > Looks good.
  - evidence `gh:acme/web#7/event/702/fact` · `sources/github/acme/web/7/event-702-500d1312635a.txt:1` · https\://api.github.com/repos/acme/web/issues/events/702 · sha256 `500d1312635a`
    > GitHub event 702 on acme/web\#7 by gh:lin-eng:5002 (User) at 2026-10-07T08:00:00Z: review\_dismissed review 701 (was approved, by commit 7b7b7b7b7b7b)

## Notes

- Changes restate two replays of the log, at the start and at the end of the period, and the audit trail between them. An event at exactly the start time belongs to the earlier replay.
- Only what replay applied changes state: an event the policy ignored is listed as an `unauthorized_attempt`, and a `comment` is not listed.
