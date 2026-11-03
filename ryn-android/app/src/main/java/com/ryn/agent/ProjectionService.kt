package com.ryn.agent

import android.app.Service
import android.content.Intent
import android.os.IBinder

/**
 * ProjectionService: يدير جلسة MediaProjection كخدمة أمامية (Foreground Service) مع إشعار واضح للمستخدم.
 */
class ProjectionService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null
}
