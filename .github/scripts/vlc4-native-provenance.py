"""Transfer exact same-source native libraries between isolated CI jobs."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ABIS = ("armeabi-v7a", "arm64-v8a", "x86", "x86_64")
ROOT = Path.cwd()
OUT = ROOT / "build-artifacts"


def git(path, ref="HEAD"):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", ref], text=True).strip()


def sources():
    return {
        "repository_commit": git(ROOT),
        "libvlcjni_commit": git("libvlcjni"),
        "vlc_commit": git("libvlcjni/vlc"),
        "vlc_tree": git("libvlcjni/vlc", "HEAD^{tree}"),
        "android_image": os.environ["ANDROID_IMAGE"],
    }


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def dirs(abi):
    return (f"libvlcjni/libvlc/jni/libs/{abi}", f"medialibrary/jni/libs/{abi}")


if sys.argv[1] == "record":
    abi = os.environ["ANDROID_ABI"]
    if abi not in ABIS:
        raise SystemExit(f"Unexpected ABI {abi}")
    record = sources()
    record.update({
        "abi": abi,
        "medialibrary_commit": git("medialibrary/medialibrary"),
        "libvlcpp_commit": git("medialibrary/medialibrary/libvlcpp"),
        "command": f"./buildsystem/compile.sh -ml -a {abi} -vlc4 -t",
        "files": {str(p.relative_to(ROOT)): digest(p) for d in dirs(abi) for p in sorted((ROOT / d).rglob("*")) if p.is_file()},
    })
    if not record["files"]:
        raise SystemExit("No native libraries produced")
    for library in ("libvlc.so", "libvlcjni.so", "libc++_shared.so"):
        if f"{dirs(abi)[0]}/{library}" not in record["files"]:
            raise SystemExit(f"Missing native library {library}")
    if f"{dirs(abi)[1]}/libmla.so" not in record["files"]:
        raise SystemExit("Missing medialibrary")
    (OUT / f"provenance-{abi}.json").write_text(json.dumps(record, indent=2) + "\n")
elif sys.argv[1] == "merge":
    expected = sources()
    provenance = OUT / "native-provenance"
    provenance.mkdir()
    records = []
    for abi in ABIS:
        record_path = ROOT / "native-input" / f"provenance-{abi}.json"
        record = json.loads(record_path.read_text())
        if record["abi"] != abi or any(record.get(k) != v for k, v in expected.items()):
            raise SystemExit(f"Native source mismatch for {abi}")
        allowed = dirs(abi)
        with tarfile.open(ROOT / "native-input" / f"native-{abi}.tar.gz") as archive:
            for member in archive.getmembers():
                path = Path(member.name)
                valid_prefix = any(member.name == d or member.name.startswith(d + "/") for d in allowed)
                if not valid_prefix or path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                    raise SystemExit(f"Unexpected native archive member: {member.name}")
                target = ROOT / path
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if member.name not in record["files"] or target.exists():
                        raise SystemExit(f"Unrecorded or duplicate native file: {member.name}")
                    with archive.extractfile(member) as source, target.open("wb") as dest:
                        shutil.copyfileobj(source, dest)
        for name, checksum in record["files"].items():
            if not any(name.startswith(d + "/") for d in allowed) or ".." in Path(name).parts:
                raise SystemExit(f"Unexpected provenance path: {name}")
            if digest(ROOT / name) != checksum:
                raise SystemExit(f"Native checksum mismatch: {name}")
        shutil.copy2(record_path, provenance)
        shutil.copy2(ROOT / "native-input/logs" / f"native-{abi}.log", OUT / "logs")
        records.append(record)
    for key in ("medialibrary_commit", "libvlcpp_commit"):
        if len({r[key] for r in records}) != 1:
            raise SystemExit(f"Architecture source mismatch: {key}")
    print("NATIVE_SOURCES_AND_CHECKSUMS=PASS")
else:
    raise SystemExit("Expected record or merge")
