import shutil
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def memfalsify_demo(tmp_path):
    return Path(shutil.copytree(EXAMPLES / "memfalsify", tmp_path / "memfalsify"))


@pytest.fixture
def org_demo(tmp_path):
    return Path(shutil.copytree(EXAMPLES / "orgmemory" / "feature-a", tmp_path / "feature-a"))


def snapshot(root: Path) -> dict:
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
