#!/usr/bin/env python3
"""Who announces each CID? Provider census, as in doc-howto-ipfs-independent-copies.md.

    python3 scripts/ipfs_provider_census.py SAMPLE.jsonl OUT.json

SAMPLE.jsonl: one {"kind", "cid", "ref"} per line. For each CID, the providers from the
delegated routing API (DHT + IPNI); for each distinct provider peer, its addresses, DNS
names and the `whois` organisation of its first public IPv4. Writes
{"cids": [{kind, cid, ref, providers: [{id, protocols}]}], "peers": {id: {...}}}
and prints a summary per kind.
"""

from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

ROUTING = "https://delegated-ipfs.dev/routing/v1"


def get(path: str) -> Any:
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"{ROUTING}/{path}", headers={
                "accept": "application/json", "user-agent": "swhprep-census"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            time.sleep(2 ** attempt)
        except (OSError, ValueError):
            time.sleep(2 ** attempt)
    return {"error": "unreachable"}


def whois_org(ip: str) -> str:
    try:
        out = subprocess.run(["whois", ip], capture_output=True, text=True, timeout=30,
                             check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    keys = ("orgname", "org-name", "netname", "descr", "organization")
    vals = [line.split(":", 1)[1].strip() for line in out.splitlines()
            if line.lower().startswith(keys)]
    return " | ".join(dict.fromkeys(vals))[:120]


def peer_info(pid: str) -> dict[str, Any]:
    d = get(f"peers/{pid}")
    addrs = [a for p in (d.get("Peers") or []) for a in (p.get("Addrs") or [])]
    ips = sorted({m.group(1) for a in addrs
                  for m in [re.search(r"/ip4/([\d.]+)/", a)] if m and not
                  m.group(1).startswith(("10.", "127.", "192.168.", "172.", "100.64."))})
    dns = sorted({m.group(1) for a in addrs for m in [re.search(r"/dns[46]?/([^/]+)/", a)]
                  if m})
    return {"ips": ips, "dns": dns, "org": whois_org(ips[0]) if ips else ""}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    with open(sys.argv[1]) as f:
        sample = [json.loads(line) for line in f]
    peers: dict[str, dict[str, Any]] = {}
    for s in sample:
        d = get(f"providers/{s['cid']}")
        s["providers"] = [{"id": p.get("ID"), "protocols": p.get("Protocols")}
                          for p in (d.get("Providers") or [])]
        if "error" in d:
            s["error"] = d["error"]
        for p in s["providers"]:
            if p["id"] and p["id"] not in peers:
                peers[p["id"]] = peer_info(p["id"])
        n = len({p["id"] for p in s["providers"]})
        print(f"{s['kind']:20} {s['cid'][:16]} {n} peers", flush=True)
    with open(sys.argv[2], "w") as f:
        json.dump({"date": time.strftime("%F"), "routing": ROUTING, "cids": sample,
                   "peers": peers}, f, indent=1)
    print("\nper kind: peers per CID (count of CIDs)")
    for kind in sorted({s["kind"] for s in sample}):
        dist = collections.Counter(len({p["id"] for p in s["providers"]})
                                   for s in sample if s["kind"] == kind)
        print(f"  {kind:20} {dict(sorted(dist.items()))}")
    use = collections.Counter(pid for s in sample for pid in {p["id"] for p in s["providers"]})
    print(f"\n{len(use)} distinct peers; CIDs announced per peer:")
    for pid, n in use.most_common():
        info = peers.get(pid, {})
        print(f"  {n:3}  {pid}  {info.get('dns', [])[:1]} {info.get('ips', [])[:1]} "
              f"{info.get('org', '')[:50]}")
    sets = collections.Counter(frozenset(p["id"] for p in s["providers"]) for s in sample)
    print(f"\n{len(sets)} distinct provider sets; most common covers {sets.most_common(1)[0][1]}"
          f" of {len(sample)} CIDs")


if __name__ == "__main__":
    main()
