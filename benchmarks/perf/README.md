# Performance benchmarks

Two scripts time the slow paths on deterministic inputs: `run.py` times MemFalsify's lexical `audit` and `diff`, and `orgmemory.py` times orgmemory's `validate`, `replay`, `explain` and `audit`.

## MemFalsify audit and diff

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

## orgmemory validate, replay, explain and audit

`orgmemory.py` builds a deterministic synthetic dataset and times `validate`, `replay`, `explain` and `audit` the way the CLI runs them: load the dataset, do the work, and build the default text report. Dataset generation is timed separately and is not included in the command times. Peak memory is tracemalloc's high-water mark for that call, so the seconds include tracing overhead. The peak is Python allocations, not the process RSS.

### Running it

From the repository root, with the packages installed in `.venv`:

```bash
.venv/bin/python benchmarks/perf/orgmemory.py
.venv/bin/python benchmarks/perf/orgmemory.py --sizes 1000,10000,100000 --seed 20261011 --json /tmp/orgmemory-perf.json
```

`--sizes` is a comma-separated list of event counts. `--seed` defaults to `20261011`. `--json` writes the raw seconds, peak bytes and output bytes.

The script imports `memcore` and `orgmemory` from `packages/` in this checkout. Its own directory is removed from `sys.path` first, because the file is named `orgmemory.py`.

### Dataset

One seed and one event count always produce the same files. The layout for `n` events is:

| | subjects | evidence files | spans per file |
|---|---:|---:|---:|
| rule | `max(40, n // 30)` | `max(4, min(100, n // 1000))` | 20 when `n < 5000`, otherwise 50 |

At the default sizes that is 1,000 events / 40 subjects / 4 files / 80 spans / 33,200 evidence bytes; 10,000 / 333 / 10 / 500 / 320,900; 100,000 / 3,333 / 100 / 5,000 / 3,209,000.

Actors are `ada`, `ben`, `cy`, `dee`, `eli`, `fay` and `gus`, plus the unmapped actor `mallory`. Action weights (out of 100) are request 18, comment 14, raise_risk 10, dependency 12, approve 14, reject 10, reconsider 12, revoke 10. The first event is always a request by `eli` on `s0000`, which is also the subject `explain` prints. Each later event draws its actor from the people allowed to perform that action 72% of the time, and otherwise from all eight names, so events from that second draw can be allowed too. A revoke stores a random earlier event id in `supersedes`. That id may not be the decision in force, and the event stays a revoke.

The first event's subject is `s0000`. Each later event picks a subject uniformly half the time and otherwise with a squared skew toward low indexes, so a few trails are long. Event timestamps start at `2026-01-05T09:00:00+00:00` and usually advance 1–120 minutes; about one event in twenty keeps the previous timestamp so replay has ties. Evidence files are `sources/fNNNN.txt` with unique ASCII quotes, SHA-256 manifest entries and `captured_at` `2026-01-05T09:00:00Z`. Each file has padding lines so hashing reads a realistic body (120 lines below 5,000 events, 480 lines otherwise).

`audit` measures staleness at the current UTC time, truncated to a whole second, as the CLI does, and writes ages as `Nd Mh`. Those strings follow the wall clock, so an audit report's byte length can change when an age gains a digit between two runs. `validate`, `replay` and `explain` are fixed for the seed. `tests/test_orgmemory_diff.py` freezes the audit clock and compares replay JSON, audit JSON and both Markdown renderers to the original algorithm in `tests/orgmemory_reference.py`.

### Measurements

Python 3.13.9, macOS-27.0-arm64, seed `20261011`, one run each. The before column is the tree before these optimizations. The after column is the same machine after them.

| Events | Command | Before (s) | After (s) | Speedup | Before peak MiB | After peak MiB | Output KiB |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1,000 | validate | 0.057 | 0.017 | 3.4× | 1.5 | 1.5 | 0.1 |
| 1,000 | replay | 0.109 | 0.053 | 2.1× | 3.0 | 3.1 | 148.7 |
| 1,000 | explain | 0.092 | 0.043 | 2.1× | 2.9 | 2.9 | 10.2 |
| 1,000 | audit | 0.191 | 0.072 | 2.7× | 4.2 | 4.3 | 229.8 |
| 10,000 | validate | 0.408 | 0.144 | 2.8× | 15.1 | 15.1 | 0.1 |
| 10,000 | replay | 1.028 | 0.548 | 1.9× | 29.3 | 29.4 | 1449.4 |
| 10,000 | explain | 0.902 | 0.478 | 1.9× | 30.3 | 29.5 | 14.0 |
| 10,000 | audit | 1.813 | 0.774 | 2.3× | 41.4 | 42.3 | 2370.6 |
| 100,000 | validate | 4.009 | 1.654 | 2.4× | 151.0 | 150.6 | 0.1 |
| 100,000 | replay | 24.071 | 6.799 | 3.5× | 293.1 | 294.2 | 14566.6 |
| 100,000 | explain | 9.072 | 5.650 | 1.6× | 294.0 | 294.2 | 18.5 |
| 100,000 | audit | 22.116 | 10.307 | 2.1× | 416.4 | 424.7 | 23108.7 |

Output bytes matched exactly for `validate`, `replay` and `explain` at every size, and for `audit` at 1,000 and 100,000 events. The 10,000-event audit was 2,427,540 bytes before and 2,427,541 bytes after: one age string gained a digit between the two clocks. Rounded to 0.1 KiB the tables still match.

The peak stays in the same range because the trail and the report are the same size. At 100,000 events the audit peak is about 8 MiB higher (416.4 → 424.7) from the dependency and request indexes held while the report is built.

The time comes from doing each piece of work once per load: each distinct evidence path is read and hashed once, each actor's permissions are resolved once, each subject state is built once, dependencies are sorted once, and the audit walks the trail once. The Markdown renderer still applies the previous escaping to any line that needs it, and skips that scan when the line has nothing to escape.
