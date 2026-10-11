"""Verified Organizational Memory: authority-aware, evidence-backed decision state."""

__version__ = "0.3.0"

from .audit import (
    AuditFinding,
    AuditReport,
    CitedEvent,
    audit,
)
from .github_apply import (
    Summary,
    run_convert,
    run_sync,
)
from .github_fetch import (
    Client,
    FetchProgress,
    GitHubError,
    PersistentTransport,
    Stopped,
    Transport,
    token_from_env,
    urllib_transport,
)
from .github_map import (
    Capture,
    GithubConfig,
    Plan,
    convert,
)
from .models import (
    Action,
    Conflict,
    Decision,
    Event,
    PendingChange,
    Policy,
    ReplayResult,
    Risk,
    SubjectState,
    TrailEntry,
)
from .replay import (
    Dataset,
    load_events,
    replay,
    validate,
)
from .report import (
    audit_markdown,
    explain,
    to_markdown,
)

__all__ = [
    "Action",
    "AuditFinding",
    "AuditReport",
    "Capture",
    "CitedEvent",
    "Client",
    "Conflict",
    "Dataset",
    "Decision",
    "Event",
    "FetchProgress",
    "GitHubError",
    "GithubConfig",
    "PendingChange",
    "PersistentTransport",
    "Plan",
    "Policy",
    "ReplayResult",
    "Risk",
    "Stopped",
    "SubjectState",
    "Summary",
    "TrailEntry",
    "Transport",
    "__version__",
    "audit",
    "audit_markdown",
    "convert",
    "explain",
    "load_events",
    "replay",
    "run_convert",
    "run_sync",
    "to_markdown",
    "token_from_env",
    "urllib_transport",
    "validate",
]
