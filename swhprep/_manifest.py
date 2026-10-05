"""Write a grokmirror manifest (the format the SWH `grokmirror` lister consumes)."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ._git import git


def find_repos(toplevel: Path) -> Iterator[Path]:
    """Yield bare repos (dirs named *.git with a HEAD) under `toplevel`, sorted."""
    for root, dirs, _files in os.walk(toplevel):
        dirs.sort()
        rootp = Path(root)
        if rootp.name.endswith(".git") and (rootp / "HEAD").is_file():
            dirs[:] = []
            yield rootp


def repo_entry(repo: Path) -> dict[str, Any] | None:
    """Manifest entry as grokmirror's get_repo_defs() builds it; None for a repo with no refs."""
    refs = git("show-ref", git_dir=repo, check=False)
    if not refs:
        return None
    last = git("for-each-ref", "--sort=-committerdate",
               "--format=%(committerdate:iso-strict)", "--count=1", git_dir=repo)
    entry: dict[str, Any] = {
        "modified": int(datetime.fromisoformat(last).timestamp()),
        # grokmirror: sha1 of `git show-ref` output including the final newline
        "fingerprint": hashlib.sha1(refs.encode() + b"\n").hexdigest(),
        "head": (repo / "HEAD").read_text().strip(),
    }
    desc_file = repo / "description"
    if desc_file.is_file():
        desc = desc_file.read_text(errors="replace").strip()
        if desc and "edit this file" not in desc:
            entry["description"] = desc
    return entry


def build_manifest(toplevel: Path, prefix: str = "/") -> dict[str, dict[str, Any]]:
    """Map '<prefix><path relative to toplevel>.git' to its entry, for every non-empty repo.

    `prefix` is the URL path where `toplevel` is served: the lister resolves keys with
    urljoin, so a leading '/' is relative to the host root, not to the forge URL.
    """
    prefix = "/" + prefix.strip("/") + "/" if prefix.strip("/") else "/"
    manifest: dict[str, dict[str, Any]] = {}
    for repo in find_repos(toplevel):
        entry = repo_entry(repo)
        if entry is not None:
            manifest[prefix + repo.relative_to(toplevel).as_posix()] = entry
    return manifest


def write_manifest(manifest: dict[str, dict[str, Any]], out: Path) -> None:
    """Write gzipped JSON atomically; mtime=0 keeps the gzip bytes reproducible."""
    data = json.dumps(manifest, indent=2, sort_keys=True).encode()
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "wb") as fh, gzip.GzipFile(filename="", fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(data)
    tmp.replace(out)


def origin_urls(manifest: dict[str, Any], forge_url: str) -> list[str]:
    """Origin URLs exactly as swh.lister.grokmirror computes them: urljoin(forge_url, key)."""
    return [urljoin(forge_url, key) for key in manifest]
