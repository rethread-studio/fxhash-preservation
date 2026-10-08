#!/usr/bin/env python3
"""Source folders for the fxhash projects whose code is in ONCHFS, laid out like the archive.

    python3 scripts/onchfs_sources.py ONCHAIN.jsonl.gz DOCS.jsonl.gz META.json.gz \
        EXTRACTED OUT REPORT.jsonl

The code root of each project comes from the chain: Tezos issuer v3 codex entry, EVM
onchainPointer, or the generativeUri of the version document a versioned EVM contract
stores. Projects listed by the API (META) whose root the chain gives and EXTRACTED holds
(onchfs.py extract) become OUT/<platform>/<year>/<name>__<id>/ with the files and a
_source.txt like the archive's (generative_uri: onchfs://<root>). REPORT gets one line per
ONCHFS project: chain root, API root, and the outcome.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


def folder_name(name: str, pid: str) -> str:
    """The archive's rule: Windows-unsafe characters to _, spaces collapsed, ends trimmed."""
    n = re.sub(r"\s+", " ", re.sub(r'[:\\/*|?"<>]', "_", name)).strip(" .")
    return f"{n}__{pid}"


def main() -> None:
    if len(sys.argv) != 7:
        raise SystemExit(__doc__)
    onchain_p, docs_p, meta_p, extracted, out, report_p = sys.argv[1:]
    with gzip.open(onchain_p, "rt") as f:
        chain = {(r["chain"], r["id"].lower()): r for r in map(json.loads, f)
                 if r.get("issuer") != "v1"}
    with gzip.open(docs_p, "rt") as f:
        docs = {(d["chain"], d["id"].lower()): d for d in map(json.loads, f) if "text" in d}
    with gzip.open(meta_p, "rt") as f:
        meta = json.load(f)
    api_uri = {}
    with open("data/fxhash-api-generative-uri.json") as f:
        for k, v in json.load(f).items():
            api_uri[tuple(k.split(":", 1))] = v
    with open(f"{extracted}/extract.jsonl") as f:
        ok = {(d["chain"], d["root"]) for d in map(json.loads, f) if d["status"] == "ok"}
    n_made = 0
    with open(report_p, "w") as rep:
        for t in meta["tokens"]:
            key = (t["chain"].lower(), t["id"].lower())
            api = api_uri.get(key, "")
            c = chain.get(key, {})
            # candidate roots from the chain, in order; the first one extracted is used
            # (Tezos 29231: its codex root is not in ONCHFS, its anchored document's is)
            cands: list[tuple[str, str]] = []
            if c.get("codex") and c["codex"][0].startswith("onchfs://"):
                cands.append((c["codex"][0][9:], "tezos codex"))
            if len(c.get("onchain_pointer_data") or "") == 64:
                cands.append((c["onchain_pointer_data"], "evm onchainPointer"))
            if key in docs:
                m = json.loads(docs[key]["text"])
                # a project document has generativeUri, a token's (folder versions) generatorUri
                g = m.get("generativeUri") or m.get("generatorUri") or ""
                if g.startswith("onchfs://") and c:
                    how = "evm versionInfo document" if c.get("version_uri") else \
                        "metadata document anchored in storage"
                    cands.append((g[9:].split("/")[0], how))
            store = "tezos" if key[0] == "tezos" else key[0]
            root, how = next(((r, h) for r, h in cands if (store, r) in ok),
                             cands[0] if cands else (None, None))
            if not root and not api.startswith("onchfs://"):
                continue  # an IPFS project
            row: dict[str, Any] = {"chain": key[0], "id": t["id"], "name": t["name"],
                                   "root_chain": root, "how": how,
                                   "root_api": api[9:].split("/")[0] or None}
            if len({r for r, _ in cands}) > 1:
                row["roots_on_chain"] = [r for r, _ in cands]
            if not root:
                row["outcome"] = "no root on chain"
            elif (store, root) not in ok:
                row["outcome"] = "root not extracted"
            else:
                plat = "fxhash-1.0-tezos" if key[0] == "tezos" else "fxhash-2.0-evm"
                year = (t.get("mint_opens_at") or t["created_at"])[:4]
                dest = Path(out) / plat / year / folder_name(t["name"], t["id"])
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(Path(extracted) / store / root, dest)
                (dest / "_source.txt").write_text(
                    f"id: {t['id']}\nname: {t['name']}\n"
                    f"mint_opens_at: {t.get('mint_opens_at') or t['created_at']}\n"
                    f"generative_uri: onchfs://{root}\n")
                row["folder"] = str(dest.relative_to(out))
                row["outcome"] = "made"
                n_made += 1
            rep.write(json.dumps(row) + "\n")
    print(f"{n_made} folders made")


if __name__ == "__main__":
    main()
