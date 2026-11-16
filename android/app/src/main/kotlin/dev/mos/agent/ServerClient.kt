package dev.mos.agent

import dev.mos.core.AppInfo
import dev.mos.core.Element
import dev.mos.core.Protocol
import dev.mos.core.StepResult
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** Blocking HTTP client for the MOS server. Call from a background thread. */
class ServerClient(private val baseUrl: String, private val token: String) {

    fun start(task: String, apps: List<AppInfo>, width: Int, height: Int): String =
        post("/v1/sessions", Protocol.startBody(task, apps, width, height)).getString("session_id")

    fun step(sessionId: String, screenshotB64: String, elements: List<Element>,
             reply: String? = null, declined: Boolean = false): StepResult =
        StepResult.parse(post("/v1/sessions/$sessionId/step",
            Protocol.stepBody(screenshotB64, elements, reply, declined)))

    private fun post(path: String, body: JSONObject): JSONObject {
        val conn = URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 10_000
            conn.readTimeout = 180_000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Authorization", "Bearer $token")
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            if (code !in 200..299) throw IOException("server returned $code: ${text.take(200)}")
            return JSONObject(text)
        } finally {
            conn.disconnect()
        }
    }
}
