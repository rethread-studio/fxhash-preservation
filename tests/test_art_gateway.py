"""Tests for the git-backed art gateway (scripts/art_gateway*.py)."""

from __future__ import annotations

import gzip
import json
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from swhprep._convert import Identity, convert_one
from tests.conftest import make_tree

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import art_gateway
import art_gateway_index

CID = "QmNetgRYXjGDXkEBYid64SXJvuRc4STLzmH2ZCB91mqAf6"
ROOT = "4079937a8321bdedd68837e1844b032ca06e9755814db189957b6ed180851cf3"


def test_labels() -> None:
    # reference: `ipfs cid base32 QmNetg…`
    assert art_gateway_index.cid_label(CID) == (
        "bafybeiaevragtdi2mrhije3on5gkjs33pf2bzcu5lmka4ucvqzeme6lpeu")
    lab = art_gateway_index.onchfs_label(ROOT)
    assert lab.startswith("o") and len(lab) == 53 and lab.isalnum() and lab == lab.lower()
    assert art_gateway_index.label_of(f"ipfs://{CID}/?fxhash=x") == \
        art_gateway_index.cid_label(CID)
    assert art_gateway_index.label_of(f"onchfs://{ROOT}") == lab
    assert art_gateway_index.label_of("https://example.org/x") is None


LIB = "https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.4.1/p5.min.js"
FONT_CSS = "https://fonts.googleapis.com/css2?family=Roboto"
FONT = "https://fonts.gstatic.com/s/roboto/v51/a.woff2"


def start(tmp_path: Path, ident: Identity, ext: art_gateway.ExtArchive | None,
          files: dict[str, str]) -> tuple[ThreadingHTTPServer, str]:
    src = make_tree(tmp_path / "src" / "p" / "2023" / "Art__1", {
        **files, "_source.txt": f"id: 1\nname: Art\ngenerative_uri: ipfs://{CID}\n"})
    git = tmp_path / "git"
    convert_one(src, git / "p" / "2023" / "Art__1.git", ident)
    index_p = tmp_path / "index.json.gz"
    sys.argv = ["x", str(git), str(index_p)]
    art_gateway_index.main()
    with gzip.open(index_p, "rt") as f:
        index = json.load(f)
    gw = art_gateway.Gateway(index, git, "art.test", ext)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), art_gateway.make_handler(gw))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture
def served(tmp_path: Path, ident: Identity) -> Iterator[str]:
    srv, base = start(tmp_path, ident, None, {
        "index.html": "<script>draw()</script>\n", "lib/a.js": "x\r\n",
        "sub/index.html": "sub\n"})
    yield base
    srv.shutdown()


@pytest.fixture
def served_ext(tmp_path: Path, ident: Identity) -> Iterator[str]:
    archive = tmp_path / "ext"
    rows = []
    for url, body, ctype, integrity in (
            (LIB, "p5()", "application/javascript", "ok"),
            (FONT_CSS, f"@font-face{{src:url({FONT})}}", "text/css", None),
            (FONT, "woff", "font/woff2", None),
            ("https://cdn.jsdelivr.net/npm/tampered.js", "evil()", "text/javascript",
             "mismatch")):
        local = url.split("//", 1)[1].split("?")[0]
        (archive / "files" / local).parent.mkdir(parents=True, exist_ok=True)
        (archive / "files" / local).write_text(body)
        rows.append({"url": url, "local": local, "status": 200, "content_type": ctype,
                     "integrity": integrity})
    (archive / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    srv, base = start(tmp_path, ident, art_gateway.ExtArchive(archive), {
        "index.html": f'<script src="{LIB}"></script><script src="'
                      f'{LIB.removeprefix("https:")}"></script>'
                      f'<link href="{FONT_CSS}" rel="stylesheet">'
                      '<script src="https://cdn.jsdelivr.net/npm/tampered.js"></script>'
                      '<script src="https://unpkg.com/other.js"></script>\n',
        "a.js": "x\r\n"})
    yield base
    srv.shutdown()


def get(base: str, host: str, path: str) -> tuple[int, dict[str, str], bytes]:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a: object, **k: object) -> None:
            return None

    req = urllib.request.Request(base + path, headers={"Host": host})
    try:
        with urllib.request.build_opener(NoRedirect).open(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_serves_tree_bytes(served: str) -> None:
    host = art_gateway_index.cid_label(CID) + ".art.test"
    s, h, b = get(served, host, "/?fxhash=abc")
    assert s == 200 and b == b"<script>draw()</script>\n"
    assert h["Content-Type"] == "text/html; charset=utf-8"
    assert "'unsafe-inline'" in h["Content-Security-Policy"]
    assert "connect-src 'self'" in h["Content-Security-Policy"]
    assert h["X-Fxhash-Verified"] == "unchecked"
    s, h, b = get(served, host, "/lib/a.js")
    assert s == 200 and b == b"x\r\n" and h["Content-Type"].startswith("text/javascript")
    assert get(served, host, "/sub/")[2] == b"sub\n"
    s, h, _ = get(served, host, "/sub")
    assert s == 301 and h["Location"] == "/sub/"


def test_refusals(served: str) -> None:
    host = art_gateway_index.cid_label(CID) + ".art.test"
    assert get(served, host, "/_source.txt")[0] == 404
    assert get(served, host, "/../../etc/passwd")[0] == 404
    assert get(served, host, "/missing.js")[0] == 404
    assert get(served, "bafyunknown.art.test", "/")[0] == 404
    assert get(served, "other.host", "/")[0] == 404


def test_redirects(served: str) -> None:
    s, h, _ = get(served, "art.test", f"/ipfs/{CID}/?fxhash=abc")
    assert s == 302 and h["Location"] == (
        f"https://{art_gateway_index.cid_label(CID)}.art.test/?fxhash=abc")
    assert get(served, "art.test", f"/onchfs/{ROOT}/")[0] == 404  # not in the index
    assert get(served, "art.test", "/ipfs/notacid/")[0] == 404
    s, _, b = get(served, "art.test", "/")
    assert s == 200 and b"1 artworks" in b


def test_archived_outside_urls_are_served_locally(served_ext: str) -> None:
    host = art_gateway_index.cid_label(CID) + ".art.test"
    s, h, b = get(served_ext, host, "/")
    html = b.decode()
    local = "/_ext/cdnjs.cloudflare.com/ajax/libs/p5.js/1.4.1/p5.min.js"
    assert html.count(local) == 2  # https: and protocol-relative forms
    assert "/_ext/fonts.googleapis.com/css2" in html
    assert "https://cdn.jsdelivr.net/npm/tampered.js" in html  # integrity mismatch: unused
    assert "https://unpkg.com/other.js" in html  # not archived: left, and blocked
    assert h["X-Fxhash-Rewritten"] == "3 outside URL(s) -> /_ext/"
    s, h, b = get(served_ext, host, local)
    assert s == 200 and b == b"p5()" and h["Content-Type"] == "application/javascript"
    # archived CSS is rewritten too, so its fonts load from the archive
    s, h, b = get(served_ext, host, "/_ext/fonts.googleapis.com/css2")
    assert b == b"@font-face{src:url(/_ext/fonts.gstatic.com/s/roboto/v51/a.woff2)}"
    assert get(served_ext, host, "/_ext/fonts.gstatic.com/s/roboto/v51/a.woff2")[2] == b"woff"
    assert get(served_ext, host, "/a.js")[2] == b"x\r\n"  # nothing to rewrite: same bytes
    assert get(served_ext, host, "/_ext/cdn.jsdelivr.net/npm/tampered.js")[0] == 404
    assert get(served_ext, host, "/_ext/../../../etc/passwd")[0] == 404
    assert get(served_ext, host, "/_ext/x/../../manifest.jsonl")[0] == 404


def test_no_archive_no_rewrite(served: str) -> None:
    host = art_gateway_index.cid_label(CID) + ".art.test"
    _, h, _ = get(served, host, "/")
    assert "X-Fxhash-Rewritten" not in h
    assert get(served, host, "/_ext/cdnjs.cloudflare.com/x.js")[0] == 404
