#!/usr/bin/env python3
"""Canary: does a project's git repo reproduce the IPFS CID of its on-chain generative_uri?

    uv run python scripts/ipfs_cid_canary.py ids.json N OUT.jsonl

N random single-snapshot projects: `git archive main` minus `_source.txt` (added by the
archive's maker), hashed with `ipfs add -n` in several variants, compared with the CID in
`_source.txt`. Folders that match none: try scripts/ipfs_cid.py (subdirectory mode 0644).
"""

from __future__ import annotations

import collections
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from swhprep._convert import safe_name

R = Path("/media/martin/63d87370-5401-42f9-a718-bead975d75d4")
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}


def cid(path: Path, *flags: str) -> str:
    return subprocess.run(["ipfs", "add", "-r", "-Q", "-n", *flags, str(path)], check=True,
                          capture_output=True, text=True).stdout.strip()


def main() -> None:
    with open(sys.argv[1]) as idf:
        ids = json.load(idf)
    n = int(sys.argv[2])
    rng = random.Random(20261007)
    out: collections.Counter[str] = collections.Counter()
    rows: list[dict[str, Any]] = []
    for rel in rng.sample(sorted(ids), len(ids)):
        if len(rows) == n:
            break
        src = R / "fxhash-archive" / rel
        st = src / "_source.txt"
        if not st.is_file() or (src / ".git").exists():
            continue
        m = re.search(r"generative_uri: ipfs://([A-Za-z0-9]+)", st.read_text(errors="replace"))
        if not m:
            continue
        want = m.group(1)
        repo = R / "fxhash-git" / (safe_name(rel) + ".git")
        if not (repo / "HEAD").is_file():
            out["skipped: repo path not found"] += 1
            print("SKIP", rel, repo, flush=True)
            continue
        tmp = Path(tempfile.mkdtemp())
        try:
            tree = tmp / "git"
            tree.mkdir()
            tar = subprocess.run(["git", f"--git-dir={repo}", "archive", "main"], check=True,
                                 capture_output=True, env=ENV).stdout
            subprocess.run(["tar", "-x", "-C", str(tree)], input=tar, check=True)
            (tree / "_source.txt").unlink(missing_ok=True)
            res = {"git -H": cid(tree, "-H"), "git": cid(tree)}
            shutil.rmtree(tree / "__MACOSX", ignore_errors=True)
            res["git -H no __MACOSX"] = cid(tree, "-H")
            res["git no __MACOSX"] = cid(tree)
            drive = tmp / "drive"
            shutil.copytree(src, drive, symlinks=True)
            (drive / "_source.txt").unlink()
            res["drive -H"] = cid(drive, "-H")
            hit = [k for k, v in res.items() if v == want]
            out["match: " + (" | ".join(hit) if hit else "NONE")] += 1
            rows.append({"folder": rel, "want": want, "cids": res, "match": hit})
            print(rel[:50], want[:12], hit or "NONE", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    for k, v in out.most_common():
        print(v, k)
    with open(sys.argv[3], "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)


if __name__ == "__main__":
    main()
