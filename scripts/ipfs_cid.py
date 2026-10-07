#!/usr/bin/env python3
"""Recompute a folder's IPFS CID with UnixFS mode 0o644 on every subdirectory node.

    python scripts/ipfs_cid.py FOLDER

Builds the DAG with kubo in a throwaway repo (`ipfs add -r -H`, CIDv0 defaults), then
rewrites each non-root directory node to carry UnixFS field 7 (mode) = 0o644, as some
fxhash uploads did, and recomputes hashes and Tsizes bottom-up. Prints both root CIDs.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def unb58(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    return n.to_bytes(34, "big")


def varint(n: int) -> bytes:
    out = b""
    while True:
        x = n & 0x7F
        n >>= 7
        out += bytes([x | (0x80 if n else 0)])
        if not n:
            return out


def read_varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return r, i


def fields(b: bytes):  # type: ignore[no-untyped-def]
    i = 0
    while i < len(b):
        k, i = read_varint(b, i)
        f, t = k >> 3, k & 7
        if t == 2:
            n, i = read_varint(b, i)
            yield f, b[i:i + n]
            i += n
        elif t == 0:
            v, i = read_varint(b, i)
            yield f, v
        else:
            raise ValueError(t)


def ld(f: int, data: bytes) -> bytes:
    return varint(f << 3 | 2) + varint(len(data)) + data


def main() -> None:
    folder = sys.argv[1]
    repo = tempfile.mkdtemp()
    env = {**os.environ, "IPFS_PATH": repo}
    try:
        subprocess.run(["ipfs", "init", "-e", "--profile=test"], env=env, check=True,
                       capture_output=True)
        root = subprocess.run(["ipfs", "add", "-r", "-H", "-Q", "--pin=false", folder],
                              env=env, check=True, capture_output=True,
                              text=True).stdout.strip()

        def block(c: str) -> bytes:
            return subprocess.run(["ipfs", "block", "get", c], env=env, check=True,
                                  capture_output=True).stdout

        def node(b: bytes) -> tuple[list[tuple[bytes, str, int]], dict[int, int]]:
            links, unixfs = [], {}
            for f, v in fields(b):
                if f == 2:
                    d = dict(fields(v))
                    links.append((d[1], d.get(2, b"").decode(), d.get(3, 0)))
                elif f == 1:
                    unixfs = {k: x for k, x in fields(v) if isinstance(x, int)}
            return links, unixfs

        def rebuild(c: str, is_root: bool) -> tuple[bytes, int]:
            """(multihash, cumulative size) of the rewritten node."""
            b = block(c)
            links, unixfs = node(b)
            if unixfs.get(1) != 1:  # not a directory: unchanged
                return unb58(c), len(b) + sum(t for _, _, t in links)
            new_links = []
            for h, name, tsize in links:
                child = b58(h)
                if node(block(child))[1].get(1) == 1:
                    h, tsize = rebuild(child, False)
                new_links.append((h, name, tsize))
            data = varint(1 << 3) + varint(1)  # Type = Directory
            if not is_root:
                data += varint(7 << 3) + varint(0o644)
            body = b"".join(ld(2, ld(1, h) + ld(2, n.encode()) + varint(3 << 3) + varint(t))
                            for h, n, t in new_links) + ld(1, data)
            mh = b"\x12\x20" + hashlib.sha256(body).digest()
            return mh, len(body) + sum(t for _, _, t in new_links)

        mh, _ = rebuild(root, True)
        print("plain", root)
        print("mode ", b58(mh))
    finally:
        shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    main()
