// Keep byte-identical to android/app/src/main/aidl/dev/mos/bridge/IMosBridge.aidl (tests/test_rom.py checks).
// Append new methods at the end only; never reorder (transaction codes are positional).
package dev.mos.bridge;

/**
 * Privileged input for the MOS agent app on MOS ROM phones.
 *
 * Coordinates are screen pixels in the current rotation, the same space the
 * accessibility API reports. Input methods return one of the result codes below.
 * Only REFUSED means "nothing happened, use accessibility instead"; every other
 * non-OK result must not be retried another way, because the input may already have
 * landed (a second attempt could send twice) or the phone is in a state the agent
 * must not drive. The Allow/Deny safety gate lives in the app and server, before any
 * of these calls; the bridge is only a transport.
 */
interface IMosBridge {
    /** Done. */
    const int OK = 0;
    /** Refused before injecting anything (off screen, key not allowed, unmappable text). */
    const int REFUSED = 1;
    /** The phone is locked. Nothing injected. Do not fall back. */
    const int LOCKED = 2;
    /** A permission or install screen is in front. Nothing injected. Do not fall back. */
    const int BLOCKED = 3;
    /** Injection failed partway; part of the input may have landed. Do not retry. */
    const int FAILED = 4;

    /** Interface version, bumped when methods are appended. */
    int version();

    /** Press at (x, y) for durationMs (60 = tap, 800 = long press). */
    int tap(int x, int y, long durationMs);

    /** Straight-line drag from (x1, y1) to (x2, y2) over durationMs. */
    int swipe(int x1, int y1, int x2, int y2, long durationMs);

    /** Press and release one allowlisted key (android.view.KeyEvent code). */
    int key(int keyCode);

    /** Type text into the focused field with key events. */
    int text(String text);

    /**
     * Turn on the MOS agent app's accessibility service (screenshots and UI tree).
     * The app calls this only when the user taps its "turn on" button.
     */
    boolean enableAgentAccessibility();
}
