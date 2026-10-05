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
        return cls(m.group(1), m.group(2), f"{int(dt.timestamp())} {tz}")

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
    status: str = "pending"  # created | updated | unchanged | exists | empty | error | planned
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
    """Create or refresh `repo` (bare) holding the content of `source` as a single commit."""
    res = Result(source=str(source), repo=str(repo))
    try:
        res.source_entries, res.embedded_git, res.issues = scan_source(source)
        if dry_run:
            res.status = "planned"
            return res
        if res.source_entries == 0:
            res.status = "empty"
            return res
        exists = (repo / "refs" / "heads" / BRANCH).exists() or _has_ref(repo)
        if exists and not rebuild:
            res.status = "exists"
            res.commit = git("rev-parse", BRANCH, git_dir=repo)
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
    return res


def _has_ref(repo: Path) -> bool:
    if not repo.is_dir():
        return False
    return bool(git("for-each-ref", f"refs/heads/{BRANCH}", git_dir=repo, check=False))


def _commit_tree(
    source: Path, repo: Path, ident: Identity, res: Result, *, ignore_filemode: bool
) -> None:
    index = repo / "swhprep.index"
    env = {"GIT_INDEX_FILE": str(index), "GIT_WORK_TREE": str(source), **ident.env()}
    cfg = ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
           "-c", f"core.fileMode={'false' if ignore_filemode else 'true'}",
           "-c", "advice.addEmbeddedRepo=false"]
    try:
        index.unlink(missing_ok=True)
        # -f: archive files even when a .gitignore inside the source excludes them
        git(*cfg, "add", "-A", "-f", ".", git_dir=repo, env=env)
        tree = git("write-tree", git_dir=repo, env=env)
    finally:
        index.unlink(missing_ok=True)
    res.tree_entries = len(git("ls-tree", "-r", "--name-only", "-z", tree, git_dir=repo)
                           .split("\0")) - 1 if tree else 0
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
