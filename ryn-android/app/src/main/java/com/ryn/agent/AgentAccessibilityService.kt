package com.ryn.agent

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * AgentAccessibilityService: ينفذ فقط التفاعلات المعتمدة بعد اجتياز فحص SafetyController.
 * النقر والتمرير عبر dispatchGesture، والكتابة عبر ACTION_SET_TEXT (تدعم العربية).
 */
class AgentAccessibilityService : AccessibilityService() {
    companion object {
        @Volatile var instance: AgentAccessibilityService? = null
    }

    private val main = Handler(Looper.getMainLooper())

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        AgentRuntime.log("✅ خدمة إمكانية الوصول شغالة")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    // ---------- الإيماءات ----------
    private fun runGesture(g: GestureDescription, timeoutMs: Long): Boolean {
        val latch = CountDownLatch(1)
        val ok = AtomicBoolean(false)
        main.post {
            val dispatched = dispatchGesture(g, object : GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) {
                    ok.set(true); latch.countDown()
                }
                override fun onCancelled(gestureDescription: GestureDescription?) {
                    latch.countDown()
                }
            }, null)
            if (!dispatched) latch.countDown()
        }
        latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        return ok.get()
    }

    private fun tapStroke(x: Int, y: Int, start: Long): GestureDescription.StrokeDescription {
        val p = Path().apply { moveTo(x.toFloat(), y.toFloat()) }
        return GestureDescription.StrokeDescription(p, start, 60)
    }

    fun tap(x: Int, y: Int, double: Boolean = false): Boolean {
        val b = GestureDescription.Builder().addStroke(tapStroke(x, y, 0))
        if (double) b.addStroke(tapStroke(x, y, 180))
        return runGesture(b.build(), 2000)
    }

    fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Long = 350): Boolean {
        val p = Path().apply {
            moveTo(x1.toFloat(), y1.toFloat())
            lineTo(x2.toFloat(), y2.toFloat())
        }
        val g = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(p, 0, durationMs))
            .build()
        return runGesture(g, durationMs + 1500)
    }

    // ---------- الكتابة ----------
    private fun findEditable(n: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
        if (n == null) return null
        if (n.isEditable) return n
        for (i in 0 until n.childCount) {
            val r = findEditable(n.getChild(i))
            if (r != null) return r
        }
        return null
    }

    private fun focusedInput(): AccessibilityNodeInfo {
        val root = rootInActiveWindow ?: throw IllegalStateException("ما في نافذة نشطة")
        val node = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: findEditable(root)
            ?: throw IllegalStateException("ما لقيت حقل كتابة؛ اضغط على الحقل أولاً")
        if (node.isPassword) throw SecurityException("⛔ ممنوع الكتابة في حقول كلمات المرور")
        return node
    }

    private fun setText(node: AccessibilityNodeInfo, value: String): Boolean {
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
        }
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    private fun currentText(node: AccessibilityNodeInfo): String =
        if (node.isShowingHintText) "" else (node.text?.toString() ?: "")

    fun typeText(text: String): Boolean {
        val node = focusedInput()
        return setText(node, currentText(node) + text)
    }

    // ---------- المفاتيح ----------
    fun pressKey(key: String, screenW: Int, screenH: Int): Boolean {
        return when (key) {
            "back", "escape", "esc" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "home" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "enter", "return" -> {
                if (Build.VERSION.SDK_INT >= 30) {
                    focusedInput().performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
                } else {
                    throw UnsupportedOperationException("Enter يحتاج أندرويد 11+")
                }
            }
            "backspace", "delete" -> {
                val node = focusedInput()
                val t = currentText(node)
                if (t.isEmpty()) true else setText(node, t.dropLast(1))
            }
            "space" -> {
                val node = focusedInput()
                setText(node, currentText(node) + " ")
            }
            "down", "pagedown" -> swipe(screenW / 2, (screenH * 0.7).toInt(), screenW / 2, (screenH * 0.3).toInt())
            "up", "pageup" -> swipe(screenW / 2, (screenH * 0.3).toInt(), screenW / 2, (screenH * 0.7).toInt())
            else -> throw UnsupportedOperationException("المفتاح '$key' غير مدعوم على أندرويد")
        }
    }
}
