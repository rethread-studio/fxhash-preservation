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
    `gitdir_repaired` (a copy tool had turned empty `.git` subdirs such as `refs/tags`
    into 0-byte files; the mirror is cloned from a fixed temporary copy, source untouched),
    `source_git_unborn` (`.git` without commits: synthetic commit instead).
  - `embedded_git`: a git repo nested inside a folder. Git would store only a gitlink and
    **lose its content**, so `convert` scans every folder first, and if any has one it
    writes nothing and exits 3 with the list. `--dry-run` reports them without aborting.
    After review, `--flatten-nested PATH` (relative to SRC, repeatable) stores a listed
    one's working-tree files as plain files, without its history: issue
    `nested_git_flattened`. Unlisted nested repos still abort; a listed path that isn't a
    nested repo exits 2.
  - `dangling_gitfile_dropped`: a `.git` *file* whose `gitdir:` target is missing (a
    submodule copied without its parent). Git treats the folder as plain, so its files are
    stored and only the pointer line is lost.
  - `count_mismatch`: tree entries ≠ files on disk.
  - `pack_too_large`: over 4 GiB. The SWH git loader rejects it.
  - `empty_dirs_dropped`, `special_files_skipped`: things git can't store (informational).
  - `all_executable`: every file of the folder has the executable bit, typical of a copy
    through exFAT/NTFS/zip. Without `--ignore-filemode`, `convert` warns and exits 1.
- `--rebuild` re-snapshots existing repos. A changed folder gets a child commit, so SWH
  sees a new revision.

## Deployment: fxhash.gakoy.com on rootmab

Serving all 27,596 fxhash repos (80 GB, 276k files) since 2026-10-06. `check` passed on
every origin (one transient timeout, fine on retry). The upload took 98 min at ~14 MB/s
and the full check 43 min at 16 jobs. Already done: the OVH A record
`fxhash` → 37.187.127.139 (id 5439621500); the snippet and vhost installed on rootmab;
the Let's Encrypt cert with HTTP→HTTPS redirect; `/srv/fxhash-git` (755, martin). An
HTTPS smoke test (check, clone, 304, receive-pack 403) passed and was cleaned up.
rootmab runs nginx 1.24, git 2.43 and fcgiwrap, with 1.6 TB free. Infra conventions are
in `../rootmab/gakoy.md`.

The conversion runs on the external drive itself: SRC is the folder tree, DEST a sibling
folder on the same drive.

```sh
SRC=/media/martin/63d87370-5401-42f9-a718-bead975d75d4/fxhash-archive  # <platform>/<year>/<project>
DEST=/media/martin/63d87370-5401-42f9-a718-bead975d75d4/fxhash-git
# the 2 nested repos found (vendored GitHub libs); approved to store as plain files
FLAT=(--flatten-nested 'fxhash-1.0-tezos/2023/Aweille!__28348/library'
      --flatten-nested 'fxhash-1.0-tezos/2023/Gridscape Symphony__29177/p5.gif.js')
# the drive is ext4, but all 663k source files are mode 777 (copy artefact): ignore modes
OPTS=(--depth 3 --ignore-filemode "${FLAT[@]}")

# 0. scan: nested git repos, names, empty folders, all-executable folders (writes nothing)
swhprep convert "$SRC" "$DEST" "${OPTS[@]}" --dry-run --report scan.jsonl \
    --author "Benoit Baudry <benoit.baudry@umontreal.ca>" --date 1970-01-01T00:00:00+00:00

# 1. convert (synthetic commits: Benoit Baudry, 1970-01-01 UTC); exit 3 = nested repos
#    ~1h50 for the 27.6k on 8 jobs (CPU-bound in repack)
swhprep convert "$SRC" "$DEST" "${OPTS[@]}" --jobs 8 --report convert.jsonl \
    --author "Benoit Baudry <benoit.baudry@umontreal.ca>" --date 1970-01-01T00:00:00+00:00
swhprep manifest "$DEST" "$DEST/manifest.js.gz"

# 2. ship
# --chmod: nginx/fcgiwrap (www-data) must read everything; -a alone copies local perms,
# including those of $DEST itself onto /srv/fxhash-git (a 0750 there = 403 everywhere)
rsync -a --delete --chmod=Da+rx,Fa+r "$DEST/" rootmab:/srv/fxhash-git/

# 3. verify from outside; must print no ❌ and exit 0
swhprep check https://fxhash.gakoy.com/manifest.js.gz --forge-url https://fxhash.gakoy.com/
```

4. Martin files Add Forge Now at https://archive.softwareheritage.org/add-forge/request/create/
   (log in to SWH first):
   - Forge type: `grokmirror`
   - Forge URL: `https://fxhash.gakoy.com/`
   - Contact name: `Martin Monperrus`
   - Contact email: `monperrus@kth.se`
   - Comment: `About 27,000 git repositories, one per fxhash project, listed in
     https://fxhash.gakoy.com/manifest.js.gz (grokmirror format), served over smart HTTPS.
     Most are single-commit snapshots of source folders; some keep their original history.
     The server is a small machine: please use the default Add Forge Now rate limits.`

Server setup, already done, kept for a rebuild. Re-installing the vhost file overwrites
certbot's 443 lines, so re-run certbot afterwards.

```sh
ssh rootmab 'sudo install -d -o martin -m755 /srv/fxhash-git'
# DNS: A record fxhash.gakoy.com -> 37.187.127.139 (OVH API, no wildcard; agent-ovh)
scp deploy/swh-git-locations.conf deploy/fxhash.gakoy.com rootmab:/tmp/
ssh rootmab 'sudo install -m644 /tmp/swh-git-locations.conf /etc/nginx/snippets/ &&
  sudo install -m644 /tmp/fxhash.gakoy.com /etc/nginx/sites-available/ &&
  sudo ln -sf ../sites-available/fxhash.gakoy.com /etc/nginx/sites-enabled/ &&
  sudo nginx -t && sudo systemctl reload nginx &&
  sudo certbot --nginx --redirect --cert-name fxhash.gakoy.com -d fxhash.gakoy.com'
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
