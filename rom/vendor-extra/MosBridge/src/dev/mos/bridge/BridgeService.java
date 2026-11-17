package dev.mos.bridge;

import android.app.ActivityManager;
import android.app.KeyguardManager;
import android.app.Service;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Point;
import android.hardware.display.DisplayManager;
import android.hardware.input.InputManager;
import android.os.Binder;
import android.os.IBinder;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.TextUtils;
import android.util.Log;
import android.view.Display;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyCharacterMap;
import android.view.KeyEvent;
import android.view.MotionEvent;

import java.util.Arrays;
import java.util.List;

/**
 * Binder service the MOS agent app binds to on MOS ROM phones.
 *
 * Kept deliberately small: it runs with the platform certificate, so everything here
 * is privileged code. It does no networking, keeps no state, and reads nothing from
 * the screen. Callers are restricted by dev.mos.permission.BRIDGE (signature|knownSigner,
 * pinned to the MOS app key) at bind time and again on every call, and the calling
 * package must be dev.mos.agent.
 *
 * Result codes (see IMosBridge.aidl): only REFUSED invites a fallback. Anything that may
 * have injected input reports FAILED so the app never repeats an action another way.
 */
public final class BridgeService extends Service {
    private static final String TAG = "MosBridge";
    static final String PERMISSION = "dev.mos.permission.BRIDGE";
    static final String AGENT_PACKAGE = "dev.mos.agent";
    static final String AGENT_SERVICE = "dev.mos.agent.MosAccessibilityService";

    /** 2: input methods return result codes (1 returned booleans). */
    static final int VERSION = 2;
    static final long MAX_DURATION_MS = 5_000;
    static final int MAX_TEXT_CHARS = 2_000;
    private static final long MOVE_INTERVAL_MS = 16;

    /**
     * Never inject while one of these is in front: runtime-permission prompts and app
     * installs. Android shields these from accessibility input; injected events would
     * get through, and the agent must not grant itself permissions or install apps.
     */
    static final List<String> BLOCKED_FOREGROUND = Arrays.asList(
        "com.android.permissioncontroller",
        "com.google.android.permissioncontroller",
        "com.android.packageinstaller",
        "com.google.android.packageinstaller");

    /** Keys the agent may press. No POWER, no volume, no camera, no calls. */
    static final int[] ALLOWED_KEYS = {
        KeyEvent.KEYCODE_BACK,
        KeyEvent.KEYCODE_HOME,
        KeyEvent.KEYCODE_APP_SWITCH,
        KeyEvent.KEYCODE_ENTER,
        KeyEvent.KEYCODE_DEL,
        KeyEvent.KEYCODE_TAB,
        KeyEvent.KEYCODE_DPAD_UP,
        KeyEvent.KEYCODE_DPAD_DOWN,
        KeyEvent.KEYCODE_DPAD_LEFT,
        KeyEvent.KEYCODE_DPAD_RIGHT,
    };

    private final IMosBridge.Stub binder = new IMosBridge.Stub() {
        @Override
        public int version() {
            enforceCaller();
            return VERSION;
        }

        @Override
        public int tap(int x, int y, long durationMs) {
            enforceCaller();
            return withIdentity(() -> doSwipe(x, y, x, y, durationMs));
        }

        @Override
        public int swipe(int x1, int y1, int x2, int y2, long durationMs) {
            enforceCaller();
            return withIdentity(() -> doSwipe(x1, y1, x2, y2, durationMs));
        }

        @Override
        public int key(int keyCode) {
            enforceCaller();
            return withIdentity(() -> doKey(keyCode));
        }

        @Override
        public int text(String text) {
            enforceCaller();
            return withIdentity(() -> doText(text));
        }

        @Override
        public boolean enableAgentAccessibility() {
            enforceCaller();
            long id = Binder.clearCallingIdentity();
            try {
                return doEnableAccessibility();
            } catch (RuntimeException e) {
                Log.w(TAG, "enable accessibility failed", e);
                return false;
            } finally {
                Binder.restoreCallingIdentity(id);
            }
        }
    };

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    // ---- guards ---------------------------------------------------------------

    private void enforceCaller() {
        // The bind-time check covers the normal path; this covers a leaked binder.
        enforceCallingPermission(PERMISSION, "MOS bridge caller lacks " + PERMISSION);
        // The permission is per signing key; also pin the package, so another app signed
        // with the same key can't drive the phone.
        String[] pkgs = getPackageManager().getPackagesForUid(Binder.getCallingUid());
        if (pkgs == null || !Arrays.asList(pkgs).contains(AGENT_PACKAGE)) {
            throw new SecurityException("MOS bridge only serves " + AGENT_PACKAGE);
        }
    }

    private interface Action {
        int run();
    }

    /**
     * Runs as the bridge (not the caller) so the platform permissions apply. An unexpected
     * exception reports FAILED, not REFUSED: we can't know whether input already landed.
     */
    private static int withIdentity(Action a) {
        long id = Binder.clearCallingIdentity();
        try {
            return a.run();
        } catch (RuntimeException e) {
            Log.w(TAG, "action failed", e);
            return IMosBridge.FAILED;
        } finally {
            Binder.restoreCallingIdentity(id);
        }
    }

    /** Never drive a locked phone: the agent must not get past the lock screen. */
    private boolean locked() {
        KeyguardManager km = getSystemService(KeyguardManager.class);
        return km == null || km.isKeyguardLocked() || km.isDeviceLocked();
    }

    /** LOCKED / BLOCKED if input must not be injected now, else OK. */
    private int gate() {
        if (locked()) return IMosBridge.LOCKED;
        if (sensitiveScreenUp()) return IMosBridge.BLOCKED;
        return IMosBridge.OK;
    }

    /**
     * True if a permission or install screen tops any of the most recent tasks (via
     * REAL_GET_TASKS). Checking a few tasks, not just the first, covers split screen;
     * it may over-block when such a screen sits in a recent background task. Unknown
     * counts as blocked.
     */
    private boolean sensitiveScreenUp() {
        ActivityManager am = getSystemService(ActivityManager.class);
        List<ActivityManager.RunningTaskInfo> tasks = am.getRunningTasks(3);
        if (tasks == null || tasks.isEmpty()) return true;
        for (ActivityManager.RunningTaskInfo t : tasks) {
            if (t.topActivity == null || BLOCKED_FOREGROUND.contains(t.topActivity.getPackageName())) return true;
        }
        return false;
    }

    private Point screen() {
        Display d = getSystemService(DisplayManager.class).getDisplay(Display.DEFAULT_DISPLAY);
        Point p = new Point();
        d.getRealSize(p); // current rotation, same space as accessibility bounds
        return p;
    }

    static boolean onScreen(int x, int y, Point s) {
        return x >= 0 && y >= 0 && x < s.x && y < s.y;
    }

    static boolean allowedKey(int keyCode) {
        for (int k : ALLOWED_KEYS) {
            if (k == keyCode) return true;
        }
        return false;
    }

    // ---- actions --------------------------------------------------------------

    private int doSwipe(int x1, int y1, int x2, int y2, long durationMs) {
        Point s = screen();
        if (!onScreen(x1, y1, s) || !onScreen(x2, y2, s)) return IMosBridge.REFUSED;
        if (durationMs < 1 || durationMs > MAX_DURATION_MS) return IMosBridge.REFUSED;
        int g = gate();
        if (g != IMosBridge.OK) return g;

        // From here on, anything short of a clean UP is FAILED: input may have landed.
        long down = SystemClock.uptimeMillis();
        if (!injectTouch(down, down, MotionEvent.ACTION_DOWN, x1, y1)) return IMosBridge.FAILED;
        long steps = Math.max(1, durationMs / MOVE_INTERVAL_MS);
        float x = x1;
        float y = y1;
        for (long i = 1; i <= steps; i++) {
            SystemClock.sleep(MOVE_INTERVAL_MS);
            float f = (float) i / steps;
            x = x1 + (x2 - x1) * f;
            y = y1 + (y2 - y1) * f;
            if (!injectTouch(down, SystemClock.uptimeMillis(), MotionEvent.ACTION_MOVE, x, y)) {
                injectTouch(down, SystemClock.uptimeMillis(), MotionEvent.ACTION_CANCEL, x, y);
                return IMosBridge.FAILED;
            }
        }
        if (locked()) { // locked mid-gesture (power button): cancel rather than release
            injectTouch(down, SystemClock.uptimeMillis(), MotionEvent.ACTION_CANCEL, x, y);
            return IMosBridge.FAILED;
        }
        return injectTouch(down, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, x2, y2)
                ? IMosBridge.OK : IMosBridge.FAILED;
    }

    private boolean injectTouch(long downTime, long eventTime, int action, float x, float y) {
        MotionEvent ev = MotionEvent.obtain(downTime, eventTime, action, x, y, 0);
        ev.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        try {
            return inject(ev);
        } finally {
            ev.recycle();
        }
    }

    private int doKey(int keyCode) {
        if (!allowedKey(keyCode)) return IMosBridge.REFUSED;
        int g = gate();
        if (g != IMosBridge.OK) return g;
        long now = SystemClock.uptimeMillis();
        KeyEvent down = new KeyEvent(now, now, KeyEvent.ACTION_DOWN, keyCode, 0, 0,
                KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD);
        KeyEvent up = KeyEvent.changeAction(down, KeyEvent.ACTION_UP);
        if (!inject(down)) return IMosBridge.FAILED;
        return inject(up) ? IMosBridge.OK : IMosBridge.FAILED;
    }

    private int doText(String text) {
        if (text == null || text.isEmpty() || text.length() > MAX_TEXT_CHARS) return IMosBridge.REFUSED;
        KeyCharacterMap map = KeyCharacterMap.load(KeyCharacterMap.VIRTUAL_KEYBOARD);
        KeyEvent[] events = map.getEvents(text.toCharArray());
        if (events == null) return IMosBridge.REFUSED; // characters the keymap can't produce
        int g = gate();
        if (g != IMosBridge.OK) return g;
        for (KeyEvent e : events) {
            if (!inject(KeyEvent.changeTimeRepeat(e, SystemClock.uptimeMillis(), 0))) return IMosBridge.FAILED;
        }
        return IMosBridge.OK;
    }

    private boolean inject(InputEvent ev) {
        InputManager im = getSystemService(InputManager.class);
        return im.injectInputEvent(ev, InputManager.INJECT_INPUT_EVENT_MODE_WAIT_FOR_FINISH);
    }

    private boolean doEnableAccessibility() {
        try {
            getPackageManager().getPackageInfo(AGENT_PACKAGE, 0);
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
        String component = new ComponentName(AGENT_PACKAGE, AGENT_SERVICE).flattenToString();
        String current = Settings.Secure.getString(getContentResolver(),
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        if (current != null) {
            for (String s : TextUtils.split(current, ":")) {
                if (component.equalsIgnoreCase(s)) {
                    return Settings.Secure.putInt(getContentResolver(),
                            Settings.Secure.ACCESSIBILITY_ENABLED, 1);
                }
            }
        }
        String updated = TextUtils.isEmpty(current) ? component : current + ":" + component;
        return Settings.Secure.putString(getContentResolver(),
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES, updated)
                && Settings.Secure.putInt(getContentResolver(), Settings.Secure.ACCESSIBILITY_ENABLED, 1);
    }
}
