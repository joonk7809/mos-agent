package dev.mos.agent

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.util.Base64
import android.view.Display
import android.view.KeyEvent
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import dev.mos.bridge.IMosBridge
import dev.mos.core.AppInfo
import dev.mos.core.DeviceAction
import dev.mos.core.Element
import dev.mos.core.Geometry
import java.io.ByteArrayOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * The agent's eyes and hands: screenshots, the UI tree, gestures, text and keys,
 * all through the accessibility APIs (no root, no ADB, works after reboot).
 * On a MOS ROM phone, taps, swipes and keys go through the privileged bridge instead
 * ([BridgeClient]), falling back to accessibility whenever the bridge refuses.
 */
class MosAccessibilityService : AccessibilityService() {

    companion object {
        @Volatile
        var instance: MosAccessibilityService? = null
            private set

        private const val MAX_SCREENSHOT_EDGE = 1600
        private const val MAX_ELEMENTS = 300
    }

    lateinit var overlay: Overlay
        private set
    private val executor = Executors.newSingleThreadExecutor()
    private val bridge = BridgeClient(this)
    private var loop: AgentLoop? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        overlay = Overlay(this)
        bridge.connect()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit
    override fun onInterrupt() = Unit

    override fun onDestroy() {
        loop?.stop()
        if (::overlay.isInitialized) overlay.remove()
        instance = null
        bridge.disconnect()
        executor.shutdownNow()
        super.onDestroy()
    }

    // ---- running tasks -------------------------------------------------------

    fun startTask(task: String, server: String, token: String, eval: Boolean = false): Boolean {
        if (loop?.isRunning == true) return false
        val l = AgentLoop(this, ServerClient(server, token), task, eval)
        loop = l
        Thread(l, "mos-agent").start()
        return true
    }

    fun stopTask() = loop?.stop()

    // ---- perception ------------------------------------------------------------

    fun screenSize(): Pair<Int, Int> {
        val b = getSystemService(WindowManager::class.java).currentWindowMetrics.bounds
        return b.width() to b.height()
    }

    /** Screenshot as base64 JPEG, long edge capped. Blocks up to 5 s. Hides the overlay while capturing. */
    fun captureScreenB64(): String {
        overlay.setHiddenBlocking(true)
        try {
            val result = AtomicReference<Bitmap?>()
            val error = AtomicReference<String?>()
            val latch = CountDownLatch(1)
            takeScreenshot(Display.DEFAULT_DISPLAY, executor, object : AccessibilityService.TakeScreenshotCallback {
                override fun onSuccess(shot: AccessibilityService.ScreenshotResult) {
                    val hw = Bitmap.wrapHardwareBuffer(shot.hardwareBuffer, shot.colorSpace)
                    result.set(hw?.copy(Bitmap.Config.ARGB_8888, false))
                    shot.hardwareBuffer.close()
                    latch.countDown()
                }

                override fun onFailure(errorCode: Int) {
                    error.set("screenshot failed (code $errorCode)")
                    latch.countDown()
                }
            })
            if (!latch.await(5, TimeUnit.SECONDS)) throw IllegalStateException("screenshot timed out")
            val bmp = result.get() ?: throw IllegalStateException(error.get() ?: "no screenshot")
            val (w, h) = Geometry.scaled(bmp.width, bmp.height, MAX_SCREENSHOT_EDGE)
            val scaled = if (w != bmp.width) Bitmap.createScaledBitmap(bmp, w, h, true) else bmp
            val out = ByteArrayOutputStream()
            scaled.compress(Bitmap.CompressFormat.JPEG, 90, out)
            return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
        } finally {
            overlay.setHiddenBlocking(false)
        }
    }

    /** Elements of the active window, in screen pixels. */
    fun dumpElements(): List<Element> {
        val root = rootInActiveWindow ?: return emptyList()
        val out = ArrayList<Element>()
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.add(root)
        val r = Rect()
        while (stack.isNotEmpty() && out.size < MAX_ELEMENTS) {
            val n = stack.removeLast()
            if (!n.isVisibleToUser) continue
            n.getBoundsInScreen(r)
            val e = Element(
                text = n.text?.toString() ?: "",
                desc = n.contentDescription?.toString() ?: "",
                resId = n.viewIdResourceName ?: "",
                cls = n.className?.toString() ?: "",
                bounds = intArrayOf(r.left, r.top, r.right, r.bottom),
                clickable = n.isClickable || n.isLongClickable,
                editable = n.isEditable,
                scrollable = n.isScrollable,
                focused = n.isFocused,
                password = n.isPassword,
            )
            if (e.worthKeeping()) out.add(e)
            for (i in n.childCount - 1 downTo 0) n.getChild(i)?.let { stack.add(it) }
        }
        return out
    }

    fun launcherApps(): List<AppInfo> {
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return packageManager.queryIntentActivities(intent, 0)
            .map { AppInfo(it.loadLabel(packageManager).toString(), it.activityInfo.packageName) }
            .filter { it.pkg != packageName }
            .distinctBy { it.pkg }
            .sortedBy { it.label.lowercase() }
    }

    // ---- actions -------------------------------------------------------------------

    /** Execute one action. Returns a short note; throws if it could not be done. */
    fun execute(a: DeviceAction): String {
        val (w, h) = screenSize()
        return when (a) {
            is DeviceAction.Click -> move(a.x, a.y, a.x, a.y, 60).let { "tap" }
            is DeviceAction.LongPress -> move(a.x, a.y, a.x, a.y, 800).let { "long press" }
            is DeviceAction.Swipe -> {
                val p = Geometry.swipe(a.direction, a.x ?: w / 2, a.y ?: h / 2, w, h)
                move(p[0], p[1], p[2], p[3], 350); "swipe ${a.direction}"
            }
            is DeviceAction.Drag -> move(a.x1, a.y1, a.x2, a.y2, 1000).let { "drag" }
            is DeviceAction.Type -> typeText(a.text)
            is DeviceAction.Key -> key(a.name)
            is DeviceAction.Open -> openApp(a)
            is DeviceAction.Wait -> { Thread.sleep(a.ms); "wait" }
            is DeviceAction.AskUser, is DeviceAction.Answer, is DeviceAction.Terminate -> "no-op"
        }
    }

    /**
     * One touch stroke: through the bridge on MOS ROM phones, else an accessibility gesture.
     *
     * The bridge is used only when the stroke starts on a labelled element the safety gate
     * could see. Android hides `accessibilityDataSensitive` views (consent and permission
     * buttons) from us and ignores accessibility gestures on them, but not injected input.
     * Sending unlabelled targets through accessibility keeps that protection: the gate
     * can't judge what it can't see, so Android's own shield must stay in place.
     */
    private fun move(x1: Int, y1: Int, x2: Int, y2: Int, ms: Long) {
        val done = labelledTargetAt(x1, y1) && viaBridge("touch") {
            if (x1 == x2 && y1 == y2) it.tap(x1, y1, ms) else it.swipe(x1, y1, x2, y2, ms)
        }
        if (!done) gesture(stroke(x1, y1, x2, y2, ms))
    }

    /** Whether the smallest visible node under (x, y) carries a label itself or via its children. */
    private fun labelledTargetAt(x: Int, y: Int): Boolean {
        val root = rootInActiveWindow ?: return false
        var best: AccessibilityNodeInfo? = null
        var bestArea = Long.MAX_VALUE
        val r = Rect()
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.add(root)
        var seen = 0
        while (stack.isNotEmpty() && seen++ < 2000) {
            val n = stack.removeLast()
            if (!n.isVisibleToUser) continue
            n.getBoundsInScreen(r)
            if (!r.contains(x, y)) continue
            val area = r.width().toLong() * r.height()
            if (area <= bestArea) { best = n; bestArea = area }
            for (i in 0 until n.childCount) n.getChild(i)?.let { stack.add(it) }
        }
        val target = best ?: return false
        return hasLabel(target) || (target.isClickable && (0 until target.childCount).any { i ->
            target.getChild(i)?.let { hasLabel(it) } == true
        })
    }

    private fun hasLabel(n: AccessibilityNodeInfo) =
        !n.text.isNullOrBlank() || !n.contentDescription.isNullOrBlank()

    /**
     * True if the bridge did it; false if there is no bridge or it refused before injecting
     * anything (caller uses accessibility). Throws for every other result: the input may
     * have landed or the phone is in a state the agent must not drive, so no second attempt.
     */
    private fun viaBridge(what: String, f: (IMosBridge) -> Int): Boolean =
        when (val r = bridge.call(f)) {
            null, IMosBridge.REFUSED -> false
            IMosBridge.OK -> true
            IMosBridge.LOCKED -> throw IllegalStateException("phone is locked; $what not done")
            IMosBridge.BLOCKED -> throw IllegalStateException("$what refused on a permission/install screen")
            else -> throw IllegalStateException("$what may have partly happened (bridge result $r); not retrying")
        }

    private fun stroke(x1: Int, y1: Int, x2: Int, y2: Int, ms: Long): GestureDescription.StrokeDescription {
        val path = Path().apply { moveTo(x1.toFloat(), y1.toFloat()); lineTo(x2.toFloat(), y2.toFloat()) }
        return GestureDescription.StrokeDescription(path, 0, ms)
    }

    private fun gesture(s: GestureDescription.StrokeDescription) {
        val done = CountDownLatch(1)
        val ok = AtomicBoolean(false)
        val g = GestureDescription.Builder().addStroke(s).build()
        val dispatched = dispatchGesture(g, object : AccessibilityService.GestureResultCallback() {
            override fun onCompleted(d: GestureDescription?) { ok.set(true); done.countDown() }
            override fun onCancelled(d: GestureDescription?) { done.countDown() }
        }, null)
        if (!dispatched || !done.await(5, TimeUnit.SECONDS) || !ok.get()) {
            throw IllegalStateException("gesture was not performed")
        }
    }

    private fun focusedInput(): AccessibilityNodeInfo? =
        rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)

    private fun typeText(text: String): String {
        val node = focusedInput() ?: throw IllegalStateException("no focused text field; tap one first")
        val existing = if (node.isShowingHintText) "" else node.text?.toString() ?: ""
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, existing + text)
        }
        if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args) &&
            !viaBridge("typing") { it.text(text) }
        ) {
            throw IllegalStateException("field did not accept text")
        }
        return "typed ${text.length} chars"
    }

    private fun key(name: String): String {
        val code = when (name.lowercase()) {
            "back" -> KeyEvent.KEYCODE_BACK
            "home" -> KeyEvent.KEYCODE_HOME
            "menu" -> KeyEvent.KEYCODE_APP_SWITCH
            "enter" -> KeyEvent.KEYCODE_ENTER
            else -> throw IllegalArgumentException("unknown key $name")
        }
        if (viaBridge("key $name") { it.key(code) }) return "key $name"
        val ok = when (name.lowercase()) {
            "back" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "home" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "menu" -> performGlobalAction(GLOBAL_ACTION_RECENTS) // closest global action; no MENU key here
            "enter" -> focusedInput()?.performAction(
                AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id) ?: false
            else -> throw IllegalArgumentException("unknown key $name")
        }
        if (!ok) throw IllegalStateException("key $name had no effect")
        return "key $name"
    }

    private fun openApp(a: DeviceAction.Open): String {
        val launch = a.pkg?.let { packageManager.getLaunchIntentForPackage(it) }
        if (launch == null) {
            performGlobalAction(GLOBAL_ACTION_HOME)
            return "unknown app '${a.label}', went home"
        }
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        startActivity(launch)
        Thread.sleep(1500)
        return "opened ${a.label}"
    }
}
