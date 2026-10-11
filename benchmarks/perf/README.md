# Audit and diff performance

Times `memfalsify` lexical `audit` and `diff` on deterministic inputs. One run each, in process, so process startup is not included. `diff` is a committed file against a working tree where every 10th claim is revised.

```bash
.venv/bin/python benchmarks/perf/run.py
.venv/bin/python benchmarks/perf/run.py --profile 1000
```

Synthetic files parse to exactly 100, 500, 1,000 and 3,000 claims. Claims in a group of eight share terms (so some pairs conflict); other groups do not. `README.md` is timed as itself, and again with the local `.md` links it follows. That linked run has no `diff` column: the link targets are the rest of this repository, not a synthetic git history.

Internal conflicts used to call the lexical verifier on every pair, and each call tokenized both texts again. They now keep one feature record per claim and compare a claim only with claims that share a rare term (or a one-term antonym). Reports are unchanged. A claim with Chinese text is measured per character, so it is found from the rarest of its bigrams, trigrams and lone characters that can still decide its coverage, and pairs with Chinese on either side are judged by the lexical verifier itself.

Timings below are one machine (macOS, CPython 3.13), before and after that change. README gained a few claims from the documentation sentence added with the change (305 → 307 alone, 1,888 → 1,895 with links). The synthetic files did not.

| Input | Claims | Audit before (s) | Audit after (s) | Diff before (s) | Diff after (s) |
|---|---:|---:|---:|---:|---:|
| synthetic-100 | 100 | 0.319 | 0.006 | 0.083 | 0.044 |
| synthetic-500 | 500 | 7.897 | 0.029 | 0.928 | 0.053 |
| synthetic-1000 | 1,000 | 31.907 | 0.083 | 3.553 | 0.073 |
| synthetic-3000 | 3,000 | 289.920 | 0.582 | 32.527 | 0.169 |
| README.md | 305 / 307 | 6.478 | 0.019 | 0.113 | 0.051 |
| README.md + links | 1,888 / 1,895 | 217.337 | 0.244 | — | — |

A 3,000-claim audit went from 290 s to 0.58 s. cProfile of the old 300-claim run spent almost all of its time in `LexicalVerifier.judge` → `features` → `stem` (on the order of 10^8 `endswith` calls). After the change, a 1,000-claim profile tokenizes each sentence a handful of times and spends the rest on the candidate pairs.
