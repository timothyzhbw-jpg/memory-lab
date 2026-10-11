# Effective-dated roles for orgmemory

Status: implemented (orgmemory, Unreleased). Code: `packages/orgmemory/src/orgmemory/authority.py`. Tests:
`tests/test_orgmemory_dated.py`. Example: `examples/orgmemory/feature-a-dated/`.

## Problem

`orgmemory replay` judged every event against the current `policy.json`. A role change therefore rewrote
history. Give the account manager the product manager role today and the account manager's ignored
"approval" from last Thursday becomes an authorized approval. Remove a product manager who has left and
every decision they made becomes an ignored attempt. Neither is what happened.

## Model

- **Baseline.** `policy.json` keeps its shape. Its `actors` hold their roles from the beginning of time.
  An optional `"as_of"` (ISO 8601 with a timezone) moves the start: before it, no actor holds a role.
- **Log.** An optional, append-only `policy-log.jsonl` in the dataset directory. One change per line:

  ```json
  {"id": "pl-wu-pm", "timestamp": "2026-10-09T09:00:00Z", "actor": "chen", "action": "grant", "subject_actor": "wu", "role": "product_manager", "evidence_ids": ["org-wu"]}
  {"id": "pl-zhou-leaves", "timestamp": "2026-10-12T09:00:00Z", "actor": "chen", "action": "revoke_role", "subject_actor": "zhou", "role": "product_manager", "evidence_ids": ["org-zhou"], "note": "moved to the platform team"}
  ```

  `timestamp` is the effective time: an ISO 8601 datetime with a timezone, never epoch seconds or
  milliseconds (including numeric strings). `actor` is who made the change. `subject_actor`
  is whose roles change. `evidence_ids` (at least one) name records in `evidence.jsonl`, as events do.
  `note` is optional free text. Unknown fields are rejected.
- **Who may change roles.** A new permission, `manage_roles`, in `policy.json` `permissions` lists the
  roles that may record a change. It is not an event action: no event can use it.
- **Bootstrap.** The first managers are the actors `policy.json` gives a `manage_roles` role. Every later
  manager is one the log made one.
- **Not dated.** `permissions` (which role may do what) has no history. Changing it in `policy.json` still
  changes every replay.

## Rules

1. **Order.** Changes apply in effective-time order. Changes with the same time apply in file order, as
   events with the same time do.
2. **Changes before events.** A change effective at T applies to every event at T. The roles an actor
   holds at time t are the baseline plus every change with a timestamp at or before t. "Effective at T"
   then means what it says, and a revocation at the same moment as a decision makes the decision
   unauthorized instead of depending on file order across two files.
3. **Cutoff.** `--as-of` applies the changes up to and including the cutoff, the same rule as for events.
   Later changes are not shown, but they are validated.
4. **Baseline.** With `as_of`, an event before it is judged with no roles (the reason in `explain` says
   so), and no change may take effect before it.
5. **Validation fails closed.** The log is trusted configuration, not a stream of claims. A change that
   does not hold is a mistake to fix, not an attempt to record, so it rejects the whole dataset (exit 2):
   - the actor holds no `manage_roles` role at the change's time, counting the changes before it;
   - a grant names a role that `policy.json` mentions nowhere (in `actors` or `permissions`), or that the
     subject already holds then;
   - a `revoke_role` removes a role the subject does not hold then;
   - the change takes effect before `as_of`;
   - its ID is repeated, equals an event ID, or starts with `gh:` (reserved for GitHub events, because the
     GitHub importer never reads this log), or it cites unknown evidence.
   The whole log is checked on every load, including changes after a cutoff. `orgmemory policy grant` and
   `orgmemory policy revoke` check the log with the new entry before appending. A backdated entry that
   would make a later one unauthorized (for example removing a manager's role before a change they made)
   is refused. `orgmemory append` checks a new event's ID against the log as well.
6. **Backdating is retrospective.** Appending a change with an earlier effective time can change outcomes
   of events already in the dataset, including replays with a past `--as-of`. For example, an approval on
   October 8 that was unauthorized becomes authorized if a manager later appends a grant effective on
   October 7. A backdated revocation can likewise invalidate an existing decision. Validation preserves
   the validity of later *policy changes*, not the previous outcomes of events. There is no recorded-at
   time: `--as-of` asks what is effective by the cutoff under the entire current log, not what entries
   were known when that cutoff occurred. Only events before a change's effective time keep their outcomes.

## What uses it

- **Replay.** `TrailEntry.roles`, `Decision.roles` and the authorization of each event use the roles at the
  event's effective time. `pending_approval` still lists roles from `permissions`. A refused decision's
  reason is unchanged, except before the baseline.
- **Explain.** The event history already shows each actor's roles. `explain` adds "Role changes of these
  actors": every applied change for an actor in the subject's history, with who made it, the roles before
  and after, the note and the evidence. If the current decision's maker had roles change later, a line
  under the decision says so and that the decision stands.
- **Audit.** A new finding kind, `role_changed_after_decision` (low): the current decision of a subject
  (its `last_authorized_decision`: approve, reject or revoke) was made by an actor who later, up to the
  cutoff, lost by `revoke_role` a role that authorized it, meaning a role they held then that `permissions`
  allows for that action. The finding cites the decision event and the `revoke_role` changes, each with
  its evidence. It is informational: the decision stands. Superseded decisions are not reported. A lost
  role that did not authorize the decision is not reported. `unauthorized_attempts` lists the roles held
  at each attempt when they differ ("roles at the time: engineer, then none"). The Markdown report adds a
  "Role changes" section with every applied change, and the JSON adds `dated_policy` and, on a finding
  that cites changes, `role_changes`.
- **Validate.** `orgmemory validate` (and the `orgmemory_validate` MCP tool, which prints the same lines)
  adds `roles: policy.json from the beginning of the log | as of T, then N role change(s) in
  policy-log.jsonl`. It also notes how many events predate the baseline. An actor granted a role in the
  log is not listed as an actor without any role.

## Backward compatibility

A dataset is *dated* when it has `policy-log.jsonl` (even an empty one) or its `policy.json` has `as_of`.
Nothing changes for any other dataset:

- every replay, explain, audit and validate output is byte-identical (`tests/test_orgmemory_dated.py`
  compares the example reports, and every output was diffed against the previous release);
- the JSON has no new fields: `dated_policy` and a finding's `role_changes` are omitted, not `null`;
- the audit lists `role_changed_after_decision` (summary table row and `kinds` key) only for dated
  datasets, because no other dataset can have one.

An empty log makes the dataset dated without changing any outcome: the reports gain the "Roles" header
line and an empty "Role changes" section.

## Alternatives considered

| Option | Why not |
|---|---|
| Versioned `policy.json` snapshots (one file per period) | Each change would duplicate the whole policy, with no per-change actor or evidence, and the diff between snapshots would be the real record |
| `policy.json` as the current state, with log entries before it undone in reverse | Two directions to reason about, and every entry would need an inverse consistency check. With forward-only replay plus `as_of`, a person reads the file top to bottom |
| Recording an unauthorized log entry as an ignored attempt, like an event | Events are claims from many people. The log is the user's configuration, and a broken entry should stop the replay, not be explained away |
| Re-judging a decision when its maker later loses the role | That is the bug this fixes. A later finding is informational |
| Always listing the new finding kind | Every existing audit report would change by one row of zeros |
| A JSON event in `orgmemory append` | Role changes are a different file with different fields and rules. A separate `orgmemory policy` command keeps "record what someone said" and "change who may decide" apart, for people and for agent skills |

## Limitations

- Identities are not authenticated: a change's `actor` is as trustworthy as the person who appended it.
- There is no correction entry or immutable historical snapshot. A `grant` or `revoke_role` changes roles
  from its effective time on, even when appended later. A user can correct the past with a valid backdated
  append, or edit `policy-log.jsonl` by hand (it is their configuration); validation checks the result.
- Self-grants by a manager are allowed and not flagged. The policy decides who may manage roles.
- The GitHub importer never reads or writes the policy log, as it never reads or writes `policy.json`.
