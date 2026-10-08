# Note: fxhash content on IPFS is held by one fixed set of providers (2026-10-08)

## Finding

On a random sample of 21 fxhash CIDs (20 Tezos token metadata documents of contract
`KT1U6EHmNxJTkvaWJ4ThczG4FSDaHC21ssvi`, plus the code folder of project 30667 "Bioz"),
**every CID had about 5 provider records, and all came from the same 6 peers.** Unrelated
files (different tokens, different years, metadata and code) share an identical provider
set. That is the signature of **one systematic pinning arrangement**, almost certainly set
up by fxhash for everything it published, not of independent collectors keeping copies.

## Data

Method: `doc-howto-ipfs-independent-copies.md` (delegated routing, `delegated-ipfs.dev`,
which merges the DHT and the IPNI indexers; then peer addresses and `whois`). Sample CIDs:
`data/ipfs-provider-sample-2026-10-08.txt`.

Distinct provider peers per CID: 5 for 20 CIDs, 4 for one. Across the whole sample:

| peer | records | addresses | operator |
|---|---|---|---|
| `12D3KooWGtYkBAaqJMJEmywMxaCiNP7LCEFUAFiLEBASe232c2VH` | 41 (2 per CID) | `bitswap.filebase.io`, 57.129.37.40 | **Filebase** (pinning service) |
| `12D3KooWEVD8HsXX9HhxteZffZG1jwjLcuwPHSRG8h9LPLMSHttP` | 21 | none published | unknown (indexer only) |
| `12D3KooWDcaZhDdXNP1rnFV8WcaPPwc1Z2pNs3C9KkTm2ATQscsi` | 21 | none published | unknown (indexer only) |
| `12D3KooWMTZBc71zEPEZ1VFWPYRMxj748oiotBGu9b3nsY17skMQ` | 17 | 65.109.148.176, `libp2p.direct` | self-hosted kubo at Hetzner (FI) |
| `12D3KooWAJJJwXsB5b68cbq69KpXiKqQAgTKssg76heHkg6mo2qB` | 14 | 148.113.159.218, `libp2p.direct` | self-hosted kubo at OVH Hosting (US/CA) |
| `12D3KooWBYznKkpGnjj2JSdbbV4nSgomi4eDSDyhfbg7962HQDD1` | 10 | 37.187.57.219, `libp2p.direct` | self-hosted kubo at OVH SAS (FR) |

Same day, retrieval through our own node (rootmab kubo, via tiramisu) succeeded for
99.994% of the token metadata it was asked for (255,075 / 255,091), so the content is
not only announced but served.

## Interpretation

- **2 to 5 independent copies**, depending on whether the self-hosted nodes and the two
  address-less peers belong to fxhash or to someone else. Hosting provider ≠ operator: the
  Hetzner and OVH nodes say where a machine runs, not who pays for it.
- **Correlated failure.** If the arrangement behind this set ends (an unpaid Filebase
  account, fxhash's own nodes switched off), most copies can disappear together, for the
  whole collection at once. The fxhashArchive README already names IPFS pins, not the
  chain, as the real long-term risk; this sample shows how concentrated they are.
- **Not visible here:** private copies that are not announced (e.g. collectors' local
  nodes, our own rootmab cache) and copies on Arweave or plain HTTP.

## Limits of this note

21 CIDs, one contract, two kinds of content; no rendered images (display/thumbnail URIs)
and no EVM content. Before relying on it, run the census of the how-to per kind of
content (a few hundred CIDs each) and record the share of CIDs with a single organisation
and with no provider at all.
