// Keep byte-identical to android/app/src/main/aidl/dev/mos/bridge/IMosBridge.aidl (tests/test_rom.py checks).
// Append new methods at the end only; never reorder (transaction codes are positional).
package dev.mos.bridge;

/**
 * Privileged input for the MOS agent app on MOS ROM phones.
 *
 * Coordinates are screen pixels in the current rotation, the same space the
 * accessibility API reports. Every method returns false (and does nothing) when it
 * refuses: the keyguard is up, the coordinates are off screen, the key is not on the
 * allowlist, or the text has characters the virtual keyboard map can't produce.
 * The caller then falls back to accessibility. The Allow/Deny safety gate lives in
 * the app, before any of these calls; the bridge is only a transport.
 */
interface IMosBridge {
    /** Interface version, bumped when methods are appended. */
    int version();

    /** Press at (x, y) for durationMs (60 = tap, 800 = long press). */
    boolean tap(int x, int y, long durationMs);

    /** Straight-line drag from (x1, y1) to (x2, y2) over durationMs. */
    boolean swipe(int x1, int y1, int x2, int y2, long durationMs);

    /** Press and release one allowlisted key (android.view.KeyEvent code). */
    boolean key(int keyCode);

    /** Type text into the focused field with key events. */
    boolean text(String text);

    /** Turn on the MOS agent app's accessibility service (screenshots and UI tree). */
    boolean enableAgentAccessibility();
}
