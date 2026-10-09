#!/usr/bin/env python3
"""Estimate the bytes behind a set of IPFS CIDs from a random sample (stdlib only).

    python3 ipfs_size_sample.py CIDS.tsv.gz FIELD N OUT.jsonl [GATEWAY] [--seed S]

CIDS from fxhash_link_inventory.py ("<cid>\t<field>\t<count>"). Takes N random CIDs of
FIELD, fetches only each root block from a trustless gateway (`?format=raw`, default
https://ipfs.filebase.io), checks it hashes to the CID (sha2-256), and reads the file size:
the block length for a raw leaf, else the UnixFS `filesize` of the dag-pb root (a directory
is reported as such, without a size). Prints the mean, a 95% interval for the mean, and
mean x the field's distinct CID count.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def varint(b: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        x = b[i]
        i += 1
        n |= (x & 0x7F) << shift
        shift += 7
        if not x & 0x80:
            return n, i


def fields(b: bytes) -> list[tuple[int, Any]]:
    """Top-level protobuf fields: (number, int or bytes)."""
    out, i = [], 0
    while i < len(b):
        key, i = varint(b, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = varint(b, i)
        elif wt == 2:
            ln, i = varint(b, i)
            v, i = b[i:i + ln], i + ln
        else:
            raise ValueError(f"wire type {wt}")
        out.append((num, v))
    return out


def parse_cid(cid: str) -> tuple[int, bytes]:
    """(codec, sha2-256 digest): CIDv0 Qm… (dag-pb) or CIDv1 base32 b…."""
    if cid.startswith("Qm"):
        mh = b58decode(cid)
        return 0x70, mh[2:]
    raw = base64.b32decode(cid[1:].upper() + "=" * (-len(cid[1:]) % 8))
    _, i = varint(raw, 0)  # version
    codec, i = varint(raw, i)
    code, i = varint(raw, i)
    ln, i = varint(raw, i)
    if code != 0x12:
        raise ValueError("not sha2-256")
    return codec, raw[i:i + ln]


def size(cid: str, gateway: str) -> dict[str, Any]:
    row: dict[str, Any] = {"cid": cid}
    try:
        codec, digest = parse_cid(cid)
        req = urllib.request.Request(f"{gateway}/ipfs/{cid}?format=raw",
                                     headers={"Accept": "application/vnd.ipld.raw"})
        block = b""
        for attempt in range(8):  # rate limits: back off, so the sample stays random
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    block = r.read()
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 7:
                    raise
                time.sleep(2 ** attempt)
        if hashlib.sha256(block).digest() != digest:
            row["error"] = "hash mismatch"
        elif codec == 0x55:
            row["size"] = len(block)
        else:
            data = next(v for n, v in fields(block) if n == 1)
            u = dict(fields(data))
            if u.get(1) in (1, 5):
                row["dir"] = True
            else:
                row["size"] = u.get(3, len(u.get(2, b"")))
    except Exception as e:  # noqa: BLE001 - recorded per CID
        row["error"] = str(e)[:200]
    return row


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--seed")]
    seed = next((int(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("--seed=")), 1)
    if len(args) not in (4, 5):
        raise SystemExit(__doc__)
    cids_p, field, n, out_p = args[0], args[1], int(args[2]), args[3]
    gateway = args[4] if len(args) > 4 else "https://ipfs.filebase.io"
    with gzip.open(cids_p, "rt") as f:
        pool = [ln.split("\t")[0] for ln in f if ln.split("\t")[1] == field]
    sample = random.Random(seed).sample(pool, min(n, len(pool)))
    with ThreadPoolExecutor(4) as ex, open(out_p, "w") as out:
        rows = list(ex.map(lambda c: size(c, gateway), sample))
        out.writelines(json.dumps(r) + "\n" for r in rows)
    sizes = [r["size"] for r in rows if "size" in r]
    if len(sizes) < 2:
        raise SystemExit(f"{field}: {len(sizes)} sized of {len(sample)}")
    m = sum(sizes) / len(sizes)
    sd = math.sqrt(sum((s - m) ** 2 for s in sizes) / (len(sizes) - 1))
    half = 1.96 * sd / math.sqrt(len(sizes))
    print(json.dumps({"field": field, "distinct": len(pool), "sampled": len(sample),
                      "sized": len(sizes), "dirs": sum("dir" in r for r in rows),
                      "errors": sum("error" in r for r in rows),
                      "mean_bytes": round(m), "mean_95": [round(m - half), round(m + half)],
                      "total_est_bytes": round(m * len(pool)),
                      "total_95": [round((m - half) * len(pool)),
                                   round((m + half) * len(pool))]}))


if __name__ == "__main__":
    main()
