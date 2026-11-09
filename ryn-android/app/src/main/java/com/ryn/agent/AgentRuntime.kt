package com.ryn.agent

import android.os.Handler
import android.os.Looper

/** حالة مشتركة بين الواجهة والخدمات: مفتاح الطوارئ + سجل النشاط. */
object AgentRuntime {
    val safety = SafetyController()

    @Volatile var taskRunning: Boolean = false
    @Volatile var listener: ((String) -> Unit)? = null

    private val buffer = ArrayDeque<String>()
    private val main = Handler(Looper.getMainLooper())

    fun log(msg: String) {
        synchronized(buffer) {
            buffer.addLast(msg)
            while (buffer.size > 200) buffer.removeFirst()
        }
        val l = listener
        if (l != null) main.post { l(msg) }
    }

    fun history(): List<String> = synchronized(buffer) { buffer.toList() }
}
