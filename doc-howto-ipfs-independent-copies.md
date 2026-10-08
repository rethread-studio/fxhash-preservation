# How to estimate the number of independent copies of IPFS content

Being retrievable today only shows that *someone* serves a CID. Preservation risk
depends on *how many independent parties* hold it. This is how to measure that from
public data, without downloading anything and without load on our own node.

## What can be observed

A node that holds content can announce it as a **provider record**. Two public
systems collect them:

- the **DHT** (Amino), filled by ordinary kubo nodes (`ipfs routing provide`);
- the **network indexers** (IPNI, `cid.contact`), where large pinning services
  (Filebase, Pinata, web3.storage, …) announce in bulk, often not in the DHT at all.

The delegated routing HTTP API (`/routing/v1`) answers from both at once;
`https://delegated-ipfs.dev` is a public instance.

## Method

1. **Providers of a CID**

   ```sh
   curl -s -H 'accept: application/json' \
     https://delegated-ipfs.dev/routing/v1/providers/<CID> | jq -r '.Providers[].ID' | sort -u
   ```

   One record per (peer, protocol); the same peer can appear twice, e.g. once from the DHT
   and once from the indexer. Count distinct `ID`s.

2. **Addresses of each provider peer**

   ```sh
   curl -s -H 'accept: application/json' \
     https://delegated-ipfs.dev/routing/v1/peers/<PEER_ID> | jq -r '.Peers[].Addrs[]'
   ```

   Keep public `/ip4/…` and `/ip6/…` addresses and DNS names (`/dns4/bitswap.filebase.io/…`
   names the operator directly). Peers announced only through an indexer often publish no
   address: keep them as "unknown operator".

3. **Operator of each address**: `whois <IP>` (org name, AS) and the DNS name. Names
   ending in `.libp2p.direct` are kubo's AutoTLS certificates: a self-hosted kubo node,
   not a service.

4. **Group peers into organisations.** One organisation usually runs many peers (a
   pinning service, a cluster). The figure that matters is **distinct organisations per
   CID**, not records or peers.

5. **Sample, per kind of content.** For a collection, take a few hundred random CIDs per
   kind (code folders, token metadata, rendered images), so the cost stays small
   (2 HTTP requests per CID plus 1 per new peer) and the error bars are known. Report:
   - the distribution of organisations per CID;
   - the share of CIDs held by **one** organisation only (the at-risk set);
   - the share with **no** provider record (not findable by routing at all);
   - which organisations hold most of the collection (shared single point of failure).

6. **Cross-check with retrieval.** Records can be stale. A fetch through our own node
   (`curl http://127.0.0.1:8086/ipfs/<CID>` on rootmab, or the tunnel from tiramisu)
   shows the CID is actually served by someone; `ipfs routing findprovs <CID>` on our
   node gives the DHT view alone.

## What this does not tell

- **Announcing is not holding.** Records expire after ~48 h if not re-announced, so a
  record means "held at most two days ago".
- **Holding is not announcing.** A node can keep content without providing it (rootmab's
  node announces only what it pins: `Provide.Strategy = pinned+mfs+unique`). Such copies
  are invisible here; the count is a lower bound for private copies and an upper bound
  for durable public ones.
- **Caches look like copies.** A kubo node with the default provide strategy announces
  everything it fetched until garbage collection. A peer seen once, on a few CIDs, may be
  a cache rather than a keeper.
- **Organisation attribution is heuristic.** It rests on IP ranges, DNS names and
  `whois`. Hosting providers (OVH, Hetzner) say where a node runs, not who runs it.
- **Independence also covers money and policy.** Five peers paid by the same account
  disappear together. Providers of unrelated CIDs all drawn from one small pool is the
  signature of one pinning arrangement (see `note-ipfs-systematic-pinning-fxhash.md`).

## Tooling

`scripts/ipfs_provider_census.py SAMPLE.jsonl OUT.json` runs steps 1 to 3 on a sample
(`{"kind", "cid", "ref"}` per line) and prints, per kind, the distribution of peers per
CID, the peers ranked by coverage with their operator, and the number of distinct provider
sets. 100 CIDs take about 1.5 min.

## References

- Routing V1 HTTP API: https://specs.ipfs.tech/routing/http-routing-v1/
- IPNI: https://docs.ipfs.tech/concepts/ipni/
- Provider records and their lifetime: https://docs.ipfs.tech/concepts/dht/
- kubo `Provide.Strategy`: https://github.com/ipfs/kubo/blob/master/docs/config.md
