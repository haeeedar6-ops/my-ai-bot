package com.ryn.agent

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * ActionDispatcher: يوجه الأوامر المفحوصة (click, scroll, type_text, wait, done, ask_user ...)
 * إلى خدمة إمكانية الوصول. الإحداثيات هنا بالفعل بإحداثيات الشاشة الحقيقية.
 */
class ActionDispatcher(private val safetyController: SafetyController) {

    fun canDispatch(action: String): Boolean =
        !safetyController.isStopped && SafetyController.ALLOWED_ACTIONS.contains(action)

    fun execute(cmd: AgentCommand, screenW: Int, screenH: Int): String {
        if (!canDispatch(cmd.action)) throw IllegalStateException("العملية غير مسموحة أو الإيقاف الطارئ مفعّل: ${cmd.action}")
        val svc = AgentAccessibilityService.instance
        val needsSvc = cmd.action in setOf("click", "double_click", "type_text", "key_press", "scroll")
        if (needsSvc && svc == null) throw IllegalStateException("فعّل خدمة إمكانية الوصول لـ RYN Agent أولاً")

        return when (cmd.action) {
            "observe" -> "Screen observed and sent for analysis"
            "click" -> {
                val x = cmd.x!!; val y = cmd.y!!
                if (!svc!!.tap(x, y)) throw IllegalStateException("فشل النقر عند ($x, $y)")
                "Clicked at ($x, $y)"
            }
            "double_click" -> {
                val x = cmd.x!!; val y = cmd.y!!
                if (!svc!!.tap(x, y, double = true)) throw IllegalStateException("فشل النقر المزدوج عند ($x, $y)")
                "Double-clicked at ($x, $y)"
            }
            "type_text" -> {
                if (!svc!!.typeText(cmd.text)) throw IllegalStateException("فشلت الكتابة")
                "Typed text (${cmd.text.length} chars)"
            }
            "key_press" -> {
                if (!svc!!.pressKey(cmd.key, screenW, screenH)) throw IllegalStateException("فشل الضغط على '${cmd.key}'")
                "Pressed key '${cmd.key}'"
            }
            "scroll" -> {
                // scroll_dy سالب = تمرير للأسفل (نفس عقد ويندوز) => سحب الإصبع للأعلى
                val dy = if (cmd.scrollDy == 0) -300 else cmd.scrollDy
                val dist = min((abs(dy) * 1.5).toInt().let { max(it, 300) }, (screenH * 0.6).toInt())
                val cx = screenW / 2
                val y1 = if (dy < 0) (screenH * 0.75).toInt() else (screenH * 0.25).toInt()
                val y2 = if (dy < 0) y1 - dist else y1 + dist
                if (!svc!!.swipe(cx, y1, cx, y2)) throw IllegalStateException("فشل التمرير")
                "Scrolled by $dy"
            }
            "wait" -> {
                val sec = cmd.waitSeconds.coerceIn(0.2, 10.0)
                var left = (sec * 1000).toLong()
                while (left > 0 && !safetyController.isStopped) {
                    val step = min(left, 100L)
                    Thread.sleep(step)
                    left -= step
                }
                "Waited ${"%.1f".format(sec)}s"
            }
            "done" -> "Task marked done"
            "ask_user" -> "Asked user: ${cmd.question.ifEmpty { cmd.reason }}"
            else -> throw IllegalStateException("لا توجد دالة تنفيذ للعملية: ${cmd.action}")
        }
    }
}
