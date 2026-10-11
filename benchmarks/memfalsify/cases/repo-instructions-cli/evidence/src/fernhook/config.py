"""Configuration: config.toml in the platformdirs user config directory, plus environment overrides."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

DEFAULT_FILE = Path(user_config_dir("fernhook")) / "config.toml"
CONFIG_FILE = Path(os.environ.get("FERNHOOK_CONFIG", DEFAULT_FILE))


@dataclass(frozen=True)
class Config:
    url: str
    space: str
    token: str
    vault: Path
    ignore: tuple[str, ...] = (".obsidian/*", "*.tmp", "drafts/*")


def load(space_override: str | None = None) -> Config:
    data = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    # FERNHOOK_TOKEN wins over the file so CI never needs a token on disk.
    token = os.environ.get("FERNHOOK_TOKEN") or data.get("token")
    if not token:
        raise SystemExit(f"fernhook: no token; set FERNHOOK_TOKEN or `token` in {CONFIG_FILE}")
    return Config(
        url=data.get("url", "http://localhost:8080"),
        space=space_override or data["space"],
        token=token,
        vault=Path(data.get("vault", ".")).expanduser(),
        ignore=tuple(data.get("ignore", Config.ignore)),
    )
