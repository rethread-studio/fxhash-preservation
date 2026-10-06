#!/usr/bin/env python3
"""Compute new tip commits with per-folder identities, without re-converting.

    uv run python scripts/rewrite_commits.py SRC DEST DEPTH ids.json OUT.jsonl.gz

For each folder of SRC (as `swhprep convert` maps it to DEST/<name>.git), take the tip
commit of the existing repo and replace its author and committer lines with the identity
from the `--identity-map` file; tree, parents and message are kept. The result is byte for
byte what `swhprep convert --identity-map` writes, in the export format of
scripts/export_commits.py (for scripts/apply_commits.py). Nothing is written to DEST.
"""

from __future__ import annotations

import base64
import gzip
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from swhprep._convert import Identity, plan

ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "LC_ALL": "C"}


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", f"--git-dir={repo}", *args], input=data, check=True,
                          capture_output=True, env=ENV).stdout


def rewrite(repo: Path, rel: str, who: Identity) -> dict[str, str]:
    ref = git(repo, "symbolic-ref", "HEAD").decode().strip()
    old = git(repo, "cat-file", "commit", ref)
    header, msg = old.split(b"\n\n", 1)
    stamp = f"{who.name} <{who.email}> {who.date.removeprefix('@')}".encode()
    lines = [b"author " + stamp if ln.startswith(b"author ") else
             b"committer " + stamp if ln.startswith(b"committer ") else ln
             for ln in header.split(b"\n")]
    raw = b"\n".join(lines) + b"\n\n" + msg
    sha = git(repo, "hash-object", "-t", "commit", "--stdin", data=raw).decode().strip()
    return {"repo": rel, "ref": ref, "commit": sha, "raw": base64.b64encode(raw).decode()}


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)
    src, dest, depth = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    with open(sys.argv[4]) as f:
        ids = {k: Identity.make(v["name"], v["email"], v["date"]) for k, v in json.load(f).items()}
    jobs = [(dest / name, name, ids[s.relative_to(src).as_posix()]) for s, name in plan(src, depth)]
    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(lambda j: rewrite(*j), jobs))
    with open(sys.argv[5], "wb") as f, gzip.GzipFile(filename="", mode="wb", fileobj=f,
                                                     mtime=0) as gz:
        for row in rows:
            gz.write((json.dumps(row) + "\n").encode())
    print(f"{len(rows)} commits -> {sys.argv[5]}")


if __name__ == "__main__":
    main()
