"""Validate swhprep's manifest against the two independent references.

1. grokmirror's own `grok-manifest` on the same repos: same keys and per-repo fields.
2. swh.lister's GrokmirrorLister fetching our manifest over HTTP: the origins it lists
   are exactly the URLs `swhprep check` tests.

Needs an env with `swh.lister` and `grokmirror` installed:
    uv venv /tmp/swhenv -p 3.12 && VIRTUAL_ENV=/tmp/swhenv uv pip install swh.lister grokmirror
    /tmp/swhenv/bin/python scripts/validate_reference.py
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swh.lister.grokmirror.lister import GrokmirrorLister
from swh.scheduler.model import Lister

from swhprep._convert import Identity, convert_all
from swhprep._manifest import build_manifest, origin_urls, write_manifest


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    try:
        for rel in ["g1/alpha/a.txt", "g1/beta/b.txt", "g2/gamma/c/d.txt"]:
            (tmp / "src" / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp / "src" / rel).write_text(rel)
        srv = tmp / "srv"
        ident = Identity.parse("T <t@e.org>", "2021-06-01T12:00:00+02:00")
        convert_all(tmp / "src", srv, 2, ident)
        ours = build_manifest(srv)
        write_manifest(ours, srv / "manifest.js.gz")

        # 1. grok-manifest
        grok_out = tmp / "grok.js.gz"
        subprocess.run([str(Path(sys.executable).parent / "grok-manifest"), "-m", str(grok_out),
                        "-t", str(srv)], check=True)
        ref = json.loads(gzip.decompress(grok_out.read_bytes()))
        assert sorted(ref) == sorted(ours), (sorted(ref), sorted(ours))
        for key, entry in ours.items():
            for field in ("modified", "fingerprint", "head", "description"):
                assert ref[key].get(field) == entry.get(field), (key, field, ref[key], entry)

        # 2. SWH grokmirror lister over HTTP
        handler = partial(SimpleHTTPRequestHandler, directory=str(srv))
        handler.log_message = lambda *a: None  # type: ignore[attr-defined]
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        forge_url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        sched = MagicMock()
        sched.get_or_create_lister.return_value = Lister(
            name="grokmirror", instance_name="test", id=uuid.uuid4())
        lister = GrokmirrorLister(scheduler=sched, url=forge_url, instance="test")
        listed = [o for page in lister.get_pages() for o in lister.get_origins_from_page(page)]
        httpd.shutdown()
        got = sorted(o.url for o in listed)
        want = sorted(origin_urls(ours, forge_url))
        assert got == want, (got, want)
        assert {o.visit_type for o in listed} == {"git"}
        assert {int(o.last_update.timestamp()) for o in listed} == {1622541600}
        print("reference OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
