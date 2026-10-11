# orgmemory benchmark results

Measured locally on 2026-10-11 with orgmemory 0.3.0 and memlab-core 0.2.0, offline. Reproduce from the repository root:

```bash
python benchmarks/orgmemory/run.py --json results.json   # exits 1 because of the mismatches below
```

[Case format, check definitions and how to add a case](README.md)

The labels were written from the documented rules (the repository README's data formats, replay rules, GitHub mapping table and audit table) and the case policies, not from `orgmemory` output. Five labels were added after the first run as further probes (duplicate JSON keys, a CRLF log, a look-alike actor, a cutoff before every event); no label was edited to match a result.

588 of 598 checks pass. The 10 failing checks come from two findings. A single behavior usually fails several checks, so count findings, not checks.

## Findings

### 1. Duplicate JSON keys are accepted, and the last one wins (2 checks, `invalid-log`)

An event line that names `actor` twice loads, and replay uses the last value. The `duplicate-key-event` line reads `"actor": "lin"` (an engineer, who may not approve) first and `"actor": "zhou"` last, and it replays as Zhou's applied approval. A `policy.json` that lists `lin` twice (`engineer`, then `product_manager`) loads and grants `product_manager`.

RFC 8259 (section 4) calls the behavior of software receiving duplicate names unpredictable: some parsers keep the last pair, some keep every pair, some fail. A reviewer's tool and `orgmemory` can therefore read different authority from the same bytes. Every other malformed input in these files fails closed; these should too. The cause is that `memcore.read_jsonl` (events and evidence manifests) and `Dataset.load` (the policy) call `json.loads` without a duplicate-name check. An `object_pairs_hook` that rejects repeated names would cover all three files. Not fixed here.

### 2. Role changes apply retroactively (8 checks, `role-change`)

`policy.json` has no effective dates, so replay judges every event by today's roles. In the case, Lin moved from engineering to product manager and Zhou from product manager to advisor on 2026-10-10, as a dated announcement in its sources says. Measured:

- Zhou's approval of *Audit log export* on 10-03, made as product manager, is `unauthorized`. The subject falls back to `proposed`, awaiting `product_manager` (1 trail and 3 state checks), and the audit reports a `stale_pending_approval` for it.
- Lin's approval of *Bulk delete* on 10-05, made as an engineer, is `applied` (1 trail check). Ana's later reject then shows as a `decision_reversed`, and Lin's attempt is missing from `unauthorized_attempts`.
- Zhou's `unauthorized_attempts` rises from medium to high, because the legitimate 10-03 approval counts as a second ignored decision.

The README states the limitation: there is no policy versioning yet, and changing the policy changes every replay. These checks measure its effect. Acts after the change (*Webhooks*) are judged correctly either way. A fix needs dated role grants in `policy.json`, evaluated at each event's effective time.

## What matches the labels

- Authority: unknown actors, a listed actor with no roles, a role that differs only in case, a Cyrillic look-alike actor ID, split approve and reject roles (`pending_approval` names only roles that may do both), and risks and dependencies from actors who may not record them.
- Time: inclusive cutoffs written in any UTC offset, a cutoff before every event, effective time over file order, ties in file order, and the strict 14-day staleness boundary (exactly 14 days is not stale; one second more is).
- Decisions: same-instant conflicts, also across UTC offsets and three at once, with the documented severities; reversals with and without a revoke between; revokes that name an earlier, chained, cross-subject or not-yet-made decision; and a revoke of the current revoke, which leaves the subject `proposed`.
- Evidence: every forged or mismatched hash, quote, offset (shifted, UTF-8 bytes, UTF-16 units), path, file and ID fails with the expected error. CRLF bytes, offsets after an emoji and a quote at its second occurrence stay valid. A consistent forgery (an edited file with its own correct hash and span) loads, as the README says: a hash proves bytes, not authenticity.
- Logs and policies: a reused event ID dated after any cutoff, missing or unknown revoke targets, malformed fields and lines, an event declaring its own authority, and malformed policies fail closed; a CRLF log loads.
- GitHub import: approve labels added and removed by the wrong people, a dismissed review, a merge by a non-approver, an unmapped reviewer, a bot's pull request, a reopen after a not-planned close and a blocked-by link replay and audit as the README's mapping table says.

## Harness output

- orgmemory 0.3.0 · cases: 10 · labels: 233 · checks: 598
- Result: 588/598 checks pass, 10 fail

## By check kind

| Kind | Checks | Pass | Fail |
|---|---|---|---|
| validation | 37 | 35 | 2 |
| subjects | 22 | 22 | 0 |
| state | 390 | 387 | 3 |
| trail | 54 | 52 | 2 |
| order | 4 | 4 | 0 |
| audit | 91 | 88 | 3 |

## By case

| Case | Labels | Checks | Pass | Fail | What it tests |
|---|---|---|---|---|---|
| as-of-ordering | 26 | 92 | 92 | 0 | As-of cutoffs and effective-time ordering versus file order: entries appended out of order and in other UTC offsets replay by effective time, the cutoff is inclusive, ties keep file order (a revoke filed before its same-instant target is invalid), and the staleness boundary is strict. |
| dependencies-risks | 25 | 61 | 61 | 0 | Dependency and risk interplay on an approved subject: risks before and after the approval, a dependency that is proposed, then rejected, then approved, one with no events, repeated and circular declarations, and risks and dependencies from an actor who may not record them. |
| forged-evidence | 17 | 24 | 24 | 0 | Forged and mismatched evidence: a changed byte, an edited source, a reworded quote, shifted or byte/UTF-16 offsets, CRLF-normalized hashes, path escapes and unknown ids must fail validation, while CRLF bytes, emoji offsets and a repeated quote stay valid and a consistent forgery shows what a hash cannot prove. |
| github-acme-api | 32 | 85 | 85 | 0 | A GitHub import (orgmemory github convert of capture.json): approve labels added and removed by the wrong people, a dismissed review, a merge by a non-approver, an unmapped reviewer, a bot-opened PR, a reopen after a not-planned close, and a blocked-by link to a rejected issue. |
| invalid-log | 20 | 20 | 18 | 2 | Invalid event logs and policies fail closed before any replay: a reused event id (even dated after any cutoff), a revoke without or with an unknown target, malformed fields and lines, an event that declares its own authority, ambiguous duplicate JSON keys, and malformed policies; a CRLF log stays valid. |
| reconsideration | 24 | 74 | 74 | 0 | Reconsideration after approval and after rejection, a reconsider from a role that may not, a request after a revoke (not a reconsideration), a reconsider before any decision, self-approval, and stale pending approvals and reconsiderations at the exact 14-day boundary and with --stale-days 0. |
| revoke-chains | 23 | 56 | 56 | 0 | Revoke chains and invalid supersession: a revoke must name the decision in force on its own subject at its effective time; stale, cross-subject, backdated and unauthorized revokes change nothing, and reversals are reported unless a revoke came between. |
| role-change | 12 | 34 | 26 | 8 | Role changes are not retroactive: policy.json holds today's roles (Lin promoted to product manager and Zhou moved to advisor on 2026-10-10, per the dated announcement), but each act should be judged by the role held when it took effect. |
| simultaneous-decisions | 23 | 76 | 76 | 0 | Conflicting authorized decisions at the same instant (also written in different UTC offsets, and three at once): file order decides and the conflict is reported; same-instant agreement or an ignored attempt is no conflict. |
| unauthorized-actors | 31 | 76 | 76 | 0 | Unauthorized and unknown actors, a look-alike actor id, a role that differs only in case, a listed actor with no roles, and split approve/reject authority: none of the ignored events may change state, and pending_approval names only roles that may both approve and reject. |

## Mismatches

| Case | When | Check | Expected | Actual | Label note |
|---|---|---|---|---|---|
| invalid-log | load | validation duplicate-key-event | error containing 'duplicate' | loads | the approve line names its actor twice ('lin', then 'zhou'); JSON parsers disagree on which one wins, so an authority log must refuse the line rather than silently pick the authorized one |
| invalid-log | load | validation duplicate-key-policy | error containing 'duplicate' | loads | 'lin' is listed twice in actors (engineer, then product_manager); an ambiguous grant must fail closed rather than silently grant the last one |
| role-change | 2026-10-09T00:00:00+00:00 | trail c02 outcome | applied | unauthorized | Zhou was product manager on 2026-10-03; the later move to advisor does not undo that approval |
| role-change | 2026-10-09T00:00:00+00:00 | trail c04 outcome | unauthorized | applied | Lin was an engineer on 2026-10-05; the later promotion does not make that approval authorized |
| role-change | 2026-10-20T00:00:00+00:00 | state Audit log export status | approved | proposed | Approved by Zhou while Zhou was product manager. |
| role-change | 2026-10-20T00:00:00+00:00 | state Audit log export pending_approval | [] | ["product_manager"] | Approved by Zhou while Zhou was product manager. |
| role-change | 2026-10-20T00:00:00+00:00 | state Audit log export last_authorized_decision | c02 | null | Approved by Zhou while Zhou was product manager. |
| role-change | 2026-10-20T00:00:00+00:00 | audit unauthorized_attempts zhou severity | medium | high | only the post-change approve of Webhooks was outside Zhou's role |
| role-change | 2026-10-20T00:00:00+00:00 | audit unauthorized_attempts lin severity | medium | not reported | Lin's approve of Bulk delete came before the promotion |
| role-change | 2026-10-20T00:00:00+00:00 | audit * unexpected findings | [] | ["decision_reversed Bulk delete (medium)", "stale_pending_approval Audit log export (medium)"] | the labeled findings are all the audit should report |
