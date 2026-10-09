#!/usr/bin/env python3
"""Every version's code link, for the versioned fxhash EVM contracts past version 1.

    python3 scripts/fxhash_evm_versions.py ONCHAIN.jsonl.gz OUT.jsonl [BASE_RPC] [SSH_HOST]

ONCHAIN from fxhash_project_onchain.py list (`version` = currentVersion()). For each Base
contract with version > 1, `versionInfo(v)` for v = 1..version gives an ipfs:// URI: a
project metadata document (`generativeUri`), or a folder of token metadata
(`<id>/metadata.json` or `metadata.json`, `generatorUri`). Documents are read through the
kubo node on SSH_HOST (default rootmab). One line per contract: [{v, uri, code}].
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
import time
import urllib.request

IPFS = "sudo -u martin env IPFS_PATH=/home/martin/.ipfs timeout 300 ipfs"


def eth_call(url: str, to: str, data: str) -> bytes:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": to, "data": data}, "latest"]}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json",
                                             "User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return bytes.fromhex(json.loads(r.read())["result"][2:])


def ipfs(host: str, *args: str) -> str | None:
    p = subprocess.run(["ssh", "-n", "-T", host, f"{IPFS} {' '.join(args)}"],
                       capture_output=True, text=True, check=False)
    return p.stdout if p.returncode == 0 else None


def code_link(host: str, uri: str) -> str:
    c = uri.removeprefix("ipfs://")
    text = (ipfs(host, "cat", f"/ipfs/{c}") or ipfs(host, "cat", f"/ipfs/{c}/1/metadata.json")
            or ipfs(host, "cat", f"/ipfs/{c}/metadata.json"))
    if not text:  # a token folder whose ids don't start at 1: its first entry
        ls = (ipfs(host, "ls", "--size=false", "--resolve-type=false", f"/ipfs/{c}")
              or "").split()
        text = ipfs(host, "cat", f"/ipfs/{c}/{ls[1]}/metadata.json") if len(ls) > 1 else None
    if not text:
        return "unreadable"
    m = json.loads(text)
    return m.get("generativeUri") or m.get("generatorUri") or "no code link"


def main() -> None:
    if len(sys.argv) not in (3, 4, 5):
        raise SystemExit(__doc__)
    onchain, out_path = sys.argv[1:3]
    url = sys.argv[3] if len(sys.argv) > 3 else "https://base-rpc.publicnode.com"
    host = sys.argv[4] if len(sys.argv) > 4 else "rootmab"
    with gzip.open(onchain, "rt") as f, open(out_path, "w") as out:
        for r in map(json.loads, f):
            if r["chain"] != "base" or r.get("version", 1) <= 1:
                continue
            vs = []
            for v in range(1, r["version"] + 1):
                info = eth_call(url, r["id"], "0x87114b8c" + f"{v:064x}")
                off = int.from_bytes(info[64:96], "big")
                n = int.from_bytes(info[off:off + 32], "big")
                uri = info[off + 32:off + 32 + n].decode()
                vs.append({"v": v, "uri": uri, "code": code_link(host, uri)})
                time.sleep(0.2)
            out.write(json.dumps({"chain": r["chain"], "id": r["id"], "versions": vs}) + "\n")
            out.flush()


if __name__ == "__main__":
    main()
