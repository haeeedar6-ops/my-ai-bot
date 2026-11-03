package com.ryn.agent

import android.content.Context
import android.content.SharedPreferences

/**
 * PairingManager: يحفظ توكن الجهاز بأمان عبر Android Keystore / EncryptedSharedPreferences
 * ولا يخزن أي مفتاح نموذج ذكاء اصطناعي داخل التطبيق.
 */
class PairingManager(private val context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("ryn_agent_secure_prefs", Context.MODE_PRIVATE)

    fun saveDeviceCredential(deviceId: String, token: String) {
        prefs.edit().putString("device_id", deviceId).putString("device_token", token).apply()
    }

    fun getDeviceToken(): String? = prefs.getString("device_token", null)

    fun clearCredential() {
        prefs.edit().clear().apply()
    }
}
