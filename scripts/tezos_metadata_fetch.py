#!/usr/bin/env python3
"""Copy the IPFS metadata JSON of every minted fxhash Tezos token (stdlib only).

    python3 tezos_metadata_fetch.py list OUTDIR      # token -> metadata URI, from the chain
    python3 tezos_metadata_fetch.py fetch OUTDIR [--workers N]

`list` reads the `token_metadata` big map of the three fxhash gentk contracts through
TzKT (current on-chain values: signed metadata, not TzKT's cached placeholder) into
OUTDIR/uris/<contract>.jsonl.gz.

`fetch` downloads each `ipfs://<CIDv0>` document through public gateways and keeps it only
if the bytes hash back to that CID (a single-block UnixFS file, as these < 256 KiB
documents are), so a gateway can't substitute content. Output, per 10,000 tokens:
OUTDIR/metadata/<contract>/<chunk>.jsonl.gz, one line per token: {"token_id", "uri",
"via", "text"} (exact UTF-8 bytes; {"b64"} if not UTF-8; `via`: the gateway host that
served it) or {"token_id", "uri", "error"}. Tokens are split across gateways (SPLIT), each
falling back to the others. A chunk
is written once all its tokens were tried; failed tokens are retried on the next run.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import http.client
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

TZKT = "https://api.tzkt.io/v1"
CONTRACTS = ["KT1KEa8z6vWXDJrVqtMrAeDVzsvxat3kHaCE", "KT1U6EHmNxJTkvaWJ4ThczG4FSDaHC21ssvi",
             "KT1EfsNuqwLAWDd3o4pvfUx1CAh5GMdTrRvr"]
# In priority order: our kubo node on rootmab (ssh tunnel from tiramisu, see
# tezos_metadata_tunnel.sh), then a public gateway as fallback. Override with
# FXHASH_GATEWAYS="url1,url2". Every document is CID-checked whatever its source.
GATEWAYS = os.environ.get(
    "FXHASH_GATEWAYS", "http://127.0.0.1:28086/ipfs/,https://ipfs.filebase.io/ipfs/").split(",")
CHUNK = 10_000
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def varint(n: int) -> bytes:
    out = b""
    while True:
        x = n & 0x7F
        n >>= 7
        out += bytes([x | (0x80 if n else 0)])
        if not n:
            return out


def cid_v0(data: bytes) -> str:
    """CIDv0 of `data` added as one UnixFS file block (kubo defaults, size <= 256 KiB)."""
    unixfs = b"\x08\x02" + (b"\x12" + varint(len(data)) + data if data else b"") \
        + b"\x18" + varint(len(data))
    node = b"\x0a" + varint(len(unixfs)) + unixfs
    return b58(b"\x12\x20" + hashlib.sha256(node).digest())


def get(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"user-agent": "fxhash-preservation/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data: bytes = r.read()
    return data


def cmd_list(out: str) -> None:
    os.makedirs(f"{out}/uris", exist_ok=True)
    for c in CONTRACTS:
        path = f"{out}/uris/{c}.jsonl.gz"
        if os.path.exists(path):
            print(f"{c}: exists", flush=True)
            continue
        bm = json.loads(get(f"{TZKT}/contracts/{c}/bigmaps/token_metadata"))
        ptr, total = bm["ptr"], bm["totalKeys"]
        rows: list[dict[str, Any]] = []
        while True:
            # offset paging over a stable sort: keys are only ever added to this big map
            page = json.loads(get(f"{TZKT}/bigmaps/{ptr}/keys?limit=10000&sort.asc=id"
                                  f"&offset={len(rows)}&select=key,value", timeout=120))
            for k in page:
                info = (k["value"] or {}).get("token_info", {})
                uri = bytes.fromhex(info.get("", "")).decode("utf-8", "replace")
                rows.append({"token_id": int(k["key"]), "uri": uri})
            print(f"{c}: {len(rows)}/{total}", flush=True)
            if len(page) < 10000:
                break
        ids = {r["token_id"] for r in rows}
        if len(rows) != total or len(ids) != total:
            raise SystemExit(f"{c}: {len(rows)} rows, {len(ids)} distinct, expected {total}")
        rows.sort(key=lambda r: r["token_id"])
        with gzip.open(path + ".tmp", "wt") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        os.replace(path + ".tmp", path)


# Share of tokens whose first try goes to each gateway (the other ones are fallbacks):
# 60% to our node, 40% to the public gateway, which rate-limits above ~20 req/s.
SPLIT = [float(x) for x in os.environ.get("FXHASH_SPLIT", "0.6,0.4").split(",")]


class Gateways:
    """Split tokens across gateways; one that is overloaded or down is benched for a while."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.bench: dict[str, float] = {}

    def order(self, token_id: int) -> list[str]:
        x = (token_id * 2654435761 % 2**32) / 2**32  # stable pseudo-random in [0, 1)
        k, acc = 0, 0.0
        for k, share in enumerate(SPLIT[:len(GATEWAYS)]):
            acc += share
            if x < acc:
                break
        pref = GATEWAYS[k:] + GATEWAYS[:k]
        with self.lock:
            now = time.time()
            return [g for g in pref if self.bench.get(g, 0) < now] or pref

    def penalize(self, g: str, seconds: float) -> None:
        with self.lock:
            self.bench[g] = time.time() + seconds


def fetch_one(gw: Gateways, row: dict[str, Any]) -> dict[str, Any]:
    uri = row["uri"]
    out: dict[str, Any] = {"token_id": row["token_id"], "uri": uri}
    if not uri.startswith("ipfs://"):
        out["error"] = "not an ipfs:// uri"
        return out
    cid = uri.removeprefix("ipfs://").split("/")[0]
    errors = []
    for attempt in range(3):
        for g in gw.order(row["token_id"]):
            try:
                data = get(g + cid, timeout=60)
            except urllib.error.HTTPError as e:
                errors.append(f"{g.split('/')[2]} {e.code}")
                # 504 from kubo = not found in time: this CID only, try the next gateway
                if e.code in (403, 429) or (e.code >= 500 and e.code != 504):
                    gw.penalize(g, 60)
                continue
            # HTTPException: e.g. IncompleteRead, a response cut short (killed the whole
            # run once, 2026-10-07 ~20:15, when it was not caught here)
            except (OSError, ValueError, http.client.HTTPException) as e:
                errors.append(f"{g.split('/')[2]} {type(e).__name__}")
                gw.penalize(g, 30)
                continue
            if cid.startswith("Qm") and len(data) <= 262144 and cid_v0(data) != cid:
                errors.append(f"{g.split('/')[2]} cid mismatch")
                continue
            out["via"] = g.split("/")[2]
            try:
                out["text"] = data.decode("utf-8")
            except UnicodeDecodeError:
                out["b64"] = base64.b64encode(data).decode()
            return out
        time.sleep(2 ** attempt)
    out["error"] = "; ".join(errors[-4:])
    return out


def cmd_fetch(out: str, workers: int) -> None:
    gw = Gateways()
    for c in CONTRACTS:
        with gzip.open(f"{out}/uris/{c}.jsonl.gz", "rt") as f:
            rows = [json.loads(line) for line in f]
        os.makedirs(f"{out}/metadata/{c}", exist_ok=True)
        chunks: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            chunks.setdefault(r["token_id"] // CHUNK, []).append(r)
        for n, todo in sorted(chunks.items()):
            path = f"{out}/metadata/{c}/{n:04d}.jsonl.gz"
            done: dict[int, dict[str, Any]] = {}
            if os.path.exists(path):
                with gzip.open(path, "rt") as f:
                    done = {d["token_id"]: d for d in map(json.loads, f) if "error" not in d}
                if len(done) == len(todo):
                    continue
            missing = [r for r in todo if r["token_id"] not in done]
            t0 = time.time()
            with ThreadPoolExecutor(workers) as pool:
                for d in pool.map(lambda r: fetch_one(gw, r), missing):
                    done[d["token_id"]] = d
            bad = sum(1 for d in done.values() if "error" in d)
            with gzip.open(path + ".tmp", "wt") as f:
                f.writelines(json.dumps(done[r["token_id"]]) + "\n" for r in todo)
            os.replace(path + ".tmp", path)
            print(f"{time.strftime('%F %T')} {c} chunk {n:04d}: {len(missing)} fetched in "
                  f"{time.time() - t0:.0f}s, {bad} failed", flush=True)


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in ("list", "fetch"):
        raise SystemExit(__doc__)
    if sys.argv[1] == "list":
        cmd_list(sys.argv[2])
    else:
        workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv \
            else 16
        cmd_fetch(sys.argv[2], workers)


if __name__ == "__main__":
    main()
