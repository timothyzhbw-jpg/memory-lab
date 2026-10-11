"""Command-line entry point: fernhook push | pull | status."""
from __future__ import annotations

import click

from . import config, sync


@click.group()
@click.option("--space", envvar="FERNHOOK_SPACE", help="Fernwiki space key; overrides `space` in config.toml.")
@click.pass_context
def main(ctx: click.Context, space: str | None) -> None:
    ctx.obj = config.load(space_override=space)


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would change without writing anything.")
@click.option("--jobs", "-j", default=8, show_default=True, help="Pages uploaded in parallel.")
@click.option("--prefer-local", is_flag=True, help="On a conflict, upload the local note instead of keeping the wiki page.")
@click.pass_obj
def push(cfg: config.Config, dry_run: bool, jobs: int, prefer_local: bool) -> None:
    """Upload notes changed since the last sync."""
    sync.push(cfg, dry_run=dry_run, jobs=jobs, prefer_local=prefer_local)


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would change without writing anything.")
@click.pass_obj
def pull(cfg: config.Config, dry_run: bool) -> None:
    """Download pages that changed on the wiki since the last sync."""
    sync.pull(cfg, dry_run=dry_run)


@main.command()
@click.pass_obj
def status(cfg: config.Config) -> None:
    """List notes changed since the last sync. Never writes anything."""
    lines = list(sync.status(cfg))
    for line in lines:
        click.echo(line)
    raise SystemExit(1 if lines else 0)
