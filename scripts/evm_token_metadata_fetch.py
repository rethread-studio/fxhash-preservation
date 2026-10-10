#!/usr/bin/env python3
"""Fetch the token metadata of fxhash EVM projects from chain-anchored IPFS folders.

    python3 evm_token_metadata_fetch.py FOLDERS.jsonl OUT.jsonl.gz [API]

Runs next to a kubo node (API, default http://127.0.0.1:5001), which checks every block
against its hash, so a path under an anchored CID is as trustworthy as the CID. FOLDERS:
one {"chain", "id", "uri", "how"} per line: an ipfs:// URI that the contract stores (base
URI, or a versionInfo uri). A folder of per-token entries gives one line per token,
<uri>/<token>/metadata.json; a folder holding only metadata.json, or a single document, is
not token metadata and gets one "not a token folder" line. A line with a "token_id" is one
token's tokenURI as the contract returns it (evm_token_uris.py), fetched as is. Output
lines: {"chain", "id", "how", "uri", "token_id", "text"} or {..., "error"}. Stdlib only.
"""

from __future__ import annotations

import gzip
import json
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def api(base: str, cmd: str, arg: str, timeout: int = 600) -> bytes:
    url = f"{base}/api/v0/{cmd}?arg={urllib.parse.quote(arg)}"
    if cmd == "ls":
        url += "&size=false&resolve-type=true"
    with urllib.request.urlopen(urllib.request.Request(url, method="POST"),
                                timeout=timeout) as r:
        return r.read()


def main() -> None:
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    folders_p, out_p = sys.argv[1:3]
    base = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:5001"
    with open(folders_p) as f:
        folders = [json.loads(line) for line in f]
    with gzip.open(out_p, "wt") as out:
        # lines with a token_id name one document: its tokenURI, as the contract returns it
        single = [fo for fo in folders if "token_id" in fo]

        def doc(fo: dict[str, Any]) -> dict[str, Any]:
            row: dict[str, Any] = {k: fo[k] for k in ("chain", "id", "how", "uri",
                                                      "token_id")}
            try:
                row["text"] = api(base, "cat", fo["uri"].removeprefix("ipfs://")).decode()
            except Exception as e:  # noqa: BLE001 - recorded, not fatal
                row["error"] = str(e)[:200]
            return row

        with ThreadPoolExecutor(8) as pool:
            for row in pool.map(doc, single):
                out.write(json.dumps(row) + "\n")
        for fo in folders:
            if "token_id" in fo:
                continue
            cid = fo["uri"].removeprefix("ipfs://").rstrip("/")
            head = {k: fo[k] for k in ("chain", "id", "how", "uri")}
            try:
                links = json.loads(api(base, "ls", cid))["Objects"][0]["Links"]
            except Exception as e:  # noqa: BLE001 - recorded, not fatal
                out.write(json.dumps({**head, "error": f"ls: {e}"}) + "\n")
                continue
            tokens = [ln["Name"] for ln in links if ln["Type"] == 1 and ln["Name"].isdigit()]
            if not tokens:
                out.write(json.dumps({**head, "error": "not a token folder"}) + "\n")
                continue

            def one(t: str, cid: str = cid, head: dict[str, Any] = head) -> dict[str, Any]:
                row: dict[str, Any] = {**head, "token_id": int(t)}
                try:
                    row["text"] = api(base, "cat", f"{cid}/{t}/metadata.json").decode()
                except Exception as e:  # noqa: BLE001
                    row["error"] = str(e)[:200]
                return row

            with ThreadPoolExecutor(8) as pool:
                for row in pool.map(one, sorted(tokens, key=int)):
                    out.write(json.dumps(row) + "\n")
            print(fo["id"], fo["how"], len(tokens), flush=True)


if __name__ == "__main__":
    main()
