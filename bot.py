import os
import time
import threading
from flask import Flask
import telebot
from google import genai

# إعداد تطبيق Flask لإبقاء الخدمة حية
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

def generate_response_with_retry(prompt, retries=3, delay=2):
    # قائمة بالنماذج المتاحة للتنقل بينها في حال وجود ضغط على أحدها
    models_to_try = ["gemini-2.5-flash", "gemini-1.5-flash"]
    
    for model_name in models_to_try:
        for attempt in range(retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                return response.text
            except Exception as e:
                error_str = str(e)
                print(f"Attempt {attempt + 1} with {model_name} failed: {error_str}")
                
                # إذا كان الخطأ بسبب الضغط (503)، ننتظر قليلاً ثم نعيد المحاولة
                if "503" in error_str or "UNAVAILABLE" in error_str:
                    time.sleep(delay)
                else:
                    break  # الانتقال للنموذج التالي في حال وجود خطأ آخر
                    
    return None

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    try:
        reply_text = generate_response_with_retry(message.text)
        if reply_text:
            bot.reply_to(message, reply_text)
        else:
            bot.reply_to(message, "خوادم الذكاء الاصطناعي تشهد ضغطاً عالياً حالياً ⏳. يرجى إعادة إرسال رسالتك بعد ثوانٍ.")
    except Exception as e:
        print(f"--- ERROR IN BOT ---: {e}")
        bot.reply_to(message, "حدث خطأ غير متوقع، يرجى المحاولة لاحقاً.")

if __name__ == "__main__":
    threading.Thread(target=lambda: bot.infinity_polling(skip_pending=True), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
