package com.ryn.agent

import android.content.Context
import android.provider.Settings
import kotlin.concurrent.thread

/**
 * PendingPoller: كل ~4 ثواني يسأل السيرفر إذا في أمر وصل من الدردشة ("افتح واتساب وارسل لأمي مرحبا").
 * يشتغل داخل الخدمة الأمامية (ProjectionService) فيضل شغال والتطبيق بالخلفية.
 * ما بيستعلم إلا إذا كل المتطلبات جاهزة (إقران + إمكانية وصول + التقاط شاشة + زر إيقاف عائم + استقبال مفعّل)،
 * فالدردشة بتعرف إنو الجهاز «غير متصل» لو ناقص شي.
 */
class PendingPoller(private val context: Context) {
    @Volatile private var running = false
    private var worker: Thread? = null
    private val pairing = PairingManager(context)

    fun start() {
        if (running) return
        running = true
        worker = thread(isDaemon = true, name = "ryn-pending-poller") { loop() }
    }

    fun stop() {
        running = false
        worker?.interrupt()
        worker = null
    }

    private fun loop() {
        while (running) {
            try {
                tick()
            } catch (e: InterruptedException) {
                break
            } catch (e: Exception) {
                // شبكة/سيرفر: نتجاهل ونعيد المحاولة بالدورة الجاية
            }
            try { Thread.sleep(4000) } catch (e: InterruptedException) { break }
        }
    }

    private fun tick() {
        if (AgentRuntime.taskRunning) return
        if (!pairing.isPaired() || !pairing.isListening()) return
        if (AgentAccessibilityService.instance == null || ProjectionService.capture == null) return
        if (!Settings.canDrawOverlays(context)) return

        val api = ApiClient(pairing.getServerUrl()) { pairing.getDeviceToken() }
        val res = api.pending()
        if (!res.optBoolean("pending", false)) return
        val id = res.optString("task_id")
        if (id.isEmpty()) return
        AgentRuntime.log("📥 وصل أمر من الدردشة: ${res.optString("goal")}")
        AgentLoop.run(context, pairing, id)
    }
}
