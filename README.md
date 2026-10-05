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

# 4. serve: deploy/swh-git-locations.conf included in a vhost (deploy/nginx-swh-git.conf,
#    or deploy/fxhash.gakoy.com with TLS), then from outside the server:
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
- A folder that already is a git repo with refs keeps its history. Every ref is mirrored
  (`clone --mirror --no-local`) with the same commit ids, and the remote config pointing
  back at the drive is dropped. If the working tree differs from HEAD (modified or
  untracked files, minus that repo's own `.gitignore`), a commit on top of HEAD goes to
  branch `swh-worktree-snapshot`. A repo with broken objects fails with `error`; it is
  never silently flattened.
- The report (one JSON line per folder) has a status (`created`, `mirrored`, `exists`,
  `updated`, `unchanged`, `empty`, `error`, `planned`) and these issues:
  - `source_is_git_repo`: history mirrored (see above). `dirty_worktree`: snapshot branch
    written. `shallow_history`, `head_repointed` (HEAD named a missing branch),
    `source_git_unborn` (`.git` without commits: synthetic commit instead).
  - `embedded_git`: a nested repo is stored as a gitlink, so its **content is lost**.
  - `count_mismatch`: tree entries ≠ files on disk.
  - `pack_too_large`: over 4 GiB. The SWH git loader rejects it.
  - `empty_dirs_dropped`, `special_files_skipped`: things git can't store (informational).
- `--rebuild` re-snapshots existing repos. A changed folder gets a child commit, so SWH
  sees a new revision.

## Deployment: fxhash.gakoy.com on rootmab

rootmab runs nginx 1.24, git 2.43 and fcgiwrap (`/run/fcgiwrap.socket`, active), with
1.6 TB free. Infra conventions are in `../rootmab/gakoy.md`.

```sh
# 1. convert (synthetic commits: Benoit Baudry, 1970-01-01 UTC)
swhprep convert "$SRC" "$DEST" --depth 1 --jobs 8 --report convert.jsonl \
    --author "Benoit Baudry <benoit.baudry@umontreal.ca>" --date 1970-01-01T00:00:00+00:00
swhprep manifest "$DEST" "$DEST/manifest.js.gz"

# 2. ship
ssh rootmab 'sudo install -d -o martin /srv/fxhash-git'
rsync -a --delete "$DEST/" rootmab:/srv/fxhash-git/

# 3. DNS: A record fxhash.gakoy.com -> 37.187.127.139 (OVH API, no wildcard)

# 4. nginx + TLS
scp deploy/swh-git-locations.conf deploy/fxhash.gakoy.com rootmab:/tmp/
ssh rootmab 'sudo install -m644 /tmp/swh-git-locations.conf /etc/nginx/snippets/ &&
  sudo install -m644 /tmp/fxhash.gakoy.com /etc/nginx/sites-available/ &&
  sudo ln -sf ../sites-available/fxhash.gakoy.com /etc/nginx/sites-enabled/ &&
  sudo nginx -t && sudo systemctl reload nginx &&
  sudo certbot --nginx --redirect --cert-name fxhash.gakoy.com -d fxhash.gakoy.com'

# 5. verify from outside, then file Add Forge Now (grokmirror, https://fxhash.gakoy.com/)
swhprep check https://fxhash.gakoy.com/manifest.js.gz --forge-url https://fxhash.gakoy.com/
```

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
