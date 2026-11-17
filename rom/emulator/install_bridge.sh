#!/usr/bin/env bash
# Quick path: put the MOS system bridge on a stock Android Emulator, no ROM build needed.
#
#   rom/emulator/install_bridge.sh [--serial emulator-5554] [--app path/to/app.apk]
#
# Runs on your own machine (macOS or Linux) with the Android SDK, against a running
# emulator that uses a "Google APIs" system image (NOT "Google Play": those are signed with
# Google's release keys) and was started with -writable-system:
#   emulator -avd <name> -writable-system
#
# It signs the bridge with the emulator image's platform key (AOSP's public test key, which
# is why this is emulator-only), installs it as a privileged system app, installs the MOS app
# and checks both permissions were granted. See rom/emulator/README.md.
set -euo pipefail

SERIAL=""
APP_APK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial) SERIAL=$2; shift 2 ;;
    --app) APP_APK=$2; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
[[ -n "$SDK" && -d "$SDK/build-tools" ]] || { echo "set ANDROID_HOME to your Android SDK" >&2; exit 1; }
BUILD_TOOLS=$(find "$SDK/build-tools" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)
APKSIGNER=$BUILD_TOOLS/apksigner
CACHE=${XDG_CACHE_HOME:-$HOME/.cache}/mos/aosp-testkey
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

adb_() { if [[ -n "$SERIAL" ]]; then adb -s "$SERIAL" "$@"; else adb "$@"; fi; }
sh_() { adb_ shell "$@" | tr -d '\r'; }
log() { printf '\n==> %s\n' "$*"; }
sha256_of_file() { python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }
signer_digest() {
  "$APKSIGNER" verify --print-certs "$1" | awk -F': ' '/Signer #1 certificate SHA-256 digest/ {print $2; exit}'
}
wait_boot() {
  adb_ wait-for-device
  for _ in $(seq 1 120); do
    [[ "$(sh_ getprop sys.boot_completed)" == 1 ]] && return 0
    sleep 2
  done
  echo "emulator did not finish booting" >&2; exit 1
}

# ---- 1. is this an emulator we can use? ------------------------------------------------
log "checking the emulator"
if [[ "$(sh_ getprop ro.kernel.qemu)" != 1 && "$(sh_ getprop ro.boot.qemu)" != 1 ]]; then
  echo "the connected device is not an Android Emulator; this script never touches real phones" >&2; exit 1
fi
sdk_int=$(sh_ getprop ro.build.version.sdk)
[[ $sdk_int -ge 31 ]] || { echo "needs Android 12 (API 31) or newer, found API $sdk_int" >&2; exit 1; }

# ---- 2. the image's platform key: AOSP's public test key --------------------------------
mkdir -p "$CACHE"
for f in platform.pk8 platform.x509.pem; do
  if [[ ! -s $CACHE/$f ]]; then
    curl -fsSL "https://android.googlesource.com/platform/build/+/refs/heads/main/target/product/security/$f?format=TEXT" \
      | python3 -c 'import base64,sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))' > "$CACHE/$f"
  fi
done
openssl x509 -in "$CACHE/platform.x509.pem" -outform DER -out "$WORK/platform.der"
testkey_digest=$(sha256_of_file "$WORK/platform.der")
adb_ pull /system/framework/framework-res.apk "$WORK/framework-res.apk" >/dev/null
image_digest=$(signer_digest "$WORK/framework-res.apk")
if [[ "$image_digest" != "$testkey_digest" ]]; then
  cat >&2 <<MSG
This emulator image isn't signed with the AOSP test keys (platform cert $image_digest).
Google Play images use Google's release keys, so no app can be platform-signed for them.
Create an AVD with a "Google APIs" system image (API 31+), start it with -writable-system,
and run this again.
MSG
  exit 1
fi

# ---- 3. build the app (if needed) and the bridge pinned to the app's key ----------------
if [[ -z "$APP_APK" ]]; then
  APP_APK=$REPO_ROOT/android/app/build/outputs/apk/debug/app-debug.apk
  log "building the MOS app (debug)"
  (cd "$REPO_ROOT/android" && ./gradlew -q :app:assembleDebug)
fi
app_digest=$(signer_digest "$APP_APK")
[[ ${#app_digest} -eq 64 ]] || { echo "couldn't read the signing certificate of $APP_APK" >&2; exit 1; }
log "building the bridge, pinned to app certificate $app_digest"
(cd "$REPO_ROOT/android" && ./gradlew -q :bridge:assembleRelease -Pmos.appCertSha256="$app_digest")
"$APKSIGNER" sign --key "$CACHE/platform.pk8" --cert "$CACHE/platform.x509.pem" \
  --out "$WORK/MosBridge.apk" "$REPO_ROOT/android/bridge/build/outputs/apk/release/bridge-release-unsigned.apk"

# ---- 4. install as a privileged system app -----------------------------------------------
log "making /system writable"
adb_ root >/dev/null; adb_ wait-for-device
if ! adb_ remount 2>&1 | tee "$WORK/remount.txt" | grep -qi "remount succeeded"; then
  if grep -qi "reboot" "$WORK/remount.txt"; then
    # First time on this AVD: verity was just disabled and needs a reboot.
    adb_ reboot; wait_boot; adb_ root >/dev/null; adb_ wait-for-device
    adb_ remount | grep -qi "remount succeeded" || { cat "$WORK/remount.txt" >&2; exit 1; }
  else
    cat "$WORK/remount.txt" >&2
    echo "remount failed: start the emulator with -writable-system" >&2; exit 1
  fi
fi
log "installing the bridge"
sh_ mkdir -p /system/priv-app/MosBridge
adb_ push "$WORK/MosBridge.apk" /system/priv-app/MosBridge/MosBridge.apk >/dev/null
adb_ push "$REPO_ROOT/rom/vendor-extra/MosBridge/privapp-permissions-mos.xml" \
  /system/etc/permissions/privapp-permissions-mos.xml >/dev/null
adb_ reboot; wait_boot
log "installing the MOS app"
adb_ install -r "$APP_APK" >/dev/null

# ---- 5. check -----------------------------------------------------------------------------
log "checking permissions"
bridge_dump=$(sh_ dumpsys package dev.mos.bridge)
app_dump=$(sh_ dumpsys package dev.mos.agent)
ok=1
grep -q "android.permission.INJECT_EVENTS: granted=true" <<<"$bridge_dump" || { echo "bridge lacks INJECT_EVENTS" >&2; ok=0; }
grep -q "dev.mos.permission.BRIDGE: granted=true" <<<"$app_dump" || { echo "app lacks dev.mos.permission.BRIDGE" >&2; ok=0; }
[[ $ok == 1 ]] || exit 1

cat <<'MSG'

Bridge installed. Next:
  1. Open MOS on the emulator, tap "Turn on the agent", and set the server URL and token.
  2. Go to the home screen and save the eval snapshot:  adb emu avd snapshot save clean
  3. Compare bridge vs accessibility on the same tasks:
       python -m evals.run_evals --driver app --bridge on  --server <url> --token <t> --repeats 3
       python -m evals.run_evals --driver app --bridge off --server <url> --token <t> --repeats 3
Always start this AVD with -writable-system from now on, or the bridge disappears.
MSG
