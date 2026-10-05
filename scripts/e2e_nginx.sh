#!/usr/bin/env bash
# End-to-end: convert synthetic folders (plain + one real git repo), write the manifest,
# serve with each shipped nginx site (generic and fxhash.gakoy.com, both including
# deploy/swh-git-locations.conf) under nginx + fcgiwrap in docker, then check + clone.
set -euo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
name=swhprep-e2e-$$
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null

mkdir -p "$work/src/g/p1" "$work/src/g/p2" "$work/src/g/withgit"
echo one > "$work/src/g/p1/a.txt"
echo two > "$work/src/g/p2/b.txt"
(cd "$work/src/g/withgit" && git init -q -b master && echo h > h.txt && git add h.txt \
    && git -c user.name=d -c user.email=d@x commit -qm hist && git branch side \
    && echo wip > wip.txt)
cd "$here"
uv run -q swhprep convert "$work/src" "$work/srv" --depth 2 \
    --author "E2E <e2e@example.org>" --date 1970-01-01T00:00:00+00:00 >/dev/null
uv run -q swhprep manifest "$work/srv" "$work/srv/manifest.js.gz"
chmod -R a+rX "$work"

# build with host networking (container DNS may be unavailable), run on the bridge
docker build -q --network host -t swhprep-e2e - >/dev/null <<'EOF'
FROM debian:stable-slim
RUN apt-get update -qq && apt-get install -y -qq nginx fcgiwrap spawn-fcgi git >/dev/null
CMD spawn-fcgi -s /run/fcgiwrap.socket -M 766 -u www-data -g www-data /usr/sbin/fcgiwrap \
    && exec nginx -g "daemon off;"
EOF

for site in nginx-swh-git.conf fxhash.gakoy.com; do
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" -p 127.0.0.1::80 \
        -v "$work/srv:/srv/git:ro" -v "$work/srv:/srv/fxhash-git:ro" \
        -v "$here/deploy/swh-git-locations.conf:/etc/nginx/snippets/swh-git-locations.conf:ro" \
        -v "$here/deploy/$site:/etc/nginx/sites-enabled/default:ro" \
        swhprep-e2e >/dev/null
    port=$(docker port "$name" 80 | head -1 | cut -d: -f2)
    url="http://127.0.0.1:$port/"
    for _ in $(seq 30); do
        curl -sf -o /dev/null "${url}manifest.js.gz" && break
        sleep 1
    done
    curl -sf -o /dev/null "${url}manifest.js.gz" || { docker logs "$name"; echo "$site: server down"; exit 1; }

    # conditional GET must give 304 (what the SWH lister sends on re-crawl)
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'If-Modified-Since: Fri, 01 Jan 2100 00:00:00 GMT' "${url}manifest.js.gz")
    [ "$code" = 304 ] || { echo "$site: expected 304, got $code"; exit 1; }
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'If-Modified-Since: Thu, 01 Jan 2015 00:00:00 GMT' "${url}manifest.js.gz")
    [ "$code" = 200 ] || { echo "$site: expected 200 for stale date, got $code"; exit 1; }

    uv run -q swhprep check "${url}manifest.js.gz" --forge-url "$url"
    rm -rf "$work/clone" "$work/clone2"
    git clone -q "${url}g/p1.git" "$work/clone"
    grep -qx one "$work/clone/a.txt"
    [ "$(git -C "$work/clone" log -1 --format=%ad --date=raw)" = "0 +0000" ]
    # mirrored history: original branches plus the working-tree snapshot
    git clone -q "${url}g/withgit.git" "$work/clone2"
    refs=$(git ls-remote --heads "${url}g/withgit.git" | awk '{print $2}' | sort | tr '\n' ' ')
    [ "$refs" = "refs/heads/master refs/heads/side refs/heads/swh-worktree-snapshot " ] \
        || { echo "$site: refs $refs"; exit 1; }
    [ "$(git -C "$work/clone2" log -1 --format=%s)" = hist ]
    # smart protocol, not dumb fallback
    GIT_TRACE_CURL=1 git ls-remote "${url}g/p2.git" 2>&1 \
        | grep -q 'application/x-git-upload-pack-advertisement'
    code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${url}g/p1.git/git-receive-pack")
    [ "$code" = 403 ] || { echo "$site: receive-pack not blocked: $code"; exit 1; }
done
echo "e2e OK"
