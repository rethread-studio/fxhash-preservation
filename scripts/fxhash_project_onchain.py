#!/usr/bin/env python3
"""The IPFS links of every fxhash project, as anchored on chain.

    uv run --with eth-abi python scripts/fxhash_project_onchain.py list OUT.jsonl \
        BASE=https://mainnet.base.org ETHEREUM=https://ethereum-rpc.publicnode.com
    uv run python scripts/fxhash_project_onchain.py fetch OUT.jsonl DOCS.jsonl.gz GATEWAY

`list`, one line per project:
- Tezos: the project ledgers of the issuer contracts, read through TzKT (current values,
  removed keys included): `metadata` (an ipfs:// URI), and for issuer v3 the code entry
  `codex_entries[codex_id]`. Issuer v2 holds projects 0-26047 (the v1 issuer's, migrated,
  plus its own), v3 the later ones; v1 is listed too, for comparison.
- EVM (FxGenArt721, contract list from evm-*-contracts.json of fxhash_evm_seeds.py):
  `metadataInfo()` = (baseURI, onchainPointer); a 32-byte baseURI is the sha256 digest of a
  CIDv0 folder of token metadata, <cid>/<token id>/metadata.json.

`fetch` reads the metadata document each project points to (Tezos: the project metadata;
EVM: token 1's metadata) through GATEWAY, which must be our own kubo node (it checks every
block against its CID, so a path below a CID is as trustworthy as the CID), and writes
{"chain", "id", "uri", "text"} or {..., "error"} per project.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

TZKT = "https://api.tzkt.io/v1"
# issuer contract -> big maps holding the projects (ledger) and, for v3, the code entries
TEZOS_ISSUERS = {"KT1XCoGnfupWk7Sp8536EfrxcP73LmT68Nyr": "v1",
                 "KT1BJC12dG17CVvPKJ1VYaNnaT5mzfnUTwXv": "v2",
                 "KT1Xpmp15KfqoePNW9HczFmqaGNHwadV2a3b": "v3"}
SEEDS = "/media/martin/63d87370-5401-42f9-a718-bead975d75d4/fxhash-seeds"
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
AGGREGATE3 = bytes.fromhex("82ad56cb")
METADATA_INFO = bytes.fromhex("ffa90668")  # metadataInfo() -> (bytes baseURI, address)
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def get(url: str, body: bytes | None = None, timeout: float = 120) -> bytes:
    req = urllib.request.Request(url, body, {"content-type": "application/json",
                                             "user-agent": "fxhash-preservation/1.0"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data: bytes = r.read()
                return data
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 5:
                raise
        except OSError:
            if attempt == 5:
                raise
        time.sleep(2 ** attempt)
    raise AssertionError


def bigmap(contract: str, path: str) -> list[dict[str, Any]]:
    bm = json.loads(get(f"{TZKT}/contracts/{contract}/bigmaps/{path}"))
    rows: list[dict[str, Any]] = []
    while True:
        page = json.loads(get(f"{TZKT}/bigmaps/{bm['ptr']}/keys?limit=10000&sort.asc=id"
                              f"&offset={len(rows)}&select=key,value,active"))
        rows += page
        if len(page) < 10000:
            break
    if len(rows) != bm["totalKeys"]:
        raise SystemExit(f"{contract} {path}: {len(rows)} keys, expected {bm['totalKeys']}")
    return rows


def uri(hexstr: str) -> str:
    return bytes.fromhex(hexstr).decode("utf-8", "replace")


def list_tezos() -> list[dict[str, Any]]:
    out = []
    for contract, ver in TEZOS_ISSUERS.items():
        # codex entry type 0: ipfs:// URIs as bytes; otherwise raw 32-byte ONCHFS hashes
        codex = {k["key"]: [uri(v) if k["value"]["type"] == "0" else "onchfs://" + v
                            for v in k["value"]["value"]]
                 for k in bigmap(contract, "codex_entries")} if ver == "v3" else {}
        for k in bigmap(contract, "ledger"):
            v = k["value"] or {}
            row = {"chain": "tezos", "id": k["key"], "issuer": ver, "contract": contract,
                   "active": k["active"], "metadata_uri": uri(v.get("metadata", ""))}
            if "codex_id" in v:
                row["codex"] = codex.get(v["codex_id"])
            out.append(row)
        print(f"tezos {ver}: {sum(1 for r in out if r['issuer'] == ver)} projects", flush=True)
    return out


def list_evm(rpcs: dict[str, str]) -> list[dict[str, Any]]:
    from eth_abi import decode, encode
    out = []
    for chain, url in rpcs.items():
        with open(f"{SEEDS}/evm-{chain.lower()}-contracts.json") as f:
            contracts = sorted(json.load(f)["total_supply"])
        for i in range(0, len(contracts), 200):
            batch = contracts[i:i + 200]
            data = AGGREGATE3 + encode(["(address,bool,bytes)[]"],
                                       [[(c, True, METADATA_INFO) for c in batch]])
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [
                {"to": MULTICALL3, "data": "0x" + data.hex()}, "latest"]}).encode()
            res = json.loads(get(url, body))
            if "error" in res:
                raise SystemExit(f"{chain}: {res['error']}")
            for c, (ok, ret) in zip(batch, decode(["(bool,bytes)[]"],
                                                  bytes.fromhex(res["result"][2:]))[0]):
                row: dict[str, Any] = {"chain": chain.lower(), "id": c}
                if not ok:
                    row["error"] = "metadataInfo reverted"
                else:
                    base, pointer = decode(["bytes", "address"], ret)
                    row["base_uri"] = "ipfs://" + b58(b"\x12\x20" + base) \
                        if len(base) == 32 else base.decode("utf-8", "replace")
                    row["onchain_pointer"] = pointer
                out.append(row)
        # onchainPointer: an SSTORE2 contract (STOP byte + data) holding the 32-byte
        # ONCHFS hash of the project's code (equals the API's onchfs:// generative URI)
        for row in out:
            p = row.get("onchain_pointer")
            if row["chain"] == chain.lower() and p and int(p, 16):
                body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getCode",
                                   "params": [p, "latest"]}).encode()
                code = bytes.fromhex(json.loads(get(url, body))["result"][2:])
                row["onchain_pointer_data"] = code[1:].hex()
                time.sleep(0.2)
        print(f"{chain}: {len(contracts)} contracts", flush=True)
    return out


def cid_v0_file(data: bytes) -> str:
    unixfs = b"\x08\x02" + (b"\x12" + _varint(len(data)) + data if data else b"") \
        + b"\x18" + _varint(len(data))
    return b58(b"\x12\x20" + hashlib.sha256(b"\x0a" + _varint(len(unixfs)) + unixfs).digest())


def _varint(n: int) -> bytes:
    out = b""
    while True:
        x, n = n & 0x7F, n >> 7
        out += bytes([x | (0x80 if n else 0)])
        if not n:
            return out


def fetch(rows_path: str, out_path: str, gateway: str) -> None:
    with (gzip.open(rows_path, "rt") if rows_path.endswith(".gz") else open(rows_path)) as f:
        rows = [json.loads(line) for line in f]
    todo = []
    for r in rows:
        if r["chain"] == "tezos" and r["issuer"] != "v1" and r["metadata_uri"]:
            todo.append((r, r["metadata_uri"]))
        elif r["chain"] != "tezos" and r.get("base_uri", "").startswith("ipfs://"):
            todo.append((r, r["base_uri"].rstrip("/") + "/1/metadata.json"))
    done: dict[tuple[str, str], dict[str, Any]] = {}
    if os.path.exists(out_path):
        with gzip.open(out_path, "rt") as f:
            done = {(d["chain"], d["id"]): d for d in map(json.loads, f) if "error" not in d}

    def one(item: tuple[dict[str, Any], str]) -> dict[str, Any]:
        r, u = item
        d: dict[str, Any] = {"chain": r["chain"], "id": r["id"], "uri": u}
        path = u.removeprefix("ipfs://")
        try:
            data = get(gateway + path, timeout=180)
        except (OSError, ValueError) as e:
            d["error"] = f"{type(e).__name__} {getattr(e, 'code', '')}".strip()
            return d
        if "/" not in path and len(data) <= 262144 and cid_v0_file(data) != path:
            d["error"] = "cid mismatch"
            return d
        try:
            d["text"] = data.decode("utf-8")
        except UnicodeDecodeError:
            d["b64"] = base64.b64encode(data).decode()
        return d

    missing = [t for t in todo if (t[0]["chain"], t[0]["id"]) not in done]
    print(f"{len(todo)} documents, {len(missing)} to fetch", flush=True)
    with ThreadPoolExecutor(16) as pool:
        for i, d in enumerate(pool.map(one, missing), 1):
            done[(d["chain"], d["id"])] = d
            if i % 1000 == 0:
                print(f"{i}/{len(missing)}", flush=True)
    with gzip.open(out_path + ".tmp", "wt") as f:
        f.writelines(json.dumps(d) + "\n" for d in done.values())
    os.replace(out_path + ".tmp", out_path)
    print(f"{sum(1 for d in done.values() if 'error' in d)} failed", flush=True)


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "list":
        rpcs = dict(a.split("=", 1) for a in sys.argv[3:])
        rows = list_tezos() + list_evm(rpcs)
        out = sys.argv[2]
        with (gzip.open(out, "wt") if out.endswith(".gz") else open(out, "w")) as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
    elif len(sys.argv) == 5 and sys.argv[1] == "fetch":
        fetch(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
