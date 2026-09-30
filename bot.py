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

# قاموس لتخزين جلسات المحادثة لكل مستخدم لضمان حفظ الذاكرة
user_chats = {}

try:
    bot.remove_webhook()
except Exception as e:
    print(f"Webhook cleanup: {e}")

def get_user_chat(chat_id, model_name="gemini-2.0-flash"):
    if chat_id not in user_chats:
        user_chats[chat_id] = client.chats.create(model=model_name)
    return user_chats[chat_id]

def send_message_with_fallback(chat_id, contents):
    models = ["gemini-2.0-flash", "gemini-1.5-flash"]
    
    for model in models:
        try:
            chat = get_user_chat(chat_id, model_name=model)
            response = chat.send_message(contents)
            if response and response.text:
                return response.text
        except Exception as e:
            print(f"Error with {model}: {e}")
            # في حال حدوث خطأ بالجلسة، نعيد إنشاء الجلسة بالنموذج التالي
            try:
                user_chats[chat_id] = client.chats.create(model=model)
                response = user_chats[chat_id].send_message(contents)
                if response and response.text:
                    return response.text
            except Exception as ex:
                print(f"Retry failed with {model}: {ex}")
    return None

# 1. معالجة النصوص مع تذكر كامل المحادثة
@bot.message_handler(content_types=['text'])
def handle_text(message):
    try:
        chat_id = message.chat.id
        reply_text = send_message_with_fallback(chat_id, message.text)
        
        if reply_text:
            bot.reply_to(message, reply_text)
        else:
            bot.reply_to(message, "عذراً، الخادم مشغول حالياً. يرجى المحاولة بعد لحظات.")
    except Exception as e:
        print(f"Error handling text: {e}")
        bot.reply_to(message, "حدث خطأ أثناء معالجة الرسالة.")

# 2. استقبال الصور وتحليل محتواها
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        chat_id = message.chat.id
        
        # تنزيل أعلى دقة للصورة
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        caption = message.caption if message.caption else "شاهد هذه الصورة واشرحها بالتفصيل."
        
        # تجهيز ملف الصورة للنموذج
        image_part = types.Part.from_bytes(data=downloaded_file, mime_type="image/jpeg")
        contents = [image_part, caption]
        
        reply_text = send_message_with_fallback(chat_id, contents)
        
        if reply_text:
            bot.reply_to(message, reply_text)
        else:
            bot.reply_to(message, "لم أستطع تحليل الصورة حالياً.")
    except Exception as e:
        print(f"Error handling photo: {e}")
        bot.reply_to(message, "حدث خطأ أثناء استلام الصورة.")

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
