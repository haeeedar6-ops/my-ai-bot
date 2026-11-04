package com.ryn.agent

/**
 * SafetyController: يفحص حدود الإحداثيات، يمنع الأوامر المحظورة (البنوك/كلمات المرور/النظام)، ويفعّل مفتاح الإيقاف الفوري.
 */
class SafetyController {
    @Volatile var isStopped: Boolean = false
    private val allowedActions = setOf(
        "observe", "click", "double_click", "type_text",
        "key_press", "scroll", "wait", "done", "ask_user"
    )
    private val blockedPattern = Regex(
        "(?i)(password|passcode|pin|bank|credit\\s*card|cvv|iban|seed\\s*phrase|format|factory\\s*reset|كلمة\\s*المرور|رمز\\s*سري|بنك|بطاقة)"
    )

    fun emergencyStop() {
        isStopped = true
    }

    fun resetStop() {
        isStopped = false
    }

    fun validateAction(action: String, text: String, reason: String, x: Int?, y: Int?, width: Int, height: Int): Pair<Boolean, String> {
        if (isStopped) return Pair(false, "🛑 مفتاح الإيقاف الطارئ مفعّل")
        val cleanAction = action.trim().lowercase()
        if (!allowedActions.contains(cleanAction)) {
            return Pair(false, "⛔ العملية غير مسموحة: $cleanAction")
        }
        if (blockedPattern.containsMatchIn("$text $reason")) {
            return Pair(false, "⛔ تم حظر العملية لحماية كلمات المرور والبيانات المالية")
        }
        if ((cleanAction == "click" || cleanAction == "double_click") && x != null && y != null) {
            if (x !in 0..width || y !in 0..height) {
                return Pair(false, "⛔ الإحداثيات ($x, $y) خارج حدود الشاشة (${width}x${height})")
            }
        }
        return Pair(true, "OK")
    }
}
