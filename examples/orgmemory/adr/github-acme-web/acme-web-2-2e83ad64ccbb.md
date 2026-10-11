# acme/web\#2

Date: 2026-10-07

## Status

Accepted

`gh:acme/web#2/event/207` is the current authorized decision when the event log is replayed as of the end of the log (59 of 59 events). Replayed status: `approved`.

## Context

Requests and risks on record for this subject, in replay order:

- `gh:acme/web#2/opened` · 2026-10-05T10:00:00+00:00 · gh:lin-eng:5002 (roles: engineer) · `request`: GitHub pull request opened · evidence `gh:acme/web#2/issue@748f0ccd2264/fact`, `gh:acme/web#2/issue@748f0ccd2264/text`
- `gh:acme/web#2/event/204` · 2026-10-06T15:00:00+00:00 · gh:lin-eng:5002 (roles: engineer) · `request`: GitHub review re-requested from gh:zhou-pm:5003 after changes requested · evidence `gh:acme/web#2/event/204/fact`

## Decision

- `gh:acme/web#2/event/207` · 2026-10-07T10:00:00+00:00 · gh:zhou-pm:5003 (roles: product\_manager) · **approved**; note: GitHub pull request merged at 54bb654c9e60 · evidence `gh:acme/web#2/event/207/fact`

Earlier authorized decisions on this subject, oldest first:

- `gh:acme/web#2/review/203` · 2026-10-06T09:00:00+00:00 · gh:zhou-pm:5003 (roles: product\_manager) · `reject`: GitHub review changes\_requested on commit 0c5a1f3e9b7d · evidence `gh:acme/web#2/review/203@c9068cf0646a/fact`, `gh:acme/web#2/review/203@c9068cf0646a/text`
- `gh:acme/web#2/review/206` · 2026-10-07T09:00:00+00:00 · gh:zhou-pm:5003 (roles: product\_manager) · `approve`: GitHub review approved on commit 54bb654c9e60 · evidence `gh:acme/web#2/review/206@67c4aa9e1b3a/fact`, `gh:acme/web#2/review/206@67c4aa9e1b3a/text`

## Consequences

The subject stays accepted until an authorized `reject` (roles: product\_manager) or a `revoke` of `gh:acme/web#2/event/207` (roles: product\_manager).

Conflicts recorded by replay:

- `decision_reversed` (`gh:acme/web#2/review/203`, `gh:acme/web#2/review/206`): reject by gh:zhou-pm:5003 then approve by gh:zhou-pm:5003

## Evidence

Each quote is the exact text at the given characters of a file whose raw bytes have the SHA-256 shown; every hash and quote was checked when the dataset was loaded. A hash proves that a quote is in those bytes, not that the source is authentic.

- `gh:acme/web#2/issue@748f0ccd2264/fact` · `sources/github/acme/web/2/issue-2-61a12a9d218f.txt:1` · characters 0–87 · sha256 `61a12a9d218f535be17e97d0e2bc248b5c1d280266259a1c3b7fd4156e23c91a` · source `https://github.com/acme/web/pull/2` · github\_pull\_request · captured 2026-10-09T12:00:00+00:00 · cited by `gh:acme/web#2/opened`
  > GitHub pull request acme/web\#2 opened by gh:lin-eng:5002 (User) at 2026-10-05T10:00:00Z
- `gh:acme/web#2/issue@748f0ccd2264/text` · `sources/github/acme/web/2/issue-2-61a12a9d218f.txt:4` · characters 393–416 · sha256 `61a12a9d218f535be17e97d0e2bc248b5c1d280266259a1c3b7fd4156e23c91a` · source `https://github.com/acme/web/pull/2` · github\_pull\_request · captured 2026-10-09T12:00:00+00:00 · cited by `gh:acme/web#2/opened`
  > Add CSV export endpoint
- `gh:acme/web#2/review/203@c9068cf0646a/fact` · `sources/github/acme/web/2/review-203-1b76d52c07b0.txt:1` · characters 0–123 · sha256 `1b76d52c07b0cad04f55794e0be736ada9622dc05ae9dec19de482265fb4fef4` · source `https://github.com/acme/web/pull/2#pullrequestreview-203` · github\_pull\_request\_review · captured 2026-10-09T12:00:00+00:00 · cited by `gh:acme/web#2/review/203`
  > GitHub review 203 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-06T09:00:00Z: changes\_requested on commit 0c5a1f3e9b7d
- `gh:acme/web#2/review/203@c9068cf0646a/text` · `sources/github/acme/web/2/review-203-1b76d52c07b0.txt:4` · characters 456–517 · sha256 `1b76d52c07b0cad04f55794e0be736ada9622dc05ae9dec19de482265fb4fef4` · source `https://github.com/acme/web/pull/2#pullrequestreview-203` · github\_pull\_request\_review · captured 2026-10-09T12:00:00+00:00 · cited by `gh:acme/web#2/review/203`
  > Hold this until the billing migration (acme/billing\#3) lands.
- `gh:acme/web#2/event/204/fact` · `sources/github/acme/web/2/event-204-b8feaa4b8834.txt:1` · characters 0–123 · sha256 `b8feaa4b8834333d600841736033a0ee3a0d140e96db8c0045b6afae61f09c9d` · source `https://api.github.com/repos/acme/web/issues/events/204` · github\_issue\_event · captured 2026-10-09T12:00:00+00:00 · cited by `gh:acme/web#2/event/204`
  > GitHub event 204 on acme/web\#2 by gh:lin-eng:5002 (User) at 2026-10-06T15:00:00Z: review\_requested reviewer gh:zhou-pm:5003
- `gh:acme/web#2/review/206@67c4aa9e1b3a/fact` · `sources/github/acme/web/2/review-206-b326a7cde4c0.txt:1` · characters 0–114 · sha256 `b326a7cde4c054563c7a40c6cb99f3f176685b34448d284385500b66c47ba518` · source `https://github.com/acme/web/pull/2#pullrequestreview-206` · github\_pull\_request\_review · captured 2026-10-09T12:00:00+00:00 · cited by `gh:acme/web#2/review/206`
  > GitHub review 206 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-07T09:00:00Z: approved on commit 54bb654c9e60
- `gh:acme/web#2/review/206@67c4aa9e1b3a/text` · `sources/github/acme/web/2/review-206-b326a7cde4c0.txt:4` · characters 438–495 · sha256 `b326a7cde4c054563c7a40c6cb99f3f176685b34448d284385500b66c47ba518` · source `https://github.com/acme/web/pull/2#pullrequestreview-206` · github\_pull\_request\_review · captured 2026-10-09T12:00:00+00:00 · cited by `gh:acme/web#2/review/206`
  > Approved now that export waits for the billing migration.
- `gh:acme/web#2/event/207/fact` · `sources/github/acme/web/2/event-207-b9890b2ca38e.txt:1` · characters 0–110 · sha256 `b9890b2ca38e9f38701b0d517452bf7c914b18cb8b10ca56ee56b634d6d15502` · source `https://api.github.com/repos/acme/web/issues/events/207` · github\_issue\_event · captured 2026-10-09T12:00:00+00:00 · cited by `gh:acme/web#2/event/207`
  > GitHub event 207 on acme/web\#2 by gh:zhou-pm:5003 (User) at 2026-10-07T10:00:00Z: merged (commit 54bb654c9e60)

*Generated by `orgmemory export` from the replayed event log, which it did not modify. It restates the log; it is not a rationale written by the decision maker.*
