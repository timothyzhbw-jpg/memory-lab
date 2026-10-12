"""A local, read-only web UI over MemFalsify and orgmemory, served by the standard library's ``http.server``.

Safety follows the MCP server (docs/mcp.md):

- It listens on 127.0.0.1 only and refuses any other host. A request must carry exactly one ``Host`` header
  naming 127.0.0.1 or localhost with the server's port, so a page on another site cannot reach it through
  DNS rebinding; requests from a non-loopback address are refused too.
- Only GET and HEAD are answered and every form uses GET: nothing a request does changes state. No page
  writes a file, appends an event, records a review or applies a patch.
- Every path in a request is resolved inside ``--root`` (``workspace.Workspace``), and each page runs inside
  ``memcore.confined_reads``, so a file swapped for an outward symlink after the check is still refused.
  Without POSIX ``O_NOFOLLOW`` and ``dir_fd`` support the server refuses to start.
- Model verifiers, which send text to external APIs and cost money, are refused unless the server was
  started with ``--allow-model-verifiers``.
- Every untrusted value is escaped by ``memcore.htmlpage``; pages carry a strict Content-Security-Policy, run
  no script and load nothing. Errors are short sentences: no traceback, no outside path, no file contents.
"""
from __future__ import annotations

import sys
from collections.abc import Iterable
from importlib import resources
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import typer

from memcore import confined_reads, confinement_supported

from . import check, decisions, home
from .i18n import language
from .pages import CSP, STATIC, language_switch, message_page, page
from .workspace import MAX_VALUE, Workspace

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_FIELDS = 32
ROUTES = ("/", "/check", "/decisions")
PATH_FIELDS = ("file", "evidence", "reviews", "dataset")
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
    ("Cache-Control", "max-age=3600"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
NOT_LOCAL = ("This page answers only at http://127.0.0.1 or http://localhost on the port memlab-ui printed. "
             "Open that address instead.")


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

    # --- checks before any page runs ----------------------------------------------------------------

    def host_allowed(self, values: list[str]) -> bool:
        """Exactly one Host header, naming this server on a loopback name (not a DNS name that resolves here)."""
        if len(values) != 1:
            return False
        allowed = {f"{HOST}:{self.port}", f"localhost:{self.port}"}
        if self.port == 80:
            allowed |= {HOST, "localhost"}
        return values[0].strip().lower() in allowed

    def handle(self, method: str, target: str, headers: Iterable[tuple[str, str]],
               client: str = HOST) -> Response:
        hosts = [value for name, value in headers if name.lower() == "host"]
        if not client.startswith("127.") or not self.host_allowed(hosts):
            return self.message(403, "Not available", NOT_LOCAL)
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
        with language("zh" if params.get("lang") == "zh" else "en"):
            # The switch links back to this page with the same fields, except a path outside the root.
            kept = {key: value for key, value in params.items()
                    if key not in PATH_FIELDS or not value or self.ws.inside(value)}
            switch = language_switch(path, kept)
            if path not in ROUTES:
                return self.message(404, "Page not found", "There is no such page. Use the links on the left.",
                                    switch)
            try:
                with confined_reads(self.ws.root):
                    status, html = self.page(path, params, switch)
            except Exception:  # never a traceback or a library message on the page
                return self.message(500, "Something went wrong",
                                    "The page could not be built. Check that the files are readable and valid.",
                                    switch)
        return self.respond(status, html)

    def parse(self, target: str) -> tuple[str, dict[str, str]]:
        parts = urlsplit(target)
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
            params[key] = value.strip()
        return parts.path, params

    # --- pages ----------------------------------------------------------------------------------------

    def page(self, path: str, params: dict[str, str], switch: str) -> tuple[int, str]:
        root = str(self.ws.root)
        if path == "/":
            body = home.render(self.ws.inventory(), models=self.models)
            return 200, page("Home", body, active="home", root=root, switch=switch)
        if path == "/check":
            status, body = check.render(self.ws, self.ws.inventory(count=False), params, models=self.models)
            return status, page("Check an agent file", body, active="check", root=root, switch=switch)
        status, body, topics = decisions.render(self.ws, self.ws.inventory(count=False), params)
        return status, page("Team decisions", body, active="decisions", root=root, topics=topics, switch=switch)

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


class Handler(BaseHTTPRequestHandler):
    """The HTTP side: hands each request to ``App.handle`` and writes the response. Logs one line per request
    with the method, a known route (or ``-``) and the status, never the query or a header value."""
    server_version = "memlab-ui"
    sys_version = ""
    timeout = 30
    server: "Server"

    def _serve(self) -> None:
        response = self.server.app.handle(self.command, self.path, list(self.headers.items()),
                                          self.client_address[0])
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
    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _serve

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Malformed requests and unknown methods get the same headers as every page, and no echo."""
        self.close_connection = True
        if self.request_version == "HTTP/0.9":  # a malformed request line: still send a status line and headers
            self.request_version = "HTTP/1.0"
        try:
            response = self.server.app.message(code, "Bad request", "The request could not be understood.")
            self._write(response)
        except Exception:
            pass

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        target = getattr(self, "path", None)
        route = urlsplit(target).path if isinstance(target, str) else "-"
        route = route if route in ROUTES else "-"
        command = getattr(self, "command", None)
        command = command if command in ("GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS") else "-"
        sys.stderr.write(f"memlab-ui: {command} {route} {code}\n")

    def log_message(self, format: str, *args) -> None:
        return None  # nothing from a request reaches the terminal except log_request's fixed fields


class Server(ThreadingHTTPServer):
    daemon_threads = True
    app: App


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
                              help="The project folder the page may read; nothing outside it is read"),
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
    typer.echo(f"Memory Lab is open at http://{HOST}:{server.server_address[1]}/")
    typer.echo("It reads only the folder given with --root and never changes a file. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    app()
