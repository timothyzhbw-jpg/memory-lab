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

## MemFalsify audit on a large repository

A monorepo stresses the evidence side of an audit: millions of passages rather than thousands of claims. `--evidence N` times one audit of 200 synthetic claims against a synthetic repository of about N passages, where every line holds terms common to the whole repository and a few lines restate a claim.

```bash
.venv/bin/python benchmarks/perf/run.py --evidence 200000
```

At 200,000 passages that went from 13.0 s and 711 MB of peak RSS to 1.6 s and 270 MB. The script only prints; nothing asserts a time. `tests/test_audit_speed.py` guards the speed by counting work instead: a claim must not read passages it cannot reach, a stem is worked out once per token, line numbers do not count from the start of the file, and the lexical verifier is not asked about pairs it must call unrelated.

The table below is the same audit on real public repositories, as shallow clones: `memfalsify audit FILE --evidence REPO --format json --as-of 2026-11-04`, the whole process, one run each, on one machine (Apple M4 Pro, 24 GB, CPython 3.13.9) with other jobs running, at a load average around 8. Before is the tree before these changes. n8n and bun hit a 900 s limit there, so their old peak is unknown. Peak RSS comes from `/usr/bin/time -l`. macOS leaves compressed pages out of RSS, so the figure moves with memory pressure; the peak memory footprint from the same tool is in brackets.

| Repository | File | Passages | Claims | Before (s) | After (s) | Peak RSS before (MB) | Peak RSS after (MB) |
|---|---|---:|---:|---:|---:|---:|---:|
| mem0 | `CLAUDE.md` | 290,145 | 746 | 113.2 | 3.7 | 1,106 (1,108) | 402 (356) |
| supabase | `CLAUDE.md` | 1,413,533 | 50 | 197.0 | 17.9 | 4,015 (4,362) | 1,138 (1,147) |
| next.js | `AGENTS.md` | 2,350,303 | 324 | 1,197.0 | 42.0 | 5,752 (6,525) | 1,352 (2,116) |
| grafana | `CLAUDE.md` | 3,978,701 | 87 | 946.9 | 35.7 | 9,664 (10,671) | 2,026 (2,466) |
| n8n | `AGENTS.md` | 4,272,802 | 1,057 | over 900 | 53.6 | not known | 2,417 (2,749) |
| bun | `AGENTS.md` | 6,907,553 | 169 | over 900 | 70.5 | not known | 3,255 (4,958) |

Small repositories gained too: excalidraw 16.0 s to 2.4 s, browser-use 13.7 s to 1.1 s, withastro/astro 60.0 s to 6.0 s, astral-sh/uv 55.1 s to 4.0 s, facebook/react 45.6 s to 7.6 s, langchain 80.2 s to 4.1 s, deno 201.4 s to 12.7 s.

Reports stay byte-identical. Where both versions finish, the JSON report of each run above is the same file before and after.

A cProfile of the supabase run showed where the time went, and each of these changed:

- Every claim walked every passage. `_PassageIndex` now keeps, for the terms of the claims being audited and their opposites, which passages hold them, and a claim scores only the passages it can reach. A passage needs a share of the claim's terms to clear the threshold, so the claim's rarest terms are enough to find it, except that an opposing term counts as a match, so the opposites are looked up too. Candidates are scored in passage order, so ties keep the order they always had.
- Scoring tokenized the passage again. It now reads the terms the scan already found, and only a claim with Chinese text, measured per character, goes through `coverage`.
- Most shortlisted pairs fell between the shortlist's threshold (0.5) and the lexical verifier's `related` (0.6). The verifier has to call those unrelated and the report drops them, so they are no longer asked about: 3.1 million pairs on n8n, of which about 95% were such pairs. A subclass of `LexicalVerifier` and every model verifier still get all of them.
- Each passage carried two Pydantic models and their dictionaries. A `Passage` is now a small dataclass that builds its `EvidenceSpan` when asked, which is most of the memory saved.
- Line numbers counted newlines from the start of the file, once per passage. They now come from a bisection over the newline positions.
- `stem` ran about 18 `endswith` checks per token with nothing memoized. A bounded cache from token to term now answers over nine lookups in ten, and passages share one string per term.
- The cycle collector walked the passages again and again, about a quarter of the run on supabase and next.js, and found nothing to free. It now stays off while they are built, and they are frozen until the audit ends (`out_of_collection`). It runs as usual for anything made after that, such as a model verifier's API client.
- `Path.relative_to` builds every parent to answer, and `input_path` asks it several times per file. A comparison of `parts` answers the common case, and pathlib still decides everything else.

What is left is mostly the scan itself: about 6 µs per passage to split sentences and extract terms, and about 400 µs per file for the path policy and the confined read. Holding one `confined_reads` open across a scan would save about a third of that per-file cost, but it moves the CLI from `os.walk` to the descriptor walk, which reports some `stat` errors differently, so it was left alone. Process pools were not needed.

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
