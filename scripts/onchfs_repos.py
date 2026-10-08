#!/usr/bin/env python3
"""Does every ONCHFS project's git repo rebuild the ONCHFS root stored on chain?

    uv run --with pycryptodome --with hpack python scripts/onchfs_repos.py GITROOT OUT.jsonl

For each GITROOT/<platform>/<year>/<project>.git: the root is the generative_uri
(onchfs://…) of `_source.txt`; the tree of `main` minus `_source.txt` is rebuilt with
onchfs.rebuilds (every uploader variant). One line per repo; prints counts.
"""

from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import onchfs

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    root, out_path = sys.argv[1:]
    stats: collections.Counter[str] = collections.Counter()
    with open(out_path, "w") as out:
        for repo in sorted(Path(root).glob("*/*/*.git")):
            git = ["git", f"--git-dir={repo}"]
            src = subprocess.run([*git, "show", "main:_source.txt"], capture_output=True,
                                 text=True, env=ENV, check=True).stdout
            want = re.search(r"generative_uri: onchfs://([0-9a-f]{64})", src)
            row = {"repo": str(repo.relative_to(root)), "want": want and want.group(1)}
            with tempfile.TemporaryDirectory() as tmp:
                tar = subprocess.run([*git, "archive", "main"], capture_output=True,
                                     env=ENV, check=True).stdout
                subprocess.run(["tar", "-x", "-C", tmp], input=tar, check=True)
                (Path(tmp) / "_source.txt").unlink()
                row["match"] = row["want"] in onchfs.rebuilds(Path(tmp))
            stats["rebuilds chain root" if row["match"] else "DIFFERS"] += 1
            out.write(json.dumps(row) + "\n")
    for k, v in stats.items():
        print(v, k)


if __name__ == "__main__":
    main()
