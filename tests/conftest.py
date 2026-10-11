import socket
import shutil
from pathlib import Path

import pytest

from memcore.reading import confinement_supported

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

requires_confinement = pytest.mark.skipif(
    not confinement_supported(),
    reason="confined reads require POSIX O_NOFOLLOW and dir_fd support",
)


@pytest.fixture
def memfalsify_demo(tmp_path):
    return Path(shutil.copytree(EXAMPLES / "memfalsify", tmp_path / "memfalsify"))


@pytest.fixture
def org_demo(tmp_path):
    return Path(shutil.copytree(EXAMPLES / "orgmemory" / "feature-a", tmp_path / "feature-a"))


def snapshot(root: Path) -> dict:
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def github_dataset(tmp_path):
    root = tmp_path / "github"
    root.mkdir()
    fixtures = Path(__file__).parent / "fixtures" / "github"
    for name in ("policy.json", "github.json"):
        shutil.copyfile(fixtures / name, root / name)
    return root
