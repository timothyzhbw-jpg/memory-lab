"""Optional Typer integration for consumers that already depend on Typer.

Framework errors (missing paths, invalid options, unknown commands) can also quote untrusted text.
Sanitize their messages before Typer formats them, using the same boundary as application errors.
"""
from __future__ import annotations

# Newer Typer releases vendor Click; older supported releases use the external package.
try:
    from typer._click.exceptions import ClickException
except ImportError:
    from click import ClickException
from typer.core import TyperGroup

from .display import one_line as terminal_text


class SafeTyperGroup(TyperGroup):
    """Keep framework diagnostics literal without changing their layout or exit behavior."""

    def parse_args(self, ctx, args):
        try:
            return super().parse_args(ctx, args)
        except ClickException as exc:
            exc.message = terminal_text(exc.message)
            raise

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except ClickException as exc:
            exc.message = terminal_text(exc.message)
            raise
