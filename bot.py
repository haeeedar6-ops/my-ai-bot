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

# قاموس لتخزين الذاكرة النصية لكل مستخدم
user_histories = {}

try:
    bot.remove_webhook()
except Exception as e:
    print(f"Webhook cleanup: {e}")

def get_history(chat_id):
    if chat_id not in user_histories:
        user_histories[chat_id] = []
    return user_histories[chat_id]

def update_history(chat_id, user_text, bot_text):
    history = get_history(chat_id)
    history.append(f"المستخدم: {user_text}")
    history.append(f"البوت: {bot_text}")
    if len(history) > 10:  # الاحتفاظ بآخر 5 محادثات
        user_histories[chat_id] = history[-10:]

# 1. معالجة النصوص مع الذاكرة
@bot.message_handler(content_types=['text'])
def handle_text(message):
    try:
        chat_id = message.chat.id
        user_text = message.text
        
        # دمج الذاكرة مع السؤال الجديد
        history = get_history(chat_id)
        prompt = "\n".join(history) + f"\nالمستخدم: {user_text}\nالبوت:"
        
        # استخدام النموذج المستقر
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )
        
        if response and response.text:
            bot.reply_to(message, response.text)
            update_history(chat_id, user_text, response.text)
        else:
            bot.reply_to(message, "حدث خطأ غير متوقع في المعالجة.")
            
    except Exception as e:
        print(f"Text Error: {e}")
        bot.reply_to(message, "عذراً، حدث خطأ أثناء المعالجة.")

# 2. معالجة الصور
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        chat_id = message.chat.id
        
        # تنزيل الصورة
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        caption = message.caption if message.caption else "اشرح هذه الصورة."
        
        image_part = types.Part.from_bytes(data=downloaded_file, mime_type="image/jpeg")
        
        # نرسل الصورة مع النص مباشرة للنموذج
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=[image_part, caption]
        )
        
        if response and response.text:
            bot.reply_to(message, response.text)
            update_history(chat_id, f"[أرسل صورة: {caption}]", response.text)
        else:
             bot.reply_to(message, "لم أستطع قراءة الصورة.")
             
    except Exception as e:
        print(f"Photo Error: {e}")
        bot.reply_to(message, "حدث خطأ أثناء معالجة الصورة.")

def run_polling():
    while True:
        try:
            bot.infinity_polling(skip_pending=True)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(3)

if __name__ == "__main__":
    threading.Thread(target=run_polling, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
