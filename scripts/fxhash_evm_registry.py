#!/usr/bin/env python3
"""Every fxhash EVM project, as registered on chain, against the fxhash API snapshot.

    uv run --with eth-abi --with 'eth-hash[pycryptodome]' \
        python scripts/fxhash_evm_registry.py data/fxhash-meta.json.gz \
        data/fxhash-evm-registry.jsonl.gz \
        ETHEREUM=https://ethereum-rpc.publicnode.com BASE=https://mainnet.base.org

The chain is the ground truth for which projects exist; the API snapshot is not. fxhash
deployed several project factories, all from the same deployer (0x0E7De56c...), each with
an on-chain registry read here with plain `eth_call`s at `latest` (no logs: public Base
RPCs limit eth_getLogs to a few blocks):
- FxIssuerFactory (Ethereum, Base): `projectId()` = count, `projects(uint96)` = contract;
  the count equals the number of ProjectCreated events (140 and 559 on 2026-10-08).
- ProjectFactory, ProjectFactoryV2 (Base, 2025, project + ERC-20 token launches):
  `projectId()`, `projects(uint256)`.
- MediaFactory (Ethereum, Base, 2026): `nextCollectiveId()`, `collectiveMedias(uint256)`
  and `nextCustomId()`, `customMedias(uint256)`.
- FxDopplerFactory (Base, 2025-09, `bundleCreateAssetAndProject`) keeps no registry: it
  deploys each project contract itself with CREATE, so its projects are the addresses
  derived from (factory, nonce) for nonce 1 .. getTransactionCount - 1 that are token
  contracts.
Factories without projects (an earlier Base ProjectFactory, the Base MediaFactory) are
listed too, so an empty registry shows as 0 rather than as missing. Ids run from 0 to the
counter, both included, and zero addresses are skipped: the factories differ on whether
ids start at 0 or 1.

For every registered contract and every API project, the number of minted tokens (one
token = one seed) is `totalSupply()`, or for MediaFactory contracts, which have none, the
ids below `nextTokenId()` that have an owner. Writes OUT, one line per project: {"chain",
"factory", "registry_id", "contract", "in_api", "total_supply"}; API projects found in no
registry get factory null. Prints counts per factory and per chain.
"""

from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.request
from typing import Any

from eth_abi import decode, encode
from eth_utils import keccak

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
AGGREGATE3 = bytes.fromhex("82ad56cb")  # aggregate3((address,bool,bytes)[])
TOTAL_SUPPLY = bytes.fromhex("18160ddd")
NEXT_TOKEN_ID = bytes.fromhex("75794a3c")
OWNER_OF = bytes.fromhex("6352211e")  # ownerOf(uint256)
BATCH = 200

# factories that deploy project contracts with CREATE and keep no registry
CREATORS = [("BASE", "FxDopplerFactory", "0xD4D978B212D729EA88eD607c925A63103be15f1B")]

# (chain, factory name, address, counter selector, getter selector)
REGISTRIES = [
    ("ETHEREUM", "FxIssuerFactory", "0x442295de8A31d65026dBc09c29d469F6854f188a",
     "3fafa127", "94a37936"),  # projectId(), projects(uint96)
    ("ETHEREUM", "MediaFactory collective", "0x7FE0F93695FDC9fF438df3e580f3c0B1389d10cD",
     "67143a78", "9664dd1a"),  # nextCollectiveId(), collectiveMedias(uint256)
    ("ETHEREUM", "MediaFactory custom", "0x7FE0F93695FDC9fF438df3e580f3c0B1389d10cD",
     "cf209d01", "8be2bbb1"),  # nextCustomId(), customMedias(uint256)
    ("BASE", "FxIssuerFactory", "0xf05636d65c7a10dF989eC2411D4F3230d3A02f3D",
     "3fafa127", "94a37936"),
    ("BASE", "ProjectFactory (2025-06-24)", "0x1884bE075623583f93e93D69c6068a3B884fed1b",
     "3fafa127", "107046bd"),  # projectId(), projects(uint256)
    ("BASE", "ProjectFactory", "0x0612ec1A199617A5b3DF627755A34F69469AEA25",
     "3fafa127", "107046bd"),
    ("BASE", "ProjectFactoryV2", "0xd3fa57c468fD9E5d2E32F05d5797291921793d76",
     "3fafa127", "107046bd"),
    ("BASE", "MediaFactory collective", "0xCeBda97CEAb732f3e07D87b064aC5f1F17D252E3",
     "67143a78", "9664dd1a"),
    ("BASE", "MediaFactory custom", "0xCeBda97CEAb732f3e07D87b064aC5f1F17D252E3",
     "cf209d01", "8be2bbb1"),
]


def rpc(url: str, method: str, params: list[Any]) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for attempt in range(7):
        try:
            req = urllib.request.Request(url, body, {"content-type": "application/json",
                                                     "user-agent": "swhprep-registry"})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
            if "error" in d:
                raise RuntimeError(str(d["error"])[:300])
            return d["result"]
        except (OSError, ValueError, RuntimeError) as e:
            if attempt == 6:
                raise
            print(f"  retry {attempt} {method}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise AssertionError


def multicall(url: str, calls: list[tuple[str, bytes]]) -> list[bytes | None]:
    """Results of (target, calldata) calls; None where the call reverted."""
    out: list[bytes | None] = []
    for i in range(0, len(calls), BATCH):
        part = calls[i:i + BATCH]
        data = AGGREGATE3 + encode(["(address,bool,bytes)[]"], [[(t, True, c) for t, c in part]])
        res = rpc(url, "eth_call", [{"to": MULTICALL3, "data": "0x" + data.hex()}, "latest"])
        (results,) = decode(["(bool,bytes)[]"], bytes.fromhex(res[2:]))
        out += [r if ok and r else None for ok, r in results]
    return out


def uint(b: bytes | None) -> int | None:
    return int.from_bytes(b[:32], "big") if b else None


def registry(url: str, address: str, counter: str, getter: str) -> list[tuple[int, str]]:
    (count_raw,) = multicall(url, [(address, bytes.fromhex(counter))])
    count = uint(count_raw) or 0
    calls = [(address, bytes.fromhex(getter) + encode(["uint256"], [i])) for i in range(count + 1)]
    found = []
    for i, r in enumerate(multicall(url, calls)):
        addr = "0x" + r[12:32].hex() if r else None
        if addr and int(addr, 16):
            found.append((i, addr))
    return found


def create_address(sender: str, nonce: int) -> str:
    """Address of the contract that `sender` deploys with CREATE at `nonce` (RLP list)."""
    n = nonce.to_bytes((nonce.bit_length() + 7) // 8, "big")
    nb = n if len(n) == 1 and n[0] < 0x80 else bytes([0x80 + len(n)]) + n
    payload = bytes([0x94]) + bytes.fromhex(sender[2:]) + nb
    digest: bytes = keccak(bytes([0xc0 + len(payload)]) + payload)
    return "0x" + digest[12:].hex()


def created(url: str, address: str) -> list[tuple[int, str]]:
    """Contracts deployed by `address` with CREATE that are token contracts (answer
    totalSupply): the Doppler factory's first creation (nonce 1) is a 977-byte helper."""
    nonce = int(rpc(url, "eth_getTransactionCount", [address, "latest"]), 16)
    addrs = [(i, create_address(address, i)) for i in range(1, nonce)]
    supplies = multicall(url, [(a, TOTAL_SUPPLY) for _, a in addrs])
    return [(i, a) for (i, a), s in zip(addrs, supplies) if s is not None]


def minted(url: str, contracts: list[str]) -> list[int | None]:
    counts = [uint(s) for s in multicall(url, [(c, TOTAL_SUPPLY) for c in contracts])]
    for k, c in enumerate(contracts):
        if counts[k] is None:
            (nxt,) = multicall(url, [(c, NEXT_TOKEN_ID)])
            if nxt is not None:
                ids = range((uint(nxt) or 0) + 1)
                owners = multicall(url, [(c, OWNER_OF + encode(["uint256"], [i])) for i in ids])
                counts[k] = sum(o is not None for o in owners)
    return counts


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    with gzip.open(sys.argv[1]) as f:
        meta = json.load(f)["tokens"]
    urls = dict(a.split("=", 1) for a in sys.argv[3:])
    rows: list[dict[str, Any]] = []
    for chain in sorted({r[0] for r in REGISTRIES}):
        url = urls[chain].split(",")[0]
        api = {t["id"].lower() for t in meta if t["chain"] == chain}
        seen: set[str] = set()
        sources: list[tuple[str, str, list[tuple[int, str]]]] = []
        for ch, name, address, counter, getter in REGISTRIES:
            if ch == chain:
                sources.append((name, address, registry(url, address, counter, getter)))
        for ch, name, address in CREATORS:
            if ch == chain:
                sources.append((name, address, created(url, address)))
        for name, address, found in sources:
            print(f"{chain} {name} {address[:10]}: {len(found)} projects", file=sys.stderr)
            for i, addr in found:
                seen.add(addr)
                rows.append({"chain": chain, "factory": name, "registry_id": i,
                             "contract": addr, "in_api": addr in api})
        for addr in sorted(api - seen):
            rows.append({"chain": chain, "factory": None, "registry_id": None,
                         "contract": addr, "in_api": True})
        mine = [r for r in rows if r["chain"] == chain]
        for r, n in zip(mine, minted(url, [r["contract"] for r in mine])):
            r["total_supply"] = n
    with gzip.open(sys.argv[2], "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    for chain in sorted({r["chain"] for r in rows}):
        mine = [r for r in rows if r["chain"] == chain]
        reg = [r for r in mine if r["factory"]]
        print(f"{chain}: {len(reg)} registered ({sum(r['in_api'] for r in reg)} in API, "
              f"{sum(not r['in_api'] for r in reg)} only on chain), "
              f"{sum(1 for r in mine if not r['factory'])} API projects in no registry; "
              f"tokens {sum(r['total_supply'] or 0 for r in reg)} registered, "
              f"{sum(1 for r in mine if r['total_supply'] is None)} contracts without totalSupply")


if __name__ == "__main__":
    main()
