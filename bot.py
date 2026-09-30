import os
import threading
from flask import Flask
import telebot
from google import genai

# إعداد تطبيق Flask لإبقاء السيرفر حياً على Render
app = Flask(name)

@app.route('/')
def home():
    return "Bot is running!"

# جلب المفاتيح من المتغيرات البيئية
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    try:
        # استدعاء أحدث نموذج من جيميناي
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=message.text
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        print(f"--- ERROR IN BOT ---: {e}")  # لطباعة الخطأ بدقة في Render
        bot.reply_to(message, f"حدث خطأ أثناء المعالجة: {e}")

if name == "main":
    # تشغيل البوت في خيط خلفي (Thread)
    threading.Thread(target=lambda: bot.infinity_polling(skip_pending=True), daemon=True).start()
    
    # تشغيل Flask على المنفذ المخصص من Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
