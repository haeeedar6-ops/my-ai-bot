package com.ryn.agent

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

class ApiException(message: String, val httpCode: Int = 0) : Exception(message)

/**
 * ApiClient: يتصل بمسارات /api/v1/agent/* عبر HTTPS مع ترويسة Authorization: Bearer <device_token>،
 * مهلة زمنية، وإعادة محاولة بتراجع أسي على أخطاء الشبكة وأخطاء 5xx (مثل استيقاظ Render).
 */
class ApiClient(baseUrl: String, private val tokenProvider: () -> String?) {
    private val base = baseUrl.trim().trimEnd('/')
    private val jsonType = "application/json; charset=utf-8".toMediaType()
    private val http = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    fun endpoint(path: String): String = base + path

    fun request(method: String, path: String, payload: JSONObject? = null, retries: Int = 3): JSONObject {
        var last: Exception? = null
        for (attempt in 0 until retries) {
            try {
                val builder = Request.Builder().url(endpoint(path))
                tokenProvider()?.takeIf { it.isNotBlank() }?.let { builder.header("Authorization", "Bearer $it") }
                if (method.equals("GET", true)) builder.get()
                else builder.method(method.uppercase(), (payload ?: JSONObject()).toString().toRequestBody(jsonType))

                http.newCall(builder.build()).execute().use { resp ->
                    val text = resp.body?.string().orEmpty()
                    val json = try { JSONObject(text) } catch (e: Exception) { JSONObject() }
                    if (resp.code >= 500) throw IOException("HTTP ${resp.code}")
                    if (resp.code >= 400) {
                        throw ApiException(json.optString("error").ifEmpty { "HTTP ${resp.code}" }, resp.code)
                    }
                    return json
                }
            } catch (e: ApiException) {
                throw e
            } catch (e: IOException) {
                last = e
                if (attempt < retries - 1) {
                    try { Thread.sleep(800L * (1L shl attempt)) } catch (_: InterruptedException) { break }
                }
            }
        }
        throw ApiException("انقطع الاتصال بالسيرفر (Offline): ${last?.message}")
    }

    fun completePairing(code: String, deviceName: String): JSONObject =
        request(
            "POST", "/api/v1/agent/pair/complete",
            JSONObject().put("code", code).put("device_name", deviceName).put("platform", "android"),
            retries = 2
        )

    fun createTask(goal: String): JSONObject =
        request("POST", "/api/v1/agent/tasks", JSONObject().put("goal", goal), retries = 2)

    fun requestNextStep(taskId: String, sequence: Int, shot: Shot, observation: String): JSONObject =
        request(
            "POST", "/api/v1/agent/step",
            JSONObject()
                .put("task_id", taskId)
                .put("sequence", sequence)
                .put("screenshot_b64", shot.b64)
                // نرسل أبعاد الصورة التي يراها النموذج؛ والتطبيق يحوّل الإحداثيات للشاشة الحقيقية
                .put("screen_width", shot.imageW)
                .put("screen_height", shot.imageH)
                .put("observation", observation)
                // بدونها السيرفر يستعمل مخططاً بسيطاً ولا يستعمل نموذج الرؤية
                .put("use_vision_model", shot.b64.isNotEmpty()),
            retries = 3
        )

    fun reportResult(
        taskId: String, commandId: String, sequence: Int, status: String,
        note: String = "", error: String = "", shot: Shot? = null
    ): JSONObject =
        request(
            "POST", "/api/v1/agent/result",
            JSONObject()
                .put("task_id", taskId)
                .put("command_id", commandId)
                .put("sequence", sequence)
                .put("status", status)
                .put("verification_note", note)
                .put("error", error)
                .put("screenshot", shot?.b64 ?: ""),
            retries = 2
        )

    fun stopTask(taskId: String = ""): JSONObject =
        request("POST", "/api/v1/agent/stop", JSONObject().put("task_id", taskId), retries = 1)

    /** استعلام خفيف: هل في مهمة وصلت من الدردشة لهذا الجهاز؟ (يحدّث «آخر ظهور» ولا يستهلك الحصة) */
    fun pending(): JSONObject = request("GET", "/api/v1/agent/pending", null, retries = 1)

    fun sendVoice(transcript: String): JSONObject =
        request(
            "POST", "/api/v1/agent/voice",
            JSONObject().put("transcript", transcript).put("start_task", true),
            retries = 2
        )
}
