"""Self-contained HTML report pages: one file, inline CSS, no script, every value escaped.

A page loads nothing: no fonts, scripts, images or external style sheets, so it works offline and as an
e-mail attachment. A Content-Security-Policy meta tag (``default-src 'none'; style-src
'unsafe-inline'; base-uri 'none'; form-action 'none'``) forbids loading resources and running
scripts, in case escaping is ever missed. The document is 7-bit
ASCII: every other character is a numeric character reference, so a shell redirect, a console code page
or a mail relay cannot re-encode it.

Data goes into a page only through ``text`` (or ``code``, ``quote``, ``link``, ``span_ref``). It is
HTML-escaped, and control, format (bidi, zero-width, tag), line/paragraph-separator, surrogate and
other default-ignorable characters (variation selectors, Hangul fillers) are replaced with a visible
``U+XXXX`` marker, so they cannot reorder, hide or forge what a reader sees. Only ``http`` and
``https`` URIs become links, with ``rel="noopener noreferrer"``; a URI containing one of those
characters stays text. These functions return markup for element content: never put their output in
an attribute value.
"""
from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable
from datetime import date, datetime
from urllib.parse import urlsplit

from .evidence import EvidenceSpan

CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
HIDDEN = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
# No glyph, but not Cc/Cf/Cs/Zl/Zp: variation selectors, Hangul fillers and a few other
# default-ignorable marks. Fixed code points so Unicode 13 (Python 3.10) and 15.1 (3.13) agree.
# U+180F is unassigned before Unicode 14 and is still invisible.
_INVISIBLE = frozenset({
    0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F, 0x3164, 0xFFA0,
    *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0),
})
TONES = ("red", "amber", "blue", "green", "gray")

_CANDIDATE = re.compile(r"[^\t\n\x20-\x7e]")
_CANDIDATE_ONE_LINE = re.compile(r"[^\t\x20-\x7e]")
_URL = re.compile(r"https?://[^\s\\<>\"'`]+", re.IGNORECASE)

STYLE = """\
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --line: #d1d9e0; --panel: #f6f8fa; --link: #0969da;
  --red-bg: #ffebe9; --red-fg: #a40e26; --amber-bg: #fff8c5; --amber-fg: #6c4400;
  --blue-bg: #ddf4ff; --blue-fg: #0a3069; --green-bg: #dafbe1; --green-fg: #116329;
  --gray-bg: #eff2f5; --gray-fg: #424a53;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --line: #3d444d; --panel: #161b22; --link: #58a6ff;
    --red-bg: #42181c; --red-fg: #ffa198; --amber-bg: #3b2b05; --amber-fg: #e3b341;
    --blue-bg: #0c2d6b; --blue-fg: #a5d6ff; --green-bg: #0f3d1f; --green-fg: #7ee787;
    --gray-bg: #262c36; --gray-fg: #d1d7e0;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg); overflow-wrap: break-word;
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Helvetica Neue", Arial, sans-serif;
}
main { max-width: 64rem; margin: 0 auto; padding: 1rem; }
h1 { font-size: 1.6rem; line-height: 1.25; margin: .5rem 0 1rem; }
h2 { font-size: 1.3rem; line-height: 1.3; margin: 2rem 0 .75rem; padding-bottom: .25rem;
     border-bottom: 1px solid var(--line); }
h3 { font-size: 1.05rem; line-height: 1.4; margin: 0 0 .5rem; overflow-wrap: break-word; }
p { margin: .4rem 0; }
a { color: var(--link); overflow-wrap: break-word; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
       font-size: .875em; background: var(--panel); padding: .1em .3em; border-radius: 4px;
       overflow-wrap: break-word; }
.meta { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: .25rem 1rem; margin: 0 0 1rem; }
.meta dt { color: var(--muted); }
.meta dd { margin: 0; overflow-wrap: break-word; }
.scroll { overflow-x: auto; margin: .5rem 0 1rem; }
table { border-collapse: collapse; }
caption { text-align: left; color: var(--muted); padding-bottom: .25rem; }
th, td { border: 1px solid var(--line); padding: .35rem .6rem; text-align: left; vertical-align: top; }
thead th, tfoot th, tfoot td { background: var(--panel); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.item { border: 1px solid var(--line); border-radius: 8px; padding: .75rem 1rem; margin: 0 0 1rem; }
blockquote { margin: .5rem 0; padding: .4rem .8rem; border-left: 4px solid var(--line); background: var(--panel);
             white-space: pre-wrap; overflow-wrap: break-word; }
ul { padding-left: 1.25rem; margin: .25rem 0 .5rem; }
li { margin: .25rem 0; overflow-wrap: break-word; }
.label { font-weight: 600; margin: .75rem 0 .25rem; }
.muted { color: var(--muted); }
.hash { font-size: .75em; }
time { white-space: nowrap; }
.badge { display: inline-block; padding: 0 .5em; border-radius: 1em; font-size: .8rem; font-weight: 600;
         white-space: nowrap; vertical-align: .1em; }
.red { background: var(--red-bg); color: var(--red-fg); }
.amber { background: var(--amber-bg); color: var(--amber-fg); }
.blue { background: var(--blue-bg); color: var(--blue-fg); }
.green { background: var(--green-bg); color: var(--green-fg); }
.gray { background: var(--gray-bg); color: var(--gray-fg); }
.ctl { font: .75em ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: var(--red-fg);
       border: 1px dashed currentColor; border-radius: 3px; padding: 0 .2em; }
@media (max-width: 40rem) {
  main { padding: 1rem; }
  .meta { grid-template-columns: minmax(0, 1fr); gap: 0; }
  .meta dd { margin-bottom: .5rem; }
  .item { padding: .6rem .75rem; }
  ul { padding-left: 1rem; }
}
@media print {
  .item { break-inside: avoid; }
  a { color: inherit; }
}
"""


def _hidden(char: str) -> bool:
    return unicodedata.category(char) in HIDDEN or ord(char) in _INVISIBLE


def _mark(match: re.Match) -> str:
    char = match.group()
    if _hidden(char):
        return f'<span class="ctl">U+{ord(char):04X}</span>'
    return char


def text(value: object, *, lines: bool = False) -> str:
    """Escaped element content. Hidden characters become visible ``U+XXXX`` markers; with ``lines``,
    line breaks are kept (a CRLF counts as one), otherwise they are marked too."""
    data = str(value)
    if lines:
        data = data.replace("\r\n", "\n")
    escaped = html.escape(data, quote=True)
    return (_CANDIDATE if lines else _CANDIDATE_ONE_LINE).sub(_mark, escaped)


def code(value: object) -> str:
    return f"<code>{text(value)}</code>"


def when(value: date | datetime) -> str:
    """A date or time as ISO 8601, kept on one line."""
    stamp = html.escape(value.isoformat(), quote=True)
    return f'<time datetime="{stamp}">{stamp}</time>'


def quote(value: object) -> str:
    """A verbatim quote: line breaks kept, nothing interpreted.

    Each line break is written as ``&#10;``, which the browser decodes to the same newline (the block is
    ``white-space: pre-wrap``), so no line of the file starts with the quote's own text."""
    return f"<blockquote>{text(value, lines=True).replace(chr(10), '&#10;')}</blockquote>"


def badge(label: str, tone: str) -> str:
    if tone not in TONES:
        raise ValueError(f"unknown badge tone: {tone}")
    return f'<span class="badge {tone}">{text(label)}</span>'


def safe_url(uri: object) -> str | None:
    """``uri`` if it is an absolute http(s) URL that can be a link as written, else None."""
    if not isinstance(uri, str) or not _URL.fullmatch(uri):
        return None
    if any(_hidden(c) for c in uri):
        return None
    try:
        parts = urlsplit(uri)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return uri


def link(uri: object) -> str:
    """An http(s) URI as a link; anything else (javascript:, data:, file:, synthetic://, ...) as text."""
    url = safe_url(uri)
    if url is None:
        return text(uri)
    return f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{text(url)}</a>'


def span_ref(span: EvidenceSpan) -> str:
    """Where a quote comes from: record ID and source when known, location, character range, SHA-256.

    A transcript or OCR sidecar quote is located in its media file first (``clip.mp4#t=12.5,17``), then in
    the sidecar whose bytes the hash covers, and ends with how the sidecar is bound to the media file."""
    parts = []
    if span.id is not None:
        parts.append(f"evidence {code(span.id)}")
    where = f"{code(span.location)} <span class=\"muted\">(characters {span.start}&ndash;{span.end})</span>"
    media = span.media
    if media is not None:
        when = f' <span class="muted">({text(media.time_range)})</span>' if media.time_range else ""
        parts.append(f"{code(span.citation)}{when}")
        where = f"{text(media.label)} {where}"
    parts.append(where)
    if span.source_uri is not None:
        parts.append(link(span.source_uri))
    parts.append(f'sha256 <code class="hash">{text(span.sha256)}</code>')
    if media is not None:
        parts.append(f'media sha256 <code class="hash">{text(media.sha256)}</code>' if media.sha256
                     else "media file not present, binding not checked" if media.path
                     else "no media file named or found")
    return " &middot; ".join(parts)


def cite(span: EvidenceSpan) -> str:
    """One evidence citation: where it comes from, then the exact quote."""
    return f'<p>{span_ref(span)}</p>\n{quote(span.quote)}'


def items(entries: Iterable[str]) -> str:
    """A list of HTML fragments."""
    return "<ul>\n" + "".join(f"<li>{entry}</li>\n" for entry in entries) + "</ul>"


def meta(rows: Iterable[tuple[str, str]]) -> str:
    """A definition list of (label, HTML fragment) pairs; labels are escaped."""
    return '<dl class="meta">\n' + "".join(f"<dt>{text(label)}</dt><dd>{value}</dd>\n"
                                           for label, value in rows) + "</dl>"


def table(caption: str, columns: list[str], rows: Iterable[list[str]], *, foot: list[str] | None = None,
          numeric: Iterable[int] = ()) -> str:
    """A table whose first column heads each row. ``caption`` and ``columns`` are plain text; cells are
    HTML fragments. Columns listed in ``numeric`` are right-aligned."""
    numeric = set(numeric)

    def row(cells: list[str]) -> str:
        out = [f'<th scope="row">{cells[0]}</th>']
        out += [f'<td class="num">{c}</td>' if i in numeric else f"<td>{c}</td>"
                for i, c in enumerate(cells[1:], start=1)]
        return "<tr>" + "".join(out) + "</tr>\n"

    head = "".join(f'<th scope="col">{text(c)}</th>' for c in columns)
    body = "".join(row(cells) for cells in rows)
    out = [f'<div class="scroll"><table>\n<caption>{text(caption)}</caption>\n',
           f"<thead><tr>{head}</tr></thead>\n<tbody>\n{body}</tbody>\n"]
    if foot is not None:
        out.append(f"<tfoot>\n{row(foot)}</tfoot>\n")
    out.append("</table></div>")
    return "".join(out)


def page(title: str, body: str, generator: str) -> str:
    """The whole document. ``title`` and ``generator`` are plain text; ``body`` is HTML.

    The result is ASCII: non-ASCII characters become numeric character references."""
    document = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f'<meta name="generator" content="{html.escape(generator, quote=True)}">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
        '<meta name="referrer" content="no-referrer">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>{html.escape(title, quote=True)}</title>\n"
        f"<style>\n{STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        "<main>\n"
        f"{body}\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )
    return document.encode("ascii", "xmlcharrefreplace").decode("ascii")
