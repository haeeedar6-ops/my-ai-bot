package com.ryn.agent

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer

/**
 * VoiceController: تعرّف صوتي بنمط Push-to-Talk فقط (يسجّل أثناء ضغط الزر)،
 * ثم يرسل النص إلى /api/v1/agent/voice. يجب استدعاؤه من الخيط الرئيسي.
 */
class VoiceController(private val context: Context) {
    var isRecording: Boolean = false
        private set
    var isMuted: Boolean = false

    private var recognizer: SpeechRecognizer? = null

    fun isAvailable(): Boolean = SpeechRecognizer.isRecognitionAvailable(context)

    fun start(onFinal: (String) -> Unit, onFail: (String) -> Unit) {
        if (isMuted) { onFail("الميكروفون مكتوم حالياً."); return }
        if (!isAvailable()) { onFail("خدمة التعرف على الصوت غير متوفرة على هذا الجهاز."); return }
        release()
        val r = SpeechRecognizer.createSpeechRecognizer(context)
        r.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() { isRecording = false }
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
            override fun onError(error: Int) {
                isRecording = false
                onFail(
                    when (error) {
                        SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "ما فهمت الصوت، جرّب مرة ثانية"
                        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "ما في إذن الميكروفون"
                        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "مشكلة شبكة بالتعرف على الصوت"
                        else -> "خطأ بالتعرف على الصوت ($error)"
                    }
                )
            }
            override fun onResults(results: Bundle?) {
                isRecording = false
                val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim().orEmpty()
                if (text.isEmpty()) onFail("ما فهمت الصوت، جرّب مرة ثانية") else onFinal(text)
            }
        })
        recognizer = r
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ar")
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
        isRecording = true
        r.startListening(intent)
    }

    fun stop() {
        recognizer?.stopListening()
    }

    fun release() {
        isRecording = false
        try { recognizer?.destroy() } catch (_: Exception) {}
        recognizer = null
    }
}
