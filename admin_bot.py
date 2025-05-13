import sqlite3
import asyncio
import logging
import os
from datetime import datetime, timedelta
from telethon import TelegramClient, events, Button
from fuzzywuzzy import fuzz
import shutil

# تنظیمات کلی
API_ID = 28550576
API_HASH = "208dfd9f8b5a6722688875f8ce9558a9"
ADMIN_BOT_TOKEN = "8046155302:AAGD567WKP2tUxzzewqorEsG8KqugNnBx9I"
ADMIN_IDS = [-1002510009543, 1905123829, 7937668031]  # اولین: گروه مدیریت، بقیه: ادمین‌ها

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler('admin_bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# کلاینت تلگرام
client = TelegramClient('admin_session', API_ID, API_HASH)

# دیتابیس
def init_db():
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS responses (
                        keyword TEXT PRIMARY KEY,
                        response TEXT,
                        category TEXT
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
        c.execute('''CREATE TABLE IF NOT EXISTS admin_notifications (
                        type TEXT PRIMARY KEY,
                        enabled INTEGER DEFAULT 1
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                     )''')
        c.execute('INSERT OR IGNORE INTO admin_notifications (type, enabled) VALUES (?, ?)', ('unanswered', 1))
        c.execute('INSERT OR IGNORE INTO admin_notifications (type, enabled) VALUES (?, ?)', ('warning', 1))
        c.execute('INSERT OR IGNORE INTO admin_notifications (type, enabled) VALUES (?, ?)', ('frequent', 1))
        c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('api_enabled', '1'))
        conn.commit()

# ذخیره پاسخ
def save_response(keyword, response, category='general'):
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO responses (keyword, response, category) VALUES (?, ?, ?)',
                  (keyword.lower(), response, category))
        conn.commit()

# حذف پاسخ
def delete_response(keyword):
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('DELETE FROM responses WHERE keyword = ?', (keyword.lower(),))
        conn.commit()

# ذخیره تنظیمات
def save_setting(key, value):
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
        conn.commit()

# دریافت تنظیمات
def get_setting(key, default=None):
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('SELECT value FROM settings WHERE key = ?', (key,))
        result = c.fetchone()
        return result[0] if result else default

# تولید داشبورد
async def generate_dashboard():
    with sqlite3.connect('users.db') as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM users')
        user_count = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM users WHERE interaction_count > 50')
        vip_users = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM conversations WHERE timestamp > ?',
                  ((datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),))
        message_count = c.fetchone()[0]
        c.execute('SELECT COUNT(DISTINCT user_id) FROM conversations WHERE timestamp > ?',
                  ((datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),))
        active_users = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM unanswered_questions')
        unanswered_count = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM conversations WHERE response != ""')
        answered_count = c.fetchone()[0]
        total_questions = answered_count + unanswered_count
        response_rate = (answered_count / total_questions * 100) if total_questions > 0 else 0
        c.execute('SELECT question_text, frequency FROM frequent_questions ORDER BY frequency DESC LIMIT 3')
        frequent = c.fetchall()
    dashboard = (
        "<b>📊 داشبورد ادمین</b>\n"
        "<pre>| 👥 کاربران کل | 🟢 فعال (24h) | 💎 VIP | 💬 پیام‌ها (24h) | 📈 نرخ پاسخ |\n"
        f"| {user_count:>12} | {active_users:>12} | {vip_users:>4} | {message_count:>14} | {response_rate:>6.1f}% |</pre>\n"
        "<b>سوالات پرتکرار:</b>\n" + "\n".join(f"- {q[0]} ({q[1]} بار)" for q in frequent)
    )
    return dashboard

# منوی اصلی
def get_main_menu():
    api_status = "🟢" if get_setting('api_enabled', '1') == '1' else "🔴"
    return [
        [Button.inline("📝 مدیریت پاسخ‌ها", b"manage_responses"), Button.inline("📊 داشبورد", b"dashboard")],
        [Button.inline("👤 مدیریت کاربران", b"manage_users"), Button.inline(f"{api_status} API", b"toggle_api")],
        [Button.inline("⚙️ تنظیمات", b"settings"), Button.inline("📜 لاگ‌ها", b"logs")]
    ]

# هندلر پیام‌ها
@client.on(events.NewMessage)
async def handle_message(event):
    if event.sender_id not in ADMIN_IDS:
        return
    try:
        message = event.message.text.lower() if event.message.text else ""
        buttons = get_main_menu()

        if client.state == "setting_responses":
            lines = message.split('\n')
            for line in lines:
                if ":" in line:
                    try:
                        keyword, response = line.split(":", 1)
                        keyword, response = keyword.strip(), response.strip()
                        save_response(keyword, response)
                    except:
                        continue
            await event.reply("✅ پاسخ‌ها ست شدند! به منوی اصلی برگردید.", buttons=buttons)
            client.state = None
            return
        elif client.state == "edit_response":
            if ":" in message:
                try:
                    keyword, response = message.split(":", 1)
                    keyword, response = keyword.strip(), response.strip()
                    save_response(keyword, response)
                    await event.reply(f"✅ پاسخ '{keyword}' ویرایش شد: {response}", buttons=buttons)
                    client.state = None
                except:
                    await event.reply("❌ فرمت: keyword:response", buttons=buttons)
            return
        elif client.state == "delete_response":
            delete_response(message)
            await event.reply(f"✅ پاسخ '{message}' حذف شد.", buttons=buttons)
            client.state = None
            return
        elif client.state == "search_user":
            with sqlite3.connect('users.db') as conn:
                c = conn.cursor()
                c.execute('SELECT user_id, name, interaction_count, is_banned FROM users WHERE name LIKE ? OR user_id = ?',
                          (f'%{message}%', message))
                users = c.fetchall()
            if users:
                response = "\n".join(f"ID: {u[0]}, نام: {u[1]}, تعاملات: {u[2]}, وضعیت: {'بلاک' if u[3] else 'فعال'}" for u in users)
                await event.reply(f"کاربران یافت‌شده:\n{response}", buttons=buttons)
            else:
                await event.reply("کاربری یافت نشد!", buttons=buttons)
            client.state = None
            return
        elif client.state == "manage_user":
            try:
                user_id, action = message.split()
                user_id = int(user_id)
                with sqlite3.connect('users.db') as conn:
                    c = conn.cursor()
                    if action == "ban":
                        c.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
                        await event.reply(f"✅ کاربر {user_id} بلاک شد.", buttons=buttons)
                    elif action == "unban":
                        c.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
                        await event.reply(f"✅ کاربر {user_id} آنبلاک شد.", buttons=buttons)
                    elif action == "info":
                        c.execute('SELECT name, interaction_count, is_banned FROM users WHERE user_id = ?', (user_id,))
                        user_data = c.fetchone()
                        if user_data:
                            await event.reply(
                                f"👤 اطلاعات کاربر {user_id}\n"
                                f"- نام: {user_data[0]}\n"
                                f"- تعاملات: {user_data[1]}\n"
                                f"- وضعیت: {'بلاک' if user_data[2] else 'فعال'}",
                                buttons=buttons
                            )
                        else:
                            await event.reply("❌ کاربر یافت نشد!", buttons=buttons)
                    conn.commit()
            except:
                await event.reply("❌ فرمت: <user_id> <ban|unban|info>", buttons=buttons)
            client.state = None
            return
        elif client.state == "search_response":
            with sqlite3.connect('users.db') as conn:
                c = conn.cursor()
                c.execute('SELECT keyword, response, category FROM responses WHERE keyword LIKE ?', (f'%{message}%',))
                responses = c.fetchall()
            if responses:
                response_text = "\n".join(f"- کلمه: {r[0]}\n  پاسخ: {r[1]}\n  دسته: {r[2]}" for r in responses)
                await event.reply(f"پاسخ‌های یافت‌شده:\n{response_text}", buttons=buttons)
            else:
                await event.reply("پاسخی یافت نشد!", buttons=buttons)
            client.state = None
            return
        elif client.state == "delete_question":
            try:
                question_id = int(message)
                with sqlite3.connect('users.db') as conn:
                    c = conn.cursor()
                    c.execute('DELETE FROM unanswered_questions WHERE id = ?', (question_id,))
                    conn.commit()
                await event.reply(f"✅ سوال با ID {question_id} حذف شد.", buttons=buttons)
            except:
                await event.reply("❌ لطفاً ID سوال را وارد کنید.", buttons=buttons)
            client.state = None
            return
        elif client.state == "delete_frequent":
            try:
                question_hash = message
                with sqlite3.connect('users.db') as conn:
                    c = conn.cursor()
                    c.execute('DELETE FROM frequent_questions WHERE question_hash = ?', (question_hash,))
                    conn.commit()
                await event.reply(f"✅ سوال پرتکرار حذف شد.", buttons=buttons)
            except:
                await event.reply("❌ لطفاً hash سوال را وارد کنید.", buttons=buttons)
            client.state = None
            return
        else:
            dashboard = await generate_dashboard()
            await event.reply(f"{dashboard}\n<b>🎮 از دکمه‌ها استفاده کنید!</b>", parse_mode='html', buttons=buttons)
            return

    except Exception as e:
        logger.error(f"خطا در admin_bot: {e}")
        await event.reply(f"⚠️ خطا: {e}", buttons=buttons)

# هندلر دکمه‌ها
@client.on(events.CallbackQuery)
async def handle_callback(event):
    if event.sender_id not in ADMIN_IDS:
        return
    data = event.data.decode()
    buttons = get_main_menu()
    try:
        if data == "dashboard":
            dashboard = await generate_dashboard()
            await event.reply(dashboard, parse_mode='html', buttons=buttons)
        elif data == "manage_responses":
            await event.reply(
                "📝 مدیریت پاسخ‌ها\nانتخاب کنید:",
                buttons=[
                    [Button.inline("➕ افزودن پاسخ", b"add_response"), Button.inline("🔍 جستجوی پاسخ", b"search_response")],
                    [Button.inline("✏️ ویرایش پاسخ", b"edit_response"), Button.inline("🗑️ حذف پاسخ", b"delete_response")],
                    [Button.inline("🔙 بازگشت", b"back")]
                ]
            )
        elif data == "add_response":
            await event.reply("پاسخ‌ها را خطی وارد کن (keyword:response):\nمثال:\nسیگنال:سیگنال‌ها تو کانال!", buttons=buttons)
            client.state = "setting_responses"
        elif data == "search_response":
            await event.reply("کلمه کلیدی را وارد کنید:", buttons=buttons)
            client.state = "search_response"
        elif data == "edit_response":
            await event.reply("کلمه کلیدی و پاسخ جدید را وارد کنید (keyword:response):", buttons=buttons)
            client.state = "edit_response"
        elif data == "delete_response":
            await event.reply("کلمه کلیدی پاسخ را وارد کنید:", buttons=buttons)
            client.state = "delete_response"
        elif data == "manage_users":
            await event.reply(
                "👤 مدیریت کاربران\nانتخاب کنید:",
                buttons=[
                    [Button.inline("🔍 جستجوی کاربر", b"search_user"), Button.inline("🚫 بلاک/آنبلاک", b"manage_user")],
                    [Button.inline("ℹ️ اطلاعات کاربر", b"user_info"), Button.inline("🔙 بازگشت", b"back")]
                ]
            )
        elif data == "search_user":
            await event.reply("نام یا ID کاربر را وارد کنید:", buttons=buttons)
            client.state = "search_user"
        elif data == "manage_user" or data == "user_info":
            await event.reply("ID کاربر و عملیات را وارد کنید (مثال: 12345 ban):", buttons=buttons)
            client.state = "manage_user"
        elif data == "toggle_api":
            current_status = get_setting('api_enabled', '1')
            new_status = '0' if current_status == '1' else '1'
            save_setting('api_enabled', new_status)
            await event.reply(f"API {'فعال' if new_status == '1' else 'غیرفعال'} شد.", buttons=get_main_menu())
        elif data == "settings":
            with sqlite3.connect('users.db') as conn:
                c = conn.cursor()
                c.execute('SELECT type, enabled FROM admin_notifications')
                notifications = c.fetchall()
            notif_status = "\n".join(f"- {n[0]}: {'🟢 فعال' if n[1] else '🔴 غیرفعال'}" for n in notifications)
            await event.reply(
                f"⚙️ تنظیمات\nوضعیت نوتیفیکیشن‌ها:\n{notif_status}\nانتخاب کنید:",
                buttons=[
                    [Button.inline("🔊 نوتیفیکیشن‌ها", b"toggle_notification"), Button.inline("📥 دانلود دیتابیس", b"download_db")],
                    [Button.inline("🗑️ پاکسازی سوالات", b"clear_questions"), Button.inline("🔙 بازگشت", b"back")]
                ]
            )
        elif data == "toggle_notification":
            await event.reply(
                "🔊 مدیریت نوتیفیکیشن‌ها\nنوع نوتیفیکیشن را انتخاب کنید:",
                buttons=[
                    [Button.inline("❓ Unanswered", b"toggle_unanswered"), Button.inline("📋 Frequent", b"toggle_frequent")],
                    [Button.inline("🚨 Warning", b"toggle_warning"), Button.inline("🔙 بازگشت", b"back")]
                ]
            )
        elif data in ["toggle_unanswered", "toggle_frequent", "toggle_warning"]:
            notif_type = data.split("_")[1]
            with sqlite3.connect('users.db') as conn:
                c = conn.cursor()
                c.execute('SELECT enabled FROM admin_notifications WHERE type = ?', (notif_type,))
                current = c.fetchone()
                enabled = 0 if current and current[0] else 1
                c.execute('INSERT OR REPLACE INTO admin_notifications (type, enabled) VALUES (?, ?)', (notif_type, enabled))
                conn.commit()
            await event.reply(f"نوتیفیکیشن {notif_type} {'فعال' if enabled else 'غیرفعال'} شد.", buttons=buttons)
        elif data == "download_db":
            if os.path.exists('users.db'):
                await client.send_file(event.sender_id, 'users.db', caption="📥 دیتابیس کاربران")
                await event.reply("✅ دیتابیس ارسال شد!", buttons=buttons)
            else:
                await event.reply("❌ دیتابیس یافت نشد!", buttons=buttons)
        elif data == "clear_questions":
            await event.reply(
                "🗑️ پاکسازی سوالات\nانتخاب کنید:",
                buttons=[
                    [Button.inline("❓ سوالات متفرقه", b"delete_unanswered"), Button.inline("📋 سوالات پرتکرار", b"delete_frequent")],
                    [Button.inline("🔙 بازگشت", b"back")]
                ]
            )
        elif data == "delete_unanswered":
            await event.reply("ID سوال متفرقه را وارد کنید:", buttons=buttons)
            client.state = "delete_question"
        elif data == "delete_frequent":
            await event.reply("Hash سوال پرتکرار را وارد کنید:", buttons=buttons)
            client.state = "delete_frequent"
        elif data == "logs":
            log_files = ['admin_bot.log', 'bot.log']
            logs = ""
            for log_file in log_files:
                if os.path.exists(log_file):
                    with open(log_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()[-10:]
                        logs += f"\n📜 {log_file}:\n" + "".join(lines)
            if logs:
                await event.reply(f"📜 آخرین لاگ‌ها:\n{logs}", buttons=buttons)
            else:
                await event.reply("❌ لاگی یافت نشد!", buttons=buttons)
        elif data == "back":
            dashboard = await generate_dashboard()
            await event.reply(f"{dashboard}\n<b>🎮 از دکمه‌ها استفاده کنید!</b>", parse_mode='html', buttons=buttons)
    except Exception as e:
        logger.error(f"خطا در handle_callback: {e}")
        await event.reply(f"⚠️ خطا: {e}", buttons=buttons)

# پشتیبان‌گیری دیتابیس
async def backup_database():
    while True:
        try:
            shutil.copy('users.db', f'backup_users_{datetime.now().strftime("%Y%m%d")}.db')
            logger.info("پشتیبان‌گیری دیتابیس انجام شد.")
            try:
                await client.send_message(ADMIN_IDS[0], "✅ پشتیبان‌گیری دیتابیس انجام شد.")
            except Exception as e:
                logger.error(f"خطا در ارسال نوتیفیکیشن به ادمین اصلی {ADMIN_IDS[0]}: {e}")
        except Exception as e:
            logger.error(f"خطا در پشتیبان‌گیری: {e}")
            try:
                await client.send_message(ADMIN_IDS[0], f"⚠️ خطا در پشتیبان‌گیری دیتابیس: {e}")
            except Exception as e:
                logger.error(f"خطا در ارسال نوتیفیکیشن به ادمین اصلی {ADMIN_IDS[0]}: {e}")
        await asyncio.sleep(24 * 3600)

# راه‌اندازی ربات
async def main():
    init_db()
    await client.start(bot_token=ADMIN_BOT_TOKEN)
    logger.info("ربات ادمین شروع شد!")
    asyncio.create_task(backup_database())  # هر وظیفه‌ای که باید انجام بشه
    await client.run_until_disconnected()