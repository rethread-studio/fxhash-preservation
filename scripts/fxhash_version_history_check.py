#!/usr/bin/env python3
"""Check repos made by fxhash_version_history.py against the chain.

    uv run --with pycryptodome --with hpack python scripts/fxhash_version_history_check.py \
        VERSIONS.jsonl NEWROOT OLDROOT

Per repo: one commit per version, in order, dates not decreasing; each commit's tree minus
`_source.txt` rebuilds that version's code hash (IPFS: `ipfs add` variants of
scripts/ipfs_cid.py; ONCHFS: onchfs.rebuilds); the newest tree equals the old repo's
(OLDROOT) when the old repo held the current version's code. Prints one line per repo
with ✅ or ❌; exits 1 on any ❌.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import onchfs

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
HERE = Path(__file__).parent


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", f"--git-dir={repo}", *args], capture_output=True, text=True,
                          env=ENV, check=True).stdout.strip()


def rebuilds(repo: Path, commit: str, link: str) -> bool:
    want = link.split("?")[0].split("://", 1)[1].split("/")[0]
    with tempfile.TemporaryDirectory() as t:
        tar = subprocess.run(["git", f"--git-dir={repo}", "archive", commit],
                             capture_output=True, env=ENV, check=True).stdout
        subprocess.run(["tar", "-x", "-C", t], input=tar, check=True)
        (Path(t) / "_source.txt").unlink()
        if link.startswith("onchfs://"):
            return want in onchfs.rebuilds(Path(t))
        out = subprocess.run(["ipfs", "add", "-r", "-n", "-Q", "-H", t], capture_output=True,
                             text=True, check=True).stdout
        out += subprocess.run([sys.executable, str(HERE / "ipfs_cid.py"), t],
                              capture_output=True, text=True, check=True).stdout
        return want in out


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    versions, new_root, old_root = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    bad = 0
    with open(versions) as f:
        rows = [json.loads(line) for line in f]
    for r in rows:
        matches = list((new_root / "fxhash-2.0-evm").glob(f"*/*__{r['id']}.git"))
        if not matches:
            continue
        new = matches[0]
        old = old_root / new.relative_to(new_root)
        commits = git(new, "rev-list", "--reverse", "--format=%H %at", "HEAD").splitlines()
        commits = [c for c in commits if not c.startswith("commit ")]
        problems = []
        if len(commits) != len(r["versions"]):
            problems.append(f"{len(commits)} commits for {len(r['versions'])} versions")
        times = [int(c.split()[1]) for c in commits]
        if times != sorted(times):
            problems.append("dates decrease")
        for c, v in zip(commits, r["versions"]):
            if not rebuilds(new, c.split()[0], v["code"]):
                problems.append(f"v{v['v']} does not rebuild {v['code']}")
        old_code = git(old, "show", "HEAD:_source.txt").split("generative_uri: ")[1].split()[0]
        if old_code == r["versions"][-1]["code"].split("?")[0]:
            ls = [git(x, "ls-tree", "-r", "HEAD") for x in (new, old)]
            strip = [{ln for ln in s.splitlines() if not ln.endswith("\t_source.txt")}
                     for s in ls]
            if strip[0] != strip[1]:
                problems.append("newest tree differs from the old repo's")
        bad += bool(problems)
        print("❌" if problems else "✅", new.relative_to(new_root), len(commits), *problems)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
