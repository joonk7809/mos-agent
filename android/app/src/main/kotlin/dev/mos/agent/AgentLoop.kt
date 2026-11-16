package dev.mos.agent

import android.util.Log
import dev.mos.core.DeviceAction

/** One task: screen -> server -> action -> screen ..., on its own thread. */
class AgentLoop(
    private val service: MosAccessibilityService,
    private val client: ServerClient,
    private val task: String,
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
                    overlay.finished(doneText(r.status))
                    return
                }
                val a = r.action ?: continue
                overlay.status("Step ${r.step + 1}: ${r.thinking.lineSequence().lastOrNull { it.isNotBlank() }?.take(140) ?: describe(a)}")
                if (r.confirm != null && !overlay.confirm(r.confirm!!)) {
                    declined = true
                    continue
                }
                if (stopped) break
                when (a) {
                    is DeviceAction.AskUser -> reply = overlay.ask(a.text)
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
            Log.e("MOS", "task failed", e)
            overlay.finished("Error: ${e.message ?: e.javaClass.simpleName}")
        } finally {
            isRunning = false
        }
    }

    private fun doneText(status: String) = when (status) {
        "success" -> "Done ✓"
        "fail" -> "The agent couldn't finish this task"
        "stopped" -> "Stopped"
        "max_steps" -> "Gave up after too many steps"
        else -> "Ended: $status"
    }

    private fun describe(a: DeviceAction) = a.javaClass.simpleName
}
