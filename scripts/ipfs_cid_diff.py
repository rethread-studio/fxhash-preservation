#!/usr/bin/env python3
"""Why does a repo not rebuild its CID? Compare its tree with the DAG as published.

    python3 scripts/ipfs_cid_diff.py UNMATCHED.txt DIRS.jsonl GITROOT OUT.jsonl

UNMATCHED.txt: "<cid> <repo>" per line (rows of ipfs_cid_all.py that matched nothing);
DIRS.jsonl: ipfs_dag_dirs.py run on rootmab over those CIDs. Per repo: files only in the
DAG, only in git, with other content, the UnixFS Data of every published directory, and a
cause: the upload has a .git folder, archive copy incomplete, empty folder(s), mixed
folder modes, or file content differs.
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import tempfile
from typing import Any

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}


def local_cids(gitdir: str) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as t:
        tar = subprocess.run(["git", f"--git-dir={gitdir}", "archive", "HEAD"],
                             capture_output=True, env=ENV, check=True).stdout
        subprocess.run(["tar", "-x", "-C", t], input=tar, check=True)
        if os.path.exists(f"{t}/_source.txt"):
            os.remove(f"{t}/_source.txt")
        lines = subprocess.run(["ipfs", "add", "-r", "-n", "-H", t], capture_output=True,
                               text=True, check=True).stdout.splitlines()
    out = {}
    for ln in lines:
        _, c, p = ln.split(" ", 2)
        out[p.split("/", 1)[1] if "/" in p else ""] = c
    return out


def compare(repo: str, cid: str, r: dict[str, Any], gitroot: str) -> dict[str, Any]:
    row: dict[str, Any] = {"repo": repo, "cid": cid}
    if "dirs" not in r:
        row["cause"] = "remote: " + str(r.get("error") or r.get("root_data") or "missing")
        return row
    local = local_cids(f"{gitroot}/{repo}.git")
    rfiles, rdirs = r["files"], r["dirs"]
    lfiles = {p: c for p, c in local.items()
              if p not in rdirs and not any(q.startswith(p + "/") for q in local)}
    only_remote = sorted(set(rfiles) - set(lfiles))
    only_local = sorted(set(lfiles) - set(rfiles))
    differ = sorted(p for p in set(rfiles) & set(lfiles) if rfiles[p] != lfiles[p])
    row.update(only_remote=only_remote[:5], n_only_remote=len(only_remote),
               only_local=only_local[:5], n_only_local=len(only_local),
               differ=differ[:5], n_differ=len(differ),
               dir_data=dict(collections.Counter(v["data"] for v in rdirs.values())))
    if only_remote:
        row["cause"] = "upload has a .git folder" if any(
            ".git" in p.split("/") for p in only_remote) else "archive copy incomplete"
    elif only_local:
        row["cause"] = "extra files in git"
    elif differ:
        row["cause"] = "file content differs"
    elif any(not v["links"] for v in rdirs.values()):
        row["cause"] = "empty folder(s)"
    else:
        row["cause"] = "mixed folder modes"
    return row


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    unmatched, dirs_path, gitroot, out_path = sys.argv[1:]
    with open(dirs_path) as f:
        remote = {d["cid"]: d for d in map(json.loads, f)}
    with open(unmatched) as f:
        todo = [line.split(maxsplit=1) for line in f if line.strip()]
    causes: collections.Counter[str] = collections.Counter()
    with open(out_path, "w") as out:
        for cid, repo in todo:
            row = compare(repo.strip(), cid, remote.get(cid, {}), gitroot)
            causes[row["cause"]] += 1
            out.write(json.dumps(row) + "\n")
    for k, v in causes.most_common():
        print(v, k)


if __name__ == "__main__":
    main()
