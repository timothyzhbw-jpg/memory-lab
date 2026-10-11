"""Verified Organizational Memory: authority-aware, evidence-backed decision state."""

__version__ = "0.3.0"

from .audit import (
    AuditFinding,
    AuditReport,
    CitedEvent,
    audit,
)
from .changes import (
    Change,
    ChangesReport,
    SubjectChanges,
    changes,
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
    DatedPolicy,
    Decision,
    Event,
    PendingChange,
    Policy,
    PolicyChange,
    ReplayResult,
    Risk,
    RoleChange,
    SubjectState,
    TrailEntry,
)
from .replay import (
    Dataset,
    load_events,
    load_policy_log,
    replay,
    validate,
)
from .report import (
    audit_markdown,
    changes_markdown,
    explain,
    to_markdown,
)

__all__ = [
    "Action",
    "AuditFinding",
    "AuditReport",
    "Capture",
    "Change",
    "ChangesReport",
    "CitedEvent",
    "Client",
    "Conflict",
    "Dataset",
    "DatedPolicy",
    "Decision",
    "Event",
    "FetchProgress",
    "GitHubError",
    "GithubConfig",
    "PendingChange",
    "PersistentTransport",
    "Plan",
    "Policy",
    "PolicyChange",
    "ReplayResult",
    "Risk",
    "RoleChange",
    "Stopped",
    "SubjectChanges",
    "SubjectState",
    "Summary",
    "TrailEntry",
    "Transport",
    "__version__",
    "audit",
    "audit_markdown",
    "changes",
    "changes_markdown",
    "convert",
    "explain",
    "load_events",
    "load_policy_log",
    "replay",
    "run_convert",
    "run_sync",
    "to_markdown",
    "token_from_env",
    "urllib_transport",
    "validate",
]
