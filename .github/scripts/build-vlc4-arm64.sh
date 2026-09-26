#!/usr/bin/env bash
set -euo pipefail

git config --global --add safe.directory /workspace
# Upstream get-vlc.sh applies VideoLAN's own bundled patches with git am.
git config --global user.name 'VLC Android CI'
git config --global user.email 'vlc-android-ci@users.noreply.github.com'
git config --global url.https://code.videolan.org/.insteadOf http://code.videolan.org/
report=/workspace/build-artifacts
mkdir -p "$HOME/.android"

# Limit Gradle memory/parallelism without editing any tracked build or app file.
export GRADLE_OPTS='-Dorg.gradle.jvmargs=-Xmx4g -Dorg.gradle.workers.max=2 -Dorg.gradle.daemon=false'
{
    java -version
    printf 'ANDROID_SDK=%s\nANDROID_NDK=%s\n' "$ANDROID_SDK" "$ANDROID_NDK"
    cat "$ANDROID_NDK/source.properties"
    cat "$ANDROID_SDK/platforms/android-36/source.properties"
    cat "$ANDROID_SDK/build-tools/36.0.0/source.properties"
    printf 'GRADLE_OPTS=%s\n' "$GRADLE_OPTS"
} 2>&1 | tee -a "$report/build-info.txt"

# This is the official initialization path; it fetches the repository-pinned
# libvlcjni and VLC sources and validates/downloads the pinned Gradle version.
./buildsystem/compile.sh --init -a arm64-v8a -vlc4

record_sources() {
    for source in . libvlcjni libvlcjni/vlc medialibrary/medialibrary medialibrary/medialibrary/libvlcpp application/remote-access-client/remoteaccess; do
        if [[ -e "$source/.git" ]]; then
            printf '\nsource=%s\n' "$source"
            git -C "$source" rev-parse HEAD
            git -C "$source" rev-parse 'HEAD^{tree}'
            git -C "$source" describe --always --tags --dirty
            git -C "$source" status --short --untracked-files=no
        fi
    done
}
record_sources | tee "$report/source-revisions-before.txt"
find libvlcjni -path '*/patches/*' -type f -print0 | sort -z | xargs -0 -r sha256sum > "$report/upstream-patches.sha256"
{
    ./gradlew --version
    grep -E 'versionName =|libvlcVersion =|medialibraryVersion =|android_plugin_version =|kotlin_version =' build.gradle
    grep -E 'VLC_TESTED_HASH=|VLC_REPOSITORY=|VLC_BRANCH=' libvlcjni/buildsystem/get-vlc.sh
} | tee -a "$report/build-info.txt"

contrib_sha=$(cd libvlcjni/vlc && extras/ci/get-contrib-sha.sh android-arm64)
contrib_url="https://artifacts.videolan.org/vlc/android-arm64/vlc-contrib-aarch64-linux-android-${contrib_sha}.tar.bz2"
printf 'contrib_identifier=%s\ncontrib_url=%s\n' "$contrib_sha" "$contrib_url" | tee -a "$report/build-info.txt"

# No release signing, source-check bypass, source patch, or playback test.
# -t lets the upstream script use prebuilt contribs when its URL check succeeds.
./buildsystem/compile.sh -a arm64-v8a -vlc4 -t

record_sources | tee "$report/source-revisions-after.txt"
git diff --exit-code HEAD
sha256sum --check --quiet "$report/source-files.sha256"

export APK_BUILD_TOOLS="$ANDROID_SDK/build-tools/36.0.0"
python3 .github/scripts/verify-vlc4-apks.py
