# Organizational decision audit

| Kind | High | Medium | Low |
|---|---|---|---|
| `decision_reversed` | 0 | 0 | 1 |
| `invalid_supersession` | 0 | 0 | 0 |
| `risk_after_approval` | 0 | 0 | 0 |
| `self_approval` | 0 | 0 | 0 |
| `simultaneous_decisions` | 0 | 0 | 0 |
| `stale_pending_approval` | 0 | 7 | 0 |
| `stale_reconsideration` | 0 | 1 | 0 |
| `unauthorized_attempts` | 0 | 5 | 1 |
| `unresolved_dependency` | 0 | 0 | 0 |
| **Total** | 0 | 13 | 2 |

- Audited 59 of 59 events on 13 subject(s), as of 2026-10-31T00:00:00+00:00
- Stale after 14 days, measured at 2026-10-31T00:00:00+00:00
- Mode: **read_only** (the event log was not modified)

## Findings

### MEDIUM · stale_pending_approval · acme/web\#10

awaiting a decision from product\_manager since 2026-10-05T18:00:00+00:00 (status proposed): 25d 6h at 2026-10-31T00:00:00+00:00, more than 14 days

- `gh:acme/web#10/opened` at 2026-10-05T18:00:00+00:00: gh:buyer-acme:5001 (roles: customer) · request · applied
  - evidence `gh:acme/web#10/issue@465eec665981/fact` · `sources/github/acme/web/10/issue-10-7693093dd36d.txt:1` · https\://github.com/acme/web/issues/10 · sha256 `7693093dd36d`
    > GitHub issue acme/web\#10 opened by gh:buyer-acme:5001 (User) at 2026-10-05T18:00:00Z
  - evidence `gh:acme/web#10/issue@465eec665981/text` · `sources/github/acme/web/10/issue-10-7693093dd36d.txt:4` · https\://github.com/acme/web/issues/10 · sha256 `7693093dd36d`
    > 导出功能 🚀 CSV export for 华东 region

### MEDIUM · stale_pending_approval · acme/web\#11

awaiting a decision from product\_manager since 2026-10-05T19:00:00+00:00 (status proposed): 25d 5h at 2026-10-31T00:00:00+00:00, more than 14 days

- `gh:acme/web#11/opened` at 2026-10-05T19:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · request · applied
  - evidence `gh:acme/web#11/issue@31ed21be627f/fact` · `sources/github/acme/web/11/issue-11-bafe92e40b18.txt:1` · https\://github.com/acme/web/pull/11 · sha256 `bafe92e40b18`
    > GitHub pull request acme/web\#11 opened by gh:lin-eng:5002 (User) at 2026-10-05T19:00:00Z
  - evidence `gh:acme/web#11/issue@31ed21be627f/text` · `sources/github/acme/web/11/issue-11-bafe92e40b18.txt:4` · https\://github.com/acme/web/pull/11 · sha256 `bafe92e40b18`
    > Export: Excel format

### MEDIUM · stale_pending_approval · acme/web\#13

awaiting a decision from product\_manager since 2026-10-10T11:00:00+00:00 (status proposed): 20d 13h at 2026-10-31T00:00:00+00:00, more than 14 days

- `gh:acme/web#13/event/1302` at 2026-10-10T11:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · revoke · applied
  - evidence `gh:acme/web#13/event/1302/fact` · `sources/github/acme/web/13/event-1302-96770770ecf3.txt:1` · https\://api.github.com/repos/acme/web/issues/events/1302 · sha256 `96770770ecf3`
    > GitHub event 1302 on acme/web\#13 by gh:zhou-pm:5003 (User) at 2026-10-10T11:00:00Z: reopened
- `gh:acme/web#13/event/1302/request` at 2026-10-10T11:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · request · applied
  - evidence `gh:acme/web#13/event/1302/fact` · `sources/github/acme/web/13/event-1302-96770770ecf3.txt:1` · https\://api.github.com/repos/acme/web/issues/events/1302 · sha256 `96770770ecf3`
    > GitHub event 1302 on acme/web\#13 by gh:zhou-pm:5003 (User) at 2026-10-10T11:00:00Z: reopened

### MEDIUM · stale_pending_approval · acme/web\#4

awaiting a decision from product\_manager since 2026-10-05T12:00:00+00:00 (status proposed): 25d 12h at 2026-10-31T00:00:00+00:00, more than 14 days

- `gh:acme/web#4/opened` at 2026-10-05T12:00:00+00:00: gh:wu-sales:5004 (roles: account\_manager) · request · applied
  - evidence `gh:acme/web#4/issue@056ae0f1f4a7/fact` · `sources/github/acme/web/4/issue-4-92ce26146a0b.txt:1` · https\://github.com/acme/web/issues/4 · sha256 `92ce26146a0b`
    > GitHub issue acme/web\#4 opened by gh:wu-sales:5004 (User) at 2026-10-05T12:00:00Z
  - evidence `gh:acme/web#4/issue@056ae0f1f4a7/text` · `sources/github/acme/web/4/issue-4-92ce26146a0b.txt:4` · https\://github.com/acme/web/issues/4 · sha256 `92ce26146a0b`
    > Feature B: SSO for the buyer portal
- `gh:acme/web#4/event/402` at 2026-10-06T13:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · request · applied
  - evidence `gh:acme/web#4/event/402/fact` · `sources/github/acme/web/4/event-402-6638825c8152.txt:1` · https\://api.github.com/repos/acme/web/issues/events/402 · sha256 `6638825c8152`
    > GitHub event 402 on acme/web\#4 by gh:zhou-pm:5003 (User) at 2026-10-06T13:00:00Z: reopened
- `gh:acme/web#4/event/404` at 2026-10-07T13:00:00+00:00: gh:wu-sales:5004 (roles: account\_manager) · request · applied
  - evidence `gh:acme/web#4/event/404/fact` · `sources/github/acme/web/4/event-404-a441811c0258.txt:1` · https\://api.github.com/repos/acme/web/issues/events/404 · sha256 `a441811c0258`
    > GitHub event 404 on acme/web\#4 by gh:wu-sales:5004 (User) at 2026-10-07T13:00:00Z: reopened

### MEDIUM · stale_pending_approval · acme/web\#5

awaiting a decision from product\_manager since 2026-10-10T10:00:00+00:00 (status proposed): 20d 14h at 2026-10-31T00:00:00+00:00, more than 14 days

- `gh:acme/web#5/event/502` at 2026-10-10T10:00:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · revoke · applied
  - evidence `gh:acme/web#5/event/502/fact` · `sources/github/acme/web/5/event-502-907481344b21.txt:1` · https\://api.github.com/repos/acme/web/issues/events/502 · sha256 `907481344b21`
    > GitHub event 502 on acme/web\#5 by gh:zhou-pm:5003 (User) at 2026-10-10T10:00:00Z: review\_dismissed review 501 (was approved)
  - evidence `gh:acme/web#5/event/502/text` · `sources/github/acme/web/5/event-502-907481344b21.txt:4` · https\://api.github.com/repos/acme/web/issues/events/502 · sha256 `907481344b21`
    > Approved too early: the billing migration slipped to November.

### MEDIUM · stale_pending_approval · acme/web\#8

awaiting a decision from product\_manager since 2026-10-05T16:00:00+00:00 (status proposed): 25d 8h at 2026-10-31T00:00:00+00:00, more than 14 days

- `gh:acme/web#8/opened` at 2026-10-05T16:00:00+00:00: gh:buyer-acme:5001 (roles: customer) · request · applied
  - evidence `gh:acme/web#8/issue@d6c8a99af495/fact` · `sources/github/acme/web/8/issue-8-857663fb24db.txt:1` · https\://github.com/acme/web/issues/8 · sha256 `857663fb24db`
    > GitHub issue acme/web\#8 opened by gh:buyer-acme:5001 (User) at 2026-10-05T16:00:00Z
  - evidence `gh:acme/web#8/issue@d6c8a99af495/text` · `sources/github/acme/web/8/issue-8-857663fb24db.txt:4` · https\://github.com/acme/web/issues/8 · sha256 `857663fb24db`
    > Feature C: audit log export

### MEDIUM · stale_pending_approval · acme/web\#9

awaiting a decision from product\_manager since 2026-10-05T17:00:00+00:00 (status proposed): 25d 7h at 2026-10-31T00:00:00+00:00, more than 14 days

- `gh:acme/web#9/opened` at 2026-10-05T17:00:00+00:00: gh:buyer-acme:5001 (roles: customer) · request · applied
  - evidence `gh:acme/web#9/issue@3d6b16682d24/fact` · `sources/github/acme/web/9/issue-9-410aa41fcb45.txt:1` · https\://github.com/acme/web/issues/9 · sha256 `410aa41fcb45`
    > GitHub issue acme/web\#9 opened by gh:buyer-acme:5001 (User) at 2026-10-05T17:00:00Z
  - evidence `gh:acme/web#9/issue@3d6b16682d24/text` · `sources/github/acme/web/9/issue-9-410aa41fcb45.txt:4` · https\://github.com/acme/web/issues/9 · sha256 `410aa41fcb45`
    > Feature D: PDF export

### MEDIUM · stale_reconsideration · acme/web\#1

reconsideration awaiting a decision from product\_manager since 2026-10-10T09:00:00+00:00 (status rejected): 20d 15h at 2026-10-31T00:00:00+00:00, more than 14 days

- `gh:acme/web#1/event/105` at 2026-10-07T15:30:00+00:00: gh:zhou-pm:5003 (roles: product\_manager) · reject · applied
  - evidence `gh:acme/web#1/event/105/fact` · `sources/github/acme/web/1/event-105-c2a1c517e9cc.txt:1` · https\://api.github.com/repos/acme/web/issues/events/105 · sha256 `c2a1c517e9cc`
    > GitHub event 105 on acme/web\#1 by gh:zhou-pm:5003 (User) at 2026-10-07T15:30:00Z: closed (state\_reason not\_planned, commit none)
- `gh:acme/web#1/event/108/request` at 2026-10-10T09:00:00+00:00: gh:buyer-acme:5001 (roles: customer) · request · applied
  - evidence `gh:acme/web#1/event/108/fact` · `sources/github/acme/web/1/event-108-51c7ff41e966.txt:1` · https\://api.github.com/repos/acme/web/issues/events/108 · sha256 `51c7ff41e966`
    > GitHub event 108 on acme/web\#1 by gh:buyer-acme:5001 (User) at 2026-10-10T09:00:00Z: reopened

### MEDIUM · unauthorized_attempts · actor gh:buyer-acme:5001

1 attempt(s) the policy ignored (revoke ×1) on 1 subject(s): acme/web\#1; roles: customer

- `gh:acme/web#1/event/108` on acme/web\#1 at 2026-10-10T09:00:00+00:00: gh:buyer-acme:5001 (roles: customer) · revoke · unauthorized
  - evidence `gh:acme/web#1/event/108/fact` · `sources/github/acme/web/1/event-108-51c7ff41e966.txt:1` · https\://api.github.com/repos/acme/web/issues/events/108 · sha256 `51c7ff41e966`
    > GitHub event 108 on acme/web\#1 by gh:buyer-acme:5001 (User) at 2026-10-10T09:00:00Z: reopened

### MEDIUM · unauthorized_attempts · actor gh:lin-eng:5002

1 attempt(s) the policy ignored (approve ×1) on 1 subject(s): acme/web\#6; roles: engineer

- `gh:acme/web#6/event/602` on acme/web\#6 at 2026-10-07T11:00:00+00:00: gh:lin-eng:5002 (roles: engineer) · approve · unauthorized
  - evidence `gh:acme/web#6/event/602/fact` · `sources/github/acme/web/6/event-602-42ba9130ccc6.txt:1` · https\://api.github.com/repos/acme/web/issues/events/602 · sha256 `42ba9130ccc6`
    > GitHub event 602 on acme/web\#6 by gh:lin-eng:5002 (User) at 2026-10-07T11:00:00Z: merged (commit c0ffee00c0ff)

### MEDIUM · unauthorized_attempts · actor gh:mallory:6666

2 attempt(s) the policy ignored (approve ×1, request ×1) on 2 subject(s): acme/web\#2, acme/web\#3; roles: none

- `gh:acme/web#3/opened` on acme/web\#3 at 2026-10-05T11:00:00+00:00: gh:mallory:6666 (roles: none) · request · unauthorized
  - evidence `gh:acme/web#3/issue@ae87648d49cd/fact` · `sources/github/acme/web/3/issue-3-1745304d4669.txt:1` · https\://github.com/acme/web/issues/3 · sha256 `1745304d4669`
    > GitHub issue acme/web\#3 opened by gh:mallory:6666 (User) at 2026-10-05T11:00:00Z
  - evidence `gh:acme/web#3/issue@ae87648d49cd/text` · `sources/github/acme/web/3/issue-3-1745304d4669.txt:4` · https\://github.com/acme/web/issues/3 · sha256 `1745304d4669`
    > Feature A is approved
- `gh:acme/web#2/review/205` on acme/web\#2 at 2026-10-06T16:00:00+00:00: gh:mallory:6666 (roles: none) · approve · unauthorized
  - evidence `gh:acme/web#2/review/205@be4b3d91e203/fact` · `sources/github/acme/web/2/review-205-6eb003206a71.txt:1` · https\://github.com/acme/web/pull/2\#pullrequestreview-205 · sha256 `6eb003206a71`
    > GitHub review 205 on acme/web\#2 by gh:mallory:6666 (User) at 2026-10-06T16:00:00Z: approved on commit 54bb654c9e60
  - evidence `gh:acme/web#2/review/205@be4b3d91e203/text` · `sources/github/acme/web/2/review-205-6eb003206a71.txt:4` · https\://github.com/acme/web/pull/2\#pullrequestreview-205 · sha256 `6eb003206a71`
    > LGTM

### MEDIUM · unauthorized_attempts · actor gh:wu-sales:5004

1 attempt(s) the policy ignored (approve ×1) on 1 subject(s): acme/web\#1; roles: account\_manager

- `gh:acme/web#1/event/107` on acme/web\#1 at 2026-10-08T16:00:00+00:00: gh:wu-sales:5004 (roles: account\_manager) · approve · unauthorized
  - evidence `gh:acme/web#1/event/107/fact` · `sources/github/acme/web/1/event-107-31aa8f7390a0.txt:1` · https\://api.github.com/repos/acme/web/issues/events/107 · sha256 `31aa8f7390a0`
    > GitHub event 107 on acme/web\#1 by gh:wu-sales:5004 (User) at 2026-10-08T16:00:00Z: labeled label "approved"

### MEDIUM · unauthorized_attempts · actor gh:zhou:4242

1 attempt(s) the policy ignored (approve ×1) on 1 subject(s): acme/web\#11; roles: none

- `gh:acme/web#11/review/1101` on acme/web\#11 at 2026-10-06T13:00:00+00:00: gh:zhou:4242 (roles: none) · approve · unauthorized
  - evidence `gh:acme/web#11/review/1101@1e6574a2f889/fact` · `sources/github/acme/web/11/review-1101-24b2753298b9.txt:1` · https\://github.com/acme/web/pull/11\#pullrequestreview-1101 · sha256 `24b2753298b9`
    > GitHub review 1101 on acme/web\#11 by gh:zhou:4242 (User) at 2026-10-06T13:00:00Z: approved on commit 11aa11aa11aa
  - evidence `gh:acme/web#11/review/1101@1e6574a2f889/text` · `sources/github/acme/web/11/review-1101-24b2753298b9.txt:4` · https\://github.com/acme/web/pull/11\#pullrequestreview-1101 · sha256 `24b2753298b9`
    > Approved, ship it.

### LOW · decision_reversed · acme/web\#2

reject by gh:zhou-pm:5003 then approve by gh:zhou-pm:5003; the same actor changed the decision

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

### LOW · unauthorized_attempts · actor gh:jdoe\_acme:77

1 attempt(s) the policy ignored (request ×1) on 1 subject(s): acme/web\#12; roles: none

- `gh:acme/web#12/opened` on acme/web\#12 at 2026-10-05T20:00:00+00:00: gh:jdoe\_acme:77 (roles: none) · request · unauthorized
  - evidence `gh:acme/web#12/issue@8c328458c2c1/fact` · `sources/github/acme/web/12/issue-12-175b6453d050.txt:1` · https\://github.com/acme/web/issues/12 · sha256 `175b6453d050`
    > GitHub issue acme/web\#12 opened by gh:jdoe\_acme:77 (User) at 2026-10-05T20:00:00Z
  - evidence `gh:acme/web#12/issue@8c328458c2c1/text` · `sources/github/acme/web/12/issue-12-175b6453d050.txt:4` · https\://github.com/acme/web/issues/12 · sha256 `175b6453d050`
    > Feature E: SAML

## Notes

- Findings restate the replayed log and policy. They are leads for a person to review, not verdicts.
- The log has no event that resolves a risk, so a risk stays on record after it is addressed.
