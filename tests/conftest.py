import os
import io
import socket
import shutil
import subprocess
from pathlib import Path

import pytest

import windows_newlines
from memcore.reading import confinement_supported

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def pytest_addoption(parser):
    parser.addoption(windows_newlines.OPTION, action="store_true",
                     help="write text-mode files and CliRunner output with CRLF, as Windows does "
                          "(tests/windows_newlines.py)")


def pytest_configure(config):
    if config.getoption(windows_newlines.OPTION):
        config.add_cleanup(windows_newlines.simulate())


def pytest_report_header(config):
    if config.getoption(windows_newlines.OPTION):
        return "simulating Windows newlines: text-mode writes and CliRunner output get CRLF"

requires_confinement = pytest.mark.skipif(
    not confinement_supported(),
    reason="confined reads require POSIX O_NOFOLLOW and dir_fd support",
)


@pytest.fixture
def windows_console(monkeypatch):
    """Mirror Typer output to cp1252/CRLF streams while retaining CliRunner's actual bytes."""
    import typer

    raw = (io.BytesIO(), io.BytesIO())
    streams = [io.TextIOWrapper(buffer, encoding="cp1252", newline="\r\n") for buffer in raw]
    real_echo = typer.echo

    def echo(message=None, nl=True, err=False, **kwargs):
        real_echo(message, nl=nl, err=err, file=streams[int(err)], **kwargs)
        return real_echo(message, nl=nl, err=err, **kwargs)

    monkeypatch.setattr(typer, "echo", echo)
    yield raw
    for stream in streams:
        stream.close()


@pytest.fixture
def make_symlink():
    """Exercise links wherever supported, including Windows with symlink privileges."""
    def create(path, target, *, directory=False):
        try:
            # Windows stores string targets verbatim in the reparse point. Native
            # separators are needed for relative targets such as ../.git/config.
            path.symlink_to(Path(target), target_is_directory=directory)
        except OSError as exc:
            if os.name == "nt" and exc.winerror == 1314:
                pytest.skip("Windows symlinks require developer mode or symlink privileges")
            raise
    return create


@pytest.fixture
def memfalsify_demo(tmp_path):
    return Path(shutil.copytree(EXAMPLES / "memfalsify", tmp_path / "memfalsify"))


@pytest.fixture
def org_demo(tmp_path):
    return Path(shutil.copytree(EXAMPLES / "orgmemory" / "feature-a", tmp_path / "feature-a"))


def snapshot(root: Path) -> dict:
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def write_text_exact(path: Path, text: str) -> None:
    """Write ``text`` as UTF-8 bytes. Path.write_text would add a CR before every LF on Windows, which changes
    evidence hashes, quote offsets and golden files (tests/test_windows_guard.py)."""
    path.write_bytes(text.encode("utf-8"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def plain_cli_output(monkeypatch):
    """Typer forces Rich styling when GITHUB_ACTIONS or FORCE_COLOR is set, so CI output would carry ESC
    sequences of its own. Tests check that untrusted text never yields an ESC, so styling stays off."""
    monkeypatch.setenv("_TYPER_FORCE_DISABLE_TERMINAL", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    for name in ("FORCE_COLOR", "PY_COLORS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def github_dataset(tmp_path):
    root = tmp_path / "github"
    root.mkdir()
    fixtures = Path(__file__).parent / "fixtures" / "github"
    for name in ("policy.json", "github.json"):
        shutil.copyfile(fixtures / name, root / name)
    return root


class GitRepo:
    """A throwaway repository: identity in its local config, signing and hooks disabled with -c on every
    command, fixed dates, and no user or system config."""

    def __init__(self, root: Path, hooks: Path):
        self.root, self.hooks, self.count = root, hooks, 0

    def git(self, *args: str) -> str:
        self.count += 1
        stamp = f"2026-10-01T{self.count // 60:02d}:{self.count % 60:02d}:00+00:00"
        env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        proc = subprocess.run([shutil.which("git"), "-C", str(self.root), "-c", "commit.gpgsign=false",
                               "-c", "tag.gpgsign=false", "-c", f"core.hooksPath={self.hooks}",
                               "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args],
                              capture_output=True, env=env, check=False)
        assert proc.returncode == 0, proc.stderr.decode()
        return proc.stdout.decode("utf-8")

    def write(self, files: dict[str, str | None]) -> None:
        for name, text in files.items():
            path = self.root / name
            if text is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text.encode("utf-8"))  # exact bytes: no CRLF translation on Windows

    def commit(self, message: str, files: dict[str, str | None] | None = None) -> str:
        self.write(files or {})
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def read(self, name: str) -> bytes:
        return (self.root / name).read_bytes()


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_bytes(b"")
    hooks = tmp_path / "no-hooks"
    hooks.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    created = GitRepo(root, hooks)
    created.git("init", "-q")
    created.git("config", "--local", "user.name", "Test Author")
    created.git("config", "--local", "user.email", "test@example.com")
    return created
