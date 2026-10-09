"""Tests for folder -> bare repo conversion."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from swhprep._convert import (
    EmbeddedGitAbort,
    Identity,
    convert_all,
    convert_one,
    plan,
    safe_name,
)
from swhprep._git import git
from tests.conftest import make_tree


def ls_tree(repo: Path) -> dict[str, str]:
    out = git("ls-tree", "-r", "main", git_dir=repo)
    return {line.split("\t")[1]: line.split()[0] for line in out.splitlines()}


def test_identity_parse() -> None:
    i = Identity.parse("A B <a@b.c>", "2020-01-01T01:00:00+01:00")
    assert (i.name, i.email, i.date) == ("A B", "a@b.c", "@1577836800 +0100")
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


def nest_repo(src: Path) -> None:
    inner = src / "sub" / "inner"
    make_tree(inner, {"z": "z"})
    git("init", "-q", str(inner))
    git("-C", str(inner), "add", "z")
    git("-C", str(inner), "-c", "user.name=a", "-c", "user.email=b", "commit", "-qm", "i")


def test_embedded_git_refused(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "1"})
    nest_repo(src)
    r = convert_one(src, tmp_path / "p.git", ident)
    assert r.status == "error" and "sub/inner" in (r.error or "")
    assert r.embedded_git == ["sub/inner"]
    assert not (tmp_path / "p.git").exists() and not (tmp_path / "p.git.tmp").exists()
    # dry run only reports
    assert convert_one(src, tmp_path / "p.git", ident, dry_run=True).status == "planned"


def test_embedded_git_aborts_whole_run(tmp_path: Path, ident: Identity) -> None:
    make_tree(tmp_path / "src", {"a/x": "1", "b/x": "1", "c/x": "1"})
    nest_repo(tmp_path / "src" / "b")
    with pytest.raises(EmbeddedGitAbort) as exc:
        convert_all(tmp_path / "src", tmp_path / "out", 1, ident)
    assert exc.value.folders == {str(tmp_path / "src" / "b"): ["sub/inner"]}
    assert not (tmp_path / "out").exists()  # nothing written, not even the clean folders
    res = convert_all(tmp_path / "src", tmp_path / "out", 1, ident, dry_run=True)
    assert ["embedded_git" in r.issues for r in res] == [False, True, False]


def test_flatten_nested_stores_plain_files(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "1"})
    nest_repo(src)
    inner = src / "sub" / "inner"
    (inner / "z").write_text("dirty")  # worktree content wins over the nested repo's HEAD
    (inner / "x.sh").write_text("#!/bin/sh\n")
    (inner / "x.sh").chmod(0o755)
    (inner / ".gitignore").write_text("*.log\n")
    (inner / "kept.log").write_text("l")
    os.symlink("z", inner / "lnk")
    nest_repo(inner)  # a repo inside the flattened one is flattened too
    r = convert_one(src, tmp_path / "p.git", ident, flatten=frozenset({"sub/inner"}))
    assert r.status == "created" and r.issues == ["nested_git_flattened"], r
    t = ls_tree(tmp_path / "p.git")
    assert t == {"a": "100644", "sub/inner/z": "100644", "sub/inner/x.sh": "100755",
                 "sub/inner/.gitignore": "100644", "sub/inner/kept.log": "100644",
                 "sub/inner/lnk": "120000", "sub/inner/sub/inner/z": "100644"}
    assert git("show", "main:sub/inner/z", git_dir=tmp_path / "p.git") == "dirty"
    assert git("cat-file", "-p", "main:sub/inner/lnk", git_dir=tmp_path / "p.git") == "z"


def test_flatten_nested_in_mirrored_source(tmp_path: Path, ident: Identity) -> None:
    src = make_tree(tmp_path / "p", {"a": "1"})
    git("init", "-q", str(src))
    git("-C", str(src), "add", "a")
    git("-C", str(src), "-c", "user.name=a", "-c", "user.email=b", "commit", "-qm", "i")
    nest_repo(src)
    r = convert_one(src, tmp_path / "p.git", ident, flatten=frozenset({"sub/inner"}))
    assert r.status == "mirrored" and "dirty_worktree" in r.issues, r
    out = git("ls-tree", "-r", "--format=%(objectmode) %(path)", "swh-worktree-snapshot",
              git_dir=tmp_path / "p.git")
    assert out.splitlines() == ["100644 a", "100644 sub/inner/z"]


def test_flatten_nested_validation(tmp_path: Path, ident: Identity) -> None:
    make_tree(tmp_path / "src", {"a/x": "1", "b/x": "1", "c/x": "1"})
    nest_repo(tmp_path / "src" / "b")
    nest_repo(tmp_path / "src" / "c")
    with pytest.raises(EmbeddedGitAbort) as exc:  # c is not listed: still aborts
        convert_all(tmp_path / "src", tmp_path / "out", 1, ident,
                    flatten_nested=["b/sub/inner"])
    assert list(exc.value.folders) == [str(tmp_path / "src" / "c")]
    with pytest.raises(ValueError, match="not a nested git repo: b/sub"):
        convert_all(tmp_path / "src", tmp_path / "out", 1, ident, flatten_nested=["b/sub"])
    assert not (tmp_path / "out").exists()
    res = convert_all(tmp_path / "src", tmp_path / "out", 1, ident,
                      flatten_nested=["b/sub/inner", "c/sub/inner"])
    assert [r.status for r in res] == ["created"] * 3
    assert [r.issues for r in res] == [[], ["nested_git_flattened"], ["nested_git_flattened"]]


def test_dangling_gitfile_is_plain_dir(tmp_path: Path, ident: Identity) -> None:
    # submodule copied without its parent: .git file points at a missing modules dir
    src = make_tree(tmp_path / "p", {"a": "1", "shdr/s.glsl": "s",
                                     "shdr/.git": "gitdir: ../.git/modules/shdr\n"})
    r = convert_one(src, tmp_path / "p.git", ident)
    assert r.status == "created" and r.issues == ["dangling_gitfile_dropped"], r
    assert set(ls_tree(tmp_path / "p.git")) == {"a", "shdr/s.glsl"}
    # a gitfile pointing at a real git dir is still a nested repo
    real = tmp_path / "real"
    git("init", "-q", "--bare", str(real))
    (src / "shdr" / ".git").write_text(f"gitdir: {real}\n")
    assert convert_one(src, tmp_path / "q.git", ident).status == "error"


def test_own_git_without_commits(tmp_path: Path, ident: Identity) -> None:
    own = make_tree(tmp_path / "q", {"a": "1"})
    git("init", "-q", str(own))  # no commits: falls back to a synthetic commit
    r2 = convert_one(own, tmp_path / "q.git", ident)
    assert {"source_is_git_repo", "source_git_unborn"} <= set(r2.issues)
    assert r2.status == "created" and set(ls_tree(tmp_path / "q.git")) == {"a"}


def test_epoch_zero_date(tmp_path: Path) -> None:
    ident = Identity.parse("Benoit Baudry <benoit.baudry@umontreal.ca>",
                           "1970-01-01T00:00:00+00:00")
    src = make_tree(tmp_path / "p", {"a": "1"})
    r = convert_one(src, tmp_path / "p.git", ident)
    assert r.status == "created", r.error
    assert git("log", "-1", "--format=%ad|%cd", "--date=raw", "main",
               git_dir=tmp_path / "p.git") == "0 +0000|0 +0000"
    git("fsck", "--strict", git_dir=tmp_path / "p.git")


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
    r = convert_one(src, tmp_path / "p.git", ident, ignore_filemode=True)
    assert ls_tree(tmp_path / "p.git") == {"a": "100644"}
    assert r.issues == ["all_executable"]
    (src / "b").write_text("2")  # boundary: one non-executable file clears it
    assert convert_one(src, tmp_path / "q.git", ident).issues == []


def test_identity_map_per_folder(tmp_path: Path) -> None:
    make_tree(tmp_path / "src", {"a/x": "1", "b/x": "1"})
    ids = {"a": Identity.make("DooDamonnn::", "tz1abc@fxhash.xyz", "2022-05-01T10:00:00+00:00"),
           "b": Identity.make("A & B", "KT1xyz@fxhash.xyz", "2023-01-02T03:04:05+02:00")}
    res = convert_all(tmp_path / "src", tmp_path / "out", 1, None, identities=ids)
    assert [r.status for r in res] == ["created", "created"]
    fmt = ["log", "-1", "--format=%an|%ae|%ad|%cn|%ce|%cd", "--date=raw", "main"]
    # trailing "::" kept: `git commit-tree` would have trimmed it
    assert git(*fmt, git_dir=tmp_path / "out" / "a.git") == (
        "DooDamonnn::|tz1abc@fxhash.xyz|1651399200 +0000|"
        "DooDamonnn::|tz1abc@fxhash.xyz|1651399200 +0000")
    assert git(*fmt, git_dir=tmp_path / "out" / "b.git") == (
        "A & B|KT1xyz@fxhash.xyz|1672621445 +0200|A & B|KT1xyz@fxhash.xyz|1672621445 +0200")
    for r in ["a", "b"]:
        git("fsck", "--strict", "--no-progress", git_dir=tmp_path / "out" / f"{r}.git")


def test_identity_map_missing_folder_writes_nothing(tmp_path: Path, ident: Identity) -> None:
    make_tree(tmp_path / "src", {"a/x": "1", "b/x": "1"})
    with pytest.raises(ValueError, match="1 folder.*missing.*b"):
        convert_all(tmp_path / "src", tmp_path / "out", 1, ident, identities={"a": ident})
    assert not (tmp_path / "out").exists()


def test_identity_make_rejects_what_git_cannot_store() -> None:
    for bad in ["", "  ", "<Draw>", "a\nb"]:
        with pytest.raises(ValueError):
            Identity.make(bad, "x@fxhash.xyz", "2020-01-01T00:00:00+00:00")
    with pytest.raises(ValueError):
        Identity.make("ok", "a b@x", "2020-01-01T00:00:00+00:00")
    assert Identity.make(" michael ", "x@y", "2020-01-01T00:00:00+00:00").name == "michael"


def test_cli_identity_map(tmp_path: Path) -> None:
    import json

    from swhprep.cli import main
    src = make_tree(tmp_path / "src", {"p/a": "1"})
    m = tmp_path / "ids.json"
    m.write_text(json.dumps({"p": {"name": "Udit Mahajan", "email": "tz1iw@fxhash.xyz",
                                   "date": "2024-03-30T00:30:45+00:00"}}))
    out = tmp_path / "out"
    assert main(["convert", str(src), str(out), "--identity-map", str(m)]) == 0
    assert git("log", "-1", "--format=%an %cd", "--date=raw", "main", git_dir=out / "p.git") == (
        "Udit Mahajan 1711758645 +0000")
    assert main(["convert", str(src), str(out)]) == 2  # no identity at all
    assert main(["convert", str(src), str(out), "--author", "A <a@b>"]) == 2  # no --date
    m.write_text(json.dumps({"p": {"name": "x"}}))
    assert main(["convert", str(src), str(tmp_path / "o2"), "--identity-map", str(m)]) == 2


def test_cli_flags_all_executable(tmp_path: Path) -> None:
    from swhprep.cli import main
    src = make_tree(tmp_path / "src", {"p/a": "1"})
    (src / "p" / "a").chmod(0o777)
    common = [str(src), str(tmp_path / "out"), "--author", "T <t@e.org>",
              "--date", "2020-01-01T00:00:00+00:00", "--dry-run"]
    assert main(["convert", *common]) == 1
    assert main(["convert", *common, "--ignore-filemode"]) == 0


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


def test_files_stored_as_on_disk_despite_gitattributes(tmp_path: Path, ident: Identity) -> None:
    # a source's own `* text=auto` must not turn its CRLF files into LF in the tree
    src = make_tree(tmp_path / "p", {".gitattributes": "* text=auto\n", "a.txt": "x\r\ny\r\n"})
    convert_one(src, tmp_path / "p.git", ident)
    blob = subprocess.run(["git", "--git-dir", str(tmp_path / "p.git"), "cat-file", "-p",
                           "main:a.txt"], capture_output=True, check=True).stdout
    assert blob == b"x\r\ny\r\n"
    assert not (tmp_path / "p.git" / "info" / "attributes").exists()
