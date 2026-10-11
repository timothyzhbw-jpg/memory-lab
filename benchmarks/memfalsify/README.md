# MemFalsify benchmark

Labeled cases for measuring how well each `memfalsify` verifier judges remembered claims against evidence. Each case is a `MEMORY.md`, an evidence directory and one label per claim. `run.py` audits every case the way `memfalsify audit` would and scores the resulting statuses against the labels.

The [lexical results](RESULTS.md) record the offline lexical baseline. The original eight cases were labeled after independent audits and lead adjudication. The three cases added later received an [independent Codex audit](../../AUDIT_CODEX.md) of all 63 claims, with judgments made from the evidence before opening their labels; RESULTS.md identifies the cases.

It answers four questions per verifier (`lexical`, `llm`, `jev`, `dual`, `cascade`):

- Does it **flag the claims the evidence contradicts**, without flagging claims that are fine?
- Does it **find support** for the claims the evidence supports?
- Do the wording checks (**issues** such as `overgeneralization`) fire where they should?
- **What does it cost**, in verifier calls?

## Running it

From the repository root:

```bash
python benchmarks/memfalsify/run.py                                   # lexical: offline, no dependencies
python benchmarks/memfalsify/run.py --verifier llm                    # Claude
python benchmarks/memfalsify/run.py --verifier jev                    # TypeSafe AI's Jev
python benchmarks/memfalsify/run.py --verifier dual                   # Claude and Jev, disagreements become disputed
python benchmarks/memfalsify/run.py --verifier cascade --accept-at 0.8
python benchmarks/memfalsify/run.py --case <case-id> --case <other-id> --json results.json
python benchmarks/memfalsify/run.py --check                           # validate labels only; calls no verifier
```

`run.py` imports `memfalsify` from `packages/` in this checkout, so it measures the code you have, not an installed copy. It still needs the dependencies installed (`pip install -e packages/memcore -e packages/memfalsify`), and the model verifiers need their extras and credentials:

| `--verifier` | Install | Credentials and network |
|---|---|---|
| `lexical` | nothing extra | none |
| `llm` | `pip install -e 'packages/memfalsify[llm]'` | Anthropic credentials (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or an `ant auth login` profile) |
| `jev` | `pip install -e 'packages/memfalsify[jev]'` | `TYPESAFE_API_KEY`, and network access to `api.typesafe.ai` |
| `dual`, `cascade` | `pip install -e 'packages/memfalsify[llm,jev]'` | both of the above |

Pass credentials through the environment (for example `export TYPESAFE_API_KEY=...` in your shell); never write them into a case or a results file. The model verifiers send every claim and the pre-selected evidence passages of the selected cases to the Anthropic and/or TypeSafe API. A missing SDK, missing credentials or an API failure stops the run with exit code 2 before a partial report is printed.

| Option | Default | Meaning |
|---|---|---|
| `--verifier` | `lexical` | Which verifier to score |
| `--llm-model`, `--jev-model` | as in `memfalsify audit` | Model for the `llm` / `jev` side |
| `--accept-at` | 0.8 | `cascade`: accept Jev at this confidence, otherwise ask Claude |
| `--case ID` | all cases | Run only these cases (repeatable) |
| `--json OUT` | none | Also write every number and every per-claim prediction as JSON |
| `--min-coverage`, `--max-judged` | as in `memfalsify audit` | Passage pre-selection: 0.5 and unbounded for `lexical`, 0.2 and 8 for the model verifiers |
| `--cases-dir` | `benchmarks/memfalsify/cases` | Where the cases live |
| `--check` | off | Validate the labels and stop |

Each case is audited with `audit()` and the CLI's defaults for the chosen verifier (`memfalsify.cli.build_verifier` and `memfalsify.cli.preselection`), with the case's `as_of` as the reference date, the default 180-day staleness window, linked `.md` files followed, and **no reviews**. The markdown report goes to stdout; the exit code is 0 whatever the scores are.

## Case format

```
benchmarks/memfalsify/cases/<case-id>/
  MEMORY.md       the memory under audit
  evidence/...    UTF-8 text evidence: docs, decision logs, meeting notes, configs, code
  labels.jsonl    one label per claim
  case.json       {"as_of": "YYYY-MM-DD", "description": "..."}
```

The claims are exactly what `memfalsify claims MEMORY.md` lists: one per bullet or sentence, deduplicated by normalized text. `labels.jsonl` has exactly one line per claim, matched by exact `text`:

```json
{"text": "The public API rate limit is 100 requests per minute.", "truth": "contradicted",
 "issues": [], "tags": ["numeric"],
 "evidence": "docs/api.md: The public API rate limit is 500 requests per minute.",
 "note": "The API doc gives a different limit."}
```

(One line per label in the real file.) All six fields are required:

| Field | Values |
|---|---|
| `text` | The claim exactly as `memfalsify claims` prints it |
| `truth` | `contradicted`, `supported` or `unsupported` (below) |
| `issues` | The issues the claim really has, from the kinds `memfalsify` reports (below); `[]` for none |
| `tags` | What makes the claim hard (below); `[]` for none |
| `evidence` | `"<path relative to evidence/>: <exact quote>"` naming the decisive passage for `contradicted` and `supported`; `null` for `unsupported` |
| `note` | One line on why the label is right |

### Truth

Truth is relative to the evidence directory only, never to world knowledge:

- `contradicted`: some evidence passage states something that cannot be true together with the claim (a different value, the opposite decision, a negation, a counterexample to an absolute claim).
- `supported`: some passage states or directly entails the claim, and nothing contradicts it.
- `unsupported`: neither. Related but non-committal passages do not count as support.

A claim is never both supported and contradicted: if the evidence points both ways, rewrite the case so it does not.

### Issues

Issues are judged from the claim's wording and the other claims in the memory, independently of the evidence, using the definitions `memfalsify` applies:

| Issue | The claim has it when |
|---|---|
| `overgeneralization` | It uses absolute wording: always, never, all, every, guaranteed, 总是, 所有, … |
| `relative_time` | It uses a relative time (next week, recently, currently, 下周, 最近, …) and no explicit date |
| `possibly_stale` | Its latest explicit date is more than 180 days before the case's `as_of` |
| `inference_marker` | It is marked as an inference: probably, likely, seems, I think, 可能, … |
| `internal_conflict` | It cannot be true together with another claim of the same memory |
| `duplicate` | The same proposition appears more than once, including paraphrases (in `MEMORY.md` or its linked files) |

The labels say what is true of the claim; `memfalsify`'s detectors are heuristics (and `internal_conflict` is always lexical), so they can disagree with the labels, which is what the issue metrics measure. In particular, the duplicate detector merges only identical normalized text; semantic duplicate labels also cover different sentences stating the same fact.

### Tags

| Tag | The claim is hard because |
|---|---|
| `paraphrase` | The claim and the decisive passage say the same thing in different words |
| `numeric` | A number, version, date or quantity decides the relation |
| `antonym` | Opposite terms decide it (approved / rejected, enabled / disabled) |
| `negation` | A negation decides it (does not, never, skips, 未) |
| `negation_trap` | Negation words appear, but the passage does not contradict the claim |
| `distractor` | A similar-looking passage about something else is in the evidence |
| `temporal` | Time decides it: a later decision supersedes an earlier one, a date moved, a plan expired |
| `chinese` | The claim or evidence is in Chinese |
| `repo` | The evidence is code or configuration from a repository |
| `multi_sentence` | Deciding needs more than one sentence of evidence |

### Validation

Before any verifier is built, every selected case is checked, and all problems of all cases are reported together (exit code 2):

- `MEMORY.md`, `labels.jsonl`, `case.json` and `evidence/` exist; `case.json` has exactly `as_of` (a valid date) and `description`.
- Every label line is valid JSON with exactly the six fields and allowed values. A repeated key in `case.json` or in a label line is invalid, including inside a nested object. The error names `case.json:<line>` or `labels.jsonl:<line>`.
- Every claim is labeled exactly once; no label text is missing from the claims (the error suggests the closest claim).
- `contradicted` and `supported` labels name evidence as `path: quote`, the path stays inside `evidence/`, and the quote occurs verbatim in that file; `unsupported` labels have `"evidence": null`.

## Metrics

### Predicted relation

Each claim's audit status maps to a predicted relation:

| Status | Predicted |
|---|---|
| `candidate_counterevidence` (and `challenged`, from reviews) | `contradicted` |
| `candidate_support` (and `supported`, from reviews) | `supported` |
| `insufficient_evidence` | `unsupported` |
| `disputed` (and `conflicted`, from reviews) | `disputed` |

The benchmark passes no reviews, so `challenged`, `supported` and `conflicted` do not occur. `disputed` only comes from `dual`.

### Contradiction detection

A claim is **flagged** when its prediction is `contradicted` or `disputed`: the statuses that make `memfalsify audit --strict` fail, so a reviewer would look at it. A claim is **positive** when its label is `contradicted`.

- TP = flagged and positive, FP = flagged and not positive, FN = positive and not flagged
- precision = TP / (TP + FP), recall = TP / (TP + FN), F1 = 2·TP / (2·TP + FP + FN)

The number of `disputed` predictions is reported separately, split by label, because a disputed flag costs a human read even when it is right.

### Support detection

Positive = labeled `supported`; detected = predicted `supported` (not `disputed`). Precision, recall and F1 as above.

### 3-way accuracy and confusion matrix

Accuracy = claims whose prediction equals the label / all claims. `disputed` never equals a label, so it always counts as wrong. The confusion matrix has labels as rows and the four predictions as columns.

### Detections citing the labeled passage

For each correctly flagged contradiction and each correctly detected support: does one of the passages the report cites with that relation (`contradicts` or `disputed` for a flag, `supports` for support) overlap the labeled `evidence` quote? Reported as hits / detections per label. A detection that does not cite the labeled passage may be right for the wrong reason.

### Issue detection

Per issue kind, comparing the kinds `memfalsify` reports for a claim with the labeled `issues`: TP = labeled and reported, FP = reported but not labeled, FN = labeled but not reported; precision and recall as above, plus the labeled and reported counts.

### By tag

For each tag, over the claims carrying it:

- **contradiction recall**: flagged / labeled `contradicted`
- **support recall**: predicted `supported` / labeled `supported`
- **false flags**: flagged / labeled `supported` or `unsupported`, the error `negation_trap` and `distractor` cases are built to provoke

A claim with several tags counts under each of them.

### Cost

- **judge() calls**: how often `audit()` called the verifier (it is wrapped and counted). For `dual` each call asks both backends; for `cascade` each call asks Jev.
- **escalations** (`cascade` only): judgments that carry two opinions, i.e. calls where Jev was below `--accept-at` and Claude was asked too. Claude calls = escalations.
- **seconds**: wall-clock time for the audits.

The issue checks never call the verifier, so they cost nothing.

### Undefined values

A ratio with a zero denominator is `n/a` in the report and `null` in JSON (precision when nothing was flagged, recall when there are no positives). In the tag table, `–` means no claim with that tag has that label.

## JSON output

`--json OUT` writes:

- `verifier` (its name, including model names), `settings` (`verifier`, `min_coverage`, `max_judged`, and `accept_at` for `cascade`), `memfalsify_version`
- `cost`: `judge_calls`, `escalations`, `seconds`
- `metrics`: `contradiction`, `support`, `accuracy`, `confusion`, `issues`, `tags`, `citations`, `claims`
- `cases`: per case `id`, `description`, `as_of`, `judge_calls`, `escalations` and the same `metrics` for that case alone
- `predictions`: per claim `case`, `memory_id`, `text`, `truth`, `predicted`, `status`, `tags`, `issues` (labeled), `predicted_issues`, `cites_labeled_passage` and the cited `evidence` with each verdict's rationale and, for `dual` and `cascade`, the individual opinions (Jev's confidence included)

## Adding a case

1. Create `cases/<case-id>/` with `evidence/`, a `MEMORY.md` and a `case.json` (`as_of` decides which dated claims are `possibly_stale`).
2. List the claims exactly as the tool extracts them: `memfalsify claims cases/<case-id>/MEMORY.md`.
3. Write one `labels.jsonl` line per listed claim. Copy each `text` and each evidence quote verbatim.
4. Run `python benchmarks/memfalsify/run.py --check --case <case-id>`, then `--case <case-id>` to see how the lexical verifier does.

Label from the evidence, not from what any verifier currently outputs, and keep at least one passage per case that a careless verifier would misread (a negated distractor, a superseded decision, a paraphrase).

## Caveats

- The cases are **synthetic and small**. They are written to exercise known failure modes, so scores show how verifiers compare on those, not how often they are right on real memories. A difference of one or two claims is noise.
- The labels are the authors' judgment. A verifier that disagrees may have a point; read the misclassified claims before trusting a number.
- `lexical` is deterministic; model verifiers are not, and `jev-latest` and Claude model versions change over time. Record the model versions (`--json` keeps the rationales, which name Jev's resolved version) and repeat runs before comparing small differences.
- Passages are single sentences and pre-selected by term overlap, so every verifier misses evidence that shares no terms with the claim or needs several sentences together; `multi_sentence` and `paraphrase` measure that ceiling as much as the verifier.
