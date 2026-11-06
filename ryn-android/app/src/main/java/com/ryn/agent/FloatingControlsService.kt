package com.ryn.agent

import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.IBinder
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button

/**
 * FloatingControlsService: يعرض زر إيقاف عائم واضح (Stop Control) فوق كل التطبيقات لإيقاف أي مهمة فوراً.
 * يحتاج إذن "العرض فوق التطبيقات الأخرى".
 */
class FloatingControlsService : Service() {
    companion object {
        fun start(context: Context) {
            context.startService(Intent(context, FloatingControlsService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, FloatingControlsService::class.java))
        }
    }

    private var view: View? = null
    private var wm: WindowManager? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (view == null) show()
        return START_NOT_STICKY
    }

    private fun show() {
        if (!Settings.canDrawOverlays(this)) { stopSelf(); return }
        val manager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val btn = Button(this).apply {
            text = "⏹ إيقاف"
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.parseColor("#FF5D6C"))
            setOnClickListener {
                AgentRuntime.safety.emergencyStop()
                AgentRuntime.log("🛑 تم ضغط زر الإيقاف العائم")
            }
        }
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.END
            y = 200
        }
        manager.addView(btn, lp)
        wm = manager
        view = btn
    }

    override fun onDestroy() {
        try { view?.let { wm?.removeView(it) } } catch (_: Exception) {}
        view = null
        super.onDestroy()
    }
}
