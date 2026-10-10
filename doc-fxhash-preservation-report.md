# fxhash preservation: what was preserved, how, and how much

State on 2026-10-10. Every number below comes from a data file in this repository
(`data/`), from the archive drive, or from the live forge manifest. The guiding rule
throughout: **everything starts from the chain**. A link counts as preserved only when the
bytes we hold hash back to a value stored in contract storage (Tezos big maps, EVM
contract storage), directly or through a document whose own hash is stored there.

## 1. Which projects exist: read from the chain

The fxhash API is used only as a cross-check.

| Chain | Contracts read | Projects | Tokens |
|---|---|---|---|
| Tezos | issuers v1, v2, v3; GENTK v1, v2, v3 | 27,432 active in the issuer ledgers, plus 3,088 burned (removed from the ledgers) | 1,845,633 (589,146 + 1,030,141 + 226,346) |
| Ethereum | FxIssuerFactory, MediaFactory | 143 | 8,510 |
| Base | FxIssuerFactory, ProjectFactory, ProjectFactoryV2, FxDopplerFactory | 797 | 851,207 |

- Scripts: `fxhash_tezos_registry.py`, `fxhash_evm_registry.py`, `fxhash_project_onchain.py`.
- Data: `data/fxhash-tezos-registry.json.gz`, `data/fxhash-evm-registry.jsonl.gz`,
  `data/fxhash-projects-onchain.jsonl.gz`.
- Every API project is in a chain registry. 176 EVM projects exist only on chain, none of
  them with a minted token.

## 2. Artwork code: a git forge for Software Heritage

One bare git repository per project, served read-only at
https://fxhash-preservation.rethread.art/ with a grokmirror manifest, so that Software
Heritage's Add Forge Now can crawl it.

- **28,193 repositories** (77 GB):
  - 27,596 converted from the archive drive (code originally on IPFS);
  - 589 rebuilt from ONCHFS, fxhash's on-chain file system, read from the chain itself
    (576 + 13; Tezos and EVM);
  - 8 active Tezos projects that the archive drive lacked, fetched from IPFS by their
    on-chain CID (6 listed by the API, 2 only on chain).
- Every commit is authored by the artist (on-chain name, `<wallet>@fxhash.xyz`) and dated
  at the project's creation on chain.
- 42 uploads that were themselves git repositories keep the artist's own history, with
  the uploaded working tree committed on top.
- `swhprep check` (a `git ls-remote` of every origin listed in the manifest) passes on
  the live forge.
- Tooling: the `swhprep` package (`convert`, `manifest`, `check`).

## 3. Code verified against the chain

For each project, the verifier compares the code hash anchored on chain with the hash
rebuilt from the git tree (`scripts/fxhash_links_verify.py`,
`data/fxhash-links-verify.jsonl.gz`). 31,284 projects (chain ∪ API), each with a verdict
and no unexplained case:

| Verdict | Projects |
|---|---|
| Git tree rebuilds the on-chain hash | **27,782** |
| Rebuilds once the uploaded `.git` folder is put back (README recipe) | 46 |
| Same files, but the hash cannot be rebuilt from git (accepted) | 47 (31 per-folder UnixFS modes, 16 empty folders) |
| Burned on chain (removed from the issuer ledger), no repo | 3,088 |
| Unverifiable: the contract stores only https URLs | 321 (318 have a repo, built from the archive) |

Details by storage:
- **IPFS:** 27,510 of 27,604 repos rebuild their CID (`scripts/ipfs_cid_all.py`,
  `data/ipfs-cid-all.jsonl.gz`):
  - 25,067 with plain `ipfs add -H`;
  - the others with UnixFS mode 0644 on folders (the 2021 zip uploads; `__MACOSX`
    excepted).
  - The 94 others are diagnosed one by one in `data/ipfs-cid-unmatched.jsonl`:
    47 uploads with a `.git` folder, 31 with per-folder modes, 16 with empty folders.
- **ONCHFS:** 589 of 589 repos rebuild their on-chain root (`scripts/onchfs.py`,
  `scripts/onchfs_repos.py`, `data/onchfs-repos.jsonl`):
  - file and directory hashes, gzip and HPACK metadata are reimplemented and checked on
    all 1,953 Tezos file inodes;
  - the EVM variant ("cheap" hashing, 24,000-byte chunks) is handled.
- **Cross-checks:** the chain and the API agree on 27,866 code links; the 5 that differ
  are explained (ONCHFS, versions).

Repairs made along the way:
- 9 repos where git had turned CRLF into LF because of the artist's `.gitattributes`:
  reconverted byte-exact, and the converter fixed for good.
- 1 incomplete copy on the archive drive (ALL SEEING): completed from IPFS by its
  on-chain CID.
- 8 `.git/index` files on the archive drive, refreshed by a `git status` during
  conversion: restored from IPFS.

## 4. Code versions of updated projects

20 Base contracts were updated after mint (`versionInfo`). Each version's code is
anchored on chain.
- 74 versions: each one dated at the block where `currentVersion()` reached it, by binary
  search on an archive node (`scripts/fxhash_evm_versions.py`).
- **72 versions are commits**, one per version and in order, each rebuilding its
  version's on-chain hash (`scripts/fxhash_version_history.py`,
  `scripts/fxhash_version_history_check.py`, `data/fxhash-evm-version-history.jsonl`).
- **2 versions are lost:** Kraswerk v2 (live 21 minutes) and SPACE WANDERER v2 (live
  2 hours). No IPFS provider was found, and Filebase returned 504. The next commit's
  message names each one.

## 5. Seeds (what makes each token unique)

- **EVM:** read from chain for all 859,717 tokens: Base 851,207 and Ethereum 8,510.
  Stored on the archive drive (`fxhash-seeds/`); script `scripts/fxhash_evm_seeds.py`.
- **Tezos:** each token's seed (`iterationHash`) is in its token metadata (see 7).

## 6. Project metadata documents

- **30,825 of 30,827** documents fetched and hash-checked against the CID stored on
  chain (archive drive: `fxhash-project-metadata.jsonl.gz`).
- The 2 others are stored on chain as `ipfs://undefined`: Tezos 28531 and 28534, never
  minted.

## 7. Token metadata

- **Tezos: 1,845,633 of 1,845,633** token documents, 0 errors.
  - Every token's `token_metadata` on chain is an `ipfs://` link.
  - Each document is accepted only if its bytes hash to that CID (fetched through
    rootmab's kubo node and Filebase; `scripts/tezos_metadata_fetch.py`, stored on
    tiramisu).
- **EVM:**
  - Only 149 contracts (13,130 tokens) have an `ipfs://` token URI. The other 516
    contracts (846,587 tokens) return `https://media.fxhash.xyz/…` URLs, which depend
    on fxhash's server and cannot be verified from chain.
  - From the 357 IPFS folders the contracts anchor: **10,219 token documents** fetched
    through a kubo node (every block hash-checked;
    `scripts/evm_token_metadata_fetch.py`; archive drive:
    `fxhash-evm-token-metadata.jsonl.gz`).

## 8. Images and previews: inventoried, not yet downloaded

Every IPFS link in the documents of sections 6 and 7 is inventoried
(`scripts/fxhash_link_inventory.py`; summaries in `data/inventory/`, CID lists on the
archive drive in `fxhash-inventory/`):

| Source | Documents | Distinct IPFS CIDs |
|---|---|---|
| Project documents | 30,825 | 94,289 |
| Tezos token documents | 1,845,633 | 3,912,934 (1.82 M display images, 1.82 M thumbnails, 296,076 per-token code folders) |
| EVM token documents | 10,219 | 21,501 |

Sizes are estimated from hash-checked root blocks of random samples
(`scripts/ipfs_size_sample.py`, 95% intervals):

| Category | Estimated size |
|---|---|
| Tezos display images | 3.55 TB (3.19–3.91) |
| Tezos thumbnails | 0.20 TB |
| Project previews | 0.05 TB |
| **Total** | **about 3.8 TB**, plus the unsized per-token code folders |

Download is waiting for a dedicated hard drive.

## 9. Displaying the artworks: the art gateway

https://fxhash.gakoy.com serves every artwork straight from the forge's git repos:
**27,975 artworks**, every version included. A token's `artifactUri` works there with its
scheme swapped: `ipfs://<cid>/?fxhash=…` becomes `https://fxhash.gakoy.com/ipfs/<cid>/?fxhash=…`.
- Each artwork gets its own origin (`<label>.fxhash.gakoy.com`).
- Each response says whether the tree rebuilds the on-chain hash.
- The page policy lets scripts run but blocks loading anything from other hosts.
- Render check, 200 random artworks in headless Chrome: 177 drew. None of the other 23
  failed because of the gateway.
- **1,115 artworks (4%) load libraries or fonts from outside servers**: cdnjs 423,
  openprocessing.org 253, jsDelivr 227, unpkg 132, Google Fonts about 60. One of these,
  cdn.rawgit.com (11 artworks), has already shut down.
  - Archived copies of those resources, served by the gateway, are built and tested
    (commit 56b5351) but not yet deployed.
- Public IPFS gateways can also break artworks: Filebase's policy blocks the inline
  fxhash snippet, so a page renders blank there.

## What is not preserved, and why

| Gap | Count | Reason |
|---|---|---|
| Burned projects | 3,088 | Removed from the issuer ledgers by their authors |
| Code of https-only EVM projects, checked against chain | 321 projects | Their contracts anchor nothing; 318 are kept as repos built from the archive |
| EVM token metadata and previews behind `media.fxhash.xyz` | 846,587 tokens | Server-only, not anchored on chain. These artworks can still be regenerated from code plus on-chain seed |
| Intermediate code versions | 2 | No copy left on IPFS |
| Images | about 3.8 TB | Inventoried, download pending |
