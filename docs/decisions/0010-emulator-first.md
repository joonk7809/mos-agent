# 0010. Prove MOS on emulators before buying a phone

- Status: Accepted
- Date: 2026-11-17
- Deciders: joon (asked to work through emulators first); agent (the two paths and the eval driver)

## Context
ADR 0008 gives the agent system-level input through a platform-signed bridge. The open question is whether that beats accessibility on our tasks. Answering it needs the bridge on a device and evals that drive the phone app rather than ADB. joon asked to use emulators for the first stretch.

## Decision
We will measure on emulators first, in two paths (`rom/emulator/README.md`):
- **Quick path ($0, your machine):** a stock "Google APIs" emulator image is signed with AOSP's public test keys. So `rom/emulator/install_bridge.sh` builds the same bridge source with Gradle, signs it with that platform key and installs it as a privileged system app. It checks the image's platform certificate first and refuses anything that isn't an emulator.
- **Full path (rented box):** `rom/build.sh sdk_phone_arm64|sdk_phone_x86_64` builds the real MOS ROM as an emulator image, which validates the build script, Soong module, keys and preinstall.
- **App-driven evals:** `evals/run_evals.py --driver app --bridge on|off` runs tasks through the MOS app (EvalReceiver) and toggles the bridge per run, so bridge vs accessibility is a paired comparison on the same tasks.

The bridge calls the hidden `InputManager.injectInputEvent` by reflection, so one Java source builds both with Soong and with the public SDK.

## Alternatives considered
- **Buy the Pixel 7a now** - real hardware, but the first questions (does the bridge help, does the ROM build) don't need it.
- **Cuttlefish only** - closest to a real device build, but needs a KVM host and doesn't run on joon's Mac.
- **Root a Google Play emulator image** - can't platform-sign for it, so the bridge would need a different privilege model than the ROM's.

## Consequences
- The eval receiver auto-approves confirmations (like the ADB harness). It is locked to adb (`DUMP` permission), debuggable builds and emulators.
- Emulator results don't cover flashing, Pixel firmware, real-device performance, telephony/NFC/camera, or apps that refuse emulators (rom/emulator/README.md).
- Buy the phone when the bridge shows a clear eval win, or when we need what only a phone tests.
