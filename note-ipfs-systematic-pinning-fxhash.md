# Note: fxhash content on IPFS is held by one fixed pool of providers (2026-10-08)

## Finding

On a stratified random sample of **100 fxhash CIDs** (40 Tezos token metadata documents,
30 project code folders, 17 display renders, 13 thumbnails), the providers of every CID
are drawn from **one fixed pool of 8 peers**, and a **core of 3 of them announces all 100
CIDs**, whatever the kind of content, project or year. Outside the pool, only 5 rare peers
appear, each on 1 to 6 CIDs. This is the signature of **one systematic pinning
arrangement** set up for everything fxhash published, not of independent holders
(collectors, artists) keeping copies.

A first sample of 21 CIDs (20 metadata + 1 code folder) showed an *identical* set of 6
peers on every CID. The larger sample refines that: provider sets vary (17 distinct sets,
the most common covering 19 CIDs), but always within the same pool.

## Data

Method: `doc-howto-ipfs-independent-copies.md`, implemented by
`scripts/ipfs_provider_census.py` (delegated routing at `delegated-ipfs.dev`, which merges
the DHT and the IPNI indexers; then peer addresses and `whois`).
Sample: `data/ipfs-provider-sample-2026-10-08-n100.jsonl` (drawn with seed 20261008:
metadata from the on-chain token lists, renders from fetched metadata, code from the
fxhashArchive catalog). Full result: `data/ipfs-provider-census-2026-10-08.json`.
First sample: `data/ipfs-provider-sample-2026-10-08.txt`.

CIDs announced per peer, by kind (n = 40 metadata / 30 code / 17 display / 13 thumbnail):

| peer | metadata | code | display | thumb | protocol | where |
|---|---|---|---|---|---|---|
| `12D3KooWGtYkBAaqJMJEmywMxaCiNP7LCEFUAFiLEBASe232c2VH` | 40 | 30 | 17 | 13 | bitswap | `bitswap.filebase.io` (**Filebase**) |
| `12D3KooWDcaZhDdXNP1rnFV8WcaPPwc1Z2pNs3C9KkTm2ATQscsi` | 40 | 30 | 17 | 13 | gateway-http | no address (IPNI only) |
| `12D3KooWEVD8HsXX9HhxteZffZG1jwjLcuwPHSRG8h9LPLMSHttP` | 40 | 29 | 17 | 13 | gateway-http | no address (IPNI only) |
| `QmbXFYQspgaCQEt3se5SbLpYEUsgGgFKb89T9vwh5warS3` | 14 | 17 | 17 | 13 | gateway-http | no address (IPNI only) |
| `Qma8ddFEQWEU8ijWvdxXm3nxU7oHsRtCykAaVz8WUYhiKn` | 14 | 17 | 17 | 13 | bitswap | no address (IPNI only) |
| `12D3KooWBYznKkpGnjj2JSdbbV4nSgomi4eDSDyhfbg7962HQDD1` | 28 | 17 | 9 | 11 | bitswap | kubo, 37.187.57.219, OVH SAS (FR) |
| `12D3KooWMTZBc71zEPEZ1VFWPYRMxj748oiotBGu9b3nsY17skMQ` | 27 | 19 | 10 | 8 | bitswap | kubo, 65.109.148.176, Hetzner (FI) |
| `12D3KooWAJJJwXsB5b68cbq69KpXiKqQAgTKssg76heHkg6mo2qB` | 25 | 10 | 14 | 6 | bitswap | kubo, 148.113.159.218, OVH Hosting (US/CA) |
| 5 other peers | 6 | 4 | 2 | 0 | bitswap | DigitalOcean, Hetzner (DE), Namecheap, 2 unknown |

Peers per CID: minimum 3, typically 5 (metadata) to 7 (code, renders).

Same day, retrieval through our own node (rootmab kubo, via tiramisu) succeeded for
99.994% of the token metadata it was asked for (255,075 / 255,091), so the content is
not only announced but served.

## Interpretation

- **Few operators.** The 8 pool peers probably belong to 3 to 5 organisations: Filebase;
  the two address-less HTTP providers present on every CID (possibly Filebase's or another
  pinning service's HTTP retrieval, not verifiable from routing data); the RSA-key pair
  (one bitswap and one HTTP peer with identical coverage: one service, holding all renders
  but only a third of the metadata); and the three self-hosted kubo nodes (same software,
  similar coverage, possibly one operator, possibly fxhash).
- **Correlated failure.** If the arrangement behind the pool ends (unpaid pinning
  accounts, fxhash's own nodes switched off), most copies of most CIDs disappear together,
  for the whole collection at once. Independent holders exist but are rare: 5 peers over
  100 CIDs, each on at most 6. The fxhashArchive README already names IPFS pins, not the
  chain, as the long-term risk; this sample shows how concentrated they are.
- **Renders are the best covered** (7 peers typically, all in the RSA pair), **metadata
  the least** (5 typically; the RSA pair holds only 35% of it).
- **Not visible here:** copies that are not announced (collectors' local nodes, our own
  rootmab cache) and copies on Arweave or plain HTTP.

## Limits of this note

100 CIDs, Tezos only (no EVM content), one day. Operator attribution rests on DNS names,
IP ranges and `whois`; hosting provider is not operator. A provider record means "held at
most ~48 h ago", not "held now". Re-run `scripts/ipfs_provider_census.py` on the same
sample later to see the pool shrink or grow.
