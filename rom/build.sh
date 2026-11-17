#!/usr/bin/env bash
# Build a MOS ROM (LineageOS + the MOS bridge and agent app) in one command.
#
#   rom/build.sh [DEVICE]            # default DEVICE=akita (Pixel 8a)
#   rom/build.sh lynx --skip-sync    # rebuild without re-syncing sources
#
# Run it on a rented Linux build box (Ubuntu 22.04 or 24.04, x86_64), from a checkout of
# this repo. Needs about 400 GB free disk, 32 GB+ RAM (64 GB is comfortable) and
# 16+ cores. First run: ~1-2 h sync + 2-4 h build; later runs are much faster with ccache.
# See rom/README.md for sizing, cost and how to flash the result.
#
# Environment (all optional):
#   MOS_ROM_DIR     work dir for sources, SDK, ccache, outputs   (default ~/mos-rom)
#   MOS_KEYS        signing keys; BACK THIS UP, see README        (default ~/mos-keys)
#   LINEAGE_BRANCH  LineageOS branch                              (default lineage-24.0)
#   MOS_VARIANT     user | userdebug | eng                        (default userdebug)
#   MOS_JOBS        parallel jobs                                 (default nproc)
#   MOS_FORCE=1     skip the disk/RAM preflight
set -euo pipefail

DEVICE=akita
SKIP_SYNC=0
for arg in "$@"; do
  case "$arg" in
    --skip-sync) SKIP_SYNC=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) DEVICE=$arg ;;
  esac
done
if [[ ! "$DEVICE" =~ ^[a-z0-9_]+$ ]]; then
  echo "device codename must be lowercase letters/digits, got '$DEVICE'" >&2; exit 2
fi

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
MOS_ROM_DIR=${MOS_ROM_DIR:-$HOME/mos-rom}
MOS_KEYS=${MOS_KEYS:-$HOME/mos-keys}
LINEAGE_BRANCH=${LINEAGE_BRANCH:-lineage-24.0}
MOS_VARIANT=${MOS_VARIANT:-userdebug}
MOS_JOBS=${MOS_JOBS:-$(nproc)}
TREE=$MOS_ROM_DIR/lineage
SDK=$MOS_ROM_DIR/android-sdk
# Pinned Android command-line tools (only used to build the agent APK).
CMDLINE_TOOLS_ZIP=commandlinetools-linux-11076708_latest.zip

case "$MOS_VARIANT" in user|userdebug|eng) ;; *) echo "bad MOS_VARIANT=$MOS_VARIANT" >&2; exit 2 ;; esac

log() { printf '\n==> %s\n' "$*"; }
SUDO=""; [[ $(id -u) -eq 0 ]] || SUDO=sudo

mkdir -p "$MOS_ROM_DIR" "$MOS_KEYS"
chmod 700 "$MOS_KEYS"

# ---- 0. preflight ---------------------------------------------------------------
log "preflight"
free_gb=$(df -BG --output=avail "$MOS_ROM_DIR" | tail -1 | tr -dc '0-9')
ram_gb=$(awk '/MemTotal/ {print int($2/1024/1024)}' /proc/meminfo)
echo "device=$DEVICE branch=$LINEAGE_BRANCH variant=$MOS_VARIANT jobs=$MOS_JOBS free=${free_gb}G ram=${ram_gb}G"
if [[ "${MOS_FORCE:-0}" != 1 ]]; then
  if [[ ! -d "$TREE/.repo" && $free_gb -lt 350 ]]; then
    echo "need ~350 GB free for a first sync+build (have ${free_gb}G); MOS_FORCE=1 to try anyway" >&2; exit 1
  fi
  if [[ $ram_gb -lt 30 ]]; then
    echo "need 32 GB+ RAM (have ${ram_gb}G); MOS_FORCE=1 to try anyway" >&2; exit 1
  fi
fi

# ---- 1. host packages -----------------------------------------------------------
log "host packages"
PKGS=(bc bison build-essential ccache curl flex g++-multilib gcc-multilib git git-lfs gnupg gperf
      imagemagick lib32readline-dev lib32z1-dev libelf-dev liblz4-tool libsdl1.2-dev libssl-dev
      libxml2 libxml2-utils lzop pngcrush rsync schedtool squashfs-tools xsltproc zip zlib1g-dev
      openjdk-17-jdk-headless python3 python-is-python3 unzip openssl)
missing=()
for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
if ((${#missing[@]})); then
  $SUDO apt-get update -q
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -q "${missing[@]}"
fi
git lfs install --skip-repo >/dev/null
if [[ -z "$(git config --global user.email || true)" ]]; then
  git config --global user.name "MOS builder"
  git config --global user.email "builder@mos.invalid"
fi
mkdir -p "$HOME/.local/bin"
export PATH=$HOME/.local/bin:$PATH
if ! command -v repo >/dev/null; then
  curl -fsSL https://storage.googleapis.com/git-repo-downloads/repo -o "$HOME/.local/bin/repo"
  chmod +x "$HOME/.local/bin/repo"
fi

# ---- 2. LineageOS sources ---------------------------------------------------------
mkdir -p "$TREE"
cd "$TREE"
if [[ ! -d .repo ]]; then
  log "repo init $LINEAGE_BRANCH"
  repo init -u https://github.com/LineageOS/android.git -b "$LINEAGE_BRANCH" --git-lfs --no-clone-bundle
fi
# Proprietary vendor blobs for the device, from TheMuppets (the usual source for Lineage builds).
mkdir -p .repo/local_manifests
cat > .repo/local_manifests/mos.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by mos-agent rom/build.sh -->
<manifest>
  <project name="TheMuppets/proprietary_vendor_google_${DEVICE}" path="vendor/google/${DEVICE}"
           remote="github" revision="${LINEAGE_BRANCH}" clone-depth="1" />
</manifest>
EOF
if [[ $SKIP_SYNC == 0 ]]; then
  log "repo sync (long on first run)"
  repo sync -c -j"$MOS_JOBS" --force-sync --no-clone-bundle --no-tags --optimized-fetch --prune
fi

# AOSP's envsetup.sh, breakfast and m aren't written for `set -euo pipefail`: relax all
# three around them and check their results explicitly.
lineage_env() {
  set +eu +o pipefail
  # shellcheck disable=SC1091
  source build/envsetup.sh >/dev/null
  breakfast "$DEVICE" "$MOS_VARIANT"
  local rc=$?
  set -eu -o pipefail
  [[ $rc -eq 0 ]] || { echo "breakfast $DEVICE failed (rc=$rc)" >&2; exit 1; }
}
log "breakfast $DEVICE (fetches device trees on first run)"
lineage_env

# ---- 3. signing keys (generated once, reused forever) ------------------------------
# Lineage's public test keys would let anyone sign an app as "platform" and get the
# bridge's powers, so MOS builds always use private release keys.
# Keys are generated once; the loop also finishes a set left half-made by an interrupted run.
CERTS=$MOS_KEYS/android-certs
mkdir -p "$CERTS"
subject='/C=US/ST=CA/L=NA/O=MOS/OU=MOS/CN=MOS ROM'
for cert in bluetooth cyngn-app media networkstack nfc platform releasekey sdk_sandbox shared testcert testkey verity; do
  [[ -f $CERTS/$cert.pk8 && -f $CERTS/$cert.x509.pem ]] && continue
  log "generating ROM signing key $cert"
  rm -f "$CERTS/$cert.pk8" "$CERTS/$cert.x509.pem" # make_key refuses to overwrite a half-made pair
  # make_key asks for a password; an empty answer makes an unencrypted key (inline signing
  # needs that). Its exit code is always 1, so check for the files instead.
  printf '\n' | ./development/tools/make_key "$CERTS/$cert" "$subject" >/dev/null 2>&1 || true
  [[ -f $CERTS/$cert.pk8 ]] || { echo "make_key failed for $cert" >&2; exit 1; }
done
chmod 600 "$CERTS"/*.pk8
mkdir -p vendor/lineage-priv
# The tree only holds the keys while this script runs.
trap 'rm -rf "$TREE/vendor/lineage-priv/keys"' EXIT
rsync -a --delete "$CERTS/" vendor/lineage-priv/keys/
echo 'PRODUCT_DEFAULT_DEV_CERTIFICATE := vendor/lineage-priv/keys/releasekey' > vendor/lineage-priv/keys/keys.mk
cat > vendor/lineage-priv/keys/BUILD.bazel <<'EOF'
filegroup(
    name = "android_certificate_directory",
    srcs = glob([
        "*.pk8",
        "*.pem",
    ]),
    visibility = ["//visibility:public"],
)
EOF

APP_KS=$MOS_KEYS/mos-app.jks
APP_PASS_FILE=$MOS_KEYS/mos-app.pass
if [[ ! -f $APP_KS ]]; then
  log "generating the MOS app signing key (first run only)"
  (umask 077; openssl rand -hex 24 > "$APP_PASS_FILE")
  keytool -genkeypair -keystore "$APP_KS" -storetype PKCS12 -alias mos -keyalg RSA -keysize 4096 \
    -validity 10000 -dname "CN=MOS app, O=MOS" \
    -storepass:file "$APP_PASS_FILE" -keypass:file "$APP_PASS_FILE" >/dev/null
  chmod 600 "$APP_KS"
fi

# ---- 4. the MOS agent app (built here: this box can reach dl.google.com) -----------
log "building the MOS agent app"
if [[ ! -x $SDK/cmdline-tools/latest/bin/sdkmanager ]]; then
  tmp=$(mktemp -d)
  curl -fsSL "https://dl.google.com/android/repository/$CMDLINE_TOOLS_ZIP" -o "$tmp/tools.zip"
  unzip -q "$tmp/tools.zip" -d "$tmp"
  mkdir -p "$SDK/cmdline-tools"
  rm -rf "$SDK/cmdline-tools/latest"
  mv "$tmp/cmdline-tools" "$SDK/cmdline-tools/latest"
  rm -rf "$tmp"
fi
# `yes` exits on SIGPIPE once sdkmanager stops reading; don't let pipefail treat that as failure.
(yes || true) | "$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" --licenses >/dev/null
"$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" "platforms;android-35" "build-tools;35.0.0" >/dev/null
(
  cd "$REPO_ROOT/android"
  ANDROID_HOME=$SDK MOS_KEYSTORE=$APP_KS MOS_KEYSTORE_PASSWORD_FILE=$APP_PASS_FILE \
    ./gradlew --no-daemon -q :app:assembleRelease
)
APK=$REPO_ROOT/android/app/build/outputs/apk/release/app-release.apk
[[ -f $APK ]] || { echo "app build produced no $APK" >&2; exit 1; }
"$SDK/build-tools/35.0.0/apksigner" verify "$APK"

# ---- 5. stage the MOS layer into vendor/extra --------------------------------------
log "staging vendor/extra"
digest=$(keytool -exportcert -keystore "$APP_KS" -alias mos -storepass:file "$APP_PASS_FILE" | sha256sum | cut -d' ' -f1)
apk_digest=$("$SDK/build-tools/35.0.0/apksigner" verify --print-certs "$APK" \
  | awk -F': ' '/Signer #1 certificate SHA-256 digest/ {print $2; exit}')
if [[ "$digest" != "$apk_digest" ]]; then
  echo "APK is signed with $apk_digest, expected the MOS app key $digest" >&2; exit 1
fi
rsync -a --delete "$REPO_ROOT/rom/vendor-extra/" vendor/extra/
python3 "$REPO_ROOT/rom/tools/pin_cert.py" "$digest" vendor/extra/MosBridge/res/values/known_certs.xml
cp "$APK" vendor/extra/MosAgent/MosAgent.apk

# ---- 6. build -----------------------------------------------------------------------
log "building LineageOS + MOS for $DEVICE (long on first run)"
export USE_CCACHE=1 CCACHE_EXEC=/usr/bin/ccache CCACHE_DIR=$MOS_ROM_DIR/ccache
ccache -M 50G >/dev/null
touch "$MOS_ROM_DIR/.build-start"
set +eu +o pipefail
m -j"$MOS_JOBS" bacon
rc=$?
OUT_DIR=$ANDROID_PRODUCT_OUT
set -eu -o pipefail
[[ $rc -eq 0 ]] || { echo "build failed (rc=$rc); see the log above" >&2; exit 1; }

# ---- 7. collect -------------------------------------------------------------------------
DEST=$MOS_ROM_DIR/out/$DEVICE-$(date -u +%Y%m%d-%H%M)
mkdir -p "$DEST"
# Newest zip by mtime. bacon hard-links it to the OTA package, so an unchanged rebuild can
# leave an older mtime: warn rather than fail.
zip_path=$(find "$OUT_DIR" -maxdepth 1 -name 'lineage-*.zip' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)
[[ -n "$zip_path" ]] || { echo "no lineage-*.zip in $OUT_DIR" >&2; exit 1; }
if [[ ! "$zip_path" -nt "$MOS_ROM_DIR/.build-start" ]]; then
  echo "warning: $zip_path predates this build (nothing changed?); copying it anyway" >&2
fi
cp "$zip_path" "$DEST/"
for img in boot dtbo init_boot vendor_boot vendor_kernel_boot recovery; do
  [[ -f $OUT_DIR/$img.img ]] && cp "$OUT_DIR/$img.img" "$DEST/"
done
cp "$APK" "$DEST/MosAgent.apk"
(cd "$DEST" && sha256sum -- * > SHA256SUMS)

log "done: $DEST"
ls -lh "$DEST"
cat <<EOF

Next: flash it (rom/README.md, "Flashing"). Back up $MOS_KEYS somewhere safe and private
NOW, then delete it from this box (or destroy the box): without those keys you can't update
a flashed phone without wiping it, and anyone with them can sign system apps for your phones.
EOF
