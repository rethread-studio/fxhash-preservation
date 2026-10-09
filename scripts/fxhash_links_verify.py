#!/usr/bin/env python3
"""Join, per project: the code link anchored on chain, the archive's, the API's, and git's.

    python3 scripts/fxhash_links_verify.py ONCHAIN.jsonl DOCS.jsonl.gz API.json CIDALL.jsonl \
        OUT.jsonl [--onchfs ONCHFS.jsonl]... [--causes UNMATCHED.jsonl]

ONCHAIN.jsonl and DOCS.jsonl.gz from fxhash_project_onchain.py, API.json and CIDALL.jsonl
from ipfs_cid_all.py, ONCHFS.jsonl from onchfs_repos.py, UNMATCHED.jsonl from
ipfs_cid_diff.py. The chain's code link of a project is:
- Tezos issuer v3: its codex entry (the link itself is in contract storage);
- Tezos issuer v2: `generativeUri` of the metadata document whose CID is in storage;
- EVM onchainPointer: the ONCHFS root in the SSTORE2 contract it points to;
- EVM versioned contract: `generativeUri` (or `generatorUri`) of its versionInfo document;
- EVM with an IPFS base URI in storage: `generatorUri` of token 1's metadata under it;
- EVM otherwise: none, the contract only names https:// URLs (unverifiable on chain).
Each project gets a verdict: git reproduces the chain's hash, or a named gap. Writes one
line per project, prints counts, and exits 1 if any verdict is "unexplained".
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import re
from typing import Any

UNVERIFIABLE = "unverifiable on chain (contract names only https URLs)"


def cid(u: str | None) -> str | None:
    if not u:
        return None
    return re.split(r"[/?#]", u.split("://", 1)[-1])[0] if "://" in u else u


def chain_link(key: tuple[str, str], c: dict[str, Any], d: dict[str, Any],
               meta: dict[str, Any]) -> tuple[str | None, str]:
    """(code link, how the chain gives it) — no link means the second item names the gap."""
    doc_code = meta.get("generativeUri") or meta.get("generatorUri")
    if c.get("codex"):
        return c["codex"][0], "storage (codex)"
    if len(c.get("onchain_pointer_data") or "") == 64:
        return "onchfs://" + c["onchain_pointer_data"], "storage (EVM onchainPointer)"
    if not c:
        return None, "project not found on chain"
    if key[0] == "tezos":
        how = "metadata document anchored in storage"
    elif (c.get("version_uri") or "").startswith("ipfs://"):
        # (the 3 Ethereum MediaFactory contracts answer versionInfo with something else)
        how = "EVM versionInfo document"
    elif c.get("base_uri"):
        how = "EVM token metadata under baseURI"
    else:
        return None, UNVERIFIABLE
    if doc_code:
        return doc_code, how
    return None, f"{how} not readable: {d.get('error') or 'no code link in it'}"


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("onchain")
    ap.add_argument("docs")
    ap.add_argument("api")
    ap.add_argument("cidall")
    ap.add_argument("out")
    ap.add_argument("--onchfs", action="append", default=[],
                    help="onchfs_repos.py output (repeatable)")
    ap.add_argument("--causes", help="ipfs_cid_diff.py output for the unmatched repos")
    ap.add_argument("--versions", help="fxhash_evm_versions.py output")
    a = ap.parse_args()
    with (gzip.open(a.onchain, "rt") if a.onchain.endswith(".gz") else open(a.onchain)) as f:
        chain = {(r["chain"], r["id"].lower()): r for r in map(json.loads, f)
                 if r.get("issuer") != "v1"}
    with gzip.open(a.docs, "rt") as f:
        docs = {(d["chain"], d["id"].lower()): d for d in map(json.loads, f)}
    with open(a.api) as f:
        api = {tuple(k.split(":", 1)): v for k, v in json.load(f).items()}
    onchfs: dict[str, dict[str, Any]] = {}
    for p in a.onchfs:
        with open(p) as f:
            onchfs.update({r["repo"].removesuffix(".git"): r for r in map(json.loads, f)})
    causes: dict[str, str] = {}
    if a.causes:
        with open(a.causes) as f:
            causes = {r["repo"]: r["cause"] for r in map(json.loads, f)}
    versions: dict[tuple[str, str], list[str | None]] = {}
    if a.versions:
        with open(a.versions) as f:
            for r in map(json.loads, f):
                versions[(r["chain"], r["id"].lower())] = [cid(v["code"]) for v in r["versions"]]
    git: dict[tuple[str, str], dict[str, Any]] = {}
    with open(a.cidall) as f:
        for r in map(json.loads, f):
            pid = r["repo"].rsplit("__", 1)[-1].lower()
            evm = [k for k in chain if k[1] == pid and k[0] != "tezos"]
            git[evm[0] if evm else ("tezos", pid)] = r
    stats: collections.Counter[str] = collections.Counter()
    verdicts: collections.Counter[str] = collections.Counter()
    with open(a.out, "w") as out:
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
            link, how = chain_link(key, c, d, meta)
            row["code_how"] = how
            if link:
                row["code_onchain"] = link
            # Tezos v3 also has the link in its metadata document; some codex entries hold
            # "ipfs://<64 hex>", which is no CID, while the document has the right one
            doc_code = meta.get("generativeUri") or meta.get("generatorUri")
            if doc_code and doc_code != link:
                row["code_onchain_doc"] = doc_code
            row["code_api"] = api.get(key)
            ons = {cid(link), cid(row.get("code_onchain_doc"))} - {None}
            if re.fullmatch(r"ipfs://[0-9a-f]{64}", link or ""):
                stats["tezos codex entry malformed (ipfs://<hex>)"] += 1
            if g:
                row["repo"] = g["repo"]
                row["code_archive"] = g.get("want")
            if not g:
                row["verdict"] = (
                    "no repo: burned (removed from the issuer ledger)"
                    if key[0] == "tezos" and c and not c.get("active") else
                    "no repo: " + how if not link else
                    "no repo: missing from the archive")
            elif not link:
                row["verdict"] = "chain: " + how
            elif g.get("onchfs"):
                o = onchfs.get(g["repo"], {})
                row["git_cids"] = [o["want"]] if o.get("match") else []
                row["verdict"] = (
                    "git reproduces chain" if o.get("match") and o["want"] in ons else
                    "unexplained: no ONCHFS rebuild result" if not o else
                    "unexplained: ONCHFS rebuild differs from chain")
            else:
                row["git_cids"] = sorted(set(g.get("cids", {}).values()))
                cause = causes.get(g["repo"])
                row["verdict"] = (
                    "git reproduces chain" if ons & set(row["git_cids"]) else
                    "git reproduces chain with the uploaded .git folder (README recipe)"
                    if cause == "upload has a .git folder" else
                    f"git differs from chain: {cause} (accepted)"
                    if cause in ("mixed folder modes", "empty folder(s)") else
                    f"unexplained: git differs from chain{': ' + cause if cause else ''}")
            # an updated EVM project: every version's code is anchored; git holds one
            vcodes = versions.get(key)
            if vcodes and g:
                held = set(row.get("git_cids", []))
                row["versions_not_in_git"] = len({v for v in vcodes if v not in held})
                k = [i + 1 for i, v in enumerate(vcodes) if v in held]
                if row["verdict"].startswith("unexplained") and k:
                    row["verdict"] = (f"git reproduces chain version {k[-1]} of {len(vcodes)}"
                                      " (current version's code not in git)")
                if row["versions_not_in_git"]:
                    stats["updated EVM projects with code versions not in git"] += 1
                    stats["code versions on chain not in git"] += row["versions_not_in_git"]
            scheme = (link or row.get("code_api") or "").split("://")[0] or "?"
            verdicts[row["verdict"]] += 1
            stats[f"{key[0] if key[0] == 'tezos' else 'evm'} {scheme}: {row['verdict']}"] += 1
            if g and link and g.get("want"):
                stats["archive == chain" if cid(g["want"]) in ons else "archive != chain"] += 1
            if link and row["code_api"]:
                stats["api == chain" if cid(row["code_api"]) in ons else "api != chain"] += 1
            out.write(json.dumps(row) + "\n")
    for k, v in sorted(stats.items()):
        print(v, k)
    print()
    for k, v in verdicts.most_common():
        print(v, k)
    if any(k.startswith("unexplained") for k in verdicts):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
