"""The publish workflow cannot publish on its own, and what it uploads is what scripts/dists.py checked.

The workflow file is read as text: this suite carries no YAML parser, and tests/test_action.py reads workflows the
same way. Its pins and the absence of expressions inside run scripts are checked in tests/test_action.py too.
"""
import importlib.util
import io
import json
import re
import tarfile
import zipfile
from pathlib import Path

import pytest

from conftest import write_text_exact

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: tomli is a test dependency there (CI, setup-local.sh)
    import tomli as tomllib
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
CODE = "\n".join(line.split("  # ", 1)[0] for line in WORKFLOW.splitlines() if not line.lstrip().startswith("#"))
PYPI_ACTION = "pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dists = _load("dists")
rehearse = _load("rehearse_install")


def _block(text, key, indent):
    """The lines under a mapping key at this indent, up to the next key at that indent or less."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line == " " * indent + key + ":")
    found = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        found.append(line)
    return "\n".join(found)


def _jobs():
    return {name: _block(_block(CODE, "jobs", 0), name, 2) for name in ("build", "publish", "dry-run")}


def _keys(block, indent):
    return re.findall(rf"^ {{{indent}}}([\w-]+):", block, re.MULTILINE)


# --- it cannot publish on its own ----------------------------------------------------------------------------

def test_the_only_trigger_is_a_person_pressing_the_button():
    assert _keys(_block(CODE, "on", 0), 2) == ["workflow_dispatch"]
    assert _keys(CODE, 0) == ["name", "on", "concurrency", "permissions", "jobs"]


def test_every_input_must_be_chosen_and_the_default_uploads_nothing():
    inputs = _block(_block(_block(CODE, "on", 0), "workflow_dispatch", 2), "inputs", 4)
    assert _keys(inputs, 6) == ["confirm", "target", "stop-after"]
    for name in _keys(inputs, 6):
        assert "required: true" in _block(inputs, name, 6), name
    confirm = _block(inputs, "confirm", 6)
    assert "type: string" in confirm and "default:" not in confirm  # the version is typed out, every time
    target = _block(inputs, "target", 6)
    assert "default: build-only" in target and re.findall(r"- ([\w-]+)$", target, re.MULTILINE) == \
        ["build-only", "testpypi", "pypi"]
    assert "default: memlab" in _block(inputs, "stop-after", 6)


def test_uploads_run_only_on_the_right_target_after_the_build():
    jobs = _jobs()
    assert list(jobs) == _keys(_block(CODE, "jobs", 0), 2)  # no fourth job can upload
    assert "needs: build" in jobs["publish"] and "needs: build" in jobs["dry-run"]
    assert "if: inputs.target == 'pypi' && github.ref == 'refs/heads/main'" in jobs["publish"]
    assert "environment: pypi\n" in jobs["publish"] + "\n"
    assert "if: inputs.target == 'testpypi'" in jobs["dry-run"]
    assert "environment: testpypi\n" in jobs["dry-run"] + "\n"
    assert "gh-action-pypi-publish" not in jobs["build"]


def test_a_real_upload_from_another_branch_fails_loudly_instead_of_skipping():
    first = _jobs()["build"].split("- uses:")[0]
    assert "if: inputs.target == 'pypi' && github.ref != 'refs/heads/main'" in first and "exit 1" in first


def test_the_confirm_input_reaches_the_check_through_the_environment():
    build = _jobs()["build"]
    assert "CONFIRM: ${{ inputs.confirm }}" in build
    assert 'run: python scripts/dists.py check dist --confirm="$CONFIRM"' in build
    # The artifact is uploaded only after the check, so a wrong version stops the run before the upload jobs start.
    assert build.index("scripts/dists.py check") < build.index("actions/upload-artifact")
    assert build.index("twine check --strict") < build.index("actions/upload-artifact")
    # Nothing can skip the checks or carry on past them; the one condition is the refusal in the first step.
    assert "continue-on-error" not in CODE and build.count("if:") == 1
    assert "if-no-files-found: error" in build


def test_it_holds_no_credentials_and_mints_a_token_only_where_it_uploads():
    assert "secrets" not in CODE and "password" not in CODE and "pull_request" not in CODE
    assert re.search(r"^permissions:\n  contents: read\n", CODE, re.MULTILINE)
    jobs = _jobs()
    assert "id-token" not in jobs["build"]
    assert "contents: read" in _block(jobs["build"], "permissions", 4)
    for name in ("publish", "dry-run"):
        assert _block(jobs[name], "permissions", 4).strip() == "id-token: write", name


def test_upload_jobs_run_nothing_from_the_repository():
    """Code running beside an id-token could upload anything: these jobs only unpack the artifact and upload it."""
    for name in ("publish", "dry-run"):
        job = _jobs()[name]
        assert "run:" not in job and "checkout" not in job and "setup-python" not in job, name
        uses = re.findall(r"uses: (\S+)", job)
        assert uses[0].startswith("actions/download-artifact@") and set(uses[1:]) == {PYPI_ACTION}, name


@pytest.mark.parametrize("name", ["ci.yml", "publish.yml"])
def test_one_line_commands_stay_valid_yaml(name):
    """``run: pip --only-binary :all: -r x`` looks fine and is not YAML: ``: `` ends a plain scalar there."""
    for line in (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8").splitlines():
        if re.match(r"\s*(- )?run: [^|>'\"]", line):
            assert ": " not in line.split("run: ", 1)[1], line


def test_checkouts_keep_no_credentials():
    uses = re.findall(r"^( +)- uses: actions/checkout@\S+\n +with:\n +persist-credentials: false$", CODE, re.MULTILINE)
    assert len(uses) == len(re.findall(r"actions/checkout@", CODE)) >= 1


def test_nothing_here_uploads_outside_the_workflow():
    for path in [ROOT / "scripts" / "dists.py", ROOT / "scripts" / "rehearse_install.py",
                 ROOT / ".github" / "workflows" / "ci.yml"]:
        text = path.read_text(encoding="utf-8")
        assert "twine upload" not in text and "gh-action-pypi-publish" not in text, path


def test_builds_use_the_locked_tools():
    build = _jobs()["build"]
    assert "--require-hashes --only-binary :all: -r scripts/publish-requirements.txt" in build
    source = (ROOT / "scripts" / "dists.py").read_text(encoding="utf-8")
    assert '"--no-isolation"' in source  # otherwise the build backend comes from the index, unpinned
    assert '"--require-hashes"' in (ROOT / "scripts" / "rehearse_install.py").read_text(encoding="utf-8")


def test_the_build_tools_lock_is_exact_and_hashed():
    text = re.sub(r"\s*\\\n\s*", " ", (ROOT / "scripts" / "publish-requirements.txt").read_text(encoding="utf-8"))
    entries = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert entries
    for entry in entries:
        assert re.match(r"^[A-Za-z0-9][\w.-]*==[^\s;]+( ; .*?)? --hash=sha256:[0-9a-f]{64}", entry), entry[:100]
    names = {re.split(r"==", entry, maxsplit=1)[0].lower() for entry in entries}
    assert {"build", "twine", "setuptools", "readme-renderer", "packaging"} <= names
    script = (ROOT / "scripts" / "update-action-lock.sh").read_text(encoding="utf-8")
    assert "scripts/publish-requirements.in -o scripts/publish-requirements.txt" in script


# --- upload order --------------------------------------------------------------------------------------------

def test_uploads_follow_the_dependency_order():
    order = list(dists.PACKAGES)
    assert order[0] == "memlab-core" and order[-1] == "memlab"
    for job in ("publish", "dry-run"):
        assert re.findall(r"packages-dir: dist/([\w-]+)", _jobs()[job]) == order, job
    position = {name: index for index, name in enumerate(order)}
    for name, directory in dists.PACKAGES.items():
        project = tomllib.loads((ROOT / "packages" / directory / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        needed = project.get("dependencies", []) + [r for e in project.get("optional-dependencies", {}).values() for r in e]
        for requirement in map(Requirement, needed):
            if requirement.name in position and requirement.name != name:
                assert position[requirement.name] < position[name], (name, requirement.name)


def test_the_two_upload_jobs_upload_the_same_things():
    jobs = _jobs()
    steps = jobs["publish"].split("    steps:\n", 1)[1].strip()
    dry = jobs["dry-run"].split("    steps:\n", 1)[1].replace("          repository-url: https://test.pypi.org/legacy/\n", "")
    assert dry.strip() == steps
    assert "repository-url" not in jobs["publish"]
    assert jobs["dry-run"].count("repository-url: https://test.pypi.org/legacy/") == len(dists.PACKAGES)
    assert steps.count("skip-existing: true") == len(dists.PACKAGES)


@pytest.mark.parametrize("job", ["publish", "dry-run"])
def test_stop_after_uploads_up_to_the_package_it_names(job):
    """The first publication goes one package per run (docs/publishing.md), so every package must be a stopping point."""
    steps = re.findall(r"- name: ([\w-]+)\n(?: +if: ([^\n]+)\n)? +uses: pypa", _jobs()[job])
    order = list(dists.PACKAGES)
    assert [name for name, _ in steps] == order
    inputs = _block(_block(_block(CODE, "on", 0), "workflow_dispatch", 2), "inputs", 4)
    assert sorted(re.findall(r"- ([\w-]+)$", _block(inputs, "stop-after", 6), re.MULTILINE)) == sorted(order)
    for index, choice in enumerate(order):
        ran = []
        for name, condition in steps:
            python = condition.replace("&&", "and").replace("||", "or").replace("inputs.stop-after", repr(choice))
            if not condition or eval(python):  # only string comparisons, from this repository's own workflow
                ran.append(name)
        assert ran == order[:index + 1], choice


# --- scripts/dists.py check ----------------------------------------------------------------------------------

def _versions():
    return dists.expected_versions()


def _metadata(name, version, requires=(), readme="# Readme\n"):
    lines = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}",
             "Description-Content-Type: text/markdown"] + [f"Requires-Dist: {r}" for r in requires]
    return ("\n".join(lines) + "\n\n" + readme).encode("utf-8")


def _write_dist(out, name, version, *, requires=(), readme="# Readme\n", wheel_version=None, sdist_version=None,
                wheel_requires=None):
    """A wheel and an sdist holding what the real ones hold: only metadata and the required files."""
    directory = out / name
    directory.mkdir(parents=True)
    stem = name.replace("-", "_")
    module = dists.MODULES.get(name)
    with zipfile.ZipFile(directory / f"{stem}-{version}-py3-none-any.whl", "w") as archive:
        info = f"{stem}-{version}.dist-info"
        archive.writestr(f"{info}/METADATA", _metadata(name, wheel_version or version,
                                                       requires if wheel_requires is None else wheel_requires, readme))
        archive.writestr(f"{info}/licenses/LICENSE", b"license\n")
        if module:
            archive.writestr(f"{module}/py.typed", b"")
        for image in dists.IMAGES.get(name, ()):
            archive.writestr(image, b"png")
    with tarfile.open(directory / f"{stem}-{version}.tar.gz", "w:gz") as archive:
        top = f"{stem}-{version}"
        files = {"PKG-INFO": _metadata(name, sdist_version or version, requires, readme), "LICENSE": b"license\n",
                 "README.md": readme.encode("utf-8"), "pyproject.toml": b"[project]\n"}
        if module:
            files[f"src/{module}/py.typed"] = b""
        for image in dists.IMAGES.get(name, ()):
            files[f"src/{image}"] = b"png"
        for path, body in files.items():
            entry = tarfile.TarInfo(f"{top}/{path}")
            entry.size = len(body)
            archive.addfile(entry, io.BytesIO(body))


@pytest.fixture
def built(tmp_path):
    out = tmp_path / "dist"
    versions = _versions()
    requires = {"memfalsify": [f"memlab-core<9,>={versions['memlab-core']}"],
                "memlab": [f"memfalsify<9,>={versions['memfalsify']}", 'memfalsify[llm]<9; extra == "llm"']}
    for name, version in versions.items():
        _write_dist(out, name, version, requires=requires.get(name, ()))
    return out


def _release():
    return _versions()["memlab"]


def test_check_passes_files_that_match(built, capsys):
    assert dists.check(built, _release(), environ={}) == 0
    assert capsys.readouterr().out.count(": ok\n") == 6


def test_check_writes_the_built_files_to_the_job_summary(built, tmp_path):
    summary = tmp_path / "summary.md"
    write_text_exact(summary, "before\n")
    assert dists.check(built, _release(), environ={"GITHUB_STEP_SUMMARY": str(summary)}) == 0
    text = summary.read_bytes().decode("utf-8")
    assert text.startswith("before\n## Built files\n") and "\r" not in text
    version = _release()
    assert f"`memlab-{version}-py3-none-any.whl`" in text and len(re.findall(r"`[0-9a-f]{64}`", text)) == 12


@pytest.mark.parametrize("confirm", ["9.9.9", "", "v0.5.0", "0.5.0 ", "0.5.0\n::error::forged", "-x", "\x1b[2J"])
def test_check_refuses_a_confirm_that_differs_and_never_prints_it(built, tmp_path, capsys, confirm):
    summary = tmp_path / "summary.md"
    write_text_exact(summary, "")
    assert dists.check(built, confirm, environ={"GITHUB_STEP_SUMMARY": str(summary)}) == 1
    out = capsys.readouterr().out
    assert "refused: the confirm input" in out and "forged" not in out and "\x1b" not in out
    assert summary.read_bytes() == b""  # nothing that reads like a result
    for line in out.splitlines():
        assert line.startswith("dists: ")


def test_check_refuses_a_version_that_differs_from_the_checkout(tmp_path, capsys):
    out = tmp_path / "dist"
    for name, version in _versions().items():
        _write_dist(out, name, "9.9.9" if name == "memfalsify" else version)
    assert dists.check(out, _release(), environ={}) == 1
    assert "memfalsify: the wheel holds version 9.9.9" in capsys.readouterr().out


def test_check_refuses_when_only_the_wheel_holds_another_version(tmp_path, capsys):
    out = tmp_path / "dist"
    versions = _versions()
    for name, version in versions.items():
        _write_dist(out, name, version, wheel_version="8.8.8" if name == "orgmemory" else None)
    assert dists.check(out, _release(), environ={}) == 1
    assert "orgmemory: the wheel holds version 8.8.8" in capsys.readouterr().out


@pytest.mark.parametrize("damage, message", [
    (lambda out: [f.unlink() for f in (out / "memfalsify").glob("*.whl")], "memfalsify: expected one wheel and one sdist"),
    (lambda out: [f.unlink() for f in (out / "memlab-core").glob("*.tar.gz")], "memlab-core: expected one wheel and one sdist"),
    (lambda out: (out / "orgmemory" / "orgmemory-9.9.9-py3-none-any.whl").write_bytes(b"x"), "orgmemory: expected one wheel"),
    (lambda out: (out / "memlab-ui" / "notes.txt").write_bytes(b"x"), "memlab-ui: unexpected file notes.txt"),
    (lambda out: (out / "strays").mkdir(), "unexpected entry in the output directory: strays"),
    (lambda out: [f.write_bytes(b"not a wheel") for f in (out / "memlab-mcp").glob("*.whl")], "memlab-mcp: the wheel"),
])
def test_check_refuses_a_missing_extra_or_broken_file(built, capsys, damage, message):
    damage(built)
    assert dists.check(built, _release(), environ={}) == 1
    assert message in capsys.readouterr().out


def test_check_refuses_a_pin_that_excludes_a_built_version(tmp_path, capsys):
    out = tmp_path / "dist"
    for name, version in _versions().items():
        _write_dist(out, name, version, requires=["memlab-core>=99"] if name == "memfalsify" else ())
    assert dists.check(out, _release(), environ={}) == 1
    assert "memfalsify: its pin memlab-core>=99 excludes the built memlab-core" in capsys.readouterr().out


def test_check_reads_the_pins_in_the_wheel_as_well_as_the_sdist(tmp_path, capsys):
    out = tmp_path / "dist"
    for name, version in _versions().items():
        _write_dist(out, name, version, wheel_requires=["memlab-core>=99"] if name == "memfalsify" else None)
    assert dists.check(out, _release(), environ={}) == 1
    assert "memfalsify: its pin memlab-core>=99 excludes the built memlab-core" in capsys.readouterr().out


def test_check_refuses_a_relative_readme_link(tmp_path, capsys):
    out = tmp_path / "dist"
    for name, version in _versions().items():
        _write_dist(out, name, version, readme="See [docs](docs/mcp.md).\n" if name == "memlab-mcp" else "# Readme\n")
    assert dists.check(out, _release(), environ={}) == 1
    assert "memlab-mcp: the README link docs/mcp.md would break on PyPI" in capsys.readouterr().out


def test_check_keeps_hostile_metadata_on_one_prefixed_line(tmp_path, capsys):
    out = tmp_path / "dist"
    for name, version in _versions().items():
        _write_dist(out, name, version, readme="[a](x\x1b[2Jy)\n" if name == "orgmemory" else "# Readme\n")
    assert dists.check(out, _release(), environ={}) == 1
    printed = capsys.readouterr().out
    assert "\x1b" not in printed and "\\u001b" in printed
    assert all(line.startswith("dists: ") for line in printed.splitlines())


def test_check_refuses_modules_in_the_meta_wheel_and_a_missing_image(tmp_path, capsys):
    out = tmp_path / "dist"
    for name, version in _versions().items():
        _write_dist(out, name, version)
    wheel = next((out / "memlab").glob("*.whl"))
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("memlab/__init__.py", b"")
    wheel = next((out / "memlab-ui").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        kept = {n: archive.read(n) for n in archive.namelist() if not n.endswith("ui-dark.png")}
    wheel.write_bytes(b"")
    with zipfile.ZipFile(wheel, "w") as archive:
        for entry, body in kept.items():
            archive.writestr(entry, body)
    assert dists.check(out, _release(), environ={}) == 1
    printed = capsys.readouterr().out
    assert "memlab: the wheel should hold no modules" in printed
    assert "memlab-ui: the wheel holds no memlab_ui/static/ui-dark.png" in printed


def test_relative_links_finds_reference_style_targets():
    assert dists.relative_links("See [docs][d] and [site][s].\n\n[d]: docs/a.md\n[s]: https://x.example\n") == ["docs/a.md"]


def test_relative_links_finds_markdown_and_html_forms():
    assert dists.relative_links('[a](docs/a.md) ![b](b.png "t") <img src="c.png"> <a href="d.html">') == \
        ["docs/a.md", "b.png", "c.png", "d.html"]
    assert dists.relative_links("[a](https://x.example/a) [b](#here) <img src='https://x.example/c.png'>") == []


# --- scripts/rehearse_install.py -----------------------------------------------------------------------------

def _installed(name, url):
    return {"metadata": {"name": name}, "download_info": {"url": url}}


def test_rehearsal_proves_nothing_of_ours_came_from_the_index():
    good = {"install": [_installed("typer", "https://files.pythonhosted.org/packages/typer.whl")]}
    assert rehearse.wrongly_sourced(json.loads(json.dumps(good))) == []
    assert rehearse.OWN == tuple(dists.PACKAGES)
    bad = {"install": [_installed("memfalsify", "https://files.pythonhosted.org/packages/memfalsify.whl"),
                       _installed("memlab_core", "https://files.pythonhosted.org/packages/memlab_core.whl"),
                       _installed("typer", "file:///tmp/typer.whl"),
                       _installed("pydantic", "https://files.pythonhosted.org/packages/pydantic.whl")]}
    assert rehearse.wrongly_sourced(bad) == ["memfalsify", "memlab-core", "typer"]


@pytest.mark.parametrize("extras, needed", [
    ((), ["memlab-core", "memfalsify", "orgmemory", "memlab"]),
    (("llm",), ["memlab-core", "memfalsify", "orgmemory", "memlab"]),
    (("ui",), ["memlab-core", "memfalsify", "orgmemory", "memlab-ui", "memlab"]),
    (("ui", "mcp"), ["memlab-core", "memfalsify", "orgmemory", "memlab-mcp", "memlab-ui", "memlab"]),
])
def test_rehearsal_installs_the_wheels_the_built_metadata_asks_for(tmp_path, extras, needed):
    out = tmp_path / "dist"
    requires = {"memlab": ["memfalsify<9,>=0", "orgmemory<9,>=0", 'memlab-ui<9,>=0; extra == "ui"',
                               'memlab-mcp<9,>=0; extra == "mcp"', 'memfalsify[llm]<9,>=0; extra == "llm"',
                               'pydantic>=2; extra == "llm"'],
                "memfalsify": ["memlab-core<9,>=0", "typer>=0", 'memfalsify[llm]; extra == "all"'],
                "orgmemory": ["memlab-core<9,>=0"], "memlab-ui": ["memfalsify<9,>=0", "orgmemory<9,>=0"],
                "memlab-mcp": ["memfalsify<9,>=0", "orgmemory<9,>=0", "mcp<3,>=2"]}
    for name, version in _versions().items():
        _write_dist(out, name, version, requires=requires.get(name, ()))
    assert [wheel.parent.name for wheel in dists.closure(out, dists.RELEASE, extras)] == needed


# --- docs/publishing ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["docs/publishing.md", "docs/publishing.zh-CN.md"])
def test_publishing_docs_spell_out_what_the_owner_types(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    for needed in ("timothyzhbw-jpg", "`memlab`", "`publish.yml`", "`pypi`", "`testpypi`", "stop-after",
                   "https://pypi.org/manage/account/publishing/", "https://pypi.org/account/register/",
                   "https://test.pypi.org/manage/account/publishing/", "memorylab",
                   # PyPI's own messages for the two things that stop a first publication
                   "too similar to an existing project", "already been registered for a different project name",
                   "Non-user identities cannot create new projects"):
        assert needed in text, needed
    rounds = re.findall(r"^\| \d \| `([\w-]+)` \| `([\w-]+)` \|$", text, re.MULTILINE)
    assert rounds == [(package, package) for package in dists.PACKAGES]  # one package per round, in upload order
    assert "docs/publishing" in WORKFLOW


def test_publishing_docs_agree_on_their_commands():
    fences = [re.findall(r"```(?:bash|text)\n(.*?)```", (ROOT / name).read_text(encoding="utf-8"), re.DOTALL)
              for name in ("docs/publishing.md", "docs/publishing.zh-CN.md")]
    assert fences[0] == fences[1] and fences[0]
