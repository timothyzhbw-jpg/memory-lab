"""The home page: what Memory Lab is, a one-step way to check a file, and the agent files under the root."""
from __future__ import annotations

from memcore import htmlpage as h

from .i18n import _
from .pages import attr, href, lang_field, plain, plural
from .workspace import Inventory

TITLE = "Is your AI assistant's memory still true?"
WHAT = ("AI coding assistants follow instruction and memory files such as CLAUDE.md, AGENTS.md and MEMORY.md. "
        "Memory Lab checks each statement in them against the rest of your project and shows the ones that may "
        "no longer be true; it can also show which decisions in a team's decision log really count. Nothing is "
        "changed and nothing leaves this computer.")
MODELS_ON = (" Model checks are switched on for this session: they send text to an outside service, but only when "
             "you pick one under More options.")
NOTE = "Compared with the whole project. Nothing is changed and nothing leaves this computer."
EMPTY = ("This folder has no CLAUDE.md, AGENTS.md, GEMINI.md, MEMORY.md or agent rule files. Start memlab-ui with "
         "--root set to a project that has one.")
STEPS = (("Pick a file.", "Choose the agent file you want to check."),
         ("Press Check.", "Each statement is compared with your project."),
         ("Look at what's flagged.", "Read the quotes side by side and decide."))
CARDS = (("/check", "Check an agent file",
          "See which statements in CLAUDE.md, AGENTS.md or MEMORY.md your project still backs up."),
         ("/decisions", "Team decisions",
          "See what was decided, who could decide it, and which messages did not count."))


def t(message: str) -> str:
    """A translated message as escaped text."""
    return h.text(_(message))


def agent(name: str) -> str:
    """An agent label from discovery; only the one the UI adds is translated."""
    return _(name) if name == "Agent memory" else name


def render(inventory: Inventory, *, models: bool) -> str:
    files = inventory.files
    out = ['<section class="hero" aria-labelledby="title">',
           f'<h1 id="title">{t(TITLE)}</h1>',
           f'<p class="lead">{h.text(_(WHAT) + (_(MODELS_ON) if models else ""))}</p>']
    if files:
        options = "".join(f'<option value="{attr(f.path)}"{" selected" if i == 0 else ""}>{plain(f.path)}'
                          f'{plain(" (" + agent(f.agent) + ")")}</option>' for i, f in enumerate(files))
        out += ['<form class="card composer" method="get" action="/check">',
                f'<label class="composer-label" for="home-file">{t("Which file should I check?")}</label>',
                f'<div class="row"><select id="home-file" name="file" required>{options}</select>',
                f'<button class="button" type="submit">{t("Check")}</button></div>{lang_field()}',
                f'<p class="note">{t(NOTE)}</p>',
                "</form>"]
    else:
        out += ['<div class="card composer">',
                f'<p class="composer-label">{t("No agent files here yet")}</p>',
                f"<p>{t(EMPTY)}</p></div>"]
    chips = [f'<li><a class="chip" href="{href("/check", file=f.path)}">{plain(_("Check {name}", name=f.path))}'
             "</a></li>" for f in files[:3]]
    if inventory.datasets:
        chips.append(f'<li><a class="chip" href="{href("/decisions")}">{t("See team decisions")}</a></li>')
    if chips:
        out.append(f'<ul class="chips" aria-label="{attr(_("Suggestions"))}">{"".join(chips)}</ul>')
    out.append("</section>")
    out += ['<section class="card" aria-labelledby="start">',
            f'<h2 id="start" class="flush">{t("Start here")}</h2>',
            '<ol class="steps">',
            *(f"<li><span><strong>{t(first)}</strong> {t(then)}</span></li>" for first, then in STEPS),
            "</ol></section>",
            '<div class="cards">',
            *(f'<a class="card" href="{href(path)}"><h3>{t(name)}</h3><p class="muted">{t(what)}</p></a>'
              for path, name, what in CARDS),
            "</div>",
            f'<section aria-labelledby="found"><h2 id="found">{t("Agent files in this project")}</h2>']
    if files:
        rows = []
        for f in files:
            count = t("can't read") if f.statements is None else str(f.statements)
            rows.append(f'<tr><th scope="row">{h.code(f.path)}</th><td>{h.text(agent(f.agent))}</td>'
                        f'<td class="num">{count}</td>'
                        f'<td><a href="{href("/check", file=f.path)}">{t("Check")}<span class="sr-only"> '
                        f"{h.text(f.path)}</span></a></td></tr>")
        caption = plural(len(files), "{n} file found by name; none of them was changed.",
                         "{n} files found by name; none of them was changed.")
        out += ['<div class="card scroll"><table>',
                f"<caption>{h.text(caption)}</caption>",
                f'<thead><tr><th scope="col">{t("File")}</th><th scope="col">{t("Agent")}</th>'
                f'<th scope="col">{t("Statements")}</th>'
                f'<th scope="col"><span class="sr-only">{t("Action")}</span></th></tr></thead>',
                f"<tbody>{''.join(rows)}</tbody></table></div>"]
    else:
        out.append(f'<p class="muted">{t("None found.")}</p>')
    out.append("</section>")
    return "\n".join(out)
