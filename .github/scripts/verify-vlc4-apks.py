"""Verify and collect the unmodified upstream Dev APK; never test playback."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

root = Path.cwd()
out = root / "build-artifacts"
build_tools = Path(os.environ["APK_BUILD_TOOLS"])
apks = sorted(p for p in root.rglob("*.apk") if out not in p.parents)
if not apks:
    raise SystemExit("No APK was produced")

records = []
for apk in apks:
    if (out / apk.name).exists():
        raise SystemExit(f"Duplicate APK basename: {apk.name}")
    with zipfile.ZipFile(apk) as archive:
        native = [n for n in archive.namelist() if n.startswith("lib/") and n.endswith(".so")]
        abis = sorted({n.split("/")[1] for n in native})
        if abis != ["arm64-v8a"]:
            raise SystemExit(f"Unexpected ABI set in {apk}: {abis}")
        for name in native:
            header = archive.read(name)[:20]
            if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 183:
                raise SystemExit(f"Not an AArch64 ELF library: {name}")
        if "lib/arm64-v8a/libvlc.so" not in native:
            raise SystemExit(f"libvlc.so missing from {apk}")
    signature = subprocess.check_output(
        [str(build_tools / "apksigner"), "verify", "--verbose", "--print-certs", str(apk)],
        text=True,
    )
    badging = subprocess.check_output([str(build_tools / "aapt"), "dump", "badging", str(apk)], text=True)
    if "name='org.videolan.vlc.debug'" not in badging or "versionName='4.0.0-preview" not in badging:
        raise SystemExit(f"Expected VLC 4 Dev application metadata in {apk}")
    if "native-code: 'arm64-v8a'" not in badging or "CN=Android Debug" not in signature:
        raise SystemExit(f"Expected ARM64 and Android debug signature in {apk}")
    (out / f"{apk.name}.signature.txt").write_text(signature)
    (out / f"{apk.name}.badging.txt").write_text(badging)
    shutil.copy2(apk, out / apk.name)
    digest = hashlib.sha256()
    with apk.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    records.append({
        "filename": apk.name,
        "source_path": str(apk.relative_to(root)),
        "size_bytes": apk.stat().st_size,
        "sha256": digest.hexdigest(),
        "abis": abis,
        "native_libraries": native,
        "signature_verified": True,
        "version_name": re.search(r"versionName='([^']+)'", badging).group(1),
    })

def revision(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()

report = {
    "result": "VLC4_ANDROID_ARM64_BUILD=PASS",
    "repository_commit": revision(root),
    "libvlcjni_commit": revision(root / "libvlcjni"),
    "vlc_commit": revision(root / "libvlcjni/vlc"),
    "vlc_tree": subprocess.check_output(["git", "-C", "libvlcjni/vlc", "rev-parse", "HEAD^{tree}"], text=True).strip(),
    "run_url": f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    "android_image": os.environ["ANDROID_IMAGE"],
    "build_command": "./buildsystem/compile.sh -a arm64-v8a -vlc4 -t",
    "variant": "Dev",
    "source_changes": False,
    "playback_test_performed": False,
    "apks": records,
}
(out / "build-info.json").write_text(json.dumps(report, indent=2) + "\n")
(out / "SHA256SUMS").write_text("".join(f"{r['sha256']}  {r['filename']}\n" for r in records))
lines = [f"Repository commit: `{report['repository_commit']}`", f"libVLC JNI commit: `{report['libvlcjni_commit']}`", f"VLC commit: `{report['vlc_commit']}`", ""]
for record in records:
    lines.extend([f"- APK: `{record['filename']}`", f"- Size: {record['size_bytes']} bytes", f"- SHA-256: `{record['sha256']}`", "- ABI: `arm64-v8a`; Android debug signature verified", ""])
lines.append("No playback test performed. The upstream Dev filename contains 'all'; the APK libraries were verified to contain ARM64 only.")
(out / "apk-report.md").write_text("\n".join(lines) + "\n")
print(json.dumps(report, indent=2))
