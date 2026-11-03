package com.ryn.agent

/**
 * ActionDispatcher: يوجه الأوامر المفحوصة (click, scroll, type_text, wait, done, ask_user) إلى الخدمة المناسبة.
 */
class ActionDispatcher(private val safetyController: SafetyController) {
    private val allowedActions = setOf("observe", "click", "double_click", "type_text", "key_press", "scroll", "wait", "done", "ask_user")

    fun canDispatch(action: String): Boolean = !safetyController.isStopped && allowedActions.contains(action)
}
