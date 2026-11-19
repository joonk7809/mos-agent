package dev.mos.agent

import android.accessibilityservice.AccessibilityService
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import java.util.concurrent.CountDownLatch
import java.util.concurrent.SynchronousQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * A small panel at the top of the screen while a task runs: what the agent is doing,
 * a Stop button, and Allow/Deny or a reply box when it needs you.
 * Drawn as an accessibility overlay, so no "draw over other apps" permission is needed.
 * All public methods are safe to call from the agent thread.
 */
class Overlay(private val service: MosAccessibilityService) {
    private val main = Handler(Looper.getMainLooper())
    private val wm = service.getSystemService(WindowManager::class.java)
    private var root: LinearLayout? = null
    private lateinit var statusView: TextView
    private lateinit var buttons: LinearLayout
    private lateinit var input: EditText
    private var params: WindowManager.LayoutParams? = null
    private val answers = SynchronousQueue<String>()

    fun show() = main.post {
        if (root != null) {
            // A finished task's panel (with Close) is still up: make it a running panel again,
            // or the new task would run with no Stop button.
            statusView.text = "Starting…"
            input.visibility = View.GONE
            setFocusable(false)
            setButtons("Stop" to { service.stopTask(); Unit })
            return@post
        }
        val ctx = service
        statusView = TextView(ctx).apply { setTextColor(Color.WHITE); textSize = 14f; text = "Starting…" }
        input = EditText(ctx).apply {
            setTextColor(Color.WHITE); setHintTextColor(Color.LTGRAY); hint = "Your answer"; visibility = View.GONE
        }
        buttons = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.argb(225, 20, 20, 28))
            setPadding(32, 24, 32, 24)
            addView(statusView)
            addView(input)
            addView(buttons)
        }
        setButtons("Stop" to { service.stopTask(); Unit })
        params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        ).apply { gravity = Gravity.TOP }
        wm.addView(root, params)
    }

    fun remove() {
        // Close taps run on the main thread: remove right away, so a show() queued behind
        // the tap (a new task starting) still gets a panel instead of having it removed.
        if (Looper.myLooper() == Looper.getMainLooper()) removeNow() else main.post { removeNow() }
    }

    private fun removeNow() {
        root?.let { wm.removeView(it) }
        root = null
    }

    fun status(text: String) = main.post { if (root != null) statusView.text = text }

    /**
     * Hide during screenshots so the panel isn't part of what the model sees. Uses alpha, not
     * visibility: an INVISIBLE panel swallows taps without delivering them, so a Stop pressed
     * while a screenshot is taken would be lost.
     */
    fun setHiddenBlocking(hidden: Boolean) {
        val latch = CountDownLatch(1)
        main.post { root?.alpha = if (hidden) 0f else 1f; latch.countDown() }
        latch.await(1, TimeUnit.SECONDS)
        if (hidden) Thread.sleep(120) // let the frame without the panel reach the screen
    }

    /**
     * If a stroke starting at (x, y) would land on the panel, let touches fall through it
     * and return true; the caller must then call [endPassThrough]. The panel is hidden from
     * screenshots, so the model can't know it's there; without this, taps aimed at the top of
     * the screen land on Stop instead. Strokes clear of the panel leave it touchable, so a
     * human Stop is only lost while a stroke actually crosses the panel.
     */
    fun beginPassThroughIfHit(x: Int, y: Int): Boolean {
        val latch = CountDownLatch(1)
        val hit = AtomicBoolean(false)
        main.post {
            val p = params
            val v = root
            if (p != null && v != null && v.isAttachedToWindow) {
                val loc = IntArray(2)
                v.getLocationOnScreen(loc)
                val r = Rect(loc[0], loc[1], loc[0] + v.width, loc[1] + v.height)
                // A stroke's target window is fixed at touch-down, so only the start point matters.
                hit.set(r.contains(x, y))
                if (hit.get()) {
                    p.flags = p.flags or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                    wm.updateViewLayout(v, p)
                }
            }
            latch.countDown()
        }
        // If the main thread is too busy to answer, the post may still set the flag later:
        // report a hit so the caller always restores touch.
        if (!latch.await(1, TimeUnit.SECONDS)) return true
        // The flag reaches the input dispatcher on the next layout pass; the touch must not beat it.
        if (hit.get()) Thread.sleep(120)
        return hit.get()
    }

    /** Make the panel touchable again after [beginPassThroughIfHit] returned true. */
    fun endPassThrough() = main.post {
        val p = params ?: return@post
        val v = root ?: return@post
        p.flags = p.flags and WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE.inv()
        wm.updateViewLayout(v, p)
    }

    /** Ask the user to allow an action. Blocks the agent thread; false if denied or stopped. */
    fun confirm(reason: String): Boolean {
        main.post {
            statusView.text = "Allow this? $reason"
            setButtons("Allow" to { answers.offer("yes"); Unit }, "Deny" to { answers.offer("no"); Unit })
        }
        val a = answers.take()
        main.post { setButtons("Stop" to { service.stopTask(); Unit }) }
        return a == "yes"
    }

    /** Ask the user a question and block until they reply (empty string if they skip). */
    fun ask(question: String): String {
        main.post {
            statusView.text = question
            input.setText("")
            input.visibility = View.VISIBLE
            setFocusable(true)
            setButtons(
                "Send" to { answers.offer(input.text.toString()); Unit },
                "Skip" to { answers.offer(""); Unit },
            )
        }
        val a = answers.take()
        main.post {
            input.visibility = View.GONE
            setFocusable(false)
            setButtons("Stop" to { service.stopTask(); Unit })
        }
        return a
    }

    /** Unblock any pending confirm/ask (used by Stop). */
    fun cancelPending() { answers.offer("no") }

    fun finished(text: String) = main.post {
        if (root == null) return@post
        statusView.text = text
        input.visibility = View.GONE
        setFocusable(false)
        setButtons("Close" to { remove(); Unit })
    }

    private fun setFocusable(focusable: Boolean) {
        val p = params ?: return
        p.flags = if (focusable) p.flags and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE.inv()
        else p.flags or WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
        root?.let { wm.updateViewLayout(it, p) }
    }

    private fun setButtons(vararg items: Pair<String, () -> Unit>) {
        buttons.removeAllViews()
        for ((label, onClick) in items) {
            buttons.addView(Button(service).apply { text = label; setOnClickListener { onClick() } })
        }
    }
}
