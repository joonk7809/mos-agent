package dev.mos.agent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.os.Build
import android.util.Log

/**
 * Lets the eval harness run tasks through the app (evals/run_evals.py --driver app), so the
 * system bridge can be compared with accessibility on the same tasks:
 *
 *   adb shell am broadcast -n dev.mos.agent/.EvalReceiver -a dev.mos.agent.EVAL_RUN --es task '...'
 *
 * Eval runs approve confirmations automatically, so this only works where that is harmless:
 * the broadcast needs DUMP (adb shell), the build must be debuggable, and the device must be
 * an emulator. The server URL and token come from the app's saved settings, never the intent.
 * The harness waits for the "MOS_TASK_END" log line.
 */
class EvalReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!allowed(context)) {
            Log.w(TAG, "eval broadcast ignored: not a debuggable build on an emulator")
            return
        }
        when (intent.action) {
            ACTION_STOP -> MosAccessibilityService.instance?.stopTask()
            ACTION_RUN -> run(context, intent.getStringExtra("task")?.trim().orEmpty())
        }
    }

    private fun run(context: Context, task: String) {
        val svc = MosAccessibilityService.instance ?: return end("no_service")
        val prefs = context.getSharedPreferences("mos", Context.MODE_PRIVATE)
        val server = prefs.getString("server", "").orEmpty()
        val token = prefs.getString("token", "").orEmpty()
        when {
            task.isEmpty() -> end("bad_request")
            server.isEmpty() || token.isEmpty() -> end("no_server_settings")
            !svc.startTask(task, server, token, eval = true) -> end("busy")
        }
    }

    private fun end(status: String) {
        Log.i(TAG, "MOS_TASK_END status=$status steps=0 eval=true")
    }

    companion object {
        private const val TAG = "MOS"
        const val ACTION_RUN = "dev.mos.agent.EVAL_RUN"
        const val ACTION_STOP = "dev.mos.agent.EVAL_STOP"

        fun allowed(context: Context): Boolean {
            val debuggable = (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
            return debuggable && isEmulator()
        }

        /** Goldfish/ranchu (Android Emulator) and Cuttlefish. */
        fun isEmulator(): Boolean =
            Build.HARDWARE in setOf("ranchu", "goldfish", "cutf_cvm", "vsoc_x86_64", "vsoc_arm64") ||
                Build.FINGERPRINT.startsWith("generic") ||
                Build.PRODUCT.contains("sdk_phone") || Build.PRODUCT.contains("cf_phone")
    }
}
