#!/usr/bin/env python3
"""Archive the outside resources artworks load, once each (stdlib only).

    python3 scripts/ext_archive.py URLS.jsonl ARCHIVE [--workers 8]

URLS from art_external_urls.py. Each URL is fetched once (browser User-Agent: font CSS
differs per browser) into ARCHIVE/files/<local path>, where the local path is the URL's
host and path (a query string becomes a `__q<sha256 prefix>` suffix). CSS bodies are
scanned for further absolute `url()`/`@import` targets (font files), fetched the same
way. When the referencing tag had an `integrity=` (SRI) value, the body is checked
against it. ARCHIVE/manifest.jsonl: one line per URL {url, local, status, final_url,
content_type, size, sha256, integrity: ok|mismatch|null, fetched_at, error}. Rerunning
skips URLs already archived with status 200.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")
CSS_URL = re.compile(r"""(?:url\(\s*["']?|@import\s+["'])(https?://[^"')\s]+)""",
                     re.IGNORECASE)
MAX_BYTES = 64 * 1024 * 1024


def local_path(url: str) -> str:
    """Stable, path-preserving file name: <host>/<path>[__q<hash of query>]."""
    u = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote(u.path).lstrip("/") or "index"
    if path.endswith("/"):
        path += "index"
    parts = [p for p in path.split("/") if p not in ("", ".", "..")]
    name = "/".join([u.netloc.lower(), *parts])
    if u.query:
        name += "__q" + hashlib.sha256(u.query.encode()).hexdigest()[:16]
    return name


def sri_ok(body: bytes, integrity: str) -> bool:
    """True if any of the SRI hashes (sha256/384/512, base64) matches the body."""
    for token in integrity.split():
        algo, _, digest = token.partition("-")
        if algo in ("sha256", "sha384", "sha512") and \
                base64.b64encode(hashlib.new(algo, body).digest()).decode() == digest:
            return True
    return False


def fetch(url: str, integrity: str | None, root: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"url": url, "local": local_path(url), "integrity": None,
                           "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read(MAX_BYTES + 1)
            row.update(status=r.status, final_url=r.geturl(),
                       content_type=r.headers.get("Content-Type", ""))
        if len(body) > MAX_BYTES:
            raise ValueError("larger than 64 MiB")
    except urllib.error.HTTPError as e:
        row.update(status=e.code, error=str(e)[:200])
        return row
    except Exception as e:  # noqa: BLE001 - recorded per URL
        row.update(status=None, error=str(e)[:200])
        return row
    if integrity:
        row["integrity"] = "ok" if sri_ok(body, integrity) else "mismatch"
    dest = root / "files" / row["local"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    row.update(size=len(body), sha256=hashlib.sha256(body).hexdigest())
    if "css" in row["content_type"] or row["local"].endswith(".css"):
        row["refs"] = sorted(set(CSS_URL.findall(body.decode("utf-8", "replace"))))
    return row


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--workers")]
    workers = next((int(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--workers=")),
                   8)
    if len(args) != 2:
        raise SystemExit(__doc__)
    urls_p, root = args[0], Path(args[1])
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.jsonl"
    done: dict[str, dict[str, Any]] = {}
    if manifest.exists():
        with open(manifest) as f:
            done = {r["url"]: r for r in map(json.loads, f) if r.get("status") == 200}
    with open(urls_p) as f:
        todo = {r["url"]: r.get("integrity") for r in map(json.loads, f)}
    rows = dict(done)
    while todo:
        batch = {u: s for u, s in todo.items() if u not in rows}
        with ThreadPoolExecutor(workers) as ex:
            for row in ex.map(lambda us: fetch(us[0], us[1], root), batch.items()):
                rows[row["url"]] = row
        # second level: what fetched CSS files point to (font files)
        todo = {u: None for done_url in batch for u in rows[done_url].get("refs", [])
                if u not in rows}
    with open(manifest, "w") as f:
        f.writelines(json.dumps(rows[u]) + "\n" for u in sorted(rows))
    ok = sum(r.get("status") == 200 for r in rows.values())
    print(json.dumps({"urls": len(rows), "ok": ok,
                      "integrity_ok": sum(r.get("integrity") == "ok" for r in rows.values()),
                      "integrity_mismatch": sum(r.get("integrity") == "mismatch"
                                                for r in rows.values()),
                      "bytes": sum(r.get("size", 0) for r in rows.values())}))


if __name__ == "__main__":
    main()
