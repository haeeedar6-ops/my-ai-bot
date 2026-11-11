package com.ryn.agent

import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.concurrent.thread
import kotlin.math.abs

/**
 * FloatingRobotService: روبوت عائم فوق كل التطبيقات.
 *  - اضغط الروبوت: تنفتح لوحة صغيرة (اكتب أمر أو اضغط 🎤) وبيتنفذ على الجهاز.
 *  - أثناء تنفيذ مهمة الروبوت بيتحول لزر ⏹ أحمر؛ اضغطه للإيقاف الفوري.
 *  - اسحبه لأي مكان بالشاشة. ✕ بلوحة الأوامر بتسكّره.
 */
class FloatingRobotService : Service() {
    companion object {
        @Volatile var active: Boolean = false

        fun start(context: Context) {
            context.startService(Intent(context, FloatingRobotService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, FloatingRobotService::class.java))
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var wm: WindowManager
    private lateinit var pairing: PairingManager
    private var bubble: TextView? = null
    private var panel: LinearLayout? = null
    private var input: EditText? = null
    private var micBtn: Button? = null
    private var voice: VoiceController? = null
    private var bubbleParams: WindowManager.LayoutParams? = null
    private var ticker: Runnable? = null

    override fun onBind(intent: Intent?): IBinder? = null

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (bubble == null) {
            if (!Settings.canDrawOverlays(this)) {
                AgentRuntime.log("⚠️ فعّل إذن العرض فوق التطبيقات أولاً")
                stopSelf()
                return START_NOT_STICKY
            }
            wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
            pairing = PairingManager(this)
            voice = VoiceController(this)
            showBubble()
            startTicker()
            active = true
            AgentRuntime.log("🫧 الروبوت العائم شغّال")
        }
        return START_NOT_STICKY
    }

    // ---------- الفقاعة ----------
    private fun circle(color: String): GradientDrawable =
        GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(Color.parseColor(color))
            setStroke(dp(2), Color.parseColor("#00FF9D"))
        }

    private fun showBubble() {
        val size = dp(58)
        val tv = TextView(this).apply {
            text = "🤖"
            textSize = 28f
            gravity = Gravity.CENTER
            background = circle("#8B5CF6")
        }
        val lp = WindowManager.LayoutParams(
            size, size,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = resources.displayMetrics.widthPixels - size - dp(8)
            y = dp(180)
        }
        tv.setOnTouchListener(object : View.OnTouchListener {
            var startX = 0; var startY = 0; var touchX = 0f; var touchY = 0f; var moved = false
            override fun onTouch(v: View, e: MotionEvent): Boolean {
                when (e.action) {
                    MotionEvent.ACTION_DOWN -> {
                        startX = lp.x; startY = lp.y; touchX = e.rawX; touchY = e.rawY; moved = false
                    }
                    MotionEvent.ACTION_MOVE -> {
                        val dx = (e.rawX - touchX).toInt(); val dy = (e.rawY - touchY).toInt()
                        if (abs(dx) > dp(6) || abs(dy) > dp(6)) moved = true
                        lp.x = startX + dx; lp.y = startY + dy
                        try { wm.updateViewLayout(tv, lp) } catch (_: Exception) {}
                    }
                    MotionEvent.ACTION_UP -> if (!moved) onBubbleClick()
                }
                return true
            }
        })
        wm.addView(tv, lp)
        bubble = tv
        bubbleParams = lp
    }

    private fun onBubbleClick() {
        if (AgentRuntime.taskRunning) {
            AgentRuntime.safety.emergencyStop()
            AgentRuntime.log("🛑 إيقاف من الروبوت العائم")
        } else if (panel == null) {
            showPanel()
        } else {
            hidePanel()
        }
    }

    // يحدّث شكل الفقاعة: روبوت عادي / ⏹ أثناء المهمة
    private fun startTicker() {
        val r = object : Runnable {
            override fun run() {
                val b = bubble
                if (b != null) {
                    if (AgentRuntime.taskRunning) {
                        if (b.text.toString() != "⏹") { b.text = "⏹"; b.background = circle("#FF5D6C") }
                        if (panel != null) hidePanel()
                    } else if (b.text.toString() != "🤖") {
                        b.text = "🤖"; b.background = circle("#8B5CF6")
                    }
                    main.postDelayed(this, 400)
                }
            }
        }
        ticker = r
        main.post(r)
    }

    // ---------- لوحة الأوامر ----------
    private fun showPanel() {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(12))
            background = GradientDrawable().apply {
                cornerRadius = dp(20).toFloat()
                setColor(Color.parseColor("#252628"))
                setStroke(dp(1), Color.parseColor("#33FFFFFF"))
            }
        }
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        head.addView(TextView(this).apply {
            text = "🤖 قلي شو أنفّذ؟"
            setTextColor(Color.WHITE); textSize = 15f
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        })
        head.addView(Button(this).apply {
            text = "✕"
            setTextColor(Color.WHITE); setBackgroundColor(Color.TRANSPARENT)
            setOnClickListener { stopSelf() }
        })
        box.addView(head)

        val et = EditText(this).apply {
            hint = "مثال: افتح واتساب وارسل لأمي مرحبا"
            setTextColor(Color.WHITE); setHintTextColor(Color.parseColor("#9A9DB8"))
            setBackgroundColor(Color.parseColor("#14151A"))
            setPadding(dp(10), dp(10), dp(10), dp(10))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            imeOptions = EditorInfo.IME_ACTION_SEND
            maxLines = 3
            setOnEditorActionListener { _, actionId, _ ->
                if (actionId == EditorInfo.IME_ACTION_SEND) { submit(text.toString()); true } else false
            }
        }
        input = et
        box.addView(et)

        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val mic = Button(this).apply {
            text = "🎤"
            setTextColor(Color.WHITE); setBackgroundColor(Color.parseColor("#3B82F6"))
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(6); topMargin = dp(8) }
            setOnClickListener { toggleVoice() }
        }
        micBtn = mic
        row.addView(mic)
        row.addView(Button(this).apply {
            text = "▶ نفّذ"
            setTextColor(Color.parseColor("#031A10")); setBackgroundColor(Color.parseColor("#00FF9D"))
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 2f).apply { topMargin = dp(8) }
            setOnClickListener { submit(et.text.toString()) }
        })
        box.addView(row)

        val bp = bubbleParams
        val lp = WindowManager.LayoutParams(
            minOf(resources.displayMetrics.widthPixels - dp(24), dp(340)),
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = dp(12)
            y = (bp?.y ?: dp(180)) + dp(64)
            softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE
        }
        wm.addView(box, lp)
        panel = box
        et.requestFocus()
    }

    private fun hidePanel() {
        voice?.release()
        try { panel?.let { wm.removeView(it) } } catch (_: Exception) {}
        panel = null
        input = null
        micBtn = null
    }

    private fun toggleVoice() {
        val v = voice ?: return
        if (v.isRecording) { v.stop(); return }
        micBtn?.text = "🔴"
        v.start(
            onFinal = { text ->
                micBtn?.text = "🎤"
                input?.setText(text)
                submit(text)
            },
            onFail = { msg ->
                micBtn?.text = "🎤"
                AgentRuntime.log("⚠️ $msg (إذا ما اشتغل الميكروفون، أعطِ إذنه من التطبيق: زر 🎤 مرة وحدة)")
            }
        )
    }

    // ---------- التنفيذ ----------
    private fun submit(goalRaw: String) {
        val goal = goalRaw.trim()
        if (goal.isEmpty()) return
        val problems = AgentLoop.prerequisites(this, pairing)
        if (problems.isNotEmpty()) {
            problems.forEach { AgentRuntime.log("⚠️ $it") }
            return
        }
        hidePanel()
        AgentRuntime.taskRunning = true
        thread {
            try {
                val api = ApiClient(pairing.getServerUrl()) { pairing.getDeviceToken() }
                val t = api.createTask(goal)
                AgentRuntime.log("🚀 بدأت المهمة: $goal")
                AgentLoop.run(applicationContext, pairing, t.getString("task_id"))
            } catch (e: Exception) {
                AgentRuntime.log("⛔ خطأ: ${e.message}")
                AgentRuntime.taskRunning = false
            }
        }
    }

    override fun onDestroy() {
        active = false
        ticker?.let { main.removeCallbacks(it) }
        hidePanel()
        try { bubble?.let { wm.removeView(it) } } catch (_: Exception) {}
        bubble = null
        voice?.release()
        voice = null
        super.onDestroy()
    }
}
