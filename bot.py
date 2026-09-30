import os
import time
import threading
from flask import Flask
import telebot
from google import genai

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

# تنظيف أي Webhook معلق
try:
    bot.remove_webhook()
except Exception as e:
    print(f"Webhook cleanup: {e}")

def generate_response_with_retry(prompt, retries=2, delay=2):
    # استخدام اسم النموذج المعتمد والرسمي من Google
    models_to_try = ["gemini-3.8-flash", "gemini-1.5-flash"]
    
    for model_name in models_to_try:
        for attempt in range(retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                print(f"Error with {model_name} (attempt {attempt+1}): {e}")
                time.sleep(delay)
    return None

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    try:
        reply_text = generate_response_with_retry(message.text)
        if reply_text:
            bot.reply_to(message, reply_text)
        else:
            bot.reply_to(message, "عذراً، لم أستطع المعالجة حالياً. يرجى المحاولة لاحقاً.")
    except Exception as e:
        print(f"Error in handler: {e}")

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
