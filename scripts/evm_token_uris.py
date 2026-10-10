#!/usr/bin/env python3
"""tokenURI of every token of some EVM contracts, read from chain (stdlib only).

    python3 scripts/evm_token_uris.py CONTRACTS.jsonl OUT.jsonl [RPC]

CONTRACTS: one {"id": address, "supply": n} per line. Token ids are probed from 0 up
(Multicall3 aggregate3, failures allowed: a missing token reverts) until `supply` tokens
answered or 50 ids in a row failed. Output: one {"id", "token_id", "uri"} per token.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
AGGREGATE3 = "82ad56cb"
TOKEN_URI = "c87b56dd"


def word(n: int) -> str:
    return f"{n:064x}"


def encode_aggregate3(calls: list[tuple[str, str]]) -> str:
    """aggregate3((address target, bool allowFailure, bytes callData)[])"""
    head, tails, off = [], [], 32 * len(calls)
    for target, data in calls:
        raw = bytes.fromhex(data)
        pad = raw + b"\0" * (-len(raw) % 32)
        tup = word(int(target, 16)) + word(1) + word(96) + word(len(raw)) + pad.hex()
        head.append(word(off))
        tails.append(tup)
        off += len(tup) // 2
    return "0x" + AGGREGATE3 + word(32) + word(len(calls)) + "".join(head) + "".join(tails)


def decode_aggregate3(hexdata: str) -> list[tuple[bool, bytes]]:
    b = bytes.fromhex(hexdata[2:])
    n = int.from_bytes(b[32:64], "big")
    out = []
    for i in range(n):
        p = 64 + int.from_bytes(b[64 + 32 * i:96 + 32 * i], "big")
        ok = bool(int.from_bytes(b[p:p + 32], "big"))
        q = p + int.from_bytes(b[p + 32:p + 64], "big")
        ln = int.from_bytes(b[q:q + 32], "big")
        out.append((ok, b[q + 32:q + 32 + ln]))
    return out


def abi_string(ret: bytes) -> str:
    off = int.from_bytes(ret[:32], "big")
    n = int.from_bytes(ret[off:off + 32], "big")
    return ret[off + 32:off + 32 + n].decode("utf-8", "replace")


def rpc_call(url: str, data: str) -> str:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": MULTICALL3, "data": data}, "latest"]}).encode()
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json",
                                                     "User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read())
            if "result" in res:
                return res["result"]
        except OSError:
            pass
        time.sleep(2 ** attempt)
    raise RuntimeError("eth_call failed")


def main() -> None:
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    url = sys.argv[3] if len(sys.argv) > 3 else "https://base-rpc.publicnode.com"
    with open(sys.argv[1]) as f:
        contracts = [json.loads(line) for line in f]
    with open(sys.argv[2], "w") as out:
        for c in contracts:
            found, misses, start = 0, 0, 0
            while found < c["supply"] and misses < 50:
                ids = range(start, start + 100)
                calls = [(c["id"], TOKEN_URI + word(i)) for i in ids]
                for i, (ok, ret) in zip(ids, decode_aggregate3(rpc_call(url,
                                                                 encode_aggregate3(calls)))):
                    if ok and ret:
                        out.write(json.dumps({"id": c["id"], "token_id": i,
                                              "uri": abi_string(ret)}) + "\n")
                        found, misses = found + 1, 0
                    else:
                        misses += 1
                start += 100
                time.sleep(0.2)
            print(c["id"], found, "of", c["supply"], flush=True)


if __name__ == "__main__":
    main()
