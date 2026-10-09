#!/usr/bin/env python3
"""Inventory of every link in fetched fxhash metadata documents (stdlib only).

    python3 fxhash_link_inventory.py OUT_PREFIX DOCS.jsonl.gz...

Each input line is a document fetched by hash from a chain-anchored URI (project documents
of fxhash_project_onchain.py fetch, token documents of tezos_metadata_fetch.py): {"text":
<JSON>, ...}; lines without "text" are counted as missing. Every string value of the
document that is a URI (ipfs://, onchfs://, https://, http://, ar://, data:) is recorded
under its field path (list indices dropped, e.g. "formats.uri").

Writes OUT_PREFIX.summary.json (per field: links, distinct, per scheme) and
OUT_PREFIX.cids.tsv.gz: one line per distinct IPFS CID, "<cid>\t<field>\t<count>" (field:
the one it appears under most often). Query strings and paths inside a CID are dropped:
the CID is what has to be kept.
"""

from __future__ import annotations

import collections
import gzip
import json
import re
import sys
from typing import Any

SCHEMES = ("ipfs://", "onchfs://", "https://", "http://", "ar://", "data:")


def walk(x: Any, path: str = "") -> Any:
    if isinstance(x, dict):
        for k, v in x.items():
            yield from walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(x, list):
        for v in x:
            yield from walk(v, path)
    elif isinstance(x, str) and x.startswith(SCHEMES):
        yield path, x


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    prefix, paths = sys.argv[1], sys.argv[2:]
    links: collections.Counter[str] = collections.Counter()
    schemes: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    distinct: dict[str, set[str]] = collections.defaultdict(set)
    cids: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    docs = missing = unparsable = 0
    for p in paths:
        with gzip.open(p, "rt") as f:
            for line in f:
                d = json.loads(line)
                if "text" not in d:
                    missing += 1
                    continue
                try:
                    doc = json.loads(d["text"])
                except ValueError:
                    unparsable += 1
                    continue
                docs += 1
                for field, uri in walk(doc):
                    scheme = uri.split(":", 1)[0]
                    links[field] += 1
                    schemes[field][scheme] += 1
                    if scheme == "ipfs":
                        cid = re.split(r"[/?#]", uri[7:])[0]
                        distinct[field].add(cid)
                        cids[cid][field] += 1
                    elif scheme != "data":
                        distinct[field].add(uri)
    summary = {"documents": docs, "missing": missing, "unparsable": unparsable,
               "distinct_ipfs_cids": len(cids),
               "fields": {k: {"links": links[k], "distinct": len(distinct[k]),
                              "schemes": dict(schemes[k])}
                          for k in sorted(links, key=lambda k: -links[k])}}
    with open(prefix + ".summary.json", "w") as f:
        json.dump(summary, f, indent=1)
    with gzip.open(prefix + ".cids.tsv.gz", "wt") as f:
        for cid in sorted(cids):
            field = cids[cid].most_common(1)[0][0]
            f.write(f"{cid}\t{field}\t{sum(cids[cid].values())}\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "fields"}))
    for k, v in summary["fields"].items():
        print(v["links"], v["distinct"], k, v["schemes"])


if __name__ == "__main__":
    main()
