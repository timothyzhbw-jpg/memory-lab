"""One exclusion policy for audit inputs, before and after symlink resolution."""
from pathlib import Path


SKIP_DIRS = frozenset({"node_modules", "__pycache__", "venv"})


class InputPathError(ValueError):
    """An input must be skipped; the message is a content-free reason."""


def component_skip_reason(relative: Path, *, directory: bool = False) -> str | None:
    """Check root-relative components, never the root's own name or ancestors."""
    if any(part.startswith(".") and part not in (".", "..") for part in relative.parts):
        return "hidden component"
    parts = relative.parts if directory else relative.parts[:-1]
    if any(part in SKIP_DIRS for part in parts):
        return "skipped directory"
    return None


def input_path(root: Path, path: Path, *, directory: bool = False) -> Path:
    """Resolve an input inside root, excluding hidden/skipped walked AND target components.

    Callers keep the walked path for reporting. The returned target must be opened without
    following new symlinks so a swap after this check cannot bypass the policy.
    """
    root = Path(root).resolve()
    walked = Path(path).absolute()
    try:
        relative = walked.relative_to(root)
    except ValueError:
        raise InputPathError("path escapes root") from None
    if reason := component_skip_reason(relative, directory=directory):
        raise InputPathError(reason + " in walked path")
    try:
        resolved = walked.resolve()
    except (OSError, RuntimeError):
        if not walked.is_symlink():
            raise
        raise InputPathError("broken or cyclic symlink") from None
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        raise InputPathError("path escapes root") from None
    if reason := component_skip_reason(relative, directory=directory):
        raise InputPathError(reason + " in resolved path")
    return resolved
