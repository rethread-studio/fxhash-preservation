"""Shared fixtures: synthetic source trees and a smart-HTTP git server (git http-backend)."""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from swhprep._convert import Identity

IDENT = Identity.parse("Test Archive <archive@example.org>", "2020-01-01T00:00:00+00:00")


def make_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root


@pytest.fixture
def ident() -> Identity:
    return IDENT


class _CGIHandler(BaseHTTPRequestHandler):
    project_root: str = ""
    url_prefix: str = "/"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _run(self) -> None:
        parts = urlsplit(self.path)
        if not parts.path.startswith(self.url_prefix):
            self.send_error(404)
            return
        path_info = "/" + parts.path[len(self.url_prefix):]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        env = {
            **os.environ,
            "GIT_PROJECT_ROOT": self.project_root,
            "GIT_HTTP_EXPORT_ALL": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "REQUEST_METHOD": self.command,
            "PATH_INFO": path_info,
            "QUERY_STRING": parts.query,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "REMOTE_ADDR": "127.0.0.1",
        }
        if self.headers.get("Git-Protocol"):
            env["GIT_PROTOCOL"] = self.headers["Git-Protocol"]
        out = subprocess.run(["git", "http-backend"], input=body, env=env,
                             capture_output=True, check=False).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        status = 200
        headers = []
        for line in head.split(b"\r\n"):
            k, _, v = line.decode().partition(":")
            if k.lower() == "status":
                status = int(v.strip().split()[0])
            elif k:
                headers.append((k, v.strip()))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _run
    do_POST = _run


@pytest.fixture
def git_http() -> Iterator[object]:
    """Factory: serve(root, prefix='/') -> base URL of a smart-HTTP server over `root`."""
    servers: list[ThreadingHTTPServer] = []

    def serve(root: Path, prefix: str = "/") -> str:
        handler = type("H", (_CGIHandler,), {"project_root": str(root), "url_prefix": prefix})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}{prefix}"

    yield serve
    for s in servers:
        s.shutdown()
