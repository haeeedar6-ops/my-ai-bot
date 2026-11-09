package com.ryn.agent

import kotlin.math.roundToInt

/**
 * TaskRunner: حلقة Capture -> Propose -> Validate -> Execute -> Verify (نفس منطق عميل ويندوز).
 * تُشغَّل دائماً من خيط خلفي (ليس من الخيط الرئيسي).
 */
class TaskRunner(
    private val api: ApiClient,
    private val safety: SafetyController,
    private val dispatcher: ActionDispatcher,
    private val confirm: (AgentCommand) -> Boolean,
    private val log: (String) -> Unit
) {
    private val terminal = setOf("COMPLETED", "CANCELLED", "FAILED")

    /** النموذج يرى صورة مصغّرة؛ نحوّل إحداثياته لإحداثيات الشاشة الحقيقية. */
    private fun scaleToScreen(cmd: AgentCommand, shot: Shot): AgentCommand {
        val x = cmd.x ?: return cmd
        val y = cmd.y ?: return cmd
        return cmd.copy(
            x = (x * shot.realW.toDouble() / shot.imageW).roundToInt(),
            y = (y * shot.realH.toDouble() / shot.imageH).roundToInt()
        )
    }

    fun run(taskId: String, maxSteps: Int = 20, approvalWaitMs: Long = 120_000): String {
        safety.reset()
        var sequence = 1
        var lastObs = "Initial Android screen"
        var steps = 0
        var waitingSince = 0L

        while (steps < maxSteps) {
            if (safety.isStopped) {
                log("🛑 تم إيقاف المهمة فوراً.")
                runCatching { api.stopTask(taskId) }
                return "CANCELLED"
            }
            val capture = ProjectionService.capture
            if (capture == null) {
                log("⛔ ما في إذن التقاط شاشة. اضغط «تفعيل التقاط الشاشة» أولاً.")
                return "NO_PROJECTION"
            }
            val shot = try {
                capture.capture()
            } catch (e: Exception) {
                log("⛔ تعذّر التقاط الشاشة: ${e.message}")
                return "FAILED"
            }

            val resp = try {
                api.requestNextStep(taskId, sequence, shot, lastObs)
            } catch (e: Exception) {
                log("⚠️ توقف آمن بسبب خطأ بالشبكة/السيرفر: ${e.message}")
                return "PAUSED_OFFLINE"
            }

            val st = resp.optString("status")
            if (st == "idle") {
                log("ℹ️ لا توجد مهمة نشطة لهذا الجهاز.")
                return "IDLE"
            }
            if (st == "WAITING_FOR_APPROVAL") {
                if (waitingSince == 0L) waitingSince = System.currentTimeMillis()
                if (System.currentTimeMillis() - waitingSince > approvalWaitMs) {
                    log("⌛ انتهت مهلة انتظار الموافقة.")
                    return "APPROVAL_TIMEOUT"
                }
                log("⏳ بانتظار موافقتك على العملية من لوحة التحكم...")
                Thread.sleep(1500)
                continue
            }
            waitingSince = 0L
            if (st in terminal) {
                log("✅ انتهت المهمة بحالة: $st")
                return st
            }
            val raw = AgentCommand.from(resp)
            if (raw.commandId.isEmpty()) {
                log("❓ المهمة تحتاج تدخلك ($st): ${raw.question.ifEmpty { resp.optString("message") }}")
                return st.ifEmpty { "NEEDS_USER_INPUT" }
            }

            steps++
            try {
                val cmd = scaleToScreen(raw, shot)
                safety.validate(cmd, shot.realW, shot.realH)
                if (safety.needsLocalConfirmation(cmd) && !confirm(cmd)) {
                    throw SecurityException("رفض المستخدم تنفيذ العملية.")
                }
                val note = dispatcher.execute(cmd, shot.realW, shot.realH)
                Thread.sleep(500) // نعطي الواجهة وقتاً لتتحدّث قبل لقطة التحقق
                val after = runCatching { capture.capture() }.getOrNull()
                val rep = api.reportResult(taskId, cmd.commandId, sequence, "succeeded", note = note, shot = after)
                lastObs = note
                log("✔ خطوة $sequence (${cmd.action}): $note")
                if (cmd.action == "ask_user") {
                    log("❓ سؤال من الوكيل: ${cmd.question.ifEmpty { cmd.reason }}")
                    return "NEEDS_USER_INPUT"
                }
                if (cmd.action == "done" || rep.optString("task_status") == "COMPLETED") {
                    return rep.optString("task_status").ifEmpty { "COMPLETED" }
                }
                sequence++
            } catch (e: Exception) {
                log("⛔ رُفضت أو فشلت الخطوة $sequence: ${e.message}")
                runCatching { api.reportResult(taskId, raw.commandId, sequence, "failed", error = e.message ?: "error") }
                return "FAILED"
            }
        }
        return "MAX_STEPS_REACHED"
    }
}
