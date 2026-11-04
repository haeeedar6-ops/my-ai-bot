package com.ryn.agent

import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

/**
 * ApiClient: يتصل بمسارات /api/v1/agent/* على Render مع ترويسة Authorization: Bearer <device_token>.
 */
class ApiClient(private val pairingManager: PairingManager) {

    fun endpoint(path: String): String = pairingManager.getServerUrl().trimEnd('/') + path

    fun postJson(path: String, body: JSONObject): JSONObject {
        val url = URL(endpoint(path))
        val conn = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 20000
            readTimeout = 25000
            doInput = true
            doOutput = true
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
            setRequestProperty("X-Requested-With", "astra")
            pairingManager.getDeviceToken()?.let { tok ->
                if (tok.isNotBlank()) {
                    setRequestProperty("Authorization", "Bearer $tok")
                }
            }
        }
        OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use { writer ->
            writer.write(body.toString())
            writer.flush()
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.let {
            BufferedReader(InputStreamReader(it, Charsets.UTF_8)).use { r -> r.readText() }
        } ?: "{}"
        return try {
            JSONObject(text).put("_http_code", code)
        } catch (_: Exception) {
            JSONObject().put("ok", false).put("error", "HTTP $code").put("_http_code", code)
        }
    }

    fun completePairing(code: String, deviceName: String): JSONObject {
        val payload = JSONObject()
            .put("code", code.trim())
            .put("device_name", deviceName)
            .put("platform", "android")
        return postJson("/api/v1/agent/pair/complete", payload)
    }

    fun createTask(goal: String): JSONObject {
        val payload = JSONObject().put("goal", goal.trim())
        return postJson("/api/v1/agent/tasks", payload)
    }

    fun sendVoiceGoal(transcript: String): JSONObject {
        val payload = JSONObject()
            .put("transcript", transcript.trim())
            .put("start_task", true)
        return postJson("/api/v1/agent/voice", payload)
    }

    fun requestNextStep(taskId: String, sequence: Int, screenshotB64: String, width: Int, height: Int, observation: String): JSONObject {
        val payload = JSONObject()
            .put("task_id", taskId)
            .put("sequence", sequence)
            .put("screenshot_b64", screenshotB64)
            .put("screen_width", width)
            .put("screen_height", height)
            .put("observation", observation)
        return postJson("/api/v1/agent/step", payload)
    }

    fun reportResult(taskId: String, commandId: String, sequence: Int, status: String, verificationNote: String): JSONObject {
        val payload = JSONObject()
            .put("task_id", taskId)
            .put("command_id", commandId)
            .put("sequence", sequence)
            .put("status", status)
            .put("verification_note", verificationNote)
        return postJson("/api/v1/agent/result", payload)
    }

    fun stopTask(taskId: String = ""): JSONObject {
        val payload = JSONObject().put("task_id", taskId)
        return postJson("/api/v1/agent/stop", payload)
    }
}
