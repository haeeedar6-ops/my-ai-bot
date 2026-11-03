package com.ryn.agent

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent

/**
 * AgentAccessibilityService: ينفذ فقط التفاعلات المعتمدة من المستخدم بعد اجتياز فحص SafetyController.
 */
class AgentAccessibilityService : AccessibilityService() {
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}
}
