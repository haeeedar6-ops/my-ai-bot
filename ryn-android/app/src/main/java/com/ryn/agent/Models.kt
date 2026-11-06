package com.ryn.agent

import org.json.JSONObject
import kotlin.math.roundToInt

/** أمر واحد قادم من السيرفر (نفس عقد /api/v1/agent/step). */
data class AgentCommand(
    val taskId: String,
    val commandId: String,
    val sequence: Int,
    val action: String,
    val x: Int?,
    val y: Int?,
    val text: String,
    val key: String,
    val scrollDy: Int,
    val waitSeconds: Double,
    val reason: String,
    val risk: String,
    val requiresConfirmation: Boolean,
    val question: String
) {
    companion object {
        private fun JSONObject.str(name: String): String =
            if (!has(name) || isNull(name)) "" else optString(name, "")

        private fun JSONObject.num(name: String): Int? {
            if (!has(name) || isNull(name)) return null
            val d = optDouble(name)
            return if (d.isNaN() || d.isInfinite()) null else d.roundToInt()
        }

        fun from(j: JSONObject): AgentCommand {
            val c = j.optJSONObject("coordinates")
            return AgentCommand(
                taskId = j.str("task_id"),
                commandId = j.str("command_id"),
                sequence = j.optInt("sequence", 0),
                action = j.str("action").trim().lowercase(),
                x = c?.num("x"),
                y = c?.num("y"),
                text = j.str("text"),
                key = j.str("key").trim().lowercase(),
                scrollDy = j.optInt("scroll_dy", 0),
                waitSeconds = j.optDouble("wait_seconds", 1.0).let { if (it.isNaN()) 1.0 else it },
                reason = j.str("reason"),
                risk = j.str("risk").ifEmpty { "low" }.lowercase(),
                requiresConfirmation = j.optBoolean("requires_confirmation", false),
                question = j.str("question")
            )
        }
    }
}

/** لقطة شاشة مضغوطة + أبعاد الصورة المرسلة وأبعاد الشاشة الحقيقية (لتحويل الإحداثيات). */
data class Shot(
    val b64: String,
    val imageW: Int,
    val imageH: Int,
    val realW: Int,
    val realH: Int
)
