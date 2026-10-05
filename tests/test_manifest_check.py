"""Tests for manifest generation and origin checking (over real smart HTTP)."""

from __future__ import annotations

import gzip
import json
import shutil
from collections.abc import Callable
from pathlib import Path

from swhprep._check import check_all, load_manifest
from swhprep._convert import Identity, convert_all
from swhprep._manifest import build_manifest, origin_urls, write_manifest
from swhprep.cli import main
from tests.conftest import make_tree

Serve = Callable[..., str]


def _forge(tmp_path: Path, ident: Identity) -> Path:
    make_tree(tmp_path / "src", {"g/p1/a": "1", "g/p2/b": "2"})
    convert_all(tmp_path / "src", tmp_path / "srv", 2, ident)
    return tmp_path / "srv"


def test_manifest_entries(tmp_path: Path, ident: Identity) -> None:
    srv = _forge(tmp_path, ident)
    (srv / "broken.git.tmp").mkdir()  # in-progress conversions are not listed
    m = build_manifest(srv)
    assert sorted(m) == ["/g/p1.git", "/g/p2.git"]
    e = m["/g/p1.git"]
    assert e["modified"] == 1577836800 and e["head"] == "ref: refs/heads/main"
    assert e["description"] == "p1" and len(e["fingerprint"]) == 40


def test_manifest_written_gzip_reproducible(tmp_path: Path, ident: Identity) -> None:
    srv = _forge(tmp_path, ident)
    write_manifest(build_manifest(srv), tmp_path / "m1.js.gz")
    write_manifest(build_manifest(srv), tmp_path / "m2.js.gz")
    assert (tmp_path / "m1.js.gz").read_bytes() == (tmp_path / "m2.js.gz").read_bytes()
    assert sorted(json.loads(gzip.decompress((tmp_path / "m1.js.gz").read_bytes()))) == [
        "/g/p1.git", "/g/p2.git"]


def test_prefix_and_urljoin(tmp_path: Path, ident: Identity) -> None:
    srv = _forge(tmp_path, ident)
    root = build_manifest(srv)
    sub = build_manifest(srv, prefix="/git")
    assert origin_urls(root, "https://h.org/") == ["https://h.org/g/p1.git",
                                                   "https://h.org/g/p2.git"]
    # pitfall: a root-relative key ignores the forge URL's path
    assert origin_urls(root, "https://h.org/git/")[0] == "https://h.org/g/p1.git"
    assert origin_urls(sub, "https://h.org/git/")[0] == "https://h.org/git/g/p1.git"


def test_check_positive(tmp_path: Path, ident: Identity, git_http: Serve) -> None:
    srv = _forge(tmp_path, ident)
    url = git_http(srv)
    res = check_all(build_manifest(srv), url)
    assert [c.ok for c in res] == [True, True], res


def test_check_negative_missing_repo(tmp_path: Path, ident: Identity, git_http: Serve) -> None:
    srv = _forge(tmp_path, ident)
    manifest = build_manifest(srv)
    shutil.rmtree(srv / "g" / "p2.git")
    res = check_all(manifest, git_http(srv))
    assert [c.ok for c in res] == [True, False]


def test_check_boundary_subpath(tmp_path: Path, ident: Identity, git_http: Serve) -> None:
    srv = _forge(tmp_path, ident)
    url = git_http(srv, prefix="/git/")
    assert not any(c.ok for c in check_all(build_manifest(srv), url))  # wrong keys
    assert all(c.ok for c in check_all(build_manifest(srv, prefix="/git/"), url))


def test_cli_end_to_end(tmp_path: Path, git_http: Serve, capsys: object) -> None:
    make_tree(tmp_path / "src", {"p1/a": "1", "p2/b": "2"})
    srv = tmp_path / "srv"
    common = ["--author", "T <t@e.org>", "--date", "2020-01-01T00:00:00+00:00"]
    assert main(["convert", str(tmp_path / "src"), str(srv), *common,
                 "--report", str(tmp_path / "r.jsonl")]) == 0
    assert len((tmp_path / "r.jsonl").read_text().splitlines()) == 2
    assert main(["manifest", str(srv), str(srv / "manifest.js.gz")]) == 0
    assert sorted(load_manifest(str(srv / "manifest.js.gz"))) == ["/p1.git", "/p2.git"]
    url = git_http(srv)
    assert main(["--json", "check", str(srv / "manifest.js.gz"), "--forge-url", url]) == 0
    shutil.rmtree(srv / "p1.git")
    assert main(["check", str(srv / "manifest.js.gz"), "--forge-url", url]) == 1
    assert main(["convert", str(tmp_path / "src"), str(srv), "--author", "bad",
                 "--date", "2020-01-01T00:00:00+00:00"]) == 2
