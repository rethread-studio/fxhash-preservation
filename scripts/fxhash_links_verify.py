#!/usr/bin/env python3
"""Join, per project: the code link anchored on chain, the archive's, the API's, and git's.

    python3 scripts/fxhash_links_verify.py ONCHAIN.jsonl DOCS.jsonl.gz API.json CIDALL.jsonl \
        OUT.jsonl

ONCHAIN.jsonl and DOCS.jsonl.gz from fxhash_project_onchain.py, API.json and CIDALL.jsonl
from ipfs_cid_all.py. The chain's code link of a project is:
- Tezos issuer v3: its codex entry (the link itself is in contract storage);
- Tezos issuer v2: `generativeUri` of the metadata document whose CID is in storage;
- EVM with an IPFS base URI in storage: `generatorUri` of token 1's metadata under it;
- EVM otherwise: none, the contract only names an https:// URL ("not anchored").
Writes one line per project and prints counts.
"""

from __future__ import annotations

import collections
import gzip
import json
import re
import sys
from typing import Any


def cid(u: str | None) -> str | None:
    if not u:
        return None
    return re.split(r"[/?#]", u.split("://", 1)[-1])[0] if "://" in u else u


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)
    onchain_p, docs_p, api_p, cidall_p, out_p = sys.argv[1:]
    with (gzip.open(onchain_p, "rt") if onchain_p.endswith(".gz") else open(onchain_p)) as f:
        chain = {(r["chain"], r["id"].lower()): r for r in map(json.loads, f)
                 if r.get("issuer") != "v1"}
    with gzip.open(docs_p, "rt") as f:
        docs = {(d["chain"], d["id"].lower()): d for d in map(json.loads, f)}
    with open(api_p) as f:
        api = {tuple(k.split(":", 1)): v for k, v in json.load(f).items()}
    git: dict[tuple[str, str], dict[str, Any]] = {}
    with open(cidall_p) as f:
        for r in map(json.loads, f):
            pid = r["repo"].rsplit("__", 1)[-1].lower()
            evm = [k for k in chain if k[1] == pid and k[0] != "tezos"]
            git[evm[0] if evm else ("tezos", pid)] = r
    stats: collections.Counter[str] = collections.Counter()
    with open(out_p, "w") as out:
        for key in sorted(set(chain) | set(api), key=str):
            c, d, g = chain.get(key, {}), docs.get(key, {}), git.get(key)
            row: dict[str, Any] = {"chain": key[0], "id": key[1], "active": c.get("active"),
                                   "in_api": key in api}
            meta = {}
            if "text" in d:
                try:
                    meta = json.loads(d["text"])
                except ValueError:
                    pass
            if c.get("codex"):
                row["code_onchain"] = c["codex"][0]
                row["code_how"] = "storage (codex)"
            elif len(c.get("onchain_pointer_data") or "") == 64:
                row["code_onchain"] = "onchfs://" + c["onchain_pointer_data"]
                row["code_how"] = "storage (EVM onchainPointer)"
            elif meta.get("generativeUri") or meta.get("generatorUri"):
                row["code_onchain"] = meta.get("generativeUri") or meta.get("generatorUri")
                row["code_how"] = "metadata document anchored in storage"
            elif not c:
                row["code_how"] = "project not found on chain"
            elif key[0] == "tezos":
                row["code_how"] = "metadata not readable: " + d.get("error", "no document")
            else:
                row["code_how"] = "not anchored (https metadata)"
            # Tezos v3 also has the link in its metadata document; some codex entries hold
            # "ipfs://<64 hex>", which is no CID, while the document has the right one
            doc_code = meta.get("generativeUri") or meta.get("generatorUri")
            if doc_code and doc_code != row.get("code_onchain"):
                row["code_onchain_doc"] = doc_code
            row["code_api"] = api.get(key)
            if g:
                row["repo"] = g["repo"]
                row["code_archive"] = g.get("want")
                row["git_cids"] = sorted(set(g.get("cids", {}).values()))
            ons = {cid(row.get("code_onchain")), cid(row.get("code_onchain_doc"))} - {None}
            if re.fullmatch(r"ipfs://[0-9a-f]{64}", row.get("code_onchain") or ""):
                stats["tezos codex entry malformed (ipfs://<hex>)"] += 1
            on = cid(row.get("code_onchain"))
            scheme = (row.get("code_onchain") or row.get("code_api") or "").split("://")[0]
            row["verdict"] = (
                "no repo" if not g else
                "chain: not anchored" if not on else
                "git reproduces chain" if ons & set(row["git_cids"]) else
                "git differs from chain")
            stats[f"{key[0] if key[0] == 'tezos' else 'evm'} {scheme or '?'}: "
                  f"{row['verdict']}"] += 1
            if g and on and g.get("want"):
                stats["archive == chain" if g["want"] in ons else "archive != chain"] += 1
            if on and row["code_api"]:
                stats["api == chain" if cid(row["code_api"]) in ons else "api != chain"] += 1
            out.write(json.dumps(row) + "\n")
    for k, v in sorted(stats.items()):
        print(v, k)


if __name__ == "__main__":
    main()
