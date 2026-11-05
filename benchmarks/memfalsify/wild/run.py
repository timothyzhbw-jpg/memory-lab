"""MemFalsify on real repositories: the lexical verifier against pairs labeled in public projects' agent files.

    python benchmarks/memfalsify/wild/run.py --check
    python benchmarks/memfalsify/wild/run.py --clones DIR [--no-audit] [--json OUT]

``labels.jsonl`` holds metadata and hashes only, so the texts come from clones at the labeled commits
(``DIR/<owner>__<name>``). The clones are untrusted data: this script only reads their files, and it runs nothing
from them, not even git. Run it from this repository, with the clones elsewhere. README.md next to this file says how
the labels were made and how they can mislead.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal, Optional

ROOT = Path(__file__).resolve().parents[3]
for _package in ("memcore", "memfalsify"):  # measure this checkout, not whatever copy is installed
    if (_src := str(ROOT / "packages" / _package / "src")) not in sys.path:
        sys.path.insert(0, _src)

import typer  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from memcore.display import report_text  # noqa: E402
from memfalsify import LexicalVerifier, audit, parse_memory  # noqa: E402
from memfalsify.audit import weighed  # noqa: E402
from memfalsify.corpus import MAX_LINE, as_read, left_out_reason  # noqa: E402
from memfalsify.text import sentence_spans  # noqa: E402

LABELS = Path(__file__).resolve().parent / "labels.jsonl"
# Pairs the rules found when the sample was drawn. Every opposing and numbers pair was labeled, negation was capped.
POPULATION = {"negation": 1218, "opposing": 83, "numbers": 70}
HEX64 = r"^[0-9a-f]{64}$"
Why = Literal["real", "different_subject", "negation_out_of_scope", "different_quantity", "opposing_unrelated",
              "example_or_fixture", "historical_note", "compatible_wording", "table_or_markup", "other"]
Tier = Literal["agent_file", "code", "docs", "config", "changelog", "test", "example", "other"]


class Row(BaseModel):
    """One labeled (claim, source passage) pair. No field holds third-party text except file names."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[0-9a-f]{16}$")
    repo: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    memory_file: str = Field(min_length=1)
    location: str = Field(pattern=r":\d+$")
    source_path: str = Field(min_length=1)
    source_line: Optional[int] = Field(ge=1)
    claim_sha256: str = Field(pattern=HEX64)
    quote_sha256: str = Field(pattern=HEX64)
    rule: Literal["negation", "opposing", "numbers"]
    label: Literal["real", "not_real"]
    why: Why
    tier: Tier
    claim_dropped: bool = False  # the labeled text is no longer read as a claim: a table header, a label, a closing tag


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_labels(path: Path = LABELS) -> list[Row]:
    rows = [Row.model_validate_json(line) for line in Path(path).read_bytes().decode("utf-8").splitlines() if line]
    ids = Counter(row.id for row in rows)
    if repeated := [key for key, count in ids.items() if count > 1]:
        raise ValueError(f"repeated ids: {repeated}")
    return rows


def clone_of(clones: Path, repo: str) -> Path:
    return Path(clones) / repo.replace("/", "__")


def head(clone: Path) -> str | None:
    """The clone's commit, read from its files: git is not run in an untrusted clone."""
    git = clone / ".git"
    try:
        current = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not current.startswith("ref: "):
            return current
        ref = current[5:]
        if (git / ref).is_file():
            return (git / ref).read_text(encoding="utf-8").strip()
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ")[0]
    except OSError:
        pass
    return None


def _quote(clone: Path, row: Row) -> tuple[str | None, int]:
    """The labeled passage, found by its hash on the labeled line, and the length of that line."""
    try:
        text = (clone / row.source_path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None, 0
    lines = text.split("\n")
    width = len(lines[row.source_line - 1]) if row.source_line and row.source_line <= len(lines) else 0
    for start, end in sentence_spans(as_read(row.source_path, text)):
        if sha256(text[start:end]) == row.quote_sha256:
            return text[start:end], width
    return None, width


def score_labeled(rows: list[Row], clones: Path) -> dict:
    """Flagged, real and not_real per labeling-time rule, for the current code: the rules, then the file's kind, with
    paths and long lines left out as a directory scan leaves them out."""
    verifier = LexicalVerifier()
    claims: dict[tuple[str, str], dict[str, str]] = {}
    by_rule: dict[str, Counter] = defaultdict(Counter)
    lost, kept, unresolved = [], [], []
    for row in rows:
        clone = clone_of(clones, row.repo)
        if (row.repo, row.memory_file) not in claims:
            found = parse_memory(clone / row.memory_file)[0] if (clone / row.memory_file).is_file() else []
            claims[row.repo, row.memory_file] = {sha256(claim.text): claim.text for claim in found}
        claim = claims[row.repo, row.memory_file].get(row.claim_sha256)
        quote, width = _quote(clone, row)
        count = by_rule[row.rule]
        count["labeled"] += 1
        count["labeled_real"] += row.label == "real"
        if row.claim_dropped:  # nothing to flag: it counts like any pair that is no longer flagged
            lost += [row] if row.label == "real" else []
            count["no_longer_claims"] += 1
            continue
        if claim is None or (quote is None and width <= MAX_LINE and not left_out_reason(row.source_path)):
            unresolved.append(row.id)
            continue
        flagged = False
        if quote is not None and width <= MAX_LINE and not left_out_reason(row.source_path):
            flagged = weighed(verifier.judge(claim, quote), row.source_path, claim, quote).relation == "contradicts"
        if flagged:
            count["flagged"] += 1
            count["real" if row.label == "real" else "not_real"] += 1
            kept.append(row.id)
        elif row.label == "real":
            lost.append(row)
    totals = sum(by_rule.values(), Counter())
    weight = {rule: POPULATION.get(rule, count["labeled"]) / count["labeled"] for rule, count in by_rule.items()}
    scaled_flagged = sum(weight[rule] * count["flagged"] for rule, count in by_rule.items())
    return {"by_rule": {rule: dict(by_rule[rule]) for rule in sorted(by_rule)}, "total": dict(totals),
            "precision": totals["real"] / totals["flagged"] if totals["flagged"] else None,
            "scaled_precision": (sum(weight[rule] * count["real"] for rule, count in by_rule.items()) / scaled_flagged
                                 if scaled_flagged else None),
            "kept": kept, "real_lost": [row.model_dump() for row in lost], "unresolved": unresolved}


def flagged_pairs(rows: list[Row], clones: Path) -> dict:
    """Full audits of the labeled memory files: contradicting evidence the report shows, with and without a label."""
    labeled = {(row.repo, row.claim_sha256, row.source_path, row.quote_sha256) for row in rows}
    seen: set[tuple[str, str, str, str]] = set()
    claims: dict[str, set[str]] = defaultdict(set)
    for repo, memory in sorted({(row.repo, row.memory_file) for row in rows}):
        clone = clone_of(clones, repo)
        if not (clone / memory).is_file():
            continue
        report = audit(clone / memory, clone)
        for finding in report.findings:
            for evidence in finding.evidence:
                if evidence.relation == "contradicts":
                    seen.add((repo, sha256(finding.text), evidence.span.path, sha256(evidence.span.quote)))
                    claims[repo].add(finding.text)
    return {"flagged_pairs": len(seen), "with_a_label": len(seen & labeled), "without_a_label": len(seen - labeled),
            "flagged_claims": {repo: len(texts) for repo, texts in sorted(claims.items())}}


def to_markdown(rows: list[Row], result: dict, audits: dict | None, stale: list[str]) -> str:
    out = ["# MemFalsify on real repositories", "",
           f"Labeled pairs: {len(rows)} · commits that differ from the labeled ones: {len(stale)} · "
           f"pairs not found in the clones: {len(result['unresolved'])} · "
           f"pairs whose text is no longer a claim: {result['total'].get('no_longer_claims', 0)}", "",
           "| Rule when labeled | Labeled | Real | Flagged now | Real kept | Not real kept | Precision |",
           "|---|---|---|---|---|---|---|"]
    for rule, count in [*result["by_rule"].items(), ("all", result["total"])]:
        flagged = count.get("flagged", 0)
        out.append(f"| {rule} | {count.get('labeled', 0)} | {count.get('labeled_real', 0)} | {flagged} | "
                   f"{count.get('real', 0)} | {count.get('not_real', 0)} | "
                   f"{count.get('real', 0) / flagged if flagged else 0:.2f} |")
    if result["scaled_precision"] is not None:
        out += ["", f"Scaled to all the pairs each rule found when the sample was drawn, since negation was sampled: "
                    f"precision {result['scaled_precision']:.2f}."]
    out += ["", f"Real rows lost: {len(result['real_lost'])}"]
    out += [f"- `{row['id']}` {row['repo']} {report_text(row['location'])} against "
            f"{report_text(row['source_path'])}:{row['source_line']}" for row in result["real_lost"]]
    if audits is not None:
        out += ["", f"Full audits: {audits['flagged_pairs']} flagged pairs, {audits['with_a_label']} with a label, "
                    f"{audits['without_a_label']} without one."]
        out += [f"- {repo}: {count} flagged claims" for repo, count in audits["flagged_claims"].items()]
    return "\n".join(out) + "\n"


app = typer.Typer(add_completion=False)


@app.command()
def main(clones: Optional[Path] = typer.Option(None, help="Directory holding <owner>__<name> clones at the labeled commits"),
         check: bool = typer.Option(False, help="Validate labels.jsonl and stop; needs no clones"),
         no_audit: bool = typer.Option(False, "--no-audit", help="Score the labeled pairs only, without the full audits"),
         labels: Path = typer.Option(LABELS, help="The labels file"),
         json_out: Optional[Path] = typer.Option(None, "--json", help="Also write every number as JSON")) -> None:
    rows = load_labels(labels)
    if check:
        real = sum(row.label == "real" for row in rows)
        typer.echo(f"{len(rows)} rows, {real} real, {len({row.repo for row in rows})} repositories")
        return
    if clones is None:
        typer.echo("--clones is required unless --check is given", err=True)
        raise typer.Exit(2)
    commits = {row.repo: row.sha for row in rows}
    stale = sorted(repo for repo, sha in commits.items() if head(clone_of(clones, repo)) != sha)
    result = score_labeled(rows, clones)
    audits = None if no_audit else flagged_pairs(rows, clones)
    typer.echo(to_markdown(rows, result, audits, stale).encode("utf-8"), nl=False)
    if json_out is not None:
        payload = {"labeled": result, "audits": audits, "stale_commits": stale}
        json_out.write_bytes(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8") + b"\n")


if __name__ == "__main__":
    app()
