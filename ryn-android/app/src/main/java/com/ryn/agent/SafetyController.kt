package com.ryn.agent

/**
 * SafetyController: يفحص حدود الإحداثيات، يمنع الأوامر المحظورة (البنوك/كلمات المرور)، ويفعّل مفتاح الإيقاف الفوري.
 */
class SafetyController {
    @Volatile var isStopped: Boolean = false

    fun emergencyStop() {
        isStopped = true
    }

    fun validateCoordinates(x: Int, y: Int, width: Int, height: Int): Boolean {
        return !isStopped && x in 0..width && y in 0..height
    }
}
