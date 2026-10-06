#!/usr/bin/env python3
"""Export the branch-tip commit object of every repo under TOPLEVEL, byte for byte.

    python scripts/export_commits.py TOPLEVEL OUT.jsonl.gz

One JSON line per repo: {"repo": "<path>.git", "ref": "refs/heads/...", "commit": sha,
"raw": base64 of the commit object}. With scripts/apply_commits.py on the server this
replaces the tip commit of repos whose trees are already there, instead of re-uploading
every pack (rewriting authors/dates changes only the commit objects).
Every repo must have exactly one ref, which HEAD points to.
"""

from __future__ import annotations

import base64
import gzip
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "LC_ALL": "C"}


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", f"--git-dir={repo}", *args], check=True,
                          capture_output=True, env=ENV).stdout


def export(top: Path, repo: Path) -> dict[str, str]:
    refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)").decode().split("\n")
    refs = [r for r in refs if r]
    head = git(repo, "symbolic-ref", "HEAD").decode().strip()
    if len(refs) != 1 or refs[0].split()[0] != head:
        raise SystemExit(f"{repo}: expected one ref, HEAD's; got {refs}")
    sha = refs[0].split()[1]
    raw = git(repo, "cat-file", "commit", sha)
    return {"repo": repo.relative_to(top).as_posix(), "ref": head, "commit": sha,
            "raw": base64.b64encode(raw).decode()}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    top = Path(sys.argv[1])
    repos = sorted(p for p in top.rglob("*.git") if (p / "HEAD").is_file())
    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(lambda r: export(top, r), repos))
    with open(sys.argv[2], "wb") as f, gzip.GzipFile(filename="", mode="wb", fileobj=f,
                                                     mtime=0) as gz:
        for row in rows:
            gz.write((json.dumps(row) + "\n").encode())
    print(f"{len(rows)} commits -> {sys.argv[2]}")


if __name__ == "__main__":
    main()
