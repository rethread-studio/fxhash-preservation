#!/usr/bin/env python3
"""Install exported tip commits into the served repos (runs on the server, stdlib only).

    python3 apply_commits.py TOPLEVEL commits.jsonl.gz > fingerprints.tsv

For each line of scripts/export_commits.py: write the commit object, check that its id
is the expected one and that its tree and parents are present, point the ref at it, drop
any other ref, refresh info/refs. Prints "<repo>\t<grokmirror fingerprint>" per repo, to
compare with the manifest built from the source of the export. Exits 1 on any failure.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "LC_ALL": "C",
       "PATH": "/usr/bin:/bin"}


def git(repo: Path, *args: str, data: bytes | None = None) -> str:
    p = subprocess.run(["git", f"--git-dir={repo}", *args], input=data, capture_output=True,
                       env=ENV, check=False)
    if p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout.decode()


def apply(top: Path, row: dict[str, str]) -> str:
    repo = top / row["repo"]
    raw = base64.b64decode(row["raw"])
    head = raw.split(b"\n\n", 1)[0].decode().split("\n")
    needed = [line.split()[1] for line in head if line.startswith(("tree ", "parent "))]
    for obj in needed:
        git(repo, "cat-file", "-e", obj)
    sha = git(repo, "hash-object", "-t", "commit", "--stdin", data=raw).strip()
    if sha != row["commit"]:
        raise RuntimeError(f"commit hashes to {sha}, expected {row['commit']}")
    git(repo, "hash-object", "-t", "commit", "-w", "--stdin", data=raw)
    for ref in git(repo, "for-each-ref", "--format=%(refname)").split():
        if ref != row["ref"]:
            git(repo, "update-ref", "-d", ref)
    git(repo, "update-ref", row["ref"], sha)
    git(repo, "symbolic-ref", "HEAD", row["ref"])
    git(repo, "update-server-info")
    refs = git(repo, "show-ref")
    return hashlib.sha1((refs.rstrip("\n") + "\n").encode()).hexdigest()


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    top = Path(sys.argv[1])
    with gzip.open(sys.argv[2]) as f:
        rows = [json.loads(line) for line in f]
    failed = 0

    def one(row: dict[str, str]) -> tuple[str, str | None, str | None]:
        try:
            return row["repo"], apply(top, row), None
        except (RuntimeError, OSError) as e:
            return row["repo"], None, str(e)

    with ThreadPoolExecutor(4) as pool:
        for repo, fp, err in pool.map(one, rows):
            if err:
                failed += 1
                print(f"FAIL {repo}: {err}", file=sys.stderr)
            else:
                print(f"{repo}\t{fp}")
    print(f"{len(rows) - failed} applied, {failed} failed", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
