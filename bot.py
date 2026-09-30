last_err = e
                code = getattr(e, "code", None)
                log.error("Gemini [%s] code=%s attempt=%s: %s", model, code, attempt + 1, e)
                if code in (404, 400):      # موديل غير موجود/غير مدعوم -> جرب التالي
                    break
                if code in (429, 500, 503): # ضغط -> انتظر وأعد
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
            except Exception as e:
                last_err = e
                log.exception("Gemini unexpected error")
                time.sleep(1)
    raise last_err or RuntimeError("كل الموديلات فشلت")


def friendly_error(e):
    code = getattr(e, "code", None)
    if code == 429:
        return "⏳ في ضغط كبير حالياً، جرّب بعد شوي."
    if code in (401, 403):
        return "🔑 في مشكلة بمفتاح Gemini، لازم يتحقق منه صاحب البوت."
    return "⚠️ صار خطأ أثناء المعالجة، جرّب مرة تانية."


def process(message, user_parts, history_label):
    """المعالجة المشتركة: نص / صورة / صوت."""
    chat_id = message.chat.id
    if is_spam(chat_id):
        bot.reply_to(message, "استنى شوي وأرسل تاني 🙂")
        return
    try:
        with typing(chat_id):
            contents = build_contents(chat_id)
            contents.append(types.Content(role="user", parts=user_parts))
            answer = ask_gemini(contents)
        if answer:
            send_long(message, answer)
            save_turn(chat_id, history_label, answer)
        else:
            bot.reply_to(message, "ما قدرت أجاوب على هاد، جرّب صياغة تانية.")
    except Exception as e:
        log.exception("Process error")
        bot.reply_to(message, friendly_error(e))


# ============ الأوامر ============
@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.reply_to(
        message,
        "أهلاً فيك! 👋\nابعتلي أي سؤال أو صورة أو رسالة صوتية وأنا بجاوبك.\n"
        "/reset لمسح الذاكرة\n/help للمساعدة",
    )


@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.reply_to(
        message,
        "• اكتب سؤالك عادي\n"
        "• ابعت صورة مع تعليق (اختياري)\n"
        "• ابعت رسالة صوتية\n"
        "• /reset لبدء محادثة جديدة",
    )


@bot.message_handler(commands=["reset"])
def cmd_reset(message):
    with lock:
        histories.pop(message.chat.id, None)
    bot.reply_to(message, "🧹 تم مسح الذاكرة.")


# ============ الرسائل ============
@bot.message_handler(content_types=["text"])
def handle_text(message):
    text = (message.text or "").strip()
    if not text:
        return
    process(message, [types.Part(text=text)], text)


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    try:
        info = bot.get_file(message.photo[-1].file_id)
        data = bot.download_file(info.file_path)
    except Exception:
        log.exception("Photo download error")
        bot.reply_to(message, "ما قدرت أنزّل الصورة، جرّب تاني.")
        return
    caption = message.caption or "اشرح هالصورة."
    parts = [
        types.Part.from_bytes(data=data, mime_type="image/jpeg"),
        types.Part(text=caption),
    ]
    process(message, parts, f"[صورة: {caption}]")


@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    try:
        info = bot.get_file(message.voice.file_id)
        data = bot.download_file(info.file_path)
    except Exception:
        log.exception("Voice download error")
        bot.reply_to(message, "ما قدرت أنزّل الرسالة الصوتية، جرّب تاني.")
        return
    parts = [
        types.Part.from_bytes(data=data, mime_type="audio/ogg"),
        types.Part(text="افهم هالرسالة الصوتية وجاوب عليها."),
    ]
    process(message, parts, "[رسالة صوتية]")


@bot.message_handler(content_types=["sticker", "video", "document", "audio"])
def handle_unsupported(message):
    bot.reply_to(message, "حالياً بدعم النص والصور والرسائل الصوتية بس 🙂")
    # ============ التشغيل ============
def run_polling():
    while True:
        try:
            log.info("Polling started | models=%s", MODELS)
            bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
        except Exception as e:
            log.error("Polling crashed: %s", e)
            time.sleep(5)


if name == "main":
    threading.Thread(target=run_polling, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
