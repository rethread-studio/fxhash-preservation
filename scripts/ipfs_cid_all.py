#!/usr/bin/env python3
"""Does every project's git repo reproduce the IPFS CID of its generative_uri?

    uv run python scripts/ipfs_cid_all.py GITROOT API.json OUT.jsonl [--workers N]

For each GITROOT/<platform>/<year>/<project>.git: the reference CID is the
generative_uri in `_source.txt` (written by the archive's maker) and, independently,
the one the fxhash API gives for the same token id (API.json: {id: generative_uri},
from `fetch_api_generative_uris`). The tree of `main` minus `_source.txt` is hashed
with `ipfs add -n`: first `-H` (kubo defaults, hidden files), then without `-H`, then
with UnixFS mode 0644 on subdirectories, all of them or all but `__MACOSX`
(scripts/ipfs_cid.py). One line per repo in OUT.jsonl; already present repos are skipped,
so the run resumes.
"""

from __future__ import annotations

import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
API = "https://api.v2.fxhash.xyz/v1/graphql"
MODE_CID = Path(__file__).with_name("ipfs_cid.py")
_repo: dict[str, str] = {}
_api: dict[str, str] = {}


def fetch_api_generative_uris(path: str) -> None:
    """Write {chain:id: generative_uri} for every generative token known to the API."""
    q = ("query ($off: Int!) { onchain { generative_token(order_by: [{created_at: asc}, "
         "{id: asc}], offset: $off, limit: 100) { id chain generative_uri } } }")
    out: dict[str, str] = {}
    while True:
        body = json.dumps({"query": q, "variables": {"off": len(out)}}).encode()
        req = urllib.request.Request(API, body, {"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            page = json.load(r)["data"]["onchain"]["generative_token"]
        out.update({f"{t['chain']}:{t['id']}".lower(): t["generative_uri"] for t in page})
        if len(page) < 100:  # the API caps pages at 100
            break
    with open(path, "w") as f:
        json.dump(out, f)


def ipfs_env() -> dict[str, str]:
    """A throwaway kubo repo per worker process: offline `ipfs add` holds a repo lock."""
    if "path" not in _repo:
        _repo["path"] = tempfile.mkdtemp(prefix="ipfs-")
        subprocess.run(["ipfs", "init", "-e", "--profile=test"], check=True,
                       capture_output=True, env={**os.environ, "IPFS_PATH": _repo["path"]})
    return {**os.environ, "IPFS_PATH": _repo["path"]}


def cid(tree: Path, *flags: str) -> str:
    return subprocess.run(["ipfs", "add", "-r", "-Q", "-n", *flags, str(tree)], check=True,
                          capture_output=True, text=True, env=ipfs_env()).stdout.strip()


def init(api: dict[str, str]) -> None:
    """Worker setup: the API map, also keyed by bare id (EVM ids are unique addresses)."""
    _api.update(api)
    _api.update({k.split(":", 1)[1]: v for k, v in api.items() if not k.startswith("tezos:")})


def check(repo: str) -> dict[str, Any]:
    rel = repo.split("/fxhash-git/", 1)[1].removesuffix(".git")
    row: dict[str, Any] = {"repo": rel}
    git = ["git", f"--git-dir={repo}"]
    src = subprocess.run([*git, "show", "main:_source.txt"], capture_output=True, text=True,
                         env=ENV)
    m = re.search(r"generative_uri: ipfs://([A-Za-z0-9]+)", src.stdout)
    row["want"] = m.group(1) if m else None
    tid = re.search(r"^id: (\S+)", src.stdout, re.M)
    if tid:  # EVM folders don't name the chain: look their address up alone
        key = ("tezos:" if rel.startswith("fxhash-1.0-tezos") else "") + tid.group(1).lower()
        row["api"] = (_api.get(key) or "").removeprefix("ipfs://") or None
    tmp = Path(tempfile.mkdtemp(prefix="cidall-"))
    try:
        tree = tmp / "t"
        tree.mkdir()
        tar = subprocess.run([*git, "archive", "main"], check=True, capture_output=True,
                             env=ENV).stdout
        subprocess.run(["tar", "-x", "-C", str(tree)], input=tar, check=True)
        (tree / "_source.txt").unlink(missing_ok=True)
        row["files"] = sum(len(f) for _, _, f in os.walk(tree))
        want = {row["want"], row.get("api")} - {None, ""}
        got = {"-H": cid(tree, "-H")}
        if not want & set(got.values()):
            got["plain"] = cid(tree)
        if not want & set(got.values()):
            out = subprocess.run([sys.executable, str(MODE_CID), str(tree)], check=True,
                                 capture_output=True, text=True).stdout
            lines = dict(line.split(maxsplit=1) for line in out.splitlines())
            got["mode0644"] = lines["mode"].strip()
            got["mode0644-macosx"] = lines["mode-macosx"].strip()
        row["cids"] = got
        row["match_source"] = [k for k, v in got.items() if v == row["want"]]
        row["match_api"] = [k for k, v in got.items() if v == row.get("api")]
    except subprocess.CalledProcessError as e:
        row["error"] = f"{e.cmd[:3]}: {(e.stderr or b'')[-200:]!r}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return row


def main() -> None:
    args = sys.argv[1:]
    workers = 6
    if "--workers" in args:
        i = args.index("--workers")
        workers = int(args[i + 1])
        del args[i:i + 2]
    if len(args) != 3:
        raise SystemExit(__doc__)
    root, api_path, out_path = args
    if not os.path.exists(api_path):
        fetch_api_generative_uris(api_path)
    with open(api_path) as f:
        api = json.load(f)
    repos = sorted(str(p) for p in Path(root).glob("*/*/*.git"))
    done: set[str] = set()
    if os.path.exists(out_path):
        with open(out_path) as f:
            done = {json.loads(line)["repo"] for line in f}
    todo = [r for r in repos if r.split("/fxhash-git/", 1)[1].removesuffix(".git") not in done]
    print(f"{len(repos)} repos, {len(todo)} to check", flush=True)
    with open(out_path, "a") as out, ProcessPoolExecutor(workers, initializer=init,
                                                     initargs=(api,)) as pool:
        for i, row in enumerate(pool.map(check, todo, chunksize=8), 1):
            out.write(json.dumps(row) + "\n")
            out.flush()
            if i % 500 == 0:
                print(f"{i}/{len(todo)}", flush=True)
    stats: collections.Counter[str] = collections.Counter()
    with open(out_path) as f:
        for row in map(json.loads, f):
            stats["source: " + ("+".join(row.get("match_source", [])) or
                                ("error" if "error" in row else "NONE"))] += 1
            stats["api: " + ("+".join(row.get("match_api", [])) or
                             ("no api cid" if not row.get("api") else "NONE"))] += 1
            stats["source == api" if row.get("api") == row["want"] else
                  "source != api"] += 1
    for k, v in sorted(stats.items()):
        print(v, k)


if __name__ == "__main__":
    main()
