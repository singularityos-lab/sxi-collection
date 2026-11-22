#!/usr/bin/env bash
set -euo pipefail

arch=$1
out=$2
config="$(dirname "$0")/sdk.json"
channel=$(jq -r --arg a "$arch" '.arches[$a].stable' "$config")
suffix=$(jq -r --arg a "$arch" '.arches[$a].suffix' "$config")

mkdir -p "$out"
curl -fsS --retry 3 "https://updates.sinty.dev/$channel/manifest.json" -o "$out/manifest.json"
version=$(jq -r .version "$out/manifest.json")
[[ "$version" =~ ^v[0-9]+$ ]]
gh release download "$version$suffix" -R singularityos-lab/sinty-updates \
  -p files.json.zst -p 'SHA256SUMS-*.txt' -D "$out"
(cd "$out" && grep -h ' files\.json\.zst$' SHA256SUMS-*.txt | sha256sum -c --quiet)
zstd -qdc "$out/files.json.zst" > "$out/files.json"
echo "[singularity] stable for $arch is $version"
