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
expected_abis = sorted(os.environ.get("EXPECTED_ABIS", "arm64-v8a").split())
elf_targets = {"armeabi-v7a": (1, 40), "arm64-v8a": (2, 183), "x86": (1, 3), "x86_64": (2, 62)}
result_name = os.environ.get("BUILD_RESULT", "VLC4_ANDROID_ARM64_BUILD")
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
        if abis != expected_abis:
            raise SystemExit(f"Unexpected ABI set in {apk}: {abis}")
        for name in native:
            with archive.open(name) as stream:
                header = stream.read(20)
            elf_class, machine = elf_targets[name.split("/")[1]]
            if header[:6] != b"\x7fELF" + bytes([elf_class, 1]) or int.from_bytes(header[18:20], "little") != machine:
                raise SystemExit(f"Wrong ELF architecture: {name}")
        for abi in expected_abis:
            for library in ("libvlc.so", "libvlcjni.so", "libmla.so", "libc++_shared.so"):
                if f"lib/{abi}/{library}" not in native:
                    raise SystemExit(f"{library} missing for {abi} from {apk}")
    signature = subprocess.check_output(
        [str(build_tools / "apksigner"), "verify", "--verbose", "--print-certs", str(apk)],
        text=True,
    )
    badging = subprocess.check_output([str(build_tools / "aapt"), "dump", "badging", str(apk)], text=True)
    if "name='org.videolan.vlc.debug'" not in badging or "versionName='4.0.0-preview" not in badging:
        raise SystemExit(f"Expected VLC 4 Dev application metadata in {apk}")
    native_code = re.search(r"^native-code: (.+)$", badging, re.MULTILINE)
    if not native_code or sorted(re.findall(r"'([^']+)'", native_code.group(1))) != expected_abis:
        raise SystemExit(f"Unexpected aapt ABI metadata in {apk}")
    if "CN=Android Debug" not in signature:
        raise SystemExit(f"Expected Android debug signature in {apk}")
    minimum_sdk = int(re.search(r"^sdkVersion:'(\d+)'", badging, re.MULTILINE).group(1))
    tv_launcher = "leanback-launchable-activity:" in badging
    touchscreen_optional = "uses-feature-not-required: name='android.hardware.touchscreen'" in badging
    if os.environ.get("REQUIRE_TV") == "1" and (minimum_sdk != 23 or not tv_launcher or not touchscreen_optional):
        raise SystemExit(f"Expected upstream API 23 minimum and TV support in {apk}")
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
        "minimum_sdk": minimum_sdk,
        "tv_launcher": tv_launcher,
        "touchscreen_optional": touchscreen_optional,
    })

def revision(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()

report = {
    "result": f"{result_name}=PASS",
    "repository_commit": revision(root),
    "libvlcjni_commit": revision(root / "libvlcjni"),
    "vlc_commit": revision(root / "libvlcjni/vlc"),
    "vlc_tree": subprocess.check_output(["git", "-C", "libvlcjni/vlc", "rev-parse", "HEAD^{tree}"], text=True).strip(),
    "run_url": f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    "android_image": os.environ["ANDROID_IMAGE"],
    "build_command": os.environ.get("BUILD_COMMAND", "./buildsystem/compile.sh -a arm64-v8a -vlc4 -t"),
    "variant": "Dev",
    "source_changes": False,
    "playback_test_performed": False,
    "apks": records,
}
(out / "build-info.json").write_text(json.dumps(report, indent=2) + "\n")
(out / "SHA256SUMS").write_text("".join(f"{r['sha256']}  {r['filename']}\n" for r in records))
lines = [f"Repository commit: `{report['repository_commit']}`", f"libVLC JNI commit: `{report['libvlcjni_commit']}`", f"VLC commit: `{report['vlc_commit']}`", ""]
for record in records:
    lines.extend([f"- APK: `{record['filename']}`", f"- Size: {record['size_bytes']} bytes", f"- SHA-256: `{record['sha256']}`", f"- ABIs: {', '.join(record['abis'])}; Android debug signature verified", f"- Minimum Android API: {record['minimum_sdk']}; TV launcher: {record['tv_launcher']}", ""])
lines.append("No playback test performed. Architecture support was verified from the APK contents, not inferred from the upstream 'all' filename.")
(out / "apk-report.md").write_text("\n".join(lines) + "\n")
print(json.dumps(report, indent=2))
