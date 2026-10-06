"""Command-line interface: `swhprep convert | manifest | check`."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import IO, Any

from ._check import Check, check_all, load_manifest
from ._convert import EmbeddedGitAbort, Identity, Result, convert_all
from ._manifest import build_manifest, write_manifest

# Statuses / issues that mean an archive would be incomplete or wrong.
_BAD_STATUS = {"error"}
_BAD_ISSUES = {"count_mismatch", "pack_too_large", "embedded_git"}


def _summary(counter: Counter[str], as_json: bool, extra: dict[str, Any]) -> None:
    if as_json:
        print(json.dumps({**extra, **dict(sorted(counter.items()))}))
    else:
        for k, v in sorted(counter.items()):
            print(f"{k:<24} {v}")


def _load_identities(path: str) -> dict[str, Identity]:
    """JSON {"<folder relative to SRC>": {"name": ..., "email": ..., "date": ISO 8601}}."""
    with open(path) as f:
        raw = json.load(f)
    try:
        return {k: Identity.make(v["name"], v["email"], v["date"]) for k, v in raw.items()}
    except (KeyError, TypeError) as e:
        raise ValueError(f"{path}: each entry needs name, email and date ({e})") from e


def cmd_convert(args: argparse.Namespace) -> int:
    if (args.author is None) != (args.date is None):
        raise ValueError("--author and --date go together")
    if args.author is None and args.identity_map is None:
        raise ValueError("give --author and --date, or --identity-map")
    ident = Identity.parse(args.author, args.date) if args.author is not None else None
    identities = _load_identities(args.identity_map) if args.identity_map else None
    try:
        with contextlib.ExitStack() as stack:
            report = stack.enter_context(open(args.report, "a")) if args.report else None
            counter = _run_convert(args, ident, identities, report)
    except EmbeddedGitAbort as e:
        if args.json:
            print(json.dumps({"aborted": "embedded_git", "folders": e.folders}))
        else:
            print(f"🛑 {e}:", file=sys.stderr)
            for folder, nested in sorted(e.folders.items()):
                print(f"   {folder}: {', '.join(nested)}", file=sys.stderr)
        return 3
    _summary(counter, args.json, {})
    bad = counter.get("status:error", 0) + sum(counter.get(f"issue:{i}", 0) for i in _BAD_ISSUES)
    if counter.get("issue:all_executable") and not args.ignore_filemode:
        bad += 1
        if not args.json:
            print(f"⚠️  {counter['issue:all_executable']} folder(s) have every file executable "
                  "(exFAT/NTFS/zip copy?): consider --ignore-filemode", file=sys.stderr)
    return 1 if bad else 0


def _run_convert(args: argparse.Namespace, ident: Identity | None,
                 identities: dict[str, Identity] | None,
                 report: IO[str] | None) -> Counter[str]:
    counter: Counter[str] = Counter()

    def on_result(r: Result) -> None:
        counter[f"status:{r.status}"] += 1
        for i in r.issues:
            counter[f"issue:{i}"] += 1
        if report:
            report.write(json.dumps(r.to_dict()) + "\n")
            report.flush()
        if r.status in _BAD_STATUS and not args.json:
            print(f"❌ {r.source}: {r.error}", file=sys.stderr)

    convert_all(Path(args.src), Path(args.dest), args.depth, ident, jobs=args.jobs,
                ignore_filemode=args.ignore_filemode, rebuild=args.rebuild,
                dry_run=args.dry_run, on_result=on_result,
                flatten_nested=args.flatten_nested, head_only=args.head_only,
                identities=identities)
    return counter


def cmd_manifest(args: argparse.Namespace) -> int:
    manifest = build_manifest(Path(args.toplevel), prefix=args.prefix)
    write_manifest(manifest, Path(args.out))
    if args.json:
        print(json.dumps({"repos": len(manifest), "out": args.out}))
    return 0 if manifest else 1


def cmd_check(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    failed: list[Check] = []

    def on_result(c: Check) -> None:
        if not c.ok:
            failed.append(c)
            if not args.json:
                print(f"❌ {c.url}: {c.error}", file=sys.stderr)

    results = check_all(manifest, args.forge_url, jobs=args.jobs, on_result=on_result)
    if args.json:
        print(json.dumps({"checked": len(results), "failed": [c.to_dict() for c in failed]}))
    elif failed:
        print(f"{len(failed)}/{len(results)} origins failed")
    return 1 if failed or not results else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="swhprep", description=__doc__)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="turn source folders into bare git repos")
    c.add_argument("src", help="root holding the source folders (read only)")
    c.add_argument("dest", help="root of the served bare repos")
    c.add_argument("--depth", type=int, default=1,
                   help="a repo is any directory exactly this deep under SRC (default 1)")
    c.add_argument("--author", help="'Name <email>' for author and committer")
    c.add_argument("--date", help="ISO 8601 date with timezone for every commit")
    c.add_argument("--identity-map", metavar="FILE",
                   help="JSON {folder relative to SRC: {name, email, date}}: per-folder author, "
                        "committer and date, instead of --author/--date; every folder must "
                        "be listed")
    c.add_argument("--jobs", type=int, default=4)
    c.add_argument("--ignore-filemode", action="store_true",
                   help="store every file as 100644 (for exFAT/NTFS where all look executable)")
    c.add_argument("--rebuild", action="store_true",
                   help="re-snapshot existing repos; a changed tree gets a child commit")
    c.add_argument("--dry-run", action="store_true", help="scan and report, write nothing")
    c.add_argument("--report", help="append one JSON line per folder to this file")
    c.add_argument("--head-only", action="store_true",
                   help="for sources that are git repos: keep only the branch HEAD points to "
                        "and commit uncommitted work on top of it (default: mirror every ref, "
                        "uncommitted work on branch swh-worktree-snapshot)")
    c.add_argument("--flatten-nested", action="append", default=[], metavar="PATH",
                   help="nested git repo (path relative to SRC) to store as plain files, "
                        "without its history; repeatable. Other nested repos still abort")
    c.set_defaults(func=cmd_convert)

    m = sub.add_parser("manifest", help="write grokmirror manifest.js.gz")
    m.add_argument("toplevel", help="root of the served bare repos")
    m.add_argument("out", help="output path, e.g. TOPLEVEL/manifest.js.gz")
    m.add_argument("--prefix", default="/",
                   help="URL path where TOPLEVEL is served (default /)")
    m.set_defaults(func=cmd_manifest)

    k = sub.add_parser("check", help="git ls-remote every origin SWH will derive")
    k.add_argument("manifest", help="manifest path or URL")
    k.add_argument("--forge-url", required=True,
                   help="the URL given to Add Forge Now, e.g. https://git.example.org/")
    k.add_argument("--jobs", type=int, default=8)
    k.set_defaults(func=cmd_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rc: int = args.func(args)
    except (ValueError, OSError) as e:  # OSError covers urllib's HTTPError/URLError
        print(f"❌ {e}", file=sys.stderr)
        return 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
