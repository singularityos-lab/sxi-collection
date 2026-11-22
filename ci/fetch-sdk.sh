#!/usr/bin/env bash
set -euo pipefail

arch=$1
out=$2
what=${3:-all}
config="$(dirname "$0")/sdk.json"
repository=$(jq -r .repository "$config")
release=$(jq -r .release "$config")
target=$(jq -r --arg a "$arch" '.arches[$a].target' "$config")
host=$(jq -r --arg a "$arch" '.arches[$a].host' "$config")
pinned=$(jq -r --arg a "$arch" '.arches[$a].sums' "$config")

mkdir -p "$out"
patterns=(-p "SHA256SUMS-$target" -p "sdk-abi-$target.json")
[ "$what" = abi ] || patterns+=(-p "sdk-$target.tar.zst.part*")
gh release download "$release" -R "$repository" "${patterns[@]}" -D "$out"
echo "$pinned  $out/SHA256SUMS-$target" | sha256sum -c --quiet
(cd "$out" && sha256sum -c --quiet --ignore-missing "SHA256SUMS-$target")
[ "$what" = abi ] && exit 0

mkdir -p "$host"
cat "$out/sdk-$target.tar.zst.part"* | zstd -dc | tar -xf - -C "$host"
rm "$out/sdk-$target.tar.zst.part"*
echo "[singularity] SDK $(cat "$host/share/sinty/sdk-build") for $target in $host"
