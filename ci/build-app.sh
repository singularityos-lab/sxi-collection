#!/usr/bin/env bash
set -euo pipefail

id=$1
arch=$2
out=$3
here=$(cd "$(dirname "$0")" && pwd)
app=$(jq -ce --arg id "$id" '.[] | select(.id == $id)' "$here/apps.json")
repo=$(jq -r .repo <<<"$app")
commit=$(jq -r .commit <<<"$app")
target=$(jq -r --arg a "$arch" '.arches[$a].target' "$here/sdk.json")
host=$(jq -r --arg a "$arch" '.arches[$a].host' "$here/sdk.json")
sysroot="$host/$target-buildroot-linux-gnu/sysroot"
prefix="/run/sxi/apps/$id"
work=$(mktemp -d "$RUNNER_TEMP/app.XXXXXX")

fetch() {
  git init -q "$3"
  git -C "$3" fetch -q --depth 1 "https://github.com/singularityos-lab/$1" "$2" </dev/null
  git -C "$3" checkout -q --detach FETCH_HEAD
  git -C "$3" submodule update -q --init --recursive --depth 1 </dev/null
}

fetch "$repo" "$commit" "$work/src"
options=()
while read -r name rev; do
  mkdir -p "$work/src/subprojects"
  fetch "$name" "$rev" "$work/src/subprojects/$name"
  if [ "$name" = libsingularity ]; then
    options+=(-Dlibsingularity:system=false -Dlibsingularity:introspection=false)
    options+=(-Dlibsingularity:sharing-host=false -Dlibsingularity:iride=enabled)
  fi
done < <(jq -r '.subprojects[]? | "\(.repo) \(.commit)"' <<<"$app")

export PATH="$host/bin:$PATH"
export STAGING_DIR="$sysroot"
printf "[binaries]\nvala = '%s'\n" "$host/bin/valac" > "$work/vala.ini"
meson setup "$work/build" "$work/src" \
  --cross-file "$host/etc/meson/cross-compilation.conf" --cross-file "$work/vala.ini" \
  --prefix "$prefix" --libdir lib --buildtype release -Diride=enabled \
  "-Dc_link_args=-Wl,-rpath,$prefix/lib" "${options[@]}"
ninja -C "$work/build"
DESTDIR="$work/stage" meson install -C "$work/build" --no-rebuild --skip-subprojects

payload="$work/stage$prefix"
rm -rf "$payload/include" "$payload/lib/pkgconfig" "$payload/share/vala" "$payload/share/locale"
while IFS= read -r -d '' link; do
  real=$(readlink -f "$link")
  case "$real" in
    "$(readlink -f "$payload")"/*) cp --remove-destination "$real" "$link" ;;
    *) echo "$link points outside the application"; exit 1 ;;
  esac
done < <(find "$payload" -type l -print0)
find "$payload" -mindepth 1 -type d -empty -delete
[ -n "$(ls "$payload/bin")" ] || { echo "$id installs no binary"; exit 1; }
[ -f "$payload/share/icons/hicolor/scalable/apps/$id.iride" ] || { echo "$id installs no Iride icon"; exit 1; }

mkdir -p "$out"
version=$(meson introspect --projectinfo "$work/build" | jq -r .version)
[ "$version" != undefined ] || version=0.1.0
jq -n --arg version "$version" '{version: $version}' > "$out/info.json"
tar -cf "$out/payload.tar" --owner=0 --group=0 --numeric-owner -C "$payload" .
