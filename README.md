# swhprep

Turn a pile of plain source folders (no VCS) into a git forge that
[Software Heritage](https://www.softwareheritage.org/) archives through **Add Forge Now**
with the `grokmirror` lister. The lister reads one gzipped JSON manifest, so re-crawls
of an unchanged forge cost a single `304`.

```
source folders ──convert──▶ bare repos (1 commit each) ──manifest──▶ manifest.js.gz
                                   │                                     │
                              nginx + git-http-backend ◀── SWH lister ───┘
                                   ▲
                              check (git ls-remote every origin SWH will derive)
```

## Install

```sh
uv tool install .        # or: uv run swhprep ...
```

## Runbook

```sh
SRC=/media/drive/projects       # read only, never written
DEST=/srv/git

# 1. look before writing: names, embedded .git dirs, empty folders
swhprep convert "$SRC" "$DEST" --depth 1 --dry-run --report scan.jsonl \
    --author "Org Archive <archive@example.org>" --date 2000-01-01T00:00:00+00:00

# 2. convert (resumable: existing repos are skipped)
swhprep convert "$SRC" "$DEST" --depth 1 --jobs 8 --report convert.jsonl \
    --author "Org Archive <archive@example.org>" --date 2000-01-01T00:00:00+00:00
#    add --ignore-filemode if SRC is exFAT/NTFS (every file would look executable)

# 3. manifest (re-run after any change)
swhprep manifest "$DEST" "$DEST/manifest.js.gz"

# 4. serve: deploy/nginx-swh-git.conf, then from outside the server:
swhprep check https://git.example.org/manifest.js.gz --forge-url https://git.example.org/

# 5. https://archive.softwareheritage.org/add-forge/request/create/
#    type: grokmirror, URL: https://git.example.org/
```

All commands accept `--json` and exit non-zero when something needs attention.

## What `convert` does

- A repo is any directory exactly `--depth` levels under SRC. It becomes `DEST/<name>.git`,
  where `<name>` is the path with URL-unsafe characters replaced by `-`. Name collisions abort the run.
- One commit on `main` holds the whole folder. Author, committer and date are the same
  for every repo, so re-runs give identical commit ids.
- `.gitignore` files inside sources are ignored (`git add -f`), and global/system git
  config is isolated. Objects are packed, and `info/refs` is written for dumb-HTTP fallback.
- The report (one JSON line per folder) has a status (`created`, `exists`, `updated`,
  `unchanged`, `empty`, `error`, `planned`) and these issues:
  - `source_is_git_repo`: the folder already has `.git`. Its history is **not** used.
  - `embedded_git`: a nested repo is stored as a gitlink, so its **content is lost**.
  - `count_mismatch`: tree entries ≠ files on disk.
  - `pack_too_large`: over 4 GiB. The SWH git loader rejects it.
  - `empty_dirs_dropped`, `special_files_skipped`: things git can't store (informational).
- `--rebuild` re-snapshots existing repos. A changed folder gets a child commit, so SWH
  sees a new revision.

## Validation

```sh
uv run pytest -q                       # unit + smart-HTTP tests
uv run ruff check . && uv run mypy swhprep
scripts/e2e_nginx.sh                   # real nginx + fcgiwrap in docker  -> e2e OK
python scripts/validate_reference.py   # in a venv with swh.lister + grokmirror -> reference OK
```

`validate_reference.py` checks the manifest against grokmirror's `grok-manifest`. It also
runs SWH's `GrokmirrorLister` on it and checks that the origins it lists are exactly the
URLs `check` tests.

## Pitfalls this handles

- The lister builds origins as `urljoin(forge_url, key)`. A key like `/g/p.git` resolves
  from the host root, so serve at `/`, or pass `manifest --prefix /git` to match a subpath.
- With nginx's default `if_modified_since exact`, the lister never gets a 304. The shipped
  config uses `before`.
- `git-http-backend` refuses repos owned by another user. The config sets `safe.directory`.
- SWH crawls from `128.93.166.0/26` (IPv4). Keep that range out of rate limits and bot walls.

## License

MIT
