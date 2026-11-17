# MOS on emulators first

Until there's a phone, MOS runs on the Android Emulator. There are two paths. Use the quick path to measure whether the system bridge beats accessibility. Use the full path to prove the real ROM builds and boots. Why: [ADR 0010](../../docs/decisions/0010-emulator-first.md).

| | Quick path | Full path |
|---|---|---|
| What runs | Stock "Google APIs" emulator image + the MOS bridge installed as a system app | The real MOS ROM (LineageOS 24.0 + bridge + app) as an emulator image |
| Where | Your machine | Built on a rented box, run on your machine |
| Cost / time | $0, ~15 minutes | ~$8-15 of box time, ~4-6 h first build |
| Proves | Bridge vs accessibility on the eval tasks | `rom/build.sh`, the Soong module, private keys, the preinstalled app, first-boot setup |

## Quick path

1. In Android Studio's Device Manager, create an AVD with a **Google APIs** system image, API 35 (arm64-v8a on Apple Silicon). Don't use a **Google Play** image: those are signed with Google's keys, so no app can be platform-signed for them, and the script refuses them.
2. Start it writable: `emulator -avd <name> -writable-system`.
3. From this repo: `rom/emulator/install_bridge.sh`. It builds the app and the bridge, signs the bridge with the image's platform key, installs it as a privileged app and checks both permissions.
4. On the emulator, open MOS, tap **Turn on the agent**, and set the server URL and token. Go home and save the eval snapshot: `adb emu avd snapshot save clean`.
5. Run the A/B. `--driver app` runs each task through the app; `--bridge` switches the bridge on or off after each snapshot load:

```bash
python -m evals.run_evals --driver app --bridge on  --server http://mos-gpu:8080 --repeats 3 --label "bridge"
python -m evals.run_evals --driver app --bridge off --server http://mos-gpu:8080 --repeats 3 --label "a11y"
python -m evals.compare runs/evals/<bridge run> runs/evals/<a11y run>
```

App-driven runs approve confirmations and answer questions automatically, as the ADB harness does. The app only accepts eval broadcasts in a debuggable build on an emulator, and only from adb.

Always start this AVD with `-writable-system` from now on, or the bridge disappears.

## Full path

On the build box: `rom/build.sh sdk_phone_arm64` (Apple Silicon) or `rom/build.sh sdk_phone_x86_64` (x86 hosts with KVM). It's the same script as for phones, minus the vendor blobs. The output folder holds `sdk-repo-*system-images*.zip`.

To run it on your machine, unzip it into your SDK as a system image and point an AVD at it:

```bash
mkdir -p "$ANDROID_HOME/system-images/android-mos/lineage"
unzip sdk-repo-*system-images*.zip -d "$ANDROID_HOME/system-images/android-mos/lineage"
# then create an AVD in Device Manager that uses it, or:
emulator -avd <any-avd> -sysdir "$ANDROID_HOME/system-images/android-mos/lineage/<abi>"
```

The bridge and app are already inside. Open MOS, tap **Turn on the agent**, and run the same evals as the quick path. Emulator builds preinstall a debug build of the app so the eval driver works. It's signed with a separate emulator-only key (`MOS_KEYS/mos-app-emulator.jks`), which is the key the bridge in that image trusts. That way the debuggable app can never install over the app on a real MOS phone. To update it, use `MosAgent-emulator-debug.apk` from the output folder.

## What emulators can't tell us

These wait for a real phone (the Pixel 7a):

- **Flashing and updates:** bootloader unlock, recovery, sideloading our signed zips, and the TheMuppets blobs and Pixel firmware.
- **Real-world speed:** Tensor performance, thermals, and battery and Doze behaviour over hours.
- **Phone hardware:** cellular calls, SMS and SIM, camera, NFC and wallet, fingerprint.
- **Apps that check where they run:** banking, some games and streaming apps refuse emulators, just as they refuse the ROM. Play Integrity fails on both.
- **Real touch latency** and OEM quirks in how apps handle injected input.

Everything the agent loop does (screenshots, UI tree, taps, swipes, keys, text, the safety gate, Stop) behaves the same on an emulator. That's what the A/B measures.
