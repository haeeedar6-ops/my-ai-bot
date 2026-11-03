package com.ryn.agent

/**
 * VoiceController: يسجل الصوت بنمط Push-to-Talk فقط أثناء ضغط الزر ويرسله إلى /api/v1/agent/voice.
 */
class VoiceController {
    var isRecording: Boolean = false
    var isMuted: Boolean = false
}
