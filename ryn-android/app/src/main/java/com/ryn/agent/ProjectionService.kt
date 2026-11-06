package com.ryn.agent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat

/**
 * ProjectionService: يدير جلسة MediaProjection كخدمة أمامية (Foreground Service) مع إشعار واضح للمستخدم.
 * يجب استدعاء startForeground قبل getMediaProjection (شرط أندرويد 14).
 */
class ProjectionService : Service() {
    companion object {
        private const val CHANNEL_ID = "ryn_agent_projection"
        private const val NOTIF_ID = 4101
        private const val ACTION_START = "com.ryn.agent.START_PROJECTION"
        private const val ACTION_STOP = "com.ryn.agent.STOP_PROJECTION"
        private const val EXTRA_CODE = "code"
        private const val EXTRA_DATA = "data"

        @Volatile var capture: ScreenCapture? = null

        fun start(context: Context, resultCode: Int, data: Intent) {
            val i = Intent(context, ProjectionService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_CODE, resultCode)
                .putExtra(EXTRA_DATA, data)
            context.startForegroundService(i)
        }

        fun stop(context: Context) {
            context.startService(Intent(context, ProjectionService::class.java).setAction(ACTION_STOP))
        }
    }

    private var projection: MediaProjection? = null
    private var poller: PendingPoller? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent == null) return START_NOT_STICKY
        if (intent.action == ACTION_STOP) {
            cleanup()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        if (intent.action != ACTION_START) return START_NOT_STICKY

        startForegroundCompat()

        val code = intent.getIntExtra(EXTRA_CODE, 0)
        @Suppress("DEPRECATION")
        val data: Intent? = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(EXTRA_DATA, Intent::class.java)
        } else {
            intent.getParcelableExtra(EXTRA_DATA)
        }
        if (data == null) {
            AgentRuntime.log("⛔ ما وصلت بيانات إذن التقاط الشاشة")
            stopSelf()
            return START_NOT_STICKY
        }

        cleanup()
        val mpm = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val mp = mpm.getMediaProjection(code, data)
        // أندرويد 14 يشترط تسجيل callback قبل إنشاء VirtualDisplay
        mp.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                AgentRuntime.log("⏹ توقف التقاط الشاشة (أوقفه النظام أو المستخدم)")
                cleanup()
            }
        }, Handler(Looper.getMainLooper()))
        projection = mp
        capture = ScreenCapture(this, mp)
        poller = PendingPoller(this).also { it.start() }
        AgentRuntime.log("✅ التقاط الشاشة جاهز — الجهاز صار يستقبل أوامر الدردشة")
        return START_NOT_STICKY
    }

    private fun startForegroundCompat() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "RYN Agent", NotificationManager.IMPORTANCE_LOW)
        )
        val stopPi = PendingIntent.getService(
            this, 0,
            Intent(this, ProjectionService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val notif: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("RYN Agent يلتقط الشاشة")
            .setContentText("اضغط إيقاف لإنهاء التقاط الشاشة فوراً")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_media_pause, "إيقاف", stopPi)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    private fun cleanup() {
        try { poller?.stop() } catch (_: Exception) {}
        poller = null
        try { capture?.release() } catch (_: Exception) {}
        capture = null
        try { projection?.stop() } catch (_: Exception) {}
        projection = null
    }

    override fun onDestroy() {
        cleanup()
        super.onDestroy()
    }
}
