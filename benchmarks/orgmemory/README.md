# orgmemory benchmark

Labeled datasets for measuring what `orgmemory` replay, audit and validation produce against what they should produce. Each case is a dataset (an event log, a policy, an evidence manifest and the evidence files it hashes) plus an `expected.json` of labels. `run.py` loads and replays every case through the library (`Dataset.load`, `replay`, `audit`), compares every labeled field, and reports each check.

The [measured results](RESULTS.md) list every label the implementation does not meet.

The cases attack the authority rules on purpose: unauthorized, unknown and look-alike actors, role changes, decisions at the same instant, reversals, invalid and chained revokes, cutoffs and effective time against file order, duplicate ids and keys, forged or mismatched evidence, dependency and risk interplay, reconsideration, and a GitHub import.

## Running it

From the repository root:

```bash
python benchmarks/orgmemory/run.py                          # markdown report; exit 1 on any mismatch
python benchmarks/orgmemory/run.py --check                  # validate the expectation files; replays nothing
python benchmarks/orgmemory/run.py --case revoke-chains --case role-change
python benchmarks/orgmemory/run.py --json results.json      # also write every check as JSON
python benchmarks/orgmemory/run.py --json -                 # JSON on stdout instead of the markdown report
```

It runs offline and needs no credentials. `run.py` imports `memcore` and `orgmemory` from `packages/` in this checkout, so it measures the code you have, not an installed copy; their dependencies must be installed (`pip install -e packages/memcore -e packages/orgmemory`).

| Option | Default | Meaning |
|---|---|---|
| `--case ID` | all cases | Run only these cases (repeatable) |
| `--json OUT` | none | Also write the totals and every check as JSON; `-` prints the JSON instead of the markdown |
| `--cases-dir` | `benchmarks/orgmemory/cases` | Where the cases live |
| `--check` | off | Validate the expectation files and stop |

Exit codes: `0` every check matches its label; `1` at least one mismatch (the report is printed in full); `2` a case or an expectation file is invalid, with every problem of every selected case listed.

## Cases

| Case | What it tests |
|---|---|
| `as-of-ordering` | Cutoffs and effective-time ordering against file order: entries appended late or written in other UTC offsets, an inclusive cutoff, a cutoff before every event, a revoke filed before its same-instant target, and the exact staleness boundary |
| `dependencies-risks` | Risks before and after an approval, a dependency that is proposed, then rejected, then approved, one with no events, repeated and circular declarations, and risks and dependencies from an actor who may not record them |
| `forged-evidence` | A changed hash digit, an edited source, a reworded quote, shifted, byte and UTF-16 offsets, a CRLF-normalized hash, path escapes, missing and non-UTF-8 files and unknown ids must fail; CRLF bytes, emoji offsets and a repeated quote stay valid; a consistent forgery loads |
| `github-acme-api` | An `orgmemory github convert` import: approve labels added and removed by the wrong people, a dismissed review, a merge by a non-approver, an unmapped reviewer, a bot's pull request, a reopen after a not-planned close, and a blocked-by link to a rejected issue |
| `invalid-log` | Logs and policies that must fail closed: a reused event id dated after any cutoff, revokes without or with an unknown target, malformed fields and lines, an event declaring its own authority, duplicate JSON keys, malformed policies; a CRLF log stays valid |
| `reconsideration` | Reconsideration after approval and after rejection, a reconsider the role may not make, a request after a revoke, a reconsider before any decision, self-approval, and staleness at the 14-day boundary and with `--stale-days 0` |
| `revoke-chains` | Revokes that name the current decision, an earlier one, another revoke, another subject's decision, or a decision not yet made; an unauthorized revoke; reversals with and without a revoke between |
| `role-change` | Role changes are not retroactive: `policy.json` holds today's roles, but an approval by a product manager since moved to advisor, and one by an engineer promoted later, should be judged by the role held at the time |
| `simultaneous-decisions` | Conflicting authorized decisions at the same instant, also in different UTC offsets and three at once; same-instant agreement and an ignored same-instant attempt are no conflict |
| `unauthorized-actors` | Unauthorized, unknown and look-alike actors, a role that differs only in case, a listed actor with no roles, and split approve and reject authority |

## Case format

```
benchmarks/orgmemory/cases/<case-id>/
  events.jsonl  policy.json  evidence.jsonl   the dataset, in the format orgmemory reads
  sources/...                                 the evidence files; the manifest's hashes and spans are real
  <file>.<variant>.<ext>                      optional replacement files that validation labels name
  expected.json                               the labels
```

`github-acme-api` also holds `capture.json`, `github.json` and `github-sync.jsonl`: it was built with `orgmemory github convert` from that capture, and a test rebuilds it byte for byte.

`expected.json` has a one-line `description`, `validation` labels and `snapshots`; a case needs at least one of the two lists. Every label has a `note`: one line on why it is right. Duplicate JSON keys anywhere in the file are an error.

```json
{
  "description": "What the case tests, in one line.",
  "validation": [
    {"name": "hash-one-digit", "evidence": "evidence.hash-one-digit.jsonl",
     "error": "evidence hash mismatch: q-f03", "note": "one hex digit of the recorded SHA-256 changed"}
  ],
  "snapshots": [
    {
      "as_of": "2026-10-20T00:00:00Z",
      "states": {
        "Export API": {"status": "approved", "pending_approval": [], "reconsideration_requested": false,
                       "risks": [], "dependencies": [], "last_authorized_decision": "u09",
                       "note": "zhou (product_manager) approves; the ignored attempts before it changed nothing."}
      },
      "trail": {"u02": {"outcome": "unauthorized", "note": "account_manager may not approve"}},
      "order": ["u01", "u09"],
      "audit": {"stale_days": 14, "findings": [
        {"kind": "unauthorized_attempts", "severity": "medium", "actor": "wu", "note": "one ignored approve"}
      ]}
    }
  ]
}
```

### Validation labels

Each loads the dataset with `Dataset.load`, as `orgmemory validate` does. `events`, `policy` and `evidence` default to the case's own files; a label replaces any of them with a file named relative to the case directory, which must exist and stay inside it. An evidence manifest's paths are relative to the manifest's directory, so variant manifests sit next to `evidence.jsonl`. `error` is a substring the error message must contain, or `null` when the files must load. `name` is lowercase words joined by hyphens, unique in the case.

A case whose own dataset is invalid (`invalid-log`) has only validation labels.

### Snapshots

A snapshot replays the case's own dataset up to `as_of` (ISO 8601 with a timezone; `null` replays the whole log). `as_of` is unique per case. It holds any of:

| Field | Labels |
|---|---|
| `states` | Every subject the replay knows at `as_of`, each with all six fields: `status` (`proposed`, `approved`, `rejected`), `pending_approval` (roles), `reconsideration_requested`, `risks` (ids of the `raise_risk` events on record), `dependencies` (subjects) and `last_authorized_decision` (an event id or `null`). `{}` means no subject yet |
| `trail` | Event id to its outcome in the audit trail: `applied`, `unauthorized`, `invalid_supersession`, or `not_replayed` for an event after `as_of` |
| `order` | At least two event ids, in the order replay must apply them (effective time, ties in file order) |
| `audit` | `stale_days` (default 14) and the complete list of findings `orgmemory audit` should report at `as_of`, each a `kind`, `severity` and who: `actor` alone for `unauthorized_attempts`, `subject` and `actor` for `self_approval`, `subject` alone otherwise. An audit snapshot needs `as_of`, the reference time for staleness |

`--check` also verifies that every referenced event id is in `events.jsonl` (read as plain JSON, not through `orgmemory`), that risks name `raise_risk` events and decisions name `approve`, `reject` or `revoke` events of the same subject, and that labeled subjects and actors occur in the log.

## Checks

Every label becomes one check, except a state label, which is one check per field (six). Two checks are implicit:

| Kind | Passes when |
|---|---|
| `validation` | The files load, or loading fails with a message containing `error`. Messages are compared as one line, without pydantic's documentation links, with the case path shown as `<case>` |
| `subjects` | (one per snapshot with `states`) The replayed subjects are exactly the labeled ones |
| `state` | The field equals the label. Lists compare sorted; `dependencies` as a set |
| `trail` | The event's outcome equals the label; an event missing from the trail is `not_replayed` |
| `order` | The labeled events appear in the trail in exactly that order |
| `audit` | Labeled findings are matched to reported ones by kind, subject and actor (equal severities first): one check per labeled finding compares its severity, or reports `not reported`. One more check per audit lists every finding that was reported but not labeled |

A failure inside `orgmemory` (an exception from `replay` or `audit`, or a labeled dataset that does not load) fails the affected checks with the error as the actual value; it does not stop the run.

The markdown report has totals per check kind and per case, then every mismatch with the expected and actual values and the label's note. `--json` adds every check with an `id` (`case | when | kind | target | field`), `expected`, `actual`, `passed` and `note`.

## Adding a case

1. Create `cases/<case-id>/sources/` and write the evidence files. They are kept byte-exact (`.gitattributes` marks `benchmarks/orgmemory/cases/**` as `-text`), so CRLF or non-UTF-8 bytes stay as written.
2. Register each quote with `orgmemory evidence add cases/<case-id> --id ... --path sources/... --quote "..." --source-uri ... --source-kind ... --captured-at ...`, which hashes the file and records the span; never hash by hand, and do not edit a source file afterwards. A GitHub-shaped case can instead be imported with `orgmemory github convert`.
3. Write `policy.json` and `events.jsonl`. Keep the defect of a validation variant to one per file, and add a valid variant when the case's own dataset is invalid, so each failure has one cause.
4. Write `expected.json` from the documented rules (the repository README's data formats, replay rules and audit table) and the policy, not from what `orgmemory` prints. Say in each note why the label is right.
5. Run `python benchmarks/orgmemory/run.py --check --case <case-id>`, then `--case <case-id>`.
6. Add the case to the table above, rerun the whole benchmark and update [RESULTS.md](RESULTS.md); `tests/test_orgbench.py` checks both.

When the implementation disagrees with a label, reread the rule. If the label is right, keep it and record the mismatch in RESULTS.md as a finding, and in `KNOWN_MISMATCHES` in `tests/test_orgbench.py`. Never edit a label, or the implementation, just to make the run pass.

## Caveats

- The cases are synthetic and small. They exercise known edge cases; the pass rate is not an estimate of correctness on real logs.
- Checks are not independent: one behavior usually fails several fields, snapshots and audit findings at once. Count findings, not failed checks.
- The labels are the authors' reading of the documented rules. The `role-change` labels judge authority at the time of each act, which the 0.3 policy format cannot express; RESULTS.md says so.
- `error` substrings for schema errors quote pydantic's wording (for example `Input should be a valid list`), which a pydantic upgrade could change.
