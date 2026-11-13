package com.ryn.agent

import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Base64
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.Locale
import java.util.UUID
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

/**
 * VoiceController: طبقة الصوت الموحدة لتطبيق أندرويد (تعرّف متعدد اللغات + تشغيل أصوات RYN العصبية + مقاطعة Barge-in).
 *
 * الإصلاحات المطبقة:
 * 1. دعم التعرّف التلقائي متعدد اللغات (asrLanguage = "auto" افتراضياً) مع إمكانية تحديد لغة BCP-47 صريحة
 *    بدلاً من إجبار RecognizerIntent.EXTRA_LANGUAGE على "ar"، مع حفظ اللغة المكتشفة والبيانات الوصفية لكل دور.
 * 2. إضافة طبقة تشغيل صوتي كاملة تتصل بـ `/api/v2/voice/speak` لتشغيل الصوت العصبي من السيرفر (MP3/WAV)
 *    كخيار أساسي، واستخدام Android TextToSpeech كمسار طوارئ مُعلَم صراحةً فقط (Explicitly Labeled Fallback).
 * 3. أزرار ودوال تحكم كاملة: play / speak, pause, resume, stopPlayback, replay, interrupt (Barge-in)،
 *    وتحرير كافة الموارد (SpeechRecognizer, MediaPlayer, TextToSpeech) في دورة الحياة release().
 */
class VoiceController(private val context: Context) {

    companion object {
        const val PREFS_NAME = "ryn_voice_prefs"
        const val KEY_ASR_LANGUAGE = "asr_language"
        const val KEY_VOICE_ID = "voice_id"
        const val KEY_VOICE_SPEED = "voice_speed"
        const val KEY_AUTO_READ = "auto_read"
        const val DEFAULT_VOICE_ID = "ar-SY-LaithNeural"
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val pairing = PairingManager(context)
    private val http = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    var isRecording: Boolean = false
        private set
    var isMuted: Boolean = false
    var isPlaying: Boolean = false
        private set
    var isPaused: Boolean = false
        private set
    var isFallbackTts: Boolean = false
        private set

    /** وضع الإدخال الصوتي: push_to_talk افتراضياً (أو live_stream عند تفعيل دفق الإطارات الصوتية). */
    var captureMode: String = "push_to_talk"
        private set

    var activeSessionId: String = ""
    var lastDetectedLanguage: String = "auto"
        private set
    var lastTranscriptMetadata: JSONObject = JSONObject()
        private set
    var lastProviderUsed: String = ""
        private set
    var lastSpokenText: String = ""
        private set

    private var recognizer: SpeechRecognizer? = null
    private var mediaPlayer: MediaPlayer? = null
    private var androidTts: TextToSpeech? = null
    private var androidTtsReady: Boolean = false
    private val playbackToken = AtomicInteger(0)
    private val stopRequested = AtomicBoolean(false)
    private val tempAudioFiles = mutableListOf<File>()

    // ---------------- إعدادات اللغة والصوت لكل مستخدم ----------------
    var asrLanguage: String
        get() = prefs.getString(KEY_ASR_LANGUAGE, "auto") ?: "auto"
        set(value) {
            val norm = value.trim().ifEmpty { "auto" }
            prefs.edit().putString(KEY_ASR_LANGUAGE, norm).apply()
        }

    var preferredVoiceId: String
        get() = prefs.getString(KEY_VOICE_ID, DEFAULT_VOICE_ID) ?: DEFAULT_VOICE_ID
        set(value) {
            prefs.edit().putString(KEY_VOICE_ID, value.trim().ifEmpty { DEFAULT_VOICE_ID }).apply()
        }

    var preferredSpeed: Float
        get() = prefs.getFloat(KEY_VOICE_SPEED, 1.0f).coerceIn(0.5f, 2.0f)
        set(value) {
            prefs.edit().putFloat(KEY_VOICE_SPEED, value.coerceIn(0.5f, 2.0f)).apply()
        }

    var autoReadReplies: Boolean
        get() = prefs.getBoolean(KEY_AUTO_READ, false)
        set(value) {
            prefs.edit().putBoolean(KEY_AUTO_READ, value).apply()
        }

    fun isAvailable(): Boolean = SpeechRecognizer.isRecognitionAvailable(context)

    /** يكشف لغة النص (BCP-47) لدعم العربية (الشامية/المصرية/الفصحى) واللغات الأوروبية والتركية. */
    fun detectLanguageFromText(text: String, hint: String = "auto"): String {
        val raw = text.trim()
        if (Regex("[\\u0600-\\u06FF]").containsMatchIn(raw)) {
            if (Regex("\\b(هلق|هلأ|شلونك|بدي|هيك|منيح|كرمال|مشان|عنجد|كتير)\\b").containsMatchIn(raw)) return "ar-SY"
            if (Regex("\\b(ازيك|إزيك|عايز|عاوز|كده|دلوقتي)\\b").containsMatchIn(raw)) return "ar-EG"
            return if (hint.startsWith("ar-")) hint else "ar-SY"
        }
        val low = " ${raw.lowercase(Locale.ROOT)} "
        if (Regex("[ğşıİ]").containsMatchIn(raw) || Regex("\\b(merhaba|nasılsın|teşekkür|için|değil)\\b").containsMatchIn(low)) return "tr-TR"
        if (Regex("[äöüß]").containsMatchIn(low) || Regex("\\b(hallo|danke|bitte|nicht|ich|für)\\b").containsMatchIn(low)) return "de-DE"
        if (Regex("[œç]").containsMatchIn(low) || Regex("\\b(bonjour|merci|comment|avec|pour|très)\\b").containsMatchIn(low)) return "fr-FR"
        if (Regex("[ñ¿¡]").containsMatchIn(low) || Regex("\\b(hola|gracias|cómo|está|para|pero)\\b").containsMatchIn(low)) return "es-ES"
        if (Regex("[ãõ]").containsMatchIn(low) || Regex("\\b(olá|obrigado|você|não|também)\\b").containsMatchIn(low)) return "pt-BR"
        if (Regex("\\b(ciao|grazie|perché|questo|buongiorno)\\b").containsMatchIn(low)) return "it-IT"
        return if (hint != "auto" && hint.isNotBlank()) hint else "en-US"
    }

    /**
     * يبدأ الاستماع الصوتي (Push-to-Talk افتراضياً) مع دعم التعرّف التلقائي متعدد اللغات أو لغة BCP-47 مخصصة،
     * ومقاطعة أي رد صوتي جارٍ فور بدء المستخدم بالكلام (Barge-in).
     */
    fun start(
        onFinal: (String) -> Unit,
        onFail: (String) -> Unit,
        languageOverride: String? = null,
        onPartial: ((String) -> Unit)? = null
    ) {
        if (isMuted) {
            onFail("الميكروفون مكتوم حالياً.")
            return
        }
        if (!isAvailable()) {
            onFail("خدمة التعرف على الصوت غير متوفرة على هذا الجهاز.")
            return
        }
        // إذا كان RYN يتحدث حالياً، نقاطع الصوت فوراً (Barge-in)
        if (isPlaying || isPaused) {
            interrupt()
        }
        stopRecognizerOnly()

        val selectedLang = (languageOverride ?: asrLanguage).trim().ifEmpty { "auto" }
        val r = SpeechRecognizer.createSpeechRecognizer(context)
        r.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {
                if (isPlaying || isPaused) {
                    interrupt()
                }
            }
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {
                if (buffer != null && buffer.isNotEmpty() && activeSessionId.isNotEmpty() && captureMode == "live_stream") {
                    streamAudioFrame(buffer, "audio/pcm")
                }
            }
            override fun onEndOfSpeech() {
                isRecording = false
            }
            override fun onPartialResults(partialResults: Bundle?) {
                val part = partialResults
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                    ?.trim()
                    .orEmpty()
                if (part.isNotEmpty()) {
                    if (isPlaying || isPaused) interrupt()
                    onPartial?.invoke(part)
                }
            }
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
                val text = results
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                    ?.trim()
                    .orEmpty()
                if (text.isEmpty()) {
                    onFail("ما فهمت الصوت، جرّب مرة ثانية")
                } else {
                    val detLang = detectLanguageFromText(text, selectedLang)
                    lastDetectedLanguage = detLang
                    lastTranscriptMetadata = JSONObject()
                        .put("requested_language", selectedLang)
                        .put("detected_language", detLang)
                        .put("provider", "android-speech-recognizer")
                        .put("capture_mode", captureMode)
                        .put("timestamp_ms", System.currentTimeMillis())
                    onFinal(text)
                }
            }
        })
        recognizer = r

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            // عدم إجبار العربية عند اختيار الوضع التلقائي (auto) للسماح بالتعرف متعدد اللغات
            if (!selectedLang.equals("auto", ignoreCase = true)) {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, selectedLang)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, selectedLang)
            }
        }
        isRecording = true
        r.startListening(intent)
    }

    /**
     * يرفع ملف/بايتات صوت مسجلة إلى مسار السيرفر `/api/v2/voice/transcribe` (Whisper متعدد اللغات مع كشف اللغة).
     */
    fun transcribeUpload(
        audioBytes: ByteArray,
        mimeType: String = "audio/wav",
        language: String = asrLanguage,
        onResult: (transcript: String, detectedLanguage: String, metadata: JSONObject) -> Unit,
        onError: (String) -> Unit
    ) {
        if (audioBytes.isEmpty()) {
            onError("بيانات الصوت فارغة")
            return
        }
        thread {
            var served = false
            try {
                val baseUrl = pairing.getServerUrl().trim().trimEnd('/')
                val tok = pairing.getDeviceToken()
                val ext = when {
                    mimeType.contains("wav") -> "wav"
                    mimeType.contains("mp4") || mimeType.contains("m4a") -> "m4a"
                    mimeType.contains("ogg") -> "ogg"
                    else -> "webm"
                }
                val bodyBuilder = MultipartBody.Builder().setType(MultipartBody.FORM)
                    .addFormDataPart("audio", "voice.$ext", audioBytes.toRequestBody(mimeType.toMediaType()))
                    .addFormDataPart("language", language.ifBlank { "auto" })
                if (activeSessionId.isNotBlank()) {
                    bodyBuilder.addFormDataPart("session_id", activeSessionId)
                }
                val reqBuilder = Request.Builder()
                    .url("$baseUrl/api/v2/voice/transcribe")
                    .header("X-Requested-With", "astra")
                    .post(bodyBuilder.build())
                if (!tok.isNullOrBlank()) {
                    reqBuilder.header("Authorization", "Bearer $tok")
                }
                http.newCall(reqBuilder.build()).execute().use { resp ->
                    val raw = resp.body?.string().orEmpty()
                    val json = try { JSONObject(raw) } catch (_: Exception) { JSONObject() }
                    if (!resp.isSuccessful || !json.optBoolean("ok", false)) {
                        val err = json.optString("error").ifEmpty { "HTTP ${resp.code}" }
                        mainHandler.post { onError(err) }
                        return@use
                    }
                    val txt = json.optString("transcript").ifEmpty { json.optString("text") }
                    val det = json.optString("detected_language").ifEmpty { detectLanguageFromText(txt, language) }
                    val meta = json.optJSONObject("transcript_metadata") ?: JSONObject()
                        .put("detected_language", det)
                        .put("requested_language", language)
                    lastDetectedLanguage = det
                    lastTranscriptMetadata = meta
                    mainHandler.post { onResult(txt, det, meta) }
                }
            } catch (e: Exception) {
                mainHandler.post { onError(e.message ?: "فشل تفريغ الصوت على السيرفر") }
            }
        }
    }

    /** يرسل إطار صوتي حقيقي إلى `/api/v2/voice/stream` في وضع البث الحي. */
    fun streamAudioFrame(frameBytes: ByteArray, mimeType: String = "audio/pcm") {
        if (activeSessionId.isBlank() || frameBytes.isEmpty()) return
        thread {
            try {
                val baseUrl = pairing.getServerUrl().trim().trimEnd('/')
                val tok = pairing.getDeviceToken()
                val payload = JSONObject()
                    .put("session_id", activeSessionId)
                    .put("event_type", "audio_frame")
                    .put("audio_b64", Base64.encodeToString(frameBytes, Base64.NO_WRAP))
                    .put("mime_type", mimeType)
                    .put("language", asrLanguage)
                val reqBuilder = Request.Builder()
                    .url("$baseUrl/api/v2/voice/stream")
                    .header("X-Requested-With", "astra")
                    .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
                if (!tok.isNullOrBlank()) {
                    reqBuilder.header("Authorization", "Bearer $tok")
                }
                http.newCall(reqBuilder.build()).execute().close()
            } catch (_: Exception) {}
        }
    }

    // ---------------- طبقة التشغيل الصوتي الموحدة (Server Neural TTS + Explicit Android TTS Fallback) ----------------
    private fun splitIntoSentenceChunks(text: String, maxChars: Int = 500): List<String> {
        val clean = text
            .replace(Regex("```[\\s\\S]*?```"), " كود برمجي جاهز بالشات. ")
            .replace(Regex("https?://\\S+"), " رابط ")
            .replace(Regex("[*#_~>|`]"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()
        if (clean.isEmpty()) return emptyList()
        val rawSents = Regex("[^.!?؟؛;\\n]+[.!?؟؛;\\n]*").findAll(clean).map { it.value.trim() }.filter { it.isNotEmpty() }.toList()
        val sents = if (rawSents.isEmpty()) listOf(clean) else rawSents
        val out = mutableListOf<String>()
        var cur = ""
        for (s in sents) {
            if (s.length > maxChars) {
                if (cur.isNotEmpty()) { out.add(cur); cur = "" }
                var idx = 0
                while (idx < s.length) {
                    val end = minOf(idx + maxChars, s.length)
                    out.add(s.substring(idx, end).trim())
                    idx = end
                }
            } else if (cur.isNotEmpty() && cur.length + 1 + s.length > maxChars) {
                out.add(cur)
                cur = s
            } else {
                cur = if (cur.isEmpty()) s else "$cur $s"
            }
        }
        if (cur.isNotEmpty()) out.add(cur)
        return out
    }

    private fun isValidAudioContainer(bytes: ByteArray): Boolean {
        if (bytes.size < 4) return false
        val isWav = bytes.size >= 12 &&
            bytes[0] == 'R'.code.toByte() && bytes[1] == 'I'.code.toByte() &&
            bytes[2] == 'F'.code.toByte() && bytes[3] == 'F'.code.toByte() &&
            bytes[8] == 'W'.code.toByte() && bytes[9] == 'A'.code.toByte() &&
            bytes[10] == 'V'.code.toByte() && bytes[11] == 'E'.code.toByte()
        val isId3 = bytes[0] == 'I'.code.toByte() && bytes[1] == 'D'.code.toByte() && bytes[2] == '3'.code.toByte()
        val isMpegSync = (bytes[0].toInt() and 0xFF) == 0xFF && (bytes[1].toInt() and 0xE0) == 0xE0
        val isOgg = bytes[0] == 'O'.code.toByte() && bytes[1] == 'g'.code.toByte() &&
            bytes[2] == 'g'.code.toByte() && bytes[3] == 'S'.code.toByte()
        return isWav || isId3 || isMpegSync || isOgg
    }

    private fun notifyServerPlaybackState(action: String, bargeIn: Boolean = false) {
        val sid = activeSessionId
        if (sid.isBlank()) return
        thread {
            try {
                val baseUrl = pairing.getServerUrl().trim().trimEnd('/')
                val tok = pairing.getDeviceToken()
                val payload = JSONObject()
                    .put("session_id", sid)
                    .put("action", action)
                    .put("barge_in", bargeIn)
                val reqBuilder = Request.Builder()
                    .url("$baseUrl/api/v2/voice/stop")
                    .header("X-Requested-With", "astra")
                    .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
                if (!tok.isNullOrBlank()) {
                    reqBuilder.header("Authorization", "Bearer $tok")
                }
                http.newCall(reqBuilder.build()).execute().close()
            } catch (_: Exception) {}
        }
    }

    /**
     * ينطق رد RYN عبر استدعاء `/api/v2/voice/speak` وتشغيل بايتات الصوت العصبي المولّد على السيرفر.
     * عند فشل الاتصال بالسيرفر، يستخدم محرك Android TextToSpeech كمسار طوارئ مُعلَم صراحةً.
     */
    fun speak(
        text: String,
        conversationId: String? = null,
        messageId: String? = null,
        voiceId: String? = null,
        speed: Float? = null,
        onComplete: (() -> Unit)? = null,
        onFallbackUsed: ((String) -> Unit)? = null
    ) {
        val cleanInput = text.trim()
        if (cleanInput.isEmpty() && messageId.isNullOrBlank()) {
            onComplete?.invoke()
            return
        }
        stopPlayback(notifyServer = false)
        stopRequested.set(false)
        val token = playbackToken.incrementAndGet()
        lastSpokenText = cleanInput
        val targetVoice = (voiceId ?: preferredVoiceId).ifBlank { DEFAULT_VOICE_ID }
        val targetSpeed = (speed ?: preferredSpeed).coerceIn(0.5f, 2.0f)
        val detectedLang = detectLanguageFromText(cleanInput, asrLanguage)

        thread {
            try {
                val baseUrl = pairing.getServerUrl().trim().trimEnd('/')
                val tok = pairing.getDeviceToken()
                val bodyJson = JSONObject()
                    .put("text", cleanInput)
                    .put("voice_id", targetVoice)
                    .put("speed", targetSpeed.toDouble())
                    .put("language", detectedLang)
                if (!conversationId.isNullOrBlank()) bodyJson.put("conversation_id", conversationId)
                if (!messageId.isNullOrBlank()) bodyJson.put("message_id", messageId)
                if (activeSessionId.isNotBlank()) bodyJson.put("session_id", activeSessionId)

                val reqBuilder = Request.Builder()
                    .url("$baseUrl/api/v2/voice/speak")
                    .header("X-Requested-With", "astra")
                    .post(bodyJson.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
                if (!tok.isNullOrBlank()) {
                    reqBuilder.header("Authorization", "Bearer $tok")
                }

                http.newCall(reqBuilder.build()).execute().use { resp ->
                    val raw = resp.body?.string().orEmpty()
                    val json = try { JSONObject(raw) } catch (_: Exception) { JSONObject() }
                    val warn = json.optString("warning")
                    if (warn.isNotEmpty()) {
                        AgentRuntime.log("⚠️ $warn")
                    }

                    val fallbackFlag = json.optBoolean("fallback_browser_tts", false)
                    val chunksArr = json.optJSONArray("chunks")
                    val audioB64 = json.optString("audio_b64")
                    val mimeType = json.optString("mime_type", "audio/mpeg")
                    val replyLang = json.optString("detected_language").ifEmpty { detectedLang }

                    val chunkPayloads = mutableListOf<Pair<ByteArray, String>>()
                    if (!fallbackFlag && chunksArr != null && chunksArr.length() > 0) {
                        for (i in 0 until chunksArr.length()) {
                            val item = chunksArr.getJSONObject(i)
                            val b64 = item.optString("audio_b64")
                            val m = item.optString("mime_type", mimeType)
                            if (b64.isNotEmpty()) {
                                val decoded = Base64.decode(b64, Base64.DEFAULT)
                                if (isValidAudioContainer(decoded)) {
                                    chunkPayloads.add(decoded to m)
                                }
                            }
                        }
                    } else if (!fallbackFlag && audioB64.isNotEmpty()) {
                        val decoded = Base64.decode(audioB64, Base64.DEFAULT)
                        if (isValidAudioContainer(decoded)) {
                            chunkPayloads.add(decoded to mimeType)
                        }
                    }

                    if (chunkPayloads.isNotEmpty()) {
                        served = true
                        isFallbackTts = json.optString("fallback_provider") == "gtts"
                        lastProviderUsed = json.optString("tts_provider_used").ifEmpty { "server-neural" }
                        isPlaying = true
                        isPaused = false
                        notifyServerPlaybackState("playback_started")
                        playAudioChunksSequentially(chunkPayloads, 0, token, replyLang, cleanInput, onComplete, onFallbackUsed)
                        return@use
                    }
                }
            } catch (_: Exception) {
                // ننتقل لمسار الطوارئ المحلي أدناه
            }

            if (!served && token == playbackToken.get() && !stopRequested.get()) {
                speakWithAndroidTtsFallback(cleanInput, detectedLang, targetSpeed, token, onComplete, onFallbackUsed)
            }
        }
    }

    private fun playAudioChunksSequentially(
        chunks: List<Pair<ByteArray, String>>,
        index: Int,
        token: Int,
        lang: String,
        fullText: String,
        onComplete: (() -> Unit)?,
        onFallbackUsed: ((String) -> Unit)?
    ) {
        if (token != playbackToken.get() || stopRequested.get() || index >= chunks.size) {
            isPlaying = false
            isPaused = false
            notifyServerPlaybackState(if (stopRequested.get()) "playback_stopped" else "playback_ended")
            if (token == playbackToken.get()) mainHandler.post { onComplete?.invoke() }
            return
        }

        val (bytes, mime) = chunks[index]
        val suffix = when {
            mime.contains("wav") -> ".wav"
            mime.contains("ogg") -> ".ogg"
            else -> ".mp3"
        }

        try {
            val tempFile = File.createTempFile("ryn_tts_${token}_$index", suffix, context.cacheDir)
            synchronized(tempAudioFiles) { tempAudioFiles.add(tempFile) }
            FileOutputStream(tempFile).use { it.write(bytes) }

            mainHandler.post {
                if (token != playbackToken.get() || stopRequested.get()) return@post
                try {
                    releaseMediaPlayerOnly()
                    val mp = MediaPlayer()
                    mp.setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build()
                    )
                    mp.setDataSource(tempFile.absolutePath)
                    mp.setOnCompletionListener {
                        try { tempFile.delete() } catch (_: Exception) {}
                        playAudioChunksSequentially(chunks, index + 1, token, lang, fullText, onComplete, onFallbackUsed)
                    }
                    mp.setOnErrorListener { _, _, _ ->
                        try { tempFile.delete() } catch (_: Exception) {}
                        speakWithAndroidTtsFallback(fullText, lang, preferredSpeed, token, onComplete, onFallbackUsed)
                        true
                    }
                    mediaPlayer = mp
                    mp.prepare()
                    mp.start()
                    isPlaying = true
                    isPaused = false
                } catch (_: Exception) {
                    speakWithAndroidTtsFallback(fullText, lang, preferredSpeed, token, onComplete, onFallbackUsed)
                }
            }
        } catch (_: Exception) {
            speakWithAndroidTtsFallback(fullText, lang, preferredSpeed, token, onComplete, onFallbackUsed)
        }
    }

    private fun speakWithAndroidTtsFallback(
        text: String,
        langBcp47: String,
        speed: Float,
        token: Int,
        onComplete: (() -> Unit)?,
        onFallbackUsed: ((String) -> Unit)?
    ) {
        if (token != playbackToken.get() || stopRequested.get()) return
        isFallbackTts = true
        lastProviderUsed = "android-tts-fallback"
        val notice = "⚠️ تعذر تشغيل صوت السيرفر العصبي — يتم استخدام صوت الطوارئ المحلي للجهاز (Android TTS Fallback · $langBcp47)"
        AgentRuntime.log(notice)
        mainHandler.post { onFallbackUsed?.invoke(notice) }

        val chunks = splitIntoSentenceChunks(text, maxChars = 450)
        if (chunks.isEmpty()) {
            isPlaying = false
            notifyServerPlaybackState("playback_ended")
            mainHandler.post { onComplete?.invoke() }
            return
        }

        mainHandler.post {
            val runTts = { tts: TextToSpeech ->
                if (token == playbackToken.get() && !stopRequested.get()) {
                    val locale = Locale.forLanguageTag(langBcp47)
                    val avail = tts.isLanguageAvailable(locale)
                    if (avail >= TextToSpeech.LANG_AVAILABLE) {
                        tts.language = locale
                    } else {
                        val baseLoc = Locale(locale.language)
                        if (tts.isLanguageAvailable(baseLoc) >= TextToSpeech.LANG_AVAILABLE) {
                            tts.language = baseLoc
                        }
                    }
                    tts.setSpeechRate(speed.coerceIn(0.6f, 1.8f))
                    isPlaying = true
                    isPaused = false
                    tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                        override fun onStart(utteranceId: String?) {}
                        override fun onDone(utteranceId: String?) {
                            if (utteranceId?.endsWith("_last") == true) {
                                isPlaying = false
                                isPaused = false
                                notifyServerPlaybackState("playback_ended")
                                mainHandler.post { onComplete?.invoke() }
                            }
                        }
                        @Deprecated("Deprecated in Java")
                        override fun onError(utteranceId: String?) {
                            isPlaying = false
                            isPaused = false
                            notifyServerPlaybackState("playback_stopped")
                            mainHandler.post { onComplete?.invoke() }
                        }
                    })
                    chunks.forEachIndexed { i, chunk ->
                        val mode = if (i == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD
                        val uid = if (i == chunks.lastIndex) "ryn_${token}_last" else "ryn_${token}_$i"
                        tts.speak(chunk, mode, null, uid)
                    }
                }
            }

            val existing = androidTts
            if (existing != null && androidTtsReady) {
                runTts(existing)
            } else {
                androidTts = TextToSpeech(context.applicationContext) { status ->
                    if (status == TextToSpeech.SUCCESS) {
                        androidTtsReady = true
                        androidTts?.let { runTts(it) }
                    } else {
                        isPlaying = false
                        notifyServerPlaybackState("playback_stopped")
                        onComplete?.invoke()
                    }
                }
            }
        }
    }

    // ---------------- أدوات التحكم بالتشغيل والمقاطعة ودورة الحياة ----------------
    fun pausePlayback() {
        mainHandler.post {
            try {
                val mp = mediaPlayer
                if (mp != null && mp.isPlaying) {
                    mp.pause()
                    isPaused = true
                    isPlaying = false
                    notifyServerPlaybackState("playback_paused")
                }
            } catch (_: Exception) {}
        }
    }

    fun resumePlayback() {
        mainHandler.post {
            try {
                val mp = mediaPlayer
                if (mp != null && isPaused) {
                    mp.start()
                    isPaused = false
                    isPlaying = true
                    notifyServerPlaybackState("playback_resumed")
                }
            } catch (_: Exception) {}
        }
    }

    fun stopPlayback(notifyServer: Boolean = true, bargeIn: Boolean = false) {
        stopRequested.set(true)
        playbackToken.incrementAndGet()
        isPlaying = false
        isPaused = false
        mainHandler.post {
            releaseMediaPlayerOnly()
            try { androidTts?.stop() } catch (_: Exception) {}
        }
        cleanupTempFiles()
        if (notifyServer) {
            notifyServerPlaybackState(if (bargeIn) "barge_in" else "playback_stopped", bargeIn = bargeIn)
        }
    }

    fun interrupt() {
        /** مقاطعة فورية (Barge-in) تلغي التشغيل الصوتي الحالي فور بدء كلام المستخدم. */
        stopPlayback(notifyServer = true, bargeIn = true)
    }

    fun replay(onComplete: (() -> Unit)? = null) {
        if (lastSpokenText.isNotBlank()) {
            speak(lastSpokenText, onComplete = onComplete)
        }
    }

    fun stop() {
        isRecording = false
        try { recognizer?.stopListening() } catch (_: Exception) {}
    }

    private fun stopRecognizerOnly() {
        isRecording = false
        try { recognizer?.destroy() } catch (_: Exception) {}
        recognizer = null
    }

    private fun releaseMediaPlayerOnly() {
        try {
            mediaPlayer?.let {
                if (it.isPlaying) it.stop()
                it.reset()
                it.release()
            }
        } catch (_: Exception) {}
        mediaPlayer = null
    }

    private fun cleanupTempFiles() {
        synchronized(tempAudioFiles) {
            val it = tempAudioFiles.iterator()
            while (it.hasNext()) {
                val f = it.next()
                try { if (f.exists()) f.delete() } catch (_: Exception) {}
                it.remove()
            }
        }
    }

    fun release() {
        stopPlayback(notifyServer = true)
        stopRecognizerOnly()
        mainHandler.post {
            try {
                androidTts?.stop()
                androidTts?.shutdown()
            } catch (_: Exception) {}
            androidTts = null
            androidTtsReady = false
        }
    }
}
