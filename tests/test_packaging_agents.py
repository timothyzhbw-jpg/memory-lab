"""The Claude Code marketplace and plugin manifests that install Memory Lab into an agent.

`.claude-plugin/marketplace.json` lists one plugin whose root is `skills/`; `skills/.claude-plugin/plugin.json`
ships the repository's two skills and declares the read-only `memlab-mcp` server. The checks follow the
Claude Code documentation, which is cited at each rule:

- marketplace: https://code.claude.com/docs/en/plugins/marketplace-reference
- plugin.json: https://code.claude.com/docs/en/plugins/manifest-reference
- plugin MCP servers: https://code.claude.com/docs/en/mcp (section "Plugin-provided MCP servers")

``claude plugin validate`` is the authoritative check and needs the Claude Code CLI, so it is not run here.
These tests pin what this repository relies on, offline and on every platform.
"""
import json
import re
from pathlib import Path

import pytest
import typer
from typer.main import get_command

from memlab_mcp.server import app as memlab_mcp_app
from test_skills import parse_frontmatter

REPO = Path(__file__).resolve().parent.parent
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
SKILLS = REPO / "skills"

# marketplace-reference, "Top-level fields" and "Plugin entries".
MARKETPLACE_KEYS = {"$schema", "name", "owner", "plugins", "description", "version", "metadata",
                    "forceRemoveDeletedPlugins", "allowCrossMarketplaceDependenciesOn", "renames"}
OWNER_KEYS = {"name", "email", "url"}
# Entry fields this marketplace may use. Component fields (skills, mcpServers, ...) stay out of the entry:
# with a plugin.json present, entry mcpServers do not apply and other component fields are appended to it,
# so plugin.json is the one place that says what the plugin loads.
ENTRY_KEYS = {"name", "source", "description", "category", "tags", "displayName"}
# "Reserved names": official, community and directory names, plus names Claude Code uses itself.
RESERVED_MARKETPLACE_NAMES = {
    "claude-code-marketplace", "claude-code-plugins", "claude-plugins-official", "anthropic-marketplace",
    "anthropic-plugins", "agent-skills", "anthropic-agent-skills", "life-sciences", "knowledge-work-plugins",
    "claude-for-legal", "claude-for-financial-services", "financial-services-plugins", "first-party-plugins",
    "claude-tag-plugins", "claude-community", "claude-plugins-community", "healthcare",
    "anthropic-plugin-directory", "claude-plugin-directory", "inline", "builtin", "skills-dir", "synced",
    "claude-plugin-test", "npm", "pip", "uv", "cargo", "github", "gh"}
# "Plugin entries": letters, digits, ".", "_" and "-", starting with a letter or digit.
ID_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# manifest-reference, "Fields". Unknown keys are only a warning; the keys below are the ones this plugin ships.
# Anything else, including hooks, monitors, commands, agents and lspServers, would run or add behavior the
# marketplace entry does not declare.
PLUGIN_KEYS = {"name", "displayName", "description", "author", "homepage", "repository", "license", "keywords",
               "skills", "mcpServers"}
KEBAB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# manifest-reference, "name": names that pass as one of Anthropic's own fail validation.
RESERVED_PLUGIN_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-")
RESERVED_PLUGIN_NAMES = {"claude", "anthropic", "anthropics", "claude-code", "claude-mods"}
# manifest-reference, "Standard layout": what Claude Code loads from a plugin root without being told to.
# The skills are declared explicitly, so none of these may exist in the plugin root.
DEFAULT_COMPONENTS = ("skills", "commands", "agents", "hooks", "monitors", "output-styles", "themes",
                      "workflows", "bin", "settings.json", ".mcp.json", ".lsp.json")
# loading reference, "Node.js package dependencies": a package.json plus a lockfile makes Claude Code run
# a package manager when it copies the plugin. Neither file may exist, so install copies files only.
NODE_INSTALL_FILES = ("package.json", "package-lock.json", "npm-shrinkwrap.json", "bun.lock", "bun.lockb",
                      "pnpm-lock.yaml", "yarn.lock")
PROJECT_DIR = "${CLAUDE_PROJECT_DIR}"
PLACEHOLDER = re.compile(r"\$\{[^}]*\}")


def _load(path: Path) -> dict:
    def no_duplicates(pairs):
        keys = [key for key, _ in pairs]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        assert not duplicates, f"{path.name}: duplicate keys {duplicates}"
        return dict(pairs)

    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
    assert isinstance(data, dict), f"{path} must hold a JSON object"
    return data


def _relative(root: Path, value: object, what: str) -> Path:
    """Path rules from both references: a string starting with ./, forward slashes, no '..', inside root."""
    assert isinstance(value, str) and value.startswith("./"), f"{what}: {value!r} must start with ./"
    assert "\\" not in value, f"{what}: {value!r} must use forward slashes"
    assert ".." not in Path(value).parts, f"{what}: {value!r} must not contain .."
    path = root / value
    assert not path.is_symlink(), f"{what}: {value!r} must not be a symlink"
    assert path.resolve().is_relative_to(root.resolve()), f"{what}: {value!r} leaves {root}"
    assert path.exists(), f"{what}: {value!r} does not exist"
    return path


@pytest.fixture(scope="module")
def marketplace() -> dict:
    return _load(MARKETPLACE)


@pytest.fixture(scope="module")
def entry(marketplace) -> dict:
    plugins = marketplace["plugins"]
    assert isinstance(plugins, list) and len(plugins) == 1, "the marketplace lists exactly one plugin"
    return plugins[0]


@pytest.fixture(scope="module")
def plugin_root(entry) -> Path:
    return _relative(REPO, entry["source"], "plugins[0].source")


@pytest.fixture(scope="module")
def plugin(plugin_root) -> dict:
    return _load(plugin_root / ".claude-plugin" / "plugin.json")


@pytest.fixture(scope="module")
def server(plugin) -> dict:
    servers = plugin["mcpServers"]
    assert isinstance(servers, dict), "mcpServers is declared inline, not as a file path"
    assert list(servers) == ["memlab"], "the plugin declares one MCP server, memlab"
    return servers["memlab"]


def test_marketplace_fields(marketplace, entry):
    assert set(marketplace) <= MARKETPLACE_KEYS, set(marketplace) - MARKETPLACE_KEYS
    name = marketplace["name"]
    assert isinstance(name, str) and ID_PART.fullmatch(name) and ".." not in name
    assert name not in RESERVED_MARKETPLACE_NAMES and not name.startswith("claudeai-")
    assert isinstance(marketplace.get("description"), str) and marketplace["description"]
    owner = marketplace["owner"]
    assert isinstance(owner, dict) and set(owner) <= OWNER_KEYS
    assert isinstance(owner.get("name"), str) and owner["name"].strip()

    assert isinstance(entry, dict) and set(entry) <= ENTRY_KEYS, set(entry) - ENTRY_KEYS
    assert isinstance(entry["name"], str) and ID_PART.fullmatch(entry["name"])


def test_entry_and_manifest_agree(entry, plugin, plugin_root):
    # "Keep the entry name and the manifest name the same": the install id uses one, skill prefixes the other.
    assert entry["name"] == plugin["name"]
    assert plugin_root == SKILLS, "the plugin root is the repository's skills/ directory"
    assert (plugin_root / ".claude-plugin").is_dir() and not (plugin_root / ".claude-plugin").is_symlink()
    # No version anywhere: Claude Code then versions the plugin by commit, so users track this repository
    # instead of staying on a pinned string that nobody bumped.
    assert "version" not in entry and "version" not in plugin


def test_plugin_fields(plugin):
    assert set(plugin) == PLUGIN_KEYS, (set(plugin) - PLUGIN_KEYS, PLUGIN_KEYS - set(plugin))
    name = plugin["name"]
    assert isinstance(name, str) and KEBAB.fullmatch(name)
    assert name not in RESERVED_PLUGIN_NAMES and not name.startswith(RESERVED_PLUGIN_PREFIXES)
    assert isinstance(plugin["description"], str) and plugin["description"]
    assert isinstance(plugin["author"], dict) and plugin["author"].get("name")
    assert plugin["license"] == "Apache-2.0"
    for key in ("homepage", "repository"):
        assert re.fullmatch(r"https://[^\s]+", plugin[key]), f"{key} must be an https URL"
    keywords = plugin["keywords"]
    assert isinstance(keywords, list) and keywords
    assert all(isinstance(word, str) and word for word in keywords)


def test_plugin_ships_exactly_the_repository_skills(plugin, plugin_root):
    declared = plugin["skills"]
    assert isinstance(declared, list) and declared
    shipped = [_relative(plugin_root, value, "skills[]") for value in declared]
    repository_skills = sorted(path.parent for path in SKILLS.glob("*/SKILL.md"))
    assert sorted(shipped) == repository_skills
    for directory in shipped:
        meta, _ = parse_frontmatter((directory / "SKILL.md").read_text(encoding="utf-8"))
        assert meta["name"] == directory.name
    for name in DEFAULT_COMPONENTS:
        assert not (plugin_root / name).exists(), f"{name} in the plugin root would load on its own"
    for name in NODE_INSTALL_FILES:
        assert not (plugin_root / name).exists(), f"{name} would make Claude Code run a package manager on install"
    assert not (plugin_root / "SKILL.md").exists()


def test_memlab_server_is_the_read_only_console_script(server):
    # A stdio server with command and args only: no env (keys), no url, no transport override.
    assert set(server) == {"command", "args"}
    scripts = (REPO / "packages" / "memlab-mcp" / "pyproject.toml").read_text(encoding="utf-8")
    section = scripts.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    assert re.search(r'^memlab-mcp = "memlab_mcp\.server:main"$', section, re.MULTILINE)
    assert server["command"] == "memlab-mcp"
    args = server["args"]
    # Exactly one root, the project directory. No --allow-model-verifiers and no other flag.
    assert args == ["--root", PROJECT_DIR]
    assert [arg for arg in args if PLACEHOLDER.search(arg)] == [PROJECT_DIR]


def test_memlab_server_arguments_parse_without_model_verifiers(server, tmp_path):
    """Parse the declared arguments with memlab-mcp's own command line, as Claude Code would pass them."""
    args = [str(tmp_path) if arg == PROJECT_DIR else arg for arg in server["args"]]
    context = get_command(memlab_mcp_app).make_context("memlab-mcp", args)
    assert context.params["allow_model_verifiers"] is False
    assert Path(context.params["root"]) == tmp_path.resolve()


def test_unsubstituted_project_dir_fails_closed(server, tmp_path, monkeypatch):
    """A client that does not expand ${CLAUDE_PROJECT_DIR} gets no server, never a different root."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(typer.BadParameter, match="does not exist"):
        get_command(memlab_mcp_app).make_context("memlab-mcp", list(server["args"]))


def test_install_docs_use_the_install_id(marketplace, entry):
    install_id = f"{entry['name']}@{marketplace['name']}"
    for name in ("README.md", "README.zh-CN.md", "docs/install-agents.md", "docs/install-agents.zh-CN.md"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert f"claude plugin install {install_id}" in text, name


def _fences(text: str, lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", text, re.DOTALL)


def test_install_docs_agree_and_distinguish_session_install():
    """Shell install and in-session /plugin install are different commands.

    Install reference: in a session, /plugin install opens the details panel and does not install until
    a scope is chosen. Org plugin docs: project extraKnownMarketplaces apply only after workspace trust,
    and are ignored silently before that. The two languages keep the same commands.
    """
    english = (REPO / "docs/install-agents.md").read_text(encoding="utf-8")
    chinese = (REPO / "docs/install-agents.zh-CN.md").read_text(encoding="utf-8")
    assert _fences(english, "bash") == _fences(chinese, "bash")
    assert _fences(english, "toml") == _fences(chinese, "toml")
    assert "opens the plugin's details" in english and "it does not install by itself" in english
    assert "workspace trust dialog" in english and "ignores the marketplace entry silently" in english
    assert "do the same" not in english
    assert "打开插件详情" in chinese and "它自己不会完成安装" in chinese
    assert "工作区信任" in chinese and "静默忽略这条市场登记" in chinese
    assert "效果相同" not in chinese
