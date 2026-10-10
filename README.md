# fxhash preservation

Code for long term preservation of fxhash.

What was preserved, by category, with all the numbers: [doc-fxhash-preservation-report.md](doc-fxhash-preservation-report.md).
`$DRIVE` below is the archive drive (`/media/martin/63d87370-5401-42f9-a718-bead975d75d4`).

## Scripts

Git forge for Software Heritage:
- `swhprep/` (package, `swhprep convert|manifest|check`): plain folders → bare git repos →
  grokmirror manifest → check every origin SWH will crawl.
- `fetch_fxhash_meta.py`: snapshot of the fxhash API (`data/fxhash-meta.json.gz`).
- `fxhash_identities.py`: per-project author and date for `convert --identity-map`.
- `rewrite_commits.py`, `export_commits.py`, `apply_commits.py`: change commit identities
  on the server by shipping tip commits instead of packs.
- `e2e_nginx.sh`, `validate_reference.py`: end-to-end test with real nginx, and check of
  the manifest against grokmirror and SWH's own lister.

What exists on chain, and the links it anchors:
- `fxhash_tezos_registry.py`, `fxhash_evm_registry.py`: every project and token count,
  from the issuer contracts (Tezos) and the project factories (Ethereum, Base).
- `fxhash_project_onchain.py`: each project's on-chain links (`list`), and the documents
  they point to, fetched by hash (`fetch`).
- `fxhash_evm_seeds.py`: the seed of every EVM token, from chain.
- `fxhash_seed_canary.py`, `ipfs_cid_canary.py`: early sample checks (seeds, CIDs).
- `fxhash_evm_versions.py`: every `versionInfo` version of updated Base contracts, dated
  by binary search on an archive node.
- `evm_token_uris.py`: `tokenURI` of every token of given contracts (Multicall3).

Code rebuilt from git and checked against the chain:
- `ipfs_cid.py`, `ipfs_cid_all.py`: rebuild each repo's IPFS CID (plain, mode-0644
  variants).
- `ipfs_dag_dirs.py`, `ipfs_cid_diff.py`: why a repo does not rebuild its CID, from the
  published DAG.
- `onchfs.py`: read ONCHFS from Tezos, Ethereum and Base, verify every inode, and rebuild
  roots from plain files.
- `onchfs_sources.py`, `onchfs_repos.py`: ONCHFS projects as source folders, and the
  check that their repos rebuild the on-chain root.
- `fxhash_version_history.py`, `fxhash_version_history_check.py`: one commit per
  on-chain version, and the check that each commit rebuilds its version.
- `fxhash_links_verify.py`: every project's chain path and verdict (exits 1 on anything
  unexplained).

Metadata and images:
- `tezos_metadata_fetch.py` (+ `tezos_metadata_tunnel.sh`): all Tezos token metadata,
  hash-checked (run on tiramisu).
- `evm_token_metadata_fetch.py`: EVM token metadata under anchored folders or exact
  token URIs, through a kubo node.
- `fxhash_link_inventory.py`: every link in the fetched documents, by field.
- `ipfs_size_sample.py`: bytes behind a set of CIDs, from hash-checked root blocks of a
  random sample.
- `ipfs_provider_census.py`: who announces each CID (see
  [doc-howto-ipfs-independent-copies.md](doc-howto-ipfs-independent-copies.md)).

Art gateway:
- `art_gateway_index.py`, `art_gateway.py`: serve each artwork from git on its own host.
- `art_gateway_render_check.py`: render a random sample in headless Chrome.
- `art_external_refs.py`, `art_external_urls.py`: artworks that load from other servers,
  and the exact URLs.
- `ext_archive.py`: archive those outside resources for the gateway's `--ext`.

## Deployment: fxhash-preservation.rethread.art on rootmab

URL: `https://fxhash-preservation.rethread.art/` (DNS at Gandi: A record to
37.187.127.139). The forge's first name, `fxhash.gakoy.com`, was removed on 2026-10-06; since
2026-10-09 that name serves the art gateway (below).

Serving 28,193 repos (77 GB) as of 2026-10-10: the 27,596 converted from the archive drive
on 2026-10-06, 589 ONCHFS projects read from chain, and 8 projects the archive lacked.
`check` passed on every origin; the first upload took 98 min at ~14 MB/s and the full
check 43 min at 16 jobs. Later additions were shipped repo by repo, their manifest entries
merged into the live manifest, and checked. Already done: the snippet and vhost installed on
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
   - Comment: `About 28,200 git repositories, one per fxhash project, listed in
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

## Artist and date

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

## Projects on chain

The chain, not the API, says which projects exist.

`scripts/fxhash_tezos_registry.py` reads the three issuer registries and the three GENTK
contracts through TzKT and writes `data/fxhash-tezos-registry.json.gz`. Issuer v2 holds
copies of v1's projects under the same ids, so each project is counted in the latest
registry where it appears. On 2026-10-08: v1 0, v2 22,649, v3 4,783, so 27,432 projects;
9,660 v1 ids are active in v2, and the 20 others were burned in v2 by their authors.
Tokens: 589,146 + 1,030,141 + 226,346 = 1,845,633.

`scripts/fxhash_evm_registry.py` reads
every fxhash EVM project factory (all deployed by 0x0E7De56c...) and writes
`data/fxhash-evm-registry.jsonl.gz`, one line per project contract with its minted token
count. On 2026-10-08:
- Ethereum: FxIssuerFactory 140, MediaFactory 3 (2026-02, beacon proxies to
  `CollectiveMedia`; 2 are named "Test Auction Mainnet"). 143 projects, 8,510 tokens.
- Base: FxIssuerFactory 559, ProjectFactory 20, ProjectFactoryV2 111, FxDopplerFactory 107
  (no registry: enumerated from its CREATE nonces). 797 projects, 851,207 tokens.
- Every API project is in a registry. 176 projects are only on chain (22 Ethereum, 154
  Base), and none of them has a minted token.
- The 3 MediaFactory projects have no `totalSupply()`; their 122 tokens are the ids below
  `nextTokenId()` that have an owner, and `fxhash_evm_seeds.py` reads their seeds with
  `tokenInfo(id)` = (version, seed) (getter `tokenInfo(version,seed)`). Ethereum seeds:
  8,510, of which the 8,388 of the earlier run are unchanged.

## Code hash on chain, rebuilt from git

`scripts/ipfs_cid_all.py` hashes the tree of each repo's HEAD, minus `_source.txt`, with
`ipfs add -n` and compares it with the CID the chain anchors (`-H`, or UnixFS mode 0644 on
subfolders, see `scripts/ipfs_cid.py`). On 2026-10-10: 27,510 of the 27,604 IPFS repos
rebuild it; the 589 ONCHFS repos all rebuild their on-chain root (`scripts/onchfs_repos.py`).
The 94 left (`data/ipfs-cid-unmatched.jsonl`, from `scripts/ipfs_cid_diff.py`):

- 47 uploads contain a `.git` folder (44 at the top, 3 nested). Each repo is the artist's
  own history, mirrored from that folder, plus a commit for the uploaded working tree. The
  CID also covers the `.git` folder's bytes (objects, index, logs), which no clone
  reproduces, so it can't be rebuilt from the forge alone. To recompute it, put the
  uploaded `.git` folder back next to HEAD's files and hash the whole folder:

  ```sh
  git clone https://fxhash-preservation.rethread.art/<repo>.git work
  mkdir upload && git --git-dir=work/.git archive HEAD | tar -x -C upload/
  rm upload/_source.txt
  ipfs get <cid>/.git -o upload/.git      # each .git of the upload (3 are nested), or
                                          # the folders kept on the archive drive
  ipfs add -r -n -Q -H upload/            # = <cid>  (python3 scripts/ipfs_cid.py upload/
                                          #   for the mode-0644 variants)
  ```

  Checked on 2026-10-09 for all 47 from the archive drive's folders. 8 of them first had
  their `.git/index` restored from IPFS: a `git status` run during the conversion had
  refreshed it.
- 31 have per-folder UnixFS modes that no rule derives, and 16 have empty folders, which
  git can't store. Not rebuildable from git; accepted.

`scripts/fxhash_links_verify.py` gives every project, from the chain and the API (31,284),
its chain path and a verdict; it exits 1 on any unexplained one. On 2026-10-10:

```sh
python3 scripts/fxhash_links_verify.py data/fxhash-projects-onchain.jsonl.gz \
    $DRIVE/fxhash-project-metadata.jsonl.gz data/fxhash-api-generative-uri.json \
    data/ipfs-cid-all.jsonl OUT.jsonl --causes data/ipfs-cid-unmatched.jsonl \
    --onchfs data/onchfs-repos.jsonl --versions data/fxhash-evm-versions.jsonl \
    --history data/fxhash-evm-version-history.jsonl
```

- 27,782 git reproduces the chain's hash (27,193 IPFS, 589 ONCHFS); 46 with the uploaded
  `.git` folder (above); 47 accepted differences (above).
- 3,088 burned: removed from the issuer ledger, no repo.
- 321 unverifiable on chain: the contract names only https URLs (318 have a repo built from
  the archive, 3 have none).
- 8 active Tezos projects that the archive lacked are repos built from IPFS by their
  on-chain CID (since 2026-10-09).
- 20 Base projects were updated after mint (`versionInfo`, `scripts/fxhash_evm_versions.py`,
  each version dated at the block where `currentVersion()` reached it). Their repos have one
  commit per version, dated then (`scripts/fxhash_version_history.py`), and every commit
  rebuilds its version's on-chain hash (`scripts/fxhash_version_history_check.py`,
  `data/fxhash-evm-version-history.jsonl`). 2 versions have no commit: Kraswerk v2 and
  SPACE WANDERER v2, short-lived (21 min, 2 h), anchored on chain but with no copy on IPFS
  on 2026-10-09 (no providers, Filebase 504); the next commit's message names them.

## Every other link: inventory (2026-10-09)

All from documents whose CID the chain anchors, each fetched by hash:
`scripts/fxhash_link_inventory.py` (summaries in `data/inventory/`, the CID lists on the
archive drive in `fxhash-inventory/`).
- Project documents: 30,825, 94,289 distinct IPFS CIDs (display image, thumbnail, code).
- Tezos token documents: all 1,845,633 (token_metadata on chain is `ipfs://` for every
  token of the three GENTK contracts), 3,912,934 distinct IPFS CIDs: ~1.82 M display
  images, ~1.82 M thumbnails, 296,076 artifact folders (per-token code folders of early
  tokens; not sized), 28,531 ONCHFS artifacts.
- EVM token documents: 10,219 fetched under the 357 anchored folders
  (`scripts/evm_token_metadata_fetch.py`, run next to rootmab's kubo), 21,501 distinct
  CIDs. That is every `ipfs://` token document that exists. Judged by token 1, 149
  contracts have an `ipfs://` tokenURI and 516 (846,587 tokens) return
  `https://media.fxhash.xyz/…` (server only). But a contract can switch: in the 41 Base
  contracts read token by token (`scripts/evm_token_uris.py`), early tokens point to IPFS
  and later ones to fxhash's server, and 337 `ipfs://` token URIs point to paths that
  never existed (`data/evm-dangling-token-uris.jsonl`).
- Size, from hash-checked root blocks of random samples (`scripts/ipfs_size_sample.py`,
  `data/inventory/*sizes.jsonl`, 95% intervals): Tezos token display images 3.55 TB
  (3.19–3.91), thumbnails 0.20 TB (0.199–0.210); project display images 0.05 TB
  (0.04–0.06), thumbnails 0.004 TB. In all about 3.8 TB, plus the unsized artifact
  folders and ~0.02 TB of EVM images.

## Art gateway: fxhash.gakoy.com

Displays any artwork from the forge's repos, without IPFS. A token's `artifactUri` with
its scheme swapped:

```
ipfs://<cid>/?fxhash=...    -> https://fxhash.gakoy.com/ipfs/<cid>/?fxhash=...
onchfs://<root>/?fxhash=... -> https://fxhash.gakoy.com/onchfs/<root>/?fxhash=...
```

redirects to `https://<label>.fxhash.gakoy.com/?fxhash=...` (label: CIDv1 base32, or `o` +
base32 of the ONCHFS root; one host per artwork, so artworks share no storage).
`scripts/art_gateway.py` serves `<commit>:<path>` of the mapped repo straight from git;
`scripts/art_gateway_index.py` builds the map (every commit with a `_source.txt` on HEAD's
first-parent line, so version histories serve each version). Header `X-Fxhash-Verified`:
`rebuilds` (the tree rebuilds that hash) or `files equal: <cause>`. The page policy runs
inline and evaluated scripts but loads nothing from other hosts, so an artwork that pulls
a library from a CDN shows up broken: it depends on that server. Public IPFS gateways
fail differently: Filebase's `default-src 'self'` blocks the inline fxhash snippet.

```sh
python3 scripts/art_gateway_index.py $DRIVE/fxhash-git index.json.gz \
    --cidall data/ipfs-cid-all.jsonl --onchfs data/onchfs-repos.jsonl \
    --causes data/ipfs-cid-unmatched.jsonl --versions-checked
python3 scripts/art_gateway.py index.json.gz $DRIVE/fxhash-git --domain art.localhost
# render check in headless Chrome over a random sample (needs Pillow)
python3 scripts/art_gateway_render_check.py index.json.gz examples.json.gz 200 out/
```

Deployment on rootmab: `deploy/fxhash.gakoy.com` (nginx, wildcard cert by DNS-01, see
`../rootmab/gakoy.md`), `deploy/fxhash-art-gateway.service`.

Findings (2026-10-09):
- Render check, 200 random artworks through the live gateway, headless Chrome, 8 s:
  177 drew (88.5%), 12 blank, 11 timeouts (`data/art-render-sample-200.json`). No
  failure came from the gateway: re-rendered without it, 15 fail the same way, and 8
  differ from run to run (e.g. SunFlower draws in `onload` callbacks and loses a race in
  headless Chrome under any server; 3 of 3 later runs drew through the gateway).
- 1,115 of the 27,975 artworks (4%) load resources from other hosts in their HTML
  (`scripts/art_external_refs.py`, `data/art-external-refs.jsonl`): cdnjs.cloudflare.com
  423, openprocessing.org 253, cdn.jsdelivr.net 227, unpkg.com 132, Google Fonts ~60,
  cdn.rawgit.com 11 (already shut down). They depend on those servers, and the gateway's
  policy shows them broken.

Archived outside resources (built and tested, not deployed yet): `art_external_urls.py`
lists the exact URLs, `ext_archive.py` fetches each once (checking the page's
`integrity=` hash when it has one, and following font CSS to the font files), and
`art_gateway.py --ext ARCHIVE` serves them at `/_ext/<host>/<path>`, rewriting exactly
the archived URLs in the HTML, CSS and JS it serves (`X-Fxhash-Rewritten`; git trees
untouched).

## License

MIT
