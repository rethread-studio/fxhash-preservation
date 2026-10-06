"""Convert plain source folders into bare git repos with one reproducible commit."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Iterable, Iterator, Mapping
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
        return cls(m.group(1), m.group(2), cls._git_date(date))

    @classmethod
    def make(cls, name: str, email: str, date: str) -> Identity:
        """Identity from separate fields; refuses what git would silently mangle."""
        if not name.strip() or any(c in name for c in "<>\n\0"):
            raise ValueError(f"unusable name for git: {name!r}")
        if not email or any(c in email for c in "<> \n\0"):
            raise ValueError(f"unusable email for git: {email!r}")
        return cls(name.strip(), email, cls._git_date(date))

    @staticmethod
    def _git_date(date: str) -> str:
        dt = datetime.fromisoformat(date)
        if dt.tzinfo is None:
            raise ValueError(f"date must carry a timezone, e.g. 2026-01-01T00:00:00+00:00: {date!r}")
        offset = dt.utcoffset()
        assert offset is not None
        minutes = int(offset.total_seconds()) // 60
        sign = "+" if minutes >= 0 else "-"
        tz = f"{sign}{abs(minutes) // 60:02d}{abs(minutes) % 60:02d}"
        # "@": git rejects a bare "0 +0000" (epoch 0) as an invalid date; same stored value
        return f"@{int(dt.timestamp())} {tz}"

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


class EmbeddedGitAbort(RuntimeError):
    """Some source folders contain nested git repos; the whole run is aborted."""

    def __init__(self, folders: dict[str, list[str]]) -> None:
        self.folders = folders
        super().__init__(f"{len(folders)} folder(s) contain nested git repos, nothing written")


def _under(rel: str, roots: frozenset[str]) -> bool:
    return any(rel == f or rel.startswith(f + "/") for f in roots)


def _nested(embedded: list[str], flatten: frozenset[str] = frozenset()) -> list[str]:
    """Nested repos git would store as a gitlink (i.e. not the source itself, not flattened)."""
    return [e for e in embedded if e != "." and not _under(e, flatten)]


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


def _is_git_marker(root: Path, dirs: list[str], files: list[str]) -> bool | None:
    """True if `root` holds a usable `.git`, None for a gitfile pointing nowhere, else False.

    A `.git` file whose `gitdir:` target is missing (a submodule copied without its parent
    repo) makes git treat the directory as a plain one: its files are stored, the pointer
    is dropped.
    """
    if ".git" in dirs:
        return True
    if ".git" not in files:
        return False
    try:
        line = (root / ".git").read_text(errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    target = line.removeprefix("gitdir:").strip()
    return True if line.startswith("gitdir:") and (root / target).is_dir() else None


def scan_source(
    path: Path, flatten: frozenset[str] = frozenset()
) -> tuple[int, list[str], list[str]]:
    """Count what git can store: regular files and symlinks outside any `.git` component.

    `flatten` lists nested repos (relative to `path`) whose files are stored as plain files
    instead of a gitlink. Returns (entries, embedded_git_dirs, issues).
    """
    entries = regular = executable = 0
    embedded: list[str] = []
    issues: set[str] = set()
    for root, dirs, files in os.walk(path, followlinks=False):
        rootp = Path(root)
        marker =_is_git_marker(rootp, dirs, files)
        if marker is None:
            issues.add("dangling_gitfile_dropped")
        elif marker:
            rel = rootp.relative_to(path).as_posix()
            embedded.append(rel)
            if rootp != path and _under(rel, flatten):
                issues.add("nested_git_flattened")
            elif rootp != path:
                # git stores a nested repo as one gitlink entry, not its files
                issues.add("embedded_git")
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
            if p.is_symlink():
                entries += 1
            elif p.is_file():
                entries += 1
                regular += 1
                executable += bool(p.stat().st_mode & 0o100)
            else:
                issues.add("special_files_skipped")
        if not dirs and not files and rootp != path:
            issues.add("empty_dirs_dropped")
    if "." in embedded:
        issues.add("source_is_git_repo")
    if regular and executable == regular:
        # typical of a copy through exFAT/NTFS/zip: modes carry no meaning
        issues.add("all_executable")
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
    scan: tuple[int, list[str], list[str]] | None = None,
    flatten: frozenset[str] = frozenset(),
    head_only: bool = False,
) -> Result:
    """Create or refresh bare `repo` from `source`.

    A source that is itself a git repo with commits keeps its history (all refs mirrored;
    with `head_only`, only the branch HEAD points to, uncommitted work committed on top);
    anything else becomes a single synthetic commit. A source holding a nested git repo is
    refused (git would store it as a bare gitlink and lose its content), unless that repo is
    listed in `flatten` (paths relative to `source`): its files are then stored as plain
    files, without its history.
    """
    res = Result(source=str(source), repo=str(repo))
    try:
        entries, embedded, issues = scan or scan_source(source, flatten)
        res.source_entries, res.embedded_git, res.issues = entries, embedded, list(issues)
        if "embedded_git" in res.issues and not dry_run:
            res.status = "error"
            res.error = (f"nested git repo(s) {_nested(res.embedded_git, flatten)}: "
                         "refusing to convert")
            return res
        flat = sorted(e for e in embedded if e != "." and e in flatten)
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
            _mirror(source, repo, ident, res, ignore_filemode=ignore_filemode, flatten=flat,
                    head_only=head_only)
            return res
        work = repo if exists else repo.with_name(repo.name + ".tmp")
        if not exists:
            shutil.rmtree(work, ignore_errors=True)
            work.parent.mkdir(parents=True, exist_ok=True)
            # empty template: no sample hooks, i.e. ~15 fewer files per repo
            git("init", "-q", "--bare", "--template=", f"--initial-branch={BRANCH}", str(work))
            (work / "description").write_text(f"{source.name}\n")
        _commit_tree(source, work, ident, res, ignore_filemode=ignore_filemode, flatten=flat)
        git("repack", "-adq", git_dir=work)
        git("update-server-info", git_dir=work)
        if not exists:
            work.rename(repo)
        res.pack_bytes = _pack_bytes(repo)
        if res.pack_bytes > SWH_MAX_PACK_BYTES:
            res.issues.append("pack_too_large")
        # unflattened nested repos were refused above, so every counted file must be stored
        if res.tree_entries != res.source_entries:
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
    source: Path, repo: Path, ident: Identity, res: Result, *, ignore_filemode: bool,
    flatten: list[str] | None = None, head_only: bool = False,
) -> None:
    """Mirror every ref of the source repo; uncommitted work goes on a snapshot branch.

    With `head_only`, keep only the branch HEAD points to (`main` if HEAD is detached), and
    commit uncommitted work on top of it; every other ref and its objects are dropped.
    """
    work = repo.with_name(repo.name + ".tmp")
    shutil.rmtree(work, ignore_errors=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    origin = _repaired_gitdir(source, repo.with_name(repo.name + ".src"), res)
    try:
        # --no-local: real pack transfer (verifies objects, no hardlinks into the drive)
        git(*_SAFE, "clone", "-q", "--mirror", "--no-local", "--template=", str(origin),
            str(work))
    finally:
        if origin != source:
            shutil.rmtree(origin, ignore_errors=True)
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
    if head_only:
        branch = git("symbolic-ref", "-q", "HEAD", git_dir=work, check=False)
        if not branch:  # detached HEAD
            branch = f"refs/heads/{BRANCH}"
            git("update-ref", branch, head, git_dir=work)
            git("symbolic-ref", "HEAD", branch, git_dir=work)
        others = [r for r in git("for-each-ref", "--format=%(refname)", git_dir=work).split("\n")
                  if r and r != branch]
        if others:
            git("update-ref", "--stdin", git_dir=work,
                input="".join(f"delete {r}\n" for r in others).encode())
            res.issues.append("refs_dropped")
    # Snapshot respects the repo's own .gitignore (no -f): ignored files are build output
    # the authors chose not to version.
    tree = _snapshot_tree(source, work, ignore_filemode=ignore_filemode, force=False,
                          start=head, flatten=flatten)
    if tree != git("rev-parse", f"{head}^{{tree}}", git_dir=work):
        msg = f"Uncommitted working tree of {source.name}\n"
        snap = _write_commit(work, tree, [head], ident, msg)
        if head_only:
            git("update-ref", "HEAD", snap, head, git_dir=work)
            head = snap
        else:
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


# Directories git needs inside .git. Some copy tools turn empty ones into 0-byte files,
# which git then reads as refs with a zero id ("not our ref 0000...").
_GITDIR_DIRS = ("branches", "hooks", "info", "objects/info", "objects/pack", "refs/heads",
                "refs/remotes", "refs/tags")


def _repaired_gitdir(source: Path, scratch: Path, res: Result) -> Path:
    """`source`, or a copy of its .git in `scratch` with such 0-byte files made dirs again."""
    gitdir = source / ".git"
    bad = [d for d in _GITDIR_DIRS
           if (gitdir / d).is_file() and not (gitdir / d).is_symlink()
           and (gitdir / d).stat().st_size == 0]
    if not bad:
        return source
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.copytree(gitdir, scratch, symlinks=True)
    for d in bad:
        (scratch / d).unlink()
        (scratch / d).mkdir()
    res.issues.append("gitdir_repaired")
    return scratch


def _count_tree(repo: Path, tree: str) -> int:
    out = git("ls-tree", "-r", "--name-only", "-z", tree, git_dir=repo)
    return out.count("\0")


def _snapshot_tree(
    source: Path, repo: Path, *, ignore_filemode: bool, force: bool, start: str | None = None,
    flatten: list[str] | None = None,
) -> str:
    """Tree object of `source` as seen by `git add -A`, written into `repo`.

    Nested repos in `flatten` are kept out of `git add` (which would store a gitlink) and
    their files are added one by one instead.
    """
    index = repo / "swhprep.index"
    env = {"GIT_INDEX_FILE": str(index), "GIT_WORK_TREE": str(source)}
    cfg = [*_SAFE, "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
           "-c", f"core.fileMode={'false' if ignore_filemode else 'true'}",
           "-c", "advice.addEmbeddedRepo=false"]
    flatten = flatten or []
    try:
        index.unlink(missing_ok=True)
        if start:
            git("read-tree", start, git_dir=repo, env=env)
        excl = [f":(exclude,literal){f}" for f in flatten]
        git(*cfg, "add", "-A", *(["-f"] if force else []), "--", ".", *excl,
            git_dir=repo, env=env)
        for f in flatten:
            _add_flat(source, f, repo, env, ignore_filemode=ignore_filemode)
        return git("write-tree", git_dir=repo, env=env)
    finally:
        index.unlink(missing_ok=True)


def _add_flat(
    source: Path, rel: str, repo: Path, env: dict[str, str], *, ignore_filemode: bool
) -> None:
    """Replace whatever the index holds under `rel` by the plain files found there on disk."""
    git(*_SAFE, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", f":(literal){rel}",
        git_dir=repo, env=env)
    regular: list[tuple[str, str]] = []  # (mode, path relative to source)
    links: list[str] = []
    for root, dirs, files in os.walk(source / rel, followlinks=False):
        rootp = Path(root)
        dirs[:] = [d for d in dirs if d != ".git"]
        links += [(rootp / d).relative_to(source).as_posix()
                  for d in dirs if (rootp / d).is_symlink()]
        dirs[:] = [d for d in dirs if not (rootp / d).is_symlink()]
        for name in files:
            p = rootp / name
            if name == ".git":
                continue
            r = p.relative_to(source).as_posix()
            if "\n" in r:
                raise ValueError(f"{p}: newline in file name")
            if p.is_symlink():
                links.append(r)
            elif p.is_file():
                exe = p.stat().st_mode & 0o100 and not ignore_filemode
                regular.append(("100755" if exe else "100644", r))
    entries: list[tuple[str, str, str]] = []
    if regular:
        paths = "".join(str(source / r) + "\n" for _, r in regular)
        shas = git("hash-object", "-w", "--no-filters", "--stdin-paths", git_dir=repo,
                   input=os.fsencode(paths)).split("\n")
        entries += [(m, s, r) for (m, r), s in zip(regular, shas, strict=True)]
    for r in links:
        sha = git("hash-object", "-w", "--stdin", git_dir=repo,
                  input=os.fsencode(os.readlink(source / r)))
        entries.append(("120000", sha, r))
    if entries:
        info = b"".join(os.fsencode(f"{m} {s}\t{r}") + b"\0" for m, s, r in entries)
        git("update-index", "-z", "--index-info", git_dir=repo, env=env, input=info)


def _write_commit(repo: Path, tree: str, parents: list[str], ident: Identity, msg: str) -> str:
    """Write a commit object byte for byte (author = committer = `ident`).

    Same bytes, hence same id, as `git commit-tree`, except that names are kept verbatim:
    commit-tree trims characters such as `.,:;"'` from the ends of names.
    """
    when = ident.date.removeprefix("@")
    who = f"{ident.name} <{ident.email}> {when}"
    head = [f"tree {tree}", *(f"parent {p}" for p in parents),
            f"author {who}", f"committer {who}"]
    body = "\n".join(head) + "\n\n" + msg
    return git("hash-object", "-t", "commit", "-w", "--stdin", git_dir=repo,
               input=body.encode())


def _commit_tree(
    source: Path, repo: Path, ident: Identity, res: Result, *, ignore_filemode: bool,
    flatten: list[str] | None = None,
) -> None:
    # force: archive files even when a .gitignore inside the source excludes them
    tree = _snapshot_tree(source, repo, ignore_filemode=ignore_filemode, force=True,
                          flatten=flatten)
    res.tree_entries = _count_tree(repo, tree)
    parent = git("rev-parse", "-q", "--verify", f"refs/heads/{BRANCH}", git_dir=repo, check=False)
    if parent and git("rev-parse", f"{parent}^{{tree}}", git_dir=repo) == tree:
        res.status, res.commit = "unchanged", parent
        return
    commit = _write_commit(repo, tree, [parent] if parent else [], ident,
                           f"Import of {source.name}\n")
    git("update-ref", f"refs/heads/{BRANCH}", commit, git_dir=repo)
    res.status, res.commit = ("updated" if parent else "created"), commit


def convert_all(
    src: Path,
    dest: Path,
    depth: int,
    ident: Identity | None,
    *,
    jobs: int = 4,
    ignore_filemode: bool = False,
    rebuild: bool = False,
    dry_run: bool = False,
    on_result: Callable[[Result], None] | None = None,
    flatten_nested: Iterable[str] = (),
    head_only: bool = False,
    identities: Mapping[str, Identity] | None = None,
) -> list[Result]:
    """Convert every source folder found at `depth` under `src` into `dest/<name>.git`.

    Unless `dry_run`, every folder is scanned first and nothing is written if any of them
    holds a nested git repo (EmbeddedGitAbort lists them) not named in `flatten_nested`
    (paths relative to `src`, e.g. "group/project/lib"). A listed path that isn't a nested
    repo is a ValueError.

    `identities` maps a folder path relative to `src` to the identity and date of its
    commit, replacing `ident`; a folder missing from it is a ValueError (nothing written).
    """
    pairs = plan(src, depth)
    if identities is not None:
        idents = [identities.get(s.relative_to(src).as_posix()) for s, _ in pairs]
        missing_ids = [s.relative_to(src).as_posix()
                       for (s, _), i in zip(pairs, idents, strict=True) if i is None]
        if missing_ids:
            raise ValueError(f"identity map: {len(missing_ids)} folder(s) missing, e.g. "
                             f"{', '.join(missing_ids[:3])}")
    elif ident is None:
        raise ValueError("an identity or an identity map is required")
    else:
        idents = [ident] * len(pairs)
    wanted = {Path(f).as_posix().strip("/") for f in flatten_nested}
    flats: list[frozenset[str]] = []
    for s, _ in pairs:
        prefix = s.relative_to(src).as_posix() + "/"
        flats.append(frozenset(f.removeprefix(prefix) for f in wanted if f.startswith(prefix)))
    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        scans = list(pool.map(scan_source, [s for s, _ in pairs], flats))
    found = {f"{s.relative_to(src).as_posix()}/{e}"
             for (s, _), sc in zip(pairs, scans, strict=True) for e in sc[1] if e != "."}
    if missing := sorted(wanted - found):
        raise ValueError(f"--flatten-nested: not a nested git repo: {', '.join(missing)}")
    if not dry_run:
        bad = {str(s): _nested(sc[1], fl)
               for (s, _), sc, fl in zip(pairs, scans, flats, strict=True)
               if "embedded_git" in sc[2]}
        if bad:
            raise EmbeddedGitAbort(bad)

    def work(item: tuple[tuple[Path, str], tuple[int, list[str], list[str]], frozenset[str],
                         Identity | None]) -> Result:
        (s, name), scan, flat, who = item
        assert who is not None
        return convert_one(s, dest / name, who, ignore_filemode=ignore_filemode,
                           rebuild=rebuild, dry_run=dry_run, scan=scan, flatten=flat,
                           head_only=head_only)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for r in pool.map(work, zip(pairs, scans, flats, idents, strict=True)):
            results.append(r)
            if on_result:
                on_result(r)
    return results
