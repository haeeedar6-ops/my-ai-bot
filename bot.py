import os
import time
import threading
from flask import Flask
import telebot
from google import genai
from google.genai import types

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

# قاموس لتخزين ذاكرة المحادثة لكل مستخدم بناءً على chat_id
user_history = {}

try:
    bot.remove_webhook()
except Exception as e:
    print(f"Webhook cleanup: {e}")

def get_user_history(chat_id):
    if chat_id not in user_history:
        user_history[chat_id] = []
    return user_history[chat_id]

def update_user_history(chat_id, user_content, bot_response):
    history = get_user_history(chat_id)
    history.append({"role": "user", "parts": [user_content]})
    history.append({"role": "model", "parts": [bot_response]})
    # الحفاظ على آخر 20 رسالة (10 حوارات) لتجنب تجاوز الحجم المسموح
    if len(history) > 20:
        user_history[chat_id] = history[-20:]

def generate_response_with_retry(contents, retries=2, delay=2):
    models_to_try = ["gemini-2.5-flash", "gemini-1.5-flash"]
    for model_name in models_to_try:
        for attempt in range(retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                print(f"Error with {model_name}: {e}")
                time.sleep(delay)
    return None

# 1. معالج الرسائل النصية (مع حفظ الذاكرة والسياق)
@bot.message_handler(content_types=['text'])
def handle_text(message):
    chat_id = message.chat.id
    history = get_user_history(chat_id)
    
    # دمج سجل المحادثة السابق مع النص الجديد
    prompt_contents = history + [{"role": "user", "parts": [message.text]}]
    
    reply_text = generate_response_with_retry(prompt_contents)
    if reply_text:
        update_user_history(chat_id, message.text, reply_text)
        bot.reply_to(message, reply_text)
    else:
        bot.reply_to(message, "عذراً، حدث خطأ أثناء معالجة الطلب.")

# 2. معالج الصور (تحميل الصورة وقراءتها بواسطة الذكاء الاصطناعي)
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        chat_id = message.chat.id
        
        # تنزيل أعلى دقة للصورة المرفقة
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        image_part = types.Part.from_bytes(
            data=downloaded_file,
            mime_type="image/jpeg"
        )
        
        caption = message.caption if message.caption else "حلل هذه الصورة واشرح محتواها بالتفصيل."
        
        # إرسال الصورة والنص معاً للنموذج
        reply_text = generate_response_with_retry([image_part, caption])
        
        if reply_text:
            update_user_history(chat_id, f"[صورة: {caption}]", reply_text)
            bot.reply_to(message, reply_text)
        else:
            bot.reply_to(message, "لم أستطع تحليل الصورة، يرجى المحاولة مرة أخرى.")
    except Exception as e:
        print(f"Error processing photo: {e}")
        bot.reply_to(message, "حدث خطأ أثناء استقبال الصورة.")

def run_polling():
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=run_polling, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
