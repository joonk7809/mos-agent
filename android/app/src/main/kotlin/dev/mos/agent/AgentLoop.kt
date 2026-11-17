package dev.mos.agent

import android.util.Log
import dev.mos.core.DeviceAction

/**
 * One task: screen -> server -> action -> screen ..., on its own thread.
 *
 * [eval] runs come from the eval driver (EvalReceiver, debuggable emulator builds only):
 * confirmations are approved and questions answered automatically, exactly as the ADB
 * eval harness does, so app runs and ADB runs are scored the same way.
 */
class AgentLoop(
    private val service: MosAccessibilityService,
    private val client: ServerClient,
    private val task: String,
    private val eval: Boolean = false,
) : Runnable {

    @Volatile
    var isRunning = false
        private set

    @Volatile
    private var stopped = false

    fun stop() {
        stopped = true
        service.overlay.cancelPending()
    }

    override fun run() {
        isRunning = true
        val overlay = service.overlay
        overlay.show()
        var steps = 0
        var status = "stopped"
        try {
            val (w, h) = service.screenSize()
            val session = client.start(task, service.launcherApps(), w, h)
            var reply: String? = null
            var declined = false
            while (!stopped) {
                val shot = service.captureScreenB64()
                val r = client.step(session, shot, service.dumpElements(), reply, declined)
                reply = null
                declined = false
                if (r.done) {
                    status = r.status
                    overlay.finished(doneText(r.status))
                    return
                }
                val a = r.action ?: continue
                steps++
                overlay.status("Step ${r.step + 1}: ${r.thinking.lineSequence().lastOrNull { it.isNotBlank() }?.take(140) ?: describe(a)}")
                if (r.confirm != null && !confirm(r.confirm!!)) {
                    declined = true
                    continue
                }
                if (stopped) break
                when (a) {
                    is DeviceAction.AskUser -> reply = if (eval) EVAL_REPLY else overlay.ask(a.text)
                    is DeviceAction.Answer -> overlay.status("Answer: ${a.text}")
                    else -> {
                        try {
                            service.execute(a)
                        } catch (e: Exception) {
                            Log.w("MOS", "action failed: $a", e) // the next screenshot shows the model it didn't work
                        }
                        Thread.sleep(700) // let the UI settle
                    }
                }
            }
            overlay.finished("Stopped")
        } catch (e: Exception) {
            status = "error"
            Log.e("MOS", "task failed", e)
            overlay.finished("Error: ${e.message ?: e.javaClass.simpleName}")
        } finally {
            isRunning = false
            // Read by evals/run_evals.py --driver app. No task text or screen content here.
            Log.i("MOS", "MOS_TASK_END status=$status steps=$steps eval=$eval")
        }
    }

    private fun confirm(reason: String): Boolean {
        if (!eval) return service.overlay.confirm(reason)
        Log.i("MOS", "eval run: auto-approved a confirmation")
        return true
    }

    private fun doneText(status: String) = when (status) {
        "success" -> "Done ✓"
        "fail" -> "The agent couldn't finish this task"
        "stopped" -> "Stopped"
        "max_steps" -> "Gave up after too many steps"
        else -> "Ended: $status"
    }

    private fun describe(a: DeviceAction) = a.javaClass.simpleName

    companion object {
        /** What the ADB eval harness answers too (evals/run_evals.py). */
        const val EVAL_REPLY = "Use your best judgment."
    }
}
