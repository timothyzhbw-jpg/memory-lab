# Handoff: MemFalsify benchmark + GitHub Action (branch `eval-action`)

中文摘要：这个分支有两部分。**GitHub Action 已完成**，并在真实 GitHub 上跑通了 CI（Python 3.10–3.13 测试和自查任务全部通过）。**评测集完成大半**：测试脚本、英文题和中文题都写好了，还剩一组"仓库说明"类题目没写完、所有标注没复核、README 没更新。

Read `AGENTS.md` first. All tests pass: `python3 -m pytest -q` → 109 passed, 1 skipped.

## Done

- **GitHub Action**: `action.yml` (composite; inputs passed through env, isolated venv under `$RUNNER_TEMP`, built from a
  copy of the source), `action/memfalsify_action.py`, `tests/test_action.py`, `docs/github-action.md`.
  `.github/workflows/ci.yml` runs pytest on 3.10–3.13 and a `dogfood` job (`uses: ./`). **All five jobs passed on
  GitHub** at commit ce32d4b. Reviewed by the lead: no problems found.
- **`memfalsify audit --exclude GLOB`** (repeatable; fnmatch on paths relative to `--evidence`; `dir/**`, `dir/*`,
  `dir` prune the directory), with tests in `tests/test_memfalsify.py`. Reviewed: correct.
- **Benchmark harness**: `benchmarks/memfalsify/run.py`, `benchmarks/memfalsify/README.md` (label format, metric
  definitions), `tests/test_benchmark.py`. `python benchmarks/memfalsify/run.py --check` validates labels only.
- **Cases** (labels written, **not yet audited**):
  - `en-decisions-platform`, `en-decisions-product`, `en-decisions-security`: 50 claims.
  - `zh-decisions-platform`, `zh-decisions-process`, `zh-decisions-roadmap`: 51 claims.
  - `repo-instructions-sdk`: 17 claims, passes `--check`.

Lexical baseline on the 101 en + zh claims (pre-audit, will move): contradiction precision 0.59 / recall 0.28;
support recall 0.06; 3-way accuracy 0.43; Chinese contradiction recall 0.11; negation traps falsely flagged 4/26.

## Remaining work, in order

1. **Finish `repo-instructions-cli`.** Only its `evidence/` exists (justfile, pyproject.toml, bitbucket-pipelines.yml,
   src/fernhook/*.py). Write `MEMORY.md`, `labels.jsonl`, `case.json` per the format in `benchmarks/memfalsify/README.md`:
   CLAUDE.md/AGENTS.md-style instructions about that repo, tagged `repo`, plus claims that exercise the wording issues
   (overgeneralization, relative_time, possibly_stale with `as_of`, inference_marker, internal_conflict, duplicate).
   Until then, `python benchmarks/memfalsify/run.py` without `--case` fails validation on this case.
2. **Audit every label** (all 7 cases), adversarially and independently: read the evidence and decide each claim's truth
   and issues *before* looking at the label. Check that quotes exist verbatim; that `contradicted` is really incompatible
   (not just different scope or time); that `supported` is stated or entailed; that `unsupported` has no hidden support
   or contradiction; that issues match memfalsify's definitions (`text.py`, `audit.py`). Fix labels or reword ambiguous
   claims. **Do not tune cases to what the lexical verifier gets right or wrong.**
3. **Review**: (a) benchmark validity: re-label ≥30 claims independently, look for bias, wrong tags and world-knowledge
   dependence, recompute one case's metrics by hand; (b) Action security: script injection, quoting, exit codes, use
   from another repository, `--exclude` edge cases (symlinks, traversal).
4. **CI dogfood**: `CLAUDE.md` on `main` is now a one-line import of `AGENTS.md`. After merging `main` into this branch,
   point the `dogfood` job at `AGENTS.md` (or drop `memory:` and use the default `CLAUDE.md AGENTS.md`), and check it
   still passes with `strict: "true"`.
5. **Results and docs**: run `python benchmarks/memfalsify/run.py --json results-lexical.json` and commit a
   `benchmarks/memfalsify/RESULTS.md` with the lexical numbers. Model verifiers need keys (see `docs/local-setup.md`):
   add their rows only from real runs, never estimates. Add short README.md and README.zh-CN.md sections linking the
   benchmark and `docs/github-action.md`, and replace the call-count estimate in `docs/local-setup*.md` with the real
   `judge()` counts the harness reports.
6. **Verify** `python3 -m pytest -q` on 3.10 and 3.13; push and check the CI jobs pass on GitHub.

When done: squash the WIP commits into one, merge into `main`, delete this file.
