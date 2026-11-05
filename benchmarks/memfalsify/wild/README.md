# MemFalsify on real repositories

The [synthetic benchmark](../README.md) checks known failure modes. This one asks a different question: when the lexical verifier flags a line in a real project's `CLAUDE.md` or `AGENTS.md`, is the flag worth reading?

`labels.jsonl` holds 413 labeled pairs from the agent files of public repositories: a claim and the source passage the verifier held against it. It has metadata and hashes only (repository, commit, memory file and line, source path and line, the SHA-256 of the claim and of the quote, the rule at labeling time, the label, the reason and a file tier). No text of those projects is in this repository beyond file names, so the script needs clones.

## Running it

```bash
python benchmarks/memfalsify/wild/run.py --check                     # the labels' format; needs no clones
python benchmarks/memfalsify/wild/run.py --clones DIR                # the labeled pairs, then full audits
python benchmarks/memfalsify/wild/run.py --clones DIR --no-audit     # the labeled pairs only, in seconds
python benchmarks/memfalsify/wild/run.py --clones DIR --json OUT
```

`DIR` holds shallow clones named `<owner>__<name>`, at the commits in `labels.jsonl`. The clones are untrusted data: the script only reads their files. It runs nothing from them, not even git, and you should keep them outside this repository and not run an interpreter inside them. A pair whose text is no longer found at a commit is counted as unresolved, and a clone at another commit is named.

It reports, for the code you have: per labeling-time rule, how many labeled pairs are still flagged, how many of those are real, and which real rows were lost; then, from full audits of the same memory files, how many flagged pairs have no label. Those last ones are flags nobody has judged. CI has no clones, so it checks the format and scores a made-up clone (`tests/test_wild_benchmark.py`).

## How the labels were made

All labeling is by AI models, reviewed by the lead of the work, who is also an AI session. No person labeled anything. Keep that in mind when reading the numbers.

1. Audits of 15 projects' agent files with the verifier as it was before this work gave 1,371 unique (claim, source) contradiction pairs, from 14 of the projects: 1,218 from "only one side is negated", 83 from opposing terms, 70 from different values. Two of the 27 agent files are not in those pairs: grafana's `AGENTS.md` timed out, and ruff's `AGENTS.md` finished after the pairs were extracted.
2. The sample is every opposing and every numbers pair, plus negation capped at 25 per repository and 260 in all, in a fixed order: 413 pairs.
3. Two models labeled each pair independently, Claude and then Codex (OpenAI), each reading the source around the line in the clone. Neither saw the other's labels. `real` means the memory line and the source cannot both be true about the same thing; everything else is `not_real`, with a reason.
4. They disagreed on 2 of 413 pairs, both where Claude said `real` and Codex said `not_real`. Both went to `not_real` on adjudication.

The result is 11 real and 402 not real.

The way text is read changed after the labeling (wrapped lines are joined in claims and in prose evidence, abbreviations no longer cut a sentence, fragments are no longer claims), so the hashes were re-pointed at the text as it now reads. Of the 413 pairs, 72 name the longer claim that holds the labeled piece and 26 the longer passage, with the label kept. 19 pairs, all `not_real`, name text that is no longer a claim (a label, a table header, a closing tag) and carry `claim_dropped`: they count as no longer flagged. Nobody labeled the longer claims again, so a pair labeled on a fragment now stands for its whole sentence.

The rules were tuned on these repositories. A further set of repositories was kept out as a holdout, and is not part of these labels.

## How it can mislead

- **It says nothing about recall.** Only pairs the old verifier flagged were labeled, so "real rows lost" counts losses among those. Stale lines the verifier never flagged are not in here. Row by row, the numbers also treat a pair as one unit, while a person reads a claim.
- **Few real rows, one repository.** 9 of the 11 real rows come from one file's list of defaults in one repository. Precision moves a lot on a handful of rows, and the named-value rule was built with those rows in view.
- **Sampling.** Negation was sampled at 260 of 1,218 and the rest were labeled in full. A plain precision over the sample flatters the verifier, since negation is where its false flags were. The script prints a scaled figure too, and that is the one to quote.
- **New flags are unlabeled.** The current rules flag pairs the old ones did not, such as `max_steps: int = 500`. The count of flagged pairs without a label is printed for that reason. They were read during this work, not labeled.
- **Luck in the labels.** Some real rows were right by accident: the memory line is stale, but the quoted source does not show it (a line about another setting, right next to the one that matters). A verifier that gets these "right" is not better. The row reasons were read while tuning, and such rows are named below when lost.
- **Two labelers can share a blind spot.** 411 of 413 agreements between two models is not the same as 411 correct.
- **Repositories change.** This is a snapshot at the labeled commits, and a line that was stale then may be fixed now.

## Results

Measured on 2026-11-05, with the rules described in [the rules page](../../../docs/lexical-rules.md).

| | Before | Now |
|---|---|---|
| Flagged pairs in full audits | 1,719 | 47 (down 97.3%) |
| Flagged claims in full audits | 794 | 29 |
| Of the 1,371 pairs the labels came from, still flagged | 1,371 | 24 (down 98.2%) |
| Labeled pairs flagged | 413 | 16 |
| Real rows kept | 11 of 11 | 9 of 11 |
| Precision on the labeled sample | 0.03 | 0.56 |
| Precision scaled for the negation sample | 0.01 | 0.33 |

By rule at labeling time, now:

| Rule when labeled | Labeled | Real | Flagged now | Real kept | Not real kept | Precision |
|---|---|---|---|---|---|---|
| negation | 260 | 0 | 3 | 0 | 3 | 0.00 |
| numbers | 70 | 10 | 9 | 8 | 1 | 0.89 |
| opposing | 83 | 1 | 4 | 1 | 3 | 0.25 |
| all | 413 | 11 | 16 | 9 | 7 | 0.56 |

The two real rows lost are both pairs where the quoted source does not show the problem: one quotes the setting next to the one that disagrees (that claim is still flagged against the right line), and one quotes a list item whose heading, on an earlier line, says the items were removed (that claim is still flagged against another line). Every memory line behind a real row is still flagged.

The full audits cover the 27 agent files of the 15 repositories, each against its own repository, and show at most three passages per claim, before and after. `run.py` audits the 18 files that hold a labeled row, so its own count is lower. The reports from before this work hold 1,719 pairs and 794 flagged claims. Those are 26 of the 27 files: grafana's `AGENTS.md` timed out then, and it adds no flag now. The 1,371 pairs the labels came from are fewer, because they were extracted before ruff's `AGENTS.md` finished, and so leave out 368 of its pairs. Of the 47 pairs now flagged, 15 have a label (8 real: two real rows were labeled on two pieces of one sentence, and now name one pair), and 32 do not: 8 were among the 1,371, counting one whose quote has since been joined with its wrapped line, and 24 are new. By reason: `same setting` 19, `same subject` 19, `set differently` 3, `opposing terms` 3, `different values` 2, removal 1. The new pairs were read but not labeled. Most `same setting` ones point at the declaration the memory line disagrees with (`max_failures: int = 5`), which is the recall the old rules missed, and those are not counted in the 9 real rows above.

A review of these rules brought back a few things the first pass over the labels had taken away: a short statement against its opposite, a line opening with `Do not` against its near opposite, and documents under `tests/`, `spec/`, `testing/` or `e2e/`. On these repositories that costs one new flag and two new internal conflicts, none of them real on reading: `no network calls allowed` against `Network call blocking`, in a document under `testing/`, and in mem0's two copies of one file `Commit .env files, API keys, or credentials.`, an item of a "do not" list, against `Never commit secrets`. It also took away three `same setting` flags: in code, `optimizeDeps: { noDiscovery: true }` at a call site is one object's field and no declared value. The labeled table and the synthetic scores did not move.

Reading wrapped evidence whole has a price and a gain. It adds one flagged row, not real, and costs 0.04 of precision on the sample, where the rows are few. Against that, a sentence wrapped in a memory line and wrapped again in a document now matches. Over the 25 agent files audited both before and after the change to how text is read (grafana's two timed out before it), claims went from 4,463 to 3,704, `NO EVIDENCE` from 3,474 to 2,966, `POSSIBLE SUPPORT` from 946 to 696 and `POSSIBLE COUNTEREVIDENCE` from 43 to 42. Most of the lost support was a fragment that matched by chance (`get closed.`); the labels say nothing about that part.

The negation rule stays because synthetic cases state things like "deploys are never allowed on Fridays" and it finds them, but on these repositories it has no real row among the 260 sampled, and of the 19 flags it still gives none read as real. It did not reach usable precision here. Treat a `same subject, but only one side is negated` finding as weak; it ranks last in a report.
