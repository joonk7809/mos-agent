# 0003. The phone app uses only Android accessibility APIs

- Status: Accepted for stock phones; amended for MOS ROM phones by [0008](0008-lineageos-rom-with-privileged-bridge.md)
- Date: 2026-11-16
- Deciders: joon (plan of record); agent (Shizuku detail)

## Context
The agent needs screenshots, the UI tree, gestures, text entry and keys on stock phones that friends own.

## Decision
A single app with an AccessibilityService and an overlay (Allow/Deny/Stop), minSdk 30. No root, no Shizuku, no custom ROM for users. LineageOS on a used Pixel is for development only.

## Alternatives considered
- **Shizuku / ADB-over-wifi** - needs re-pairing after every reboot; too fragile for friends.
- **Custom ROM / system app** - deeper control but asks friends to reflash; kept as the long-term product path, not the friends-and-family path.

## Consequences
`FLAG_SECURE` screens (banking) come through black; apps ignoring `ACTION_SET_TEXT` need an IME fallback later. Sideloaded apps on Android 13+ need "Allow restricted settings".
