package com.ryn.agent

/**
 * ApiClient: يتصل بمسارات /api/v1/agent/* عبر HTTPS مع ترويسة Authorization: Bearer <device_token>.
 */
class ApiClient(private val baseUrl: String, private val tokenProvider: () -> String?) {
    fun endpoint(path: String): String = baseUrl.trimEnd('/') + path
}
