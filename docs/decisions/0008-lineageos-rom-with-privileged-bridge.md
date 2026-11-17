# 0008. Build a LineageOS-based MOS ROM with a small privileged input bridge

- Status: Accepted
- Date: 2026-11-17
- Deciders: joon (custom ROM now, accepting broken banking/wallet apps and ROM upkeep); agent (bridge design, signing, build approach)

## Context
The scoping review recommended staying app-first and gating a custom ROM behind usage gates. joon chose "Custom ROM phone now" on 2026-11-17: he wants system-level agent control on a dedicated Pixel. Accessibility-only control has known limits (ADR 0003): gestures some apps ignore, `ACTION_SET_TEXT` that some fields reject, the Android 13+ "restricted settings" step, and Android 17 Advanced Protection blocking non-verified accessibility apps.

LineageOS 24.0 builds weekly for every Pixel from the 6a to the 10, and TheMuppets publish the vendor blobs, so a Lineage base needs no device bring-up. Lineage inherits `vendor/extra/product.mk` if present, so MOS can add packages without forking any Lineage repo.

## Decision
We will ship MOS as an overlay on stock LineageOS (`rom/vendor-extra/` copied to `vendor/extra/`) built by `rom/build.sh` on a rented box. System-level control comes from **MosBridge**, a separate, minimal platform-signed priv-app exposing an AIDL service (tap, swipe, allowlisted keys, text, enable the agent's accessibility service). The agent app stays a normal app signed with its own key; the bridge grants access only to `dev.mos.agent` signed with that key, through `dev.mos.permission.BRIDGE` with `signature|knownSigner` (the key's SHA-256 pinned at build time) plus a per-call permission and package check. The bridge refuses input while the phone is locked or a permission prompt/package installer is in front, refuses off-screen coordinates, never presses power/volume/call/camera keys, and does no networking and no screen reading. Input calls return result codes; only REFUSED (nothing injected) lets the app fall back to accessibility, so one approved action can never run twice. Accessibility is enabled through the bridge only when the user taps the app's button. Screenshots and the UI tree stay on accessibility. APKs and the update zip are signed with private release keys, never Lineage's public test keys.

## Alternatives considered
- **Make the agent app itself platform-signed** - one APK, but the network-facing app would run with platform powers, and every app update would need the platform key. Larger attack surface for no gain.
- **Run the agent as a shell/root daemon (`uiautomator`/`input` style)** - works, but root on a daily phone and a bigger privileged component.
- **Fork a Lineage device tree / our own lunch target** - unnecessary: `vendor/extra` gets us in without carrying patches through Lineage updates.
- **Capture screenshots in the bridge (READ_FRAME_BUFFER / MediaProjection without consent)** - deferred. Accessibility screenshots work at agent speed, and the hidden capture APIs change each Android release. Revisit if screenshot latency or `FLAG_SECURE` handling becomes the bottleneck (and `FLAG_SECURE` capture would need its own page: it is the banking-app privacy line).

## Consequences
- The same APK works on stock phones (bridge absent: pure accessibility) and MOS ROM phones (bridge used, accessibility as fallback). The stock-app path stays the fallback product.
- App builds for ROM phones must be signed with the MOS app key from `MOS_KEYS`; CI's debug-signed APK runs on them but without the bridge.
- Keys in `MOS_KEYS` are now critical assets (see `rom/README.md`).
- Injected input reaches views Android shields from accessibility (`accessibilityDataSensitive`), which the agent may not see in the UI tree. Three layers keep that from bypassing the Allow/Deny gate: the app sends a stroke through the bridge only when it starts on a labelled element the gate could judge (anything else goes through accessibility, where Android's shield still applies); the bridge refuses input while a permission or install screen tops a recent task; and the gate treats "allow"/"grant" labels as risky. The labelled-target rule is a heuristic (a sensitive view under a labelled clickable parent would pass); verify on device.
- When the bridge reports LOCKED or BLOCKED, the app does not fall back to accessibility.
- Not yet private: APEX keys, verified boot (bootloader stays unlocked). Builds default to `userdebug`. Close these before friends get phones.
- Unverified until a build box run: the Java compiles against `lineage-24.0` platform APIs (`InputManager.injectInputEvent`, `INJECT_INPUT_EVENT_MODE_WAIT_FOR_FINISH`), and the build script end to end.
- Revisit if Lineage drops `vendor/extra/product.mk`, or if on-device evals show the bridge doesn't beat accessibility (then it isn't worth the ROM).
