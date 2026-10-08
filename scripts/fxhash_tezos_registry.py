#!/usr/bin/env python3
"""Every fxhash Tezos project, as registered on chain, counted once (stdlib only).

    python3 scripts/fxhash_tezos_registry.py data/fxhash-tezos-registry.json.gz

Reads, through TzKT, the `ledger` big map of the three issuer contracts (the project
registries) and the token count of the three GENTK contracts. Issuer v2 took over the
projects of issuer v1 under the same ids, so a project is counted in the latest registry
where it appears: an id still active in v1 but removed from v2 was burned in v2 (the
script records the entrypoint and sender of the removal, and whether the sender is the
author). Writes {"fetched_at", "contracts": [...], "projects": {id: {"registry", ...}},
"v1_burned_in_v2": [...]} and prints the counts the paper's table uses.
"""

from __future__ import annotations

import datetime
import gzip
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

TZKT = "https://api.tzkt.io/v1"
ISSUERS = [("Issuer v1", "KT1XCoGnfupWk7Sp8536EfrxcP73LmT68Nyr"),
           ("Issuer v2", "KT1BJC12dG17CVvPKJ1VYaNnaT5mzfnUTwXv"),
           ("Issuer v3", "KT1Xpmp15KfqoePNW9HczFmqaGNHwadV2a3b")]
GENTKS = [("GENTK v1", "KT1KEa8z6vWXDJrVqtMrAeDVzsvxat3kHaCE"),
          ("GENTK v2", "KT1U6EHmNxJTkvaWJ4ThczG4FSDaHC21ssvi"),
          ("GENTK v3", "KT1EfsNuqwLAWDd3o4pvfUx1CAh5GMdTrRvr")]


def get(path: str) -> Any:
    req = urllib.request.Request(TZKT + path, headers={"user-agent": "fxhash-preservation/1.0"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except (OSError, ValueError) as e:
            if attempt == 5 or (isinstance(e, urllib.error.HTTPError) and e.code == 404):
                raise
            time.sleep(2 ** attempt)
    raise AssertionError


def ledger(contract: str) -> tuple[int, dict[int, dict[str, Any]]]:
    """Big map pointer and every key ever set (active or removed)."""
    ptr = get(f"/contracts/{contract}/bigmaps/ledger")["ptr"]
    keys: dict[int, dict[str, Any]] = {}
    while True:
        page = get(f"/bigmaps/{ptr}/keys?limit=10000&offset={len(keys)}&sort.asc=id")
        for k in page:
            keys[int(k["key"])] = k
        if len(page) < 10000:
            return ptr, keys


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    contracts = []
    for name, address in ISSUERS + GENTKS:
        c = get(f"/contracts/{address}")
        contracts.append({"name": name, "address": address,
                          "deployed": c["firstActivityTime"][:10],
                          "transactions": c["numTransactions"],
                          "tokens": c["tokensCount"] if name.startswith("GENTK") else None})
    ledgers = {name: ledger(address) for name, address in ISSUERS}
    active = {name: {k for k, v in keys.items() if v["active"]}
              for name, (_, keys) in ledgers.items()}
    projects: dict[int, dict[str, Any]] = {}
    for name, _ in ISSUERS:  # oldest first: a later registry overrides
        for k in active[name]:
            projects[k] = {"registry": name,
                           "author": ledgers[name][1][k]["value"]["author"]}
    v2_ptr, v2_keys = ledgers["Issuer v2"]
    burned = []
    for k in sorted(active["Issuer v1"] - active["Issuer v2"]):
        if k not in v2_keys:
            continue  # never copied to v2: stays counted in v1
        removal = [u for u in get(f"/bigmaps/{v2_ptr}/keys/{k}/updates")
                   if u["action"] == "remove_key"][-1]
        ops = get(f"/operations/transactions?level={removal['level']}"
                  f"&target={ISSUERS[1][1]}&entrypoint=burn&select=parameter,sender")
        mine = [o for o in ops if str(o["parameter"]["value"]) == str(k)]
        sender = mine[0]["sender"]["address"] if mine else None
        author = ledgers["Issuer v1"][1][k]["value"]["author"]
        burned.append({"id": k, "removed_at": removal["timestamp"], "burn_sender": sender,
                       "by_author": sender == author})
        projects.pop(k)
    per_registry = {name: sum(1 for p in projects.values() if p["registry"] == name)
                    for name, _ in ISSUERS}
    for c in contracts:
        c["projects"] = per_registry.get(c["name"])
    out = {"fetched_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
           "contracts": contracts, "projects": projects, "v1_burned_in_v2": burned}
    with gzip.open(sys.argv[1], "wt") as f:
        json.dump(out, f)
    for c in contracts:
        print(f"{c['name']}\t{c['address'][:10]}\t{c['deployed']}\t{c['transactions']}\t"
              f"projects={c['projects']}\ttokens={c['tokens']}")
    copies = len(active["Issuer v1"] & active["Issuer v2"])
    print(f"projects counted once: {len(projects)}; v1 ids active in v2: {copies}; "
          f"v1 ids burned in v2: {len(burned)} ({sum(b['by_author'] for b in burned)} by author)")
    print(f"tokens: {sum(c['tokens'] or 0 for c in contracts)}")


if __name__ == "__main__":
    main()
