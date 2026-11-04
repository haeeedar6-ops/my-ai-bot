package com.ryn.agent

import android.content.Context
import android.content.SharedPreferences

/**
 * PairingManager: يحفظ توكن الجهاز ورابط السيرفر بأمان عبر SharedPreferences الخاصة بالتطبيق
 * ولا يخزن أي مفتاح نموذج ذكاء اصطناعي داخل تطبيق الهاتف.
 */
class PairingManager(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("ryn_agent_secure_prefs", Context.MODE_PRIVATE)

    fun saveServerUrl(url: String) {
        prefs.edit().putString("server_url", url.trim().trimEnd('/')).apply()
    }

    fun getServerUrl(): String {
        return prefs.getString("server_url", "https://ryn-ai-1.onrender.com") ?: "https://ryn-ai-1.onrender.com"
    }

    fun saveDeviceCredential(deviceId: String, token: String) {
        prefs.edit()
            .putString("device_id", deviceId.trim())
            .putString("device_token", token.trim())
            .apply()
    }

    fun getDeviceId(): String? = prefs.getString("device_id", null)

    fun getDeviceToken(): String? = prefs.getString("device_token", null)

    fun isPaired(): Boolean = !getDeviceToken().isNullOrBlank()

    fun clearCredential() {
        prefs.edit().remove("device_id").remove("device_token").apply()
    }
}
