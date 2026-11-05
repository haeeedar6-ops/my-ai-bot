package com.ryn.agent

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * PairingManager: يحفظ توكن الجهاز مشفّراً (Android Keystore + EncryptedSharedPreferences)
 * ولا يخزن أي مفتاح نموذج ذكاء اصطناعي داخل التطبيق.
 */
class PairingManager(context: Context) {
    companion object {
        const val DEFAULT_SERVER = "https://ryn-ai-1.onrender.com"
    }

    private val prefs: SharedPreferences = try {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(
            context,
            "ryn_agent_secure_prefs",
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    } catch (e: Exception) {
        // بعض الأجهزة يفشل عندها Keystore؛ نرجع لتخزين خاص بالتطبيق فقط (allowBackup=false)
        context.getSharedPreferences("ryn_agent_fallback_prefs", Context.MODE_PRIVATE)
    }

    fun saveDeviceCredential(deviceId: String, token: String) {
        prefs.edit().putString("device_id", deviceId).putString("device_token", token).apply()
    }

    fun getDeviceId(): String? = prefs.getString("device_id", null)
    fun getDeviceToken(): String? = prefs.getString("device_token", null)
    fun isPaired(): Boolean = !getDeviceToken().isNullOrBlank()

    fun getServerUrl(): String = prefs.getString("server_url", DEFAULT_SERVER) ?: DEFAULT_SERVER
    fun saveServerUrl(url: String) {
        prefs.edit().putString("server_url", url.trim().trimEnd('/')).apply()
    }

    /** استقبال الأوامر من الدردشة (مفعّل افتراضياً) */
    fun isListening(): Boolean = prefs.getBoolean("listen_chat", true)
    fun setListening(on: Boolean) { prefs.edit().putBoolean("listen_chat", on).apply() }

    fun clearCredential() {
        prefs.edit().remove("device_id").remove("device_token").apply()
    }
}
