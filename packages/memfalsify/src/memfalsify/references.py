"""``missing_reference``: a path the memory names that exists nowhere under the evidence root.

The verifiers guess from words. This check does not: it takes the paths written in a memory line and looks them up
in a listing of the tree. Nothing from the memory ever reaches the filesystem: a path is only compared with names
the walk found, so ``../../etc/passwd`` opens nothing, and a path that leaves the root is not judged at all.

A path counts as missing only if all of this holds, because a wrong report sends a person to fix what is not broken:

- it has at least two segments, it is not a placeholder, a remote name, a MIME type, a branch or a glob, and its line
  does not say the path is gone, yet to be made or someone else's;
- its first segment is a directory at the root or next to the file that names it, so names relative to something
  unknown are left alone;
- it is not a suffix of anything the walk sees under the root, with any extension, so a monorepo's short paths and
  import paths pass, and no file the evidence scan skips (binary, over 2 MB, hidden) reads as missing;
- nothing on its way is a link to a directory, an empty directory (a submodule not checked out) or a directory the
  walk does not enter, since what lies below those cannot be known;
- git does not ignore it, so generated output passes. Git reads only its ignore files here (``--no-index``), with
  hooks and the file system monitor off and a time limit, so nothing from the repository runs. If git is absent or
  fails, a few well-known names of generated directories stand in for it.

A sparse checkout leaves tracked files off the disk, so nothing is called missing in one.
"""
from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import time
import unicodedata
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from itertools import dropwhile
from pathlib import Path

from memcore.display import one_line
from memcore.paths import SKIP_DIRS
from memcore.reading import confinement_root, current_read_limits, walk_files

from .gitstore import _env
from .memory import Claim

KIND = "missing_reference"
MAX_PATH = 400  # characters; anything longer is not a path somebody wrote by hand
GIT_SECONDS = 15

# Never entered: git's own files, dependencies and tool caches. Their size says nothing about the project, and a
# path below them is local state.
NOT_ENTERED = frozenset({".git", ".hg", ".svn", ".venv", ".tox", ".nox", ".cache", ".mypy_cache", ".pytest_cache",
                         ".ruff_cache", ".gradle", ".yarn", ".pnpm-store", ".next", ".nuxt", ".turbo", ".terraform",
                         ".eggs", "bower_components", *SKIP_DIRS})
# Stand-ins for git's ignore rules when git cannot be asked.
GENERATED = frozenset({"build", "dist", "out", "target", "coverage", "bin", "obj", "tmp", "temp", "logs", "cache",
                       "generated", "gen", "_build", "site"})
GIT_NAMES = frozenset({"origin", "upstream", "refs", "heads", "head", "remotes"})
SCRIPT_FAMILY = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
# Two segments with neither an extension nor a slash read as many things (`json/jsonc`, `Core/Button`,
# `actions/checkout`). They count only under a directory that holds code by convention.
SOURCE_DIRS = frozenset({"src", "lib", "libs", "test", "tests", "spec", "specs", "packages", "apps", "crates", "cmd",
                         "pkg", "internal", "scripts", "tools", "docs", "doc", "examples", "modules", "services",
                         "components", "plugins", "extensions", "include", "third_party"})
MIME_ALWAYS = frozenset({"application", "multipart"})
MIME_BARE = frozenset({"audio", "font", "image", "message", "model", "text", "video"})

_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)([^`]+)\1(?!`)")
_LINK_TARGET = re.compile(r"!?\[[^\]\[]*\]\(([^)\s]{1,400})(?:\s+\"[^\"]*\")?\)")  # bounded: a line can be hostile
# Where in a file, added to its path by habit: src/a.py:12, src/a.py:12:3, src/a.py:12-20, src/a.py#L12, a.py::test_b
_POSITION = re.compile(r"(?:::[\w.:\[\]-]+|:\d+(?:[:-]\d+)*|#[\w.-]*)$")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_NOT_A_PATH = re.compile(r"[\s<>{}$%*?\[\]()|&;=!'\"^#,:~+\\`]")
_VERSIONED = re.compile(r"@[\w.^~-]*$")  # actions/checkout@v4, lodash/fp@4
_DIGITS = re.compile(r"\d+")
_VERSION_SEGMENT = re.compile(r"v\d+", re.IGNORECASE)  # api/v1/eval
_EXTENSION = re.compile(r"\.[^\W\d]\w*$")  # not gemini-1.5-pro, gpt-4.1 or 3.11
_PATH_TO = re.compile(r"(?:^|/)path/to(?:/|$)", re.IGNORECASE)  # test/path/to/test.ts
# A source directory and a name that every project has: docs use src/app.py as an example of a path.
_GENERIC_NAME = re.compile(r"^(?:app|main|index|utils?|helpers?|tests?|module|script|config)\.", re.IGNORECASE)
# A segment that stands for something, not a name: nodes/YourService/, test_action_EventNameHere.py, snake_case.py
_PLACEHOLDER_ANYWHERE = re.compile(
    r"^(?:foo|bar|baz|qux|quux|xxx+|yyy+|zzz+|nnn+|n+|x+|placeholder|snake_case|camelcase|pascalcase|kebab-case|"
    r"your|my|some|here|myservice|mycomponent|myplugin|myfile|myfeature|mytool|myrepo|mylib)$", re.IGNORECASE)
_PLACEHOLDER_LAST = re.compile(r"^(?:example|examples|sample|dummy|name|names|filename|file|files|thing|whatever|"
                               r"something|mymodule|mypackage|myproject|myapp|yourname)$", re.IGNORECASE)
_PLACEHOLDER_PART = re.compile(
    r"^(?:your|my|some|specific|example|sample|dummy|foo|baz)[-_]"  # your-file, my_module, specific_test
    r"|^(?:Your|My(?!S[Qq][Ll])|Some|Example|Sample|Dummy|Foo|Baz)[A-Z0-9]"  # YourService, ExampleTest
    r"|[-_](?:example|here|foo|bar|baz)$"  # file_here, name-example
    r"|(?:Here|Example|Foo)(?:Name)?$"  # EventNameHere, HereName
    r"|^Here[A-Z]"
    r"|N{3,}|X{3,}|(?:^|[-_.])[xX]{2,}(?:$|[-_.])"  # 0001-NNN, ADR-XXX, test_xx
    r"|YYYY|yyyy")  # vYYYY_MM_DD.py
_PLACEHOLDER_WORD = re.compile(r"(?:^|[-_.])(?:NAME|FILE|PATH|MODULE|COMPONENT|SERVICE|PROVIDER|PACKAGE|FEATURE|PLUGIN|"
                               r"DIR|TYPE|ID|VERSION|MAJOR|MINOR|PATCH)(?:$|[-_.])")  # PLUGIN_NAME, 0.MINOR.x.md
# Words on the line, outside code spans, that change what a path means there.
_LINE_WORDS = {
    "remote": re.compile(r"\b(?:actions?|repos?|repository|repositories|github|gitlab|forks?|clones?|upstream|"
                         r"organi[sz]ations?|orgs?|owner|workflows?|marketplace)\b|仓库|上游|派生", re.IGNORECASE),
    "branch": re.compile(r"\b(?:branch|branches|branching|checkout|rebase|cherry-pick|tags?|prs?)\b|分支",
                         re.IGNORECASE),
    "web": re.compile(r"\b(?:endpoints?|routes?|urls?|uris?|https?|apis?|webhooks?|requests?)\b|接口|路由|端点|请求",
                      re.IGNORECASE),
    # "in the Turborepo source repository", "inside that installed package"; not "in the repo" or "in this repository"
    "elsewhere": re.compile(r"\b(?:in|from|of|inside|within)\s+(?:(?:the|its|their)\s+(?!same\b)[\w-]+\s+|"
                            r"(?:that|another|an?)\s+)(?:[\w-]+\s+){0,2}?"
                            r"(?:repo|repository|package|project|fork|checkout|clone|submodule)\b(?!\s+root)"
                            r"|(?:另一个|其他|其它|别的|外部|第三方|上游)的?(?:仓库|项目|包)", re.IGNORECASE),
    "gone": re.compile(r"\b(?:removed|deleted|renamed|deprecated|no longer|formerly|previously|used to be|was moved|"
                       r"moved from|moved to|migrated from|replaced by|does not exist|doesn't exist)\b"
                       r"|已?被?(?:删除|移除)|已?(?:改名|重命名)|不再|已?(?:废弃|弃用)|已?迁移|搬到|不存在", re.IGNORECASE),
    "made": re.compile(r"\b(?:(?:re-?)?creat(?:e|es|ed|ing)|(?:re-?)?generat(?:e|es|ed|ing)|new file|will be|will live|"
                       r"would be|write to|writes|written to|save to|saves to|saved to|scaffolds?|outputs?|produces?|"
                       r"move to|ignored|gitignored|untracked|not committed|if (?:it )?exists?|if present|when present|"
                       r"if any|optional(?:ly)?|e\.g|for example|such as|"
                       # "Do not add `src/shim.py`": the path is not meant to exist. Bounded: a line can be hostile.
                       r"(?:never|not|don['’]t|avoid)\b[\s:*_,]{0,8}(?:\w{1,30}\s{1,8}){0,2}?"
                       r"(?:(?:re-?)?(?:add|adding|introduce|introducing)|commit|committing|restore|restoring)|"
                       r"there (?:must|should) not be|there (?:must|should) be no|(?:must|should) not exist)\b"
                       r"|创建|新建|重建|生成|写入|写到|输出|保存到|如果存在|若存在|如有|可选|例如|比如|诸如|这样的"
                       r"|(?:不要|不能|不得|不可|别|禁止|切勿|勿|避免)(?:再|重新)?(?:添加|新增|增加|加入|引入|提交|恢复)"
                       r"|不应该?(?:存在|有)",
                       re.IGNORECASE),
}
_POSSESSIVE = re.compile(r"\w['’]s\s*$")  # Prettier's `src/main/core.js`
_OTHER_REPO_AFTER = re.compile(r"^[\s*_)]*(?:in|from|at)\s+(?:the\s+)?[\w.-]+/[\w.-]+")  # `a/b` in oven-sh/WebKit
_NEAR = 80  # characters around a path that can say whose it is
# An inline command says what it makes: those paths need not exist yet.
_SHELL_JOINS = frozenset({"&&", "||", "|", "&", ";"})
_REDIRECT = re.compile(r"\d*>>?|&>>?|>\|")
_MAKES_EVERY_ARGUMENT = frozenset({"mkdir", "touch", "tee"})
_MAKES_LAST_ARGUMENT = frozenset({"mv", "cp", "ln"})
_OUTPUT_FLAGS = frozenset({"-o", "--output", "--out", "--outfile", "--out-file", "--output-file", "--outdir", "--out-dir",
                           "--output-dir", "--output-path", "--target-dir"})
_PROBE = "memfalsify-probe"  # a name git can always answer about


def _said(raw: str) -> frozenset[str]:
    """Which of ``_LINE_WORDS`` a raw line uses."""
    prose = _CODE_SPAN.sub(" ", raw)
    return frozenset(name for name, words in _LINE_WORDS.items() if words.search(prose))


@dataclass(frozen=True)
class Reference:
    """One path as a memory line writes it."""
    written: str  # as written, without a position such as :12; shown in the report
    path: str  # POSIX, without ./ and without a trailing slash; may start with ../
    directory: bool  # it ended in a slash
    said: frozenset[str] = frozenset()  # see ``_said``
    foreign: bool = False  # the words next to it give it to someone else
    whole: str | None = None  # a word of a code span: the span read as one path, which may name a file with a space

    @property
    def bare(self) -> bool:
        """Neither an extension nor a trailing slash says this is a path."""
        return not self.directory and _EXTENSION.search(self.path.rpartition("/")[2].strip(".")) is None


def _key(name: str) -> str:
    """A name as compared: the same on every platform, and generous. If a path differs from a file only by case or
    Unicode composition, then on macOS and Windows it opens that file, so it is not reported on Linux either."""
    return name.lower() if name.isascii() else unicodedata.normalize("NFC", name).casefold()


def _clean(text: str) -> tuple[str, str] | None:
    """``text`` without a position, as written and with forward slashes; None if it cannot be a relative path."""
    if len(text) > MAX_PATH:
        return None
    written = _POSITION.sub("", text.strip()).rstrip(".,;:。，；：、")
    if (not written or "\x00" in written or "://" in written or _DRIVE.match(written)
            or written[0] in "/~-@\\" or written.startswith("...")):
        return None
    candidate = written
    if "\\" in written:
        # A Windows path, not an escape or a regular expression: it has to read as a file or a directory.
        if "/" in written or not re.search(r"\.\w+$|\\$", written):
            return None
        candidate = written.replace("\\", "/")
    # Backing up through a name: that name may be a link, and then ``normpath`` would lead somewhere else.
    if ".." in dropwhile(lambda part: part in (".", ".."), candidate.split("/")):
        return None
    return written, candidate


def parse_reference(text: str, said: frozenset[str] = frozenset(), foreign: bool = False,
                    whole: str | None = None) -> Reference | None:
    """``text`` as a relative path, or None if it does not read as one."""
    cleaned = _clean(text)
    if cleaned is None:
        return None
    written, candidate = cleaned
    if _NOT_A_PATH.search(candidate) or _VERSIONED.search(candidate):
        return None
    path = posixpath.normpath(candidate)
    return None if path in (".", "..") else Reference(written, path, candidate.endswith("/"), said, foreign, whole)


def _whole_span(span: str) -> str | None:
    """A code span of several words read as one path, since a file name can hold a space. It is only ever compared
    with names from the walk, so any character will do; None if it cannot be a path."""
    cleaned = _clean(span)
    return None if cleaned is None or "/" not in cleaned[1] else posixpath.normpath(cleaned[1])


def _made_by_simple_command(words: list[str]) -> set[str]:
    made = {after for word, after in zip(words, words[1:]) if word in _OUTPUT_FLAGS or _REDIRECT.fullmatch(word)}
    at = 0
    while at < len(words) and (words[at] == "sudo" or "=" in words[at]):  # FLAG=1 sudo mkdir x
        at += 1
    at += words[at:at + 1] == ["git"]
    tool = words[at] if at < len(words) else ""
    arguments = [word for word in words[at + 1:] if not word.startswith("-")]
    if tool in _MAKES_EVERY_ARGUMENT:
        made.update(arguments)
    elif tool in _MAKES_LAST_ARGUMENT:
        made.update(arguments[-1:])
    return made


def _made_by_command(words: list[str]) -> set[str]:
    """The words of an inline command that name what it makes: ``mkdir -p a/b/``, ``git mv a b``, ``make > a/b.txt``,
    ``--output a/b.md``. Without the semicolon that may end one."""
    made: set[str] = set()
    simple: list[str] = []
    for word in words:
        if word not in _SHELL_JOINS:
            simple.append(word.rstrip(";"))
        if word in _SHELL_JOINS or word.endswith(";"):
            made |= _made_by_simple_command(simple)
            simple = []
    return made | _made_by_simple_command(simple)


def _line_references(raw: str) -> tuple[list[int], list[Reference]]:
    """Every path a raw line writes in inline code or as a link target, in order, and the offset each starts at.
    Inside a code span of several words (a command), only words that say they are paths count: an extension or a
    trailing slash, and not what the command makes. The whole line at once, since a sentence can end inside a
    command."""
    said = _said(raw)

    def foreign(match: re.Match) -> bool:
        at, past = match.span()
        return bool(_POSSESSIVE.search(raw, max(0, at - _NEAR), at) or _OTHER_REPO_AFTER.match(raw[past:past + _NEAR]))

    found: list[tuple[int, Reference]] = []
    for match in _CODE_SPAN.finditer(raw):
        words = match.group(2).split()
        whose = foreign(match)
        if len(words) == 1:
            parsed = [parse_reference(words[0], said, whose)]
        else:
            whole, made = _whole_span(match.group(2)), _made_by_command(words)
            parsed = [parse_reference(word, said, whose, whole) for word in words if word.rstrip(";") not in made]
        found += [(match.start(), ref) for ref in parsed if ref is not None and (len(words) == 1 or not ref.bare)]
    for match in _LINK_TARGET.finditer(raw):
        if ref := parse_reference(match.group(1).split("#")[0], said, foreign(match)):
            found.append((match.start(), ref))
    found.sort(key=lambda item: item[0])
    return [at for at, _ in found], [ref for _, ref in found]


def references_in(raw: str, start: int = 0, end: int | None = None,
                  lines: dict[str, tuple[list[int], list[Reference]]] | None = None) -> list[Reference]:
    """The paths that start in ``raw[start:end]``, each once, in order. ``lines`` holds the lines read so far: a hostile
    line can hold very many paths and be asked about for each of them, in as many copies as the file likes."""
    parsed = None if lines is None else lines.get(raw)
    if parsed is None:
        parsed = _line_references(raw)
        if lines is not None:
            lines[raw] = parsed
    offsets, references = parsed
    high = len(offsets) if end is None else bisect_left(offsets, end)
    return list({(ref.written, ref.whole): ref for ref in references[bisect_left(offsets, start):high]}.values())


def _placeholder(segment: str, last: bool) -> bool:
    stem = (segment.partition(".")[0] or segment).removeprefix("test_")  # test_foo.py says no more than foo.py
    if _PLACEHOLDER_ANYWHERE.match(stem) or _PLACEHOLDER_PART.search(stem) or _PLACEHOLDER_WORD.search(segment):
        return True
    return last and _PLACEHOLDER_LAST.match(stem) is not None


def noise(ref: Reference) -> str | None:
    """Why this path is not worth looking up, judging from the path and its line alone; None if it is."""
    parts = ref.path.split("/")
    named = [part for part in parts if part != ".."]
    if len(named) < 2:
        return "one segment"
    if "..." in ref.path:
        return "ellipsis"  # src/..., chart/templates/...yaml
    if any(part.lower() in NOT_ENTERED for part in parts):  # src/.CACHE/a.py opens src/.cache/a.py on macOS
        return "not entered"
    if all(_DIGITS.fullmatch(part) for part in named):
        return "numbers"
    first = parts[0].lower()
    if len(parts) == 2 and not ref.directory and (first in MIME_ALWAYS or (first in MIME_BARE and ref.bare)):
        return "mime type"
    if first in GIT_NAMES:
        return "git name"
    if _PATH_TO.search(ref.path) or any(_placeholder(part, at == len(named) - 1) for at, part in enumerate(named)):
        return "placeholder"
    if len(named) == 2 and named[0] in SOURCE_DIRS and _GENERIC_NAME.match(named[1]):
        return "placeholder"
    if ref.bare:
        if "." in named[-1].strip("."):
            return "version in the name"  # models/gemini-1.5-pro, python/3.11
        if len(named) == 2 and (named[0] not in SOURCE_DIRS or "remote" in ref.said):
            return "two bare segments"
        if "branch" in ref.said:
            return "branch name"
        if "web" in ref.said or any(_VERSION_SEGMENT.fullmatch(part) for part in named):
            return "web route"
    if ref.foreign or "elsewhere" in ref.said:
        return "someone else's"
    if "gone" in ref.said:
        return "said to be gone"
    if "made" in ref.said:
        return "said to be made"
    return None


def _backwards(directory: str) -> str:
    """A directory written back to front: a sorted list of these answers "which end with this?" by bisection."""
    return ("/" + directory)[::-1]


def _any_ends_with(backwards: list[str], head: str) -> bool:
    """Whether a directory in this sorted list of ``_backwards`` is ``head``, or ends with it on a segment boundary."""
    wanted = _backwards(head)
    at = bisect_left(backwards, wanted)
    return at < len(backwards) and backwards[at].startswith(wanted)


@dataclass
class Tree:
    """What a walk of the root found, kept only for the names the references ask about."""
    wanted: set[str]
    places: dict[str, set[str]] = field(default_factory=dict)  # wanted key -> directories holding it, as keys
    folders: dict[str, set[str]] = field(default_factory=dict)  # exact directory name -> directories holding it
    opaque: set[str] = field(default_factory=set)  # directories whose contents cannot be known, as keys
    _places_backwards: dict[str, list[str]] = field(default_factory=dict)  # built when first asked, after the walk

    def note(self, directory: str, name: str) -> None:
        key = _key(name)
        while True:
            # a.test.ts answers for a.test.ts, a.test and a: an import path leaves the extension out.
            if key in self.wanted:
                self.places.setdefault(key, set()).add(directory)
            cut = key.rfind(".", 1)
            if cut < 0:
                return
            key = key[:cut]

    def note_folder(self, directory: str, name: str) -> None:
        self.note(directory, name)
        if _key(name) in self.wanted:
            self.folders.setdefault(name, set()).add(directory)

    def holds_folder(self, directory: str, name: str) -> bool:
        """Whether the exact directory (a POSIX path, "" for the root) holds a directory called exactly ``name``."""
        return _key(directory) in self.folders.get(name, ())

    def _has_suffix(self, path: str) -> bool:
        head, _, name = _key(path).rpartition("/")
        if name not in self._places_backwards:
            self._places_backwards[name] = sorted(map(_backwards, self.places.get(name, ())))
        return _any_ends_with(self._places_backwards[name], head)

    def answers(self, path: str) -> bool:
        """Whether ``path`` is the end of something in the tree, on a segment boundary, with any extension. A script's
        extensions count as one, since compilers and imports treat them so."""
        if self._has_suffix(path):
            return True
        for suffix in SCRIPT_FAMILY:
            if path.lower().endswith(suffix):
                return any(self._has_suffix(path[:-len(suffix)] + other) for other in SCRIPT_FAMILY)
        return False

    @cached_property
    def _opaque_backwards(self) -> list[str]:
        return sorted(map(_backwards, self.opaque))

    def unknowable(self, path: str, anchors: Iterable[str]) -> bool:
        """Whether ``path`` runs through a directory whose contents cannot be known: read from one of ``anchors``, or,
        as a monorepo's short path, from anywhere when two or more of its segments lead there. One segment alone says
        little: an empty ``test/fake/src`` has nothing to do with ``src/gone.py``."""
        if not self.opaque:
            return False
        parts = _key(path).split("/")
        ways = ["/".join(parts[:count]) for count in range(1, len(parts) + 1)]
        if any((f"{_key(anchor)}/{way}" if anchor else way) in self.opaque for anchor in anchors for way in ways):
            return True
        return any(_any_ends_with(self._opaque_backwards, way) for way in ways[1:])


def _wanted_names(path: str) -> set[str]:
    names = {_key(part) for part in path.split("/")}
    last = path.rpartition("/")[2]
    for suffix in SCRIPT_FAMILY:
        if last.lower().endswith(suffix):
            names |= {_key(last[:-len(suffix)] + other) for other in SCRIPT_FAMILY}
    return names


def walk_tree(root: Path, wanted: set[str]) -> Tree | None:
    """A listing of ``root``, with what the evidence scan skips and what ``--exclude`` leaves out, since an excluded
    file still exists. Only names are read, and no link is followed. None if some directory cannot be listed: then
    nothing can be called missing."""
    tree = Tree(wanted)
    start = str(root)
    failed: list[OSError] = []
    for directory, dirnames, filenames in walk_files(root, onerror=failed.append):
        relative = str(directory)[len(start):].strip(os.sep).replace(os.sep, "/")
        here = _key(relative)
        if relative and not dirnames and not filenames:
            tree.opaque.add(here)  # git keeps no empty directory: a submodule not fetched
        keep = []
        for name in dirnames:
            tree.note_folder(here, name)
            if name in NOT_ENTERED:
                continue
            if os.path.islink(os.path.join(str(directory), name)):
                tree.opaque.add(f"{here}/{_key(name)}" if here else _key(name))
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            tree.note(here, name)
    return None if failed else tree


def _trusted_git(root: Path) -> str | None:
    """Git from PATH, unless it lies in the evidence or the current directory. Windows looks in the current directory
    first, and a repository can hold a ``git.exe``."""
    exe = shutil.which("git")
    if exe is None:
        return None
    try:
        place = Path(exe).resolve()
        return None if place.is_relative_to(root) or place.parent == Path.cwd().resolve() else exe
    except (OSError, RuntimeError):
        return None


def _git_without_hooks(exe: str, root: Path, *args: str) -> list[str]:
    # Not GIT_OPTIONS: check-ignore refuses --literal-pathspecs.
    return [exe, "-C", str(root), "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", *args]


def git_ignored(root: Path, paths: Iterable[str]) -> set[str] | None:
    """Which of ``paths`` (relative to ``root``) git ignores, or None if git cannot say.

    ``--no-index`` keeps git from reading the index, which is what could start ``core.fsmonitor``, and from fetching
    anything into a partial clone. No hook belongs to this command. A time limit covers an ignore file that is a
    FIFO. Inside confined reads (the MCP server), git would read files the confinement cannot see, so it is not asked.

    A leading ``./`` keeps a directory named ``:(icase)pkg`` from reading as pathspec magic. Git stops at a path it
    refuses (one below a link to a file), and ``--verbose --non-matching`` shows which that was, since every path gets
    an answer: nothing can be ignored there, so the rest are asked again without it. The first name is one git can
    always answer about, so no answer at all means git itself failed. The time limit covers all the asking."""
    exe = None if confinement_root() is not None else _trusted_git(root)
    if exe is None:
        return None
    args = _git_without_hooks(exe, root, "check-ignore", "--no-index", "--stdin", "-z", "--verbose", "--non-matching")
    names = list(dict.fromkeys(paths))
    ignored: set[str] = set()
    deadline = time.monotonic() + GIT_SECONDS
    while True:
        question = "".join(f"./{name}\0" for name in (_PROBE, *names)).encode("utf-8", "surrogateescape")
        try:
            done = subprocess.run(args, input=question, capture_output=True, env={**_env(), "GIT_NO_LAZY_FETCH": "1"},
                                  timeout=max(deadline - time.monotonic(), 0.001), check=False)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        fields = done.stdout.split(b"\0")
        # Four fields each: source, line number, pattern, path. A pattern that starts with ! says not ignored.
        answers = list(zip(fields[2::4], fields[3::4]))
        ignored |= {path[2:].decode("utf-8", "surrogateescape") for pattern, path in answers
                    if pattern and not pattern.startswith(b"!")}
        if done.returncode not in (0, 1) and not answers:
            return None
        names = names[len(answers):]  # the probe stands in for the one git refused, if it did
        if done.returncode in (0, 1) or not names:
            return ignored


def sparse_checkout(root: Path) -> bool:
    """Whether git says tracked files may be left out of this working tree. Asked and hardened as in ``git_ignored``;
    if git cannot say, the answer is no."""
    exe = None if confinement_root() is not None else _trusted_git(root)
    if exe is None:
        return False
    try:
        done = subprocess.run(_git_without_hooks(exe, root, "config", "--type=bool", "--get", "core.sparseCheckout"),
                              capture_output=True, env=_env(), timeout=GIT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return done.returncode == 0 and done.stdout.strip() == b"true"


@dataclass(frozen=True)
class _Question:
    """A path to look up: where it leads from the root, and the directories that can vouch for its first segment."""
    claim: str
    ref: Reference
    path: str
    anchors: tuple[str, ...]
    whole: str | None  # from the root: the code span it is a word of, read as one path

    def still_open(self, tree: Tree) -> bool:
        """Whether the walk leaves this path unexplained. The whole span goes first: a word means nothing if the span
        names something, or may name something below a directory that cannot be known."""
        return not any(tree.answers(way) or tree.unknowable(way, self.anchors)
                       for way in (self.whole, self.path) if way is not None
                       ) and any(tree.holds_folder(anchor, self.path.split("/")[0]) for anchor in self.anchors)


def _anchors(path: str, prefix: str) -> tuple[str, ...]:
    """The root, the directory of the memory file and the directory of the file that states a claim, at ``path``; POSIX
    paths relative to the root, "" for the root. ``prefix`` is the directory ``path`` is relative to."""
    own = posixpath.normpath(posixpath.join(prefix or ".", posixpath.dirname(path) or "."))
    return tuple(dict.fromkeys(("", prefix, "" if own == "." else own)))


def _from_root(path: str, anchors: tuple[str, ...]) -> str | None:
    """Where ``path`` leads, relative to the root; None if it leaves the root. Only ``..`` depends on the anchor."""
    if not path.startswith("../"):
        return path
    joined = posixpath.normpath(posixpath.join(anchors[-1], path))
    return None if joined == ".." or joined.startswith("../") else joined


def _ignore_names(question: _Question) -> list[str]:
    """The names to ask git about: the path from the root and from the file stating it, also as a directory."""
    names = [question.path]
    if not question.ref.path.startswith("../"):
        names += [f"{anchor}/{question.path}" for anchor in question.anchors if anchor]
    return names + [name + "/" for name in names]


def missing_references(claims: Iterable[Claim], root: Path, prefix: str = "") -> dict[str, list[str]]:
    """{claim ID: the paths it names that exist nowhere under ``root``, as written}, in the claims' order.

    ``root`` is resolved. ``prefix`` is the directory ``Claim.path`` is relative to, as a POSIX path relative to
    ``root``. A claim parsed by anything but ``parse_text`` has no raw line, and so no paths. A claim stated several
    times is read in each place, since only one may have its path in code, and each place from its own directory."""
    if current_read_limits() is not None:
        return {}  # a budgeted run has no walk to spare, and a cut-off walk would call existing files missing
    questions: list[_Question] = []
    wanted: set[str] = set()
    lines: dict[str, tuple[list[int], list[Reference]]] = {}  # for this call only: lines can be hostile, servers live long
    for claim in claims:
        if claim._raw is None:
            continue
        for stated_at, source in ((claim.path, claim._raw), *claim._raw_copies):
            anchors = _anchors(stated_at, prefix)
            for ref in references_in(*source, lines=lines):
                path = None if noise(ref) else _from_root(ref.path, anchors)
                if path is not None:
                    whole = None if ref.whole is None else _from_root(ref.whole, anchors)
                    questions.append(_Question(claim.id, ref, path, anchors, whole))
                    wanted |= _wanted_names(path) | (_wanted_names(whole) if whole is not None else set())
    if not questions:
        return {}
    tree = walk_tree(root, wanted)
    if tree is None:
        return {}
    open_questions = [q for q in questions if q.still_open(tree)]
    if not open_questions or sparse_checkout(root):
        return {}
    ignored = git_ignored(root, (name for q in open_questions for name in _ignore_names(q)))
    found: dict[str, dict[str, None]] = {}
    for question in open_questions:
        if ignored is None:
            if any(part.lower() in GENERATED for part in question.path.split("/")[:-1]):
                continue
        elif not ignored.isdisjoint(_ignore_names(question)):
            continue
        found.setdefault(question.claim, {})[question.ref.written] = None
    return {claim: list(written) for claim, written in found.items()}


def detail(written: str) -> str:
    """The text of the issue. The path is untrusted, and this text may be printed by more than our own reports."""
    return f"'{one_line(written)}' is named here, but nothing under the evidence root matches it"
