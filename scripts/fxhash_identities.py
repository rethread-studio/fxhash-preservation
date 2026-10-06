#!/usr/bin/env python3
"""Build the `swhprep convert --identity-map` file for the fxhash archive.

    python scripts/fxhash_identities.py SRC data/fxhash-meta.json.gz OUT.json

SRC is `fxhash-archive/` (`<platform>/<year>/<project>`). Each project's fxhash id is the
`id:` line of its `_source.txt`, or the `__<id>` suffix of the folder name. Per project:
- date: `created_at` (issuer creation on Tezos, contract deployment on EVM);
- name: the artist's on-chain Tezos name, else their fxhash account username, else the
  address (EVM: username first, it is the only name there); collaborations: the members'
  names joined with " & ";
- email: `<author address>@fxhash.xyz` (the collab contract for collaborations).
Exits 1 if any folder has no id or no metadata.
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

EMAIL_DOMAIN = "fxhash.xyz"
CHAINS = {"fxhash-1.0-tezos": ("TEZOS",), "fxhash-2.0-evm": ("BASE", "ETHEREUM")}


def clean(name: str | None) -> str:
    """A name git can store verbatim: no <, > (look-alikes instead), no line breaks."""
    if not name:
        return ""
    for a, b in (("<", "‹"), (">", "›"), ("\n", " "), ("\r", " "), ("\0", "")):
        name = name.replace(a, b)
    return name.strip()


def pick(*names: str | None) -> str:
    return next((c for n in names if (c := clean(n))), "")


def person(rec: dict[str, Any], tezos: bool) -> tuple[str, str]:
    """(name, which source) for an author or collab member record."""
    addr = rec.get("author") or rec.get("id") or ""
    order = (("onchain", rec.get("onchain_name")), ("username", rec.get("username")))
    for source, n in (order if tezos else order[::-1]):
        if c := clean(n):
            return c, source
    return addr, "address"


def project_id(folder: Path) -> str | None:
    src = folder / "_source.txt"
    if src.is_file():
        for line in src.read_text(errors="replace").splitlines():
            if line.startswith("id:"):
                return line.split(":", 1)[1].strip()
    if "__" in folder.name:
        return folder.name.rsplit("__", 1)[1]
    return None


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    src, meta_path, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    with gzip.open(meta_path) as f:
        meta = json.load(f)
    by_key = {(t["chain"], t["id"].lower()): t for t in meta["tokens"]}
    stats: Counter[str] = Counter()
    problems: list[str] = []
    identities: dict[str, dict[str, str]] = {}
    for folder in sorted(p for p in src.glob("*/*/*") if p.is_dir()):
        rel = folder.relative_to(src).as_posix()
        chains = CHAINS.get(rel.split("/", 1)[0])
        pid = project_id(folder)
        if not chains or not pid:
            problems.append(f"{rel}: no fxhash id")
            continue
        t = next((by_key[(c, pid.lower())] for c in chains if (c, pid.lower()) in by_key), None)
        if t is None:
            problems.append(f"{rel}: id {pid} not in {meta_path}")
            continue
        tezos = t["chain"] == "TEZOS"
        if t["collaborators"]:
            names = [person(c, tezos) for c in t["collaborators"]]
            name = " & ".join(n for n, _ in names)
            stats["collaboration"] += 1
            stats.update(f"member:{s}" for _, s in names)
        else:
            name, source = person(t, tezos)
            stats[f"author:{source}"] += 1
        if not name or not t.get("created_at"):
            problems.append(f"{rel}: id {pid} has no name or no created_at")
            continue
        identities[rel] = {"name": name, "email": f"{t['author']}@{EMAIL_DOMAIN}",
                           "date": t["created_at"]}
        stats[f"chain:{t['chain']}"] += 1
    with open(out, "w") as f:
        json.dump(identities, f, ensure_ascii=False, indent=1, sort_keys=True)
    for k, v in sorted(stats.items()):
        print(f"{k:<20} {v}")
    print(f"{len(identities)} identities -> {out}")
    for p in problems:
        print(f"❌ {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
