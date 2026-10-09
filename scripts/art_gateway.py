#!/usr/bin/env python3
"""Art gateway: serve fxhash artworks from the forge's bare git repos (stdlib only).

    python3 scripts/art_gateway.py INDEX.json.gz GITROOT [--host 127.0.0.1] [--port 8095]
        [--domain fxhash.gakoy.com]

Behind nginx. `<label>.<domain>/<path>` serves `<commit>:<path>` of the repo the index
(art_gateway_index.py) maps the label to; `/` and folders serve their `index.html`;
`_source.txt` (ours, not the artwork's) is hidden. `<domain>/ipfs/<cid>/<path>?q` and
`<domain>/onchfs/<root>/<path>?q` redirect (302) to `<label>.<domain>/<path>?q`, so a token's
`artifactUri` works by replacing `ipfs://` with `https://<domain>/ipfs/`. Every artwork has
its own origin; the policy runs inline and evaluated scripts but loads nothing from
elsewhere. `X-Fxhash-Verified` tells how the served tree relates to the on-chain hash.
"""

from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import os
import posixpath
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from art_gateway_index import cid_label, onchfs_label

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
CSP = ("default-src 'self' data: blob:; "
       "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' data: blob:; "
       "style-src 'self' 'unsafe-inline' data:; img-src 'self' data: blob:; "
       "media-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' data: blob:; "
       "worker-src 'self' data: blob:; frame-src 'self' data: blob:; form-action 'none'; "
       "base-uri 'self'")
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".wasm": "application/wasm",
         ".json": "application/json", ".glsl": "text/plain", ".frag": "text/plain",
         ".vert": "text/plain", ".txt": "text/plain", ".md": "text/plain",
         ".html": "text/html", ".htm": "text/html", ".css": "text/css",
         ".svg": "image/svg+xml"}
HIDDEN = {"_source.txt"}
LANDING = """fxhash art gateway: {n} artworks served from git repos whose trees rebuild the
code hash stored on chain (https://fxhash-preservation.rethread.art/).

A token's artifactUri, with its scheme replaced:
  ipfs://<cid>/?fxhash=...     -> https://{domain}/ipfs/<cid>/?fxhash=...
  onchfs://<root>/?fxhash=...  -> https://{domain}/onchfs/<root>/?fxhash=...
Each artwork is served from its own host, <label>.{domain}.
"""


def content_type(path: str) -> str:
    ext = posixpath.splitext(path)[1].lower()
    t = TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
    return t + ("; charset=utf-8" if t.startswith("text/") or t.endswith(("json", "xml"))
                else "")


class Gateway:
    def __init__(self, index: dict[str, dict[str, Any]], gitroot: Path, domain: str):
        self.index, self.gitroot, self.domain = index, gitroot, domain

    def git(self, repo: str, *args: str, binary: bool = False) -> Any:
        # the repos belong to the forge's user, not to the one this runs as
        p = subprocess.run(["git", "-c", "safe.directory=*",
                            f"--git-dir={self.gitroot / repo}.git", *args],
                           capture_output=True, env=ENV, check=False)
        return (p.stdout if binary else p.stdout.decode()) if p.returncode == 0 else None

    def resolve(self, label: str, path: str) -> tuple[int, dict[str, str], bytes]:
        e = self.index.get(label)
        if not e:
            return 404, {}, b"unknown artwork\n"
        rel = posixpath.normpath(urllib.parse.unquote(path).lstrip("/"))
        rel = "" if rel == "." else rel
        if rel.startswith("..") or rel in HIDDEN:
            return 404, {}, b"not found\n"
        spec = f"{e['commit']}:{rel}"
        kind = self.git(e["repo"], "cat-file", "-t", spec)
        if kind and kind.strip() == "tree":
            rel = posixpath.join(rel, "index.html") if rel else "index.html"
            if not path.endswith("/") and path not in ("", "/"):
                return 301, {"Location": "/" + rel.removesuffix("index.html")}, b""
            spec = f"{e['commit']}:{rel}"
            kind = self.git(e["repo"], "cat-file", "-t", spec)
        if not kind or kind.strip() != "blob":
            return 404, {}, b"not found\n"
        body = self.git(e["repo"], "cat-file", "blob", spec, binary=True)
        blob = (self.git(e["repo"], "rev-parse", spec) or "").strip()
        return 200, {"Content-Type": content_type(rel), "ETag": f'"{blob}"',
                     "Cache-Control": "public, max-age=31536000, immutable",
                     "X-Fxhash-Verified": e["verified"], "X-Fxhash-Uri": e["uri"]}, body

    def redirect(self, path: str, query: str) -> tuple[int, dict[str, str], bytes]:
        if path in ("", "/"):
            return 200, {}, LANDING.format(domain=self.domain, n=len(self.index)).encode()
        parts = path.lstrip("/").split("/", 2)
        if len(parts) < 2 or parts[0] not in ("ipfs", "onchfs"):
            return 404, {}, b"not found\n"
        try:
            label = cid_label(parts[1]) if parts[0] == "ipfs" else onchfs_label(parts[1])
        except ValueError:
            return 404, {}, b"not a CID\n"
        if label not in self.index:
            return 404, {}, b"unknown artwork\n"
        rest = parts[2] if len(parts) > 2 else ""
        loc = f"https://{label}.{self.domain}/{rest}" + (f"?{query}" if query else "")
        return 302, {"Location": loc}, b""


def make_handler(gw: Gateway) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "fxhash-art-gateway"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def handle_one(self, head_only: bool) -> None:
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            u = urllib.parse.urlsplit(self.path)
            if host == gw.domain:
                status, headers, body = gw.redirect(u.path, u.query)
            elif host.endswith("." + gw.domain):
                status, headers, body = gw.resolve(host[:-len(gw.domain) - 1], u.path)
            else:
                status, headers, body = 404, {}, b"unknown host\n"
            if self.headers.get("If-None-Match") and \
                    self.headers["If-None-Match"] == headers.get("ETag"):
                status, body = 304, b""
            self.send_response(status)
            headers.setdefault("Content-Type", "text/plain; charset=utf-8")
            for k, v in {**headers, "Content-Security-Policy": CSP,
                         "X-Content-Type-Options": "nosniff",
                         "Access-Control-Allow-Origin": "*",
                         "Content-Length": str(len(body))}.items():
                self.send_header(k, v)
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def do_GET(self) -> None:
            self.handle_one(False)

        def do_HEAD(self) -> None:
            self.handle_one(True)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("index")
    ap.add_argument("gitroot")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--domain", default="fxhash.gakoy.com")
    a = ap.parse_args()
    with gzip.open(a.index, "rt") as f:
        index = json.load(f)
    gw = Gateway(index, Path(a.gitroot), a.domain)
    ThreadingHTTPServer((a.host, a.port), make_handler(gw)).serve_forever()


if __name__ == "__main__":
    main()
