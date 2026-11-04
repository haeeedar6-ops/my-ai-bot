package com.ryn.agent

import android.content.Context
import android.speech.tts.TextToSpeech
import java.util.Locale

/**
 * VoiceController: يدعم الأوامر الصوتية (Push-to-Talk) والرد الصوتي المسموع (TextToSpeech بالعربية والإنجليزية).
 */
class VoiceController(context: Context) : TextToSpeech.OnInitListener {
    var isRecording: Boolean = false
    var isMuted: Boolean = false
    private var tts: TextToSpeech? = null
    private var ttsReady: Boolean = false

    init {
        try {
            tts = TextToSpeech(context.applicationContext, this)
        } catch (_: Exception) {}
    }

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts?.language = Locale("ar")
            ttsReady = true
        }
    }

    fun speak(text: String) {
        if (isMuted || !ttsReady || text.isBlank()) return
        try {
            val hasArabic = Regex("[\\u0600-\\u06FF]").containsMatchIn(text)
            tts?.language = if (hasArabic) Locale("ar") else Locale.US
            tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "ryn_tts_utt")
        } catch (_: Exception) {}
    }

    fun shutdown() {
        try {
            tts?.stop()
            tts?.shutdown()
        } catch (_: Exception) {}
    }
}
