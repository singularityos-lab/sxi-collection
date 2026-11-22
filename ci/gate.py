#!/usr/bin/env python3

import json
import os
import posixpath
import struct
import sys
from pathlib import Path

SEARCH = (
    "/usr/lib",
    "/usr/local/lib",
    "/usr/lib/sinty-framework",
    "/usr/local/lib/fotone-proxy",
)
SHT_DYNAMIC = 6
SHT_DYNSYM = 11
DT_NEEDED = 1
STB_WEAK = 2


def dynamic(data: bytes) -> tuple[set[str], set[str], list[str]]:
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    shentsize, shnum = struct.unpack_from("<HH", data, 0x3A)
    sections = [
        struct.unpack_from("<IIQQQQIIQQ", data, shoff + index * shentsize)
        for index in range(shnum)
    ]

    def text(section: tuple, offset: int) -> str:
        start = sections[section[6]][4] + offset
        return data[start:data.index(b"\0", start)].decode()

    imports: set[str] = set()
    exports: set[str] = set()
    needed: list[str] = []
    for section in sections:
        entries = range(section[4], section[4] + section[5], section[9] or 1)
        if section[1] == SHT_DYNSYM:
            for at in entries:
                name, info, _, shndx = struct.unpack_from("<IBBH", data, at)
                if not name or not info >> 4:
                    continue
                if shndx:
                    exports.add(text(section, name))
                elif info >> 4 != STB_WEAK:
                    imports.add(text(section, name))
        elif section[1] == SHT_DYNAMIC:
            for at in entries:
                tag, value = struct.unpack_from("<qQ", data, at)
                if tag == DT_NEEDED:
                    needed.append(text(section, value))
    return imports, exports, needed


class Stable:
    def __init__(self, manifest: Path, added: Path):
        self.files: dict[str, list] = {}
        for line in manifest.read_text().splitlines():
            entry = json.loads(line)
            self.files[entry[0]] = entry
        self.added = json.loads(added.read_text())["added"] if added.is_file() else {}

    def find(self, name: str) -> list | None:
        for top in SEARCH:
            entry = self.files.get(f"{top}/{name}")
            for _ in range(16):
                if entry is None or entry[1] != "l":
                    break
                path = posixpath.normpath(posixpath.join(posixpath.dirname(entry[0]), entry[3]))
                entry = self.files.get(path)
            if entry is not None and entry[1] == "f":
                return entry
        return None

    def lacks(self, name: str, library: dict, symbol: str) -> bool:
        entry = self.find(name)
        if entry is None:
            return True
        if entry[3] == library["sha256"]:
            return False
        if entry[4]:
            return symbol not in entry[4]
        if name in self.added:
            return symbol in self.added[name]
        return True


def check(payload: Path, abi: dict, stable: Stable) -> dict:
    own: dict[str, tuple[set[str], set[str], list[str]]] = {}
    for base, _, names in os.walk(payload):
        for name in names:
            path = Path(base, name)
            if path.is_symlink():
                continue
            data = path.read_bytes()
            if data[:4] == b"\x7fELF":
                own[str(path.relative_to(payload))] = dynamic(data)

    bundled = {posixpath.basename(path): own[path][2] for path in own}
    for path in payload.rglob("*.so*"):
        real = posixpath.basename(os.path.realpath(path))
        bundled.setdefault(path.name, bundled.get(real, []))
    bundled_exports = set().union(*(exports for _, exports, _ in own.values()))

    broken: set[str] = set()
    absent: set[str] = set()
    for path, (imports, _, needed) in own.items():
        loaded: dict[str, dict] = {}
        pending = list(needed)
        if "/" in path and not path.startswith("bin/"):
            for other, (_, _, host_needed) in own.items():
                if other.startswith("bin/"):
                    pending += host_needed
        seen: set[str] = set()
        for name in pending:
            if name in seen:
                continue
            seen.add(name)
            if name in bundled:
                pending.extend(bundled[name])
                continue
            key = abi["names"].get(name)
            if key is None:
                broken.add(f"{path}: {name}")
                continue
            loaded[name] = abi["files"][key]
            pending.extend(loaded[name]["needed"])
        for name in needed:
            if name in loaded and stable.find(name) is None:
                absent.add(name)
        exporters = {name: set(library["exports"]) for name, library in loaded.items()}
        for symbol in imports - bundled_exports:
            providers = [name for name, exports in exporters.items() if symbol in exports]
            if not providers:
                broken.add(f"{path}: {symbol}")
            elif all(stable.lacks(name, loaded[name], symbol) for name in providers):
                absent.add(symbol)

    status = "broken" if broken else "withheld" if absent else "compatible"
    return {"status": status, "broken": sorted(broken), "absent": sorted(absent)}


def main() -> int:
    if len(sys.argv) != 5:
        print("usage: gate.py PAYLOAD SDK_ABI STABLE_FILES ADDED", file=sys.stderr)
        return 2
    payload, abi, manifest, added = map(Path, sys.argv[1:])
    result = check(payload, json.loads(abi.read_text()), Stable(manifest, added))
    json.dump(result, sys.stdout, indent=2)
    print()
    return 1 if result["status"] == "broken" else 0


if __name__ == "__main__":
    sys.exit(main())
