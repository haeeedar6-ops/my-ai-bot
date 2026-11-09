package com.ryn.agent

import kotlin.math.max
import kotlin.math.sqrt

/**
 * كشف مقاطعة المستخدم أثناء كلام رين (نفس منطق الويب والويندوز). كلاس نقي بدون أي اعتماد على Android.
 * ملاحظة: هذا الكلاس لسا مو موصول بـ VoiceController (ما انعمل ربط مع AudioRecord/AEC لأنو ما في بيئة بناء
 * أندرويد هون). الربط المقترح: AudioRecord بمصدر VOICE_COMMUNICATION + AcousticEchoCanceler، وتغذية step() بكل إطار 30ms.
 */
class DuplexVad(profile: Profile = Profile.NORMAL) {

    enum class Profile(val minRms: Double, val ratio: Double, val minSpeechMs: Double) {
        NORMAL(0.05, 4.0, 240.0),
        HEADPHONES(0.025, 3.0, 240.0),
        SPEAKER(0.08, 5.0, 320.0)
    }

    private val minRms = profile.minRms
    private val ratio = profile.ratio
    private val minSpeechMs = profile.minSpeechMs
    private val warmupMs = 700.0
    private val absorbMs = 150.0
    private val floorAttack = 0.02
    private val floorRelease = 0.05

    private var t = 0.0
    private var floor = 0.01
    private var run = 0.0
    private var fired = false

    /** يرجّع true مرة وحدة بس عند تأكد كلام مستمر. */
    fun step(rms: Double, dtMs: Double = 30.0): Boolean {
        t += dtMs
        if (fired) return false
        val thr = max(minRms, floor * ratio)
        val hot = rms > thr
        run = if (hot) run + dtMs else 0.0
        if (run <= absorbMs || !hot) {
            val a = if (rms > floor) floorAttack else floorRelease
            floor += (rms - floor) * a
        }
        if (t < warmupMs) return false
        if (run >= minSpeechMs) {
            fired = true
            return true
        }
        return false
    }

    companion object {
        fun rms(samples: ShortArray, length: Int = samples.size): Double {
            if (length <= 0) return 0.0
            var sum = 0.0
            for (i in 0 until length) {
                val v = samples[i] / 32768.0
                sum += v * v
            }
            return sqrt(sum / length)
        }
    }
}
