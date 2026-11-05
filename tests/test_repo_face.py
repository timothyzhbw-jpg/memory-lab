"""What a stranger sees first stays true: the badge row, the issue forms and the contributor docs.

A badge keeps saying what it said when the code moves on, and GitHub drops an issue form it cannot read with
no error. These are plain-text checks: CI installs no YAML parser, and the forms are simple enough to read
by block.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
FORMS = ("bug_report.yml", "false_flag.yml")
READMES = ("README.md", "README.zh-CN.md")
CONTRIBUTING = ("CONTRIBUTING.md", "CONTRIBUTING.zh-CN.md")
REPOSITORY = "https://github.com/timothyzhbw-jpg/memory-lab"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _badge_row(readme: str) -> str:
    rows = [line for line in _read(ROOT / readme).splitlines()[:25] if line.startswith("[![")]
    assert len(rows) == 1, readme
    return rows[0]


def _blocks(form: str) -> dict[str, str]:
    """A form's body items, keyed by id: the text from each ``- type:`` to the next."""
    text = _read(TEMPLATES / form).split("body:\n", 1)[1]
    items = re.split(r"(?m)^(?=  - type: )", text)
    found: dict[str, str] = {}
    for item in items:
        match = re.search(r"(?m)^    id: (\S+)$", item)
        if match:
            assert match.group(1) not in found, f"{form}: repeated id {match.group(1)}"
            found[match.group(1)] = item
    return found


@pytest.mark.parametrize("readme", READMES)
def test_badge_row_shows_ci_license_and_python(readme):
    row = _badge_row(readme)
    assert f"{REPOSITORY}/actions/workflows/ci.yml/badge.svg?branch=main" in row
    assert "license-Apache--2.0" in row and "](LICENSE)" in row
    assert "img.shields.io/badge/python-" in row
    assert (ROOT / "LICENSE").is_file() and (ROOT / ".github" / "workflows" / "ci.yml").is_file()


def test_python_badge_names_the_versions_ci_tests_and_packages_declare():
    row = _badge_row("README.md")
    assert _badge_row("README.zh-CN.md") == row
    badge = set(re.search(r"badge/python-([0-9.%A-Z]+)-blue", row).group(1).split("%20%7C%20"))
    matrix = re.search(r'python-version: \[([^\]]+)\]', _read(ROOT / ".github" / "workflows" / "ci.yml")).group(1)
    assert badge == set(re.findall(r'"(3\.\d+)"', matrix))
    pyprojects = sorted((ROOT / "packages").glob("*/pyproject.toml"))
    assert pyprojects
    for pyproject in pyprojects:
        declared = set(re.findall(r"Programming Language :: Python :: (3\.\d+)", _read(pyproject)))
        assert badge == declared, pyproject


@pytest.mark.parametrize("form", FORMS)
def test_issue_forms_have_what_github_requires(form):
    text = _read(TEMPLATES / form)
    for key in ("name", "description"):
        assert re.search(rf"(?m)^{key}: \S", text), f"{form}: {key}"
    assert re.search(r"(?m)^body:$", text), form
    blocks = _blocks(form)
    assert blocks
    for name, block in blocks.items():
        assert re.search(r"(?m)^      label: \S", block), f"{form}: {name} has no label"
    assert "中文" in text and "Feel free to write in Chinese" in text
    assert "public" in text


def test_false_flag_form_asks_for_the_four_things_and_warns_about_private_text():
    blocks = _blocks("false_flag.yml")
    for name in ("claim", "source", "report", "truth"):
        assert "required: true" in blocks[name], name
    assert "Do not paste private text" in _read(TEMPLATES / "false_flag.yml")
    assert "required: true" in blocks["public-safe"]


def test_security_reports_point_to_the_policy():
    config = _read(TEMPLATES / "config.yml")
    assert f"url: {REPOSITORY}/blob/main/SECURITY.md" in config
    assert (ROOT / "SECURITY.md").is_file()


def test_pull_request_template_invites_chinese():
    text = _read(ROOT / ".github" / "pull_request_template.md")
    assert "中文" in text and "Feel free to write in Chinese" in text


@pytest.mark.parametrize("name", CONTRIBUTING)
def test_contributing_runs_the_dogfood_audit_ci_runs(name):
    workflow = _read(ROOT / ".github" / "workflows" / "ci.yml")
    block = re.search(r"exclude: \|\n((?: +\S+\n)+)", workflow).group(1)
    patterns = block.split()
    assert patterns
    command = next(line for line in _read(ROOT / name).splitlines() if line.startswith("memfalsify audit AGENTS.md"))
    assert "--evidence . " in command and command.endswith(" --strict")
    for pattern in patterns:
        assert f"--exclude '{pattern}'" in command, pattern


def test_contributing_gives_the_same_commands_in_both_languages():
    fence = re.compile(r"(?ms)^```bash\n(.*?)^```$")
    english, chinese = (fence.findall(_read(ROOT / name)) for name in CONTRIBUTING)
    assert english and english == chinese


@pytest.mark.parametrize("name", CONTRIBUTING + ("SECURITY.md",))
def test_contributor_docs_name_only_real_paths_and_links(name):
    text = _read(ROOT / name)
    links = [link.split("#", 1)[0] for link in re.findall(r"\]\(([^)\s]+)\)", text) if not link.startswith("https://")]
    named = re.findall(r"`((?:scripts|tests|docs|skills|packages)/[^`\s]*)`", text)
    assert links
    missing = [path for path in links + named if not (ROOT / path).exists()]
    assert not missing, missing
