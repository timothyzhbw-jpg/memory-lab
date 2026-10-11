# Handoff: orgmemory GitHub Issues / PR ingestion (branch `github-ingest`)

中文摘要：这个分支实现"把 GitHub issue / PR 导入 orgmemory"。网络层已完成并测试；映射层代码已写但未测试、未审查；写入层、命令行、集成测试和示例还没做。设计合同在 `docs/design/github-ingestion-spec.md`，照它做，不要自己改规则。

Read `AGENTS.md` first (project conventions), then the spec `docs/design/github-ingestion-spec.md`. The spec is the
contract: section numbers below refer to it. It was produced by three independent designs, two judges and a synthesis;
its authority rules (§1, §7, §11) exist to stop GitHub activity from being turned into decisions by the wrong person.
Do not relax them.

## State when handed off

All tests pass: `python3 -m pytest -q` → 154 passed, 1 skipped (79 existing + 70 fetch tests + 5 others).

| Part | File | State |
|---|---|---|
| Fetch layer (§6) | `packages/orgmemory/src/orgmemory/github_fetch.py` | Done, 70 tests in `tests/test_github_fetch.py`, mutation-checked. Deviations listed below. |
| Test fakes | `tests/github_fakes.py` | Done: `FakeTransport`, `routes_from_capture`, builders. |
| Mapping layer (§5, §7, §8, §10) | `packages/orgmemory/src/orgmemory/github_map.py` | **Written (538 lines) but not tested or reviewed.** The agent writing it was stopped mid-way; check every row of the §7.2 table. |
| Fixtures (§13.1) | `tests/fixtures/github/capture-1.json`, `capture-2.json`, `policy.json`, `github.json` | Written (items #1–#13, #14 skipped); **not yet checked** against the §13.1 scenario table. |
| `load_events` refactor (§2) | `packages/orgmemory/src/orgmemory/replay.py` | Done, no behaviour change. |
| Docs (§14) | `README.md`, `README.zh-CN.md`, `skills/orgmemory/SKILL.md` | Written from the spec; **must be reconciled with the real CLI** once it exists. |

Fetch-layer deviations from the spec (keep them unless you find a reason not to):
- A stop during the very first request (`GET /repos/{o}/{r}`) raises `GitHubError` (exit 2), not a partial capture.
- A 404/410 on `GET /issues/{n}` (targeted or PR-scan-only numbers) is recorded in `capture.skipped`.
- `fetch()` with `issues` set ignores `since`; mode is `"targeted"`. The CLI must reject `--issue` with `--full`/`--since`.
- `since` is truncated to whole UTC seconds; `full_name` must equal the requested repo (case-insensitive).
- No parseable `Date` header on any response → `GitHubError`. Tokens with characters outside `\x21-\x7e` are rejected.
- The autouse no-network fixture currently lives in `tests/test_github_fetch.py`; move it to `conftest.py` (§13.1).

## Remaining work, in order

1. **Finish and test the mapping layer.** Check `github_map.py` against §5, §7 (every row M1–M31), §8, §10 and the §12
   signatures. Write `tests/test_github_map.py` with every test listed for it in §13.2. For the replay-outcome tests,
   materialize the plan (captures + manifest + events) in `tmp_path` and call `replay()`. If an expected state in the
   §13.1 table disagrees with what `replay()` produces for the events the mapping table prescribes, trust the mapping
   table and `replay.py`, fix the expectation, and note it here.
2. **Apply layer** `packages/orgmemory/src/orgmemory/github_apply.py` (§9, §4, §12): `lock`, `load_existing` (fail closed
   before any request), `load_config`, `next_since` (§6.4: only complete `sync` lines with mode `full`/`incremental`),
   `pinned_repo_id`, `Summary`, `apply` (resolve_inside; create-only content-addressed files via tmp + `os.replace`;
   tamper check; `verify_entry` on disk; append evidence → events → sync-log line), `run_sync`, `run_convert`.
3. **CLI** (§3): `orgmemory github sync|convert` in `cli.py`, exit codes 0/2/3, summary built from importer strings only,
   `--dry-run`, `--save-capture` (create-only, warns), `--since` only without a cursor, `--issue`. Bump orgmemory to
   `0.2.0` in `pyproject.toml` and `__init__.py`.
4. **Tests** `tests/test_github_apply.py`: every test listed for it in §13.2, plus the fetch-list tests that need
   apply/CLI (`--since` with a cursor exits 2, `test_cursor_rules`, rate-limit partial run → exit 3 with the cursor
   unchanged and a clean rerun, `test_fetch_convert_round_trip` over capture-1, `test_no_network_and_no_token_leak`,
   `test_cli_sync_and_convert`). Add the `github_dataset` fixture to `conftest.py`.
5. **Golden example** `examples/orgmemory/github-acme-web/`: `orgmemory github convert` of capture-1 then capture-2 into a
   copy of the fixture `policy.json` and `github.json`, plus `replay-report.md`. Make `github-sync.jsonl` deterministic
   (take `finished_at` from the capture or an injected clock) so `test_golden_example_reproducible` can compare bytes.
6. **Docs**: make README.md, README.zh-CN.md and SKILL.md match the real flags, defaults, exit codes and file names.
7. **Verify**: `python3 -m pytest -q` on Python 3.10 and 3.13; then review from three angles and fix what you confirm:
   - authority & security: can a crafted capture produce a state change attributed to someone who did not act, or get
     untrusted text into ids, notes, actors, subjects, paths, the fact line or terminal output? token leaks? fail-closed?
   - spec & replay: walk §7.2 row by row; run multi-run histories (capture-1 then capture-2 vs capture-2 alone).
   - tests & docs: does each §13.2 test assert what it claims (mutate the code and see it fail)?
8. **Manual smoke test** (needs network): `GH_TOKEN=$(gh auth token) orgmemory github sync /tmp/gh-smoke timothyzhbw-jpg/memory-lab`.

When done: squash the branch into one commit, merge into `main`, delete this file.
