package com.ryn.agent

import java.util.concurrent.ConcurrentHashMap

/**
 * SafetyController: يفحص حدود الإحداثيات، يمنع الأوامر المحظورة (البنوك/كلمات المرور)،
 * يرفض الأوامر المكررة/القديمة، ويفعّل مفتاح الإيقاف الفوري.
 */
class SafetyController {
    @Volatile var isStopped: Boolean = false
        private set

    private val lastSequence = ConcurrentHashMap<String, Int>()

    companion object {
        val ALLOWED_ACTIONS = setOf(
            "observe", "click", "double_click", "type_text", "key_press",
            "scroll", "wait", "done", "ask_user"
        )

        private val BLOCKED = Regex(
            "(?i)(\\brm\\s+-rf\\b|\\bdel\\s+/[fsq]\\b|\\bformat\\s+[a-z](?=:)|\\bpowershell\\b|\\bcmd\\.exe\\b|" +
                "\\bregedit\\b|\\bbcdedit\\b|\\bshutdown\\b|\\btaskkill\\b|" +
                "\\b(?:password|credit\\s*card|cvv|iban|bank|bitwarden|1password|seed\\s*phrase)\\b|" +
                "كلمة\\s*المرور|كلمات\\s*المرور|بطاقة\\s*ائتمان|تحويل\\s*بنكي)"
        )
    }

    fun emergencyStop() { isStopped = true }
    fun reset() { isStopped = false }

    fun validateCoordinates(x: Int, y: Int, width: Int, height: Int): Boolean =
        !isStopped && x in 0..width && y in 0..height

    /** يرمي استثناء إن كان الأمر مرفوضاً؛ ويسجّل رقم التسلسل عند القبول. */
    fun validate(cmd: AgentCommand, screenW: Int, screenH: Int) {
        if (isStopped) throw SecurityException("🛑 مفتاح الإيقاف الطارئ مفعّل.")
        if (cmd.action !in ALLOWED_ACTIONS) throw IllegalArgumentException("عملية غير مسموحة محلياً: ${cmd.action}")

        val last = lastSequence[cmd.taskId] ?: 0
        if (cmd.sequence <= last) throw IllegalArgumentException("رفض أمر مكرر أو قديم (sequence=${cmd.sequence} <= $last).")

        if (BLOCKED.containsMatchIn("${cmd.reason} ${cmd.text}")) {
            throw SecurityException("⛔ حظر محلي: الأمر يتضمن كلمات مرور أو معاملات مالية أو أوامر نظام محظورة.")
        }

        if (cmd.action == "click" || cmd.action == "double_click") {
            val x = cmd.x
            val y = cmd.y
            if (x == null || y == null || !validateCoordinates(x, y, screenW, screenH)) {
                throw IllegalArgumentException("الإحداثيات ($x, $y) خارج حدود الشاشة (${screenW}x$screenH).")
            }
        }
        lastSequence[cmd.taskId] = cmd.sequence
    }

    fun needsLocalConfirmation(cmd: AgentCommand): Boolean {
        if (cmd.action in setOf("observe", "done", "wait", "ask_user")) return false
        return cmd.requiresConfirmation || cmd.risk == "medium" || cmd.risk == "high"
    }
}
