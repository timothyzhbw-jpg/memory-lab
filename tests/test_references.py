"""``missing_reference``: a path the memory names that exists nowhere. Real cases in a temporary tree, one for each kind
of noise a scan of real agent files turned up, and for each way an existing file could read as missing."""
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import requires_confinement, snapshot, write_text_exact
from memcore.reading import confined_reads, read_limits
from memfalsify import Claim, audit, audit_directory, parse_memory
from memfalsify.audit import ATTENTION, ISSUE_PROPOSALS, needs_attention
from memfalsify.cli import app
from memfalsify.corpus import MAX_BYTES
from memfalsify.htmlreport import set_to_html, to_html
from memfalsify.memory import claim_record
from memfalsify.references import git_ignored, missing_references, noise, parse_reference, references_in
from memfalsify import references as references_module
from memfalsify.report import attention_count, set_to_markdown, to_markdown

runner = CliRunner()
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
posix_only = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks, permissions or file names")
AS_OF = date(2026, 11, 4)
ROOT = Path(__file__).resolve().parent.parent


def tree(root: Path, *names: str, **files: str) -> Path:
    """Make empty placeholder files (a name ending in a slash makes a directory holding one)."""
    for name in names:
        path = root / name
        if name.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
            write_text_exact(path / "keep.txt", "")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text_exact(path, "")
    return root


def flagged(root: Path, memory: str, name: str = "AGENTS.md", **options) -> list[str]:
    """The paths the audit flags for this memory text, as the issues name them."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_exact(path, memory)
    report = audit(path, root, as_of=AS_OF, **options)
    return [issue.detail.split("'")[1] for finding in report.findings for issue in finding.issues
            if issue.kind == "missing_reference"]


# --- the basic finding --------------------------------------------------------------------------------------------

def test_a_path_that_exists_nowhere_is_named_and_the_status_stays(tmp_path):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", "- The parser lives in `src/old/module.py` and it is small.\n")
    report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    finding = report.findings[0]
    assert finding.status == "insufficient_evidence"
    assert report.summary == {"insufficient_evidence": 1}
    assert [(i.kind, "src/old/module.py" in i.detail) for i in finding.issues] == [("missing_reference", True)]
    assert [p.action for p in finding.proposals] == ["attach_evidence", "fix_reference"]
    assert ISSUE_PROPOSALS["missing_reference"][0] == "fix_reference"
    assert needs_attention(report) and attention_count(report) == 1
    assert not ATTENTION & report.summary.keys()


def test_an_existing_path_is_not_flagged(tmp_path):
    tree(tmp_path, "src/old/module.py", "src/pkg/")
    assert flagged(tmp_path, "- The parser lives in `src/old/module.py` and in `src/pkg/` here.\n") == []


@pytest.mark.parametrize("memory, expected", [
    ("- See `src/gone.py:12` for the details.\n", ["src/gone.py"]),
    ("- See `src/gone.py:12:3` for the details.\n", ["src/gone.py"]),
    ("- See `src/gone.py#L12` for the details.\n", ["src/gone.py"]),
    ("- Run `src/gone.py::test_one` for the details.\n", ["src/gone.py"]),
    ("- Run `python src/gone.py --fast` for the details.\n", ["src/gone.py"]),
    ("- Read [the module](src/gone.py) for the details.\n", ["src/gone.py"]),
    ("- Read [the module](src/gone.py#top) for the details.\n", ["src/gone.py"]),
    ("- Look under `src/gone/` for the details.\n", ["src/gone/"]),
    ("- Look under `./src/gone/` for the details.\n", ["./src/gone/"]),
    ("- Read `src/gone.py` and `src/lost.py` for details.\n", ["src/gone.py", "src/lost.py"]),
    ("- Read `src/gone.py` and `src/gone.py` again for details.\n", ["src/gone.py"]),
    ("| Parser | `src/gone.py` |\n", ["src/gone.py"]),
    ("- `src/gone.py`\n", ["src/gone.py"]),
    ("- Run `python src/gone.py; echo done` for the details.\n", ["src/gone.py"]),
    ("Read `src/gone.py`.\n", ["src/gone.py"]),
    ("- Read ``src/gone.py`` for the details.\n", ["src/gone.py"]),
])
def test_where_a_path_can_be_written(tmp_path, memory, expected):
    tree(tmp_path, "src/here.py")
    assert flagged(tmp_path, memory) == expected


def test_each_sentence_answers_for_its_own_paths(tmp_path):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", "- The parser is quick and small. It lives in `src/gone.py` now.\n")
    report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    by_text = {f.text: [i.kind for i in f.issues] for f in report.findings}
    assert by_text == {"The parser is quick and small.": [], "It lives in src/gone.py now.": ["missing_reference"]}


def test_a_path_in_a_span_two_sentences_share_goes_to_the_sentence_it_starts_in(tmp_path):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", "- First run `python src/gone.py; echo done` and then stop there.\n")
    report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    assert sum(i.kind == "missing_reference" for f in report.findings for i in f.issues) == 1


def test_a_sentence_that_starts_with_a_path_keeps_it(tmp_path):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", "- The parser is quick and small. `src/gone.py` holds the parser now.\n")
    report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    kinds = {f.text: [i.kind for i in f.issues] for f in report.findings}
    assert kinds["src/gone.py holds the parser now."] == ["missing_reference"]


# --- what must stay silent: each kind of noise, with its first segment present ------------------------------------

@pytest.mark.parametrize("memory, present", [
    # GitHub actions and repositories
    ("- Use the `actions/cache` action in every workflow.\n", "actions/local.yml"),
    ("- Use `actions/checkout` to fetch the code.\n", "actions/local.yml"),
    ("- Use `actions/checkout@v4` to fetch the code.\n", "actions/local.yml"),
    ("- Use `actions/*@v4` to fetch the code.\n", "actions/local.yml"),
    ("- Lens owns the storage in `BerriAI/lens` today.\n", "BerriAI/local.txt"),
    ("- The `docs/manual` repo holds the manual pages.\n", "docs/local.md"),
    ("- It is a fork of `packages/next` on GitHub.\n", "packages/local.txt"),
    # MIME types
    ("- Send `multipart/form-data` to the server.\n", "multipart/local.txt"),
    ("- Send `application/json` to the server.\n", "application/local.txt"),
    ("- Send `image/png` to the server.\n", "image/local.txt"),
    ("- Send `text/plain` to the server.\n", "text/local.txt"),
    # branches and refs
    ("- Name the branch `docs/fix-typo` when fixing words.\n", "docs/local.md"),
    ("- Push `feature/new-cli-command` for review now.\n", "feature/local.txt"),
    ("- Compare with `origin/main` before merging.\n", "origin/local.txt"),
    # placeholders
    ("- Create the directory `nodes/YourService/` first.\n", "nodes/local.txt"),
    ("- Tests go in `tests/test_action_EventNameHere.py` always.\n", "tests/local.py"),
    ("- Modules use `src/snake_case.py` as their names.\n", "src/local.py"),
    ("- Records are `docs/NNN-title.md` in that folder.\n", "docs/local.md"),
    ("- Records are `docs/ADR-XXX.md` in that folder.\n", "docs/local.md"),
    ("- Try `src/foo.py` and see what happens here.\n", "src/local.py"),
    ("- Try `src/foo/bar.py` and see what happens here.\n", "src/local.py"),
    ("- Try `src/example.py` and see what happens here.\n", "src/local.py"),
    ("- Try `src/your_module.py` and see what happens here.\n", "src/local.py"),
    ("- Try `src/my-thing.py` and see what happens here.\n", "src/local.py"),
    ("- Try `src/ExampleWidget.tsx` and see what happens here.\n", "src/local.py"),
    ("- Try `src/PROVIDER_NAME.py` and see what happens here.\n", "src/local.py"),
    ("- Move the old text to `docs/0.MINOR.x.md` at each release.\n", "docs/local.md"),
    ("- Try `src/your/path/thing.py` and see what happens here.\n", "src/local.py"),
    ("- Run `go test ./src/myservice/` and see what happens here.\n", "src/local.py"),
    ("- Run `pytest tests/path/to/test.py` and read the output.\n", "tests/local.py"),
    ("- Try `src/<name>.py` and see what happens here.\n", "src/local.py"),
    ("- Try `src/{name}.py` and see what happens here.\n", "src/local.py"),
    ("- Try `src/$NAME.py` and see what happens here.\n", "src/local.py"),
    # generated output and dependencies
    ("- The bundle lands in `node_modules/dep/index.js` here.\n", "node_modules/local.txt"),
    ("- The venv holds `.venv/bin/python` for the tools.\n", ".venv/local.txt"),
    # names relative to something unknown
    ("- Read `lib/gone.py` in the other tool.\n", "src/local.py"),
    ("- It is `gone.py` over in the other tool.\n", "src/local.py"),
    # not paths at all
    ("- Open https://example.com/src/gone.py in a browser.\n", "src/local.py"),
    ("- Open `https://example.com/src/gone.py` in a browser.\n", "src/local.py"),
    ("- Read `/usr/local/src/gone.py` on the server.\n", "src/local.py"),
    ("- Read `~/src/gone.py` on the server.\n", "src/local.py"),
    ("- Read `$HOME/src/gone.py` on the server.\n", "src/local.py"),
    ("- Read `C:\\src\\gone.py` on the server.\n", "src/local.py"),
    ("- Matches `src/*.py` and nothing more than that.\n", "src/local.py"),
    ("- Matches `src/**` and nothing more than that.\n", "src/local.py"),
    ("- Use `src/...` and nothing more than that.\n", "src/local.py"),
    ("- Use `src/templates/...yaml` and nothing more than that.\n", "src/local.py"),
    ("- One file per release, `src/vYYYY_MM_DD.py` in that folder.\n", "src/local.py"),
    ("- It runs at `24/7` and it costs `1/2` as much.\n", "24/local.txt"),
    ("- Lint with `src/rules/` globs.\n```\nsrc/gone.py in a fence is not a claim\n```\n", "src/rules/local.txt"),
    # API routes and other names
    ("- `api/v1/eval` is used by the query runner.\n", "api/local.txt"),
    ("- The endpoint `src/users/list` answers quickly.\n", "src/local.py"),
    ("- A language such as `json/jsonc` works from a plugin.\n", "json/local.txt"),
    ("- The title is `Core/EmptyState` in the design system.\n", "Core/local.txt"),
    # the sentence says the path is someone else's, gone or yet to be made
    ("- Prettier's `src/main/core.js` does the same thing.\n", "src/local.py"),
    ("- The compiler is `src/aot/b3.cpp` in oven-sh/WebKit today.\n", "src/local.py"),
    ("- Read `docs/README.md` inside that installed package first.\n", "docs/local.md"),
    ("- In the Turborepo source repository, the template is `src/cli/agent.rs` today.\n", "src/local.py"),
    ("- `src/old.py` was removed in the last rewrite.\n", "src/local.py"),
    ("- `src/old.py` no longer holds the parser.\n", "src/local.py"),
    ("- Read `docs/crates.md` (if exists) before starting.\n", "docs/local.md"),
    ("- Create `src/brand_new.py` for each new parser.\n", "src/local.py"),
    ("- The tool writes `src/plan.json` during the build.\n", "src/local.py"),
    ("- Never hand-edit generated files: `src/functions/database-types.ts`, `src/gen.ts` and more.\n", "src/local.py"),
    ("- The module is ready to move to `packages/skills/backend` next year.\n", "packages/local.txt"),
])
def test_noise_stays_silent(tmp_path, memory, present):
    tree(tmp_path, present)
    assert flagged(tmp_path, memory) == []


def test_the_words_of_a_code_span_say_nothing_about_a_path(tmp_path):
    tree(tmp_path, "src/here.py")
    memory = "- Never edit `src/gone.py` by hand (run `make generate.parts`).\n"
    assert flagged(tmp_path, memory) == ["src/gone.py"]


def test_in_this_repository_is_not_someone_else(tmp_path):
    tree(tmp_path, "src/here.py")
    assert flagged(tmp_path, "- In this repository the parser is `src/gone.py` for now.\n") == ["src/gone.py"]
    assert flagged(tmp_path, "- From the repo root run `src/gone.py` for now.\n") == ["src/gone.py"]


def test_used_to_as_a_purpose_says_nothing_about_a_path(tmp_path):
    tree(tmp_path, "scripts/here.py")
    assert flagged(tmp_path, "- `scripts/gone.py` is used to build the docs.\n") == ["scripts/gone.py"]


def test_two_bare_segments_count_only_under_a_source_directory(tmp_path):
    tree(tmp_path, "tests/here.py", "Management/keep.txt")
    assert flagged(tmp_path, "- `tests/test_litellm` holds 59 percent of them.\n") == ["tests/test_litellm"]
    assert flagged(tmp_path, "- `Management/UI` holds 59 percent of them.\n") == []


def test_the_first_segment_must_match_its_case(tmp_path):
    tree(tmp_path, "core/here.py")
    assert flagged(tmp_path, "- The widget is `Core/Button/Thing.tsx` in the system.\n") == []


def test_the_first_segment_next_to_a_nested_file_counts_but_a_middle_directory_does_not(tmp_path):
    tree(tmp_path, "packages/web/src/here.ts", "packages/json/here.txt")
    assert flagged(tmp_path, "- The app is in `src/gone.ts` for now.\n", "packages/web/AGENTS.md") == ["src/gone.ts"]
    # packages/json is next to packages/web, not next to the file or at the root.
    assert flagged(tmp_path, "- The app is in `json/gone.ts` for now.\n", "packages/web/AGENTS.md") == []


def test_a_file_as_first_segment_does_not_vouch(tmp_path):
    tree(tmp_path, "docs.md")
    assert flagged(tmp_path, "- Read `docs/gone.md` for the rules.\n") == []


# --- an existing file must never read as missing --------------------------------------------------------------------

@pytest.mark.parametrize("memory, present", [
    # a monorepo's short paths
    ("- The parser is in `core/parse.py` for now.\n", ["core/here.txt", "packages/one/core/parse.py"]),
    # import paths: no extension, or a directory's index
    ("- Use `hooks/misc/useThing` in the app.\n", ["hooks/here.ts", "apps/studio/hooks/misc/useThing.ts"]),
    ("- Use `lib/telemetry` in the app.\n", ["lib/telemetry/index.ts"]),
    ("- Use `lib/track` in the app.\n", ["lib/track.tsx"]),
    ("- Use `lib/track.server` in the app.\n", ["lib/track.server.ts"]),
    ("- Use `lib/track` in the app.\n", ["lib/track.test.ts"]),
    # TypeScript imports name the .js a compiler will write
    ("- Import `src/thing.js` in the app.\n", ["src/thing.ts"]),
    ("- Import `src/thing.ts` in the app.\n", ["src/thing.tsx"]),
    # what the evidence scan skips
    ("- The workflow is `.github/workflows/ci.yml` today.\n", [".github/workflows/ci.yml"]),
    ("- The hook is `tools/.hidden/run.sh` today.\n", ["tools/.hidden/run.sh"]),
    ("- Settings stay in `.env/local.txt` today.\n", [".env/local.txt"]),
    # a case or composition difference opens the same file on macOS and Windows
    ("- Read `Docs/README.md` for the rules.\n", ["Docs/here.txt", "docs/readme.md"]),
])
def test_existing_files_are_found(tmp_path, memory, present):
    tree(tmp_path, *present)
    assert flagged(tmp_path, memory) == []


def test_a_file_over_the_size_limit_and_a_binary_file_are_found(tmp_path):
    tree(tmp_path, "data/here.txt")
    (tmp_path / "data" / "large.bin").write_bytes(b"x" * (MAX_BYTES + 10))
    (tmp_path / "data" / "image.png").write_bytes(b"\x89PNG\x00\x00")
    (tmp_path / "data" / "latin.txt").write_bytes("caf\xe9".encode("latin-1"))
    memory = "- Fixtures are `data/large.bin`, `data/image.png` and `data/latin.txt` for now.\n"
    write_text_exact(tmp_path / "AGENTS.md", memory)
    report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    assert {s.path for s in report.skipped_evidence} >= {"data/large.bin", "data/latin.txt"}
    assert not report.findings[0].issues


def test_an_excluded_file_still_exists(tmp_path):
    tree(tmp_path, "vendor/lib/thing.py", "src/here.py")
    write_text_exact(tmp_path / "src" / "note.md", "Some evidence text that stays indexed.\n")
    assert flagged(tmp_path, "- The vendored copy is `vendor/lib/thing.py` here.\n", exclude=["vendor/**"]) == []
    assert flagged(tmp_path, "- The vendored copy is `vendor/lib/gone.py` here.\n", exclude=["vendor/**"]) == [
        "vendor/lib/gone.py"]


def test_a_linked_file_counts_as_existing(tmp_path):
    tree(tmp_path, "docs/guide.md", "src/here.py")
    assert flagged(tmp_path, "- The guide is `docs/guide.md` and the code `src/here.py` here.\n") == []


@posix_only
def test_a_symlink_is_a_name_and_is_never_followed(tmp_path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    tree(outside, "secret.txt")
    tree(root, "src/here.py", "links/keep.txt")
    (root / "links" / "dir").symlink_to(outside, target_is_directory=True)
    (root / "links" / "file.txt").symlink_to(outside / "secret.txt")
    (root / "links" / "broken.txt").symlink_to(outside / "nothing.txt")
    # What is below a link to a directory cannot be known, whether or not it exists out there.
    memory = ("- It is `links/dir/secret.txt` and `links/dir/nothing.txt` here.\n"
              "- It is `links/file.txt` and `links/broken.txt` here.\n"
              "- It is `src/gone.py` here.\n")
    assert flagged(root, memory) == ["src/gone.py"]


def test_an_empty_directory_is_a_submodule_not_fetched(tmp_path):
    tree(tmp_path, "vendor/keep.txt")
    (tmp_path / "vendor" / "sub").mkdir()
    assert flagged(tmp_path, "- The code is `vendor/sub/src/thing.c` for now.\n") == []


@posix_only
def test_an_unreadable_directory_means_nothing_can_be_called_missing(tmp_path):
    tree(tmp_path, "src/here.py", "locked/a.txt")
    write_text_exact(tmp_path / "src" / "note.md", "Some evidence text that stays indexed.\n")
    locked = tmp_path / "locked"
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running as a user that ignores permissions")
        write_text_exact(tmp_path / "AGENTS.md", "- It is `src/gone.py` for now.\n")
        report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF, exclude=["locked/**"])
        assert not report.findings[0].issues
    finally:
        locked.chmod(0o755)


# --- the characters in the text ---------------------------------------------------------------------------------------

def test_windows_backslashes(tmp_path):
    tree(tmp_path, "src/here/mod.py")
    assert flagged(tmp_path, "- It is in `src\\here\\mod.py` for now.\n") == []
    assert flagged(tmp_path, "- It is in `src\\gone\\mod.py` for now.\n") == ["src\\gone\\mod.py"]
    assert flagged(tmp_path, "- It is in `src\\gone\\` for now.\n") == ["src\\gone\\"]
    assert flagged(tmp_path, "- It is in `.\\src\\gone.py` for now.\n") == [".\\src\\gone.py"]
    # A regular expression or an escape is not a path.
    assert flagged(tmp_path, "- It matches `src\\d+` and `src\\nname` for now.\n") == []
    assert flagged(tmp_path, "- It matches `\\d+\\.py` for now.\n") == []
    assert flagged(tmp_path, "- It is in `src\\gone/mod.py` for now.\n") == []


def test_cjk_around_and_inside_the_backticks(tmp_path):
    tree(tmp_path, "src/here.py", "docs/说明.md")
    assert flagged(tmp_path, "- 解析器在`src/gone.py`里面，请先阅读。\n") == ["src/gone.py"]
    assert flagged(tmp_path, "- 解析器在`src/here.py`里面，请先阅读。\n") == []
    assert flagged(tmp_path, "- 说明在`docs/说明.md`里面，请先阅读。\n") == []
    assert flagged(tmp_path, "- 说明在`docs/缺失.md`里面，请先阅读。\n") == ["docs/缺失.md"]
    assert flagged(tmp_path, "- 说明在`docs/缺失.md。`里面，请先阅读。\n") == ["docs/缺失.md"]


@pytest.mark.parametrize("memory", [
    "- `src/old.py` 已删除，请不要再用。\n",
    "- 新建 `src/brand_new.py` 来放解析器。\n",
    "- 导出的文件放进 `docs/adr` 这样的目录。\n",
    "- 另一个仓库里的 `src/other.py` 也是这样。\n",
    "- 请使用 `src/gone` 分支来修复。\n",
    "- 调用 `src/users/list` 接口就够了。\n",
])
def test_chinese_lines_can_say_what_a_path_is_too(tmp_path, memory):
    tree(tmp_path, "src/here.py", "docs/here.md")
    assert flagged(tmp_path, memory) == []


def test_unicode_composition_does_not_make_a_file_missing(tmp_path):
    name = unicodedata.normalize("NFD", "docs/café.md")
    tree(tmp_path, name)
    assert flagged(tmp_path, "- It is in `" + unicodedata.normalize("NFC", "docs/café.md") + "` for now.\n") == []


HOSTILE = ("src/\x07gone\x1b.py", "src/\u202egone.py", "src/gone\x7f.py", "src/\u200bgone.py")


@pytest.mark.parametrize("path", HOSTILE)
def test_a_hostile_path_reaches_no_report_raw(tmp_path, monkeypatch, path):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", f"- It is in `{path}` for now.\n")
    report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    issue = next(i for f in report.findings for i in f.issues if i.kind == "missing_reference")
    assert "\\u" in issue.detail and path not in issue.detail
    hostile_chars = {c for c in path if not c.isascii() or ord(c) < 32 or ord(c) == 127}
    assert hostile_chars
    for text in (to_markdown(report), to_html(report)):
        assert not hostile_chars & set(text), text
    monkeypatch.chdir(tmp_path)
    for fmt in ("markdown", "json", "html"):
        shown = runner.invoke(app, ["audit", "AGENTS.md", "--evidence", ".", "--format", fmt, "--as-of", "2026-11-04"])
        assert shown.exit_code == 0, shown.output
        assert not hostile_chars & set(shown.stdout)
        assert "missing_reference" in shown.stdout
    assert not hostile_chars & set(shown.stderr)


def test_a_line_separator_splits_a_code_span_into_words(tmp_path):
    tree(tmp_path, "src/here.py")
    assert flagged(tmp_path, "- It is in `src/\u2028gone.py` for now.\n") == []
    write_text_exact(tmp_path / "AGENTS.md", "- It is in `src/\x1b[31mgone\x1b[0m.py` for now.\n")
    assert "\x1b" not in to_markdown(audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF))


def test_markup_in_a_path_stays_text_in_every_report(tmp_path):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", "- It is in ``src/**bold**/a|b/x.py`` and `src/_a_/__b__/<img>.py` here.\n"
                                             "- It is in `src/_a_/__init__.py` here too.\n")
    report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    assert "src/_a_/__init__.py" in " ".join(i.detail for f in report.findings for i in f.issues)
    assert "\\_\\_init\\_\\_" in to_markdown(report) or "__init__" in to_markdown(report)
    assert "<img>" not in to_html(report)


@pytest.mark.parametrize("text", [
    "`" * 200_000,
    "` `" * 60_000,
    "`a/b.py` " * 20_000,
    "[x](a/b.py) " * 20_000,
    "src/" * 50_000,
    "[" * 200_000,
    "[](a" * 60_000,
    "`" + ":" * 200_000 + "!`",
    "in the " * 40_000,
])
def test_a_hostile_line_costs_little(text):
    assert isinstance(references_in(text), list)


def test_a_very_long_path_is_not_a_path():
    assert parse_reference("src/" + "a" * 5000 + ".py") is None
    assert parse_reference("src/\x00gone.py") is None


# --- paths that leave the root ----------------------------------------------------------------------------------------

def test_a_path_that_leaves_the_root_is_not_judged_and_nothing_outside_is_read(tmp_path, monkeypatch):
    root = tmp_path / "a" / "b" / "repo"
    tree(root, "etc/here.txt", "src/here.py")
    tree(tmp_path, "a/b/secret.txt")
    opened: list[str] = []
    real_lstat = os.lstat

    def watching(path, *args, **kwargs):
        opened.append(str(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", watching)
    memory = ("- It is `../../etc/passwd` on this machine.\n- It is `../secret.txt` on this machine.\n"
              "- It is `../../../../../../../../etc/passwd` on this machine.\n- It is `src/../../secret.txt` here.\n"
              "- It is `/etc/passwd` on this machine.\n")
    assert flagged(root, memory) == []
    assert not [name for name in opened if "passwd" in name or "secret" in name]


def test_upward_paths_inside_the_root_are_judged_from_the_file_that_names_them(tmp_path):
    tree(tmp_path, "src/here.py", "docs/here.md")
    memory = "- See `../src/here.py` and `../src/gone.py` for this.\n"
    assert flagged(tmp_path, memory, "docs/AGENTS.md") == ["../src/gone.py"]


def test_upward_from_a_nested_file_can_land_inside_and_is_only_judged_there(tmp_path):
    tree(tmp_path, "etc/here.txt", "a/b/here.txt")
    # From a/b, this stays inside the root: it means etc/passwd in the project, not the machine's.
    assert flagged(tmp_path, "- It is `../../etc/passwd.txt` here.\n", "a/b/AGENTS.md") == ["../../etc/passwd.txt"]
    # One more level up leaves the root.
    assert flagged(tmp_path, "- It is `../../../etc/passwd.txt` here.\n", "a/b/AGENTS.md") == []


# --- when the check runs ------------------------------------------------------------------------------------------------

MISSING_LINE = "- The parser lives in `src/gone.py` and it is small.\n"


def _kinds(report) -> list[str]:
    return [issue.kind for finding in report.findings for issue in finding.issues]


def test_no_evidence_root_means_no_check_and_the_report_says_nothing(tmp_path):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", MISSING_LINE)
    report = audit(tmp_path / "AGENTS.md", as_of=AS_OF)
    assert "missing_reference" not in _kinds(report)
    assert "missing_reference" not in to_markdown(report)
    assert not any("path" in line for line in report.limitations)


def test_a_memory_outside_the_evidence_root_means_no_check(tmp_path):
    tree(tmp_path, "evidence/src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", MISSING_LINE)
    assert "missing_reference" not in _kinds(audit(tmp_path / "AGENTS.md", tmp_path / "evidence", as_of=AS_OF))
    tree(tmp_path, "src/here.py")
    assert "missing_reference" in _kinds(audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF))


def test_check_references_false_turns_it_off(tmp_path):
    tree(tmp_path, "src/here.py")
    assert flagged(tmp_path, MISSING_LINE, check_references=False) == []


def test_a_nested_memory_under_the_root_is_checked(tmp_path):
    tree(tmp_path, "src/here.py", "docs/here.md")
    write_text_exact(tmp_path / "docs" / "AGENTS.md", MISSING_LINE)
    report = audit(tmp_path / "docs" / "AGENTS.md", tmp_path, as_of=AS_OF)
    assert _kinds(report) == ["missing_reference"]


def test_a_linked_file_is_checked_from_its_own_directory(tmp_path):
    tree(tmp_path, "notes/src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", "- [Notes](notes/more.md)\n")
    write_text_exact(tmp_path / "notes" / "more.md", MISSING_LINE)
    report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    assert [(f.location, [i.kind for i in f.issues]) for f in report.findings] == [
        ("notes/more.md:1", ["missing_reference"])]


def test_a_budgeted_run_makes_no_second_walk(tmp_path):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", MISSING_LINE)
    with read_limits(directories=1000):
        report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    assert _kinds(report) == []


def test_a_claim_with_no_raw_line_has_no_paths(tmp_path):
    tree(tmp_path, "src/here.py")
    claim = Claim(id="m-00000000", text="The parser lives in src/gone.py today.", path="AGENTS.md", line=1)
    assert missing_references([claim], tmp_path.resolve()) == {}


def test_no_walk_when_no_claim_names_a_path(tmp_path, monkeypatch):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", "- The parser is small and quick in every test.\n")
    walks = []
    monkeypatch.setattr(references_module, "walk_tree", lambda *a: walks.append(a) or None)
    audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    assert walks == []


def test_claims_keep_their_public_shape(tmp_path):
    write_text_exact(tmp_path / "AGENTS.md", MISSING_LINE)
    claims, _ = parse_memory(tmp_path / "AGENTS.md")
    assert set(Claim.model_fields) == {"id", "text", "path", "line", "section", "also_at", "metadata"}
    assert set(claims[0].model_dump()) == set(Claim.model_fields)
    assert set(claim_record(claims[0])) == {"memory_id", "location", "text"}
    assert "`" not in claims[0].text


# --- generated output: git's ignore rules, and what stands in for them -----------------------------------------------

@needs_git
def test_paths_git_ignores_are_not_flagged(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tree(tmp_path, "build/keep.txt", "packages/web/src/here.ts")
    write_text_exact(tmp_path / ".gitignore", "build/\ndist/\n*.log\n")
    memory = ("- The binary is `build/debug/app` after a build.\n"
              "- The bundle is `packages/web/dist/app.js` after a build.\n"
              "- The bundle folder is `packages/web/dist` after a build.\n"
              "- The trace is `packages/web/trace.log` after a build.\n"
              "- The parser is `packages/web/gone.ts` for now.\n")
    assert flagged(tmp_path, memory) == ["packages/web/gone.ts"]


@needs_git
def test_paths_a_nested_ignore_file_covers_are_not_flagged(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tree(tmp_path, "packages/web/src/here.ts")
    write_text_exact(tmp_path / "packages" / "web" / ".gitignore", "out/\n")
    memory = "- The bundle is `src/out/app.js` after a build.\n- It is `out/app.js` here.\n- It is `src/gone.js` here.\n"
    assert flagged(tmp_path, memory, "packages/web/AGENTS.md") == ["src/gone.js"]


def test_without_git_generated_names_stand_in(tmp_path, monkeypatch):
    tree(tmp_path, "build/keep.txt", "src/here.py")
    monkeypatch.setattr(references_module.shutil, "which", lambda name: None)
    memory = "- The binary is `build/debug/app` after a build.\n- The parser is `src/gone.py` for now.\n"
    assert flagged(tmp_path, memory) == ["src/gone.py"]


def test_outside_a_repository_generated_names_stand_in(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    tree(tmp_path, "target/keep.txt", "src/here.py")
    memory = "- The binary is `target/debug/app` after a build.\n- The parser is `src/gone.py` for now.\n"
    assert flagged(tmp_path, memory) == ["src/gone.py"]


@needs_git
@posix_only
def test_repository_config_and_hooks_never_run(tmp_path):
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    marker = tmp_path / "ran"
    script = tmp_path / "script.sh"
    write_text_exact(script, f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
    script.chmod(0o755)
    hooks = root / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for hook in ("post-checkout", "post-commit", "pre-commit", "post-index-change", "reference-transaction",
                 "fsmonitor-watchman", "query-watchman"):
        shutil.copy(script, hooks / hook)
    for key in ("core.fsmonitor", "core.pager", "core.sshCommand", "diff.external", "gpg.program", "core.editor",
                "core.hooksPath", "core.gitProxy"):
        subprocess.run(["git", "-C", str(root), "config", "--local", key, str(script)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "--local", "alias.check-ignore", f"!{script}"], check=True)
    tree(root, "src/here.py")
    # An index on disk is what would make git consult the file system monitor.
    subprocess.run(["git", "-C", str(root), "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", "add",
                    "src/here.py"], check=True)
    assert not marker.exists()
    before = snapshot(root / ".git")
    assert flagged(root, MISSING_LINE) == ["src/gone.py"]
    assert not marker.exists()
    assert snapshot(root / ".git") == before


@posix_only
def test_a_git_inside_the_repository_is_never_run(tmp_path, monkeypatch):
    marker = tmp_path / "ran"
    root = tmp_path / "repo"
    tree(root, "src/here.py")
    fake = root / "git"
    write_text_exact(fake, f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
    fake.chmod(0o755)
    monkeypatch.setattr(references_module.shutil, "which", lambda name: str(fake))
    assert git_ignored(root.resolve(), ["src/gone.py"]) is None
    assert flagged(root, MISSING_LINE) == ["src/gone.py"]
    assert not marker.exists()


@posix_only
def test_a_git_in_the_current_directory_is_never_run(tmp_path, monkeypatch):
    marker = tmp_path / "ran"
    root = tmp_path / "repo"
    here = tmp_path / "elsewhere"
    tree(root, "src/here.py")
    here.mkdir()
    fake = here / "git"
    write_text_exact(fake, f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
    fake.chmod(0o755)
    monkeypatch.chdir(here)
    monkeypatch.setattr(references_module.shutil, "which", lambda name: str(fake))
    assert git_ignored(root.resolve(), ["src/gone.py"]) is None
    assert not marker.exists()


@needs_git
@posix_only
def test_an_ignore_file_that_is_a_fifo_cannot_hang_the_audit(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tree(tmp_path, "src/here.py")
    os.mkfifo(tmp_path / ".gitignore")
    monkeypatch.setattr(references_module, "GIT_SECONDS", 1)
    write_text_exact(tmp_path / "AGENTS.md", MISSING_LINE)
    claims, _ = parse_memory(tmp_path / "AGENTS.md")
    assert missing_references(claims, tmp_path.resolve()) == {claims[0].id: ["src/gone.py"]}


@pytest.mark.parametrize("failure", [OSError("no"), subprocess.TimeoutExpired("git", 1), ValueError("no")])
def test_when_git_fails_the_check_goes_on_without_it(tmp_path, monkeypatch, failure):
    tree(tmp_path, "src/here.py", "build/keep.txt")

    def refuse(*args, **kwargs):
        raise failure

    monkeypatch.setattr(references_module, "_trusted_git", lambda root: "git")
    monkeypatch.setattr(references_module.subprocess, "run", refuse)
    assert flagged(tmp_path, MISSING_LINE + "- It is `build/app/x` here.\n") == ["src/gone.py"]


def test_a_fatal_answer_from_git_goes_on_without_it(tmp_path, monkeypatch):
    tree(tmp_path, "src/here.py", "build/keep.txt")
    monkeypatch.setattr(references_module, "_trusted_git", lambda root: "git")
    monkeypatch.setattr(references_module.subprocess, "run",
                        lambda args, **kwargs: subprocess.CompletedProcess(args, 128, b"", b"fatal: not a repository"))
    assert flagged(tmp_path, MISSING_LINE + "- It is `build/app/x` here.\n") == ["src/gone.py"]


# --- reports ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path, monkeypatch):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", MISSING_LINE)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _audit(*args):
    return runner.invoke(app, ["audit", *args, "--as-of", "2026-11-04"])


def test_all_three_formats_show_it(repo):
    markdown = _audit("AGENTS.md", "--evidence", ".")
    assert markdown.exit_code == 0
    assert "- missing_reference: 'src/gone.py' is named here" in markdown.stdout
    assert "`fix_reference`" in markdown.stdout
    html = _audit("AGENTS.md", "--evidence", ".", "--format", "html")
    assert "<code>missing_reference</code>: &#x27;src/gone.py&#x27; is named here" in html.stdout
    assert "<code>fix_reference</code>" in html.stdout
    data = json.loads(_audit("AGENTS.md", "--evidence", ".", "--format", "json").stdout)
    finding = data["findings"][0]
    assert finding["status"] == "insufficient_evidence"
    assert finding["issues"][0]["kind"] == "missing_reference" and "src/gone.py" in finding["issues"][0]["detail"]
    assert data["summary"] == {"insufficient_evidence": 1}


def test_a_note_on_stderr_says_why_strict_may_fail(repo):
    result = _audit("AGENTS.md", "--evidence", ".")
    assert "1 claim(s) name a path that exists nowhere" in result.stderr
    assert "missing_reference" in result.stderr


def test_a_run_with_no_missing_path_prints_no_note(repo):
    tree(repo, "src/gone.py")
    result = _audit("AGENTS.md", "--evidence", ".")
    assert result.exit_code == 0 and result.stderr == ""


def test_a_run_with_a_missing_path_gains_only_the_issue_and_its_proposal(repo):
    with_issue = json.loads(_audit("AGENTS.md", "--evidence", ".", "--format", "json").stdout)
    without = json.loads(_audit("AGENTS.md", "--evidence", ".", "--format", "json", "--no-references").stdout)
    assert with_issue != without
    finding = with_issue["findings"][0]
    assert [i["kind"] for i in finding["issues"]] == ["missing_reference"]
    finding["issues"] = []
    finding["proposals"] = [p for p in finding["proposals"] if p["action"] != "fix_reference"]
    assert with_issue == without


def test_strict_fails_on_a_missing_path(repo):
    assert _audit("AGENTS.md", "--evidence", ".", "--strict").exit_code == 1
    assert _audit("--strict").exit_code == 1


def test_no_references_turns_it_off_for_strict_too(repo):
    result = _audit("AGENTS.md", "--evidence", ".", "--strict", "--no-references")
    assert result.exit_code == 0, result.output
    assert "missing_reference" not in result.stdout and result.stderr == ""
    assert _audit("--strict", "--no-references").exit_code == 0


def test_strict_passes_once_the_path_is_right(repo):
    tree(repo, "src/gone.py")
    assert _audit("AGENTS.md", "--evidence", ".", "--strict").exit_code == 0
    assert _audit("--strict").exit_code == 0


def test_strict_without_evidence_is_not_affected(repo):
    assert _audit("AGENTS.md", "--strict").exit_code == 0


def test_no_argument_mode_reports_it_under_the_right_file(tmp_path, monkeypatch):
    tree(tmp_path, "packages/web/src/here.ts", "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", "- The app starts from `src/gone.py` for now.\n")
    write_text_exact(tmp_path / "packages" / "web" / "AGENTS.md", "- The web app starts from `src/lost.ts` for now.\n")
    monkeypatch.chdir(tmp_path)
    result = audit_directory(Path("."), as_of=AS_OF)
    assert {r.memory_file: [i.detail.split("'")[1] for f in r.findings for i in f.issues] for r in result.reports} == {
        "AGENTS.md": ["src/gone.py"], "packages/web/AGENTS.md": ["src/lost.ts"]}
    markdown = set_to_markdown(result)
    assert markdown.count("missing_reference") == 2
    assert "| `AGENTS.md` | 1 | 1 |" in markdown
    assert "| `packages/web/AGENTS.md` | 1 | 1 |" in markdown
    assert set_to_html(result).count("missing_reference") == 2
    shown = runner.invoke(app, ["audit", "--as-of", "2026-11-04"])
    assert shown.exit_code == 0
    assert "2 claim(s) name a path" in shown.stderr


def test_no_argument_mode_with_outside_evidence_makes_no_check(tmp_path, monkeypatch):
    tree(tmp_path, "project/src/here.py", "evidence/note.md")
    write_text_exact(tmp_path / "project" / "AGENTS.md", MISSING_LINE)
    monkeypatch.chdir(tmp_path / "project")
    result = audit_directory(Path("."), tmp_path / "evidence", as_of=AS_OF)
    assert "missing_reference" not in set_to_markdown(result)


def test_no_argument_mode_below_the_evidence_root_is_checked(tmp_path, monkeypatch):
    tree(tmp_path, "project/src/here.py", "shared/here.md")
    write_text_exact(tmp_path / "project" / "AGENTS.md", MISSING_LINE + "- See `shared/lost.md` for this.\n")
    monkeypatch.chdir(tmp_path / "project")
    result = audit_directory(Path("."), tmp_path, as_of=AS_OF)
    assert [i.detail.split("'")[1] for r in result.reports for f in r.findings for i in f.issues] == [
        "src/gone.py", "shared/lost.md"]


def test_no_argument_mode_walks_once(tmp_path, monkeypatch):
    tree(tmp_path, "src/here.py", "packages/web/src/here.ts")
    write_text_exact(tmp_path / "AGENTS.md", MISSING_LINE)
    write_text_exact(tmp_path / "CLAUDE.md", "- The other parser lives in `src/lost.py` for now.\n")
    write_text_exact(tmp_path / "packages" / "web" / "AGENTS.md", "- The app starts in `src/none.ts` for now.\n")
    monkeypatch.chdir(tmp_path)
    walks = []
    real = references_module.walk_tree
    monkeypatch.setattr(references_module, "walk_tree", lambda *a: walks.append(a) or real(*a))
    audit_directory(Path("."), as_of=AS_OF)
    assert len(walks) == 1


@pytest.mark.parametrize("first, second", [
    ("- The parser lives in src/gone.py for now.\n", "- The parser lives in `src/gone.py` for now.\n"),
    ("- The parser lives in `src/gone.py` for now.\n", "- The parser lives in src/gone.py for now.\n"),
])
def test_a_claim_stated_twice_is_read_in_both_places(tmp_path, monkeypatch, first, second):
    tree(tmp_path, "src/here.py")
    assert flagged(tmp_path, first + second) == ["src/gone.py"]
    write_text_exact(tmp_path / "AGENTS.md", first)
    write_text_exact(tmp_path / "CLAUDE.md", second)
    monkeypatch.chdir(tmp_path)
    result = audit_directory(Path("."), as_of=AS_OF)
    assert set_to_markdown(result).count("missing_reference") == 1
    assert runner.invoke(app, ["audit", "--strict", "--as-of", "2026-11-04"]).exit_code == 1


def test_a_claim_in_two_files_is_reported_once(tmp_path, monkeypatch):
    tree(tmp_path, "src/here.py")
    write_text_exact(tmp_path / "AGENTS.md", MISSING_LINE)
    write_text_exact(tmp_path / "CLAUDE.md", MISSING_LINE)
    monkeypatch.chdir(tmp_path)
    result = audit_directory(Path("."), as_of=AS_OF)
    assert set_to_markdown(result).count("missing_reference") == 1


def test_the_noise_function_names_its_reason():
    assert noise(parse_reference("src/foo.py")) == "placeholder"
    assert noise(parse_reference("src/real.py")) is None
    assert noise(parse_reference("single.py")) == "one segment"


# --- the GitHub Action and the MCP server -----------------------------------------------------------------------------

def _load_action():
    spec = importlib.util.spec_from_file_location("memfalsify_action_refs", ROOT / "action" / "memfalsify_action.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _action_env(tmp_path: Path) -> dict[str, str]:
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()
    env = {"RUNNER_TEMP": str(runner_dir), "GITHUB_STEP_SUMMARY": str(runner_dir / "summary.md"),
           "GITHUB_OUTPUT": str(runner_dir / "output")}
    write_text_exact(Path(env["GITHUB_STEP_SUMMARY"]), "")
    write_text_exact(Path(env["GITHUB_OUTPUT"]), "")
    return env


@pytest.mark.parametrize("strict, code, level", [("true", 1, "::error"), ("false", 0, "::warning")])
def test_the_action_counts_a_missing_path_and_says_so(tmp_path, monkeypatch, capsys, strict, code, level):
    work = tmp_path / "work"
    tree(work, "src/here.py")
    write_text_exact(work / "AGENTS.md", MISSING_LINE)
    monkeypatch.chdir(work)
    env = {**_action_env(tmp_path), "MEMFALSIFY_MEMORY": "AGENTS.md", "MEMFALSIFY_EVIDENCE": ".",
           "MEMFALSIFY_AS_OF": "2026-11-04", "MEMFALSIFY_STRICT": strict}
    assert _load_action().main(env) == code
    output = dict(line.split("=", 1) for line in Path(env["GITHUB_OUTPUT"]).read_text().splitlines())
    assert output["attention"] == "1"
    summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
    assert "missing_reference" in summary
    out = capsys.readouterr().out
    assert f"{level} title=MemFalsify::1 memory needs attention" in out
    assert "a path that exists nowhere" in out


def test_the_action_can_turn_the_check_off(tmp_path, monkeypatch, capsys):
    work = tmp_path / "work"
    tree(work, "src/here.py")
    write_text_exact(work / "AGENTS.md", MISSING_LINE)
    monkeypatch.chdir(work)
    env = {**_action_env(tmp_path), "MEMFALSIFY_MEMORY": "AGENTS.md", "MEMFALSIFY_EVIDENCE": ".",
           "MEMFALSIFY_AS_OF": "2026-11-04", "MEMFALSIFY_STRICT": "true", "MEMFALSIFY_REFERENCES": "false"}
    assert _load_action().main(env) == 0
    assert "missing_reference" not in Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
    assert _load_action().main({**env, "MEMFALSIFY_REFERENCES": "maybe"}) == 2
    assert "the references input must be true or false" in capsys.readouterr().out


def test_the_action_passes_the_input_through_env():
    text = (ROOT / "action.yml").read_text(encoding="utf-8")
    assert "\n  references:\n" in text and "MEMFALSIFY_REFERENCES: ${{ inputs.references }}" in text


@requires_confinement
def test_confined_reads_still_check_and_never_ask_git(tmp_path, monkeypatch):
    tree(tmp_path, "src/here.py", "build/keep.txt")
    memory = MISSING_LINE + "- The binary is `build/debug/app` for now.\n"
    write_text_exact(tmp_path / "AGENTS.md", memory)
    monkeypatch.setattr(references_module.subprocess, "run",
                        lambda *a, **k: pytest.fail("git must not run inside confined reads"))
    with confined_reads(tmp_path.resolve()):
        report = audit(tmp_path / "AGENTS.md", tmp_path, as_of=AS_OF)
    assert [i.detail.split("'")[1] for f in report.findings for i in f.issues] == ["src/gone.py"]


# --- this repository, and the docs ------------------------------------------------------------------------------------

def test_this_repository_names_no_missing_path_in_its_own_instructions():
    report = audit(ROOT / "AGENTS.md", ROOT, as_of=AS_OF, exclude=[
        "benchmarks/**", "examples/**", "tests/fixtures/**", ".worktrees/**", ".venv*/**"],
        check_references=True, min_coverage=2.0)
    assert [i.detail for f in report.findings for i in f.issues if i.kind == "missing_reference"] == []


@pytest.mark.parametrize("page", ["docs/quickstart.md", "docs/quickstart.zh-CN.md", "skills/memfalsify/SKILL.md"])
def test_the_docs_explain_the_check(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    assert "missing_reference" in text and "--no-references" in text
