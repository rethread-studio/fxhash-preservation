#!/usr/bin/env python3
"""Rebuild the repos of updated fxhash EVM projects with one commit per on-chain version.

    uv run python scripts/fxhash_version_history.py VERSIONS.jsonl GITROOT ONCHFS_FILES \
        IPFS_DIR OUTROOT [--unretrievable CID]...

VERSIONS from fxhash_evm_versions.py (with block times). For each contract, the existing
repo GITROOT/fxhash-2.0-evm/<year>/<name>__<id>.git gives the artist identity, the date of
version 1 (the project's creation) and `_source.txt`. Version v's code is
ONCHFS_FILES/base/<root> (onchfs.py extract) or IPFS_DIR/<cid> (fetched, CID checked).
Commit v holds that code plus `_source.txt` with v's generative_uri and a `version:` line,
dated at the block where currentVersion() reached v; every version gets a commit, even when
only its metadata changed. A version whose code is listed with --unretrievable (anchored
on chain, but no copy found) gets no commit; the next commit's message names it. Any other
missing code is an error. The new repo goes to OUTROOT/<same path>; GITROOT is not touched.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from swhprep._convert import BRANCH, Identity, _snapshot_tree, _write_commit
from swhprep._git import git


def code_dir(link: str, onchfs_files: Path, ipfs_dir: Path) -> Path:
    bare = link.split("?")[0].split("://", 1)[1].split("/")[0]
    return onchfs_files / "base" / bare if link.startswith("onchfs://") else ipfs_dir / bare


def main() -> None:
    args = sys.argv[1:]
    gone = {args[i + 1] for i, a in enumerate(args) if a == "--unretrievable"}
    args = [a for i, a in enumerate(args)
            if a != "--unretrievable" and (i == 0 or args[i - 1] != "--unretrievable")]
    if len(args) != 5:
        raise SystemExit(__doc__)
    versions, gitroot, onchfs_files, ipfs_dir, outroot = map(Path, args)
    with open(versions) as f:
        rows = [json.loads(line) for line in f]
    for r in rows:
        (old,) = (gitroot / "fxhash-2.0-evm").glob(f"*/*__{r['id']}.git")
        rel = old.relative_to(gitroot)
        name, email, date = git("log", "-1", "--format=%an%x00%ae%x00%ad", "--date=raw",
                                "HEAD", git_dir=old).split("\0")
        source = git("show", "HEAD:_source.txt", git_dir=old).splitlines()
        new = outroot / rel
        if new.exists():
            shutil.rmtree(new)
        new.parent.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "--bare", "--template=", f"--initial-branch={BRANCH}", str(new))
        shutil.copy(old / "description", new / "description")
        folder = (old / "description").read_text().strip()
        parents: list[str] = []
        skipped: list[str] = []
        for v in r["versions"]:
            link = v["code"].split("?")[0]
            src = code_dir(link, onchfs_files, ipfs_dir)
            if not src.is_dir():
                if src.name in gone:
                    skipped.append(f"Version {v['v']} (versionInfo({v['v']}).uri = {v['uri']}, "
                                   f"from {v.get('time', '?')}): code {link} is anchored on "
                                   "chain but no copy was found on IPFS on 2026-10-09; "
                                   "no commit.\n")
                    continue
                raise SystemExit(f"{rel} v{v['v']}: no code at {src}")
            lines = [ln for ln in source if not ln.startswith(("generative_uri:", "version:"))]
            lines += [f"generative_uri: {link}", f"version: {v['v']}"]
            when = (date if v["v"] == 1 else
                    Identity.parse(f"x <{email}>", v["time"]).date.removeprefix("@"))
            with tempfile.TemporaryDirectory() as t:
                work = Path(t) / "src"
                shutil.copytree(src, work, symlinks=True)
                (work / "_source.txt").write_text("\n".join(lines) + "\n")
                tree = _snapshot_tree(work, new, ignore_filemode=True, force=True)
            msg = (f"Version {v['v']} of {folder}\n\nversionInfo({v['v']}).uri = {v['uri']}\n"
                   f"code: {link}\n" + ("\n" + "".join(skipped) if skipped else ""))
            skipped = []
            parents = [_write_commit(new, tree, parents, Identity(name, email, when), msg)]
        if skipped:
            raise SystemExit(f"{rel}: the last version has no code")
        git("update-ref", f"refs/heads/{BRANCH}", parents[0], git_dir=new)
        git("repack", "-adq", git_dir=new)
        git("update-server-info", git_dir=new)
        n = int(git("rev-list", "--count", parents[0], git_dir=new))
        print(json.dumps({"repo": str(rel), "commits": n, "head": parents[0]}))


if __name__ == "__main__":
    main()
