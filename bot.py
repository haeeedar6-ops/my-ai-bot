import os
import time
import logging
import threading
from flask import Flask
import telebot
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise SystemExit("BOT_TOKEN أو GEMINI_API_KEY ناقصين بالـ Environment Variables")

SYSTEM_PROMPT = "أنت مساعد ذكي. جاوب بالعربي بشكل مختصر ومفيد."
MAX_HISTORY = 10  # آخر 5 محادثات (سؤال + جواب)

app = Flask(name)
bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
client = genai.Client(api_key=GEMINI_API_KEY)

user_histories = {}  # chat_id -> [(role, text), ...]
lock = threading.Lock()


@app.route("/")
def home():
    return "Bot is running!"


try:
    bot.remove_webhook()
except Exception as e:
    log.warning("Webhook cleanup: %s", e)
