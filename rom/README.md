# MOS ROM

LineageOS for a Pixel, plus two MOS pieces:

- **MosBridge** (`vendor-extra/MosBridge`): a small platform-signed system app. It injects real touch and key events (`INJECT_EVENTS`) and can switch on the agent's accessibility service (`WRITE_SECURE_SETTINGS`). Only apps signed with the MOS app key may use it.
- **MosAgent** (`vendor-extra/MosAgent`): the normal MOS app from `android/`, preinstalled and signed with the MOS app key. It uses the bridge for taps, swipes, keys and text when the bridge is there, and accessibility for everything else (screenshots, UI tree) and as a fallback.

Why it is built this way: [ADR 0007](../docs/decisions/0007-lineageos-rom-with-privileged-bridge.md). Which Pixel: [ADR 0008](../docs/decisions/0008-first-rom-device.md).

What you give up on this phone: Play Integrity fails, so banking, wallet/tap-to-pay and some streaming apps won't work. Google apps aren't included (LineageOS ships without them).

## Build (one command, on a rented box)

```bash
git clone https://github.com/joonk7809/mos-agent && cd mos-agent
rom/build.sh akita          # Pixel 8a. Other codenames: lynx (7a), tegu (9a), shiba (8), ...
```

The box: Ubuntu 22.04/24.04 x86_64, **16+ cores, 32 GB+ RAM (64 GB comfortable), ~400 GB disk**. The cloud container this repo is developed in can't do this (no `dl.google.com`, too small). Any hourly VM works; pick one with a persistent disk so later builds reuse the source tree and ccache.

| | First build | Rebuild after a MOS change |
|---|---|---|
| Time (32 vCPU) | ~1-2 h sync + ~2-3 h build | ~20-40 min (`--skip-sync`) |
| Rough cost at ~$1-1.5/h | ~$5-8 | ~$1 |

Plus disk while you keep the box (or snapshot the disk and stop the VM). Stop the VM when the build finishes.

What the script does: installs host packages and `repo`, syncs `LINEAGE_BRANCH` (default `lineage-24.0`) and the device's vendor blobs from TheMuppets, generates signing keys once, builds the MOS app with the Android SDK and the MOS app key, pins that key into the bridge, copies `vendor-extra/` to `vendor/extra/` (Lineage inherits `vendor/extra/product.mk` automatically), then runs `breakfast <device>` and `m bacon`. Outputs land in `~/mos-rom/out/<device>-<time>/`: the flashable `lineage-*.zip`, the boot/recovery images, `MosAgent.apk` and `SHA256SUMS`.

Settings are environment variables documented at the top of `build.sh` (`MOS_ROM_DIR`, `MOS_KEYS`, `LINEAGE_BRANCH`, `MOS_VARIANT`, `MOS_JOBS`).

## Keys: back them up

`~/mos-keys/` holds the ROM's private signing keys (`android-certs/`) and the MOS app key (`mos-app.jks`, `mos-app.pass`). Copy the folder somewhere safe and private right after the first build.

- Lose `android-certs/` and you can't update a flashed phone without wiping it.
- Lose `mos-app.jks` and the bridge stops trusting new app builds until you rebuild the ROM.
- Anyone holding `platform.pk8` can build apps with system powers on your phones. Never commit these files or copy them to a shared box.

Lineage's public test keys are never used for app signing, because anyone could sign an app with them and get platform (and so bridge) powers. APEX modules are still signed with AOSP's default keys; that's a known gap to close before friends get phones.

## Flashing (first time)

Follow the LineageOS install guide for your device (`https://wiki.lineageos.org/devices/<codename>/install/`), with two changes: use **this build's** images and zip instead of the official downloads, and skip the GApps step. In short:

1. Buy an **unlocked** Pixel (carrier-locked units, especially Verizon, usually can't unlock the bootloader).
2. Update it to the stock Android version the Lineage guide asks for (firmware must match).
3. Developer options: enable OEM unlocking and USB debugging. `adb reboot bootloader`, then `fastboot flashing unlock` (this wipes the phone).
4. `fastboot flash` the partitions the guide lists (typically `boot`, `dtbo`, `vendor_kernel_boot` and/or `vendor_boot`) from the output folder. You must use these images, not official Lineage ones: the recovery only accepts zips signed with your keys.
5. Reboot to recovery, factory reset, then `adb sideload lineage-*.zip`.
6. Boot, open **MOS**. On first launch the bridge switches the agent's accessibility service on by itself; the status line should say "Agent service: on". Then set the server URL and token as usual.

Updates later: build again, then in recovery `adb sideload` the new zip (no wipe needed while the keys are the same). App-only updates: `adb install -r MosAgent.apk` from the output folder (must be signed with the MOS app key, which `build.sh` does).

## Check it worked

```bash
adb shell pm list packages | grep dev.mos             # dev.mos.bridge and dev.mos.agent
adb shell dumpsys package dev.mos.agent | grep -A1 dev.mos.permission.BRIDGE   # granted=true
adb logcat -s MOS MosBridge                           # "bridge connected" when the agent starts
```

Then run the emulator-safe eval tasks on the phone (never anything that sends, pays or deletes for real), and compare with the accessibility-only path.

## Layout

```
rom/build.sh                         one-command build
rom/tools/pin_cert.py                writes the bridge's trusted-app digest
rom/vendor-extra/                    copied to vendor/extra/ in the Lineage tree
  product.mk                         adds MosBridge + MosAgent, power-save exemption
  MosBridge/                         privileged bridge: Android.bp, manifest, AIDL, Java
  MosAgent/Android.bp                prebuilt MOS app (APK dropped in by build.sh)
  etc/sysconfig-mos.xml              Doze exemption for dev.mos.agent
android/app/src/main/aidl/...        app's copy of IMosBridge.aidl (kept identical; tested)
```
