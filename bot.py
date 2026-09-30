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

# قاموس لتخزين ذاكرة المحادثات لكل مستخدم
user_histories = {}

try:
    bot.remove_webhook()
except Exception as e:
    print(f"Webhook cleanup: {e}")

def get_history(chat_id):
    if chat_id not in user_histories:
        user_histories[chat_id] = []
    return user_histories[chat_id]

def add_to_history(chat_id, user_content, model_text):
    history = get_history(chat_id)
    history.append(user_content)
    history.append(
        types.Content(
            role="model",
            parts=[types.Part.from_text(text=model_text)]
        )
    )
    # الحفاظ على آخر 20 عنصر في الذاكرة (10 محادثات)
    if len(history) > 20:
        user_histories[chat_id] = history[-20:]

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
                print(f"Error with {model_name} (attempt {attempt+1}): {e}")
                time.sleep(delay)
    return None

# 1. معالجة النصوص مع الذاكرة
@bot.message_handler(content_types=['text'])
def handle_text(message):
    try:
        chat_id = message.chat.id
        
        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=message.text)]
        )
        
        full_contents = get_history(chat_id) + [user_content]
        reply_text = generate_response_with_retry(full_contents)
        
        if reply_text:
            add_to_history(chat_id, user_content, reply_text)
            bot.reply_to(message, reply_text)
        else:
            bot.reply_to(message, "عذراً، لم أستطع المعالجة حالياً. يرجى المحاولة لاحقاً.")
    except Exception as e:
        print(f"Error handling text: {e}")
        bot.reply_to(message, "حدث خطأ أثناء معالجة الرسالة.")

# 2. معالجة الصور وقراءتها
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        chat_id = message.chat.id
        
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        caption = message.caption if message.caption else "حلل هذه الصورة واشرح محتواها بالتفصيل."
        
        parts = [
            types.Part.from_bytes(data=downloaded_file, mime_type="image/jpeg"),
            types.Part.from_text(text=caption)
        ]
        
        user_content = types.Content(role="user", parts=parts)
        full_contents = get_history(chat_id) + [user_content]
        
        reply_text = generate_response_with_retry(full_contents)
        
        if reply_text:
            add_to_history(chat_id, user_content, reply_text)
            bot.reply_to(message, reply_text)
        else:
            bot.reply_to(message, "لم أستطع تحليل الصورة حالياً.")
    except Exception as e:
        print(f"Error handling photo: {e}")
        bot.reply_to(message, "حدث خطأ أثناء استقبال أو معالجة الصورة.")

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
