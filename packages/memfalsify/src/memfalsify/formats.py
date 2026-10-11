"""Agent instruction file formats: which agent reads a file, and which frontmatter fields say where it applies.

Checked against each agent's documentation in October 2026:

- Claude Code (``CLAUDE.md``, ``CLAUDE.local.md``, ``.claude/CLAUDE.md``, ``.claude/rules/**/*.md`` with
  ``paths``): https://code.claude.com/docs/en/memory
- Codex and other ``AGENTS.md`` readers (``AGENTS.md``, ``AGENTS.override.md``, and ``.claude/AGENTS.md``,
  which Claude Code also loads):
  https://learn.chatgpt.com/docs/agent-configuration/agents-md
- Cursor (``.cursor/rules/**/*.mdc`` with ``description``, ``globs``, ``alwaysApply``; legacy
  ``.cursorrules``): https://cursor.com/docs/context/rules
- GitHub Copilot (``.github/copilot-instructions.md``; ``.github/instructions/**/*.instructions.md`` with
  ``applyTo``, ``excludeAgent``):
  https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions
- Gemini CLI (``GEMINI.md``, ``@file.md`` imports):
  https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md
- Windsurf, now Devin Desktop (``.windsurf/rules/*.md`` and ``.devin/rules/*.md`` with ``trigger``,
  ``globs``, ``description``; legacy ``.windsurfrules``): https://docs.devin.ai/desktop/cascade/memories
  (where https://docs.windsurf.com/windsurf/cascade/memories redirects)

Every format is Markdown (or plain text) once its frontmatter is removed, so all of them are split into claims
the same way. The frontmatter is never a claim; the fields a format documents are kept as claim metadata.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Union

Value = Union[str, list[str]]
MAX_VALUE = 300  # characters kept per frontmatter value (untrusted input; display only)
MAX_ITEMS = 50  # items kept per list value
BLOCK_SCALAR = re.compile(r"[|>][+-]?\d*")


@dataclass(frozen=True)
class MemoryFormat:
    name: str  # stable identifier, e.g. "cursor-rule"
    agent: str  # who reads it
    metadata: tuple[str, ...] = ()  # frontmatter fields recorded on each claim of the file


MARKDOWN = MemoryFormat("markdown", "any agent")
CLAUDE_MD = MemoryFormat("claude-md", "Claude Code")
CLAUDE_RULE = MemoryFormat("claude-rule", "Claude Code", ("paths",))
AGENTS_MD = MemoryFormat("agents-md", "Codex and other AGENTS.md readers")
CURSOR_RULE = MemoryFormat("cursor-rule", "Cursor", ("description", "globs", "alwaysApply"))
CURSORRULES = MemoryFormat("cursorrules", "Cursor (legacy)")
COPILOT = MemoryFormat("copilot-instructions", "GitHub Copilot")
COPILOT_PATH = MemoryFormat("copilot-path-instructions", "GitHub Copilot", ("applyTo", "excludeAgent"))
GEMINI_MD = MemoryFormat("gemini-md", "Gemini CLI")
WINDSURF_RULE = MemoryFormat("windsurf-rule", "Windsurf", ("trigger", "globs", "description"))
WINDSURFRULES = MemoryFormat("windsurfrules", "Windsurf (legacy)")
FORMATS = (CLAUDE_MD, CLAUDE_RULE, AGENTS_MD, CURSOR_RULE, CURSORRULES, COPILOT, COPILOT_PATH, GEMINI_MD,
           WINDSURF_RULE, WINDSURFRULES, MARKDOWN)

NAMES = {"CLAUDE.md": CLAUDE_MD, "CLAUDE.local.md": CLAUDE_MD, "AGENTS.md": AGENTS_MD,
         "AGENTS.override.md": AGENTS_MD, "GEMINI.md": GEMINI_MD, ".cursorrules": CURSORRULES,
         ".windsurfrules": WINDSURFRULES, "copilot-instructions.md": COPILOT}


def _under(directories: tuple[str, ...], hidden: str, child: str) -> bool:
    return any(a == hidden and b == child for a, b in zip(directories, directories[1:]))


def detect(path: str | PurePath) -> MemoryFormat:
    """The format of a file, from its name and the directories it sits in (exact, case-sensitive names).

    Anything else, such as ``MEMORY.md``, is plain Markdown."""
    parts = PurePath(path).parts
    if not parts:
        return MARKDOWN
    name, directories = parts[-1], parts[:-1]
    if name in NAMES:
        return NAMES[name]
    if name.endswith(".instructions.md"):
        return COPILOT_PATH
    if name.endswith(".mdc"):
        return CURSOR_RULE
    if name.endswith(".md"):
        if _under(directories, ".claude", "rules"):
            return CLAUDE_RULE
        if _under(directories, ".windsurf", "rules") or _under(directories, ".devin", "rules"):
            return WINDSURF_RULE
    return MARKDOWN


# --- frontmatter ----------------------------------------------------------------------------------------

def _cap(text: str) -> str:
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE] + "…"


def _scalar(text: str) -> str:
    """A YAML scalar as written: quotes removed, a trailing `` # comment`` dropped from an unquoted value.

    Values are not interpreted further: ``globs: **/*.ts`` (not valid YAML, but what the agents' own
    examples show) is kept as written."""
    text = text.strip()
    if text[:1] in ("'", '"'):
        end = text.find(text[0], 1)
        return text[1:end] if end > 0 else text
    return re.split(r"\s+#", text, maxsplit=1)[0].strip()


def _flow_items(text: str) -> list[str]:
    """Items of a flow list's inside (``a, "b,c", src/*.{ts,tsx}``); commas in quotes and braces stay."""
    items, current, quote, depth = [], [], None, 0
    for ch in text:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth <= 0:
            items.append("".join(current))
            current = []
            continue
        current.append(ch)
    items.append("".join(current))
    return [value for item in items if (value := _scalar(item))]


def frontmatter(lines: list[str]) -> tuple[int, dict[str, Value]]:
    """(index of the first line after the frontmatter, its top-level fields); (0, {}) without frontmatter.

    Frontmatter is a block between a first line ``---`` and the next ``---``. Only a small, tolerant subset of
    YAML is read: ``key: value``, quoted values, flow lists ``[a, b]``, block lists (``- item`` lines) and block
    scalars (``|`` or ``>``). Nested mappings are skipped. Values are strings, or lists of strings."""
    if not lines or lines[0].lstrip("﻿").strip() != "---":
        return 0, {}
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return 0, {}
    fields: dict[str, Value] = {}
    key, kind, parts = None, None, []  # kind: "open" (no value yet), "list" or "block"

    def close() -> None:
        if key is not None and kind == "list" and (items := [_cap(p) for p in parts if p][:MAX_ITEMS]):
            fields[key] = items
        elif key is not None and kind == "block" and (text := _cap(" ".join(parts))):
            fields[key] = text

    for raw in lines[1:end]:
        stripped = raw.strip()
        if not stripped or (stripped.startswith("#") and kind != "block"):
            continue
        indented = raw[:1] in (" ", "\t")
        if kind == "block" and indented:
            parts.append(stripped)
            continue
        if kind in ("open", "list") and (stripped == "-" or stripped.startswith("- ")):
            kind = "list"
            parts.append(_scalar(stripped[1:]))
            continue
        if indented:
            continue  # inside a nested mapping
        close()
        key, kind, parts = None, None, []
        name, colon, value = raw.partition(":")
        name = name.strip().strip("\"'")
        if not colon or not name:
            continue
        key, value = name, value.strip()
        if not value:
            kind = "open"
        elif BLOCK_SCALAR.fullmatch(value):
            kind = "block"
        elif value.startswith("["):
            inside = value[1:value.rfind("]")] if "]" in value else value[1:]
            if items := [_cap(item) for item in _flow_items(inside)][:MAX_ITEMS]:
                fields[key] = items
        elif text := _cap(_scalar(value)):
            fields[key] = text
    close()
    return end + 1, fields


def metadata_of(fmt: MemoryFormat, fields: dict[str, Value]) -> dict[str, Value]:
    """The frontmatter fields ``fmt`` documents, in the documented order."""
    return {name: fields[name] for name in fmt.metadata if name in fields}
