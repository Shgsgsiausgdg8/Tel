import json
import os
import re
import asyncio
import logging
import random
from contextlib import contextmanager
import sqlite3
from datetime import datetime, timedelta
from telethon import TelegramClient, events, Button
from telethon.tl.functions.messages import GetPeerDialogsRequest, SetTypingRequest
from telethon.tl.types import SendMessageTypingAction, SendMessageUploadPhotoAction, SendMessageUploadDocumentAction
import aiohttp
from fuzzywuzzy import fuzz
import hashlib
from tenacity import retry, stop_after_attempt, wait_fixed

# تنظیمات کلی
API_ID = 28550576
API_HASH = "208dfd9f8b5a6722688875f8ce9558a9"
ADMIN_IDS = [-1002510009543, 1905123829, 7937668031]  # اولین: گروه مدیریت، بقیه: ادمین‌ها
API_AI_TOKEN = os.getenv("ONE_API_TOKEN", "657183:67a9655bdca33")
API_AI_URL = "https://api.one-api.ir/chatbot/v1/gpt3.5-turbo/"
RESPONSES_JSON = "responses.json"
SESSION_FILE = "session.session"
FUZZY_THRESHOLD = 90

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# کلاینت تلگرام
client = TelegramClient('session', API_ID, API_HASH)

# بررسی وجود فایل سشن
if not os.path.exists(SESSION_FILE):
    logger.error("فایل سشن یافت نشد!")
    raise FileNotFoundError("فایل session.session یافت نشد. لطفاً فایل سشن معتبر را فراهم کنید.")

# بارگذاری پاسخ‌های JSON
def load_responses():
    try:
        with open(RESPONSES_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"خطا در بارگذاری responses.json: {e}")
        return {"responses": [], "welcome_messages": [], "settings": {}}

RESPONSES = load_responses()

# مدیریت اتصال دیتابیس
@contextmanager
def get_db_connection():
    conn = sqlite3.connect('users.db')
    try:
        yield conn
    finally:
        conn.close()

# دیتابیس
def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        name TEXT,
                        interaction_count INTEGER DEFAULT 0,
                        is_banned INTEGER DEFAULT 0
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS responses (
                        keyword TEXT PRIMARY KEY,
                        response TEXT,
                        category TEXT,
                        weight INTEGER DEFAULT 1
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS conversations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        message TEXT,
                        response TEXT,
                        timestamp TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS frequent_questions (
                        question_hash TEXT PRIMARY KEY,
                        question_text TEXT,
                        frequency INTEGER,
                        suggested_response TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS unanswered_questions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        question TEXT,
                        timestamp TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS gpt_cache (
                        message_hash TEXT PRIMARY KEY,
                        response TEXT,
                        timestamp TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS admin_notifications (
                        type TEXT PRIMARY KEY,
                        enabled INTEGER DEFAULT 1
                     )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations (user_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_responses_keyword ON responses (keyword)')
        c.execute('INSERT OR IGNORE INTO admin_notifications (type, enabled) VALUES (?, ?)', ('frequent', 1))
        c.execute('INSERT OR IGNORE INTO admin_notifications (type, enabled) VALUES (?, ?)', ('unanswered', 1))
        c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('api_enabled', '1'))
        conn.commit()
        sync_responses()

# همگام‌سازی پاسخ‌های JSON
def sync_responses():
    with get_db_connection() as conn:
        c = conn.cursor()
        for response in RESPONSES["responses"]:
            for keyword in response["keyword"]:
                c.execute('INSERT OR REPLACE INTO responses (keyword, response, category, weight) VALUES (?, ?, ?, ?)',
                          (keyword.lower(), response["response"][0]["text"], response["category"], response.get("priority", 1)))
        conn.commit()

# اعتبارسنجی ورودی
def sanitize_input(text):
    if not text:
        return text
    malicious_patterns = [
        r'<\s*script\s*>|<\s*iframe\s*>',
        r'(\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b|\bDROP\b|\bALTER\b)\s+.*\bFROM\b',
        r'(\bexec\b|\bsystem\b|\bpopen\b)\s*\(',
        r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]',
        r'(\bhttp\b|\bhttps\b)://.*\.(exe|sh|bat)',
    ]
    for pattern in malicious_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            logger.warning(f"پیام مشکوک شناسایی شد: {text}")
            return None
    text = re.sub(r'[<>{};]', '', text)
    return text[:1000]

# ذخیره و دریافت تنظیمات
def save_setting(key, value):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
        conn.commit()

def get_setting(key, default=None):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT value FROM settings WHERE key = ?', (key,))
        result = c.fetchone()
        return result[0] if result else default

# ذخیره کاربر
def save_user(user_id, name=None, interaction_count=None, is_banned=None):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        existing = c.fetchone()
        if existing:
            updates = {}
            if name: updates['name'] = sanitize_input(name)
            if interaction_count is not None: updates['interaction_count'] = interaction_count
            if is_banned is not None: updates['is_banned'] = is_banned
            if updates:
                query = 'UPDATE users SET ' + ', '.join(f'{k} = ?' for k in updates.keys()) + ' WHERE user_id = ?'
                c.execute(query, list(updates.values()) + [user_id])
        else:
            c.execute('INSERT INTO users (user_id, name, interaction_count, is_banned) VALUES (?, ?, ?, ?)',
                      (user_id, sanitize_input(name) or 'کاربر', 0, 0))
        conn.commit()

# ذخیره مکالمه
def save_conversation(user_id, message, response):
    with get_db_connection() as conn:
        c = conn.cursor()
        message = sanitize_input(message)
        response = sanitize_input(response)
        if message is not None and response is not None:
            c.execute('INSERT INTO conversations (user_id, message, response, timestamp) VALUES (?, ?, ?, ?)',
                      (user_id, message, response, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()

# شناسایی سوالات پرتکرار
async def identify_frequent_questions():
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT message, COUNT(*) as freq FROM conversations WHERE timestamp > ? GROUP BY message HAVING freq > 5',
                  ((datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S'),))
        questions = c.fetchall()
        for question, freq in questions:
            question_hash = hashlib.sha256(question.encode()).hexdigest()
            c.execute('INSERT OR REPLACE INTO frequent_questions (question_hash, question_text, frequency, suggested_response) VALUES (?, ?, ?, ?)',
                      (question_hash, question, freq, ''))
        conn.commit()

# کش پاسخ‌ها
def get_cached_response(message):
    message_hash = hashlib.sha256(message.encode()).hexdigest()
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT response FROM gpt_cache WHERE message_hash = ? AND timestamp > ?',
                  (message_hash, (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')))
        result = c.fetchone()
        if result:
            logger.info(f"پاسخ کش‌شده برای پیام '{message}' یافت شد: {result[0]}")
        return result[0] if result else None

def save_cached_response(message, response):
    message_hash = hashlib.sha256(message.encode()).hexdigest()
    response = sanitize_input(response)
    if response:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO gpt_cache (message_hash, response, timestamp) VALUES (?, ?, ?)',
                      (message_hash, response, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
            logger.info(f"پاسخ برای پیام '{message}' در کش ذخیره شد: {response}")

# تولید پاسخ با API AI
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def generate_ai_response(user_id, user_name, user_message, interaction_count):
    user_message = sanitize_input(user_message)
    if not user_message:
        return None
    if get_setting('api_enabled', '1') == '0':
        logger.info(f"API غیرفعال است. پیام: {user_message}")
        return None
    cached_response = get_cached_response(user_message)
    if cached_response:
        return cached_response

    prompt = (
        f"تو ربات بلک تریدر هستی، متخصص کمک به معامله‌گران طلا، انس، و مظنه. "
        f"کاربر {user_name} پرسیده: '{user_message}'. "
        f"تعاملات کاربر: {interaction_count}. "
        f"به فارسی، با لحن دوستانه و حرفه‌ای (مثل چت با یک دوست معامله‌گر) پاسخ بده. "
        f"فقط درباره طلا، انس، مظنه، و آموزش ترید صحبت کن. اگر پیام عمومی (مثل 'چخبرا') بود، به کانال‌ها هدایت کن. "
        f"پاسخ کوتاه (حداکثر 50 کلمه)، طبیعی، با یک ایموجی مرتبط. "
        f"کانال مناسب پیشنهاد بده: "
        f"- برای انس: @blacktraderons "
        f"- برای مظنه یا VIP: @blacktradergold "
        f"- برای آموزش: @blacktraderamoozesh "
        f"مثال: 'چخبرا؟ 😎 سیگنال انس می‌خوای؟ چک کن: @blacktraderons' "
    )

    headers = {"accept": "application/json", "one-api-token": API_AI_TOKEN, "Content-Type": "application/json"}
    data = [{"role": "user", "content": prompt}]
    async with aiohttp.ClientSession() as session:
        async with session.post(API_AI_URL, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 200:
                result = await response.json()
                if isinstance(result, dict) and "result" in result and result["result"]:
                    ai_response = result["result"][0].strip()
                    if re.search(r'chatgpt|هوش مصنوعی|ai|فارکس|کریپتو', ai_response, re.IGNORECASE):
                        return None
                    save_cached_response(user_message, ai_response)
                    return ai_response
            logger.error(f"خطای API: کد {response.status}")
            try:
                await client.send_message(ADMIN_IDS[0], f"⚠️ خطای API: کد {response.status}")
            except Exception as e:
                logger.error(f"خطا در ارسال نوتیفیکیشن به ادمین اصلی {ADMIN_IDS[0]}: {e}")
            return None

# منوی پویا
async def get_dynamic_buttons(user_id):
    logger.info(f"تولید دکمه‌ها برای کاربر {user_id}")
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT message FROM conversations WHERE user_id = ? LIMIT 10', (user_id,))
        messages = c.fetchall()
    buttons = [
        [Button.inline("💰 انس", b"ons"), Button.inline("📈 مظنه", b"mazneh")],
        [Button.inline("🎓 آموزش", b"amoozesh"), Button.inline("📞 ادمین", b"admin")],
        [Button.inline("👤 پروفایل", b"profile")]
    ]
    if any("مظنه" in m[0].lower() for m in messages):
        buttons = [
            [Button.inline("📈 مظنه", b"mazneh"), Button.inline("💰 انس", b"ons")],
            [Button.inline("🎓 آموزش", b"amoozesh"), Button.inline("📞 ادمین", b"admin")],
            [Button.inline("👤 پروفایل", b"profile")]
        ]
    logger.info(f"دکمه‌های تولیدشده: {buttons}")
    return buttons

# خوش‌آمدگویی
async def send_welcome_message(user_id, user_name):
    logger.info(f"ارسال پیام خوش‌آمدگویی به کاربر {user_id} ({user_name})")
    welcome_messages = RESPONSES.get("welcome_messages", [])
    if not welcome_messages:
        welcome_messages = [
            "سلام {name}! 😊 به بلک تریدر خوش اومدی! برای سیگنال‌های خفن آماده‌ای؟"
        ]
    message = random.choice(welcome_messages).format(name=user_name)
    buttons = [
        [Button.url("💰 کانال انس", "https://t.me/blacktraderons"), Button.url("📈 کانال مظنه", "https://t.me/blacktradergold")]
    ]
    logger.info(f"پیام خوش‌آمدگویی: {message}, دکمه‌ها: {buttons}")
    await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
    await client(SetTypingRequest(user_id, action=SendMessageTypingAction()))
    await asyncio.sleep(0.3)
    await client.send_message(user_id, message, parse_mode='html', buttons=buttons)

# راهنمای تعاملی
async def send_onboarding(user_id):
    logger.info(f"ارسال راهنمای تعاملی برای کاربر {user_id}")
    steps = [
        "1️⃣ با دکمه‌های 'مظنه' و 'انس' سیگنال بگیر!",
        "2️⃣ آموزش‌های رایگان رو از 'آموزش' چک کن!",
        "3️⃣ سوالی داشتی؟ با 'ادمین' چت کن!"
    ]
    for i, step in enumerate(steps):
        buttons = [[Button.inline("بعدی", b"onboarding_next")]] if i < len(steps)-1 else await get_dynamic_buttons(user_id)
        await client.send_message(user_id, step, parse_mode='html', buttons=buttons)
        await asyncio.sleep(1.5)

# هندلر پیام‌ها
@client.on(events.NewMessage)
async def handle_message(event):
    try:
        if event.message.out:
            return
        if not event.is_private:
            return
        sender = await event.get_sender()
        if sender.bot or hasattr(sender, 'channel'):
            return
        user_id = event.sender_id
        user_name = sender.first_name or "کاربر"
        logger.info(f"پیام دریافتی از کاربر {user_id} ({user_name}): {event.message.text or 'رسانه'}")

        # بررسی رسانه
        if not event.message.text:
            media_type = "رسانه ناشناخته"
            if event.message.photo:
                media_type = "عکس"
                await client(SetTypingRequest(user_id, action=SendMessageUploadPhotoAction()))
            elif event.message.video:
                media_type = "ویدیو"
            elif event.message.gif:
                media_type = "گیف"
            elif event.message.audio:
                media_type = "صدا"
            elif event.message.document:
                media_type = "فایل"
                await client(SetTypingRequest(user_id, action=SendMessageUploadDocumentAction()))
            
            bot_response = f"{media_type} رو گرفتم {user_name}! 😎 ادمین به زودی بررسی می‌کنه."
            await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
            await event.reply(bot_response)
            try:
                await client.forward_messages(ADMIN_IDS[0], event.message)
            except Exception as e:
                logger.error(f"خطا در فوروارد رسانه به ادمین اصلی {ADMIN_IDS[0]}: {e}")
            save_conversation(user_id, f"ارسال {media_type}", bot_response)
            return

        user_message = event.message.text
        if not user_message:
            return
        user_message_lower = user_message.lower()
        user_message_sanitized = sanitize_input(user_message)
        buttons = await get_dynamic_buttons(user_id)

        if user_message_sanitized is None:
            try:
                await client.send_message(ADMIN_IDS[0], f"🚨 پیام مشکوک از کاربر {user_name} (ID: {user_id}): {user_message}")
            except Exception as e:
                logger.error(f"خطا در ارسال نوتیفیکیشن به ادمین اصلی {ADMIN_IDS[0]}: {e}")
            bot_response = "لطفاً پیام خود را واضح‌تر بنویسید یا با ادمین تماس بگیرید: @BlackTraderAdmin 😊"
            await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
            await client(SetTypingRequest(user_id, action=SendMessageTypingAction()))
            await asyncio.sleep(0.3)
            await event.reply(bot_response, parse_mode='html', buttons=buttons)
            save_conversation(user_id, user_message, bot_response)
            return

        save_conversation(user_id, user_message, '')

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT interaction_count, is_banned FROM users WHERE user_id = ?', (user_id,))
            user_data = c.fetchone()
            c.execute('SELECT message FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT 3', (user_id,))
            recent_messages = [row[0] for row in c.fetchall()]

        if user_data and user_data[1]:
            bot_response = "لطفاً صبر کنید، پیام شما به مدیر ارجاع شد. 😊 به زودی پاسخ می‌گیرید!"
            await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
            await client(SetTypingRequest(user_id, action=SendMessageTypingAction()))
            await asyncio.sleep(0.3)
            await event.reply(bot_response, parse_mode='html', buttons=buttons)
            save_conversation(user_id, user_message, bot_response)
            return

        interaction_count = user_data[0] if user_data else 0
        interaction_count += 1
        save_user(user_id, name=user_name, interaction_count=interaction_count)
        logger.info(f"تعامل کاربر {user_id}: interaction_count={interaction_count}")

        # خوش‌آمدگویی برای کاربران جدید
        if interaction_count == 1:
            await send_welcome_message(user_id, user_name)
            await send_onboarding(user_id)
            return

        # پاسخ‌های چندمرحله‌ای
        if "چطور ترید کنم" in user_message_lower:
            steps = [
                "1️⃣ اول مدیریت سرمایه رو یاد بگیر: @blacktraderamoozesh",
                "2️⃣ با سیگنال‌های رایگان انس شروع کن: @blacktraderons",
                "3️⃣ تو کانال VIP حرفه‌ای شو: @blacktradergold"
            ]
            client.state = {"user_id": user_id, "steps": steps, "current": 0}
            await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
            await client(SetTypingRequest(user_id, action=SendMessageTypingAction()))
            await asyncio.sleep(0.3)
            await event.reply(steps[0], parse_mode='html', buttons=[[Button.inline("ادامه", b"next_step")]])
            save_conversation(user_id, user_message, steps[0])
            return

        # بررسی پاسخ‌های JSON
        for response in sorted(RESPONSES["responses"], key=lambda x: x.get("priority", 1), reverse=True):
            for keyword in response["keyword"]:
                if fuzz.ratio(keyword.lower(), user_message_lower) >= FUZZY_THRESHOLD:
                    selected_response = response["response"][0]["text"]
                    for resp in response["response"]:
                        if interaction_count >= resp["interaction_count"]:
                            selected_response = resp["text"]
                    bot_response = selected_response.format(name=user_name)
                    await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
                    await client(SetTypingRequest(user_id, action=SendMessageTypingAction()))
                    await asyncio.sleep(0.3)
                    await event.reply(bot_response, parse_mode='html', buttons=buttons)
                    logger.info(f"پاسخ ارسالی به {user_id}: {bot_response}, دکمه‌ها: {buttons}")
                    save_conversation(user_id, user_message, bot_response)
                    return

        # بررسی دیتابیس
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT response, weight FROM responses WHERE keyword = ?', (user_message_lower,))
            db_response = c.fetchone()
            if db_response:
                bot_response = db_response[0].format(name=user_name)
                await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
                await client(SetTypingRequest(user_id, action=SendMessageTypingAction()))
                await asyncio.sleep(0.3)
                await event.reply(bot_response, parse_mode='html', buttons=buttons)
                logger.info(f"پاسخ ارسالی به {user_id}: {bot_response}, دکمه‌ها: {buttons}")
                save_conversation(user_id, user_message, bot_response)
                return

        # بررسی احساسات
        if "ضرر" in user_message_lower or "باختم" in user_message_lower:
            bot_response = await generate_ai_response(user_id, user_name, user_message, interaction_count)
            if not bot_response:
                bot_response = f"اووه {user_name}، ضرر سخته! 😔 ولی نگران نباش، بازار فرصت می‌ده. آموزش مدیریت سرمایه: <a href='@blacktraderamoozesh'>اینجا</a>"
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute('SELECT COUNT(*) FROM conversations WHERE user_id = ? AND message LIKE ? AND timestamp > ?',
                          (user_id, '%ضرر%', (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')))
                loss_count = c.fetchone()[0]
            if loss_count >= 3:
                try:
                    await client.send_message(ADMIN_IDS[0], f"🚨 هشدار احساسی: کاربر {user_name} (ID: {user_id}) {loss_count} بار ضرر گزارش کرده. پیام: {user_message}")
                except Exception as e:
                    logger.error(f"خطا در ارسال نوتیفیکیشن به ادمین اصلی {ADMIN_IDS[0]}: {e}")
            await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
            await client(SetTypingRequest(user_id, action=SendMessageTypingAction()))
            await asyncio.sleep(0.3)
            await event.reply(bot_response, parse_mode='html', buttons=buttons)
            logger.info(f"پاسخ ارسالی به {user_id}: {bot_response}, دکمه‌ها: {buttons}")
            save_conversation(user_id, user_message, bot_response)
            return
        elif "سود" in user_message_lower or "برنده" in user_message_lower:
            bot_response = await generate_ai_response(user_id, user_name, user_message, interaction_count)
            if not bot_response:
                bot_response = f"دمت گرم {user_name}! 💪 سودتو سیو کن! آموزش روانشناسی: <a href='@blacktraderamoozesh'>اینجا</a>"
            await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
            await client(SetTypingRequest(user_id, action=SendMessageTypingAction()))
            await asyncio.sleep(0.3)
            await event.reply(bot_response, parse_mode='html', buttons=buttons)
            logger.info(f"پاسخ ارسالی به {user_id}: {bot_response}, دکمه‌ها: {buttons}")
            save_conversation(user_id, user_message, bot_response)
            return

        # API AI
        bot_response = await generate_ai_response(user_id, user_name, user_message, interaction_count)
        if not bot_response:
            bot_response = f"{user_name}، سوالت تخصصیه! 😎 یه نکته بخون: <a href='@blacktraderamoozesh'>اینجا</a>\nدکمه‌ها رو نمی‌بینی؟ تلگرامت رو آپدیت کن!"
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute('INSERT INTO unanswered_questions (user_id, question, timestamp) VALUES (?, ?, ?)',
                      (user_id, user_message, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            c.execute('SELECT enabled FROM admin_notifications WHERE type = ?', ('unanswered',))
            result = c.fetchone()
            if result and result[0]:
                try:
                    await client.send_message(ADMIN_IDS[0], f"❓ سوال جدید: کاربر {user_name} (ID: {user_id}) پرسید: {user_message}")
                except Exception as e:
                    logger.error(f"خطا در ارسال نوتیفیکیشن به ادمین اصلی {ADMIN_IDS[0]}: {e}")
            conn.commit()
        await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
        await client(SetTypingRequest(user_id, action=SendMessageTypingAction()))
        await asyncio.sleep(0.3)
        await event.reply(bot_response, parse_mode='html', buttons=buttons)
        logger.info(f"پاسخ ارسالی به {user_id}: {bot_response}, دکمه‌ها: {buttons}")
        save_conversation(user_id, user_message, bot_response)

    except Exception as e:
        logger.error(f"خطا در handle_message: {e}")
        bot_response = f"اوپس {user_name}! 😅 یه مشکلی پیش اومد. با ادمین تماس بگیر: @BlackTraderAdmin"
        await event.reply(bot_response, parse_mode='html')
        save_conversation(user_id, user_message, bot_response)

# هندلر دکمه‌ها
@client.on(events.CallbackQuery)
async def handle_callback(event):
    try:
        user_id = event.sender_id
        sender = await event.get_sender()
        if sender.bot or hasattr(sender, 'channel'):
            return
        data = event.data.decode()
        buttons = await get_dynamic_buttons(user_id)
        logger.info(f"دکمه کلیک‌شده توسط کاربر {user_id}: {data}")
        if data == "mazneh":
            bot_response = "مظنه تخصص ماست! 💪 تو کانال VIP سیگنالای خفن داریم: <a href='@blacktradergold'>اینجا</a>"
        elif data == "ons":
            bot_response = "سیگنال انس رایگانه! 😎 روزانه تا ۱۰ سیگنال: <a href='@blacktraderons'>اینجا</a>"
        elif data == "amoozesh":
            bot_response = "آموزشای جامع مظنه و انس: <a href='@blacktraderamoozesh'>اینجا</a>"
        elif data == "admin":
            bot_response = "با ادمین چت کن: @BlackTraderAdmin"
        elif data == "profile":
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute('SELECT interaction_count FROM users WHERE user_id = ?', (user_id,))
                interaction_count = c.fetchone()[0]
            bot_response = (
                f"👤 پروفایل شما\n"
                f"- نام: {sender.first_name or 'کاربر'}\n"
                f"- تعاملات: {interaction_count}\n"
                f"- وضعیت: {'VIP' if interaction_count > 50 else 'عادی'}\n"
                f"- کانال پیشنهادی: @blacktradergold"
            )
        elif data == "onboarding_next":
            bot_response = None
            await event.delete()
            return
        elif data == "next_step":
            state = client.state
            if state and state["user_id"] == user_id:
                state["current"] += 1
                if state["current"] < len(state["steps"]):
                    bot_response = state["steps"][state["current"]]
                    await event.reply(bot_response, parse_mode='html', buttons=[[Button.inline("ادامه", b"next_step")]])
                else:
                    bot_response = "تموم شد! 😎 حالا آماده تریدی؟"
                    client.state = None
                save_conversation(user_id, "Next step", bot_response)
                return
        if bot_response:
            await client(GetPeerDialogsRequest(peers=[await client.get_input_entity(user_id)]))
            await client(SetTypingRequest(user_id, action=SendMessageTypingAction()))
            await asyncio.sleep(0.3)
            await event.reply(bot_response, parse_mode='html', buttons=buttons)
            logger.info(f"پاسخ ارسالی به {user_id}: {bot_response}, دکمه‌ها: {buttons}")
            save_conversation(user_id, f"Button: {data}", bot_response)
    except Exception as e:
        logger.error(f"خطا در handle_callback: {e}")
        bot_response = f"اوپس {sender.first_name or 'کاربر'}! 😅 یه مشکلی پیش اومد. با ادمین تماس بگیر: @BlackTraderAdmin"
        await event.reply(bot_response, parse_mode='html')

# نوتیفیکیشن سوالات پرتکرار
async def notify_frequent_questions():
    while True:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT question_text, frequency FROM frequent_questions WHERE frequency > 5')
            questions = c.fetchall()
            c.execute('SELECT enabled FROM admin_notifications WHERE type = ?', ('frequent',))
            result = c.fetchone()
            if result and result[0]:
                if questions:
                    message = "📋 سوالات پرتکرار جدید:\n" + "\n".join(f"- {q[0]} ({q[1]} بار)" for q in questions)
                    try:
                        await client.send_message(ADMIN_IDS[0], message)
                    except Exception as e:
                        logger.error(f"خطا در ارسال نوتیفیکیشن به ادمین اصلی {ADMIN_IDS[0]}: {e}")
        await asyncio.sleep(24 * 3600)

# وظایف دوره‌ای
async def periodic_tasks():
    while True:
        await identify_frequent_questions()
        try:
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute('SELECT 1')
        except Exception as e:
            logger.error(f"خطای دیتابیس در بررسی سلامت: {e}")
            try:
                await client.send_message(ADMIN_IDS[0], f"⚠️ خطای دیتابیس: {e}")
            except Exception as e:
                logger.error(f"خطا در ارسال نوتیفیکیشن به ادمین اصلی {ADMIN_IDS[0]}: {e}")
        await asyncio.sleep(24 * 3600)

# راه‌اندازی ربات

async def main():
    init_db()
    while True:
        try:
            await client.start()
            logger.info("ربات شروع شد!")
            # وظایف پس‌زمینه مانند task های دوره‌ای و نوتیفیکیشن‌ها
            asyncio.create_task(periodic_tasks())
            asyncio.create_task(notify_frequent_questions())
            await client.run_until_disconnected()
        except Exception as e:
            logger.error(f"قطع ارتباط: {e}. تلاش برای اتصال مجدد پس از 10 ثانیه...")
            try:
                await client.send_message(ADMIN_IDS[0], f"⚠️ ربات قطع شد: {e}. در حال ری‌کانکت...")
            except Exception as e:
                logger.error(f"خطا در ارسال نوتیفیکیشن به ادمین اصلی {ADMIN_IDS[0]}: {e}")
            await client.disconnect()
            await asyncio.sleep(10)

if __name__ == '__main__':
    client.state = None
    asyncio.run(main())