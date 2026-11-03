package com.ryn.agent

import android.app.Service
import android.content.Intent
import android.os.IBinder

/**
 * FloatingControlsService: خدمة زر الإيقاف العائم (Floating Stop Control) لإيقاف أي مهمة جارية فوراً.
 */
class FloatingControlsService : Service() {
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
