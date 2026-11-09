package com.ryn.agent

import android.content.Context
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.util.Base64
import android.util.DisplayMetrics
import android.view.WindowManager
import java.io.ByteArrayOutputStream
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * ScreenCapture: يلتقط الشاشة بموافقة المستخدم (MediaProjection)، يصغّر العرض إلى maxWidth،
 * يضغط JPEG، ويدعم تدوير الشاشة. على أندرويد 14+ لا يجوز إنشاء أكثر من VirtualDisplay
 * لنفس MediaProjection، لذلك عند تغيّر الأبعاد نستعمل resize + تبديل الـ Surface.
 */
class ScreenCapture(
    private val context: Context,
    private val projection: MediaProjection,
    private val maxWidth: Int = 720,
    private val quality: Int = 70
) {
    private var reader: ImageReader? = null
    private var display: VirtualDisplay? = null
    private var curW = 0
    private var curH = 0
    private var cached: Shot? = null

    private val thread = HandlerThread("ryn-capture").also { it.start() }
    private val handler = Handler(thread.looper)

    var screenWidth: Int = 1080
        private set
    var screenHeight: Int = 2400
        private set

    private fun realSize(): Pair<Int, Int> {
        val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        return if (Build.VERSION.SDK_INT >= 30) {
            val b = wm.currentWindowMetrics.bounds
            b.width() to b.height()
        } else {
            val dm = DisplayMetrics()
            @Suppress("DEPRECATION")
            wm.defaultDisplay.getRealMetrics(dm)
            dm.widthPixels to dm.heightPixels
        }
    }

    private fun ensureDisplay(iw: Int, ih: Int): Boolean {
        if (display != null && iw == curW && ih == curH) return false
        val dpi = context.resources.displayMetrics.densityDpi
        val newReader = ImageReader.newInstance(iw, ih, PixelFormat.RGBA_8888, 2)
        val d = display
        if (d == null) {
            display = projection.createVirtualDisplay(
                "ryn-capture", iw, ih, dpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                newReader.surface, null, handler
            )
        } else {
            d.resize(iw, ih, dpi)
            d.surface = newReader.surface
        }
        reader?.close()
        reader = newReader
        curW = iw
        curH = ih
        cached = null
        return true
    }

    @Synchronized
    fun capture(): Shot {
        val (rw, rh) = realSize()
        screenWidth = rw
        screenHeight = rh
        val iw = min(maxWidth, rw)
        val ih = (rh.toDouble() * iw / rw).roundToInt().coerceAtLeast(1)
        val fresh = ensureDisplay(iw, ih)

        val rd = reader ?: throw IllegalStateException("ImageReader غير جاهز")
        // الشاشة الثابتة لا تنتج إطارات جديدة؛ لو ما في إطار نستعمل آخر لقطة ما لم يكن العرض جديداً
        val deadline = System.currentTimeMillis() + if (fresh || cached == null) 2500 else 150
        var img = rd.acquireLatestImage()
        while (img == null && System.currentTimeMillis() < deadline) {
            Thread.sleep(60)
            img = rd.acquireLatestImage()
        }
        if (img == null) {
            return cached ?: throw IllegalStateException("ما وصلت لقطة الشاشة (تأكد من إذن التقاط الشاشة)")
        }
        try {
            val plane = img.planes[0]
            val pixelStride = plane.pixelStride
            val rowPadding = plane.rowStride - pixelStride * iw
            val full = Bitmap.createBitmap(iw + rowPadding / pixelStride, ih, Bitmap.Config.ARGB_8888)
            full.copyPixelsFromBuffer(plane.buffer)
            val cropped = if (rowPadding == 0) full else Bitmap.createBitmap(full, 0, 0, iw, ih)
            val out = ByteArrayOutputStream()
            cropped.compress(Bitmap.CompressFormat.JPEG, quality, out)
            if (cropped !== full) cropped.recycle()
            full.recycle()
            val shot = Shot(Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP), iw, ih, rw, rh)
            cached = shot
            return shot
        } finally {
            img.close()
        }
    }

    @Synchronized
    fun release() {
        try { display?.release() } catch (_: Exception) {}
        try { reader?.close() } catch (_: Exception) {}
        display = null
        reader = null
        cached = null
        thread.quitSafely()
    }
}
