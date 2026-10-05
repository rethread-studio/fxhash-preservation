"""Tests for folder -> bare repo conversion."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from swhprep._convert import Identity, convert_all, convert_one, plan, safe_name
from swhprep._git import git
from tests.conftest import make_tree


def ls_tree(repo: Path) -> dict[str, str]:
    out = git("ls-tree", "-r", "main", git_dir=repo)
    return {line.split("\t")[1]: line.split()[0] for line in out.splitlines()}


def test_identity_parse() -> None:
    i = Identity.parse("A B <a@b.c>", "2020-01-01T01:00:00+01:00")
    assert (i.name, i.email, i.date) == ("A B", "a@b.c", "1577836800 +0100")
    with pytest.raises(ValueError):
        Identity.parse("no email", "2020-01-01T00:00:00+00:00")
    with pytest.raises(ValueError):
        Identity.parse("A <a@b>", "2020-01-01T00:00:00")


def test_basic_tree_and_metadata(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "src" / "p", {"a.txt": "a\n", "d/b.txt": "b\n"})
    (src / "run.sh").write_text("#!/bin/sh\n")
    (src / "run.sh").chmod(0o755)
    os.symlink("a.txt", src / "link")
    r = convert_one(src, tmp_path / "out" / "p.git", ident)
    assert r.status == "created" and r.issues == [], r
    repo = tmp_path / "out" / "p.git"
    assert ls_tree(repo) == {"a.txt": "100644", "d/b.txt": "100644",
                             "run.sh": "100755", "link": "120000"}
    meta = git("log", "-1", "--format=%an|%ae|%ad|%cn|%cd|%s", "--date=raw", "main", git_dir=repo)
    assert meta == ("Test Archive|archive@example.org|1577836800 +0000|"
                    "Test Archive|1577836800 +0000|Import of p")
    assert (repo / "info" / "refs").is_file()  # dumb-HTTP fallback
    assert not list((repo / "objects").glob("??"))  # everything packed
    assert git("symbolic-ref", "HEAD", git_dir=repo) == "refs/heads/main"


def test_reproducible_commit_ids(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "1"})
    a = convert_one(src, tmp_path / "x" / "p.git", ident)
    b = convert_one(src, tmp_path / "y" / "p.git", ident)
    assert a.commit == b.commit and a.commit


def test_gitignored_files_are_kept(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {".gitignore": "*.log\nbuild/\n", "x.log": "l",
                                     "build/o": "o"})
    r = convert_one(src, tmp_path / "p.git", ident)
    assert set(ls_tree(tmp_path / "p.git")) == {".gitignore", "x.log", "build/o"}
    assert r.issues == []


def test_embedded_git_and_own_git_are_flagged(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "1", "sub/inner/z": "z"})
    git("init", "-q", str(src / "sub" / "inner"))
    git("-C", str(src / "sub" / "inner"), "add", "z")
    git("-C", str(src / "sub" / "inner"), "-c", "user.name=a", "-c", "user.email=b",
        "commit", "-qm", "i")
    r = convert_one(src, tmp_path / "p.git", ident)
    assert "embedded_git" in r.issues and r.embedded_git == ["sub/inner"]
    assert ls_tree(tmp_path / "p.git")["sub/inner"] == "160000"  # content lost -> flagged
    assert "count_mismatch" not in r.issues

    own = make_tree(tmp_path / "q", {"a": "1"})
    git("init", "-q", str(own))
    r2 = convert_one(own, tmp_path / "q.git", ident)
    assert "source_is_git_repo" in r2.issues
    assert set(ls_tree(tmp_path / "q.git")) == {"a"}


def test_empty_dirs_and_special_files(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "1"})
    (src / "emptyd").mkdir()
    os.mkfifo(src / "fifo")
    r = convert_one(src, tmp_path / "p.git", ident)
    assert r.status == "created"
    assert set(r.issues) == {"empty_dirs_dropped", "special_files_skipped"}


def test_empty_source_creates_nothing(tmp_path: Path, ident: Identity) -> None:
    (tmp_path / "p").mkdir()
    r = convert_one(tmp_path / "p", tmp_path / "p.git", ident)
    assert r.status == "empty" and not (tmp_path / "p.git").exists()


def test_ignore_filemode(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "1"})
    (src / "a").chmod(0o755)
    convert_one(src, tmp_path / "p.git", ident, ignore_filemode=True)
    assert ls_tree(tmp_path / "p.git") == {"a": "100644"}


def test_source_untouched(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "1"})
    before = sorted(str(p) for p in src.rglob("*"))
    src.chmod(0o555)
    try:
        r = convert_one(src, tmp_path / "p.git", ident)
    finally:
        src.chmod(0o755)
    assert r.status == "created"
    assert sorted(str(p) for p in src.rglob("*")) == before


def test_rerun_skips_and_rebuild_appends(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "1"})
    repo = tmp_path / "p.git"
    first = convert_one(src, repo, ident)
    assert convert_one(src, repo, ident).status == "exists"
    assert convert_one(src, repo, ident, rebuild=True).status == "unchanged"
    (src / "b").write_text("2")
    r = convert_one(src, repo, ident, rebuild=True)
    assert r.status == "updated"
    assert git("rev-parse", "main^", git_dir=repo) == first.commit


def test_interrupted_tmp_is_redone(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "1"})
    (tmp_path / "p.git.tmp").mkdir()  # leftover from a crash
    assert convert_one(src, tmp_path / "p.git", ident).status == "created"
    assert not (tmp_path / "p.git.tmp").exists()


def test_safe_name_and_collisions(tmp_path: Path) -> None:
    assert safe_name("My Project (v2)/é") == "My-Project-v2/_"
    make_tree(tmp_path / "src", {"a b/x": "1", "a-b/x": "1"})
    with pytest.raises(ValueError, match="collision"):
        plan(tmp_path / "src", 1)


def test_depth_and_dry_run(tmp_path: Path, ident: Identity) -> None:
    make_tree(tmp_path / "src", {"g1/p1/a": "1", "g1/p2/a": "1", "g2/p3/a": "1"})
    pairs = plan(tmp_path / "src", 2)
    assert [n for _, n in pairs] == ["g1/p1.git", "g1/p2.git", "g2/p3.git"]
    res = convert_all(tmp_path / "src", tmp_path / "out", 2, ident, dry_run=True)
    assert {r.status for r in res} == {"planned"} and not (tmp_path / "out").exists()
    res = convert_all(tmp_path / "src", tmp_path / "out", 2, ident, jobs=2)
    assert [r.status for r in res] == ["created"] * 3


def test_clone_roundtrip_matches_source(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "x" * 10000, "d/e/f": "deep", "crlf.txt": "a\r\nb\r\n"})
    convert_one(src, tmp_path / "p.git", ident)
    subprocess.run(["git", "clone", "-q", str(tmp_path / "p.git"), str(tmp_path / "c")],
                   check=True, env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})
    for rel in ["a", "d/e/f", "crlf.txt"]:
        assert (tmp_path / "c" / rel).read_bytes() == (src / rel).read_bytes()
