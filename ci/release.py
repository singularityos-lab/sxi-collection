#!/usr/bin/env python3

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import gate

HERE = Path(__file__).resolve().parent
ARCHES = ("amd64", "arm64")
REPOSITORY = "singularityos-lab/sxi-collection"
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,31}$")


def load(path: Path):
    return json.loads(path.read_text())


def wanted(names: str) -> list[dict]:
    apps = load(HERE / "apps.json")
    chosen = set(names.split())
    unknown = chosen - {app["id"] for app in apps}
    if unknown:
        raise SystemExit(f"unknown applications: {' '.join(sorted(unknown))}")
    return [app for app in apps if not chosen or app["id"] in chosen]


def matrix(args: argparse.Namespace) -> None:
    include = [
        {"id": app["id"], "arch": arch} for app in wanted(args.apps) for arch in ARCHES
    ]
    print("matrix=" + json.dumps({"include": include}, separators=(",", ":")))


def select(args: argparse.Namespace) -> None:
    sdk = load(HERE / "sdk.json")
    references = {}
    for arch in ARCHES:
        build = load(args.refs / arch / "manifest.json")["product_build"]
        if not build.isalnum():
            raise SystemExit(f"invalid stable build {build!r}")
        target = sdk["arches"][arch]["target"]
        references[arch] = (
            load(args.refs / arch / f"sdk-abi-{target}.json"),
            gate.Stable(args.refs / arch / "files.json", HERE / "abi" / f"{build}.json"),
        )

    results = []
    for app in wanted(args.apps):
        for arch in ARCHES:
            built = args.built / f"payload-{app['id']}-{arch}"
            result = {"id": app["id"], "name": app["name"], "repo": app["repo"],
                      "commit": app["commit"], "arch": arch, "status": "failed"}
            results.append(result)
            if not (built / "payload.tar").is_file():
                continue
            payload = args.work / app["id"] / arch
            payload.mkdir(parents=True)
            with tarfile.open(built / "payload.tar") as archive:
                archive.extractall(payload, filter="data")
            version = load(built / "info.json")["version"]
            if not VERSION.match(version):
                raise SystemExit(f"{app['id']}: invalid version {version!r}")
            result["version"] = version
            result.update(gate.check(payload, *references[arch]))

    (args.work / "selection.json").write_text(json.dumps(results, indent=2) + "\n")
    print("| Application | Arch | Result | Missing |")
    print("| --- | --- | --- | --- |")
    for result in results:
        missing = result.get("broken") or result.get("absent") or []
        shown = ", ".join(missing[:4])
        if len(missing) > 4:
            shown += f" and {len(missing) - 4} more"
        print(f"| {result['id']} | {result['arch']} | {result['status']} | {shown} |")


def run(*command, output: Path | None = None) -> None:
    command = [str(part) for part in command]
    if output is None:
        subprocess.run(command, check=True)
        return
    with output.open("w") as stream:
        subprocess.run(command, check=True, stdout=stream)


def sign(args: argparse.Namespace) -> None:
    index = args.collection / "index.json"
    highest: dict[str, int] = {}
    for app in load(index)["apps"]:
        highest[app["id"]] = max((r["sequence"] for r in app["releases"]), default=0)
    for tag in args.tags.read_text().split():
        app_id, _, sequence = tag.rpartition("-")
        if app_id in highest and sequence.isdigit():
            highest[app_id] = max(highest[app_id], int(sequence))

    args.out.mkdir(parents=True)
    signed_index = args.out / "index.json"
    shutil.copyfile(index, signed_index)
    released = []
    for result in load(args.work / "selection.json"):
        if result["status"] != "compatible":
            continue
        app_id, arch = result["id"], result["arch"]
        payload = args.work / app_id / arch
        names = sorted(path.name for path in (payload / "bin").iterdir())
        main_binary = result["repo"] if result["repo"] in names else names[0]
        sequence = highest[app_id] + 1
        highest[app_id] = sequence
        base = f"{app_id}-{sequence}"
        spec = args.work / f"{base}.spec.json"
        spec.write_text(json.dumps({
            "id": app_id, "name": result["name"], "version": result["version"],
            "sequence": sequence,
            "entries": [{"arch": arch, "image": f"bin/{main_binary}", "personality": "linux"}],
        }))
        bundle = args.work / f"{base}.bundle"
        packed = args.work / f"{base}.pack.json"
        image = args.out / f"{base}.sxi"
        run(args.sxi, "build", "-spec", spec, "-payload", payload, "-root", args.root,
            "-key", args.key, "-certs", args.certs, "-o", bundle)
        run(args.sxi, "pack", "-roots", args.root.parent, "-o", image, bundle, output=packed)
        url = f"https://github.com/{REPOSITORY}/releases/download/{base}/{base}.sxi"
        run(args.sxi, "index", "add", "-index", signed_index, "-arch", arch,
            "-pack", packed, "-url", url)
        icon = payload / "share/icons/hicolor/scalable/apps" / f"{app_id}.svg"
        if icon.is_file():
            (args.out / "icons").mkdir(exist_ok=True)
            shutil.copyfile(icon, args.out / "icons" / icon.name)
        released.append({**result, "tag": base, "sequence": sequence,
                         "sha256": load(packed)["sha256"], "url": url})

    if not released:
        raise SystemExit("no compatible release to sign")
    run(args.sxi, "index", "sign", "-index", signed_index,
        "-key", args.key, "-certs", args.certs, "-root", args.root)
    (args.out / "releases.json").write_text(json.dumps(released, indent=2) + "\n")
    print(f"[singularity] {len(released)} releases signed")


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(required=True)

    command = commands.add_parser("matrix")
    command.add_argument("--apps", default="")
    command.set_defaults(action=matrix)

    command = commands.add_parser("select")
    command.add_argument("--apps", default="")
    for name in ("built", "refs", "work"):
        command.add_argument(f"--{name}", type=Path, required=True)
    command.set_defaults(action=select)

    command = commands.add_parser("sign")
    for name in ("work", "collection", "tags", "sxi", "root", "key", "certs", "out"):
        command.add_argument(f"--{name}", type=Path, required=True)
    command.set_defaults(action=sign)

    args = parser.parse_args()
    args.action(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
