"""Mobile web app served on your LAN (standard library only).

``marksman web`` starts a small HTTP server so a phone on the same Wi-Fi can
log sessions (tap shots onto the target face), follow the drill plan and see
progress.  Desktop browsers work too -- it is the same data file the CLI and
the tkinter app use, so everything stays in sync.

Security: every request must carry a per-run random token, printed as part
of the URL and then kept in a cookie.  That keeps casual LAN neighbours out;
it is NOT hardened for the open internet -- don't port-forward it.

The page itself is ``webapp/index.html`` beside this module, and the API is
:mod:`marksman.webapi` -- both shared with the installable build of the app,
so the phone, the desktop and this server run the same code.
"""
from __future__ import annotations

import hmac
import html
import json
import os
import secrets
import socket
import threading
from http import cookies as http_cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from . import packs as packs_mod
from .storage import Database, DEFAULT_DB_PATH
from .webapi import (DEFAULT_PORT, MAX_BODY, _Bad, _icon_png, _state,
                     dispatch, download)

WEBAPP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")

def _webapp_file(name: str) -> bytes:
    """One of the page's own static files, from the installed package."""
    with open(os.path.join(WEBAPP, name), "rb") as fh:
        return fh.read()


PAGE = _webapp_file("index.html").decode("utf-8")

# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):

    def log_message(self, *args):            # keep the terminal quiet
        pass

    def _authed(self) -> bool:
        token = self.server.token
        q = parse_qs(urlparse(self.path).query).get("k", [""])[0]
        if q and hmac.compare_digest(q, token):
            return True
        jar = http_cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return "k" in jar and hmac.compare_digest(jar["k"].value, token)

    def _send(self, code: int, body, ctype: str = "application/json",
              extra=None) -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/boot.js":
            # Static, no user data: the local build of this is a comment.
            self._send(200, _webapp_file("boot.js"), "text/javascript; charset=utf-8",
                       [("Cache-Control", "max-age=86400")])
            return
        if path == "/icon.png":
            # No user data in a logo, and the manifest fetches it without
            # credentials -- so this one is open.
            self._send(200, _icon_png(), "image/png",
                       [("Cache-Control", "max-age=86400")])
            return
        if not self._authed():
            self._send(403, b"Missing or bad access code. Open the full URL "
                            b"printed by 'marksman web'.", "text/plain; charset=utf-8")
            return
        if path == "/":
            cookie = "k=%s; Path=/; HttpOnly; SameSite=Lax" % self.server.token
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8",
                       [("Set-Cookie", cookie)])
        elif path == "/api/state":
            self._send(200, _state(Database.load(self.server.db_path)))
        elif path == "/manifest.webmanifest":
            self._send(200, json.dumps(self._manifest()).encode("utf-8"),
                       "application/manifest+json")
        elif path in ("/target.html", "/export.csv", "/export.json"):
            try:
                mime, data = download(self.server, path[1:], parse_qs(parsed.query))
            except _Bad as e:
                self._send(400, str(e).encode("utf-8"),
                           "text/plain; charset=utf-8")
                return
            extra = []
            if path != "/target.html":
                extra = [("Content-Disposition", 'attachment; filename="%s"'
                          % ("marksman-sessions" + path[7:]))]
            self._send(200, data, mime, extra)
        else:
            self._send(404, {"error": "not found"})

    def _manifest(self) -> Dict[str, Any]:
        """Enough for "add to home screen" to give a real app icon."""
        return {
            "name": "Marksman", "short_name": "Marksman",
            "description": "Foam dart drills and progress tracking.",
            # The key rides along so an installed shortcut keeps working.
            "start_url": "/?k=" + self.server.token,
            "scope": "/", "display": "standalone", "orientation": "any",
            "background_color": "#141821", "theme_color": "#141821",
            "icons": [{"src": "/icon.png", "sizes": "512x512",
                       "type": "image/png", "purpose": "any maskable"}],
        }

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._authed():
            self._send(403, {"error": "bad access code"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._send(413, {"error": "body too large"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise _Bad("expected a JSON object")
            resp = dispatch(self.server, path, body)
        except _Bad as e:
            self._send(400, {"error": str(e)})
            return
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"error": "invalid JSON"})
            return
        self._send(200, resp)


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:            # UDP connect sends nothing; it just picks the outbound iface
        s.connect(("192.0.2.1", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def make_server(db_path: str = DEFAULT_DB_PATH, host: str = "",
                port: int = DEFAULT_PORT, token: Optional[str] = None
                ) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.db_path = db_path
    httpd.lock = threading.Lock()
    db = Database.load(db_path)
    if not token:
        # Keep the key across runs, or every restart breaks the phone's
        # bookmark and its installed icon. Rotate it with --new-key.
        token = db.settings.get("web_key")
        if not token:
            token = secrets.token_urlsafe(8)
            db.settings["web_key"] = token
            db.save()
    httpd.token = token
    packs_mod.load(db)                         # content: drills, faces, terms
    return httpd


def serve(db_path: str = DEFAULT_DB_PATH, host: str = "",
          port: int = DEFAULT_PORT) -> int:
    """Run the server until Ctrl+C.  Returns a process exit code."""
    try:
        httpd = make_server(db_path, host, port)
    except OSError as e:
        print("error: can't listen on port %d (%s). Try --port." % (port, e))
        return 1
    bound = httpd.server_address[1]
    print("Marksman web is running (Ctrl+C to stop).")
    print("  this machine:  http://127.0.0.1:%d/?k=%s" % (bound, httpd.token))
    print("  your phone:    http://%s:%d/?k=%s   (same Wi-Fi)"
          % (_lan_ip(), bound, httpd.token))
    print("The link carries an access code that stays the same across runs, so")
    print("you can bookmark it or add it to your phone's home screen. Anyone on")
    print("your network with the full link can view and add sessions -- don't")
    print("expose it to the internet. Rotate it with: marksman web --new-key")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


# --------------------------------------------------------------------------- #
# The page (inline: no build step, no framework, no external requests)
# --------------------------------------------------------------------------- #
