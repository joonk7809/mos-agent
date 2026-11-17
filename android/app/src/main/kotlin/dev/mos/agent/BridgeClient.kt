package dev.mos.agent

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import android.os.RemoteException
import android.util.Log
import dev.mos.bridge.IMosBridge

/**
 * Connection to the MOS ROM's privileged input bridge (rom/vendor-extra/MosBridge).
 *
 * On a MOS ROM phone, taps, swipes and keys go through the bridge (real injected input,
 * works where accessibility gestures are ignored). Everywhere else the bridge is absent
 * and [call] returns false, so callers fall back to accessibility. A bridge call that
 * fails or is refused also returns false, with the same fallback.
 */
class BridgeClient(
    private val context: Context,
    private val onConnected: ((BridgeClient) -> Unit)? = null,
) {
    @Volatile
    private var bridge: IMosBridge? = null
    private var bound = false

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, service: IBinder) {
            bridge = IMosBridge.Stub.asInterface(service)
            Log.i(TAG, "bridge connected")
            onConnected?.invoke(this@BridgeClient)
        }

        override fun onServiceDisconnected(name: ComponentName) {
            bridge = null
        }

        override fun onBindingDied(name: ComponentName) {
            bridge = null
            disconnect()
            connect()
        }
    }

    /** Bind if the bridge exists and we hold its permission. Safe to call on any phone. */
    fun connect() {
        if (bound) return
        val intent = Intent(ACTION).setPackage(PACKAGE)
        try {
            bound = true
            // false = no bridge on this phone; the docs still ask for an unbind.
            if (!context.bindService(intent, connection, Context.BIND_AUTO_CREATE)) disconnect()
        } catch (e: SecurityException) {
            bound = false
            Log.i(TAG, "bridge present but this APK isn't signed with the MOS app key")
        }
    }

    fun disconnect() {
        if (bound) {
            try {
                context.unbindService(connection)
            } catch (_: IllegalArgumentException) {
            }
        }
        bound = false
        bridge = null
    }

    val available: Boolean get() = bridge != null

    /** Runs [f] on the bridge; false when there is no bridge, it refused, or it died. */
    fun call(f: (IMosBridge) -> Boolean): Boolean {
        val b = bridge ?: return false
        return try {
            f(b)
        } catch (e: RemoteException) {
            Log.w(TAG, "bridge call failed", e)
            false
        } catch (e: SecurityException) {
            Log.w(TAG, "bridge refused caller", e)
            false
        }
    }

    companion object {
        private const val TAG = "MOS"
        const val PACKAGE = "dev.mos.bridge"
        const val ACTION = "dev.mos.bridge.BIND"
    }
}
