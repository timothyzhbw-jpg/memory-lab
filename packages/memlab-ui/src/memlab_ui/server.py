"""A local, read-only web UI over MemFalsify and orgmemory, served by the standard library's ``http.server``.

Safety follows the MCP server (docs/mcp.md):

- It listens on 127.0.0.1 only and refuses any other host. A request must carry exactly one ``Host`` header
  naming 127.0.0.1 or localhost with the server's port, so a page on another site cannot reach it through
  DNS rebinding; requests from a non-loopback address are refused too.
- A request another site sent (``Sec-Fetch-Site`` other than ``same-origin`` or ``none``, or an ``Origin``
  that is not this server's) is refused before any work, so a page elsewhere cannot make it run checks, and
  at most ``MAX_PAGES`` pages are computed at once (503 beyond that).
- At most ``MAX_CONNECTIONS`` connections are served at once (more are closed at once), and the request line
  and headers must arrive within ``HEADER_DEADLINE`` seconds in total, so slow clients cannot pin threads.
- Only GET and HEAD are answered (405 for every other method) and every form uses GET: nothing a request does
  changes state. No page writes a file, appends an event, records a review or applies a patch.
- Every path in a request is resolved inside ``--root`` (``workspace.Workspace``), and each page runs inside
  ``memcore.confined_reads``, so a file swapped for an outward symlink after the check is still refused.
  Without POSIX ``O_NOFOLLOW`` and ``dir_fd`` support the server refuses to start. Each page also runs within
  ``read_limits``: bytes per file and per request, and directories walked by discovery, the lists and the
  evidence scan together; a page that hit a limit says its view is incomplete.
- Model verifiers, which send text to external APIs and cost money, are refused unless the server was
  started with ``--allow-model-verifiers``.
- Every untrusted value is escaped by ``memcore.htmlpage``; pages carry a strict Content-Security-Policy, run
  no script and load nothing. Errors are short sentences: no traceback, no outside path, no file contents.
"""
from __future__ import annotations

import hmac
import io
import secrets
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import typer

from memcore import confined_reads, confinement_supported
from memcore.reading import read_limits

from . import check, decisions, home
from .i18n import _, language
from .pages import CSP, STATIC, language_switch, message_page, notice, page, request_key
from .workspace import MAX_VALUE, Workspace

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_FIELDS = 32
ROUTES = ("/", "/check", "/decisions")
QUERY_KEYS = ("file", "evidence", "reviews", "as_of", "since", "verifier", "dataset", "subject", "from", "to")
PATH_FIELDS = ("file", "evidence", "reviews", "dataset")
MAX_CONNECTIONS = 16       # connections served at once; more are closed straight away
MAX_PAGES = 2              # pages computed at once; more get 503
HEADER_DEADLINE = 10.0     # seconds for the request line and all headers together
FILE_BYTES = 8_000_000     # per file read for a page (evidence files keep MemFalsify's own 2 MB limit)
TOTAL_BYTES = 64_000_000   # all files read for one page
DIRECTORIES = 20_000       # directories one page may walk: discovery, the lists and the evidence scan together
DEPTH = 64                 # levels below a walk's start
ENTRIES = 100_000          # directory entries one page may list
ITEMS = {"claims": 5_000, "passages": 5_000, "comparisons": 2_000_000}  # statements parsed, evidence passages
# built, claim-passage and old-new statement comparisons: each is counted while it is built
TRIMMED = ("as_of", "since", "verifier", "lang", "from", "to")  # fields whose spaces mean nothing
HEADERS = (
    ("Content-Type", "text/html; charset=utf-8"),
    ("Content-Security-Policy", CSP + "; frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
ASSET_HEADERS = (
    ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "max-age=3600"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
NOT_LOCAL = ("This page answers only at http://127.0.0.1 or http://localhost on the port memlab-ui printed. "
             "Open that address instead.")
CROSS_SITE = ("This request came from another website, so it was not run. Open the page from the address "
              "memlab-ui printed.")
BUSY = "Memory Lab is busy with other pages. Wait a moment and try again."
LARGE = "This project is large, so only part of it was read. Results may be incomplete."
FAILED = (b"<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>Something went wrong</title>"
          b"</head><body><p>Something went wrong. Reload the page.</p></body></html>\n")


@dataclass
class Response:
    status: int
    body: bytes
    headers: list[tuple[str, str]] = field(default_factory=list)


class BadRequest(Exception):
    """The request itself is malformed; the message is a fixed English message key."""


def static_files() -> dict[str, bytes]:
    """The logo and favicon images shipped as package data, read once."""
    folder = resources.files("memlab_ui").joinpath("static")
    return {name: folder.joinpath(name).read_bytes() for name in STATIC}


class App:
    """Turns one request (method, target, headers) into one response. No socket: the HTTP handler and the
    tests both call ``handle``."""

    def __init__(self, root: Path | str, *, allow_model_verifiers: bool = False, port: int = 0):
        if not confinement_supported():
            raise ValueError("memlab-ui needs POSIX O_NOFOLLOW and dir_fd support for confined reads")
        self.ws = Workspace(root)
        self.models = allow_model_verifiers
        self.port = port
        self.static = static_files()
        self.pages = threading.BoundedSemaphore(MAX_PAGES)
        # With model verifiers, a random key printed only in the terminal: model checks need it (like Jupyter).
        self.key = secrets.token_urlsafe(24) if allow_model_verifiers else None

    # --- checks before any page runs ----------------------------------------------------------------

    def host_allowed(self, values: list[str]) -> bool:
        """Exactly one Host header, naming this server on a loopback name (not a DNS name that resolves here)."""
        if len(values) != 1:
            return False
        allowed = {f"{HOST}:{self.port}", f"localhost:{self.port}"}
        if self.port == 80:
            allowed |= {HOST, "localhost"}
        return values[0].strip().lower() in allowed

    @staticmethod
    def same_origin(headers: list[tuple[str, str]], host: str) -> bool:
        """Not sent by another site: Sec-Fetch-Site, when present, is same-origin or none (typed, bookmarked),
        and Origin, when present, is exactly this server's origin as the browser addressed it."""
        sites = [value.strip().lower() for name, value in headers if name.lower() == "sec-fetch-site"]
        origins = [value.strip().lower() for name, value in headers if name.lower() == "origin"]
        if any(site not in ("same-origin", "none") for site in sites) or len(sites) > 1:
            return False
        return not origins or (len(origins) == 1 and origins[0] == f"http://{host.strip().lower()}")

    def handle(self, method: str, target: str, headers: Iterable[tuple[str, str]],
               client: str = HOST) -> Response:
        headers = list(headers)
        hosts = [value for name, value in headers if name.lower() == "host"]
        if not client.startswith("127.") or not self.host_allowed(hosts):
            return self.message(403, "Not available", NOT_LOCAL)
        if not self.same_origin(headers, hosts[0]):
            return self.message(403, "Not available", CROSS_SITE)
        if method not in ("GET", "HEAD"):
            response = self.message(405, "Not possible here",
                                    "This page only shows things: it never changes or uploads anything.")
            response.headers.append(("Allow", "GET, HEAD"))
            return response
        try:
            path, params = self.parse(target)
        except BadRequest as exc:
            return self.message(400, "Bad request", str(exc))
        if path.startswith("/static/") or path == "/favicon.ico":
            return self.asset(path)
        with language("zh" if params.get("lang") == "zh" else "en"), request_key(self.keyed(params)):
            # The switch links to a known page with known fields only, minus any path outside the root.
            kept = {key: value for key, value in params.items() if key in QUERY_KEYS
                    and (key not in PATH_FIELDS or not value or self.ws.inside(value))}
            switch = language_switch(path, kept) if path in ROUTES else language_switch("/", {})
            if path not in ROUTES:
                return self.message(404, "Page not found", "There is no such page. Use the links on the left.",
                                    switch)
            if not self.pages.acquire(blocking=False):
                response = self.message(503, "Busy", BUSY, switch)
                response.headers.append(("Retry-After", "5"))
                return response
            try:
                with confined_reads(self.ws.root), read_limits(FILE_BYTES, TOTAL_BYTES, DIRECTORIES, depth=DEPTH,
                                                               entries=ENTRIES, items=ITEMS) as limits:
                    status, html = self.page(path, params, switch, limits)
            except Exception:  # never a traceback or a library message on the page
                return self.message(500, "Something went wrong",
                                    "The page could not be built. Check that the files are readable and valid.",
                                    switch)
            finally:
                self.pages.release()
        return self.respond(status, html)

    def keyed(self, params: dict[str, str]) -> str | None:
        """The run's key when the request carried it, compared as bytes in constant time; None otherwise."""
        given = params.get("key")
        if self.key is None or given is None:
            return None
        if hmac.compare_digest(given.encode("utf-8", "surrogatepass"), self.key.encode("ascii")):
            return self.key
        return None

    def parse(self, target: str) -> tuple[str, dict[str, str]]:
        try:
            parts = urlsplit(target)
        except ValueError:  # e.g. an unclosed IPv6 bracket in an absolute-form target
            raise BadRequest("The address is not a path on this server.") from None
        if parts.scheme or parts.netloc or not parts.path.startswith("/"):
            raise BadRequest("The address is not a path on this server.")
        try:
            pairs = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=MAX_FIELDS)
        except ValueError:
            raise BadRequest("The address has too many fields.") from None
        params: dict[str, str] = {}
        for key, value in pairs:
            if key in params:
                raise BadRequest("Each field may appear only once in the address.")
            if len(value) > MAX_VALUE:
                raise BadRequest("A field in the address is too long.")
            params[key] = value.strip() if key in TRIMMED else value  # paths and subjects stay exact
        return parts.path, params

    # --- pages ----------------------------------------------------------------------------------------

    def page(self, path: str, params: dict[str, str], switch: str, limits) -> tuple[int, str]:
        root = str(self.ws.root)
        topics = ""
        if path == "/":
            status, title, active = 200, "Home", "home"
            body = home.render(self.ws.inventory(), models=self.models)
        elif path == "/check":
            title, active = "Check an agent file", "check"
            status, body = check.render(self.ws, self.ws.inventory(count=False), params, models=self.models,
                                        keyed=self.keyed(params) is not None)
        else:
            title, active = "Team decisions", "decisions"
            status, body, topics = decisions.render(self.ws, self.ws.inventory(count=False), params)
        if limits.truncated:  # one notice, whichever walk or read hit a limit
            body = notice(_(LARGE)) + "\n" + body
        return status, page(title, body, active=active, root=root, topics=topics, switch=switch)

    def asset(self, path: str) -> Response:
        """A shipped image by its exact name; nothing else is served from disk."""
        name = "memory-lab-icon-32.png" if path == "/favicon.ico" else path.removeprefix("/static/")
        if name not in self.static:
            return self.message(404, "Page not found", "There is no such page. Use the links on the left.")
        return Response(200, self.static[name], [("Content-Type", "image/png"), *ASSET_HEADERS])

    def respond(self, status: int, html: str) -> Response:
        return Response(status, html.encode("ascii"), list(HEADERS))

    def message(self, status: int, title: str, text: str, switch: str = "") -> Response:
        """A page with one sentence; ``title`` and ``text`` are English message keys."""
        return self.respond(status, message_page(title, text, root=str(self.ws.root), switch=switch))


class _HeaderDeadline(io.RawIOBase):
    """Reads the socket, but only until a fixed moment: the request line and headers must arrive within
    ``HEADER_DEADLINE`` seconds in total, however slowly a client trickles bytes."""

    def __init__(self, sock, deadline: float) -> None:
        super().__init__()
        self.sock, self.deadline = sock, deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("request headers took too long")
        self.sock.settimeout(min(left, Handler.timeout))
        return self.sock.recv_into(buffer)


class Handler(BaseHTTPRequestHandler):
    """The HTTP side: hands each request to ``App.handle`` and writes the response. Logs one line per request
    with the method, a known route (or ``-``) and the status, never the query or a header value."""
    server_version = "memlab-ui"
    sys_version = ""
    timeout = 30
    server: "Server"

    def setup(self) -> None:
        super().setup()
        self.rfile.close()
        self.rfile = io.BufferedReader(_HeaderDeadline(self.connection, time.monotonic() + HEADER_DEADLINE))

    def parse_request(self) -> bool:
        """HTTP/0.9 ("GET /" without a version) gets no headers in reply, so it is refused, framed as HTTP/1.0."""
        if not super().parse_request():
            return False
        if self.request_version == "HTTP/0.9":
            self.send_error(400)
            return False
        return True

    def __getattr__(self, name: str):
        """Every other method is answered by ``App.handle`` too: 405, with the usual headers."""
        if name.startswith("do_"):
            return self._serve
        raise AttributeError(name)

    def _serve(self) -> None:
        self.connection.settimeout(self.timeout)  # the headers are in; the reply gets the ordinary timeout
        try:
            response = self.server.app.handle(self.command, self.path, list(self.headers.items()),
                                              self.client_address[0])
        except Exception:  # App.handle answers every error itself; this is the last resort
            sys.stderr.write("memlab-ui: internal error\n")
            response = Response(500, FAILED, list(HEADERS))
        self._write(response)

    def _write(self, response: Response) -> None:
        self.send_response(response.status)
        for name, value in response.headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    do_GET = do_HEAD = _serve

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Malformed requests get the same headers as every page, framed as HTTP/1.0, and no echo."""
        self.close_connection = True
        if self.request_version == "HTTP/0.9":
            self.request_version = "HTTP/1.0"
        try:
            response = self.server.app.message(code, "Bad request", "The request could not be understood.")
            self._write(response)
        except Exception:
            pass

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        try:
            target = getattr(self, "path", None)
            route = urlsplit(target).path if isinstance(target, str) else "-"
        except ValueError:
            route = "-"
        route = route if route in ROUTES else "-"
        command = getattr(self, "command", None)
        command = command if command in ("GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS") else "-"
        sys.stderr.write(f"memlab-ui: {command} {route} {code}\n")

    def log_message(self, format: str, *args) -> None:
        return None  # nothing from a request reaches the terminal except log_request's fixed fields


class Server(ThreadingHTTPServer):
    """At most ``MAX_CONNECTIONS`` connections at once: a further one is closed without a thread."""
    daemon_threads = True
    app: App

    def server_bind(self) -> None:
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().server_bind()

    def process_request(self, request, client_address) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address) -> None:
        sys.stderr.write("memlab-ui: a connection failed\n")  # never a traceback with request data


def create_server(root: Path | str = ".", port: int = 0, allow_model_verifiers: bool = False,
                  host: str = HOST) -> ThreadingHTTPServer:
    """Bind the read-only UI to ``host``:``port`` (port 0 picks a free one; see ``server_address``) without
    serving yet: call ``serve_forever()``. Only 127.0.0.1 is accepted as ``host``. Raises ``ValueError`` for
    another host, a root that is not a directory, or a platform without confined reads, and ``OSError``
    when the port cannot be bound."""
    if host != HOST:
        raise ValueError("memlab-ui serves only on 127.0.0.1: it has no login, so other machines must not reach it")
    app = App(root, allow_model_verifiers=allow_model_verifiers)
    server = Server((HOST, port), Handler)
    server.app = app
    app.port = server.server_address[1]
    return server


app = typer.Typer(add_completion=False, help="Serve Memory Lab's read-only web page on 127.0.0.1.")


@app.command()
def serve(
    root: Path = typer.Option(Path("."), "--root", exists=True, file_okay=False, resolve_path=True,
                              help="The project folder the page may read. Paths outside it are refused; "
                                   "a file hard-linked into it from elsewhere is read like any other (docs/ui.md)"),
    port: int = typer.Option(DEFAULT_PORT, "--port", min=0, max=65535,
                             help="Port on 127.0.0.1 (0 picks a free one)"),
    host: str = typer.Option(HOST, "--host", help="Only 127.0.0.1 is accepted: the page has no login"),
    allow_model_verifiers: bool = typer.Option(
        False, "--allow-model-verifiers",
        help="Offer the llm/jev/dual/cascade verifiers, which send memory and evidence text to external APIs "
             "and cost money"),
) -> None:
    """Start the page and print its address. Press Ctrl+C to stop."""
    try:
        server = create_server(root, port, allow_model_verifiers, host)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    except OSError:
        typer.echo(f"error: cannot listen on {HOST}:{port}; another program may be using it (try --port 0)",
                   err=True)
        raise typer.Exit(2) from None
    address = f"http://{HOST}:{server.server_address[1]}/"
    if server.app.key is not None:  # printed here only: pages never show it to a visitor without it
        typer.echo(f"Memory Lab is open at {address}?key={server.app.key}")
        typer.echo("Model checks need this address with its key. Pages opened without it offer quick checks only.")
    else:
        typer.echo(f"Memory Lab is open at {address}")
    typer.echo("It reads only the folder given with --root (and files hard-linked into it) and never changes a "
               "file. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    app()
