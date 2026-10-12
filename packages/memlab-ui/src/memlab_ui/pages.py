"""Server-rendered HTML for the UI: one shell, three pages, plain words.

Every value that comes from a file, a request or git goes through ``memcore.htmlpage`` (``text``, ``code``,
``link``) or ``attr`` below, so it is escaped and hidden characters show as ``U+XXXX``. The document has no
script and loads nothing but the logo and favicon this server ships: styles are inline, icons are inline SVG,
fonts are the system's. The result is 7-bit ASCII, like the HTML reports. Wording goes through ``i18n``.
"""
from __future__ import annotations

import html
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

from memcore import EvidenceSpan
from memcore import htmlpage as h

from .i18n import _, current, n_

# Sent as a header too, with frame-ancestors (which a meta tag cannot carry).
# img-src 'self' admits only the logo and favicon this server ships under /static/.
CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'"

STYLE = """\
:root {
  color-scheme: light dark;
  --page: #FAF9F5; --side: #F5F4ED; --card: #FFFFFF; --line: rgba(31, 30, 29, 0.15);
  --field: rgba(31, 30, 29, 0.28); --text: #141413; --muted: #5E5D59; --soft: #73726C;
  --active: rgba(115, 114, 108, 0.12); --button: #141413; --button-text: #FAF9F5;
  --attn-dot: #B6532F; --attn-fg: #8F3B1C; --attn-bg: #F7E6DD;
  --fine-dot: #2A78D6; --fine-fg: #1D5AA6; --fine-bg: #E3EEFA;
  --grey-dot: #B0AEA5; --grey-fg: #5E5D59; --grey-bg: #EFEEE8;
  --quote: #F5F4ED; --mark: #F7E6DD; --focus: #1D5AA6;
  --serif: Georgia, "Times New Roman", "Songti SC", "Noto Serif CJK SC", "SimSun", serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Helvetica Neue", Arial, "PingFang SC",
          "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #1C1B19; --side: #23221F; --card: #2A2926; --line: rgba(250, 249, 245, 0.16);
    --field: rgba(250, 249, 245, 0.3); --text: #F2F1EC; --muted: #C2C0B8; --soft: #ADABA3;
    --active: rgba(250, 249, 245, 0.1); --button: #F2F1EC; --button-text: #141413;
    --attn-dot: #DE7A52; --attn-fg: #F4BBA3; --attn-bg: #45261B;
    --fine-dot: #5C9DEB; --fine-fg: #AACBF3; --fine-bg: #1A2D46;
    --grey-dot: #8E8C84; --grey-fg: #D2D0C8; --grey-bg: #34332F;
    --quote: #23221F; --mark: #5A3324; --focus: #8DBBF0;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--page); color: var(--text); font: 16px/1.55 var(--sans);
       overflow-wrap: break-word; }
a { color: inherit; text-underline-offset: .18em; }
a:hover { text-decoration-thickness: 2px; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; border-radius: 6px; }
code, .mono { font-family: var(--mono); font-size: .875em; overflow-wrap: anywhere; }
h1, h2, h3, .serif { font-family: var(--serif); font-weight: 400; }
h1 { font-size: 2.25rem; line-height: 1.15; margin: 0 0 .75rem; letter-spacing: -.01em; }
h2 { font-size: 1.6rem; line-height: 1.25; margin: 2.5rem 0 1rem; }
h3 { font-size: 1.2rem; line-height: 1.3; margin: 1.75rem 0 .6rem; }
p { margin: .5rem 0; }
.skip { position: absolute; left: -999px; top: 0; background: var(--card); padding: .75rem 1rem; z-index: 2; }
.skip:focus { left: 1rem; top: 1rem; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
           clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.shell { display: flex; flex-wrap: wrap; min-height: 100vh; }
.sidebar { flex: 0 0 240px; width: 240px; background: var(--side); border-right: .5px solid var(--line);
           padding: 1.25rem .9rem; display: flex; flex-direction: column; gap: 1.25rem;
           position: sticky; top: 0; height: 100vh; overflow-y: auto; }
.brand { display: flex; align-items: center; min-height: 44px; padding: 0 .4rem; align-self: flex-start; }
.brand img { display: block; height: 24px; width: auto; }
.sidebar ul { list-style: none; margin: 0; padding: 0; }
.nav a, .topics a { display: flex; align-items: center; gap: .6rem; min-height: 44px; padding: .5rem .6rem;
                    border-radius: 10px; text-decoration: none; color: var(--text); }
.nav a:hover, .topics a:hover { background: var(--active); }
.nav a[aria-current="page"], .topics a[aria-current="true"] { background: var(--active); font-weight: 600; }
.nav svg { flex: 0 0 18px; color: var(--muted); }
.side-title { font: 600 .75rem/1.2 var(--sans); text-transform: uppercase; letter-spacing: .06em;
              color: var(--soft); margin: 0 .6rem .4rem; }
.topics a { font-size: .95rem; }
.folder { margin-top: auto; background: var(--card); border: .5px solid var(--line); border-radius: 14px;
          padding: .75rem .85rem; font-size: .8rem; color: var(--muted); }
.folder p { margin: .15rem 0; }
.folder .mono { color: var(--text); font-size: .85rem; overflow-wrap: anywhere; }
.details { margin: .6rem 0 0; }
.details summary { min-height: 44px; font-size: .85rem; font-weight: 400; color: var(--soft); }
.details dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: .2rem .9rem; margin: 0;
              font-size: .82rem; color: var(--muted); }
.details dt { color: var(--soft); }
.details dd { margin: 0; overflow-wrap: anywhere; }
.lang { display: flex; align-items: center; gap: .3rem; margin: -.5rem 0 0; padding: 0 .4rem; font-size: 13px;
        color: var(--soft); }
.lang svg { flex: 0 0 14px; }
.lang a { color: inherit; display: inline-flex; align-items: center; min-height: 44px; padding: 0 .15rem; }
.content { flex: 1 1 0; min-width: 0; padding: 64px 1.5rem 4rem; }
.column { max-width: 820px; margin: 0 auto; }
.lead { color: var(--muted); font-size: 1.075rem; max-width: 42rem; }
.small, .meta { font-size: .85rem; color: var(--soft); }
.muted { color: var(--muted); }
.card { background: var(--card); border: .5px solid var(--line); border-radius: 18px; padding: 1.25rem 1.4rem;
        margin: 1rem 0; }
.hero { text-align: center; padding: 1rem 0 .5rem; }
.hero h1 { font-size: 2.5rem; }
.hero .lead { margin: 0 auto 1.75rem; }
.composer { text-align: left; border-radius: 20px; padding: 1.4rem 1.5rem 1.1rem; max-width: 640px;
            margin: 0 auto; box-shadow: 0 1px 2px rgba(31, 30, 29, .04), 0 6px 24px rgba(31, 30, 29, .05); }
.composer-label { display: block; font: 400 1.35rem/1.3 var(--serif); margin-bottom: .75rem; }
.row { display: flex; flex-wrap: wrap; gap: .6rem; align-items: flex-end; }
.row select, .row .field { flex: 1 1 16rem; margin: 0; }
.note { font-size: .85rem; color: var(--soft); margin: .75rem 0 0; }
.chips { list-style: none; display: flex; flex-wrap: wrap; justify-content: center; gap: .5rem;
         padding: 0; margin: 1.1rem auto 0; max-width: 640px; }
.chip { display: inline-flex; align-items: center; min-height: 44px; padding: .4rem 1rem; border-radius: 999px;
        border: .5px solid var(--line); background: var(--card); text-decoration: none; font-size: .92rem; }
.chip:hover { background: var(--active); }
label, .label { display: block; font-weight: 600; margin: 0 0 .35rem; }
.hint { display: block; font-weight: 400; font-size: .85rem; color: var(--soft); margin-top: .1rem; }
select, input { font: inherit; color: var(--text); background: var(--card); min-height: 44px; width: 100%;
                border: 1px solid var(--field); border-radius: 12px; padding: .5rem .75rem; }
.field { margin: 0 0 1rem; }
.fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 0 1rem; }
.button { font: 600 1rem/1 var(--sans); min-height: 44px; padding: 0 1.4rem; border: 0; border-radius: 12px;
          background: var(--button); color: var(--button-text); cursor: pointer; }
.actions { display: flex; justify-content: flex-end; margin-top: .25rem; }
details { margin: .75rem 0; }
summary { cursor: pointer; min-height: 44px; display: flex; align-items: center; color: var(--muted);
          font-weight: 600; }
details[open] > summary { margin-bottom: .5rem; }
.steps { list-style: none; counter-reset: step; display: grid; gap: .75rem;
         grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); padding: 0; margin: .5rem 0 0; }
.steps li { counter-increment: step; display: flex; gap: .7rem; align-items: flex-start; }
.steps li::before { content: counter(step); flex: 0 0 28px; height: 28px; border-radius: 50%;
                    display: grid; place-items: center; border: .5px solid var(--line);
                    font: 1rem/1 var(--serif); background: var(--page); }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 1rem; margin: 1rem 0; }
.cards .card { margin: 0; text-decoration: none; display: block; }
.cards .card:hover { border-color: var(--field); }
.cards h3 { margin: 0 0 .25rem; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .95rem; }
caption { text-align: left; color: var(--soft); font-size: .85rem; padding: 0 0 .5rem; }
th, td { text-align: left; vertical-align: middle; padding: .65rem .6rem; border-bottom: .5px solid var(--line); }
thead th { font-size: .8rem; font-weight: 600; color: var(--soft); text-transform: uppercase;
           letter-spacing: .04em; }
tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td a { display: inline-flex; align-items: center; min-height: 44px; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; flex: 0 0 9px; }
.dot.attention { background: var(--attn-dot); }
.dot.fine, .dot.approved { background: var(--fine-dot); }
.dot.grey, .dot.rejected { background: var(--grey-dot); }
.dot.waiting { border: 1.5px solid var(--grey-dot); }
.pill { display: inline-flex; align-items: center; gap: .35rem; border-radius: 999px; padding: .15rem .65rem;
        font: 600 .8rem/1.5 var(--sans); white-space: nowrap; vertical-align: .2em; }
.pill.attention { color: var(--attn-fg); background: var(--attn-bg); }
.pill.fine, .pill.approved { color: var(--fine-fg); background: var(--fine-bg); }
.pill.grey, .pill.rejected, .pill.waiting { color: var(--grey-fg); background: var(--grey-bg); }
.tiles { list-style: none; display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
         gap: .75rem; padding: 0; margin: 1.25rem 0 2rem; }
.tile { background: var(--card); border: .5px solid var(--line); border-radius: 16px; padding: 1rem 1.1rem; }
.tile .name { display: flex; align-items: center; gap: .5rem; font-weight: 600; }
.tile .num { display: block; font: 2.2rem/1.1 var(--serif); margin: .35rem 0 .2rem; }
.tile a { text-decoration: none; }
.tile p { margin: 0; font-size: .875rem; color: var(--muted); }
.card h4 { margin: 0 0 .9rem; font: inherit; }
.compare { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 1rem; }
.side-label { font-size: .85rem; color: var(--soft); margin: 0 0 .4rem; }
blockquote { margin: 0; padding: .7rem .9rem; border-radius: 12px; background: var(--quote);
             white-space: pre-wrap; overflow-wrap: break-word; }
.q { font: 1.05rem/1.5 var(--serif); }
.big-quote { font: 1.4rem/1.45 var(--serif); padding: 1rem 1.25rem; margin: 1rem 0 .5rem; }
mark { background: var(--mark); color: inherit; border-radius: 4px; padding: 0 .12em; }
.advice { margin: .9rem 0 0; }
.issues { margin: .6rem 0 0; padding-left: 1.2rem; color: var(--muted); font-size: .92rem; }
.more { margin-top: .75rem; }
.others .card { padding: 1rem 1.15rem; }
.banner { display: flex; gap: .75rem; align-items: center; flex-wrap: wrap; }
.banner .dot { width: 11px; height: 11px; flex-basis: 11px; }
.banner a { min-height: 44px; display: inline-flex; align-items: center; }
.error { border-left: 4px solid var(--attn-dot); }
.error h2 { margin: 0 0 .25rem; font-size: 1.3rem; }
.facts { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: .35rem 1.25rem; margin: 1rem 0; }
.facts dt { color: var(--soft); }
.facts dd { margin: 0; }
.timeline { list-style: none; margin: 1rem 0; padding: 0; }
.timeline li { position: relative; padding: 0 0 1.5rem 1.9rem; }
.timeline li::before { content: ""; position: absolute; left: 6px; top: 1.1rem; bottom: -.2rem;
                       border-left: 1px solid var(--line); }
.timeline li:last-child::before { display: none; }
.timeline .mark { position: absolute; left: 0; top: .35rem; width: 13px; height: 13px; border-radius: 50%;
                  background: var(--page); border: 1.5px solid var(--grey-dot); }
.timeline .counted .mark { background: var(--text); border-color: var(--text); }
.timeline p { margin: .1rem 0; }
.timeline blockquote { margin-top: .4rem; font-size: .95rem; }
.verdict { font-weight: 600; font-size: .9rem; }
.verdict.not { color: var(--attn-fg); }
.verdict.noted { color: var(--soft); }
.attention-list { padding-left: 1.2rem; }
.attention-list li { margin: .5rem 0; }
ul.plain { padding-left: 1.2rem; }
.flush { margin-top: 0; }
.subject-title { font-size: 2rem; }
@media (max-width: 760px) {
  .sidebar { flex: 1 1 100%; width: 100%; border-right: 0; border-bottom: .5px solid var(--line); gap: .75rem;
             position: static; height: auto; }
  .nav ul { display: flex; flex-wrap: wrap; gap: .25rem; }
  .folder { margin-top: 0; }
  .content { padding: 32px 16px 3rem; }
  h1, .hero h1 { font-size: 2rem; }
  .card { padding: 1rem; border-radius: 14px; }
  .facts { grid-template-columns: minmax(0, 1fr); gap: 0; }
  .facts dd { margin-bottom: .6rem; }
  .big-quote { font-size: 1.2rem; }
}
@media print { .sidebar { display: none; } .content { padding: 0; } }
"""

ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h14V9.5"/><path d="M10 20v-5h4v5"/>',
    "check": '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/>'
             '<path d="m9 14 2 2 4-4"/>',
    "decisions": '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/>'
                 '<circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.2c3 .2 5.5 2.6 5.5 5.8"/>',
}
NAV = (("home", "/", "Home"), ("check", "/check", "Check an agent file"), ("decisions", "/decisions", "Team decisions"))
GLOBE = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
         'stroke-linecap="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/>'
         '<path d="M3 12h18"/><path d="M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9s1.3-6.4 '
         '3.8-9z"/></svg>')
# Package data under static/, served at /static/<name>; nothing else is ever served from disk.
STATIC = ("ui-light.png", "ui-dark.png", "memory-lab-icon-64.png", "memory-lab-icon-32.png")
_CTL = re.compile(r'<span class="ctl">(U\+[0-9A-F]+)</span>')


# --- small helpers ------------------------------------------------------------------------------------

def attr(value: object) -> str:
    """An escaped attribute value (memcore's helpers return element content only)."""
    return html.escape(str(value), quote=True)


_KEY: ContextVar[str | None] = ContextVar("memlab_ui_key", default=None)


@contextmanager
def request_key(key: str | None) -> Iterator[None]:
    """The run's key while serving a request that carried it (None otherwise): links and forms keep it."""
    token = _KEY.set(key)
    try:
        yield
    finally:
        _KEY.reset(token)


def _kept() -> list[tuple[str, str]]:
    """The fields every link keeps: the language, and the key when this request carried a valid one."""
    kept = [("lang", "zh")] if current() == "zh" else []
    return kept + ([("key", key)] if (key := _KEY.get()) else [])


def query(path: str, params: dict[str, str | None]) -> str:
    """A same-site address with its query, unescaped; the page's language (and key) are kept."""
    pairs = [(key, value) for key, value in params.items() if value and key not in ("lang", "key")]
    pairs += _kept()
    return path + ("?" + urlencode(pairs) if pairs else "")


def href(path: str, **params: str | None) -> str:
    """A same-site link with its query (and the page's language), escaped for an attribute."""
    return attr(query(path, params))


def lang_field() -> str:
    """The hidden form fields that keep the page's language (and key) across a form submission."""
    return "".join(f'<input type="hidden" name="{name}" value="{attr(value)}">' for name, value in _kept())


def plain(value: object) -> str:
    """Escaped text for places that cannot hold markup, such as an <option>: hidden characters as [U+XXXX]."""
    return _CTL.sub(r"[\1]", h.text(value))


def plural(count: int, one: str, many: str, **values: object) -> str:
    """``{n}`` messages by count, in the page's language (plain text)."""
    return n_(count, one, many, **values)


def fill(message: str, **parts: str) -> str:
    """A translated sentence as HTML: its own words escaped, each ``{placeholder}`` replaced by an HTML part
    that the caller escaped."""
    pieces = re.split(r"\{(\w+)\}", _(message))
    return "".join(parts[piece] if index % 2 else h.text(piece) for index, piece in enumerate(pieces))


def when(moment: datetime | date) -> str:
    """A time shown in UTC, minutes only, with the exact ISO value in the datetime attribute."""
    if isinstance(moment, datetime):
        shown = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    else:
        shown = moment.isoformat()
    return f'<time datetime="{attr(moment.isoformat())}">{html.escape(shown)}</time>'


def role_words(roles: Iterable[str]) -> str:
    return ", ".join(h.text(role.replace("_", " ")) for role in roles)


def where(location: str) -> tuple[str, str | None]:
    """``path:line`` split for display."""
    path, _sep, line = location.rpartition(":")
    return (path, line) if path and line.isdigit() else (location, None)


def location_code(path: str, line: int | str | None) -> str:
    return h.code(f"{path}:{line}" if line else path)


def under(base: str | None, path: str) -> str:
    """``path`` (relative to ``base``) relative to the root, for display."""
    return path if base in (None, "", ".") else f"{base}/{path}"


def span_place(span: EvidenceSpan) -> str:
    """A short label for a quote: the file name and line (``api.md:3``), or for a transcript or OCR sidecar the
    media file's name and the position in it. The full path and the hash are in ``span_details``."""
    media = span.media
    if media is None:
        return location_code(PurePosixPath(span.path).name, span.line)
    position = media.time_range or media.locator
    return (f"{h.code(PurePosixPath(media.path or span.path).name)} "
            f"<span class=\"small\">({h.text(media.label)}, {h.text(position)})</span>")


def span_details(span: EvidenceSpan, base: str | None = None) -> list[tuple[str, str]]:
    """Rows for a card's Details: where the quote is (from the project folder), its source and its hash.
    ``base`` is the folder the span's path is relative to, itself relative to the root."""
    rows = [(_("File"), location_code(under(base, span.path), span.line))]
    if span.media is not None:
        rows.append((_("Media file"), h.code(under(base, span.media.path or span.path))))
    if span.source_uri is not None:
        rows.append((_("Source"), h.link(span.source_uri)))
    rows.append(("SHA-256", h.code(span.sha256)))
    return rows


def details(rows: list[tuple[str, str]]) -> str:
    """A folded "Details" block of (plain-text label, HTML value) rows for the technical facts of a card."""
    if not rows:
        return ""
    items = "".join(f"<dt>{h.text(label)}</dt><dd>{value}</dd>" for label, value in rows)
    return f'<details class="details"><summary>{h.text(_("Details"))}</summary><dl>{items}</dl></details>'


def notice(text: str) -> str:
    """A quiet banner with one plain-text sentence."""
    return (f'<div class="card banner" role="status"><span class="dot attention" aria-hidden="true"></span>'
            f"<span>{h.text(text)}</span></div>")


def quote(text: str, marks: Iterable[tuple[int, int]] = (), css: str = "q") -> str:
    """A verbatim quote with optional highlighted ranges; line breaks kept as ``&#10;``."""
    out, at = [], 0
    for start, end in sorted(marks):
        if start < at:
            continue
        out.append(_lines(text[at:start]))
        out.append(f"<mark>{_lines(text[start:end])}</mark>")
        at = end
    out.append(_lines(text[at:]))
    return f'<blockquote class="{css}">{"".join(out)}</blockquote>'


def _lines(text: str) -> str:
    return h.text(text, lines=True).replace("\n", "&#10;")


def select(name: str, label: str, options: list[tuple[str, str]], selected: str | None, *, hint: str = "",
           placeholder: str = "", required: bool = False, ident: str | None = None) -> str:
    """A labelled select; label, hint, option values and texts are plain text, escaped here."""
    ident = ident or f"f-{name}"
    hint_id = f"{ident}-hint"
    out = [f'<div class="field"><label for="{ident}">{h.text(label)}'
           + (f'<span class="hint" id="{hint_id}">{h.text(hint)}</span>' if hint else "") + "</label>",
           f'<select id="{ident}" name="{attr(name)}"' + (" required" if required else "")
           + (f' aria-describedby="{hint_id}"' if hint else "") + ">"]
    if placeholder:
        out.append(f'<option value=""{" selected" if not selected else ""}>{h.text(placeholder)}</option>')
    for value, shown in options:
        chosen = " selected" if value == selected else ""
        out.append(f'<option value="{attr(value)}"{chosen}>{plain(shown)}</option>')
    out.append("</select></div>")
    return "".join(out)


def text_input(name: str, label: str, value: str | None, *, kind: str = "text", hint: str = "",
               placeholder: str = "") -> str:
    ident = f"f-{name}"
    hint_id = f"{ident}-hint"
    return (f'<div class="field"><label for="{ident}">{h.text(label)}'
            + (f'<span class="hint" id="{hint_id}">{h.text(hint)}</span>' if hint else "") + "</label>"
            f'<input id="{ident}" name="{attr(name)}" type="{kind}" value="{attr(value or "")}"'
            + (f' placeholder="{attr(placeholder)}"' if placeholder else "")
            + (f' aria-describedby="{hint_id}"' if hint else "")
            + ' autocomplete="off" spellcheck="false"></div>')


def error_card(title: str, message: str) -> str:
    """A visible, announced error. ``title`` and ``message`` are plain text."""
    return f'<div class="card error" role="alert"><h2>{h.text(title)}</h2><p>{h.text(message)}</p></div>'


# --- the shell -----------------------------------------------------------------------------------------

def language_switch(path: str, params: dict[str, str]) -> str:
    """"English / 中文" at the foot of the sidebar: the current language as text, the other as a link to the
    same page and fields."""
    rest = [(key, value) for key, value in params.items() if key not in ("lang", "key")]
    rest += [("key", key)] if (key := _KEY.get()) else []
    english = attr(path + ("?" + urlencode(rest) if rest else ""))
    chinese = attr(path + "?" + urlencode([*rest, ("lang", "zh")]))
    if current() == "zh":
        links = (f'<a href="{english}" lang="en" hreflang="en">English</a> / '
                 '<span lang="zh-CN" aria-current="true">中文</span>')
    else:
        links = ('<span lang="en" aria-current="true">English</span> / '
                 f'<a href="{chinese}" lang="zh-CN" hreflang="zh-CN">中文</a>')
    return f'<p class="lang" role="group" aria-label="{attr(_("Language"))}">{GLOBE}{links}</p>\n'


def page(title: str, body: str, *, active: str | None, root: str, topics: str = "", switch: str = "") -> str:
    """The whole document: sidebar (logo, navigation, optional topics, the project folder, the language switch)
    and the content column. ``title`` and ``root`` are plain text; ``body``, ``topics`` and ``switch`` are HTML."""
    nav = []
    for key, path, label in NAV:
        current_page = ' aria-current="page"' if key == active else ""
        icon = (f'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
                f'focusable="false">{ICONS[key]}</svg>')
        nav.append(f'<li><a href="{href(path)}"{current_page}>{icon}<span>{h.text(_(label))}</span></a></li>')
    document = (
        "<!DOCTYPE html>\n"
        f'<html lang="{"zh-CN" if current() == "zh" else "en"}">\n<head>\n<meta charset="utf-8">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
        '<meta name="referrer" content="no-referrer">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>{h.text(_(title))} &middot; Memory Lab</title>\n"
        '<link rel="icon" type="image/png" sizes="32x32" href="/static/memory-lab-icon-32.png">\n'
        '<link rel="icon" type="image/png" sizes="64x64" href="/static/memory-lab-icon-64.png">\n'
        f"<style>\n{STYLE}</style>\n</head>\n<body>\n"
        f'<a class="skip" href="#main">{h.text(_("Skip to content"))}</a>\n'
        '<div class="shell">\n<aside class="sidebar">\n'
        f'<a class="brand" href="{href("/")}"><picture>'
        '<source srcset="/static/ui-dark.png" media="(prefers-color-scheme: dark)">'
        '<img src="/static/ui-light.png" alt="Memory Lab" width="145" height="24"></picture></a>\n'
        f'<nav class="nav" aria-label="{attr(_("Main"))}"><ul>{"".join(nav)}</ul></nav>\n'
        f"{topics}"
        f'<div class="folder"><p>{h.text(_("Project folder"))}</p>'
        f'<p class="mono" title="{attr(root)}">{h.text(PurePosixPath(Path(root).as_posix()).name or root)}</p>'
        f'<p>{h.text(_("Read-only: nothing here changes a file."))}</p></div>\n'
        f"{switch}"
        "</aside>\n"
        f'<main id="main" class="content" tabindex="-1"><div class="column">\n{body}\n</div></main>\n'
        "</div>\n</body>\n</html>\n"
    )
    return document.encode("ascii", "xmlcharrefreplace").decode("ascii")


def message_page(title: str, message: str, *, root: str, switch: str = "") -> str:
    """A page that only says what went wrong. ``title`` and ``message`` are English message keys."""
    return page(title, f"<h1>{h.text(_(title))}</h1>\n<p class=\"lead\">{h.text(_(message))}</p>\n"
                       f'<p><a class="chip" href="{href("/")}">{h.text(_("Back to the start"))}</a></p>',
                active=None, root=root, switch=switch)
