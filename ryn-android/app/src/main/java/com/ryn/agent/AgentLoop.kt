package com.ryn.agent

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.provider.Settings

/**
 * AgentLoop: تشغيل مهمة واحدة (من واجهة التطبيق أو من الدردشة) — مشترك بين MainActivity و PendingPoller.
 * التأكيد المحلي بيمر عبر confirmHandler اللي بتسجّله MainActivity؛ ولو الواجهة مسكّرة منفتحها تلقائياً.
 */
object AgentLoop {
    @Volatile var confirmHandler: ((AgentCommand) -> Boolean)? = null
    @Volatile var minimizeHandler: (() -> Unit)? = null

    private val main = Handler(Looper.getMainLooper())

    private fun requestConfirm(app: Context, cmd: AgentCommand): Boolean {
        var h = confirmHandler
        if (h == null) {
            // الواجهة مسكّرة: نفتحها (مسموح لأن التطبيق عنده إذن العرض فوق التطبيقات) وننتظر تسجيل الـ handler
            app.startActivity(Intent(app, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            val deadline = System.currentTimeMillis() + 6000
            while (h == null && System.currentTimeMillis() < deadline) {
                Thread.sleep(150)
                h = confirmHandler
            }
        }
        return h?.invoke(cmd) ?: false
    }

    /** المتطلبات الجاهزة قبل أي مهمة؛ ترجّع قائمة المشاكل (فاضية = جاهز). */
    fun prerequisites(context: Context, pairing: PairingManager): List<String> {
        val problems = mutableListOf<String>()
        if (!pairing.isPaired()) problems += "أقرن الجهاز أولاً"
        if (AgentAccessibilityService.instance == null) problems += "فعّل خدمة إمكانية الوصول"
        if (ProjectionService.capture == null) problems += "فعّل التقاط الشاشة"
        if (!Settings.canDrawOverlays(context)) problems += "اسمح بالعرض فوق التطبيقات (للإيقاف الفوري)"
        if (AgentRuntime.taskRunning) problems += "في مهمة شغالة حالياً"
        return problems
    }

    /** يُستدعى من خيط خلفي. يرجّع حالة المهمة. */
    fun run(context: Context, pairing: PairingManager, taskId: String): String {
        val app = context.applicationContext
        AgentRuntime.taskRunning = true
        main.post {
            // لو الروبوت العائم شغّال، هو نفسه زر الإيقاف
            if (!FloatingRobotService.active) app.startService(Intent(app, FloatingControlsService::class.java))
            main.postDelayed({ minimizeHandler?.invoke() }, 1200)
        }
        try {
            Thread.sleep(1500)
            val api = ApiClient(pairing.getServerUrl()) { pairing.getDeviceToken() }
            val runner = TaskRunner(
                api, AgentRuntime.safety, ActionDispatcher(AgentRuntime.safety),
                confirm = { cmd -> requestConfirm(app, cmd) }, log = { AgentRuntime.log(it) }
            )
            val res = runner.run(taskId)
            AgentRuntime.log("🏁 النتيجة: $res")
            return res
        } finally {
            AgentRuntime.taskRunning = false
            main.post { app.stopService(Intent(app, FloatingControlsService::class.java)) }
        }
    }
}
