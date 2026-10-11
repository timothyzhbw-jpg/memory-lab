"""MemFalsify: evidence-grounded auditing and active falsification of agent memory."""

__version__ = "0.3.0"

from .audit import (
    ATTENTION,
    AuditReport,
    EvidenceFinding,
    Finding,
    Issue,
    Proposal,
    Review,
    Status,
    audit,
    audit_claims,
    load_reviews,
)
from .corpus import (
    Skipped,
)
from .gitstore import (
    GitError,
    HistoryError,
    resolve_commit,
)
from .history import (
    Change,
    ClaimChange,
    ClaimHistory,
    ClaimVersion,
    CommitInfo,
    DiffReport,
    HistoryReport,
    RemovedClaim,
    Revision,
    compare,
    diff_to_markdown,
    history_to_markdown,
    memory_diff,
    memory_history,
)
from .jev import (
    JevVerifier,
)
from .llm import (
    LLMVerifier,
)
from .memory import (
    Claim,
    claim_id,
    parse_memory,
    parse_text,
)
from .report import (
    to_markdown,
)
from .rollback import (
    Rollback,
    rollback_patch,
)
from .verify import (
    CascadeVerifier,
    DualVerifier,
    Judgment,
    LexicalVerifier,
    Relation,
    Verifier,
    VerifierError,
)

__all__ = [
    "ATTENTION",
    "AuditReport",
    "CascadeVerifier",
    "Change",
    "Claim",
    "ClaimChange",
    "ClaimHistory",
    "ClaimVersion",
    "CommitInfo",
    "DiffReport",
    "DualVerifier",
    "EvidenceFinding",
    "Finding",
    "GitError",
    "HistoryError",
    "HistoryReport",
    "Issue",
    "JevVerifier",
    "Judgment",
    "LLMVerifier",
    "LexicalVerifier",
    "Proposal",
    "Relation",
    "RemovedClaim",
    "Review",
    "Revision",
    "Rollback",
    "Skipped",
    "Status",
    "Verifier",
    "VerifierError",
    "__version__",
    "audit",
    "audit_claims",
    "claim_id",
    "compare",
    "diff_to_markdown",
    "history_to_markdown",
    "load_reviews",
    "memory_diff",
    "memory_history",
    "parse_memory",
    "parse_text",
    "resolve_commit",
    "rollback_patch",
    "to_markdown",
]
