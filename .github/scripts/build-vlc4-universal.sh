#!/usr/bin/env bash
set -euo pipefail

mode=${1:?Expected native or assemble}
git config --global --add safe.directory /workspace
git config --global user.name 'VLC Android CI'
git config --global user.email 'vlc-android-ci@users.noreply.github.com'
git config --global url.https://code.videolan.org/.insteadOf http://code.videolan.org/
# Make upstream git-am commits identical across architecture and assembly jobs.
GIT_COMMITTER_DATE=$(git show -s --format=%cI HEAD)
export GIT_COMMITTER_DATE
export GRADLE_OPTS='-Dorg.gradle.jvmargs=-Xmx4g -Dorg.gradle.workers.max=2 -Dorg.gradle.daemon=false'
mkdir -p build-artifacts/logs "$HOME/.android"
while IFS= read -r -d '' path; do
    if [[ -f "$path" && ! -L "$path" ]]; then
        sha256sum "$path"
    fi
done < <(git ls-files -z) > "build-artifacts/sources-${ANDROID_ABI:-universal}.sha256"

case "$mode" in
    native)
        case "$ANDROID_ABI" in
            armeabi-v7a|arm64-v8a|x86|x86_64) ;;
            *) echo "Unsupported ABI: $ANDROID_ABI" >&2; exit 1 ;;
        esac
        ./buildsystem/compile.sh -ml -a "$ANDROID_ABI" -vlc4 -t
        python3 .github/scripts/vlc4-native-provenance.py record
        tar -czf "build-artifacts/native-$ANDROID_ABI.tar.gz" \
            "libvlcjni/libvlc/jni/libs/$ANDROID_ABI" \
            "medialibrary/jni/libs/$ANDROID_ABI"
        ;;
    assemble)
        ./buildsystem/compile.sh --init -a arm64-v8a -vlc4
        # Validate exact source revisions, archive paths and checksums, then merge.
        python3 .github/scripts/vlc4-native-provenance.py merge
        {
            echo "repository_commit=$(git rev-parse HEAD)"
            echo "android_image=$ANDROID_IMAGE"
            echo "native_command=./buildsystem/compile.sh -ml -a ABI -vlc4 -t"
            echo "assembly_command=./gradlew -PforceVlc4=true assembleDev"
            echo "ABIs=armeabi-v7a arm64-v8a x86 x86_64"
            echo "GIT_COMMITTER_DATE=$GIT_COMMITTER_DATE"
            java -version
            ./gradlew --version
            cat "$ANDROID_NDK/source.properties"
            cat "$ANDROID_SDK/build-tools/36.0.0/source.properties"
            grep -E 'versionName =|libvlcVersion =|medialibraryVersion =|android_plugin_version =|kotlin_version =' build.gradle
            grep -E 'VLC_TESTED_HASH=' libvlcjni/buildsystem/get-vlc.sh
        } 2>&1 | tee build-artifacts/build-info.txt
        GRADLE_ABI=ALL ./gradlew -PforceVlc4=true assembleDev
        export APK_BUILD_TOOLS="$ANDROID_SDK/build-tools/36.0.0"
        export EXPECTED_ABIS='armeabi-v7a arm64-v8a x86 x86_64'
        export BUILD_RESULT=VLC4_ANDROID_UNIVERSAL_BUILD
        export BUILD_COMMAND='Four native builds: ./buildsystem/compile.sh -ml -a ABI -vlc4 -t; merge; GRADLE_ABI=ALL ./gradlew -PforceVlc4=true assembleDev'
        export REQUIRE_TV=1
        python3 .github/scripts/verify-vlc4-apks.py
        ;;
    *) echo "Unknown mode: $mode" >&2; exit 1 ;;
esac

git diff --exit-code HEAD
sha256sum --check --quiet "build-artifacts/sources-${ANDROID_ABI:-universal}.sha256"
