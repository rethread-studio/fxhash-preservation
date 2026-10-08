#!/usr/bin/env python3
"""Dump the directory nodes of IPFS DAGs, as published (stdlib + a local kubo).

    python3 ipfs_dag_dirs.py CID... > dirs.jsonl

One line per CID: {"cid", "dirs": {path: {"data": hex of the UnixFS Data field,
"links": [[name, cid, tsize]]}}, "files": {path: cid}} or {"cid", "error"}. Meant to run
where a kubo node is (rootmab), to see what a git tree cannot carry (UnixFS mode, mtime).
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from typing import Any


def dag(c: str) -> dict[str, Any]:
    out = subprocess.run(["ipfs", "dag", "get", "--", c], capture_output=True, text=True,
                         timeout=120, check=True).stdout
    d: dict[str, Any] = json.loads(out)
    return d


def data(node: dict[str, Any]) -> bytes:
    b = node.get("Data", {}).get("/", {}).get("bytes", "")
    return base64.b64decode(b + "=" * (-len(b) % 4))


def walk(c: str) -> dict[str, Any]:
    dirs: dict[str, Any] = {}
    files: dict[str, str] = {}
    todo = [("", c)]
    while todo:
        path, cid = todo.pop()
        node = dag(cid)
        d = data(node)
        if d[:2] != b"\x08\x01":  # not a plain UnixFS directory
            if path == "":
                return {"root_data": d[:16].hex()}
            files[path] = cid
            continue
        links = [[x["Name"], x["Hash"]["/"], x["Tsize"]] for x in node.get("Links", [])]
        dirs[path] = {"data": d.hex(), "links": links}
        todo += [(f"{path}/{n}" if path else n, h) for n, h, _ in links]
    return {"dirs": dirs, "files": files}


def main() -> None:
    for c in sys.argv[1:]:
        try:
            print(json.dumps({"cid": c, **walk(c)}), flush=True)
        except (subprocess.SubprocessError, ValueError) as e:
            print(json.dumps({"cid": c, "error": type(e).__name__}), flush=True)


if __name__ == "__main__":
    main()
