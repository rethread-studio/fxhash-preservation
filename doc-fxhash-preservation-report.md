# fxhash preservation by category

State on 2026-10-10. Projects are grouped by chain and by where their code is anchored on
chain:
- **IPFS:** a CID in contract storage, or in a document whose CID is in storage.
- **ONCHFS:** fxhash's on-chain file system.
- **https only:** the contract stores nothing content-addressed.

Every number comes from `data/` in this repository, from the archive drive, or from the
live forge manifest. Rule: a link counts as preserved only if the bytes we hold hash back
to a value the chain stores.

Columns of the overview:
- **Projects:** from the chain registries.
- **Tokens:** minted.
- **Repos:** git repos on the forge (https://fxhash-preservation.rethread.art/).
- **Code verified:** the git tree rebuilds the hash on chain.
- **Token metadata:** documents we hold, each hash-checked against its on-chain link.
- **Seeds:** the per-token value that makes each output unique, as held.
- **Gateway:** artworks served by https://fxhash.gakoy.com.

## Overview

| Category | Projects | Tokens | Repos | Code verified | Token metadata | Seeds | Gateway |
|---|---|---|---|---|---|---|---|
| 1. Tezos + IPFS | 27,060 | 1,817,102 | 27,060 | 26,967 (+46 with `.git`, 47 equal files) | 1,817,102 | in metadata | 27,060 |
| 2. Tezos + ONCHFS | 372 | 28,531 | 372 | 372 | 28,531 | in metadata | 372 |
| 3. Ethereum + IPFS | 8 | 639 | 8 | 8 | 639 | 639 from chain | 8 |
| 4. Ethereum + ONCHFS | 50 | 5,605 | 50 | 50 | 1,935 | 5,605 from chain | 50 |
| 5. Ethereum + https only | 63 | 2,266 | 60 | unverifiable | 0 (server only) | 2,266 from chain | 60 |
| 6. Base + IPFS | 274 | 21,567 | 274 | 274 | 6,810 | 21,567 from chain | 274 |
| 7. Base + ONCHFS | 111 | 643,334 | 111 | 111 | 835 | 643,334 from chain | 111 |
| 8. Base + https only | 258 | 186,306 | 258 | unverifiable | 0 (server only) | 186,306 from chain | 258 |
| Tezos burned | 3,088 | — | 0 | — | — | — | — |
| **Total** | **31,284** | **2,705,350** | **28,193** | **27,782** | **1,855,852** | | **28,193** |

Totals:
- Tokens: 1,845,633 on Tezos plus 859,717 on EVM.
- Code verified: also 46 Tezos projects that rebuild once their uploaded `.git` folder is
  put back, and 47 with equal files that git can't rebuild exactly.
- Gateway: 28,193 repos behind 27,975 distinct code hashes, plus every older code
  version.
- Not in the table: 176 EVM projects that exist only on chain and have no minted token.

## 1. Tezos + IPFS

- **Projects:** 27,060 active, read from the issuer v2 and v3 ledgers.
- **Code:**
  - 27,060 repos: 27,052 from the archive drive and 8 that the archive lacked, fetched
    from IPFS by their on-chain CID.
  - 26,967 rebuild their on-chain CID exactly: 25,067 with plain `ipfs add -H`, the rest
    with UnixFS mode 0644 on folders (the 2021 zip uploads).
  - 46 rebuild it once the uploaded `.git` folder is put back (README recipe).
  - 47 hold the same files but can't be rebuilt from git: 31 have per-folder modes and 16
    have empty folders.
  - The on-chain link is the codex entry (issuer v3) or the `generativeUri` of the
    project document whose CID the ledger stores (issuer v2). 69 v3 codex entries are
    malformed (`ipfs://<hex>`); their project document has the right link.
- **Repairs:**
  - 9 repos lost CRLF line endings to the artist's `.gitattributes`; reconverted.
  - 1 incomplete drive copy (ALL SEEING) completed from IPFS.
  - 8 `.git/index` files restored from IPFS.
- **Tokens:**
  - 1,817,102 token metadata documents, all `ipfs://` on chain, all fetched and
    hash-checked, 0 errors. Each holds the token's seed (`iterationHash`) and its image
    links.
  - Images: about 1.82 M display images (3.55 TB, sampled) and 1.82 M thumbnails
    (0.20 TB), both shared with category 2; plus 296,076 per-token copies of the code
    folder (early tokens). Inventoried, not yet downloaded.
- **Gateway:** 1,066 artworks load libraries or fonts from outside servers (CDNs).

## 2. Tezos + ONCHFS

- **Projects:** 372. Their code is stored on chain (Tezos ONCHFS contracts).
- **Code:** 372 repos, all **read from the chain itself**: every file and directory hash
  is checked, and every repo rebuilds its on-chain root from git. These projects were
  missing from the source archive entirely.
- **Tokens:** 28,531 token documents (`artifactUri` = `onchfs://…`), hash-checked, with
  seeds; images on IPFS, inventoried with category 1.
- **Gateway:** 372; 10 load from outside servers.

## 3. Ethereum + IPFS

- **Projects:** 8. Code link = the `generativeUri` of the versioned contract's IPFS
  project document, or of token metadata under its IPFS base URI.
- **Code:** 8 repos, 8 rebuild the on-chain CID.
- **Tokens:** 639. All have an `ipfs://` token URI; all 639 metadata documents fetched
  through a kubo node (every block hash-checked). Seeds read from chain for all 639.

## 4. Ethereum + ONCHFS

- **Projects:** 50. The contract's `onchainPointer` (an SSTORE2 contract) holds the ONCHFS
  root of the code.
- **Code:** 50 repos, read from Ethereum's ONCHFS contracts (chunks read one by one,
  since `readFile` runs out of gas); all 50 rebuild their root.
- **Tokens:** 5,605, seeds read from chain for all of them.
  - 1,935 have an `ipfs://` token URI, and their metadata is fetched and hash-checked.
  - 3,670 have an `https://media.fxhash.xyz` token URI: their metadata lives only on
    fxhash's server.

## 5. Ethereum + https only

- **Projects:** 63. The contract stores no content hash, only https URLs.
- **Code:** 60 repos, built from the archive drive. They match the archive's and the
  API's CID but cannot be checked against chain. 3 have no repo: the MediaFactory
  projects (2 named "Test Auction Mainnet", 1 "Deliverance").
- **Tokens:** 2,266, seeds from chain. Metadata is server only.

## 6. Base + IPFS

- **Projects:** 274: an IPFS base URI in storage, or versioned contracts (`versionInfo`).
- **Code:** 274 repos, all rebuild the on-chain CID.
- **Versions:** 20 contracts were updated after mint, with 74 versions in all.
  - 72 versions are commits, each dated at the block where the version went live and
    each rebuilding its own on-chain hash.
  - 2 versions have no copy left on IPFS: Kraswerk v2 (live 21 minutes) and
    SPACE WANDERER v2 (live 2 hours).
- **Tokens:** 21,567, seeds from chain for all.
  - 9,721 have an `ipfs://` token URI; 6,810 metadata documents fetched from the
    anchored folders. The remaining 2,911 are still to fetch.
  - 11,846 tokens of versioned contracts have an https token URI, even though the
    project's code is anchored on IPFS.

## 7. Base + ONCHFS

- **Projects:** 111, with code in Base's ONCHFS through `onchainPointer`.
- **Code:** 111 repos read from chain; all rebuild their root.
- **Tokens:** 643,334, the largest EVM group; seeds from chain for all.
  - 835 have an `ipfs://` token URI, with metadata fetched.
  - 642,499 have an `https://media.fxhash.xyz` token URI: server only.
  - Every one can still be regenerated from on-chain code plus on-chain seed.

## 8. Base + https only

- **Projects:** 258, with no content hash on chain.
- **Code:** 258 repos from the archive drive, not verifiable against chain.
- **Tokens:** 186,306, seeds from chain. Metadata is server only.

## Burned (Tezos)

3,088 projects were removed from the issuer ledgers by their authors. Not preserved, by
decision ("they are burned").

## Across categories

- **Project documents:** 30,825 of 30,827 hash-checked. The 2 others are `ipfs://undefined`
  on chain (never minted).
- **Images:** about 3.8 TB in all, inventoried by CID:
  - Tezos token images: 3.55 TB display, 0.20 TB thumbnails;
  - project previews: 0.05 TB;
  - EVM: 21,501 CIDs.

  Download is waiting for a dedicated drive.
- **Outside servers:** 1,115 artworks load from cdnjs (423), openprocessing.org (253),
  jsDelivr (227), unpkg (132), Google Fonts (~60) or cdn.rawgit.com (11, already dead).
  Archived copies served by the gateway are built and tested, not yet deployed.
- **Gateway render check:** 177 of 200 random artworks drew in headless Chrome. None of
  the other 23 failed because of the gateway.
