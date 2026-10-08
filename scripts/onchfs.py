#!/usr/bin/env python3
"""Read ONCHFS (fxhash's on-chain file system) from the chain, verify it, rebuild hashes.

    uv run --with pycryptodome --with hpack --with eth-abi python scripts/onchfs.py \
        dump-tezos CACHE
    ... extract CACHE ROOTS.txt OUTDIR [BASE=url] [ETHEREUM=url]
    ... rebuild DIR...

`dump-tezos` saves the Tezos inode and chunk big maps (TzKT) as CACHE/tezos-{inodes,chunks}.json.
`extract` reads each `<chain>:<hex root>` line of ROOTS.txt (chain: tezos, base, ethereum),
checks every inode against its hash, and writes the files, decompressed, to
OUTDIR/<chain>/<root>/; OUTDIR/extract.jsonl gets one line per root: files, bytes, and
"ok" or the first error. `rebuild` prints the ONCHFS root hash of plain folders.

Hashes (onchfs npm 0.1.0): file = keccak(01 ‖ keccak(content) ‖ keccak(metadata)), or with
keccak(chunk hashes) for content ("cheap"); directory = keccak(00 ‖ for each name in
reverse sorted order: cid ‖ keccak(name)). Content is gzip (zlib level 6) when smaller;
metadata is the HPACK block of content-type and content-encoding.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any

from Crypto.Hash import keccak as _keccak

TZKT = "https://api.tzkt.io/v1"
TEZOS_INODES, TEZOS_CHUNKS = 549145, 549144  # KT1Ae7dT…6LUo inodes, KT1JySSN…YAq1 store
EVM_FS = {"ethereum": "0x9e0f2864c6f125bbf599df6ca6e6c3774c5b2e04",
          "base": "0x2983008f292a43f208bba0275afd7e9b3d39af3b"}
HPACK_TABLE_SIZE_UPDATE = bytes.fromhex("3fe101")  # emitted by python-hpack, not by hpack.js


def keccak(b: bytes) -> bytes:
    h = _keccak.new(digest_bits=256)
    h.update(b)
    return h.digest()


INODES_SEL = keccak(b"inodes(bytes32)")[:4].hex()


def http(url: str, body: bytes | None = None, timeout: float = 300) -> bytes:
    req = urllib.request.Request(url, body, {"content-type": "application/json",
                                             "user-agent": "fxhash-preservation/1.0"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data: bytes = r.read()
                return data
        except (urllib.error.HTTPError, OSError):
            if attempt == 5:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError


def file_cid(content: bytes, metadata: bytes, chunk_hashes: list[bytes] | None = None) -> str:
    body = keccak(b"".join(chunk_hashes)) if chunk_hashes is not None else keccak(content)
    return keccak(b"\x01" + body + keccak(metadata)).hex()


def dir_cid(entries: dict[str, str]) -> str:
    acc = b"".join(bytes.fromhex(entries[n]) + keccak(n.encode())
                   for n in sorted(entries, reverse=True))
    return keccak(b"\x00" + acc).hex()


class Tezos:
    def __init__(self, cache: str) -> None:
        with open(f"{cache}/tezos-inodes.json") as f:
            self.inodes = {r["key"]: r["value"] for r in json.load(f)}
        with open(f"{cache}/tezos-chunks.json") as f:
            self.chunks = {r["key"]: bytes.fromhex(r["value"]) for r in json.load(f)}

    def inode(self, cid: str) -> dict[str, Any]:
        v = self.inodes.get(cid)
        if v is None:
            raise KeyError(f"inode {cid} not on chain")
        if "directory" in v:
            return {"dir": v["directory"]}
        f = v["file"]
        content = b"".join(self.chunks[c] for c in f["chunk_pointers"])
        return {"content": content, "metadata": bytes.fromhex(f["metadata"]),
                "chunks": [bytes.fromhex(c) for c in f["chunk_pointers"]]}


class Evm:
    """ONCHFS on an EVM chain, read with views. File content is read chunk by chunk (each
    chunk is an SSTORE2 contract of the content store): readFile() runs out of the nodes'
    50M gas cap on large files."""

    def __init__(self, chain: str, url: str) -> None:
        self.url, self.fs = url, EVM_FS[chain]
        self.store = "0x" + self.call(self.fs, "6bdd12cf", "")[-20:].hex()  # CONTENT_STORE()

    def rpc(self, method: str, params: list[Any]) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                           "params": params}).encode()
        res = json.loads(http(self.url, body))
        if "error" in res:
            raise RuntimeError(f"{method} {str(params)[:120]}: {res['error']}")
        return res["result"]

    def call(self, to: str, sel: str, arg: str) -> bytes:
        return bytes.fromhex(self.rpc("eth_call", [{"to": to, "data": "0x" + sel + arg},
                                                   "latest"])[2:])

    def chunk(self, h: bytes) -> bytes:
        ptr = "0x" + self.call(self.store, "4641dce6", h.hex())[-20:].hex()  # getPointer
        data = bytes.fromhex(self.rpc("eth_getCode", [ptr, "latest"])[2:])[1:]  # STOP byte
        if keccak(data) != h:
            raise ValueError(f"chunk {h.hex()}: hash mismatch")
        return data

    def inode(self, cid: str) -> dict[str, Any]:
        from eth_abi import decode
        if not int.from_bytes(self.call(self.fs, "31c11f6b", cid), "big"):  # inodeExists
            raise KeyError(f"inode {cid} not on chain")
        kind, (meta, chunks), (names, cids) = decode(
            ["uint8", "(bytes,bytes32[])", "(string[],bytes32[])"],
            self.call(self.fs, INODES_SEL, cid))
        if kind == 0:  # enum InodeType { Directory, File }
            return {"dir": {n: c.hex() for n, c in zip(names, cids)}}
        content = b"".join(self.chunk(h) for h in chunks)
        return {"content": content, "metadata": meta, "chunks": list(chunks)}


def extract(src: Tezos | Evm, root: str, out: Path) -> dict[str, int]:
    stats = {"files": 0, "bytes": 0}

    def walk(cid: str, path: Path) -> None:
        node = src.inode(cid)
        if "dir" in node:
            if dir_cid(node["dir"]) != cid:
                raise ValueError(f"directory {path}: hash mismatch")
            path.mkdir(parents=True, exist_ok=True)
            for name, child in node["dir"].items():
                if "/" in name or name in ("", ".", ".."):
                    raise ValueError(f"unsafe name {name!r} in {path}")
                walk(child, path / urllib.parse.unquote(name))
            return
        content, meta = node["content"], node["metadata"]
        if cid not in (file_cid(content, meta), file_cid(content, meta, node["chunks"])):
            raise ValueError(f"file {path}: hash mismatch")
        headers = decode_metadata(meta)
        data = gzip.decompress(content) if headers.get("content-encoding") == "gzip" \
            else content
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        stats["files"] += 1
        stats["bytes"] += len(data)

    walk(root, out)
    return stats


def decode_metadata(meta: bytes) -> dict[str, str]:
    from hpack import Decoder
    return {k: v for k, v in Decoder().decode(meta)}


def encode_metadata(headers: list[tuple[str, str]]) -> bytes:
    from hpack import Encoder
    from hpack.table import HeaderTable
    static: dict[str, int] = {}
    for i, (n, _) in enumerate(HeaderTable.STATIC_TABLE, 1):
        static.setdefault(n.decode(), i)
    # hpack.js order (onchfs encodeMetadata): unknown names first, then by static index desc
    headers = sorted(headers, key=lambda h: (h[0] in static, -static.get(h[0], 0)))
    e = Encoder()
    e.header_table_size = 256
    return e.encode(headers, huffman=True).removeprefix(HPACK_TABLE_SIZE_UPDATE)


MIME_EXTRA = {"vert": "text/plain"}
# extension -> type as npm mime-types 2.1.35 over mime-db 1.52.0 computes it (what the
# onchfs uploader used); Python's mimetypes differs (e.g. .js is text/javascript there)
with open(Path(__file__).parent.parent / "data" / "onchfs-mime-types.json") as _f:
    MIME_TYPES: dict[str, str] = json.load(_f)


def mime_lookup(name: str, extra: bool = True) -> str | None:
    """mime-types `lookup(name)`: extname("x." + name), lowercased, without the dot; then
    onchfs' own additions (`extra`: absent from earlier uploaders, e.g. 3 Tezos projects)."""
    ext = ("x." + name).rsplit(".", 1)[-1].lower()
    return MIME_TYPES.get(ext) or (MIME_EXTRA.get(name.rsplit(".", 1)[-1]) if extra else None)


# (strategy, chunk size) by uploader: Tezos hashes the whole content ("consistent", 16 KiB
# chunks); EVM hashes the chunk hashes ("cheap") of 24,000-byte chunks (SSTORE2 contracts)
VARIANTS = [("consistent", 16384), ("cheap", 24000), ("cheap", 16384)]


def rebuild_file(path: Path, extra: bool = True, strategy: str = "consistent",
                 chunk: int = 16384) -> str:
    raw = path.read_bytes()
    headers = []
    mime = mime_lookup(path.name, extra)
    if mime:
        headers.append(("content-type", mime))
    co = zlib.compressobj(6, zlib.DEFLATED, 31, 8)
    gz = co.compress(raw) + co.flush()
    content = raw
    if len(gz) < len(raw):
        content = gz
        headers.append(("content-encoding", "gzip"))
    chunks = None
    if strategy == "cheap":
        chunks = [keccak(content[i:i + chunk]) for i in range(0, len(content), chunk)]
    return file_cid(content, encode_metadata(headers), chunks)


def rebuild(path: Path, extra: bool = True, strategy: str = "consistent",
            chunk: int = 16384) -> str:
    if path.is_file():
        return rebuild_file(path, extra, strategy, chunk)
    return dir_cid({urllib.parse.quote(p.name, safe="!'()*-._~"):
                    rebuild(p, extra, strategy, chunk) for p in path.iterdir()})


def rebuilds(path: Path) -> set[str]:
    """Root hashes under each uploader version: with and without onchfs' MIME additions,
    times each (strategy, chunk size) in VARIANTS."""
    return {rebuild(path, extra, s, c) for extra in (True, False) for s, c in VARIANTS}


def main() -> None:
    a = sys.argv[1:]
    if a[:1] == ["dump-tezos"] and len(a) == 2:
        os.makedirs(a[1], exist_ok=True)
        for name, ptr in (("inodes", TEZOS_INODES), ("chunks", TEZOS_CHUNKS)):
            rows: list[Any] = []
            while True:
                page = json.loads(http(f"{TZKT}/bigmaps/{ptr}/keys?limit=1000&sort.asc=id"
                                       f"&offset={len(rows)}&select=key,value,active"))
                rows += page
                if len(page) < 1000:
                    break
            with open(f"{a[1]}/tezos-{name}.json", "w") as f:
                json.dump(rows, f)
            print(name, len(rows))
    elif a[:1] == ["extract"] and len(a) >= 4:
        cache, roots_path, outdir = a[1:4]
        rpcs = dict(x.split("=", 1) for x in a[4:])
        tz = Tezos(cache)
        srcs: dict[str, Tezos | Evm] = {"tezos": tz}
        for chain, url in rpcs.items():
            srcs[chain.lower()] = Evm(chain.lower(), url)
        with open(roots_path) as f:
            roots = [line.strip().split(":", 1) for line in f if line.strip()]
        Path(outdir).mkdir(parents=True, exist_ok=True)
        log = Path(outdir) / "extract.jsonl"
        done = set()
        if log.exists():
            done = {(d["chain"], d["root"]) for d in map(json.loads, log.open())
                    if d["status"] == "ok"}  # failed roots are tried again
        with log.open("a") as out:
            for chain, root in roots:
                if (chain, root) in done:
                    continue
                row: dict[str, Any] = {"chain": chain, "root": root}
                try:
                    row.update(extract(srcs[chain], root, Path(outdir) / chain / root))
                    row["status"] = "ok"
                except (KeyError, ValueError, RuntimeError, OSError) as e:
                    row["status"] = f"{type(e).__name__}: {e}"[:300]
                out.write(json.dumps(row) + "\n")
                out.flush()
                print(chain, root[:12], row["status"], row.get("files", ""), flush=True)
    elif a[:1] == ["rebuild"] and len(a) >= 2:
        for p in a[1:]:
            print(" ".join(sorted(rebuilds(Path(p)))), p)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
