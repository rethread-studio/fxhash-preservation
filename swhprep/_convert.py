"""Convert plain source folders into bare git repos with one reproducible commit."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ._git import GitError, git

BRANCH = "main"
# Mirrored repos: uncommitted/untracked files of the source working tree land here.
SNAPSHOT_BRANCH = "swh-worktree-snapshot"
# SWH git loader rejects packfiles above this size (swh.loader.git pack_size_bytes).
SWH_MAX_PACK_BYTES = 4 * 1024**3
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Identity:
    """Author/committer identity and date used for every synthetic commit."""

    name: str
    email: str
    date: str  # git internal format "<epoch> <+hhmm>"

    @classmethod
    def parse(cls, author: str, date: str) -> Identity:
        m = re.fullmatch(r"\s*(.+?)\s*<([^<>]+)>\s*", author)
        if not m:
            raise ValueError(f"author must look like 'Name <email>', got {author!r}")
        dt = datetime.fromisoformat(date)
        if dt.tzinfo is None:
            raise ValueError(f"date must carry a timezone, e.g. 2026-01-01T00:00:00+00:00: {date!r}")
        offset = dt.utcoffset()
        assert offset is not None
        minutes = int(offset.total_seconds()) // 60
        sign = "+" if minutes >= 0 else "-"
        tz = f"{sign}{abs(minutes) // 60:02d}{abs(minutes) % 60:02d}"
        # "@": git rejects a bare "0 +0000" (epoch 0) as an invalid date; same stored value
        return cls(m.group(1), m.group(2), f"@{int(dt.timestamp())} {tz}")

    def env(self) -> dict[str, str]:
        return {
            "GIT_AUTHOR_NAME": self.name,
            "GIT_AUTHOR_EMAIL": self.email,
            "GIT_AUTHOR_DATE": self.date,
            "GIT_COMMITTER_NAME": self.name,
            "GIT_COMMITTER_EMAIL": self.email,
            "GIT_COMMITTER_DATE": self.date,
        }


@dataclass
class Result:
    """Outcome of converting one source folder (one JSONL line in the report)."""

    source: str
    repo: str
    # created | mirrored | updated | unchanged | exists | empty | error | planned
    status: str = "pending"
    commit: str | None = None
    source_entries: int = 0
    tree_entries: int = 0
    pack_bytes: int = 0
    issues: list[str] = field(default_factory=list)
    embedded_git: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def safe_name(rel: str) -> str:
    """Map a relative source path to a URL-safe repo path (without the .git suffix)."""
    parts = [_UNSAFE.sub("-", p).strip("-.") or "_" for p in Path(rel).parts]
    return "/".join(parts)


def find_sources(src: Path, depth: int) -> Iterator[Path]:
    """Yield directories exactly `depth` levels below `src`, sorted, not following symlinks."""
    if depth == 0:
        yield src
        return
    try:
        entries = sorted(os.scandir(src), key=lambda e: e.name)
    except OSError:
        return
    for e in entries:
        if e.is_dir(follow_symlinks=False) and e.name != ".git":
            yield from find_sources(Path(e.path), depth - 1)


def plan(src: Path, depth: int) -> list[tuple[Path, str]]:
    """Pair each source folder with its repo path; fail loudly on name collisions."""
    pairs: list[tuple[Path, str]] = []
    seen: dict[str, Path] = {}
    for s in find_sources(src, depth):
        rel = s.relative_to(src).as_posix()
        name = safe_name(rel) + ".git"
        if name.lower() in seen:
            raise ValueError(f"repo name collision: {s} and {seen[name.lower()]} -> {name}")
        seen[name.lower()] = s
        pairs.append((s, name))
    return pairs


def scan_source(path: Path) -> tuple[int, list[str], list[str]]:
    """Count what git can store: regular files and symlinks outside any `.git` component.

    Returns (entries, embedded_git_dirs, issues).
    """
    entries = 0
    embedded: list[str] = []
    issues: set[str] = set()
    for root, dirs, files in os.walk(path, followlinks=False):
        rootp = Path(root)
        if ".git" in dirs or ".git" in files:
            rel = rootp.relative_to(path).as_posix()
            embedded.append(rel)
            if rootp != path:
                # git stores a nested repo as one gitlink entry, not its files
                dirs[:] = []
                continue
        dirs[:] = [d for d in dirs if d != ".git"]
        files = [f for f in files if f != ".git"]
        # symlinked dirs show up in `dirs` but are stored by git as a symlink entry
        for d in dirs:
            if (rootp / d).is_symlink():
                entries += 1
        dirs[:] = [d for d in dirs if not (rootp / d).is_symlink()]
        for f in files:
            p = rootp / f
            if p.is_symlink() or p.is_file():
                entries += 1
            else:
                issues.add("special_files_skipped")
        if not dirs and not files and rootp != path:
            issues.add("empty_dirs_dropped")
    if "." in embedded:
        issues.add("source_is_git_repo")
    if any(e != "." for e in embedded):
        issues.add("embedded_git")
    return entries, embedded, sorted(issues)


def _pack_bytes(repo: Path) -> int:
    packdir = repo / "objects" / "pack"
    if not packdir.is_dir():
        return 0
    return max((p.stat().st_size for p in packdir.glob("*.pack")), default=0)


def convert_one(
    source: Path,
    repo: Path,
    ident: Identity,
    *,
    ignore_filemode: bool = False,
    rebuild: bool = False,
    dry_run: bool = False,
) -> Result:
    """Create or refresh bare `repo` from `source`.

    A source that is itself a git repo with commits keeps its history (all refs mirrored);
    anything else becomes a single synthetic commit.
    """
    res = Result(source=str(source), repo=str(repo))
    try:
        res.source_entries, res.embedded_git, res.issues = scan_source(source)
        has_history = "source_is_git_repo" in res.issues and _has_commits(source)
        if "source_is_git_repo" in res.issues and not has_history:
            res.issues.append("source_git_unborn")
        if dry_run:
            res.status = "planned"
            return res
        if res.source_entries == 0 and not has_history:
            res.status = "empty"
            return res
        # repos only appear under their final name once complete (built in *.tmp, renamed)
        exists = repo.is_dir()
        if exists and not rebuild:
            res.status = "exists"
            res.commit = git("rev-parse", "-q", "--verify", "HEAD", git_dir=repo, check=False)
            return res
        if has_history:
            _mirror(source, repo, ident, res, ignore_filemode=ignore_filemode)
            return res
        work = repo if exists else repo.with_name(repo.name + ".tmp")
        if not exists:
            shutil.rmtree(work, ignore_errors=True)
            work.parent.mkdir(parents=True, exist_ok=True)
            # empty template: no sample hooks, i.e. ~15 fewer files per repo
            git("init", "-q", "--bare", "--template=", f"--initial-branch={BRANCH}", str(work))
            (work / "description").write_text(f"{source.name}\n")
        _commit_tree(source, work, ident, res, ignore_filemode=ignore_filemode)
        git("repack", "-adq", git_dir=work)
        git("update-server-info", git_dir=work)
        if not exists:
            work.rename(repo)
        res.pack_bytes = _pack_bytes(repo)
        if res.pack_bytes > SWH_MAX_PACK_BYTES:
            res.issues.append("pack_too_large")
        expected = res.source_entries + len([e for e in res.embedded_git if e != "."])
        if res.tree_entries != expected:
            res.issues.append("count_mismatch")
    except (OSError, GitError, ValueError) as e:
        res.status = "error"
        res.error = str(e)
        shutil.rmtree(repo.with_name(repo.name + ".tmp"), ignore_errors=True)
    return res


# The drive's files may belong to another uid (or none, on exFAT): git must still read them.
_SAFE = ["-c", "safe.directory=*"]


def _has_commits(source: Path) -> bool:
    # Any ref counts, even if HEAD or objects are broken: such a repo must fail loudly in
    # _mirror rather than be silently flattened into a synthetic commit.
    # (%(refname) alone doesn't read objects, so broken objects don't hide the ref.)
    return bool(git(*_SAFE, "-C", str(source), "for-each-ref", "--count=1",
                    "--format=%(refname)", check=False))


def _mirror(
    source: Path, repo: Path, ident: Identity, res: Result, *, ignore_filemode: bool
) -> None:
    """Mirror every ref of the source repo; uncommitted work goes on a snapshot branch."""
    work = repo.with_name(repo.name + ".tmp")
    shutil.rmtree(work, ignore_errors=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    # --no-local: real pack transfer (verifies objects, no hardlinks into the drive)
    git(*_SAFE, "clone", "-q", "--mirror", "--no-local", "--template=", str(source), str(work))
    git("config", "--remove-section", "remote.origin", git_dir=work)
    (work / "description").write_text(f"{source.name}\n")
    if git("rev-parse", "--is-shallow-repository", git_dir=work) == "true":
        res.issues.append("shallow_history")
    head = git("rev-parse", "-q", "--verify", "HEAD^{commit}", git_dir=work, check=False)
    if not head:  # HEAD names a branch that doesn't exist: point it at the first one
        branch = git("for-each-ref", "--count=1", "--format=%(refname)", "refs/heads/",
                     git_dir=work)
        if not branch:
            raise GitError(f"{source}: no branch to use as HEAD")
        git("symbolic-ref", "HEAD", branch, git_dir=work)
        head = git("rev-parse", "HEAD^{commit}", git_dir=work)
        res.issues.append("head_repointed")
    # Snapshot respects the repo's own .gitignore (no -f): ignored files are build output
    # the authors chose not to version.
    tree = _snapshot_tree(source, work, ignore_filemode=ignore_filemode, force=False,
                          start=head)
    if tree != git("rev-parse", f"{head}^{{tree}}", git_dir=work):
        msg = f"Uncommitted working tree of {source.name}\n".encode()
        snap = git("commit-tree", tree, "-p", head, git_dir=work, env=ident.env(), input=msg)
        git("update-ref", f"refs/heads/{SNAPSHOT_BRANCH}", snap, git_dir=work)
        res.issues.append("dirty_worktree")
    git("repack", "-adq", git_dir=work)
    git("update-server-info", git_dir=work)
    old = repo.with_name(repo.name + ".old")
    if repo.exists():
        before = git("show-ref", git_dir=repo, check=False)
        repo.rename(old)
    else:
        before = None
    work.rename(repo)
    shutil.rmtree(old, ignore_errors=True)
    after = git("show-ref", git_dir=repo, check=False)
    res.status = "mirrored" if before is None else ("unchanged" if before == after else "updated")
    res.commit = head
    res.tree_entries = _count_tree(repo, tree)
    res.pack_bytes = _pack_bytes(repo)
    if res.pack_bytes > SWH_MAX_PACK_BYTES:
        res.issues.append("pack_too_large")


def _count_tree(repo: Path, tree: str) -> int:
    out = git("ls-tree", "-r", "--name-only", "-z", tree, git_dir=repo)
    return out.count("\0")


def _snapshot_tree(
    source: Path, repo: Path, *, ignore_filemode: bool, force: bool, start: str | None = None
) -> str:
    """Tree object of `source` as seen by `git add -A`, written into `repo`."""
    index = repo / "swhprep.index"
    env = {"GIT_INDEX_FILE": str(index), "GIT_WORK_TREE": str(source)}
    cfg = [*_SAFE, "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
           "-c", f"core.fileMode={'false' if ignore_filemode else 'true'}",
           "-c", "advice.addEmbeddedRepo=false"]
    try:
        index.unlink(missing_ok=True)
        if start:
            git("read-tree", start, git_dir=repo, env=env)
        git(*cfg, "add", "-A", *(["-f"] if force else []), ".", git_dir=repo, env=env)
        return git("write-tree", git_dir=repo, env=env)
    finally:
        index.unlink(missing_ok=True)


def _commit_tree(
    source: Path, repo: Path, ident: Identity, res: Result, *, ignore_filemode: bool
) -> None:
    env = ident.env()
    # force: archive files even when a .gitignore inside the source excludes them
    tree = _snapshot_tree(source, repo, ignore_filemode=ignore_filemode, force=True)
    res.tree_entries = _count_tree(repo, tree)
    parent = git("rev-parse", "-q", "--verify", f"refs/heads/{BRANCH}", git_dir=repo, check=False)
    if parent and git("rev-parse", f"{parent}^{{tree}}", git_dir=repo) == tree:
        res.status, res.commit = "unchanged", parent
        return
    msg = f"Import of {source.name}\n".encode()
    args = ["commit-tree", tree] + (["-p", parent] if parent else [])
    commit = git(*args, git_dir=repo, env=env, input=msg)
    git("update-ref", f"refs/heads/{BRANCH}", commit, git_dir=repo)
    res.status, res.commit = ("updated" if parent else "created"), commit


def convert_all(
    src: Path,
    dest: Path,
    depth: int,
    ident: Identity,
    *,
    jobs: int = 4,
    ignore_filemode: bool = False,
    rebuild: bool = False,
    dry_run: bool = False,
    on_result: Callable[[Result], None] | None = None,
) -> list[Result]:
    """Convert every source folder found at `depth` under `src` into `dest/<name>.git`."""
    pairs = plan(src, depth)
    results: list[Result] = []

    def work(pair: tuple[Path, str]) -> Result:
        s, name = pair
        return convert_one(s, dest / name, ident, ignore_filemode=ignore_filemode,
                           rebuild=rebuild, dry_run=dry_run)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for r in pool.map(work, pairs):
            results.append(r)
            if on_result:
                on_result(r)
    return results
