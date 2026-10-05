"""Check that every origin the SWH lister will derive from the manifest is clonable."""

from __future__ import annotations

import gzip
import json
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from ._git import GitError, git
from ._manifest import origin_urls


@dataclass
class Check:
    """Outcome of `git ls-remote` against one origin URL."""

    url: str
    ok: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_manifest(location: str) -> dict[str, Any]:
    """Load a manifest from a local path or an http(s) URL, gzipped or not."""
    if location.startswith(("http://", "https://")):
        with urlopen(location, timeout=60) as resp:
            raw: bytes = resp.read()
    else:
        raw = Path(location).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data: dict[str, Any] = json.loads(raw)
    return data


def ls_remote(url: str, timeout: float = 120) -> Check:
    """Same validity test as swh.lister.save_bulk: listing remote refs must succeed."""
    try:
        refs = git("ls-remote", url, timeout=timeout)
    except (GitError, OSError, subprocess.TimeoutExpired) as e:
        return Check(url, False, f"{type(e).__name__}: {e}")
    if not refs:
        return Check(url, False, "no refs")
    return Check(url, True)


def check_all(
    manifest: dict[str, Any],
    forge_url: str,
    *,
    jobs: int = 8,
    on_result: Callable[[Check], None] | None = None,
) -> list[Check]:
    """ls-remote every origin URL derived from `manifest` and `forge_url`."""
    results: list[Check] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for c in pool.map(ls_remote, origin_urls(manifest, forge_url)):
            results.append(c)
            if on_result:
                on_result(c)
    return results
