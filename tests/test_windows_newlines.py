"""``pytest --simulate-windows-newlines`` writes the bytes Windows writes (tests/windows_newlines.py)."""
import builtins
import io
import os
import pathlib
import tempfile
from pathlib import Path

import typer
from typer.testing import CliRunner

import windows_newlines
from conftest import write_text_exact


def echo_app() -> typer.Typer:
    app = typer.Typer()

    @app.command()
    def main(exact: bool = False) -> None:
        text = "report\nend\n"
        typer.echo(text.encode("utf-8") if exact else text, nl=False)
        typer.echo("note", err=True)

    return app


def test_simulated_text_writes_get_crlf_and_exact_writes_stay_lf(tmp_path):
    before = builtins.open, io.open, pathlib.Path.open
    undo = windows_newlines.simulate()
    try:
        (tmp_path / "fixture.md").write_text("a\nb\n", encoding="utf-8")  # windows-guard: allow (as in c92d759)
        with open(tmp_path / "log.jsonl", "a", encoding="utf-8") as handle:  # windows-guard: allow
            handle.write("{}\n")
        descriptor = os.open(tmp_path / "fd.txt", os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:  # windows-guard: allow
            handle.write("c\n")
        with tempfile.NamedTemporaryFile("w", dir=tmp_path, delete=False, encoding="utf-8") as named:
            named.write("d\n")
        write_text_exact(tmp_path / "exact.md", "a\nb\n")
        (tmp_path / "newline.md").write_text("a\nb\n", encoding="utf-8", newline="")
        with open(tmp_path / "binary.md", "wb") as handle:
            handle.write(b"a\nb\n")
        assert (tmp_path / "fixture.md").read_bytes() == b"a\r\nb\r\n"
        assert (tmp_path / "log.jsonl").read_bytes() == b"{}\r\n"
        assert (tmp_path / "fd.txt").read_bytes() == b"c\r\n"
        assert Path(named.name).read_bytes() == b"d\r\n"
        for name in ("exact.md", "newline.md", "binary.md"):
            assert (tmp_path / name).read_bytes() == b"a\nb\n", name
        assert (tmp_path / "fixture.md").read_text(encoding="utf-8") == "a\nb\n"  # reading is universal

        text, exact = CliRunner().invoke(echo_app(), []), CliRunner().invoke(echo_app(), ["--exact"])
        assert text.stdout_bytes == b"report\r\nend\r\n" and text.stderr_bytes == b"note\r\n"  # c92d759 rollback
        assert exact.stdout_bytes == b"report\nend\n"
        assert text.output == exact.output == "report\nend\nnote\n"
    finally:
        undo()
    assert (builtins.open, io.open, pathlib.Path.open) == before
