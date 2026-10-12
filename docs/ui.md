# Memory Lab web page

English | [中文](ui.zh-CN.md)

`memlab-ui` is a local, read-only web page for trying MemFalsify and orgmemory without learning the command line. It
runs on your own computer, reads one project folder, and never changes a file. It uses the same library code as the
`memfalsify` and `orgmemory` commands and follows the same confinement rules as the [MCP server](mcp.md).

## Install and run

From a checkout of this repository:

```bash
pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory -e packages/memlab-ui
memlab-ui --root examples
```

`./scripts/setup-local.sh` installs it too. The command prints the address to open, `http://127.0.0.1:8765/` by
default. Press Ctrl+C to stop it.

| Option | Meaning |
|---|---|
| `--root DIR` | The project folder the page may read (default: the current directory). Nothing outside it is read. |
| `--port N` | Port on 127.0.0.1 (default 8765; `0` picks a free port and prints it). A busy port exits with code 2. |
| `--host HOST` | Only `127.0.0.1` is accepted. Any other value exits with code 2: the page has no login, so other machines must not reach it. |
| `--allow-model-verifiers` | Offers the `llm`, `jev`, `dual` and `cascade` verifiers under *More options*. They send memory and evidence text to external APIs and cost money; see [local-setup.md](local-setup.md) for their keys. |

Every page reads in English or Simplified Chinese. `?lang=zh` selects Chinese; links and forms keep the
language, and the *English / 中文* link at the foot of the sidebar switches it on the same page.

## Pages

Forms use GET, so every result has its own address that can be reloaded or bookmarked, and no request changes
anything.

### Home

What Memory Lab is, a *Which file should I check?* box that checks a file against the whole project in one step,
and *Agent files in this project*: the files `memfalsify discover` finds (CLAUDE.md, AGENTS.md, GEMINI.md, Cursor,
Copilot and Windsurf rules) plus any `MEMORY.md`, with the number of statements in each.

### Check an agent file

Pick an *Agent file* and what to *Compare against*: the whole project (the default) or one folder. *More
options* holds:

- *Only statements changed since*: a branch, tag or commit. The page then runs `memfalsify diff` and checks only
  the statements added or changed since that revision; unchanged ones are not checked again. It is offered only
  when the root folder is the top of a git repository, and, as for the MCP tools, the repository's work tree, git
  directory, common directory and object store must all be inside the root.
- *Treat today as*: the reference date for old dates in the file (`--as-of`).
- *Reviews file*: a `reviews.jsonl` of human-confirmed reviews. Its paths must point into the folder you compare
  against.
- *How to compare*: only with `--allow-model-verifiers`.

The result is the `memfalsify audit` report in plain words:

| Tile | Statuses |
|---|---|
| *Look at these* | `challenged`, `conflicted`, `candidate_counterevidence` (possible counterevidence), `disputed` |
| *Probably fine* | `supported`, `candidate_support` |
| *Can't tell* | `insufficient_evidence` |

Each statement to look at is a card: *Your agent file says* with its `path:line`, next to *Your docs say* with the
quote that seems to disagree, the differing words or numbers highlighted (for the lexical verifier's own matches),
why it was flagged, the first 12 characters of the source file's SHA-256, and one line of advice. More sources,
wording issues (*always*, *next week*, guesses, duplicates) and the statement ID follow. The other statements are
folded under *Show the N other statements*, and files left out of the comparison under *N files were not read*. A
closing note says that the quick check matches words: it can miss a source that says the same thing in other words
and can flag a sentence that only looks like a disagreement.

### Team decisions

Pick a *Decision log*: any folder under the root with `events.jsonl`, `policy.json` and `evidence.jsonl` (and
optionally `policy-log.jsonl`). *More options* can look at the log as it was at the end of a day (UTC).

- The sidebar lists the log's topics (subjects) with their status: *Approved*, *Rejected* or *Waiting*
  (`proposed`).
- A banner says how many things need attention: the findings of `orgmemory audit` (stale after 14 days, measured
  at the chosen day or now).
- For the chosen topic: who decided, with which role and when, the quote the decision is based on, what it is
  waiting for, open risks and dependencies, its findings as plain sentences, and *How we got here*: every event in
  replay order with its quote, marked *Counted* (an authorized decision), *Noted* (an authorized request, risk,
  dependency or comment) or *Not counted* (for example *account manager can't approve*).
- *What changed between two dates* runs `orgmemory changes` for the events after the end of the first day and up to
  the end of the second (UTC).

## Safety

The page follows the [MCP server's](mcp.md) rules, adapted to a browser:

- **Loopback only.** It binds 127.0.0.1 and refuses any other `--host`. Requests from another address are
  refused.
- **DNS rebinding.** Every request must carry exactly one `Host` header naming `127.0.0.1` or `localhost` with
  the server's port; anything else gets 403 without echoing the header. A page on another site that makes its
  own name resolve to 127.0.0.1 therefore gets nothing.
- **Read-only.** Only GET and HEAD are answered (other methods get 405), every form uses GET, and no page writes a
  report, appends an event, records a review or applies a patch. Git runs with `GIT_OPTIONAL_LOCKS=0`, so it does
  not refresh the index either. The tests snapshot the root before and after visiting every page.
- **Confinement.** Every path in a request is resolved inside `--root`: relative paths, and absolute ones only
  when they stay inside; `..` and symlinks only when the resolved path stays inside. Hidden files and folders,
  `node_modules`, `__pycache__` and `venv` are refused except at a documented agent instruction location such as
  `.cursor/rules/`. Each page runs inside `memcore`'s confined reads, so a file swapped for an outward symlink after
  these checks is still refused when it is opened. Without POSIX `O_NOFOLLOW` and `dir_fd` support (Windows) the
  server refuses to start. The lists the forms offer come from the same walk and skip nested git repositories and
  symlinked folders.
- **Model verifiers off.** The default is the offline lexical verifier. A request for `llm`, `jev`, `dual` or
  `cascade` gets 403 unless the server was started with `--allow-model-verifiers`.
- **Untrusted text stays text.** Memory, evidence, file names, reviews, event logs, policy names and source URIs
  go through `memcore.htmlpage`: escaped, with control, bidi and other invisible characters shown as `U+XXXX`. Only
  `http` and `https` sources become links. The document is 7-bit ASCII.
- **Strict headers.** `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline';
  form-action 'self'; base-uri 'none'; frame-ancestors 'none'` (the same policy without `frame-ancestors` is also
  in a meta tag), plus `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`
  and `Cache-Control: no-store`. There is no JavaScript, no cookie and no external resource: styles are inline,
  icons are inline SVG, fonts are the system's, and the only images are the logo and favicon, served from the
  package at `/static/` by exact name.
- **Errors without details.** An error is one sentence. It never contains a traceback, a path outside the root,
  git's own diagnostics or the contents of a rejected file, and a value outside the root is not shown back in the
  form. The terminal log has one line per request: the method, a known route or `-`, and the status, never the
  query or a header.

## Limitations

- There is no login. Other programs and other users on the same computer can open the address and read what the
  page shows from the root, so run it on a computer you do not share, or choose a root you are happy to show.
- A page in another browser tab can make your browser send GET requests to the server, and so make it run checks,
  but it cannot read the answers, and no request changes anything.
- The page does not save reports. To share one, run the CLI with `--format html`.
- Lists stop after 5000 folders, and folders are offered as *Compare against* choices down to four levels.
- Library details in small print (finding details, skip reasons) stay in English on Chinese pages.
