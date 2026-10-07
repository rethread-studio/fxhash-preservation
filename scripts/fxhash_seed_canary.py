#!/usr/bin/env python3
"""Canary: can we capture the seed of every minted fxhash iteration, and is it right?

50 random minted tokens (40 Tezos, 10 EVM) from projects in the fxhash archive. For each,
the seed (`generation_hash`) and params (`input_bytes`) from the fxhash v2 API are compared
with independent copies:
- Tezos: TzKT's cache of the token's IPFS metadata (`iterationHash`), and the
  killedbyapixel/fxhashArchive seed chunks (also from TzKT, captured 2026-08-18);
- EVM: the chain itself (`genArtInfo(tokenId)` -> (minter, seed, fxParams) on the token
  contract), and fxhash's own metadata.json.
Writes canary.jsonl (one record per token, in the proposed seed-record format).
"""

from __future__ import annotations

import gzip
import json
import random
import sys
import time
import urllib.request
from typing import Any

API = "https://api.v2.fxhash.xyz/v1/graphql"
TZKT = "https://api.tzkt.io/v1"
FXA = "https://raw.githubusercontent.com/killedbyapixel/fxhashArchive/{sha}/public/data/seeds"
RPC = {"BASE": "https://mainnet.base.org", "ETHEREUM": "https://ethereum-rpc.publicnode.com"}
GENARTINFO = "a5a2d097"  # keccak("genArtInfo(uint256)")[:4]
FXA_CONTRACTS = {"KT1KEa8z6vWXDJrVqtMrAeDVzsvxat3kHaCE": 0,
                 "KT1U6EHmNxJTkvaWJ4ThczG4FSDaHC21ssvi": 1,
                 "KT1EfsNuqwLAWDd3o4pvfUx1CAh5GMdTrRvr": 2}


def http(url: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data, {"content-type": "application/json",
                                                     "user-agent": "swhprep-canary"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except (OSError, ValueError) as e:
            if attempt == 3:
                return {"_error": str(e)}
            time.sleep(2 ** attempt)
    return None


def gql(query: str, variables: dict[str, Any]) -> Any:
    d = http(API, {"query": query, "variables": variables})
    if "errors" in d or "_error" in d:
        raise RuntimeError(str(d)[:300])
    return d["data"]["onchain"]


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit("usage: fxhash_seed_canary.py data/fxhash-meta.json.gz ids.json "
                         "<fxhashArchive commit sha> OUT.jsonl")
    meta_path, ids_path, sha, out = sys.argv[1:5]
    with gzip.open(meta_path) as mf:
        meta = json.load(mf)
    with open(ids_path) as idf:
        ids = json.load(idf)
    # our projects, by (chain, id)
    ours: dict[tuple[str, str], dict[str, Any]] = {}
    for t in meta["tokens"]:
        ours[(t["chain"], t["id"])] = t
    folders = {}
    for rel in ids:
        pid = rel.rsplit("__", 1)[1]
        folders[pid.lower()] = rel
    tz = [t for k, t in ours.items() if k[0] == "TEZOS" and t["id"].lower() in folders]
    evm = [t for k, t in ours.items() if k[0] != "TEZOS" and t["id"].lower() in folders]
    rng = random.Random(20261007)
    picked: list[dict[str, Any]] = []
    for pool, n in ((tz, 40), (evm, 10)):
        cands = rng.sample(pool, len(pool))
        got = 0
        for t in cands:
            if got == n:
                break
            g = gql("query($id:String!){onchain{generative_token(where:{id:{_eq:$id}})"
                    "{iterations_count}}}", {"id": t["id"]})["generative_token"]
            count = int(g[0]["iterations_count"] or 0) if g else 0
            if count == 0:
                continue  # never minted
            it = rng.randint(1, count)
            o = gql("""query($id:String!,$it:numeric!){onchain{objkt(where:{issuer_id:{_eq:$id},
                    iteration:{_eq:$it}}){id iteration generation_hash input_bytes minter_id
                    created_at metadata_uri}}}""", {"id": t["id"], "it": it})["objkt"]
            if not o:
                continue
            picked.append({"project": t, "folder": folders[t["id"].lower()], "objkt": o[0],
                           "iterations": count})
            got += 1
    fxa_cache: dict[str, Any] = {}
    with open(out, "w") as f:
        for p in picked:
            t, o = p["project"], p["objkt"]
            contract, token_id = o["id"].rsplit("-", 1)
            rec = {"chain": t["chain"], "project_id": t["id"], "folder": p["folder"],
                   "contract": contract, "token_id": token_id,
                   "iteration": int(o["iteration"]), "seed": o["generation_hash"],
                   "params": o["input_bytes"], "minter": o["minter_id"],
                   "minted_at": o["created_at"], "metadata_uri": o["metadata_uri"],
                   "checks": {}}
            if t["chain"] == "TEZOS":
                tk = http(f"{TZKT}/tokens?contract={contract}&tokenId={token_id}"
                          "&select=metadata")
                m = tk[0] if isinstance(tk, list) and tk else {}
                rec["checks"]["tzkt_iterationHash"] = (m or {}).get("iterationHash")
                rec["checks"]["tzkt_inputBytes"] = (m or {}).get("inputBytes")
                idx = FXA_CONTRACTS.get(contract)
                if idx is not None:
                    chunk = f"{idx}/{int(token_id) // 10000:04d}.json"
                    if chunk not in fxa_cache:
                        fxa_cache[chunk] = http(f"{FXA.format(sha=sha)}/{chunk}")
                    c = fxa_cache[chunk]
                    rec["checks"]["fxarchive_seed"] = c["seeds"][int(token_id) - c["from"]]
            else:
                data = "0x" + GENARTINFO + f"{int(token_id):064x}"
                r = http(RPC[t["chain"]], {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                                           "params": [{"to": contract, "data": data}, "latest"]})
                res = (r or {}).get("result", "")[2:]
                if len(res) >= 192:
                    minter = "0x" + res[24:64]
                    seed = "0x" + res[64:128]
                    off = int(res[128:192], 16) * 2
                    ln = int(res[off:off + 64], 16)
                    params = res[off + 64:off + 64 + ln * 2]
                    rec["checks"]["chain_seed"] = seed
                    rec["checks"]["chain_minter"] = minter
                    rec["checks"]["chain_params"] = ("0x" + params) if ln else None
                else:
                    rec["checks"]["chain_error"] = str(r)[:200]
                mj = http(o["metadata_uri"]) if o["metadata_uri"].startswith("http") else None
                rec["checks"]["fxhash_metadata"] = {k: (mj or {}).get(k) for k in
                                                    ("seed", "iterationHash", "_error")
                                                    if (mj or {}).get(k) is not None}
            f.write(json.dumps(rec) + "\n")
            print(rec["chain"], rec["folder"][:45], rec["iteration"], rec["seed"][:14],
                  json.dumps(rec["checks"])[:160], flush=True)


if __name__ == "__main__":
    main()
