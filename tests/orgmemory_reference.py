"""Original replay, audit and report algorithms, kept so scale optimizations can be diffed.

These functions are the pre-optimization behavior: verbatim copies of the library modules as they were
just before the replay-perf optimizations were merged (``orgmemory_reference_frozen``), so they include
effective-dated roles and every other merged behavior. Tests compare them to the library on seeded
datasets. Do not "fix" them to match a faster implementation.
"""
from __future__ import annotations

from orgmemory_reference_frozen import audit as _audit
from orgmemory_reference_frozen import replay as _replay
from orgmemory_reference_frozen import report as _report

reference_replay = _replay.replay
reference_audit = _audit.audit
reference_explain = _report.explain
reference_to_markdown = _report.to_markdown
reference_audit_markdown = _report.audit_markdown
reference_one_line = _report._one_line
reference_safe = _report._safe
reference_audit_code = _report._audit_code
