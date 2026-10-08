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
#    or deploy/fxhash-preservation.rethread.art with TLS), then from outside the server:
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
- `--head-only` publishes such a repo with a single branch: the one HEAD points to (`main`
  if detached), with the uncommitted work committed on top of it. Other branches,
  remote-tracking refs, tags and stashes are dropped, with their objects (`refs_dropped`).
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

## Deployment: fxhash-preservation.rethread.art on rootmab

URL: `https://fxhash-preservation.rethread.art/` (DNS at Gandi: A record to
37.187.127.139). The first name, `fxhash.gakoy.com`, was removed on 2026-10-06: vhost,
certificate and OVH A record.

Serving all 27,596 fxhash repos (80 GB, 276k files) since 2026-10-06. `check` passed on
every origin (one transient timeout, fine on retry). The upload took 98 min at ~14 MB/s
and the full check 43 min at 16 jobs. Already done: the snippet and vhost installed on
rootmab; the Let's Encrypt cert with HTTP→HTTPS redirect; `/srv/fxhash-git` (755,
martin). An HTTPS smoke test (check, clone, 304, receive-pack 403) passed.
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
# the 42 source git repos: one branch each, uncommitted work committed on it (user's call)
# the commit of each project: author = committer = the artist, dated at the project's
# creation on chain (see "Artist and date" below)
python scripts/fxhash_identities.py "$SRC" data/fxhash-meta.json.gz ids.json \
    data/name-overrides.json
OPTS=(--depth 3 --ignore-filemode --head-only "${FLAT[@]}" --identity-map ids.json)

# 0. scan: nested git repos, names, empty folders, all-executable folders (writes nothing)
swhprep convert "$SRC" "$DEST" "${OPTS[@]}" --dry-run --report scan.jsonl

# 1. convert; exit 3 = nested repos
#    ~1h50 for the 27.6k on 8 jobs (CPU-bound in repack)
swhprep convert "$SRC" "$DEST" "${OPTS[@]}" --jobs 8 --report convert.jsonl
swhprep manifest "$DEST" "$DEST/manifest.js.gz"

# 2. ship
# --chmod: nginx/fcgiwrap (www-data) must read everything; -a alone copies local perms,
# including those of $DEST itself onto /srv/fxhash-git (a 0750 there = 403 everywhere)
rsync -a --delete --chmod=Da+rx,Fa+r "$DEST/" rootmab:/srv/fxhash-git/
#    shortcut when only commit identities changed (trees already on the server):
#    ship the tip commits (a few MB), not the packs (80 GB, ~100 min)
python scripts/export_commits.py "$DEST" commits.jsonl.gz
scp scripts/apply_commits.py commits.jsonl.gz rootmab:/tmp/
ssh rootmab 'python3 /tmp/apply_commits.py /srv/fxhash-git /tmp/commits.jsonl.gz' > fp.tsv
#    then compare fp.tsv with the fingerprints in "$DEST/manifest.js.gz" and upload the
#    manifest. The server keeps the old commits as unreachable objects, and its packs no
#    longer match $DEST byte for byte, so the next full rsync moves everything again.

# 3. verify from outside; must print no ❌ and exit 0
swhprep check https://fxhash-preservation.rethread.art/manifest.js.gz \
    --forge-url https://fxhash-preservation.rethread.art/
```

4. Martin files Add Forge Now at https://archive.softwareheritage.org/add-forge/request/create/
   (log in to SWH first):
   - Forge type: `grokmirror`
   - Forge URL: `https://fxhash-preservation.rethread.art/`
   - Contact name: `Martin Monperrus`
   - Contact email: `monperrus@kth.se`
   - Comment: `About 27,600 git repositories, one per fxhash project, listed in
     https://fxhash-preservation.rethread.art/manifest.js.gz (grokmirror format), served
     over smart HTTPS. Each is a snapshot of the project's source, committed as its artist
     at the project's creation date; some keep their original history.
     The server is a small machine: please use the default Add Forge Now rate limits.`

Server setup, already done, kept for a rebuild. Re-installing the vhost file overwrites
certbot's 443 lines, so re-run certbot afterwards.

```sh
ssh rootmab 'sudo install -d -o martin -m755 /srv/fxhash-git'
# DNS: fxhash-preservation.rethread.art A 37.187.127.139 (Gandi)
H=fxhash-preservation.rethread.art
scp deploy/swh-git-locations.conf deploy/$H rootmab:/tmp/
ssh rootmab "sudo install -m644 /tmp/swh-git-locations.conf /etc/nginx/snippets/ &&
  sudo install -m644 /tmp/$H /etc/nginx/sites-available/ &&
  sudo ln -sf ../sites-available/$H /etc/nginx/sites-enabled/ &&
  sudo nginx -t && sudo systemctl reload nginx && sleep 5 &&
  sudo certbot --nginx --redirect --cert-name $H -d $H"
# (sleep: right after a reload, old nginx workers still answer for a few seconds, and
#  the first certbot attempt failed with a 404 on the challenge)
```

### Artist and date

`data/fxhash-meta.json.gz` is a snapshot of the fxhash v2 GraphQL API (28,194 projects,
both chains, fetched 2026-10-06), and `data/fxhash-api-raw.json.gz` keeps the rows exactly
as the API returned them. Re-fetch with `scripts/fetch_fxhash_meta.py`. fxhash is shut
down, so the snapshot is the source of record.

`scripts/fxhash_identities.py` derives one identity per project:
- date: `created_at`, i.e. the issuer creation on Tezos and the contract deployment on
  EVM. It is not `mint_opens_at`, the sale date found in `_source.txt`.
- name: on Tezos, the artist's on-chain name (FXHASH User Registry), else their fxhash
  username, else the address. 169 artists set no name anywhere, so the address is their
  name. On EVM, the fxhash username is the only name. A collaboration is named `A & B`.
  Names are stored verbatim (`<`/`>` become `‹`/`›`).
- email: `<author address>@fxhash.xyz`, using the collab contract address for collaborations.

`data/name-overrides.json` replaces the on-chain name when the artist asked for another
one: `ALT null` → `OGCrayon` (64 projects, one of them a collaboration), as fxhashArchive
also does.

Cross-checks: every Tezos `created_at` equals fxhashArchive's capture of the v1 API
(27,051/27,051), and with the override every solo Tezos name it has matches.

### EVM projects on chain

The chain, not the API, says which projects exist. `scripts/fxhash_evm_registry.py` reads
every fxhash EVM project factory (all deployed by 0x0E7De56c...) and writes
`data/fxhash-evm-registry.jsonl.gz`, one line per project contract with its minted token
count. On 2026-10-08:
- Ethereum: FxIssuerFactory 140, MediaFactory 3 (2026-02, beacon proxies to
  `CollectiveMedia`; 2 are named "Test Auction Mainnet"). 143 projects, 8,510 tokens.
- Base: FxIssuerFactory 559, ProjectFactory 20, ProjectFactoryV2 111, FxDopplerFactory 107
  (no registry: enumerated from its CREATE nonces). 797 projects, 851,207 tokens.
- Every API project is in a registry. 176 projects are only on chain (22 Ethereum, 154
  Base), and none of them has a minted token.
- The 122 tokens of the 3 MediaFactory projects have no seed in `fxhash_evm_seeds.py`
  output: those contracts have no `totalSupply()`, so the seeds script skipped them
  (`total_supply_reverted`); their count here comes from `nextTokenId()` and `ownerOf`.

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
