# Singularity Collection

The curated applications for SintyOS. `sxid` on every SintyOS system reads
this collection, checks its signature against the release root of the system
and offers its applications in the Store with the Singularity Collection badge.

- `index.json`: the applications and their releases.
- `index.json.cose`: the detached signature of `index.json`.
- `index.certs`: the delegation from the release root to the key that signs
  the index and the releases.

Application images are attached to the releases of this repository. Every
image is listed in the index with its size, SHA-256, dm-verity root hash and
slot ID, so a system only installs exactly what the signed index names.

Publishing a release:

```sh
sxi pack -roots roots -o app-1.sxi App.sxi > app-1.json
sxi index add -index index.json -pack app-1.json -url URL -name App
sxi index sign -index index.json -key signing.key -certs index.certs -root root.pub
```
