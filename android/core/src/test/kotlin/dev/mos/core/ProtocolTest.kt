package dev.mos.core

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    @Test
    fun parsesEveryServerActionType() {
        fun p(s: String) = DeviceAction.parse(JSONObject(s))
        assertEquals(DeviceAction.Click(540, 1200), p("""{"type":"click","x":540,"y":1200}"""))
        assertEquals(DeviceAction.LongPress(1, 2), p("""{"type":"long_press","x":1,"y":2}"""))
        assertEquals(DeviceAction.Swipe("up", null, null), p("""{"type":"swipe","direction":"up"}"""))
        assertEquals(DeviceAction.Swipe("left", 5, 6), p("""{"type":"swipe","direction":"left","x":5,"y":6}"""))
        assertEquals(DeviceAction.Drag(1, 2, 3, 4), p("""{"type":"drag","x1":1,"y1":2,"x2":3,"y2":4}"""))
        assertEquals(DeviceAction.Type("héllo 👋"), p("""{"type":"type","text":"héllo 👋"}"""))
        assertEquals(DeviceAction.Open("Settings", "com.android.settings"),
            p("""{"type":"open","label":"Settings","package":"com.android.settings"}"""))
        assertEquals(DeviceAction.Open("Nope", null), p("""{"type":"open","label":"Nope","package":null}"""))
        assertEquals(DeviceAction.Key("enter"), p("""{"type":"key","name":"enter"}"""))
        assertEquals(DeviceAction.Wait(2000), p("""{"type":"wait","ms":2000}"""))
        assertEquals(DeviceAction.AskUser("Which?"), p("""{"type":"ask_user","text":"Which?"}"""))
        assertEquals(DeviceAction.Answer("42"), p("""{"type":"answer","text":"42"}"""))
        assertEquals(DeviceAction.Terminate("success"), p("""{"type":"terminate","status":"success"}"""))
    }

    @Test
    fun parsesStepResult() {
        val r = StepResult.parse(JSONObject(
            """{"action":{"type":"click","x":1,"y":2},"thinking":"t","confirm":"Tap \"Send\"","done":false,"status":"running","step":3}"""))
        assertEquals(DeviceAction.Click(1, 2), r.action)
        assertEquals("Tap \"Send\"", r.confirm)
        assertEquals(3, r.step)
        val done = StepResult.parse(JSONObject("""{"action":null,"thinking":"","confirm":null,"done":true,"status":"stopped","step":4}"""))
        assertNull(done.action)
        assertNull(done.confirm)
        assertTrue(done.done)
    }

    @Test
    fun buildsRequestBodiesTheServerAccepts() {
        val start = Protocol.startBody("open settings", listOf(AppInfo("Settings", "com.android.settings")), 1080, 2400)
        assertEquals("com.android.settings", start.getJSONArray("apps").getJSONObject(0).getString("package"))
        assertEquals(2400, start.getJSONObject("screen").getInt("height"))
        val el = Element("Send", "", "app:id/send", "android.widget.Button", intArrayOf(0, 0, 10, 10),
            clickable = true, editable = false, scrollable = false, focused = false, password = false)
        val step = Protocol.stepBody("AAAA", listOf(el), reply = "7:30")
        assertEquals("7:30", step.getString("reply"))
        assertFalse(step.has("declined"))
        val e = step.getJSONArray("elements").getJSONObject(0)
        assertEquals("app:id/send", e.getString("res_id"))
        assertEquals(10, e.getJSONArray("bounds").getInt(3))
        assertTrue(Protocol.stepBody("A", emptyList(), declined = true).getBoolean("declined"))
    }

    @Test
    fun filtersElementsLikeThePythonSide() {
        fun el(text: String = "", click: Boolean = false, b: IntArray = intArrayOf(0, 0, 100, 100)) =
            Element(text, "", "", "View", b, click, false, false, false, false)
        assertTrue(el(text = "Hi").worthKeeping())
        assertTrue(el(click = true).worthKeeping())
        assertFalse(el().worthKeeping())
        assertFalse(el(text = "Hi", b = intArrayOf(0, 0, 0, 0)).worthKeeping())
    }

    @Test
    fun swipeMatchesPythonImplementation() {
        // values from mos.remote.swipe_vector
        assertArrayEquals(intArrayOf(540, 1560, 540, 840), Geometry.swipe("up", 540, 1200, 1080, 2400))
        assertArrayEquals(intArrayOf(702, 1200, 378, 1200), Geometry.swipe("left", 540, 1200, 1080, 2400))
        assertArrayEquals(intArrayOf(540, 1, 540, 360), Geometry.swipe("down", 540, 0, 1080, 2400))
    }

    @Test
    fun scalesScreenshotsByLongEdge() {
        assertEquals(1080 to 2400, Geometry.scaled(1080, 2400, 2400))
        assertEquals(720 to 1600, Geometry.scaled(1080, 2400, 1600))
    }
}
