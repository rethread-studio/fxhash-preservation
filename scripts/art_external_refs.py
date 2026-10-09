#!/usr/bin/env python3
"""Which artworks load resources from other hosts (CDNs, fonts, APIs)?

    python3 scripts/art_external_refs.py INDEX.json.gz GITROOT > OUT.jsonl

For each artwork of the art gateway index, the top-level HTML files of its commit are
scanned for `src=`/`href=` values pointing to another host (`http://`, `https://`, `//`),
links of `<a>` tags excepted. One line per artwork that has any: {"label", "repo",
"hosts": {host: count}}. Such an artwork depends on those servers to display, and the
gateway's policy (no other hosts) shows it broken. JavaScript files are not scanned.
"""

from __future__ import annotations

import collections
import gzip
import json
import os
import re
import subprocess
import sys

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
PAT = re.compile(r"""(?:src|href)\s*=\s*["'](https?:)?//([^/"']+)""", re.IGNORECASE)


def git(repo: str, *args: str) -> bytes:
    return subprocess.run(["git", f"--git-dir={repo}", *args], capture_output=True,
                          env=ENV, check=False).stdout


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    with gzip.open(sys.argv[1], "rt") as f:
        index = json.load(f)
    for label, e in index.items():
        repo = f"{sys.argv[2]}/{e['repo']}.git"
        names = git(repo, "ls-tree", "--name-only", e["commit"]).decode().split("\n")
        hosts: collections.Counter[str] = collections.Counter()
        for name in names:
            if not name.lower().endswith((".html", ".htm")):
                continue
            text = git(repo, "cat-file", "blob", f"{e['commit']}:{name}").decode(
                "utf-8", "replace")
            for m in PAT.finditer(text):
                if "<a " in text[max(0, m.start() - 60):m.start()].lower():
                    continue  # a link, not a resource
                hosts[m.group(2).lower()] += 1
        if hosts:
            print(json.dumps({"label": label, "repo": e["repo"], "hosts": dict(hosts)}))


if __name__ == "__main__":
    main()
