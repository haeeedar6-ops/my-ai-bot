import os
import io
import threading
from flask import Flask
import telebot
from google import genai
from PIL import Image

# 1. خادم وهمي لإبقاء Render سعيداً ولا يغلق الخدمة
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# 2. قراءة المفاتيح من المتغيرات البيئية
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# إعداد العميل الجديد لـ Gemini
client = genai.Client(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(BOT_TOKEN)

# الرد على أمر /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "أهلاً بك! أنا بوت الذكاء الاصطناعي الخاص بك.\nيمكنك إرسال نصوص أو صور لأقوم بتحليلها والإجابة عليها فوراً.")

# معالجة الصور
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        image = Image.open(io.BytesIO(downloaded_file))
        
        prompt = message.caption if message.caption else "اشرح هذه الصورة بالتفصيل."
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt, image]
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, "حدث خطأ أثناء تحليل الصورة، يرجى المحاولة لاحقاً.")
        print(f"Error in photo handler: {e}")

# معالجة النصوص
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=message.text
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, "حدث خطأ في المعالجة، يرجى المحاولة لاحقاً.")
        print(f"Error in text handler: {e}")

if __name__ == "__main__":
    # تشغيل خادم Flask في الخلفية
    threading.Thread(target=run_flask, daemon=True).start()
    print("البوت يعمل الآن بنجاح على Render...")
    bot.infinity_polling()
