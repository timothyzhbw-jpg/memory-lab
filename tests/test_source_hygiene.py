"""No Python file holds a literal bidi, zero-width or other invisible control character.

Such characters make code read differently from how it runs ("Trojan Source", CVE-2021-42574): a bidi
override reorders what an editor shows, a zero-width character hides inside a name or a string, and a
line separator looks like a line break that Python does not see. Code and tests that need one write it
as an escape, such as ``"\\u202e"``.
"""
import os
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRUNE = {"build", "dist", "__pycache__", "node_modules"}
ALLOWED_CONTROLS = "\t\n\r\f"


def invisible(char: str) -> bool:
    """Format (bidi, zero-width, tag), line and paragraph separator, private-use, surrogate and control
    characters, except the ordinary whitespace controls."""
    category = unicodedata.category(char)
    return category in ("Cf", "Zl", "Zp", "Co", "Cs") or (category == "Cc" and char not in ALLOWED_CONTROLS)


def python_files() -> list[Path]:
    """Every .py file of the repository, without hidden directories (.git, virtualenvs) or build output."""
    found = []
    for directory, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d not in PRUNE and not d.endswith(".egg-info"))
        found += [Path(directory) / name for name in sorted(filenames) if name.endswith(".py")]
    return found


def test_python_files_hold_no_invisible_or_bidi_characters():
    files = python_files()
    assert len(files) > 50 and Path(__file__).resolve() in files
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.split("\n"), start=1):
            offenders += [f"{path.relative_to(ROOT).as_posix()}:{number}:{column}: U+{ord(char):04X} "
                          f"{unicodedata.name(char, 'unnamed')}"
                          for column, char in enumerate(line, start=1) if invisible(char)]
    assert not offenders, "write these characters as escapes (\\uXXXX):\n" + "\n".join(offenders)


def test_the_check_flags_trojan_source_characters():
    for char in ("\u202a", "\u202e", "\u2066", "\u2069", "\u061c", "\u200b", "\u200d", "\u2060", "\ufeff",
                 "\u2028", "\u2029", "\U000e0041", "\x1b", "\x00", "\x7f", "\x85"):
        assert invisible(char), f"U+{ord(char):04X}"
    for char in ("a", " ", "\t", "\n", "\r", "\f", "\u00a0", "\u4e2d", "\u00e9", "\u2014"):
        assert not invisible(char), f"U+{ord(char):04X}"
