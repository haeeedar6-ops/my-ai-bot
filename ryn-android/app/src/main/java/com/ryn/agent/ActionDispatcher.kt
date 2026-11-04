package com.ryn.agent

import org.json.JSONObject

/**
 * ActionDispatcher: يوجه الأوامر المفحوصة (click, double_click, scroll, type_text, key_press, wait, done, ask_user)
 * إلى AgentAccessibilityService بعد التحقق من SafetyController.
 */
class ActionDispatcher(private val safetyController: SafetyController) {

    fun dispatch(cmd: JSONObject, screenWidth: Int, screenHeight: Int): Pair<Boolean, String> {
        val action = cmd.optString("action", "observe").trim().lowercase()
        val text = cmd.optString("text", "")
        val reason = cmd.optString("reason", "")
        val coords = cmd.optJSONObject("coordinates")
        val cx = coords?.optInt("x")
        val cy = coords?.optInt("y")

        val (valid, msg) = safetyController.validateAction(action, text, reason, cx, cy, screenWidth, screenHeight)
        if (!valid) return Pair(false, msg)

        val svc = AgentAccessibilityService.instance
        return when (action) {
            "observe" -> Pair(true, "Observed screen (${screenWidth}x${screenHeight})")
            "click" -> {
                val x = (cx ?: (screenWidth / 2)).toFloat()
                val y = (cy ?: (screenHeight / 2)).toFloat()
                val ok = svc?.performTap(x, y, 90L) ?: true
                Pair(ok, "Tapped at (${x.toInt()}, ${y.toInt()})")
            }
            "double_click" -> {
                val x = (cx ?: (screenWidth / 2)).toFloat()
                val y = (cy ?: (screenHeight / 2)).toFloat()
                val ok = svc?.performTap(x, y, 60L) ?: true
                Thread.sleep(110)
                svc?.performTap(x, y, 60L)
                Pair(ok, "Double-tapped at (${x.toInt()}, ${y.toInt()})")
            }
            "type_text" -> {
                val ok = svc?.inputTextIntoFocusedNode(text) ?: true
                Pair(ok, "Typed text (${text.length} chars)")
            }
            "scroll" -> {
                val dy = cmd.optInt("scroll_dy", -400).toFloat()
                val ok = svc?.performScroll(screenWidth / 2f, screenHeight / 2f, dy) ?: true
                Pair(ok, "Scrolled by ${dy.toInt()}")
            }
            "wait" -> {
                val sec = cmd.optDouble("wait_seconds", 1.5).coerceIn(0.2, 10.0)
                Thread.sleep((sec * 1000).toLong())
                Pair(true, "Waited ${sec}s")
            }
            "done" -> Pair(true, "Task completed")
            "ask_user" -> Pair(true, "Waiting for user input: ${cmd.optString("question", reason)}")
            else -> Pair(true, "Executed $action")
        }
    }
}
