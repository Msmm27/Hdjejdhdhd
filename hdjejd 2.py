import os
import sys
import subprocess
import json
import re
import time
import asyncio
import shutil
import random
from datetime import datetime, timedelta
from pathlib import Path

# ==================== АВТОУСТАНОВКА ====================
try:
    import telethon
    from telethon import TelegramClient, functions
    from telethon.errors import PhoneCodeInvalidError, PhoneCodeExpiredError, PhoneNumberInvalidError, FloodWaitError
    import socks
except ImportError:
    print("📦 Устанавливаю Telethon и PySocks...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "telethon", "PySocks"])
    print("✅ Telethon и PySocks установлены!")
    import telethon
    from telethon import TelegramClient, functions
    from telethon.errors import PhoneCodeInvalidError, PhoneCodeExpiredError, PhoneNumberInvalidError, FloodWaitError
    import socks

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
except ImportError:
    print("📦 Устанавливаю python-telegram-bot...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot"])
    print("✅ python-telegram-bot установлен!")
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

try:
    from dotenv import load_dotenv
except ImportError:
    print("📦 Устанавливаю python-dotenv...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dotenv"])
    print("✅ python-dotenv установлен!")
    from dotenv import load_dotenv

# ==================== ЗАГРУЗКА .ENV ====================
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PASSWORD = os.getenv("PASSWORD")

SESSIONS_DIR = "sessions"
TEMP_DIR = "temp"
MONITOR_FILE = "monitors.json"
PROXY_FILE = "proxies.txt"
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

bot_app = Application.builder().token(BOT_TOKEN).build()
user_data = {}
auth_data = {}
monitor_tasks = {}

# ==================== ОЧЕРЕДИ ====================
tida_queue = {"active": False, "end_time": None, "owner": None}
au_queue = {"active": False, "end_time": None, "owner": None}

# ==================== КД ====================
user_cooldown = {
    "geo": {},
    "oper": {},
    "channel": {},
    "bot": {},
    "mega": {}
}

def check_cooldown(user_id: int, cooldown_type: str, seconds: int) -> tuple:
    if user_id == ADMIN_ID:
        return True, 0
    last = user_cooldown[cooldown_type].get(user_id)
    if not last:
        return True, 0
    diff = (datetime.now() - last).total_seconds()
    if diff >= seconds:
        return True, 0
    return False, int(seconds - diff)

# ==================== ОЧЕРЕДЬ ====================

def is_queue_busy(queue_name: str) -> tuple:
    queue = tida_queue if queue_name == "tida" else au_queue
    if not queue["active"]:
        return False, 0
    if queue["end_time"] is None:
        queue["active"] = False
        return False, 0
    now = datetime.now()
    if now >= queue["end_time"]:
        queue["active"] = False
        queue["end_time"] = None
        queue["owner"] = None
        return False, 0
    remaining = int((queue["end_time"] - now).total_seconds())
    return True, remaining

def set_queue_busy(queue_name: str, seconds: int = 300, owner_id: int = None):
    queue = tida_queue if queue_name == "tida" else au_queue
    if seconds <= 0:
        queue["active"] = False
        queue["end_time"] = None
        queue["owner"] = None
        return
    queue["active"] = True
    queue["end_time"] = datetime.now() + timedelta(seconds=seconds)
    queue["owner"] = owner_id

def get_queue_owner(queue_name: str):
    queue = tida_queue if queue_name == "tida" else au_queue
    return queue.get("owner")

# ==================== ПРОКСИ ====================

def load_proxies():
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    return []

def save_proxies(proxies):
    with open(PROXY_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(proxies))

def parse_proxy(proxy_str: str):
    try:
        parts = proxy_str.split(':')
        if len(parts) != 4:
            return None
        ip = parts[0].strip()
        port = int(parts[1].strip())
        login = parts[2].strip()
        password = parts[3].strip()
        if not ip or not login or not password:
            return None
        return socks.SOCKS5, ip, port, True, login, password
    except (ValueError, IndexError):
        return None

# ==================== МОНИТОРИНГ ФАЙЛ ====================

def load_monitors():
    if os.path.exists(MONITOR_FILE):
        with open(MONITOR_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"monitors": []}

def save_monitors(data):
    with open(MONITOR_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def add_monitor(user_id, target, session_name):
    data = load_monitors()
    data["monitors"].append({
        "user_id": user_id,
        "target": target,
        "session_name": session_name,
        "status": "active",
        "last_check": datetime.now().isoformat(),
        "created_at": datetime.now().isoformat()
    })
    save_monitors(data)
    return True

def count_user_monitors(user_id: int) -> int:
    data = load_monitors()
    return len([m for m in data["monitors"] if m["user_id"] == user_id])

def can_add_monitor(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    return count_user_monitors(user_id) < 5

# ==================== ЮЗЕРЫ ====================

def load_users():
    if os.path.exists("users.json"):
        with open("users.json", 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"users": []}

def save_users(data):
    with open("users.json", 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_user(user_id):
    data = load_users()
    for user in data["users"]:
        if user["user_id"] == user_id:
            return user
    return None

def create_user(user_id, username=None):
    data = load_users()
    for user in data["users"]:
        if user["user_id"] == user_id:
            return user
    new_user = {
        "user_id": user_id,
        "username": username or str(user_id),
        "is_admin": user_id == ADMIN_ID,
        "subscription_end": None,
        "sessions": [],
        "reports_sent": 0,
        "created_at": datetime.now().isoformat()
    }
    data["users"].append(new_user)
    save_users(data)
    return new_user

def has_subscription(user_id):
    user = get_user(user_id)
    if not user:
        return False
    if user.get("is_admin"):
        return True
    subscription_end = user.get("subscription_end")
    if not subscription_end:
        return False
    try:
        end_date = datetime.fromisoformat(subscription_end)
        return datetime.now() < end_date
    except:
        return False

def get_subscription_info(user_id):
    user = get_user(user_id)
    if not user:
        return "❌ Не найден"
    if user.get("is_admin"):
        return "👑 Админ (бессрочно)"
    subscription_end = user.get("subscription_end")
    if not subscription_end:
        return "❌ Нет подписки"
    try:
        end_date = datetime.fromisoformat(subscription_end)
        if datetime.now() < end_date:
            days_left = (end_date - datetime.now()).days
            hours_left = (end_date - datetime.now()).seconds // 3600
            return f"✅ {days_left}д {hours_left}ч"
        else:
            return "❌ Истекла"
    except:
        return "❌ Ошибка"

def add_subscription(user_id, days=1):
    data = load_users()
    for user in data["users"]:
        if user["user_id"] == user_id:
            if user.get("subscription_end"):
                try:
                    current_end = datetime.fromisoformat(user["subscription_end"])
                    new_end = max(current_end, datetime.now()) + timedelta(days=days)
                except:
                    new_end = datetime.now() + timedelta(days=days)
            else:
                new_end = datetime.now() + timedelta(days=days)
            user["subscription_end"] = new_end.isoformat()
            save_users(data)
            return True
    return False

def session_name_for_phone(phone):
    phone_clean = re.sub(r'[^0-9]', '', phone)
    return f"snoser_{phone_clean}"

def save_log(message: str):
    with open("logs.txt", 'a', encoding='utf-8') as f:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        f.write(f"[{timestamp}] {message}\n")

# ==================== ГЛОБАЛЬНЫЕ СЕССИИ ====================

def load_global_sessions():
    if os.path.exists("global_sessions.json"):
        with open("global_sessions.json", 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"sessions": []}

def save_global_sessions(data):
    with open("global_sessions.json", 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def add_global_session(session_name, phone, session_type="au"):
    data = load_global_sessions()
    for s in data["sessions"]:
        if s["session_name"] == session_name:
            return False
    data["sessions"].append({
        "session_name": session_name,
        "phone": phone,
        "type": session_type,
        "added_at": datetime.now().isoformat(),
        "reports_sent": 0
    })
    save_global_sessions(data)
    return True

def get_global_sessions(session_type=None):
    data = load_global_sessions()
    if session_type:
        return [s for s in data["sessions"] if s.get("type") == session_type]
    return data["sessions"]

def delete_global_session(session_name):
    data = load_global_sessions()
    data["sessions"] = [s for s in data["sessions"] if s["session_name"] != session_name]
    save_global_sessions(data)
    session_path = os.path.join(SESSIONS_DIR, f"{session_name}.session")
    if os.path.exists(session_path):
        os.remove(session_path)
    return True

# ==================== ПРОВЕРКА СЕССИИ ====================

async def check_session_valid(session_name: str, proxy=None):
    try:
        client = TelegramClient(
            os.path.join(SESSIONS_DIR, session_name),
            API_ID,
            API_HASH
        )
        if proxy:
            client.set_proxy(proxy)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False, None
        me = await client.get_me()
        phone = me.phone
        await client.disconnect()
        return True, phone
    except Exception:
        return False, None

# ==================== ПРОВЕРКА ЦЕЛИ ====================

async def check_target_exists(target: str, session_type: str = None, target_type: str = "channel") -> bool:
    if session_type == "tida":
        sessions = get_global_sessions("tida")
    elif session_type == "au":
        sessions = get_global_sessions("au")
    else:
        sessions = get_global_sessions("tida") + get_global_sessions("au")
    
    if not sessions:
        return False
    
    username = target.replace("https://t.me/", "").replace("t.me/", "").strip()
    username = username.lstrip("@").split("/")[0]
    
    for session in sessions:
        session_name = session["session_name"]
        try:
            client = TelegramClient(
                os.path.join(SESSIONS_DIR, session_name),
                API_ID,
                API_HASH
            )
            await client.connect()
            
            if not await client.is_user_authorized():
                await client.disconnect()
                continue
            
            if target_type == "bot":
                try:
                    found = False
                    try:
                        entity = await client.get_entity(username)
                        if entity:
                            found = True
                    except Exception:
                        pass
                    
                    if not found:
                        try:
                            result = await client(functions.contacts.SearchRequest(q=username, limit=5))
                            for chat in result.chats:
                                if hasattr(chat, 'username') and chat.username:
                                    if chat.username.lower() == username.lower():
                                        found = True
                                        break
                        except Exception:
                            pass
                    
                    if not found:
                        await client.disconnect()
                        continue
                    
                    try:
                        await client.send_message(username, "/start")
                        await asyncio.sleep(3)
                        messages = await client.get_messages(username, limit=1)
                        if messages:
                            await client.disconnect()
                            return True
                        else:
                            await client.disconnect()
                            continue
                    except Exception:
                        await client.disconnect()
                        continue
                        
                except Exception:
                    await client.disconnect()
                    continue
            
            elif target_type == "channel":
                try:
                    found = False
                    try:
                        entity = await client.get_entity(username)
                        if entity:
                            found = True
                    except Exception:
                        pass
                    
                    if not found:
                        try:
                            result = await client(functions.contacts.SearchRequest(q=username, limit=5))
                            for chat in result.chats:
                                if hasattr(chat, 'username') and chat.username:
                                    if chat.username.lower() == username.lower():
                                        found = True
                                        break
                        except Exception:
                            pass
                    
                    if found:
                        await client.disconnect()
                        return True
                    else:
                        await client.disconnect()
                        continue
                except Exception:
                    await client.disconnect()
                    continue
                    
        except Exception:
            continue
    
    return False

# ==================== АВТОМОНИТОРИНГ ====================

async def auto_monitor_target(user_id: int, target: str, session_type: str = None, target_type: str = "channel", duration_hours: int = None):
    if duration_hours is None:
        if user_id == ADMIN_ID and target_type == "bot":
            duration_hours = 48
        else:
            duration_hours = 24
    
    start_time = datetime.now()
    end_time = start_time + timedelta(hours=duration_hours)
    
    target_label = "бота" if target_type == "bot" else "канала"
    duration_label = f"{duration_hours} часов" if duration_hours < 48 else "2 дня"
    
    try:
        await bot_app.bot.send_message(
            chat_id=user_id,
            text=f"🔍 **Мониторинг запущен!**\n\n"
                 f"📎 {target_label.capitalize()}: `{target}`\n"
                 f"⏱️ Длительность: {duration_label}\n"
                 f"📊 Проверка: каждую минуту",
            parse_mode='Markdown'
        )
        save_log(f"🔍 Автомониторинг: {target} ({target_type}, {duration_hours}ч)")
    except Exception:
        pass
    
    while datetime.now() < end_time:
        exists = await check_target_exists(target, session_type, target_type)
        
        if not exists:
            msg = (
                f"🚨 **{'Бот' if target_type == 'bot' else 'Канал'} СНЕСЕН!**\n\n"
                f"📎 `{target}`\n"
                f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            try:
                await bot_app.bot.send_message(chat_id=user_id, text=msg, parse_mode='Markdown')
            except Exception:
                pass
            
            try:
                await bot_app.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode='Markdown')
            except Exception:
                pass
            
            save_log(f"🚨 Снесено: {target} ({target_type})")
            return
        
        await asyncio.sleep(60)
    
    try:
        await bot_app.bot.send_message(
            chat_id=user_id,
            text=f"✅ **Мониторинг завершен**\n\n📎 `{target}`\n📊 Статус: ЦЕЛЬ ЖИВА",
            parse_mode='Markdown'
        )
        save_log(f"✅ Мониторинг завершен: {target} жив")
    except Exception:
        pass

# ==================== ПРОВЕРКА МЁРТВЫХ СЕССИЙ ====================

async def check_sessions_loop():
    while True:
        try:
            await asyncio.sleep(3600)
            dead_sessions = []
            
            for session in get_global_sessions("au"):
                is_valid, _ = await check_session_valid(session["session_name"])
                if not is_valid:
                    dead_sessions.append(f"🔵 AU: {session['phone']}")
            
            for session in get_global_sessions("tida"):
                is_valid, _ = await check_session_valid(session["session_name"])
                if not is_valid:
                    dead_sessions.append(f"🟢 TIDA: {session['phone']}")
            
            if dead_sessions:
                text = "💀 **Мёртвые сессии!**\n\n" + "\n".join(dead_sessions)
                try:
                    await bot_app.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode='Markdown')
                    save_log(f"💀 Мёртвых сессий: {len(dead_sessions)}")
                except Exception:
                    pass
        except Exception as e:
            print(f"Ошибка check_sessions_loop: {e}")
            await asyncio.sleep(3600)

# ==================== ОТПРАВКА AU ====================

async def send_au_report(session_name: str, target: str, report_type: str = "drug") -> bool:
    try:
        client = TelegramClient(
            os.path.join(SESSIONS_DIR, session_name),
            API_ID,
            API_HASH
        )
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.disconnect()
            return False
        
        await client.send_message("AUReportBot", "/start")
        await asyncio.sleep(1.5)
        await client.send_message("AUReportBot", target)
        await asyncio.sleep(2)
        
        if report_type == "child":
            await client.send_message("AUReportBot", "Child sexual exploitation material")
        else:
            await client.send_message("AUReportBot", "Drug-related material")
        await asyncio.sleep(1.5)
        
        await client.send_message("AUReportBot", "Confirm")
        await asyncio.sleep(1)
        
        await client.disconnect()
        return True
    except Exception as e:
        print(f"Ошибка AU: {e}")
        return False

# ==================== ОТПРАВКА TIDA ====================

async def send_tida_reports(target: str, complaint_text: str) -> int:
    tida_sessions = get_global_sessions("tida")
    if not tida_sessions:
        return 0
    
    proxies = load_proxies()
    success_count = 0
    total = len(tida_sessions)
    
    for i, session in enumerate(tida_sessions):
        session_name = session["session_name"]
        proxy_str = proxies[i % len(proxies)] if proxies else None
        proxy = parse_proxy(proxy_str) if proxy_str else None
        
        try:
            client = TelegramClient(
                os.path.join(SESSIONS_DIR, session_name),
                API_ID,
                API_HASH
            )
            if proxy:
                client.set_proxy(proxy)
            await client.connect()
            
            if not await client.is_user_authorized():
                await client.disconnect()
                continue
            
            await client.send_message("TIDABot", "/start")
            await asyncio.sleep(2)
            
            await client.send_message("TIDABot", target)
            await asyncio.sleep(2)
            
            await client.send_message("TIDABot", "Non-consensual intimate image sharing")
            await asyncio.sleep(2)
            
            await client.send_message("TIDABot", complaint_text)
            await asyncio.sleep(2)
            
            await client.send_message("TIDABot", "Proceed without documentation")
            await asyncio.sleep(2)
            
            await client.send_message("TIDABot", "Confirm")
            await asyncio.sleep(1)
            
            await client.disconnect()
            success_count += 1
            save_log(f"✅ TIDA {i+1}/{total}: {target}")
            
        except Exception as e:
            print(f"Ошибка TIDA {i+1}: {e}")
            try:
                await client.disconnect()
            except:
                pass
        
        if i < total - 1:
            await asyncio.sleep(120)
    
    return success_count

# ==================== КЛАВИАТУРЫ ====================

def get_main_keyboard(user_id=None):
    keyboard = [
        [InlineKeyboardButton("👊 Шакал оперов", callback_data="attack_oper")],
        [InlineKeyboardButton("📢 Шакал каналов", callback_data="attack_channel")],
        [InlineKeyboardButton("🤖 Шакал ботов", callback_data="attack_bot")],
        [InlineKeyboardButton("🌍 Гео бан ботов", callback_data="attack_geo_bot")],
        [InlineKeyboardButton("🐕 Шакал мега", callback_data="attack_mega")],
        [InlineKeyboardButton("🔍 Мониторинг", callback_data="add_monitor")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("📊 Статистика", callback_data="stats")])
        keyboard.append([InlineKeyboardButton("👑 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("👤 Выдать подписку", callback_data="admin_give_sub")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("📁 Глобальные сессии", callback_data="admin_global_sessions")],
        [InlineKeyboardButton("📱 Добавить сессию", callback_data="admin_add_session")],
        [InlineKeyboardButton("📡 Загрузить прокси", callback_data="admin_load_proxy")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="main")]
    ])

def get_global_sessions_keyboard(page=0, session_type=None):
    sessions = get_global_sessions(session_type)
    if not sessions:
        return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")]])
    start_idx = page * 5
    end_idx = min(start_idx + 5, len(sessions))
    keyboard = []
    for i in range(start_idx, end_idx):
        s = sessions[i]
        label = "🟢" if s.get("type") == "tida" else "🔵"
        keyboard.append([InlineKeyboardButton(f"{label} {s['phone']}", callback_data=f"global_session_{i}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"global_page_{page-1}"))
    if end_idx < len(sessions):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"global_page_{page+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def get_sub_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 1 день", callback_data=f"sub_1_{user_id}"),
         InlineKeyboardButton("📅 3 дня", callback_data=f"sub_3_{user_id}")],
        [InlineKeyboardButton("📅 7 дней", callback_data=f"sub_7_{user_id}"),
         InlineKeyboardButton("📅 30 дней", callback_data=f"sub_30_{user_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")]
    ])

# ==================== КОМАНДЫ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not context.user_data.get("authenticated"):
        await update.message.reply_text("🔐 **Введите пароль:**", parse_mode='Markdown')
        context.user_data["waiting_password"] = True
        return
    
    create_user(user_id, update.effective_user.username)
    if not has_subscription(user_id) and user_id != ADMIN_ID:
        await update.message.reply_text("🔐 Нет подписки!")
        return
    await update.message.reply_text(
        f"🤖 **Главное меню**\n\n👤 {get_subscription_info(user_id)}",
        reply_markup=get_main_keyboard(user_id),
        parse_mode='Markdown'
    )

async def check_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if text == PASSWORD:
        context.user_data["authenticated"] = True
        context.user_data["waiting_password"] = False
        
        create_user(user_id, update.effective_user.username)
        if not has_subscription(user_id) and user_id != ADMIN_ID:
            await update.message.reply_text("🔐 Нет подписки!")
            return
        
        await update.message.reply_text(
            f"✅ **Доступ разрешен!**\n\n👤 {get_subscription_info(user_id)}",
            reply_markup=get_main_keyboard(user_id),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ **Неверный пароль!**\nПопробуйте еще раз:")

# ==================== ОБРАБОТЧИК КНОПОК ====================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("authenticated"):
        await update.callback_query.answer("❌ Сначала введите пароль: /start", show_alert=True)
        return
    
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    if data == "main":
        await query.edit_message_text(
            "🤖 **Главное меню**",
            reply_markup=get_main_keyboard(user_id),
            parse_mode='Markdown'
        )
        return

    # ===== ПРОКСИ =====
    if data == "admin_load_proxy":
        if user_id != ADMIN_ID:
            return
        await query.edit_message_text(
            "📡 **Загрузка прокси**\n\nОтправь .txt или список:\n`ip:port:login:password`",
            parse_mode='Markdown'
        )
        user_data[user_id] = {"step": "admin_waiting_proxy"}
        return

    # ===== СЕССИИ =====
    if data == "admin_add_session":
        if user_id != ADMIN_ID:
            return
        await query.edit_message_text(
            "📱 **Добавление сессии**\n\nВыбери тип:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔵 AUReport", callback_data="admin_global_au")],
                [InlineKeyboardButton("🟢 TIDA", callback_data="admin_global_tida")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")]
            ]),
            parse_mode='Markdown'
        )
        return

    if data == "admin_global_au":
        if user_id != ADMIN_ID:
            return
        user_data[user_id] = {"step": "admin_waiting_phone", "session_subtype": "au"}
        await query.edit_message_text(
            "🔵 Отправь номер: `+79991234567`\nИли `.session` файл",
            parse_mode='Markdown'
        )
        return

    if data == "admin_global_tida":
        if user_id != ADMIN_ID:
            return
        user_data[user_id] = {"step": "admin_waiting_phone", "session_subtype": "tida"}
        await query.edit_message_text(
            "🟢 Отправь номер: `+79991234567`\nИли `.session` файл",
            parse_mode='Markdown'
        )
        return

    # ===== АДМИН =====
    if data == "admin_panel":
        if user_id != ADMIN_ID:
            return
        await query.edit_message_text("👑 **Админ-панель**", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
        return
    
    if data == "admin_users":
        if user_id != ADMIN_ID:
            return
        users = load_users()
        text = "📋 **Пользователи:**\n\n"
        for u in users["users"]:
            text += f"👤 {u['username']} ({u['user_id']})\n   {get_subscription_info(u['user_id'])}\n   Жалоб: {u.get('reports_sent', 0)}\n\n"
        await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")]]), parse_mode='Markdown')
        return
    
    if data == "admin_give_sub":
        if user_id != ADMIN_ID:
            return
        await query.edit_message_text(
            "👤 Отправь ID пользователя:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")]]),
            parse_mode='Markdown'
        )
        user_data[user_id] = {"step": "waiting_sub"}
        return
    
    if data.startswith("sub_"):
        if user_id != ADMIN_ID:
            return
        parts = data.split("_")
        days = int(parts[1])
        target_user = int(parts[2])
        if add_subscription(target_user, days):
            await query.edit_message_text(f"✅ Подписка на {days} дней!", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Не найден!", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
        return
    
    if data == "admin_stats":
        if user_id != ADMIN_ID:
            return
        users = load_users()
        await query.edit_message_text(
            f"📊 **Статистика**\n\n"
            f"👥 Юзеров: {len(users['users'])}\n"
            f"🌐 AU: {len(get_global_sessions('au'))}\n"
            f"🟢 TIDA: {len(get_global_sessions('tida'))}\n"
            f"📡 Прокси: {len(load_proxies())}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")]]),
            parse_mode='Markdown'
        )
        return
    
    if data == "admin_global_sessions":
        if user_id != ADMIN_ID:
            return
        await query.edit_message_text(
            "📋 **Сессии**",
            reply_markup=get_global_sessions_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    if data.startswith("global_page_"):
        if user_id != ADMIN_ID:
            return
        page = int(data.split("_")[2])
        await query.edit_message_text("📋 **Сессии**", reply_markup=get_global_sessions_keyboard(page), parse_mode='Markdown')
        return
    
    if data.startswith("global_session_"):
        if user_id != ADMIN_ID:
            return
        idx = int(data.split("_")[2])
        sessions = get_global_sessions()
        if idx >= len(sessions):
            return
        s = sessions[idx]
        label = "TIDA" if s.get("type") == "tida" else "AUReport"
        await query.edit_message_text(
            f"🌐 **Сессия**\n\n📞 {s['phone']}\n🔹 {label}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_global_{s['session_name']}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="admin_global_sessions")]
            ]),
            parse_mode='Markdown'
        )
        return
    
    if data.startswith("delete_global_"):
        if user_id != ADMIN_ID:
            return
        session_name = data.replace("delete_global_", "")
        delete_global_session(session_name)
        await query.edit_message_text("✅ Удалено!", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
        return
    
    # ===== АТАКИ =====
    if data == "attack_oper":
        can, remaining = check_cooldown(user_id, "oper", 5 * 3600)
        if not can:
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await query.edit_message_text(
                f"⏳ **КД 5 часов!**\n\nОсталось: {hours} ч {minutes} мин",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        is_busy, busy_remaining = is_queue_busy("au")
        if is_busy and get_queue_owner("au") != user_id:
            mins = busy_remaining // 60
            secs = busy_remaining % 60
            await query.edit_message_text(
                f"⏳ **Отправка уже идёт!**\n\nПопробуйте через: **{mins} мин {secs} сек**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        user_cooldown["oper"][user_id] = datetime.now()
        await query.edit_message_text("👊 **Шакал оперов**\n\nОтправь ссылку:\n`t.me/username`", parse_mode='Markdown')
        user_data[user_id] = {"step": "waiting_oper_link"}
        return
    
    if data == "attack_channel":
        can, remaining = check_cooldown(user_id, "channel", 3600)
        if not can:
            minutes = remaining // 60
            await query.edit_message_text(
                f"⏳ **КД 1 час!**\n\nОсталось: {minutes} мин",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        is_busy, busy_remaining = is_queue_busy("au")
        if is_busy and get_queue_owner("au") != user_id:
            mins = busy_remaining // 60
            secs = busy_remaining % 60
            await query.edit_message_text(
                f"⏳ **Отправка уже идёт!**\n\nПопробуйте через: **{mins} мин {secs} сек**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        user_cooldown["channel"][user_id] = datetime.now()
        await query.edit_message_text("📢 **Шакал каналов**\n\nОтправь ссылку:\n`t.me/channelname`", parse_mode='Markdown')
        user_data[user_id] = {"step": "waiting_channel_link"}
        return
    
    if data == "attack_bot":
        can, remaining = check_cooldown(user_id, "bot", 3600)
        if not can:
            minutes = remaining // 60
            await query.edit_message_text(
                f"⏳ **КД 1 час!**\n\nОсталось: {minutes} мин",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        is_busy, busy_remaining = is_queue_busy("au")
        if is_busy and get_queue_owner("au") != user_id:
            mins = busy_remaining // 60
            secs = busy_remaining % 60
            await query.edit_message_text(
                f"⏳ **Отправка уже идёт!**\n\nПопробуйте через: **{mins} мин {secs} сек**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        user_cooldown["bot"][user_id] = datetime.now()
        await query.edit_message_text("🤖 **Шакал ботов**\n\nОтправь ссылку:\n`t.me/botname`", parse_mode='Markdown')
        user_data[user_id] = {"step": "waiting_bot_link"}
        return
    
    if data == "attack_geo_bot":
        can, remaining = check_cooldown(user_id, "geo", 24 * 3600)
        if not can:
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await query.edit_message_text(
                f"⏳ **КД 24 часа!**\n\nОсталось: {hours} ч {minutes} мин",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        is_busy, busy_remaining = is_queue_busy("au")
        if is_busy and get_queue_owner("au") != user_id:
            mins = busy_remaining // 60
            secs = busy_remaining % 60
            await query.edit_message_text(
                f"⏳ **Отправка уже идёт!**\n\nПопробуйте через: **{mins} мин {secs} сек**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        user_cooldown["geo"][user_id] = datetime.now()
        await query.edit_message_text("🌍 **Гео бан**\n\nОтправь ссылку:\n`t.me/botname`", parse_mode='Markdown')
        user_data[user_id] = {"step": "waiting_geo_bot_link"}
        return
    
    if data == "attack_mega":
        can, remaining = check_cooldown(user_id, "mega", 3 * 3600)
        if not can:
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await query.edit_message_text(
                f"⏳ **КД 3 часа!**\n\nОсталось: {hours} ч {minutes} мин",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        is_busy, busy_remaining = is_queue_busy("tida")
        if is_busy and get_queue_owner("tida") != user_id:
            mins = busy_remaining // 60
            secs = busy_remaining % 60
            await query.edit_message_text(
                f"⏳ **TIDA занята!**\n\nПопробуйте через: **{mins} мин {secs} сек**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        user_cooldown["mega"][user_id] = datetime.now()
        await query.edit_message_text(
            "🐕 **Шакал мега**\n\n"
            "Отправь ссылку:\n`t.me/botname`\n\n"
            "Затем введи текст жалобы на английском",
            parse_mode='Markdown'
        )
        user_data[user_id] = {"step": "waiting_mega_link"}
        return
    
    # ===== TIDA ПОДТВЕРЖДЕНИЕ =====
    if data == "mega_confirm":
        target = user_data.get(user_id, {}).get("target")
        mega_text = user_data.get(user_id, {}).get("mega_text")
        
        if not target or not mega_text:
            await query.edit_message_text("❌ Данные не найдены!")
            return
        
        # Проверка очереди
        is_busy, busy_remaining = is_queue_busy("tida")
        if is_busy and get_queue_owner("tida") != user_id:
            mins = busy_remaining // 60
            secs = busy_remaining % 60
            await query.edit_message_text(
                f"⏳ **TIDA занята!**\n\nПопробуйте через: {mins} мин {secs} сек",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        set_queue_busy("tida", 300, user_id)
        user_data[user_id]["step"] = None
        
        await query.edit_message_text(
            f"🚀 **Отправка через TIDA...**\n\n📎 `{target}`",
            parse_mode='Markdown'
        )
        
        success = await send_tida_reports(target, mega_text)
        
        # Освобождаем очередь
        set_queue_busy("tida", 0, None)
        
        try:
            await bot_app.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📨 **TIDA!**\n👤 {user_id}\n📎 `{target}`\n✅ {success}",
                parse_mode='Markdown'
            )
        except Exception:
            pass
        
        await query.edit_message_text(
            f"✅ **Готово!**\n\n📎 `{target}`\n✅ Успешно: {success}\n\n🔍 Мониторинг на 24ч!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Главное меню", callback_data="main")]]),
            parse_mode='Markdown'
        )
        save_log(f"✅ TIDA: {user_id} → {target} ({success})")
        
        asyncio.create_task(auto_monitor_target(user_id, target, "tida", "bot", 48 if user_id == ADMIN_ID else 24))
        return

    if data == "mega_cancel":
        # Освобождаем очередь
        set_queue_busy("tida", 0, None)
        user_data[user_id]["step"] = None
        user_data[user_id].pop("target", None)
        user_data[user_id].pop("mega_text", None)
        
        await query.edit_message_text(
            "❌ **Отправка отменена!**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="main")]]),
            parse_mode='Markdown'
        )
        save_log(f"❌ TIDA отменена: {user_id}")
        return
    
    # ===== МОНИТОРИНГ =====
    if data == "add_monitor":
        if not can_add_monitor(user_id):
            count = count_user_monitors(user_id)
            await query.edit_message_text(
                f"❌ **Лимит мониторинга!**\n\nУ вас уже {count}/5",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        tida_sessions = get_global_sessions("tida")
        au_sessions = get_global_sessions("au")
        
        if not tida_sessions and not au_sessions:
            await query.edit_message_text(
                "❌ **Нет сессий!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]),
                parse_mode='Markdown'
            )
            return
        
        if user_id == ADMIN_ID:
            bot_desc = "🤖 Бот (2 дня)"
        else:
            bot_desc = "🤖 Бот (24ч)"
        
        await query.edit_message_text(
            "🔍 **Что мониторить?**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(bot_desc, callback_data="monitor_bot")],
                [InlineKeyboardButton("📢 Канал (24ч)", callback_data="monitor_channel")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="main")]
            ]),
            parse_mode='Markdown'
        )
        return

    if data == "monitor_bot":
        if user_id == ADMIN_ID:
            text = "🤖 **Мониторинг бота**\n\nОтправь:\n`t.me/botname`\n\n⏱️ 2 дня"
        else:
            text = "🤖 **Мониторинг бота**\n\nОтправь:\n`t.me/botname`\n\n⏱️ 24 часа"
        await query.edit_message_text(text, parse_mode='Markdown')
        user_data[user_id] = {"step": "waiting_monitor_link", "target_type": "bot"}
        return

    if data == "monitor_channel":
        await query.edit_message_text(
            "📢 **Мониторинг канала**\n\nОтправь:\n`t.me/channelname`\n\n⚠️ Только публичные\n⏱️ 24 часа",
            parse_mode='Markdown'
        )
        user_data[user_id] = {"step": "waiting_monitor_link", "target_type": "channel"}
        return
    
    if data == "stats":
        if user_id != ADMIN_ID:
            return
        users = load_users()
        text = "📊 **Статистика**\n\n"
        text += f"👥 Юзеров: {len(users['users'])}\n"
        text += f"🌐 AU: {len(get_global_sessions('au'))}\n"
        text += f"🟢 TIDA: {len(get_global_sessions('tida'))}\n"
        text += f"📡 Прокси: {len(load_proxies())}\n"
        text += f"📤 Жалоб: {sum(u.get('reports_sent', 0) for u in users['users'])}"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="main")]]), parse_mode='Markdown')
        return

# ==================== ОБРАБОТКА ТЕКСТА ====================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if context.user_data.get("waiting_password"):
        await check_password(update, context)
        return
    
    if not context.user_data.get("authenticated"):
        await update.message.reply_text("❌ Сначала /start")
        return
    
    # ===== ПРОКСИ =====
    if user_data.get(user_id, {}).get("step") == "admin_waiting_proxy":
        if user_id != ADMIN_ID:
            return
        proxies = [line.strip() for line in text.split('\n') if line.strip()]
        valid = []
        for p in proxies:
            parts = p.split(':')
            if len(parts) == 4:
                try:
                    int(parts[1])
                    valid.append(p)
                except ValueError:
                    continue
        if valid:
            save_proxies(valid)
            await update.message.reply_text(f"✅ Загружено {len(valid)} прокси!")
            save_log(f"📡 Прокси: {len(valid)}")
        else:
            await update.message.reply_text("❌ Неверный формат!")
        user_data[user_id]["step"] = None
        return
    
    # ===== СЕССИЯ ПО НОМЕРУ =====
    if user_data.get(user_id, {}).get("step") == "admin_waiting_phone":
        if user_id != ADMIN_ID:
            return
        if re.match(r'^\+\d{10,15}$', text):
            try:
                client = TelegramClient(
                    os.path.join(SESSIONS_DIR, f"admin_temp_{int(time.time())}"),
                    API_ID,
                    API_HASH
                )
                await client.connect()
                sent_code = await client.send_code_request(text)
                user_data[user_id]["admin_phone"] = text
                user_data[user_id]["admin_client"] = client
                user_data[user_id]["admin_phone_code_hash"] = sent_code.phone_code_hash
                user_data[user_id]["step"] = "admin_waiting_code"
                await update.message.reply_text(f"✅ Код отправлен на {text}")
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {e}")
                user_data[user_id]["step"] = None
            return
        else:
            await update.message.reply_text("❌ Формат: +79991234567")
            return
    
    if user_data.get(user_id, {}).get("step") == "admin_waiting_code":
        if user_id != ADMIN_ID:
            return
        if not text or not text.isdigit():
            await update.message.reply_text("❌ Код цифрами!")
            return
        
        session_subtype = user_data[user_id].get("session_subtype", "au")
        client = user_data[user_id].get("admin_client")
        phone = user_data[user_id].get("admin_phone")
        phone_code_hash = user_data[user_id].get("admin_phone_code_hash")
        
        if not client:
            await update.message.reply_text("❌ Сессия потеряна!")
            user_data[user_id]["step"] = None
            return
        
        try:
            await client.sign_in(phone, code=text, phone_code_hash=phone_code_hash)
            await client.disconnect()
            
            session_name = os.path.basename(client.session.filename).replace('.session', '')
            new_session_name = session_name_for_phone(phone)
            
            old_path = os.path.join(SESSIONS_DIR, f"{session_name}.session")
            new_path = os.path.join(SESSIONS_DIR, f"{new_session_name}.session")
            if os.path.exists(old_path):
                shutil.move(old_path, new_path)
            
            if add_global_session(new_session_name, phone, session_subtype):
                label = "TIDA" if session_subtype == "tida" else "AUReport"
                await update.message.reply_text(f"✅ Сессия добавлена!\n📞 {phone}\n🔹 {label}")
            else:
                await update.message.reply_text(f"⚠️ Уже существует!")
            user_data[user_id]["step"] = None
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")
            user_data[user_id]["step"] = None
        return
    
    # ===== ПОДПИСКА =====
    if user_data.get(user_id, {}).get("step") == "waiting_sub":
        if user_id != ADMIN_ID:
            return
        try:
            target_id = int(text)
            user = get_user(target_id)
            if user:
                await update.message.reply_text(
                    f"👤 {user['username']}\n📅 {get_subscription_info(target_id)}\n\nВыбери:",
                    reply_markup=get_sub_keyboard(target_id),
                    parse_mode='Markdown'
                )
                user_data[user_id]["step"] = None
            else:
                await update.message.reply_text("❌ Не найден!")
        except:
            await update.message.reply_text("❌ Введи ID!")
        return
    
    # ===== АТАКИ =====
    if user_data.get(user_id, {}).get("step") == "waiting_oper_link":
        if text.startswith(('t.me/', 'https://t.me/')):
            target = text
            user_data[user_id]["step"] = None
            au_sessions = get_global_sessions("au")
            if not au_sessions:
                await update.message.reply_text("❌ Нет сессий!")
                return
            set_queue_busy("au", 300, user_id)
            success = 0
            total = len(au_sessions)
            await update.message.reply_text(f"🚀 Отправка...\n📎 `{target}`", parse_mode='Markdown')
            for i, s in enumerate(au_sessions):
                if await send_au_report(s["session_name"], target, "drug"):
                    success += 1
                if i < total - 1:
                    await asyncio.sleep(5)
            set_queue_busy("au", 0, None)
            try:
                await bot_app.bot.send_message(chat_id=ADMIN_ID, text=f"📨 {user_id} → `{target}` ({success}/{total})", parse_mode='Markdown')
            except: pass
            await update.message.reply_text(f"✅ Готово!\n📎 `{target}`\n✅ {success}/{total}\n\n🔍 Мониторинг!", parse_mode='Markdown')
            asyncio.create_task(auto_monitor_target(user_id, target, "au", "channel", 24))
        else:
            await update.message.reply_text("❌ Неверный формат!")
        return
    
    if user_data.get(user_id, {}).get("step") == "waiting_channel_link":
        if text.startswith(('t.me/', 'https://t.me/')):
            target = text
            user_data[user_id]["step"] = None
            au_sessions = get_global_sessions("au")
            if not au_sessions:
                await update.message.reply_text("❌ Нет сессий!")
                return
            set_queue_busy("au", 300, user_id)
            success = 0
            total = len(au_sessions)
            await update.message.reply_text(f"🚀 Отправка...\n📎 `{target}`", parse_mode='Markdown')
            for i, s in enumerate(au_sessions):
                if await send_au_report(s["session_name"], target, "child"):
                    success += 1
                if i < total - 1:
                    await asyncio.sleep(5)
            set_queue_busy("au", 0, None)
            try:
                await bot_app.bot.send_message(chat_id=ADMIN_ID, text=f"📨 {user_id} → `{target}` ({success}/{total})", parse_mode='Markdown')
            except: pass
            await update.message.reply_text(f"✅ Готово!\n📎 `{target}`\n✅ {success}/{total}\n\n🔍 Мониторинг!", parse_mode='Markdown')
            asyncio.create_task(auto_monitor_target(user_id, target, "au", "channel", 24))
        else:
            await update.message.reply_text("❌ Неверный формат!")
        return
    
    if user_data.get(user_id, {}).get("step") == "waiting_bot_link":
        if text.startswith(('t.me/', 'https://t.me/')):
            target = text
            user_data[user_id]["step"] = None
            au_sessions = get_global_sessions("au")
            if not au_sessions:
                await update.message.reply_text("❌ Нет сессий!")
                return
            set_queue_busy("au", 300, user_id)
            success = 0
            total = len(au_sessions)
            await update.message.reply_text(f"🚀 Отправка...\n📎 `{target}`", parse_mode='Markdown')
            for i, s in enumerate(au_sessions):
                if await send_au_report(s["session_name"], target, "child"):
                    success += 1
                if i < total - 1:
                    await asyncio.sleep(5)
            set_queue_busy("au", 0, None)
            try:
                await bot_app.bot.send_message(chat_id=ADMIN_ID, text=f"📨 {user_id} → `{target}` ({success}/{total})", parse_mode='Markdown')
            except: pass
            await update.message.reply_text(f"✅ Готово!\n📎 `{target}`\n✅ {success}/{total}\n\n🔍 Мониторинг!", parse_mode='Markdown')
            asyncio.create_task(auto_monitor_target(user_id, target, "au", "bot", 48 if user_id == ADMIN_ID else 24))
        else:
            await update.message.reply_text("❌ Неверный формат!")
        return
    
    if user_data.get(user_id, {}).get("step") == "waiting_geo_bot_link":
        if text.startswith(('t.me/', 'https://t.me/')):
            target = text
            user_data[user_id]["step"] = None
            au_sessions = get_global_sessions("au")
            if not au_sessions:
                await update.message.reply_text("❌ Нет сессий!")
                return
            set_queue_busy("au", 300, user_id)
            success = 0
            total = len(au_sessions)
            await update.message.reply_text(f"🚀 Отправка...\n📎 `{target}`", parse_mode='Markdown')
            for i, s in enumerate(au_sessions):
                if await send_au_report(s["session_name"], target, "drug"):
                    success += 1
                if i < total - 1:
                    await asyncio.sleep(5)
            set_queue_busy("au", 0, None)
            try:
                await bot_app.bot.send_message(chat_id=ADMIN_ID, text=f"📨 {user_id} → `{target}` ({success}/{total})", parse_mode='Markdown')
            except: pass
            await update.message.reply_text(f"✅ Готово!\n📎 `{target}`\n✅ {success}/{total}\n\n🔍 Мониторинг!", parse_mode='Markdown')
            asyncio.create_task(auto_monitor_target(user_id, target, "au", "bot", 48 if user_id == ADMIN_ID else 24))
        else:
            await update.message.reply_text("❌ Неверный формат!")
        return
    
    # ===== TIDA =====
    if user_data.get(user_id, {}).get("step") == "waiting_mega_link":
        if text.startswith(('t.me/', 'https://t.me/')):
            target = text
            user_data[user_id]["target"] = target
            user_data[user_id]["step"] = "waiting_mega_text"
            await update.message.reply_text(
                f"📎 Цель: {text}\n\n"
                f"Отправь текст жалобы на **английском**:\n\n"
                f"Пример:\n`This bot sells child pornography. I request that action be taken against the bot.`",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Неверный формат!")
        return
    
    if user_data.get(user_id, {}).get("step") == "waiting_mega_text":
        if len(text) < 10:
            await update.message.reply_text("❌ Слишком короткий текст!")
            return
        
        target = user_data[user_id].get("target")
        user_data[user_id]["mega_text"] = text
        user_data[user_id]["step"] = "waiting_mega_confirm"
        
        await update.message.reply_text(
            f"📎 **Цель:** `{target}`\n\n"
            f"📝 **Текст:**\n_{text[:200]}_\n\n"
            f"⚠️ **Подтвердите отправку:**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Отправить", callback_data="mega_confirm")],
                [InlineKeyboardButton("❌ Отменить", callback_data="mega_cancel")]
            ]),
            parse_mode='Markdown'
        )
        return
    
    # ===== МОНИТОРИНГ =====
    if user_data.get(user_id, {}).get("step") == "waiting_monitor_link":
        if text.startswith(('t.me/', 'https://t.me/')):
            target = text
            target_type = user_data[user_id].get("target_type", "channel")
            
            if target_type == "channel" and ("+" in target or "joinchat" in target):
                await update.message.reply_text("❌ Частные каналы не поддерживаются!")
                user_data[user_id]["step"] = None
                return
            
            tida_sessions = get_global_sessions("tida")
            au_sessions = get_global_sessions("au")
            
            if not tida_sessions and not au_sessions:
                await update.message.reply_text("❌ Нет сессий!")
                user_data[user_id]["step"] = None
                return
            
            session_type = "tida" if tida_sessions else "au"
            
            if user_id == ADMIN_ID and target_type == "bot":
                duration = 48
                duration_label = "2 дня"
            else:
                duration = 24
                duration_label = "24 часа"
            
            target_label = "Бота" if target_type == "bot" else "Канала"
            
            await update.message.reply_text(
                f"🔍 **Мониторинг {target_label} добавлен!**\n\n"
                f"📎 `{target}`\n"
                f"⏱️ {duration_label}\n"
                f"📊 Проверка: каждую минуту",
                parse_mode='Markdown'
            )
            save_log(f"🔍 Мониторинг: {target} ({target_type}, {duration}ч)")
            
            asyncio.create_task(auto_monitor_target(user_id, target, session_type, target_type, duration))
            user_data[user_id]["step"] = None
        else:
            await update.message.reply_text("❌ Неверный формат!")
        return

# ==================== ОБРАБОТЧИК ФАЙЛОВ ====================

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("authenticated"):
        await update.message.reply_text("❌ /start")
        return
    
    user_id = update.effective_user.id
    doc = update.message.document
    
    if user_data.get(user_id, {}).get("step") == "admin_waiting_proxy":
        if doc.file_name.endswith('.txt'):
            fp = await update.message.download(file_name=os.path.join(TEMP_DIR, f"proxy_{user_id}.txt"))
            with open(fp, 'r', encoding='utf-8') as f:
                proxies = [line.strip() for line in f if line.strip()]
            os.remove(fp)
            valid = [p for p in proxies if len(p.split(':')) == 4]
            if valid:
                save_proxies(valid)
                await update.message.reply_text(f"✅ Загружено {len(valid)} прокси!")
                save_log(f"📡 Прокси: {len(valid)}")
            else:
                await update.message.reply_text("❌ Ошибка!")
            user_data[user_id]["step"] = None
            return
    
    if user_data.get(user_id, {}).get("step") == "admin_waiting_phone":
        if not doc.file_name.endswith('.session'):
            await update.message.reply_text("❌ Только .session!")
            return
        
        session_subtype = user_data[user_id].get("session_subtype", "au")
        fp = await update.message.download(file_name=os.path.join(TEMP_DIR, f"{user_id}_{doc.file_name}"))
        
        session_name = doc.file_name.replace('.session', '')
        is_valid, phone = await check_session_valid(session_name)
        
        if not is_valid:
            await update.message.reply_text("❌ Сессия невалидна!")
            os.remove(fp)
            return
        
        dst = os.path.join(SESSIONS_DIR, f"{session_name}.session")
        shutil.copy2(fp, dst)
        os.remove(fp)
        
        if add_global_session(session_name, phone, session_subtype):
            label = "TIDA" if session_subtype == "tida" else "AUReport"
            await update.message.reply_text(f"✅ Сессия добавлена!\n📞 {phone}\n🔹 {label}")
        else:
            await update.message.reply_text(f"⚠️ Уже существует!")
        
        user_data[user_id]["step"] = None
        return

# ==================== ЗАПУСК ====================

async def main():
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    
    asyncio.create_task(check_sessions_loop())
    
    print("=" * 50)
    print("🤖 БОТ ЗАПУЩЕН!")
    print(f"🔐 API ID: {API_ID}")
    print(f"👑 Админ: {ADMIN_ID}")
    print(f"🔑 Пароль: {PASSWORD}")
    print("=" * 50)
    
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()
    
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        await bot_app.updater.stop()
        await bot_app.stop()

if __name__ == "__main__":
    asyncio.run(main())