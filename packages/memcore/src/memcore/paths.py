"""One exclusion policy for audit inputs, before and after symlink resolution."""
from pathlib import Path, PurePath


SKIP_DIRS = frozenset({"node_modules", "__pycache__", "venv"})

# Agent instruction files that agents document under a hidden directory or as a hidden name. A memory reader
# passes ``instructions=True`` to accept exactly these, in the walked and the resolved path alike; evidence
# scans never do, so they are never evidence, and ``.git/config`` or ``.env`` stay excluded everywhere.
INSTRUCTION_NAMES = (".cursorrules", ".windsurfrules")
# (hidden directory chain, exact names, name suffix, whether non-hidden subdirectories may follow the chain)
INSTRUCTION_DIRS = (
    ((".claude",), ("CLAUDE.md", "AGENTS.md"), None, False),
    ((".claude", "rules"), (), ".md", True),
    ((".cursor", "rules"), (), ".mdc", True),
    ((".windsurf", "rules"), (), ".md", False),
    ((".devin", "rules"), (), ".md", False),
    ((".github",), ("copilot-instructions.md",), None, False),
    ((".github", "instructions"), (), ".instructions.md", True),
)


def _plain(parts: tuple[str, ...]) -> bool:
    return not any(part.startswith(".") or part in SKIP_DIRS for part in parts)


def instruction_file(relative: PurePath) -> bool:
    """A root-relative file path where an agent documents its instructions, e.g. ``.cursor/rules/api.mdc``."""
    if not relative.parts:
        return False
    *directories, name = relative.parts
    if name in INSTRUCTION_NAMES:
        return _plain(tuple(directories))
    if name.startswith("."):
        return False
    for at in range(len(directories)):
        if not _plain(tuple(directories[:at])):
            return False
        rest = tuple(directories[at:])
        for chain, names, suffix, nested in INSTRUCTION_DIRS:
            below = rest[len(chain):]
            if rest[:len(chain)] != chain or (below and not nested) or not _plain(below):
                continue
            if name in names or (suffix is not None and name.endswith(suffix)):
                return True
    return False


class InputPathError(ValueError):
    """An input must be skipped; the message is a content-free reason."""


def component_skip_reason(relative: Path, *, directory: bool = False, instructions: bool = False) -> str | None:
    """Check root-relative components, never the root's own name or ancestors.

    With ``instructions``, a file at a documented agent instruction location passes (``instruction_file``)."""
    if instructions and not directory and instruction_file(PurePath(relative)):
        return None
    if any(part.startswith(".") and part not in (".", "..") for part in relative.parts):
        return "hidden component"
    parts = relative.parts if directory else relative.parts[:-1]
    if any(part in SKIP_DIRS for part in parts):
        return "skipped directory"
    return None


def input_path(root: Path, path: Path, *, directory: bool = False, instructions: bool = False) -> Path:
    """Resolve an input inside root, excluding hidden/skipped walked AND target components.

    Callers keep the walked path for reporting. The returned target must be opened without
    following new symlinks so a swap after this check cannot bypass the policy.
    """
    if "\x00" in str(path) or "\x00" in str(root):
        raise InputPathError("null character in path")  # the OS would raise ValueError on lstat
    root = Path(root).resolve()
    walked = Path(path).absolute()
    try:
        relative = walked.relative_to(root)
    except ValueError:
        raise InputPathError("path escapes root") from None
    if reason := component_skip_reason(relative, directory=directory, instructions=instructions):
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
    if reason := component_skip_reason(relative, directory=directory, instructions=instructions):
        raise InputPathError(reason + " in resolved path")
    return resolved
