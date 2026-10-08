#!/usr/bin/env python3
"""Read the seed of every minted fxhash EVM token straight from the chain.

    uv run --with eth-abi python scripts/fxhash_evm_seeds.py data/fxhash-meta.json.gz OUTDIR \
        ETHEREUM=http://127.0.0.1:18545 BASE=https://mainnet.base.org,https://1rpc.io/base

For every fxhash project contract (FxGenArt721) of the given chains, in the metadata
snapshot: token ids run from 1 to `totalSupply()`; `genArtInfo(id)` returns
(minter, seed, fxParams). Calls are batched through Multicall3 (same address on every
chain). Several comma-separated RPC URLs for a chain share the batches, one request in
flight per URL (public endpoints rate-limit). Writes OUTDIR/evm-<chain>.jsonl.gz, one line
per token: {"chain", "contract", "token_id", "seed", "params", "minter", "block"}, and
OUTDIR/evm-<chain>-contracts.json (totalSupply per contract, contracts that revert).
Contracts without totalSupply (MediaFactory's CollectiveMedia, 2026) are read too: their
tokens are the ids below nextTokenId() that have an owner, and tokenInfo gives the seed.
Reads are made at `latest` (public nodes keep only minutes of state; a minted token's seed
never changes); `block` is the head when the run started.
"""

from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from eth_abi import decode, encode

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
AGGREGATE3 = bytes.fromhex("82ad56cb")  # aggregate3((address,bool,bytes)[])
TOTAL_SUPPLY = bytes.fromhex("18160ddd")
GEN_ART_INFO = bytes.fromhex("a5a2d097")  # genArtInfo(uint256) -> (minter, seed, fxParams)
# Later full (non-clone) fxhash contracts on Base revert on genArtInfo; their
# tokenInfo(uint256) returns 4 words, the 3rd being the seed (checked against the fxhash API)
TOKEN_INFO = bytes.fromhex("cc33c875")
# MediaFactory contracts (CollectiveMedia, 2026) have no totalSupply: their tokens are the
# ids below nextTokenId() that have an owner, and tokenInfo returns (version, seed)
NEXT_TOKEN_ID = bytes.fromhex("75794a3c")
OWNER_OF = bytes.fromhex("6352211e")  # ownerOf(uint256)
# 250 genArtInfo results are ~64 KB; erigon's default --rpc.returndata.limit is 100 KB
BATCH = 250


def rpc(url: str, method: str, params: list[Any]) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, body, {"content-type": "application/json",
                                                     "user-agent": "swhprep-seeds"})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
            if "error" in d:
                raise RuntimeError(str(d["error"])[:300])
            return d["result"]
        except (OSError, ValueError, RuntimeError) as e:
            if attempt == 5:
                raise
            print(f"  retry {attempt} {method}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise AssertionError


def multicall(url: str, block: str, calls: list[tuple[str, bytes]]) -> list[bytes | None]:
    """Results of (target, calldata) calls; None where the call reverted."""
    data = AGGREGATE3 + encode(["(address,bool,bytes)[]"], [[(t, True, c) for t, c in calls]])
    out = rpc(url, "eth_call", [{"to": MULTICALL3, "data": "0x" + data.hex()}, block])
    (results,) = decode(["(bool,bytes)[]"], bytes.fromhex(out[2:]))
    return [r if ok else None for ok, r in results]


def chunks(xs: list[Any], n: int) -> list[list[Any]]:
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def run_chain(chain: str, urls: list[str], contracts: list[str], outdir: str) -> None:
    url = urls[0]
    block = rpc(url, "eth_blockNumber", [])
    print(f"{chain}: {len(contracts)} contracts at block {int(block, 16)}", file=sys.stderr)
    supplies: dict[str, int] = {}
    reverted: list[str] = []  # not an FxGenArt721 (e.g. fxhash test contracts)
    for batch in chunks(contracts, BATCH):
        for c, r in zip(batch, multicall(url, "latest", [(c, TOTAL_SUPPLY) for c in batch]),
                        strict=True):
            supplies[c] = decode(["uint256"], r)[0] if r else 0
            if r is None:
                reverted.append(c)
    owned: dict[str, list[int]] = {}  # token ids of contracts without totalSupply
    for c in reverted:
        (nxt,) = multicall(url, "latest", [(c, NEXT_TOKEN_ID)])
        if nxt:
            ids = list(range(decode(["uint256"], nxt)[0] + 1))
            owners = multicall(url, "latest",
                               [(c, OWNER_OF + encode(["uint256"], [i])) for i in ids])
            owned[c] = [i for i, o in zip(ids, owners, strict=True) if o]
    with open(f"{outdir}/evm-{chain.lower()}-contracts.json", "w") as sf:
        # host only: provider URLs often embed an API key in the path
        hosts = [urllib.parse.urlsplit(u).netloc for u in urls]
        json.dump({"chain": chain, "rpc_hosts": hosts, "block": int(block, 16),
                   "total_supply": supplies, "total_supply_reverted": reverted,
                   "owned_token_ids": {c: len(v) for c, v in owned.items()}}, sf, indent=1)
    todo = [(c, i) for c in contracts for i in range(1, supplies[c] + 1)]
    todo += [(c, i) for c, ids in owned.items() for i in ids]
    print(f"{chain}: {len(todo)} tokens", file=sys.stderr)

    def read(job: tuple[int, list[tuple[str, int]]]) -> list[dict[str, Any]]:
        n, batch = job
        calls = [(c, GEN_ART_INFO + encode(["uint256"], [i])) for c, i in batch]
        url = urls[n % len(urls)]
        rows = []
        for (c, i), r in zip(batch, multicall(url, "latest", calls), strict=True):
            row: dict[str, Any] = {"chain": chain, "contract": c, "token_id": i,
                                   "seed": None, "params": None, "minter": None,
                                   "getter": None, "block": int(block, 16)}
            if r:
                minter, seed, params = decode(["address", "bytes32", "bytes"], r)
                row.update(seed="0x" + seed.hex(), params=("0x" + params.hex()) if params
                           else None, minter=minter, getter="genArtInfo")
            rows.append(row)
        retry = [row for row in rows if row["getter"] is None]
        if retry:
            calls = [(row["contract"], TOKEN_INFO + encode(["uint256"], [row["token_id"]]))
                     for row in retry]
            for row, r in zip(retry, multicall(url, "latest", calls), strict=True):
                if r and len(r) >= 96:
                    row.update(seed="0x" + r[64:96].hex(), getter="tokenInfo")
                elif r and len(r) == 64:  # CollectiveMedia: (version, seed)
                    row.update(seed="0x" + r[32:64].hex(), getter="tokenInfo(version,seed)")
        return rows

    path = f"{outdir}/evm-{chain.lower()}.jsonl.gz"
    done = 0
    with ThreadPoolExecutor(len(urls)) as pool, gzip.open(path, "wt") as f:
        for rows in pool.map(read, enumerate(chunks(todo, BATCH))):
            for row in rows:
                f.write(json.dumps(row) + "\n")
            done += len(rows)
            if done % 50000 < BATCH:
                print(f"{chain}: {done}/{len(todo)}", file=sys.stderr)
    print(f"{chain}: {done} tokens -> {path}", file=sys.stderr)


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    meta_path, outdir = sys.argv[1], sys.argv[2]
    urls = dict(a.split("=", 1) for a in sys.argv[3:])
    with gzip.open(meta_path) as f:
        tokens = json.load(f)["tokens"]
    for chain, url in urls.items():
        contracts = sorted(t["id"] for t in tokens if t["chain"] == chain)
        run_chain(chain, url.split(","), contracts, outdir)


if __name__ == "__main__":
    main()
