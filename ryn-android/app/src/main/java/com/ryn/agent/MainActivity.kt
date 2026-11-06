package com.ryn.agent

import android.Manifest
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/**
 * MainActivity: واجهة إقران الجهاز، عرض حالة الأذونات والمهمة، زر الصوت، وزر الإيقاف الفوري (Stop).
 * الواجهة مبنية بالكود (بدون ملفات XML) لتبقى بسيطة.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var pairing: PairingManager
    private lateinit var statusView: TextView
    private lateinit var logView: TextView
    private lateinit var logScroll: ScrollView
    private lateinit var serverInput: EditText
    private lateinit var codeInput: EditText
    private lateinit var goalInput: EditText
    private val voice: VoiceController by lazy { VoiceController(this) }
    private val ui = Handler(Looper.getMainLooper())

    private val projectionLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
            val data = r.data
            if (r.resultCode == RESULT_OK && data != null) {
                ProjectionService.start(this, r.resultCode, data)
                ui.postDelayed({ refreshStatus() }, 800)
            } else {
                AgentRuntime.log("⚠️ لم تتم الموافقة على التقاط الشاشة")
            }
        }

    private val permLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { refreshStatus() }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun button(label: String, bg: String, fg: String = "#FFFFFF", onClick: () -> Unit): Button =
        Button(this).apply {
            text = label
            setTextColor(Color.parseColor(fg))
            setBackgroundColor(Color.parseColor(bg))
            setOnClickListener { onClick() }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(6) }
        }

    private fun input(hint: String, value: String = ""): EditText =
        EditText(this).apply {
            this.hint = hint
            setText(value)
            setTextColor(Color.parseColor("#EEF0FF"))
            setHintTextColor(Color.parseColor("#7C80A8"))
            setBackgroundColor(Color.parseColor("#0D0A26"))
            setPadding(dp(10), dp(10), dp(10), dp(10))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(6) }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pairing = PairingManager(this)
        AgentLoop.confirmHandler = { cmd -> confirmOnUi(cmd) }
        AgentLoop.minimizeHandler = { moveTaskToBack(true) }

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#070816"))
            setPadding(dp(14), dp(40), dp(14), dp(14))
        }

        root.addView(TextView(this).apply {
            text = "🤖 RYN Agent · أندرويد"
            setTextColor(Color.parseColor("#00FF9D"))
            textSize = 20f
            gravity = Gravity.CENTER
        })

        statusView = TextView(this).apply {
            setTextColor(Color.parseColor("#EEF0FF"))
            textSize = 13f
            setPadding(0, dp(8), 0, dp(8))
        }
        root.addView(statusView)

        serverInput = input("رابط السيرفر", pairing.getServerUrl())
        serverInput.inputType = InputType.TYPE_TEXT_VARIATION_URI
        root.addView(serverInput)

        codeInput = input("كود الإقران (6 أرقام)")
        codeInput.inputType = InputType.TYPE_CLASS_NUMBER
        root.addView(codeInput)
        root.addView(button("🔗 إقران الجهاز", "#8B5CF6") { doPair() })

        root.addView(button("♿ 1) تفعيل خدمة إمكانية الوصول", "#243B6B") {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        })
        root.addView(button("📺 2) تفعيل التقاط الشاشة", "#243B6B") { requestProjection() })
        root.addView(button("🪟 3) السماح بزر الإيقاف العائم", "#243B6B") { requestOverlay() })

        val listenBtn = button("", "#243B6B") {}
        fun paintListen() {
            listenBtn.text = if (pairing.isListening()) "📡 استقبال الأوامر من الدردشة: مفعّل" else "📡 استقبال الأوامر من الدردشة: مغلق"
        }
        paintListen()
        listenBtn.setOnClickListener { pairing.setListening(!pairing.isListening()); paintListen() }
        root.addView(listenBtn)

        goalInput = input("اكتب المهمة هنا", "افتح الإعدادات")
        root.addView(goalInput)
        root.addView(button("▶ بدء المهمة", "#00FF9D", "#031A10") { startFromGoal() })

        val talk = button("🎤 اضغط مطوّلاً وتكلم", "#3B82F6") {}
        talk.setOnTouchListener { v, e ->
            when (e.action) {
                MotionEvent.ACTION_DOWN -> { startVoice(); true }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> { voice.stop(); v.performClick(); true }
                else -> false
            }
        }
        root.addView(talk)

        root.addView(button("🛑 إيقاف فوري", "#FF5D6C") {
            AgentRuntime.safety.emergencyStop()
            AgentRuntime.log("🛑 تم ضغط زر الإيقاف")
        })

        logView = TextView(this).apply {
            setTextColor(Color.parseColor("#EEF0FF"))
            textSize = 12f
            setPadding(dp(8), dp(8), dp(8), dp(8))
        }
        logScroll = ScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#0D0A26"))
            addView(logView)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f
            ).apply { topMargin = dp(10) }
        }
        root.addView(logScroll)
        setContentView(root)

        // أندرويد 13+: بدون هذا الإذن ما بيظهر إشعار الخدمة الأمامية
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            permLauncher.launch(arrayOf(Manifest.permission.POST_NOTIFICATIONS))
        }
    }

    override fun onStart() {
        super.onStart()
        logView.text = AgentRuntime.history().joinToString("\n")
        AgentRuntime.listener = { msg ->
            logView.append(msg + "\n")
            logScroll.post { logScroll.fullScroll(View.FOCUS_DOWN) }
            refreshStatus()
        }
        refreshStatus()
    }

    override fun onStop() {
        AgentRuntime.listener = null
        super.onStop()
    }

    override fun onDestroy() {
        AgentLoop.confirmHandler = null
        AgentLoop.minimizeHandler = null
        voice.release()
        super.onDestroy()
    }

    private fun refreshStatus() {
        val a11y = AgentAccessibilityService.instance != null
        val proj = ProjectionService.capture != null
        val overlay = Settings.canDrawOverlays(this)
        statusView.text = listOf(
            if (pairing.isPaired()) "✅ الجهاز مقترن (${pairing.getDeviceId()})" else "❌ الجهاز غير مقترن",
            if (a11y) "✅ خدمة إمكانية الوصول" else "❌ خدمة إمكانية الوصول",
            if (proj) "✅ التقاط الشاشة" else "❌ التقاط الشاشة",
            if (overlay) "✅ زر الإيقاف العائم" else "❌ زر الإيقاف العائم (مطلوب قبل بدء المهمة)",
            if (AgentRuntime.taskRunning) "⚙️ مهمة شغالة" else "⏸ لا توجد مهمة"
        ).joinToString("\n")
    }

    private fun doPair() {
        val url = serverInput.text.toString().trim()
        val code = codeInput.text.toString().trim()
        if (url.isEmpty() || code.length != 6) { AgentRuntime.log("⚠️ أدخل رابط السيرفر وكود من 6 أرقام"); return }
        pairing.saveServerUrl(url)
        thread {
            try {
                val res = ApiClient(url) { null }.completePairing(code, Build.MODEL ?: "Android")
                pairing.saveDeviceCredential(res.getString("device_id"), res.getString("device_token"))
                AgentRuntime.log("✅ تم إقران الجهاز (${res.getString("device_id")})")
            } catch (e: Exception) {
                AgentRuntime.log("⛔ فشل الإقران: ${e.message}")
            }
            ui.post { refreshStatus() }
        }
    }

    private fun requestProjection() {
        val mpm = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projectionLauncher.launch(mpm.createScreenCaptureIntent())
    }

    private fun requestOverlay() {
        if (Settings.canDrawOverlays(this)) { AgentRuntime.log("✅ إذن العرض فوق التطبيقات مفعّل"); return }
        startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
    }

    private fun api(): ApiClient = ApiClient(pairing.getServerUrl()) { pairing.getDeviceToken() }

    /** يتأكد من كل المتطلبات قبل أي مهمة. */
    private fun prerequisitesOk(): Boolean {
        val problems = mutableListOf<String>()
        if (!pairing.isPaired()) problems += "أقرن الجهاز أولاً"
        if (AgentAccessibilityService.instance == null) problems += "فعّل خدمة إمكانية الوصول"
        if (ProjectionService.capture == null) problems += "فعّل التقاط الشاشة"
        if (!Settings.canDrawOverlays(this)) problems += "اسمح بزر الإيقاف العائم (أمان)"
        if (AgentRuntime.taskRunning) problems += "في مهمة شغالة حالياً"
        problems.forEach { AgentRuntime.log("⚠️ $it") }
        return problems.isEmpty()
    }

    private fun startFromGoal() {
        val goal = goalInput.text.toString().trim()
        if (goal.isEmpty() || !prerequisitesOk()) return
        AgentRuntime.taskRunning = true
        thread {
            try {
                val t = api().createTask(goal)
                AgentRuntime.log("🚀 بدأت المهمة ${t.optString("task_id")}: $goal")
                runTask(t.getString("task_id"))
            } catch (e: Exception) {
                AgentRuntime.log("⛔ خطأ: ${e.message}")
                AgentRuntime.taskRunning = false
                ui.post { refreshStatus() }
            }
        }
    }

    private fun startVoice() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            permLauncher.launch(arrayOf(Manifest.permission.RECORD_AUDIO))
            return
        }
        if (!prerequisitesOk()) return
        AgentRuntime.log("🎤 احكي هلأ...")
        voice.start(
            onFinal = { text ->
                AgentRuntime.log("🎤 سمعتك: $text")
                AgentRuntime.taskRunning = true
                thread {
                    try {
                        val res = api().sendVoice(text)
                        val id = res.optJSONObject("task")?.optString("task_id").orEmpty()
                        if (id.isEmpty()) throw IllegalStateException("السيرفر ما أنشأ مهمة")
                        runTask(id)
                    } catch (e: Exception) {
                        AgentRuntime.log("⛔ خطأ: ${e.message}")
                        AgentRuntime.taskRunning = false
                        ui.post { refreshStatus() }
                    }
                }
            },
            onFail = { AgentRuntime.log("⚠️ $it") }
        )
    }

    /** يُستدعى من خيط خلفي؛ التشغيل الفعلي مشترك مع استقبال أوامر الدردشة. */
    private fun runTask(taskId: String) {
        try {
            AgentLoop.run(applicationContext, pairing, taskId)
        } finally {
            ui.post { refreshStatus() }
        }
    }

    /** تأكيد محلي قبل العمليات متوسطة/عالية الخطورة؛ نجلب التطبيق للواجهة ثم نعرض الحوار. */
    private fun confirmOnUi(cmd: AgentCommand): Boolean {
        val latch = CountDownLatch(1)
        val ok = AtomicBoolean(false)
        ui.post {
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
            AlertDialog.Builder(this)
                .setTitle("تأكيد أمان RYN Agent")
                .setMessage("هل توافق على تنفيذ العملية؟\n\nالعملية: ${cmd.action}\nالسبب: ${cmd.reason}\nالخطورة: ${cmd.risk}")
                .setPositiveButton("موافق") { _, _ -> ok.set(true); latch.countDown() }
                .setNegativeButton("رفض") { _, _ -> latch.countDown() }
                .setOnCancelListener { latch.countDown() }
                .show()
        }
        latch.await(60, TimeUnit.SECONDS)
        return ok.get()
    }
}
