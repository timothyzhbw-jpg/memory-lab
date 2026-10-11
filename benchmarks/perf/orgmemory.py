"""Time ``orgmemory`` validate, replay, explain and audit on synthetic datasets.

    python benchmarks/perf/orgmemory.py [--sizes 1000,10000,100000] [--seed 20261011]
    python benchmarks/perf/orgmemory.py --json /tmp/orgmemory-perf.json

The generator is deterministic for a given seed and size. Each command is timed
the way the CLI runs it: load the dataset, do the work, and build the default
text report. Peak memory is the tracemalloc high-water mark for that call, so
the timings include tracemalloc's overhead. Dataset generation is not included.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# This file is named orgmemory.py, so its directory must not stay on sys.path
# ahead of the real package.
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != Path(_SCRIPT_DIR)]
for _package in ("memcore", "orgmemory"):
    _src = str(ROOT / "packages" / _package / "src")
    if _src not in sys.path:
        sys.path.insert(0, _src)

from memcore import sha256_bytes  # noqa: E402
from orgmemory.audit import audit  # noqa: E402
from orgmemory.replay import Dataset, replay  # noqa: E402
from orgmemory.report import audit_markdown, explain, to_markdown  # noqa: E402

SEED = 20261011
ACTIONS = (
    ("request", 18),
    ("comment", 14),
    ("raise_risk", 10),
    ("dependency", 12),
    ("approve", 14),
    ("reject", 10),
    ("reconsider", 12),
    ("revoke", 10),
)
ACTION_TOTAL = sum(weight for _, weight in ACTIONS)
ACTORS = (
    ("ada", ("product_manager",)),
    ("ben", ("engineer",)),
    ("cy", ("engineer",)),
    ("dee", ("account_manager",)),
    ("eli", ("customer",)),
    ("fay", ("customer",)),
    ("gus", ("legal",)),
)
OUTSIDER = "mallory"
PERMISSIONS = {
    "request": ["customer", "account_manager", "engineer", "product_manager"],
    "reconsider": ["customer", "account_manager", "engineer", "product_manager"],
    "raise_risk": ["engineer", "product_manager"],
    "dependency": ["engineer", "product_manager"],
    "comment": ["customer", "account_manager", "engineer", "product_manager", "legal"],
    "approve": ["product_manager"],
    "reject": ["product_manager"],
    "revoke": ["product_manager"],
}
NOTES = (
    "export reads a schema that is mid-migration",
    "quota can be exceeded on the batch path",
    "rollback needs a manual step",
    "backup window overlaps the cutover",
)
EXPLAIN_SUBJECT = "s0000"
CAPTURED_AT = "2026-01-05T09:00:00Z"


def layout(events: int) -> tuple[int, int, int, int]:
    """Return (subjects, files, spans per file, padding lines) for ``events``."""
    subjects = max(40, events // 30)
    files = max(4, min(100, events // 1000))
    spans = 20 if events < 5000 else 50
    # ~70 bytes per padding line, so repeated hashing has a real file to read.
    padding = 120 if events < 5000 else 480
    return subjects, files, spans, padding


def _choose_action(rng, has_prior: bool) -> str:
    pick = rng.randrange(ACTION_TOTAL)
    upto = 0
    chosen = ACTIONS[0][0]
    for action, weight in ACTIONS:
        upto += weight
        if pick < upto:
            chosen = action
            break
    if chosen == "revoke" and not has_prior:
        return "request"
    return chosen


def _choose_actor(rng, action: str) -> str:
    allowed_roles = set(PERMISSIONS[action])
    permitted = [name for name, roles in ACTORS if allowed_roles.intersection(roles)]
    pool = [name for name, _roles in ACTORS]
    pool.append(OUTSIDER)
    if permitted and rng.randrange(100) < 72:
        return rng.choice(permitted)
    return rng.choice(pool)


def _choose_subject(rng, subjects: int) -> int:
    # Half uniform, half skewed toward low indexes, so a few trails are long.
    if rng.randrange(2) == 0:
        return rng.randrange(subjects)
    return min(subjects - 1, int(rng.random() * rng.random() * subjects))


def _source_file(file_index: int, spans: int, padding: int) -> tuple[bytes, list[dict]]:
    lines = [f"pad {file_index:04d} {n:04d} {'x' * 48}" for n in range(padding)]
    quotes = [f"quote {file_index:04d} {n:04d} shared evidence line" for n in range(spans)]
    lines.extend(quotes)
    text = "\n".join(lines) + "\n"
    raw = text.encode("utf-8")
    digest = sha256_bytes(raw)
    relative = f"sources/f{file_index:04d}.txt"
    records = []
    for number, quote in enumerate(quotes):
        start = text.index(quote)
        records.append({
            "id": f"v{file_index:04d}-{number:04d}",
            "path": relative,
            "sha256": digest,
            "quote": quote,
            "start": start,
            "end": start + len(quote),
            "source_uri": f"synthetic://perf/f{file_index:04d}.txt#{number}",
            "source_kind": "note",
            "captured_at": CAPTURED_AT,
        })
    return raw, records


def generate(directory: Path, events: int, seed: int) -> dict:
    """Write a dataset under ``directory``. Returns counts, not timings."""
    import random

    subjects, files, spans, padding = layout(events)
    rng = random.Random(seed + events)
    evidence_ids: list[str] = []
    evidence_lines: list[str] = []
    sources = directory / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    evidence_bytes = 0
    for file_index in range(files):
        raw, records = _source_file(file_index, spans, padding)
        (sources / f"f{file_index:04d}.txt").write_bytes(raw)
        evidence_bytes += len(raw)
        for record in records:
            evidence_ids.append(record["id"])
            evidence_lines.append(json.dumps(record, ensure_ascii=False))
    (directory / "evidence.jsonl").write_bytes(("\n".join(evidence_lines) + "\n").encode("utf-8"))

    policy = {
        "actors": {name: list(roles) for name, roles in ACTORS},
        "permissions": PERMISSIONS,
    }
    (directory / "policy.json").write_bytes((json.dumps(policy, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    clock = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    prior: list[str] = []
    seen_subjects: set[str] = set()
    event_lines: list[str] = []
    for index in range(events):
        if index == 0:
            action, actor, subject_index = "request", "eli", 0
        else:
            action = _choose_action(rng, bool(prior))
            actor = _choose_actor(rng, action)
            subject_index = _choose_subject(rng, subjects)
        subject = f"s{subject_index:04d}"
        seen_subjects.add(subject)
        event_id = f"e{index:06d}"
        cited = [evidence_ids[(index * 3 + rng.randrange(len(evidence_ids))) % len(evidence_ids)]]
        if rng.randrange(4) == 0:
            extra = evidence_ids[rng.randrange(len(evidence_ids))]
            if extra not in cited:
                cited.append(extra)
        record = {
            "id": event_id,
            "timestamp": clock.isoformat(),
            "actor": actor,
            "action": action,
            "subject": subject,
            "evidence_ids": cited,
        }
        if action == "raise_risk":
            record["note"] = NOTES[rng.randrange(len(NOTES))]
        elif action == "dependency":
            if rng.randrange(5) == 0:
                record["dependency"] = f"external-{rng.randrange(8)}"
            else:
                record["dependency"] = f"s{rng.randrange(subjects):04d}"
        elif action == "revoke":
            record["supersedes"] = prior[rng.randrange(len(prior))]
        event_lines.append(json.dumps(record, ensure_ascii=False))
        prior.append(event_id)
        if rng.randrange(20) != 0:
            clock += timedelta(minutes=1 + rng.randrange(120))
    (directory / "events.jsonl").write_bytes(("\n".join(event_lines) + "\n").encode("utf-8"))
    return {
        "events": events,
        "subjects": len(seen_subjects),
        "subject_slots": subjects,
        "evidence": len(evidence_ids),
        "files": files,
        "evidence_bytes": evidence_bytes,
        "explain_subject": EXPLAIN_SUBJECT,
    }


def _measure(fn) -> tuple[float, int, int]:
    tracemalloc.start()
    started = time.perf_counter()
    output_bytes = fn()
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return elapsed, peak, output_bytes


def _validate(directory: Path) -> int:
    data = Dataset.load(directory)
    unknown = sorted({event.actor for event in data.events} - data.policy.actors.keys())
    text = f"ok: {len(data.events)} events, {len(data.evidence)} evidence records"
    if unknown:
        text += f"\nnote: actors without any role (their events cannot change state): {', '.join(unknown)}"
    if not text.startswith("ok:"):
        raise RuntimeError(text)
    return len(text.encode("utf-8"))


def _replay(directory: Path) -> int:
    text = to_markdown(replay(Dataset.load(directory)))
    if not text.startswith("# Organizational memory replay\n"):
        raise RuntimeError("replay did not render")
    return len(text.encode("utf-8"))


def _explain(directory: Path) -> int:
    text = explain(replay(Dataset.load(directory)), EXPLAIN_SUBJECT)
    if not text.startswith(f"## {EXPLAIN_SUBJECT}\n"):
        raise RuntimeError("explain did not render")
    return len(text.encode("utf-8"))


def _audit(directory: Path) -> int:
    text = audit_markdown(audit(Dataset.load(directory)))
    if not text.startswith("# Organizational decision audit\n"):
        raise RuntimeError("audit did not render")
    return len(text.encode("utf-8"))


COMMANDS = (
    ("validate", _validate),
    ("replay", _replay),
    ("explain", _explain),
    ("audit", _audit),
)


def run_size(events: int, seed: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="orgmemory-perf-") as tmp:
        directory = Path(tmp)
        generated_at = time.perf_counter()
        meta = generate(directory, events, seed)
        meta["generate_seconds"] = time.perf_counter() - generated_at
        measurements = {}
        for name, fn in COMMANDS:
            print(f"timing {name} events={events}", flush=True)
            elapsed, peak, output_bytes = _measure(lambda fn=fn: fn(directory))
            measurements[name] = {"seconds": elapsed, "peak_bytes": peak, "output_bytes": output_bytes}
        meta["commands"] = measurements
        return meta


def _format_table(rows: list[dict]) -> str:
    lines = [
        f"python {platform.python_version()}  {platform.platform()}",
        "timings include dataset load, the command's work, and the default text report;",
        "peak is tracemalloc's high-water mark (allocator overhead included).",
        "",
        f"{'events':>8} {'command':<10} {'seconds':>10} {'peak_mib':>10} {'output_kib':>10}",
    ]
    for row in rows:
        for name, _fn in COMMANDS:
            item = row["commands"][name]
            lines.append(
                f"{row['events']:8d} {name:<10} {item['seconds']:10.3f} "
                f"{item['peak_bytes'] / (1024 * 1024):10.1f} {item['output_bytes'] / 1024:10.1f}"
            )
        lines.append(
            f"{'':8} {'subjects':<10} {row['subjects']:10d} {'evidence':>10} {row['evidence']:10d} "
            f"files={row['files']} evidence_bytes={row['evidence_bytes']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Time orgmemory on synthetic datasets.")
    parser.add_argument("--sizes", default="1000,10000,100000", help="Comma-separated event counts")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--json", type=Path, help="Write the machine-readable results to this path")
    args = parser.parse_args(argv)
    sizes = [int(part) for part in args.sizes.split(",") if part.strip()]
    if any(size < 1 for size in sizes):
        raise SystemExit("sizes must be positive")
    rows = [run_size(size, args.seed) for size in sizes]
    report = {
        "seed": args.seed,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "tracemalloc": True,
        "runs": rows,
    }
    print(_format_table(rows), flush=True)
    if args.json is not None:
        args.json.write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
        print(f"wrote {args.json}", flush=True)


if __name__ == "__main__":
    main()
