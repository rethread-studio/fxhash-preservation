#!/usr/bin/env python3
"""Index for the art gateway: DNS label of each code hash -> repo, commit, verification.

    python3 scripts/art_gateway_index.py GITROOT OUT.json.gz [--cidall data/ipfs-cid-all.jsonl]
        [--onchfs data/onchfs-repos.jsonl] [--causes data/ipfs-cid-unmatched.jsonl]

Walks every GITROOT/<platform>/<year>/<project>.git. Each commit on HEAD's first-parent line
that has a `_source.txt` contributes its `generative_uri` (so a version history serves every
version). Labels: IPFS CID -> CIDv1 base32 (`bafy…`); ONCHFS root -> `o` + base32 of the
32 bytes (64 hex chars would exceed the 63-char DNS label limit). `verified` says how the
tree relates to that hash: "rebuilds" (ipfs_cid_all / onchfs_repos / version check),
"files equal: <cause>" (the 94 known differences), or "unchecked". When two repos carry
the same hash, the first in path order wins and the others are listed in `also`.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import subprocess
from pathlib import Path
from typing import Any

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode().lower().rstrip("=")


def cid_label(cid: str) -> str:
    """CIDv0 `Qm…` or CIDv1 base32 `b…` -> CIDv1 base32 (lowercase, DNS-safe)."""
    if cid.startswith("Qm"):
        n = 0
        for ch in cid:
            n = n * 58 + B58.index(ch)
        mh = n.to_bytes(34, "big")
        return "b" + b32(b"\x01\x70" + mh)
    if cid.startswith("b"):
        return cid.lower()
    raise ValueError(f"unsupported CID {cid}")


def onchfs_label(root: str) -> str:
    return "o" + b32(bytes.fromhex(root))


def label_of(uri: str) -> str | None:
    """Label of an ipfs:// or onchfs:// generative URI (query and subpath dropped)."""
    scheme, _, rest = uri.partition("://")
    h = rest.split("?")[0].split("/")[0]
    if scheme == "ipfs" and h:
        return cid_label(h)
    if scheme == "onchfs" and len(h) == 64:
        return onchfs_label(h)
    return None


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", f"--git-dir={repo}", *args], capture_output=True,
                          text=True, env=ENV, check=False).stdout


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("gitroot")
    ap.add_argument("out")
    ap.add_argument("--cidall")
    ap.add_argument("--onchfs")
    ap.add_argument("--causes")
    ap.add_argument("--versions-checked", action="store_true",
                    help="every version commit was checked by fxhash_version_history_check")
    a = ap.parse_args()
    good: set[tuple[str, str]] = set()  # (repo, label) whose tree rebuilds the hash
    if a.cidall:
        with open(a.cidall) as f:
            for r in map(json.loads, f):
                matched = {r["cids"][v] for v in (r.get("match_source") or [])
                           + (r.get("match_api") or []) if v in r.get("cids", {})}
                good |= {(r["repo"], cid_label(c)) for c in matched}
    if a.onchfs:
        with open(a.onchfs) as f:
            good |= {(r["repo"].removesuffix(".git"), onchfs_label(r["want"]))
                     for r in map(json.loads, f) if r.get("match")}
    causes: dict[tuple[str, str], str] = {}
    if a.causes:
        with open(a.causes) as f:
            causes = {(r["repo"], cid_label(r["cid"])): r["cause"] for r in map(json.loads, f)}
    root = Path(a.gitroot)
    index: dict[str, dict[str, Any]] = {}
    for repo in sorted(root.glob("*/*/*.git")):
        rel = repo.relative_to(root).as_posix().removesuffix(".git")
        commits = git(repo, "rev-list", "--first-parent", "HEAD").split()
        for i, c in enumerate(commits):
            src = git(repo, "show", f"{c}:_source.txt")
            uri = next((ln.split(":", 1)[1].strip() for ln in src.splitlines()
                        if ln.startswith("generative_uri:")), "")
            if not src and i:
                break  # an artist's own history below the snapshot: no hash to serve
            label = label_of(uri) if uri else None
            if not label:
                continue
            verified = ("rebuilds" if (rel, label) in good
                        or (i > 0 and a.versions_checked) else
                        f"files equal: {causes[(rel, label)]}" if (rel, label) in causes else
                        "unchecked")
            if label in index:
                index[label].setdefault("also", []).append(rel)
                continue
            index[label] = {"repo": rel, "commit": c, "uri": uri, "verified": verified}
    with gzip.open(a.out, "wt") as f:
        json.dump(index, f, sort_keys=True)
    counts: dict[str, int] = {}
    for v in index.values():
        k = v["verified"].split(":")[0]
        counts[k] = counts.get(k, 0) + 1
    print(len(index), "labels", counts)


if __name__ == "__main__":
    main()
