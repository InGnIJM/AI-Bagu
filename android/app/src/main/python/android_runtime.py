"""Process-owned Android runtime; no desktop paths or user configuration imports."""
import json
from pathlib import Path
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import bagu

_lock = threading.Lock()
_server = None
_thread = None
_paths = None
_identity = None
_info = None


def _origin(url):
    parsed = urllib.parse.urlsplit(url)
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    return parsed.scheme.lower(), parsed.hostname, port


class SecureRedirectHandler(urllib.request.HTTPRedirectHandler):
    """CPython sockets bypass Android XML: enforce transport policy in Python too."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(req.full_url).scheme == "https" and urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.HTTPError(req.full_url, code, "HTTPS downgrade blocked", headers, fp)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and _origin(req.full_url) != _origin(newurl):
            for header, _ in redirected.header_items():
                if header.lower() in {"authorization", "proxy-authorization", "cookie"}:
                    redirected.remove_header(header)
        return redirected


def start(files_dir: str, static_root: str, seed_path: str, variant: str) -> str:
    """Start once per process, keeping the port/token stable through Activity recreation."""
    global _server, _thread, _paths, _identity, _info
    if variant not in ("internal", "public"):
        raise ValueError("Unknown Android distribution variant")
    private = Path(files_dir).resolve()
    static = Path(static_root).resolve()
    seed = Path(seed_path).resolve()
    identity = (private, static, seed, variant)
    with _lock:
        if _server is not None:
            if identity != _identity:
                raise ValueError("Android process runtime already belongs to another directory")
            return json.dumps(_info)
        if not (static / "web/index.html").is_file():
            raise ValueError("Missing bundled application page")
        paths = bagu.AppPaths(private / "data", private / "config", static, private / "logs")
        for directory in (paths.data_dir, paths.config_dir, paths.log_dir):
            directory.mkdir(parents=True, exist_ok=True)
        bagu.prepare_mobile_database(paths.db_path, seed)
        bagu.configure_logging(log_dir=paths.log_dir)
        urllib.request.install_opener(urllib.request.build_opener(SecureRedirectHandler()))
        token = secrets.token_urlsafe(32)
        handler = bagu.make_http_handler(
            root=paths.config_dir, db_path=paths.db_path, static_root=paths.static_dir,
            access_token=token, android=True,
        )

        class AndroidHandler(handler):
            def end_headers(self):
                # The bridge is visible in every WebView frame. Disallow frames and
                # remote executable content, while keeping reference images usable.
                self.send_header("Content-Security-Policy", (
                    "default-src 'none'; script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; "
                    "font-src 'self'; connect-src 'self'; frame-src 'none'; child-src 'none'; "
                    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                ))
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                super().end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), AndroidHandler)
        server.daemon_threads = True
        port = server.server_address[1]
        query = urllib.parse.urlencode({"platform": "android", "variant": variant, "token": token})
        info = {"url": f"http://127.0.0.1:{port}/?{query}", "port": port}
        thread = threading.Thread(target=server.serve_forever, name="bagu-loopback", daemon=True)
        try:
            thread.start()
        except Exception:
            server.server_close()
            raise
        _server, _thread, _paths, _identity, _info = server, thread, paths, identity, info
        return json.dumps(info)


def _connection():
    if _paths is None:
        raise RuntimeError("Android runtime has not started")
    return bagu.get_conn(_paths.db_path)


def export_archive() -> bytes:
    conn = _connection()
    try:
        return bagu.export_backup(conn)
    finally:
        conn.close()


def restore_archive(data) -> str:
    conn = _connection()
    try:
        return json.dumps(bagu.restore_backup(conn, bytes(data)))
    finally:
        conn.close()
