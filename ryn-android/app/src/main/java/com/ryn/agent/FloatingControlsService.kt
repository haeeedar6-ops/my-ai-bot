package com.ryn.agent

import android.app.Service
import android.content.Intent
import android.os.IBinder

/**
 * FloatingControlsService: يعرض زر إيقاف عائم واضح (Stop Control) لإيقاف أي مهمة فوراً.
 */
class FloatingControlsService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null
}
