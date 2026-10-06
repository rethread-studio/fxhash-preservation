#!/usr/bin/env python3
"""Snapshot fxhash project metadata (both chains) from the fxhash v2 GraphQL API.

    python scripts/fetch_fxhash_meta.py data/fxhash-meta.json.gz [data/fxhash-api-raw.json.gz]

The optional second file keeps the API rows exactly as returned (all pages, in order).
Writes a reproducible gzip (sorted, mtime 0) of one record per generative token:
id, chain, name, created_at (issuer creation on Tezos, contract deployment on EVM),
mint_opens_at, the author address, its on-chain name (Tezos user registry) and fxhash
account username, the user type, and the collaborators of collab contracts.
"""

from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

API = "https://api.v2.fxhash.xyz/v1/graphql"
PAGE = 100  # server-side cap
QUERY = """query ($off: Int!, $lim: Int!) { onchain { generative_token(
  order_by: [{created_at: asc}, {id: asc}], offset: $off, limit: $lim) {
  id chain name created_at mint_opens_at author_id
  wallet_account { username }
  user { name type collaborations {
    collaborator { id name wallet_account { username } } } }
} } }"""


def _post(offset: int) -> list[dict[str, Any]]:
    body = json.dumps({"query": QUERY, "variables": {"off": offset, "lim": PAGE}}).encode()
    for attempt in range(6):
        try:
            req = urllib.request.Request(API, body, {"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            if "errors" in data:
                raise RuntimeError(str(data["errors"])[:300])
            tokens: list[dict[str, Any]] = data["data"]["onchain"]["generative_token"]
            return tokens
        except (OSError, RuntimeError, ValueError) as e:
            print(f"offset {offset}, attempt {attempt}: {e}", file=sys.stderr)
            time.sleep(2**attempt)
    raise SystemExit(f"giving up at offset {offset}")


def _slim(t: dict[str, Any]) -> dict[str, Any]:
    user = t.get("user") or {}
    return {
        "id": str(t["id"]),
        "chain": t["chain"],
        "name": t["name"],
        "created_at": t["created_at"],
        "mint_opens_at": t["mint_opens_at"],
        "author": t["author_id"],
        "onchain_name": user.get("name"),
        "username": (t.get("wallet_account") or {}).get("username"),
        "user_type": user.get("type"),
        "collaborators": [
            {"id": c["collaborator"]["id"], "onchain_name": c["collaborator"].get("name"),
             "username": (c["collaborator"].get("wallet_account") or {}).get("username")}
            for c in user.get("collaborations") or []
        ],
    }


def _write_gz(path: str, doc: dict[str, Any]) -> None:
    with open(path, "wb") as f, gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0) as gz:
        gz.write(json.dumps(doc, ensure_ascii=False, indent=0).encode())


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    raw: list[dict[str, Any]] = []
    while True:
        page = _post(len(raw))
        raw.extend(page)
        if len(page) < PAGE:
            break
        time.sleep(0.2)
    tokens = sorted((_slim(t) for t in raw), key=lambda t: (t["chain"], t["id"]))
    ids = [(t["chain"], t["id"]) for t in tokens]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate ids: offset paging was not stable, re-run")
    _write_gz(sys.argv[1],
              {"source": API, "fetched_at": fetched_at, "count": len(tokens), "tokens": tokens})
    print(f"{len(tokens)} tokens -> {sys.argv[1]}")
    if len(sys.argv) == 3:
        _write_gz(sys.argv[2], {"source": API, "query": QUERY, "fetched_at": fetched_at,
                                "count": len(raw), "generative_token": raw})
        print(f"raw API rows -> {sys.argv[2]}")


if __name__ == "__main__":
    main()
