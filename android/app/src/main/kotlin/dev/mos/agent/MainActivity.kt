package dev.mos.agent

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** Settings (server URL, token) and a box to type a task. */
class MainActivity : Activity() {
    private val prefs by lazy { getSharedPreferences("mos", MODE_PRIVATE) }
    private lateinit var server: EditText
    private lateinit var token: EditText
    private lateinit var task: EditText
    private lateinit var state: TextView

    // On a MOS ROM phone the bridge can turn the agent's accessibility service on when the
    // user taps the button, skipping accessibility settings and the "restricted settings" step.
    // Never automatically: switching it off in Settings must keep it off.
    private val bridge = BridgeClient(this)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        server = EditText(this).apply {
            hint = "Server, e.g. http://100.x.y.z:8080"
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setText(prefs.getString("server", ""))
        }
        token = EditText(this).apply {
            hint = "Token (MOS_TOKEN on the server)"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setText(prefs.getString("token", ""))
        }
        task = EditText(this).apply { hint = "What should I do?"; minLines = 3 }
        state = TextView(this)
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
            addView(TextView(this@MainActivity).apply { text = "MOS"; textSize = 24f })
            addView(state)
            addView(Button(this@MainActivity).apply {
                text = "Turn on the agent"
                setOnClickListener { turnOnAgent() }
            })
            addView(server)
            addView(token)
            addView(task)
            addView(Button(this@MainActivity).apply { text = "Run"; setOnClickListener { run() } })
        }
        setContentView(ScrollView(this).apply {
            addView(col, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        })
    }

    override fun onStart() {
        super.onStart()
        bridge.connect()
    }

    override fun onStop() {
        bridge.disconnect()
        super.onStop()
    }

    override fun onResume() {
        super.onResume()
        refreshState()
    }

    private fun refreshState() {
        state.text = if (MosAccessibilityService.instance != null) "Agent service: on"
        else "Agent service: off. Turn on \"MOS agent\" in accessibility settings."
    }

    private fun turnOnAgent() {
        if (MosAccessibilityService.instance != null) return toast("The agent is already on")
        val enabled = try {
            bridge.call { it.enableAgentAccessibility() } == true
        } catch (e: IllegalStateException) {
            false
        }
        if (enabled) {
            state.postDelayed({ refreshState() }, 1000)
        } else {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) // stock phones
        }
    }

    private fun run() {
        val s = server.text.toString().trim()
        val t = token.text.toString().trim()
        val job = task.text.toString().trim()
        prefs.edit().putString("server", s).putString("token", t).apply()
        val svc = MosAccessibilityService.instance
        when {
            svc == null -> toast("Turn on the MOS agent in accessibility settings first")
            s.isEmpty() || t.isEmpty() -> toast("Set the server and token")
            job.isEmpty() -> toast("Type a task")
            !svc.startTask(job, s, t) -> toast("A task is already running")
            else -> moveTaskToBack(true) // get out of the way so the agent sees the phone
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
