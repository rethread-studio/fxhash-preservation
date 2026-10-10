#!/usr/bin/env python3
"""Every outside URL an artwork may load, from its HTML, CSS and JavaScript.

    python3 scripts/art_external_urls.py INDEX.json.gz GITROOT OUT.jsonl

For each artwork of the art gateway index, `git grep` finds absolute URLs (`http://`,
`https://`, and `//host/` in HTML attributes) in the .html/.htm/.css/.js/.mjs files of its
commit. Kept as resources: HTML `src=`/`href=` values (not on `<a>` links), CSS `url()` and
`@import`, and JavaScript string literals whose host is a known CDN or whose path ends in
a resource extension. XML namespaces and plain links are dropped. One line per URL:
{"url", "kind" (html|css|js), "integrity" (SRI value when the tag has one), "labels"}.
"""

from __future__ import annotations

import collections
import gzip
import json
import os
import re
import subprocess
import sys
from typing import Any

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
CDNS = ("cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com", "fonts.googleapis.com",
        "fonts.gstatic.com", "code.jquery.com", "cdn.babylonjs.com", "cdn.rawgit.com",
        "rawcdn.githack.com", "raw.githack.com", "cdn.skypack.dev", "esm.sh",
        "ajax.googleapis.com", "threejs.org", "cdn.plot.ly", "d3js.org", "openprocessing.org")
EXT = re.compile(r"\.(js|mjs|css|json|wasm|png|jpe?g|gif|webp|svg|glb|gltf|obj|mp3|wav|ogg|"
                 r"mp4|webm|woff2?|ttf|otf|frag|vert|glsl|txt|csv)(\?|#|$)", re.IGNORECASE)
TAG_ATTR = re.compile(r"""<(\w+)[^>]*?\s(?:src|href)\s*=\s*["']((?:https?:)?//[^"']+)["'][^>]*>""",
                      re.IGNORECASE | re.DOTALL)
INTEGRITY = re.compile(r"""integrity\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
CSS_URL = re.compile(r"""(?:url\(\s*["']?|@import\s+["'])(https?://[^"')\s]+)""", re.IGNORECASE)
JS_STR = re.compile(r"""["'`](https?://[^"'`\s\\]+)["'`]""")
SKIP_HOSTS = ("www.w3.org", "w3.org", "schemas.xmlsoap.org", "ns.adobe.com", "purl.org")


def host(url: str) -> str:
    return url.split("//", 1)[1].split("/", 1)[0].lower()


def scan(text: str, kind: str) -> list[tuple[str, str | None]]:
    found: list[tuple[str, str | None]] = []
    if kind == "html":
        for m in TAG_ATTR.finditer(text):
            if m.group(1).lower() == "a":
                continue
            url = m.group(2)
            url = "https:" + url if url.startswith("//") else url
            sri = INTEGRITY.search(m.group(0))
            found.append((url, sri.group(1) if sri else None))
        kind = "js"  # inline scripts
    if kind == "css":
        found += [(u, None) for u in CSS_URL.findall(text)]
    if kind == "js":
        for u in JS_STR.findall(text):
            h = host(u)
            if h in SKIP_HOSTS:
                continue
            if h in CDNS or EXT.search(u.split("//", 1)[1].split("/", 1)[-1]):
                found.append((u, None))
    return [(u, s) for u, s in found if host(u) not in SKIP_HOSTS and "${" not in u]


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    with gzip.open(sys.argv[1], "rt") as f:
        index = json.load(f)
    urls: dict[str, dict[str, Any]] = {}
    for label, e in index.items():
        repo = f"{sys.argv[2]}/{e['repo']}.git"
        out = subprocess.run(
            ["git", f"--git-dir={repo}", "grep", "-I", "-l", "-E", "(https?:)?//[a-z0-9.-]+\\.",
             e["commit"], "--", "*.html", "*.htm", "*.css", "*.js", "*.mjs"],
            capture_output=True, text=True, env=ENV, check=False).stdout
        for line in out.splitlines():
            path = line.split(":", 1)[1]
            kind = {"htm": "html", "mjs": "js"}.get(path.rsplit(".", 1)[-1].lower(),
                                                   path.rsplit(".", 1)[-1].lower())
            text = subprocess.run(["git", f"--git-dir={repo}", "cat-file", "blob",
                                   f"{e['commit']}:{path}"], capture_output=True,
                                  env=ENV, check=False).stdout.decode("utf-8", "replace")
            for url, sri in scan(text, kind):
                u = urls.setdefault(url, {"url": url, "kind": kind, "integrity": None,
                                          "labels": set()})
                u["labels"].add(label)
                u["integrity"] = u["integrity"] or sri
    with open(sys.argv[3], "w") as f:
        for u in sorted(urls.values(), key=lambda u: u["url"]):
            f.write(json.dumps({**u, "labels": sorted(u["labels"])}) + "\n")
    by_host = collections.Counter(host(u) for u in urls)
    artworks = {lab for u in urls.values() for lab in u["labels"]}
    print(len(urls), "URLs,", len(artworks), "artworks,", by_host.most_common(10))


if __name__ == "__main__":
    main()
