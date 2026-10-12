"""Agent instruction formats: detection, frontmatter metadata, @-imports, and claims/audit/diff/history on
Cursor, Copilot, Gemini CLI, Windsurf and Claude Code files (fixtures in tests/fixtures/formats)."""
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memfalsify.audit import audit
from memfalsify.cli import app
from memfalsify.formats import MAX_VALUE, detect, frontmatter
from memfalsify.history import diff_to_markdown, history_to_markdown, memory_diff, memory_history
from memfalsify.memory import parse_memory, parse_text
from memfalsify.report import to_markdown

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "formats"
AS_OF = date(2026, 10, 10)
runner = CliRunner()


@pytest.fixture
def formats(tmp_path):
    return Path(shutil.copytree(FIXTURE, tmp_path / "formats"))


# --- detection and frontmatter -------------------------------------------------------------------

@pytest.mark.parametrize("path, name", [
    ("CLAUDE.md", "claude-md"), ("pkg/CLAUDE.local.md", "claude-md"), (".claude/CLAUDE.md", "claude-md"),
    (".claude/rules/api.md", "claude-rule"), ("/home/u/.claude/rules/deep/x.md", "claude-rule"),
    ("AGENTS.md", "agents-md"), ("pkg/AGENTS.override.md", "agents-md"), ("GEMINI.md", "gemini-md"),
    (".cursor/rules/api.mdc", "cursor-rule"), ("pkg/.cursor/rules/a/b.mdc", "cursor-rule"),
    (".cursorrules", "cursorrules"), (".github/copilot-instructions.md", "copilot-instructions"),
    (".github/instructions/py.instructions.md", "copilot-path-instructions"),
    (".windsurf/rules/t.md", "windsurf-rule"), (".devin/rules/t.md", "windsurf-rule"),
    (".windsurfrules", "windsurfrules"),
    # Plain Markdown: memory files, look-alikes and a .md file Cursor ignores in .cursor/rules.
    ("MEMORY.md", "markdown"), ("docs/rules/x.md", "markdown"), ("claude.md", "markdown"),
    (".cursor/rules/x.md", "markdown"), (".claude/x.md", "markdown"), ("rules/.windsurf/x.md", "markdown"),
])
def test_detect(path, name):
    assert detect(path).name == name


def test_frontmatter_reads_the_yaml_subset_the_agents_document():
    lines = ["---",
             "description: \"Rules: naming\" # comment",
             "globs: **/*.test.ts",  # not valid YAML; kept as written, as the agents' examples show
             "alwaysApply: false",
             "applyTo: '**/*.py,scripts/**'",
             "paths:",
             "  - \"src/api/**/*.ts\"",
             "  - lib/**/*.{ts,tsx}",
             "flow: [\"a,b\", src/*.{ts,tsx}, 'c']",
             "folded: >-",
             "  When deploying",
             "  the service",
             "nested:",
             "  key: ignored",
             "empty:",
             "# a comment",
             "---",
             "- The first claim."]
    end, fields = frontmatter(lines)
    assert end == 17
    assert fields == {"description": "Rules: naming", "globs": "**/*.test.ts", "alwaysApply": "false",
                      "applyTo": "**/*.py,scripts/**", "paths": ["src/api/**/*.ts", "lib/**/*.{ts,tsx}"],
                      "flow": ["a,b", "src/*.{ts,tsx}", "c"], "folded": "When deploying the service"}


@pytest.mark.parametrize("lines", [[], ["no frontmatter"], ["---", "a: b"], ["--- x", "a: b", "---"]])
def test_without_frontmatter_nothing_is_skipped(lines):
    assert frontmatter(lines) == (0, {})


def test_frontmatter_tolerates_a_bom_and_caps_values():
    end, fields = frontmatter(["\ufeff---", "globs: " + "x" * 1000, "paths: [" + ", ".join("p" for _ in range(80)) + "]",
                               "---"])
    assert end == 4
    assert fields["globs"] == "x" * MAX_VALUE + "…" and len(fields["paths"]) == 50


def test_only_documented_fields_become_metadata():
    text = "---\ndescription: d\nglobs: src/**\nalwaysApply: true\nowner: someone\n---\n- The cache uses Redis.\n"
    [rule] = parse_text(text, "api.mdc", detect(".cursor/rules/api.mdc"))[0]
    assert rule.metadata == {"description": "d", "globs": "src/**", "alwaysApply": "true"}
    [plain] = parse_text(text, "MEMORY.md", detect("MEMORY.md"))[0]
    assert plain.metadata == {} and plain.line == 7  # frontmatter is still skipped, and counted in line numbers


# --- every fixture format ------------------------------------------------------------------------

CURSOR_API = {"description": "Rate limits and validation for the fixture API", "globs": "src/api/**/*.ts",
              "alwaysApply": "false"}


@pytest.mark.parametrize("relative, expected, metadata", [
    (".cursor/rules/api.mdc", [("api.mdc:9", "The public API rate limit is 100 requests per minute."),
                               ("api.mdc:10", "Validate request bodies with a schema before using them.")], CURSOR_API),
    (".cursor/rules/frontend/components.mdc", [("components.mdc:9", "Fixture components use PascalCase file names.")],
     {"description": "Component conventions: naming, props",
      "globs": ["src/components/**/*.{ts,tsx}", "src/ui/**"], "alwaysApply": "true"}),
    ("packages/web/.cursor/rules/vue.mdc", [("vue.mdc:7", "Fixture Vue components use the composition API.")],
     {"globs": "*.vue"}),
    (".cursorrules", [(".cursorrules:3", "The cache uses Redis for sessions.")], {}),
    (".github/copilot-instructions.md",
     [("copilot-instructions.md:3", "The main database runs PostgreSQL 16.")], {}),
    (".github/instructions/python.instructions.md",
     [("python.instructions.md:8", "Python code uses four spaces for indentation.")],
     {"applyTo": "**/*.py,scripts/**", "excludeAgent": "code-review"}),
    (".windsurf/rules/testing.md", [("testing.md:8", "Fixture test files mock external API calls.")],
     {"trigger": "glob", "globs": "**/*.test.ts"}),
    (".devin/rules/deploy.md", [("deploy.md:10", "Fixture deployments go through the staging cluster.")],
     {"trigger": "model_decision", "description": "When deploying the fixture service"}),
    (".windsurfrules", [(".windsurfrules:3", "The queue runs on Kafka.")], {}),
    (".claude/rules/api.md",
     [("api.md:9", "Fixture API handlers return errors as objects with code and message fields.")],
     {"paths": ["src/api/**/*.ts", "lib/**/*.{ts,tsx}"]}),
    ("packages/web/AGENTS.md", [("AGENTS.md:3", "The fixture web package builds with Vite.")], {}),
])
def test_each_format_is_split_into_located_claims(relative, expected, metadata):
    claims, files = parse_memory(FIXTURE / relative, follow_links=False)
    assert [(c.location, c.text) for c in claims] == expected
    assert all(c.metadata == metadata for c in claims)
    assert files == [(FIXTURE / relative).resolve()]


def test_imports_are_followed_like_links():
    claims, files = parse_memory(FIXTURE / "CLAUDE.md")
    assert [(c.location, c.text) for c in claims] == [
        ("CLAUDE.md:5", "The main database runs PostgreSQL 15."),
        ("AGENTS.md:3", "The public API rate limit is 100 requests per minute."),
        ("AGENTS.md:4", "Run the fixture test suite with make test before every commit.")]
    assert [f.name for f in files] == ["CLAUDE.md", "AGENTS.md"]
    claims, files = parse_memory(FIXTURE / "GEMINI.md")
    assert [c.location for c in claims] == ["GEMINI.md:5", "GEMINI.md:6", "docs/style.md:1"]
    assert [f.relative_to(FIXTURE).as_posix() for f in files] == ["GEMINI.md", "docs/style.md"]
    assert [c.location for c in parse_memory(FIXTURE / "GEMINI.md", follow_links=False)[0]] == [
        "GEMINI.md:5", "GEMINI.md:6"]


@pytest.mark.parametrize("line, targets", [
    ("@AGENTS.md", ["AGENTS.md"]),
    ("See @./docs/a.md and @b.md.", ["./docs/a.md", "b.md"]),
    ("(@notes/x.md)", []),  # an import follows whitespace or starts the line
    ("Mail ops@example.md about it.", []),
    ("Not in code: `@x.md`, nor ``@y.md``.", []),
    ("@README for the overview.", []),  # only .md files are followed
    ("@https://example.com/x.md", []),
])
def test_import_syntax(line, targets):
    claims, links = parse_text(line + "\n", "CLAUDE.md")
    assert links == targets
    assert bool(claims) == (not targets)  # a line that imports is an index entry, not a claim


def test_audit_cites_frontmatter_and_locates_claims(formats):
    report = audit(formats / ".cursor" / "rules" / "api.mdc", formats / "docs", as_of=AS_OF)
    found = {f.text: f for f in report.findings}
    limit = found["The public API rate limit is 100 requests per minute."]
    assert limit.status == "candidate_counterevidence" and limit.location == "api.mdc:9"
    assert limit.metadata == CURSOR_API
    assert limit.evidence[0].span.location == "api.md:1"
    markdown = to_markdown(report)
    assert ("`api.mdc:9` · `Fixture: Cursor rule for memfalsify format tests (not instructions for this repository)`"
            "\n\nFrontmatter: `description: Rate limits and validation for the fixture API` · "
            "`globs: src/api/**/*.ts` · `alwaysApply: false`\n") in markdown
    assert json.loads(report.model_dump_json())["findings"][0]["metadata"] == CURSOR_API


def test_instruction_paths_and_headings_stay_inert(tmp_path):
    name = "a`[x](evil).mdc"
    rule = tmp_path / ".cursor" / "rules" / name
    rule.parent.mkdir(parents=True)
    rule.write_bytes(b"---\ndescription: scope\n---\n# See [phish](http://evil) and a `tick`\n\n"
                     b"- The cache uses Redis.\n")
    markdown = to_markdown(audit(rule, as_of=AS_OF))
    memory = next(line for line in markdown.splitlines() if line.startswith("- Memory: "))
    assert memory.startswith("- Memory: ``") and name in memory
    assert "``a`[x](evil).mdc:6``" in markdown
    # The heading ends in a backtick, so the span is padded and cannot stay open.
    assert "`` See [phish](http://evil) and a `tick` ``" in markdown
    assert "\n[phish](http://evil)" not in markdown and "\n[x](evil)" not in markdown


def test_frontmatter_text_is_shown_safely(tmp_path):
    rule = tmp_path / ".cursor" / "rules" / "x.mdc"
    rule.parent.mkdir(parents=True)
    rule.write_bytes("---\ndescription: a `tick` and \x1b[31mred\n---\n- The cache uses Redis.\n".encode("utf-8"))
    markdown = to_markdown(audit(rule, as_of=AS_OF))
    assert "Frontmatter: ``description: a `tick` and \\u001b[31mred``" in markdown and "\x1b" not in markdown


def test_claims_cli_adds_metadata_only_when_present():
    result = runner.invoke(app, ["claims", str(FIXTURE / ".github" / "instructions" / "python.instructions.md")])
    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "memory_id": parse_memory(FIXTURE / ".github" / "instructions" / "python.instructions.md")[0][0].id,
        "location": "python.instructions.md:8", "text": "Python code uses four spaces for indentation.",
        "metadata": {"applyTo": "**/*.py,scripts/**", "excludeAgent": "code-review"}}
    rows = [json.loads(line) for line in runner.invoke(app, ["claims", str(FIXTURE / "AGENTS.md")]).output.splitlines()]
    assert rows and all(set(row) == {"memory_id", "location", "text"} for row in rows)


# --- diff and history on a rule file -------------------------------------------------------------

RULE = ".cursor/rules/api.mdc"
RULE_V1 = ("---\nglobs: src/api/**\nalwaysApply: false\n---\n\n"
           "- The public API rate limit is 100 requests per minute.\n- Handlers validate request bodies.\n")
RULE_V2 = ("---\nglobs: src/**\nalwaysApply: false\n---\n\n"
           "- The public API rate limit is 100 requests per minute.\n"
           "- Handlers validate request bodies with a schema.\n- Errors are JSON objects with a code field.\n")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_diff_and_history_follow_a_rule_file_and_its_frontmatter(git_repo):
    base = git_repo.commit("add rule", {RULE: RULE_V1,
                                        "docs/api.md": "The public API rate limit is 60 requests per minute.\n"})
    head = git_repo.commit("widen rule", {RULE: RULE_V2})
    report = memory_diff(git_repo.root / RULE, base, evidence_root=git_repo.root / "docs", as_of=AS_OF)
    assert report.summary == {"added": 1, "changed": 1, "removed": 0, "unchanged": 1}
    changes = {c.change: c for c in report.changes}
    assert (changes["changed"].old.location, changes["changed"].new.location) == ("api.mdc:7", "api.mdc:7")
    assert changes["added"].new.location == "api.mdc:8"
    unchanged = changes["unchanged"]
    assert unchanged.old.metadata == {"globs": "src/api/**", "alwaysApply": "false"}
    assert unchanged.new.metadata == {"globs": "src/**", "alwaysApply": "false"}
    markdown = diff_to_markdown(report)
    assert "## Frontmatter changed" in markdown
    assert (f"- `{unchanged.new.memory_id}` at `api.mdc:6`: Frontmatter: `globs: src/api/**` · "
            "`alwaysApply: false` → `globs: src/**` · `alwaysApply: false`") in markdown
    history = memory_history(git_repo.root / RULE)
    by_text = {c.text: c for c in history.claims}
    limit = by_text["The public API rate limit is 100 requests per minute."]
    assert limit.introduced.commit == base and limit.last_changed.commit == head  # the frontmatter changed
    assert limit.metadata == {"globs": "src/**", "alwaysApply": "false"} and limit.location == "api.mdc:6"
    assert "Frontmatter: `globs: src/**` · `alwaysApply: false`" in history_to_markdown(history)
    result = runner.invoke(app, ["history", str(git_repo.root / RULE), "--format", "json"])
    assert result.exit_code == 0 and json.loads(result.output)["claims"][0]["metadata"]["globs"] == "src/**"
