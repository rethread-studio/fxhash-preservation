"""Tests for sources that already are git repos: history is mirrored, not flattened."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from swhprep._convert import SNAPSHOT_BRANCH, Identity, convert_one
from swhprep._git import GitError, git
from swhprep._manifest import build_manifest
from tests.conftest import make_tree

DEV = ["-c", "user.name=Dev", "-c", "user.email=dev@x.org"]


def commit(repo: Path, msg: str, date: str) -> None:
    git("-C", str(repo), "add", "-A")
    git("-C", str(repo), *DEV, "commit", "-qm", msg,
        env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})


def make_repo(path: Path) -> Path:
    make_tree(path, {"a.txt": "v1\n", ".gitignore": "*.o\n"})
    git("init", "-q", "-b", "master", str(path))
    commit(path, "first", "2015-03-01T00:00:00+00:00")
    git("-C", str(path), "tag", "v1")
    git("-C", str(path), "checkout", "-q", "-b", "feature")
    (path / "f.txt").write_text("feat\n")
    commit(path, "feature", "2016-03-01T00:00:00+00:00")
    git("-C", str(path), "checkout", "-q", "master")
    (path / "a.txt").write_text("v2\n")
    commit(path, "second", "2017-03-01T00:00:00+00:00")
    return path


def refs(repo: Path) -> dict[str, str]:
    out = git("show-ref", git_dir=repo)
    return {line.split()[1]: line.split()[0] for line in out.splitlines()}


def test_history_and_refs_preserved(tmp_path: Path, ident: Identity) -> None:
    src = make_repo(tmp_path / "p")
    r = convert_one(src, tmp_path / "p.git", ident)
    assert r.status == "mirrored", r.error
    assert "dirty_worktree" not in r.issues and "source_is_git_repo" in r.issues
    repo = tmp_path / "p.git"
    src_refs = {k: v for k, v in
                (line.split()[::-1] for line in git("-C", str(src), "show-ref").splitlines())}
    assert refs(repo) == src_refs  # same commit ids: history untouched
    assert git("symbolic-ref", "HEAD", git_dir=repo) == "refs/heads/master"
    assert git("log", "--format=%s", "master", git_dir=repo).splitlines() == ["second", "first"]
    assert not git("remote", git_dir=repo)  # no pointer back to the drive
    assert (repo / "info" / "refs").is_file()
    assert build_manifest(tmp_path)["/p.git"]["modified"] == 1488326400  # real latest date


def test_dirty_worktree_snapshot(tmp_path: Path, ident: Identity) -> None:
    src = make_repo(tmp_path / "p")
    (src / "a.txt").write_text("uncommitted\n")
    (src / "new.txt").write_text("untracked\n")
    (src / "build.o").write_text("ignored by the repo\n")
    r = convert_one(src, tmp_path / "p.git", ident)
    assert "dirty_worktree" in r.issues
    repo = tmp_path / "p.git"
    snap = f"refs/heads/{SNAPSHOT_BRANCH}"
    assert git("rev-parse", f"{snap}^", git_dir=repo) == git("rev-parse", "master", git_dir=repo)
    files = set(git("ls-tree", "-r", "--name-only", snap, git_dir=repo).splitlines())
    assert files == {".gitignore", "a.txt", "new.txt"}
    assert git("show", f"{snap}:a.txt", git_dir=repo) == "uncommitted"
    assert git("log", "-1", "--format=%an", snap, git_dir=repo) == "Test Archive"
    # source repo untouched: still dirty, no new refs
    assert "a.txt" in git("-C", str(src), "status", "--porcelain")
    assert SNAPSHOT_BRANCH not in git("-C", str(src), "branch")


def test_rerun_and_rebuild(tmp_path: Path, ident: Identity) -> None:
    src = make_repo(tmp_path / "p")
    repo = tmp_path / "p.git"
    convert_one(src, repo, ident)
    assert convert_one(src, repo, ident).status == "exists"
    assert convert_one(src, repo, ident, rebuild=True).status == "unchanged"
    (src / "a.txt").write_text("v3\n")
    commit(src, "third", "2018-03-01T00:00:00+00:00")
    assert convert_one(src, repo, ident, rebuild=True).status == "updated"
    assert git("log", "-1", "--format=%s", "master", git_dir=repo) == "third"
    assert not (tmp_path / "p.git.old").exists() and not (tmp_path / "p.git.tmp").exists()


def test_detached_head_and_shallow(tmp_path: Path, ident: Identity) -> None:
    src = make_repo(tmp_path / "p")
    git("-C", str(src), "checkout", "-q", "v1")
    r = convert_one(src, tmp_path / "p.git", ident)
    assert r.status == "mirrored", r.error
    assert r.commit == git("-C", str(src), "rev-parse", "v1^{commit}")

    shallow = tmp_path / "s"
    git("clone", "-q", "--depth", "1", f"file://{src}", str(shallow))
    r2 = convert_one(shallow, tmp_path / "s.git", ident)
    assert r2.status == "mirrored", r2.error
    assert "shallow_history" in r2.issues


def test_head_to_missing_branch(tmp_path: Path, ident: Identity) -> None:
    src = make_repo(tmp_path / "p")
    git("-C", str(src), "symbolic-ref", "HEAD", "refs/heads/gone")
    r = convert_one(src, tmp_path / "p.git", ident)
    assert r.status == "mirrored", r.error
    assert "head_repointed" in r.issues
    assert git("symbolic-ref", "HEAD", git_dir=tmp_path / "p.git") == "refs/heads/feature"


def test_broken_repo_reports_error(tmp_path: Path, ident: Identity) -> None:
    src = make_repo(tmp_path / "p")
    for obj in (src / ".git" / "objects").rglob("*"):
        if obj.is_file() and obj.parent.name not in ("info", "pack"):
            obj.chmod(0o644)
            obj.write_bytes(b"corrupt")
    r = convert_one(src, tmp_path / "p.git", ident)
    assert r.status == "error"
    assert not (tmp_path / "p.git").exists() and not (tmp_path / "p.git.tmp").exists()


def test_head_only_clean(tmp_path: Path, ident: Identity) -> None:
    src = make_repo(tmp_path / "p")
    master = git("-C", str(src), "rev-parse", "master")
    feature = git("-C", str(src), "rev-parse", "feature")
    git("-C", str(src), "update-ref", "refs/remotes/origin/master", feature)
    r = convert_one(src, tmp_path / "p.git", ident, head_only=True)
    assert r.status == "mirrored", r.error
    assert "refs_dropped" in r.issues and "dirty_worktree" not in r.issues
    repo = tmp_path / "p.git"
    assert refs(repo) == {"refs/heads/master": master}  # tags, branches, remotes gone
    assert git("symbolic-ref", "HEAD", git_dir=repo) == "refs/heads/master"
    assert r.commit == master
    # objects only reachable from dropped refs are not published
    with pytest.raises(GitError):
        git("cat-file", "-e", feature, git_dir=repo)
    assert git("rev-list", "--count", "master", git_dir=repo) == "2"


def test_head_only_dirty_commits_on_branch(tmp_path: Path, ident: Identity) -> None:
    src = make_repo(tmp_path / "p")
    master = git("-C", str(src), "rev-parse", "master")
    (src / "a.txt").write_text("v3 uncommitted\n")
    (src / "new.txt").write_text("untracked\n")
    (src / "x.o").write_text("ignored by the repo's .gitignore\n")
    r = convert_one(src, tmp_path / "p.git", ident, head_only=True)
    assert r.status == "mirrored", r.error
    assert "dirty_worktree" in r.issues
    repo = tmp_path / "p.git"
    assert list(refs(repo)) == ["refs/heads/master"]
    tip = refs(repo)["refs/heads/master"]
    assert r.commit == tip and git("rev-parse", "master^", git_dir=repo) == master
    assert git("log", "-1", "--format=%an|%s", "master", git_dir=repo) == (
        "Test Archive|Uncommitted working tree of p")
    assert git("show", "master:a.txt", git_dir=repo) == "v3 uncommitted"
    names = git("ls-tree", "--name-only", "master", git_dir=repo).split("\n")
    assert "new.txt" in names and "x.o" not in names
    # rerun is stable: same tip, nothing changed
    again = convert_one(src, repo, ident, head_only=True, rebuild=True)
    assert again.status == "unchanged" and again.commit == tip


def test_head_only_snapshot_uses_given_identity(tmp_path: Path) -> None:
    src = make_repo(tmp_path / "p")
    (src / "a.txt").write_text("dirty\n")
    who = Identity.make("Artist.", "tz1q@fxhash.xyz", "2023-06-01T12:00:00+00:00")
    r = convert_one(src, tmp_path / "p.git", who, head_only=True)
    assert r.status == "mirrored", r.error
    assert git("log", "-1", "--format=%an|%ae|%cn|%cd", "--date=raw", "master",
               git_dir=tmp_path / "p.git") == "Artist.|tz1q@fxhash.xyz|Artist.|1685620800 +0000"
    # the real history is untouched
    assert git("log", "-1", "--format=%an", "master^", git_dir=tmp_path / "p.git") == "Dev"


def test_head_only_detached(tmp_path: Path, ident: Identity) -> None:
    src = make_repo(tmp_path / "p")
    first = git("-C", str(src), "rev-parse", "v1")
    git("-C", str(src), "checkout", "-q", "--detach", "v1")
    r = convert_one(src, tmp_path / "p.git", ident, head_only=True)
    assert r.status == "mirrored", r.error
    assert refs(tmp_path / "p.git") == {"refs/heads/main": first}


def test_empty_dirs_turned_files_in_gitdir(tmp_path: Path, ident: Identity) -> None:
    # seen on the fxhash drive: a copy turned empty .git subdirs into 0-byte files
    src = make_repo(tmp_path / "p")
    git("-C", str(src), "pack-refs", "--all")
    git("-C", str(src), "repack", "-adq")
    want = refs(src / ".git")
    gd = src / ".git"
    for d in ["refs/tags", "objects/info"]:
        for f in (gd / d).iterdir():
            f.unlink()
        (gd / d).rmdir()
        (gd / d).write_bytes(b"")
    before = sorted((str(p), p.stat().st_mtime_ns) for p in gd.rglob("*"))
    # negative control: git itself can't serve it
    with pytest.raises(GitError, match="not our ref 0000"):
        git("clone", "-q", "--mirror", "--no-local", str(src), str(tmp_path / "raw.git"))
    r = convert_one(src, tmp_path / "p.git", ident)
    assert r.status == "mirrored", r.error
    assert "gitdir_repaired" in r.issues
    assert refs(tmp_path / "p.git") == want
    assert sorted((str(p), p.stat().st_mtime_ns) for p in gd.rglob("*")) == before
    assert not (tmp_path / "p.git.src").exists()
    git("fsck", "--strict", git_dir=tmp_path / "p.git")


def test_snapshot_keeps_crlf_despite_gitattributes(tmp_path: Path, ident: Identity) -> None:
    src = make_repo(tmp_path / "p")
    (src / ".gitattributes").write_text("* text=auto\n")
    commit(src, "attributes", "2018-03-01T00:00:00+00:00")
    (src / "a.txt").write_bytes(b"v2\r\n")  # a Windows checkout of the same content
    r = convert_one(src, tmp_path / "p.git", ident, head_only=True)
    assert "dirty_worktree" in r.issues
    blob = subprocess.run(["git", "--git-dir", str(tmp_path / "p.git"), "cat-file", "blob",
                           "master:a.txt"], capture_output=True, check=True).stdout
    assert blob == b"v2\r\n"
