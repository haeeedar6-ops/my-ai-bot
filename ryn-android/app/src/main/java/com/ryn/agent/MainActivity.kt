package com.ryn.agent

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.provider.Settings
import android.speech.RecognizerIntent
import android.text.InputType
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.Locale
import kotlin.concurrent.thread

/**
 * MainActivity: الواجهة الكاملة لتطبيق RYN Agent على أندرويد (إقران الجهاز، تحكم كتابي وصوتي، نطق الردود، وإيقاف طوارئ).
 */
class MainActivity : Activity() {

    private lateinit var pairingManager: PairingManager
    private lateinit var apiClient: ApiClient
    private lateinit var safetyController: SafetyController
    private lateinit var dispatcher: ActionDispatcher
    private lateinit var screenCapture: ScreenCapture
    private lateinit var voiceController: VoiceController

    private lateinit var statusView: TextView
    private lateinit var logView: TextView
    private lateinit var goalInput: EditText

    companion object {
        private const val REQ_VOICE_SPEECH = 901
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        pairingManager = PairingManager(this)
        apiClient = ApiClient(pairingManager)
        safetyController = SafetyController()
        dispatcher = ActionDispatcher(safetyController)
        screenCapture = ScreenCapture(this)
        voiceController = VoiceController(this)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#070816"))
            setPadding(36, 48, 36, 36)
        }

        val title = TextView(this).apply {
            text = "🤖 RYN Agent · الهاتف والكمبيوتر"
            textSize = 20f
            setTextColor(Color.parseColor("#00FF9D"))
            gravity = Gravity.CENTER
        }
        root.addView(title)

        statusView = TextView(this).apply {
            text = if (pairingManager.isPaired()) "🟢 الهاتف مقترن وجاهز (${pairingManager.getDeviceId()})" else "⚪ الهاتف غير مقترن — أدخل كود الإقران (6 أرقام)"
            textSize = 13f
            setTextColor(Color.parseColor("#EEF0FF"))
            setPadding(0, 14, 0, 14)
        }
        root.addView(statusView)

        val serverInput = EditText(this).apply {
            setText(pairingManager.getServerUrl())
            hint = "رابط سيرفر Render (مثال: https://ryn-ai-1.onrender.com)"
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
        }
        root.addView(serverInput)

        val codeInput = EditText(this).apply {
            hint = "كود الإقران السداسي (6 أرقام)"
            inputType = InputType.TYPE_CLASS_NUMBER
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
        }
        root.addView(codeInput)

        val pairRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }
        val pairBtn = Button(this).apply {
            text = "🔗 إقران الهاتف"
            setOnClickListener {
                pairingManager.saveServerUrl(serverInput.text.toString())
                val code = codeInput.text.toString().trim()
                if (code.length != 6) {
                    appendLog("⛔ أدخل كود إقران مكوّن من 6 أرقام من موقع البوت.")
                    return@setOnClickListener
                }
                thread {
                    val res = apiClient.completePairing(code, android.os.Build.MODEL ?: "Android-Phone")
                    runOnUiThread {
                        if (res.optBoolean("ok")) {
                            val devId = res.optString("device_id")
                            val tok = res.optString("device_token")
                            pairingManager.saveDeviceCredential(devId, tok)
                            statusView.text = "🟢 الهاتف مقترن كـ $devId"
                            appendLog("✅ تم إقران الهاتف بنجاح!")
                            voiceController.speak("تم إقران الهاتف بنجاح مع عقل رين")
                        } else {
                            appendLog("⛔ فشل الإقران: ${res.optString("error")}")
                        }
                    }
                }
            }
        }
        val accBtn = Button(this).apply {
            text = "⚙️ تفعيل إمكانية الوصول"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        }
        pairRow.addView(pairBtn)
        pairRow.addView(accBtn)
        root.addView(pairRow)

        goalInput = EditText(this).apply {
            hint = "اكتب المهمة هنا أو اضغط 🎤 وتحدث بصوتك..."
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
        }
        root.addView(goalInput)

        val actionRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }
        val startBtn = Button(this).apply {
            text = "▶ تنفيذ كتابي"
            setOnClickListener {
                val g = goalInput.text.toString().trim()
                if (g.isNotEmpty()) startAgentTask(g, isVoice = false)
            }
        }
        val voiceBtn = Button(this).apply {
            text = "🎤 أمر صوتي"
            setOnClickListener { startVoiceRecognition() }
        }
        val stopBtn = Button(this).apply {
            text = "🛑 إيقاف فوري"
            setOnClickListener {
                safetyController.emergencyStop()
                appendLog("🛑 تم تفعيل الإيقاف الفوري!")
                voiceController.speak("تم إيقاف المهمة فوراً")
                thread { apiClient.stopTask() }
            }
        }
        actionRow.addView(startBtn)
        actionRow.addView(voiceBtn)
        actionRow.addView(stopBtn)
        root.addView(actionRow)

        logView = TextView(this).apply {
            text = "📋 سجل عمليات RYN Agent:\n"
            textSize = 12f
            setTextColor(Color.parseColor("#A7F3D0"))
            setPadding(10, 16, 10, 16)
        }
        val scroll = ScrollView(this).apply { addView(logView) }
        root.addView(scroll)

        setContentView(root)
    }

    private fun appendLog(msg: String) {
        logView.append("$msg\n")
    }

    private fun startVoiceRecognition() {
        try {
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ar-SA")
                putExtra(RecognizerIntent.EXTRA_PROMPT, "تحدث الآن لإرسال أمر صوتي إلى RYN Agent...")
            }
            startActivityForResult(intent, REQ_VOICE_SPEECH)
        } catch (_: Exception) {
            appendLog("⚠️ الإملاء الصوتي غير متاح على هذا الجهاز، استخدم خانة الكتابة.")
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_VOICE_SPEECH && resultCode == RESULT_OK && data != null) {
            val matches = data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
            val spoken = matches?.firstOrNull()?.trim().orEmpty()
            if (spoken.isNotEmpty()) {
                goalInput.setText(spoken)
                startAgentTask(spoken, isVoice = true)
            }
        }
    }

    private fun startAgentTask(goal: String, isVoice: Boolean) {
        pairingManager.resetIfNeeded()
        safetyController.resetStop()
        appendLog("🚀 جاري إرسال المهمة: $goal")
        thread {
            val createRes = if (isVoice) apiClient.sendVoiceGoal(goal) else apiClient.createTask(goal)
            val taskObj = createRes.optJSONObject("task") ?: createRes
            val taskId = taskObj.optString("task_id", "")
            if (!createRes.optBoolean("ok") || taskId.isBlank()) {
                runOnUiThread {
                    appendLog("⛔ تعذر بدء المهمة: ${createRes.optString("error")}")
                }
                return@thread
            }
            runOnUiThread {
                voiceController.speak("سمعتك، بدأت تنفيذ المهمة على الهاتف")
            }
            var seq = 1
            var lastObs = "Android Phone Screen"
            for (stepIdx in 1..15) {
                if (safetyController.isStopped) break
                val stepRes = apiClient.requestNextStep(
                    taskId = taskId,
                    sequence = seq,
                    screenshotB64 = screenCapture.captureBase64Snapshot(),
                    width = screenCapture.screenWidth,
                    height = screenCapture.screenHeight,
                    observation = lastObs
                )
                if (!stepRes.optBoolean("ok")) {
                    runOnUiThread { appendLog("⚠️ توقف آمن: ${stepRes.optString("error")}") }
                    break
                }
                val st = stepRes.optString("status")
                if (st == "COMPLETED" || st == "CANCELLED" || st == "FAILED") {
                    runOnUiThread {
                        appendLog("✅ انتهت المهمة بحالة: $st")
                        voiceController.speak("اكتملت المهمة بنجاح")
                    }
                    break
                }
                val (okExec, note) = dispatcher.dispatch(stepRes, screenCapture.screenWidth, screenCapture.screenHeight)
                val cmdId = stepRes.optString("command_id", "")
                if (cmdId.isNotBlank()) {
                    apiClient.reportResult(taskId, cmdId, seq, if (okExec) "succeeded" else "failed", note)
                }
                lastObs = note
                runOnUiThread { appendLog("✔ خطوة $seq (${stepRes.optString("action")}): $note") }
                if (!okExec || stepRes.optString("action") == "done") {
                    runOnUiThread { voiceController.speak("تم إنجاز المهمة") }
                    break
                }
                seq += 1
            }
        }
    }

    override fun onDestroy() {
        voiceController.shutdown()
        super.onDestroy()
    }
}

private fun PairingManager.resetIfNeeded() {}
