# Jira import for orgmemory: authority rules

Status: implemented as `orgmemory jira convert DATASET CAPTURE_JSON [--dry-run]` (offline only). It reuses the
machinery and invariants of the [GitHub ingestion spec](github-ingestion-spec.md) through
`orgmemory/ingest.py`; this note covers only what is specific to Jira. Code: `jira_map.py` (pure planning) and
`jira_apply.py` (validation and append-only writes).

## Input

The user saves the pages of one Jira Cloud search and wraps them:

```json
{"jira_capture": 1, "site": "https://acme.atlassian.net", "captured_at": "2026-10-09T12:00:00Z",
 "pages": [{"startAt": 0, "maxResults": 100, "total": 230, "issues": ["..."]}, "..."]}
```

Each page is the unmodified response of
`GET <site>/rest/api/3/search?jql=...&fields=summary,description,created,creator,reporter,status,comment&expand=changelog&startAt=N`.
The README has the curl and jq recipe. The importer contains no network code and handles no credentials.

## Authority rules

1. **Only a status transition can be a decision, and only by configuration.** `jira.json` maps target status
   ids to `approve`, `reject`, `revoke` or `reconsider`:
   `{"statuses": {"10003": "approve", "10004": "reject", "10005": "revoke", "10007": "reconsider"}}`.
   Ids, not names: a renamed status keeps its id, while old changelog entries keep the old name, and two
   workflows can have statuses with the same name. Without `jira.json`, nothing is a decision. Every other
   status transition is a `comment`. Other changelog items are not events: assignee, resolution, priority,
   sprint, links, edits and custom fields. That includes a custom field named "status", because only an
   item with `fieldtype` `jira` and `fieldId` `status` counts.
2. **One actor field per act.** Creation is attributed to `fields.creator`, never to `reporter`, which can
   be set to someone else. A comment is attributed to its `author`, never to `updateAuthor`; an edit is
   noted, and the editor is named. A transition is attributed to its changelog history's `author`. Display
   names, mentions and text are never read for identity. The actor ID is `jira:<accountId>`.
3. **`revoke` needs a decision to withdraw.** A transition into a `revoke` status supersedes the latest
   earlier transition on the same issue whose action is `approve` or `reject`. That action is read from
   the log first and from this run second, so changing `jira.json` never changes the target of an event
   already in the log. With no such transition, it is a `comment`. Replay then checks that the revoker
   may revoke and that the target is the current decision (`invalid_supersession` otherwise).
4. **Automation is comment-only.** Only `accountType` `atlassian` (people) and `customer` (Service
   Management portal customers) can make a decision. An `app` account (Automation for Jira, Connect and
   Forge apps), `unknown`, or a missing type turns the act into a `comment`. The note keeps the action it
   would have been.
5. **No actor, no event.** A history or comment without an author (system or anonymous changes), or with
   an account id that is missing, `unknown` or malformed, produces no event and is listed under `skipped`.
   A deactivated or deleted account still has its account id, so its past acts stay attributed to that
   account, and the policy decides whether they count. `active` and `displayName` are recorded for
   auditors but never used for mapping: they describe the account when the capture was made, not when
   it acted.
6. **Policy-blind.** The importer never opens `policy.json`, and its output is byte-identical under any
   policy. Unmapped `jira:` accounts are recorded, and replay ignores their decisions.
7. **Text is inert.** Summaries, descriptions, comment bodies (Atlassian Document Format flattened to text,
   mentions and link URLs), status names and display names are never parsed. They appear only after the
   capture file's separator, or JSON-escaped in its record line. Fact lines, ids, notes, paths and the
   terminal summary are built from validated keys, numeric ids, account ids, timestamps and constants.

## Completeness: an issue is imported whole or not at all

A partial history replays to a wrong state rather than an unknown one: for example, a missing `reject`
leaves the subject `proposed`. Problems are therefore handled as follows.

| Problem | Result |
|---|---|
| Not a `jira_capture` 1 document; site not `https://<name>.atlassian.net`; an issue's `self` is not `<site>/rest/api/3/issue/<id>` (another site, or API v2); invalid key, id, timestamp or changelog item; duplicate history or comment ids; a history that changes the status twice; one issue key with two ids | Exit 2, nothing written |
| The same issue on two pages with different content (pages saved from two runs) | Exit 2, nothing written |
| An identical duplicate copy (a page saved twice) | Ignored and counted |
| Pages do not cover `[0, total)`, disagree on `total`, extend past it, or put two issues at one position | Incomplete: exit 3, and the issues present are applied |
| An issue without `changelog` or `comment`, or with either truncated (`startAt` > 0 or `total` > length) | That issue is skipped whole; incomplete |
| Consecutive status changes do not chain (`to` of one is not `from` of the next), or the last one does not end at `fields.status.id` | That issue is skipped whole; incomplete |
| An issue id recorded under another key (a moved issue), or a key recorded for another id | That issue is skipped and reported; the run can still be complete |

The status chain check also catches a truncated changelog when `total` is wrong. A run that is incomplete
still appends its `jira-sync.jsonl` line, with `complete: false` and the reasons.

## Identity and re-imports

| | Format | Example |
|---|---|---|
| Subject | Issue key | `ACME-1` |
| Actor | `jira:<accountId>` | `jira:61a0c3e5f2b1d40069a1b2c3` |
| Event | `jira:<key>/created`, `/comment/<id>`, `/transition/<history id>` | `jira:ACME-1/transition/20102` |
| Evidence | `jira:<key>/issue/<issue id>@<th12>`, `/comment/<id>@<th12>`, `/transition/<history id>`, then `/fact` or `/text` | `jira:ACME-1/transition/20102/fact` |
| Source file | `sources/jira/<key>/{issue,comment,transition}-<id>-<fh12>.txt` | |

- Timestamps are normalized to UTC with milliseconds, so a capture made in another profile time zone yields
  the same ids and facts.
- Re-importing appends only what is new. An existing event is never rewritten. If its recomputed action,
  actor, subject or target differs (for example after a `jira.json` change), it is listed under `drift`.
- An edited summary, description or comment gets a new evidence version and no event. A logged comment
  that is missing from a complete re-capture of its issue is reported as `gone` and kept.
- `jira-sync.jsonl` pins the site: one dataset imports one Jira site, because subjects are bare keys.
- The GitHub and Jira importers each hold a lock. Neither starts while the other's lock exists.

## Residual risks

- A Jira account is not an authenticated person. Anyone who can perform a transition is recorded as
  making that decision, and only the policy filters it. Map only statuses whose transitions your workflow
  restricts, and map only accounts you have verified in Jira, never by display name.
- An automation rule that runs as the user who triggered it is attributed to that user. A Service
  Management approval that a rule turns into a transition is an app's `comment`.
- Decisions expressed only through a resolution ("Done" with "Won't Do") are not mapped; use statuses.
- Bulk moves or migrations that change a status without a changelog item make the issue fail the chain
  check, so it is skipped.
- Hashes prove the bytes the importer rendered from the capture, not what Jira served. A capture is trusted
  as what Jira said, so convert only captures you saved yourself.

## Out of scope

Fetching from Jira; the token-paged `/rest/api/3/search/jql` endpoint (its pages carry no `startAt` or
`total`, so completeness cannot be checked, and they fail as malformed today); merging full changelogs from
`/rest/api/3/issue/{key}/changelog` for issues with more history than a search page returns; following moved
issues; several sites per dataset; issue links as dependencies; risks.
