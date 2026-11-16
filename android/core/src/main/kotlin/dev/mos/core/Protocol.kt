package dev.mos.core

import org.json.JSONArray
import org.json.JSONObject

/**
 * The phone side of the MOS protocol (see mos/server.py). Pure Kotlin + org.json,
 * so it is unit-tested on the JVM without an Android device.
 */
sealed class DeviceAction {
    data class Click(val x: Int, val y: Int) : DeviceAction()
    data class LongPress(val x: Int, val y: Int) : DeviceAction()
    /** [direction] is the direction the finger moves; null x/y means screen centre. */
    data class Swipe(val direction: String, val x: Int?, val y: Int?) : DeviceAction()
    data class Drag(val x1: Int, val y1: Int, val x2: Int, val y2: Int) : DeviceAction()
    data class Type(val text: String) : DeviceAction()
    data class Open(val label: String, val pkg: String?) : DeviceAction()
    data class Key(val name: String) : DeviceAction()
    data class Wait(val ms: Long) : DeviceAction()
    data class AskUser(val text: String) : DeviceAction()
    data class Answer(val text: String) : DeviceAction()
    data class Terminate(val status: String) : DeviceAction()

    companion object {
        fun parse(o: JSONObject): DeviceAction = when (val t = o.getString("type")) {
            "click" -> Click(o.getInt("x"), o.getInt("y"))
            "long_press" -> LongPress(o.getInt("x"), o.getInt("y"))
            "swipe" -> Swipe(
                o.optString("direction", "up"),
                if (o.has("x")) o.getInt("x") else null,
                if (o.has("y")) o.getInt("y") else null,
            )
            "drag" -> Drag(o.getInt("x1"), o.getInt("y1"), o.getInt("x2"), o.getInt("y2"))
            "type" -> Type(o.optString("text", ""))
            "open" -> Open(o.optString("label", ""), if (o.isNull("package")) null else o.optString("package"))
            "key" -> Key(o.optString("name", "back"))
            "wait" -> Wait(o.optLong("ms", 2000))
            "ask_user" -> AskUser(o.optString("text", ""))
            "answer" -> Answer(o.optString("text", ""))
            "terminate" -> Terminate(o.optString("status", "fail"))
            else -> throw IllegalArgumentException("unknown action type $t")
        }
    }
}

data class StepResult(
    val action: DeviceAction?,
    val thinking: String,
    val confirm: String?,
    val done: Boolean,
    val status: String,
    val step: Int,
) {
    companion object {
        fun parse(o: JSONObject) = StepResult(
            action = o.optJSONObject("action")?.let { DeviceAction.parse(it) },
            thinking = o.optString("thinking", ""),
            confirm = if (o.isNull("confirm")) null else o.optString("confirm"),
            done = o.optBoolean("done", false),
            status = o.optString("status", "running"),
            step = o.optInt("step", 0),
        )
    }
}

/** One UI element as the server expects it (same fields as mos/uitree.py Element.to_dict). */
data class Element(
    val text: String,
    val desc: String,
    val resId: String,
    val cls: String,
    val bounds: IntArray, // x1, y1, x2, y2 in screen pixels
    val clickable: Boolean,
    val editable: Boolean,
    val scrollable: Boolean,
    val focused: Boolean,
    val password: Boolean,
) {
    /** Keep only what a user could perceive or act on (mirrors mos/uitree.py). */
    fun worthKeeping(): Boolean {
        val w = bounds[2] - bounds[0]
        val h = bounds[3] - bounds[1]
        if (w <= 1 || h <= 1) return false
        return text.isNotBlank() || desc.isNotBlank() || clickable || editable || scrollable
    }

    fun toJson(): JSONObject = JSONObject()
        .put("text", text).put("desc", desc).put("res_id", resId).put("cls", cls)
        .put("bounds", JSONArray(bounds.toList()))
        .put("clickable", clickable).put("editable", editable).put("scrollable", scrollable)
        .put("focused", focused).put("password", password)
}

data class AppInfo(val label: String, val pkg: String)

object Protocol {
    fun startBody(task: String, apps: List<AppInfo>, width: Int, height: Int): JSONObject = JSONObject()
        .put("task", task)
        .put("apps", JSONArray(apps.map { JSONObject().put("label", it.label).put("package", it.pkg) }))
        .put("screen", JSONObject().put("width", width).put("height", height))

    fun stepBody(screenshotB64: String, elements: List<Element>, reply: String? = null, declined: Boolean = false): JSONObject {
        val o = JSONObject()
            .put("screenshot_b64", screenshotB64)
            .put("elements", JSONArray(elements.map { it.toJson() }))
        if (reply != null) o.put("reply", reply)
        if (declined) o.put("declined", true)
        return o
    }
}

object Geometry {
    /** Start/end of a swipe whose finger moves in [direction], centred on (x, y). Same as mos/remote.py. */
    fun swipe(direction: String, x: Int, y: Int, w: Int, h: Int): IntArray {
        val dx = (w * 0.3).toInt()
        val dy = (h * 0.3).toInt()
        val (vx, vy) = when (direction.lowercase()) {
            "down" -> 0 to dy
            "left" -> -dx to 0
            "right" -> dx to 0
            else -> 0 to -dy
        }
        fun clamp(v: Int, hi: Int) = v.coerceIn(1, hi - 2)
        return intArrayOf(
            clamp(x - pyFloorDiv(vx, 2), w), clamp(y - pyFloorDiv(vy, 2), h),
            clamp(x + pyFloorDiv(vx, 2), w), clamp(y + pyFloorDiv(vy, 2), h),
        )
    }

    /** Size to send screenshots at: long edge at most [maxEdge], aspect kept. */
    fun scaled(w: Int, h: Int, maxEdge: Int): Pair<Int, Int> {
        val long = maxOf(w, h)
        if (long <= maxEdge) return w to h
        val s = maxEdge.toDouble() / long
        return Math.round(w * s).toInt() to Math.round(h * s).toInt()
    }

    private fun pyFloorDiv(a: Int, b: Int) = Math.floorDiv(a, b)
}
