import os
import re
import logging
import asyncio
import aiohttp
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# ==================== CONFIG ====================
BOT_TOKEN = "8589753213:AAEElVXtq9KY-TwopTWxez5tqQMV08RJd4s"
AUDD_API_KEY = "fd8bde2f5e826049cf8f3f0dbef54af0"
ADMIN_IDS = [7434706702]
REQUIRED_CHANNELS = []

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect("nola_bot.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        user_id INTEGER UNIQUE,
        username TEXT,
        full_name TEXT,
        lang TEXT DEFAULT 'uz',
        joined_at TEXT,
        last_active TEXT,
        is_blocked INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS searches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        query TEXT,
        result TEXT,
        searched_at TEXT
    )""")
    conn.commit()
    conn.close()

def add_user(user_id, username, full_name):
    conn = sqlite3.connect("nola_bot.db")
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, joined_at, last_active) VALUES (?, ?, ?, ?, ?)",
              (user_id, username, full_name, now, now))
    c.execute("UPDATE users SET last_active=?, username=?, full_name=? WHERE user_id=?",
              (now, username, full_name, user_id))
    conn.commit()
    conn.close()

def get_user_lang(user_id):
    conn = sqlite3.connect("nola_bot.db")
    c = conn.cursor()
    c.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else "uz"

def set_user_lang(user_id, lang):
    conn = sqlite3.connect("nola_bot.db")
    c = conn.cursor()
    c.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, user_id))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect("nola_bot.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE is_blocked=0")
    total = c.fetchone()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE joined_at LIKE ?", (f"{today}%",))
    today_new = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM searches")
    total_s = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM searches WHERE searched_at LIKE ?", (f"{today}%",))
    today_s = c.fetchone()[0]
    conn.close()
    return total, today_new, total_s, today_s

def get_all_users():
    conn = sqlite3.connect("nola_bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_blocked=0")
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

def log_search(user_id, query, result):
    conn = sqlite3.connect("nola_bot.db")
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO searches (user_id, query, result, searched_at) VALUES (?, ?, ?, ?)",
              (user_id, query, result, now))
    conn.commit()
    conn.close()

# ==================== TEXTS ====================
TEXTS = {
    "uz": {
        "welcome": "🎵 <b>Nola Bot</b>ga xush kelibsiz!\n\nQo'shiq nomini yozing yoki audio yuboring!\nInstagram video linkini ham yuborsangiz qo'shiqni topaman!",
        "choose_lang": "🌐 Tilni tanlang:",
        "lang_set": "✅ Til o'zgartirildi!",
        "send_audio": "🎵 Audio fayl, ovozli xabar yoki Instagram link yuboring!",
        "searching": "🔍 Qidirilmoqda...",
        "recognizing": "🎵 Qo'shiq tanilmoqda...",
        "found_shazam": "✅ Qo'shiq topildi!\n\n🎵 <b>{title}</b>\n🎤 {artist}\n💿 {album}\n📅 {year}\n\n⬇️ Yuklab olish uchun tanlang:",
        "not_found": "❌ Qo'shiq topilmadi.",
        "downloading": "⬇️ Yuklanmoqda, kuting...",
        "help": "ℹ️ <b>Yordam</b>\n\n• Qo'shiq nomi yozing → YouTube dan qidiradi\n• Audio yuboring → Shazam kabi taniydi\n• Instagram link yuboring → qo'shiqni topadi\n• /lang → til o'zgartirish\n• /admin → admin panel",
        "error": "⚠️ Xatolik yuz berdi. Qaytadan urinib ko'ring.",
        "no_result": "❌ Natija topilmadi.",
        "page_info": "📄 Sahifa {cur}/{total} — {count} ta natija",
        "processing_instagram": "📱 Instagram video qayta ishlanmoqda...",
        "instagram_error": "❌ Instagram linkidan qo'shiq topilmadi.",
        "sub_required": "📢 Botdan foydalanish uchun kanalga obuna bo'ling:",
        "sub_check": "✅ Obunani tekshirish",
        "sub_ok": "✅ Rahmat! Botdan foydalanishingiz mumkin.",
        "sub_fail": "❌ Hali obuna bo'lmagansiz.",
    },
    "ru": {
        "welcome": "🎵 Добро пожаловать в <b>Nola Bot</b>!\n\nНапишите название песни или отправьте аудио!\nМожно отправить ссылку на Instagram видео!",
        "choose_lang": "🌐 Выберите язык:",
        "lang_set": "✅ Язык изменён!",
        "send_audio": "🎵 Отправьте аудио, голосовое или ссылку на Instagram!",
        "searching": "🔍 Поиск...",
        "recognizing": "🎵 Распознаю песню...",
        "found_shazam": "✅ Песня найдена!\n\n🎵 <b>{title}</b>\n🎤 {artist}\n💿 {album}\n📅 {year}\n\n⬇️ Выберите для скачивания:",
        "not_found": "❌ Песня не найдена.",
        "downloading": "⬇️ Загрузка, подождите...",
        "help": "ℹ️ <b>Помощь</b>\n\n• Напишите название → поиск на YouTube\n• Отправьте аудио → распознаёт как Shazam\n• Ссылка Instagram → найдёт песню\n• /lang → сменить язык",
        "error": "⚠️ Ошибка. Попробуйте снова.",
        "no_result": "❌ Результат не найден.",
        "page_info": "📄 Страница {cur}/{total} — {count} результатов",
        "processing_instagram": "📱 Обрабатываю Instagram видео...",
        "instagram_error": "❌ Не удалось найти песню из Instagram.",
        "sub_required": "📢 Подпишитесь на канал для использования бота:",
        "sub_check": "✅ Проверить подписку",
        "sub_ok": "✅ Спасибо! Теперь можете пользоваться ботом.",
        "sub_fail": "❌ Вы ещё не подписались.",
    },
    "en": {
        "welcome": "🎵 Welcome to <b>Nola Bot</b>!\n\nType a song name or send audio!\nYou can also send an Instagram video link!",
        "choose_lang": "🌐 Choose language:",
        "lang_set": "✅ Language changed!",
        "send_audio": "🎵 Send audio, voice message or Instagram link!",
        "searching": "🔍 Searching...",
        "recognizing": "🎵 Recognizing song...",
        "found_shazam": "✅ Song found!\n\n🎵 <b>{title}</b>\n🎤 {artist}\n💿 {album}\n📅 {year}\n\n⬇️ Select to download:",
        "not_found": "❌ Song not found.",
        "downloading": "⬇️ Downloading, please wait...",
        "help": "ℹ️ <b>Help</b>\n\n• Type song name → search YouTube\n• Send audio → Shazam-like recognition\n• Instagram link → find the song\n• /lang → change language",
        "error": "⚠️ Error occurred. Please try again.",
        "no_result": "❌ No results found.",
        "page_info": "📄 Page {cur}/{total} — {count} results",
        "processing_instagram": "📱 Processing Instagram video...",
        "instagram_error": "❌ Could not find song from Instagram.",
        "sub_required": "📢 Subscribe to the channel to use the bot:",
        "sub_check": "✅ Check subscription",
        "sub_ok": "✅ Thank you! You can now use the bot.",
        "sub_fail": "❌ You have not subscribed yet.",
    }
}

def t(user_id, key, **kwargs):
    lang = get_user_lang(user_id)
    text = TEXTS.get(lang, TEXTS["uz"]).get(key, key)
    return text.format(**kwargs) if kwargs else text

# ==================== KEYBOARDS ====================
def lang_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data="lang_uz"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
    ]])

def main_keyboard(user_id):
    lang = get_user_lang(user_id)
    labels = {
        "uz": ["🎵 Qo'shiq qidirish", "🌐 Til", "ℹ️ Yordam"],
        "ru": ["🎵 Поиск песни", "🌐 Язык", "ℹ️ Помощь"],
        "en": ["🎵 Search song", "🌐 Language", "ℹ️ Help"],
    }
    lb = labels.get(lang, labels["uz"])
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=lb[0])], [KeyboardButton(text=lb[1]), KeyboardButton(text=lb[2])]],
        resize_keyboard=True
    )

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Kanallar", callback_data="admin_channels")],
        [InlineKeyboardButton(text="📣 Reklama → Kanal", callback_data="adm_bc_channel")],
        [InlineKeyboardButton(text="💬 Reklama → Lichniy", callback_data="adm_bc_private")],
    ])

def sub_keyboard(channels):
    buttons = []
    for ch in channels:
        buttons.append([InlineKeyboardButton(text=f"📢 {ch}", url=f"https://t.me/{ch.lstrip('@')}")])
    buttons.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def results_keyboard(results, page, uid):
    buttons = []
    start = page * 10
    end = min(start + 10, len(results))
    for i in range(start, end):
        r = results[i]
        title = (r.get("title") or "")[:28]
        artist = (r.get("artist") or "")[:18]
        buttons.append([InlineKeyboardButton(
            text=f"🎵 {i - start + 1}. {title} — {artist}",
            callback_data=f"dl|{uid}|{i}"
        )])
    nav = []
    total_pages = (len(results) + 9) // 10
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Oldingi", callback_data=f"pg|{uid}|{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Keyingi ▶️", callback_data=f"pg|{uid}|{page+1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== CACHE ====================
search_cache = {}  # uid -> list of results

# ==================== YOUTUBE ====================
async def search_youtube(query: str, limit=20) -> list:
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}&sp=EgIQAQ%3D%3D"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                html = await resp.text()

        results = []
        seen = set()
        pattern = r'"videoId":"([\w-]{11})"[^}]*?"text":"([^"]+)"[^}]*?(?:"simpleText":"(\d+:\d+)")?'
        matches = re.findall(pattern, html)
        
        for vid_id, title, duration in matches:
            if vid_id not in seen and len(results) < limit:
                if len(title) > 3 and not title.startswith("Watch"):
                    seen.add(vid_id)
                    results.append({
                        "video_id": vid_id,
                        "title": title,
                        "artist": "",
                        "duration": duration or "",
                        "url": f"https://www.youtube.com/watch?v={vid_id}"
                    })
        return results
    except Exception as e:
        logger.error(f"YT search error: {e}")
        return []

async def get_download_url(video_id: str) -> str | None:
    try:
        async with aiohttp.ClientSession() as session:
            # Try cobalt.tools
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            payload = {"url": f"https://www.youtube.com/watch?v={video_id}", "isAudioOnly": True, "aFormat": "mp3"}
            async with session.post("https://api.cobalt.tools/api/json", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") in ["stream", "redirect", "tunnel"]:
                        return data.get("url")
    except Exception as e:
        logger.error(f"Cobalt error: {e}")
    
    try:
        async with aiohttp.ClientSession() as session:
            # Try y2mate style
            async with session.get(
                f"https://yt-mp3.com/api/mp3/{video_id}",
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("url")
    except:
        pass
    return None

# ==================== AUDD (SHAZAM) ====================
async def recognize_from_file(file_path: str) -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            with open(file_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("api_token", AUDD_API_KEY)
                data.add_field("file", f, filename="audio.ogg")
                data.add_field("return", "spotify,deezer")
                async with session.post("https://api.audd.io/", data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    result = await resp.json()
                    if result.get("status") == "success" and result.get("result"):
                        r = result["result"]
                        return {
                            "title": r.get("title", "?"),
                            "artist": r.get("artist", "?"),
                            "album": r.get("album", "?"),
                            "release_date": r.get("release_date", "?"),
                        }
    except Exception as e:
        logger.error(f"AudD error: {e}")
    return None

async def recognize_from_url(url: str) -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field("api_token", AUDD_API_KEY)
            data.add_field("url", url)
            data.add_field("return", "spotify,deezer")
            async with session.post("https://api.audd.io/", data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                result = await resp.json()
                if result.get("status") == "success" and result.get("result"):
                    r = result["result"]
                    return {
                        "title": r.get("title", "?"),
                        "artist": r.get("artist", "?"),
                        "album": r.get("album", "?"),
                        "release_date": r.get("release_date", "?"),
                    }
    except Exception as e:
        logger.error(f"AudD URL error: {e}")
    return None

# ==================== INSTAGRAM ====================
def is_instagram_link(text: str) -> bool:
    return bool(re.search(r'instagram\.com/(p|reel|tv)/[\w-]+', text))

async def get_instagram_audio_url(ig_url: str) -> str | None:
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            payload = {"url": ig_url, "isAudioOnly": True}
            async with session.post("https://api.cobalt.tools/api/json", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") in ["stream", "redirect", "tunnel"]:
                        return data.get("url")
    except Exception as e:
        logger.error(f"Instagram cobalt error: {e}")
    return None

# ==================== SUBSCRIPTION ====================
async def check_subscription(bot: Bot, user_id: int) -> bool:
    if not REQUIRED_CHANNELS:
        return True
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status in ["left", "kicked"]:
                return False
        except:
            pass
    return True

# ==================== FSM ====================
class BroadcastState(StatesGroup):
    waiting_message = State()
    confirm = State()

# ==================== BOT INIT ====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# ==================== COMMAND HANDLERS ====================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    add_user(uid, message.from_user.username, message.from_user.full_name)
    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(REQUIRED_CHANNELS))
        return
    await message.answer(t(uid, "welcome"), reply_markup=main_keyboard(uid))

@dp.message(Command("lang"))
async def cmd_lang(message: types.Message):
    await message.answer(t(message.from_user.id, "choose_lang"), reply_markup=lang_keyboard())

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("👑 <b>Admin Panel</b>", reply_markup=admin_keyboard())

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(t(message.from_user.id, "help"))

# ==================== CALLBACKS ====================
@dp.callback_query(F.data.startswith("lang_"))
async def cb_lang(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    set_user_lang(callback.from_user.id, lang)
    await callback.message.edit_text(t(callback.from_user.id, "lang_set"))
    await callback.message.answer(t(callback.from_user.id, "welcome"), reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if await check_subscription(bot, uid):
        await callback.message.edit_text(t(uid, "sub_ok"))
        await callback.message.answer(t(uid, "welcome"), reply_markup=main_keyboard(uid))
    else:
        await callback.answer(t(uid, "sub_fail"), show_alert=True)

@dp.callback_query(F.data.startswith("dl|"))
async def cb_download(callback: types.CallbackQuery):
    uid = callback.from_user.id
    parts = callback.data.split("|")
    idx = int(parts[2])
    
    results = search_cache.get(uid, [])
    if not results or idx >= len(results):
        await callback.answer("❌ Natija topilmadi", show_alert=True)
        return
    
    song = results[idx]
    status = await callback.message.answer(t(uid, "downloading"))
    
    try:
        video_id = song.get("video_id")
        dl_url = await get_download_url(video_id)
        
        if dl_url:
            title = song.get("title", "Qo'shiq")
            await bot.send_audio(
                uid,
                audio=dl_url,
                title=title[:64],
                caption=f"🎵 {title}"
            )
            await status.delete()
        else:
            await status.edit_text(f"🔗 <a href='{song.get('url')}'>YouTube da tinglash</a>")
    except Exception as e:
        logger.error(f"DL error: {e}")
        await status.edit_text(f"🔗 <a href='{song.get('url')}'>YouTube da tinglash</a>")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("pg|"))
async def cb_page(callback: types.CallbackQuery):
    uid = callback.from_user.id
    parts = callback.data.split("|")
    page = int(parts[2])
    
    results = search_cache.get(uid, [])
    if not results:
        await callback.answer("❌", show_alert=True)
        return
    
    total_pages = (len(results) + 9) // 10
    text = t(uid, "page_info", cur=page+1, total=total_pages, count=len(results))
    await callback.message.edit_text(text, reply_markup=results_keyboard(results, page, uid))
    await callback.answer()

# ==================== ADMIN CALLBACKS ====================
@dp.callback_query(F.data == "admin_stats")
async def cb_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    total, today_new, total_s, today_s = get_stats()
    text = (f"📊 <b>Statistika</b>\n\n"
            f"👥 Jami foydalanuvchilar: <b>{total}</b>\n"
            f"🆕 Bugun yangilar: <b>{today_new}</b>\n"
            f"🔍 Jami qidiruvlar: <b>{total_s}</b>\n"
            f"🔎 Bugun qidiruvlar: <b>{today_s}</b>\n"
            f"🕐 Vaqt: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    await callback.message.edit_text(text, reply_markup=admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_channels")
async def cb_channels(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    channels = "\n".join(REQUIRED_CHANNELS) if REQUIRED_CHANNELS else "❌ Kanal ulanmagan"
    await callback.message.edit_text(
        f"📢 <b>Ulangan kanallar:</b>\n{channels}\n\n"
        f"Kanal qo'shish: bot.py da REQUIRED_CHANNELS ni o'zgartiring.",
        reply_markup=admin_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "adm_bc_private")
async def cb_bc_private(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastState.waiting_message)
    await state.update_data(target="private")
    await callback.message.answer("💬 Lichniy foydalanuvchilarga yuboriladigan xabarni yozing:")
    await callback.answer()

@dp.callback_query(F.data == "adm_bc_channel")
async def cb_bc_channel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastState.waiting_message)
    await state.update_data(target="channel")
    await callback.message.answer("📣 Kanalga yuboriladigan xabarni yozing:")
    await callback.answer()

@dp.message(BroadcastState.waiting_message)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    await state.update_data(broadcast_text=message.text)
    await state.set_state(BroadcastState.confirm)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, yuborish", callback_data=f"bc_yes_{data['target']}"),
        InlineKeyboardButton(text="❌ Bekor", callback_data="bc_no"),
    ]])
    await message.answer(f"📤 Xabar:\n\n{message.text}\n\nYuborishni tasdiqlaysizmi?", reply_markup=kb)

@dp.callback_query(F.data.startswith("bc_yes_"))
async def confirm_bc(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    target = callback.data.replace("bc_yes_", "")
    await state.clear()
    
    users = get_all_users()
    sent = failed = 0
    status_msg = await callback.message.answer("📤 Yuborilmoqda...")
    
    if target == "channel" and REQUIRED_CHANNELS:
        for ch in REQUIRED_CHANNELS:
            try:
                await bot.send_message(ch, text)
                sent += 1
            except:
                failed += 1
    else:
        for uid in users:
            try:
                await bot.send_message(uid, text)
                sent += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
    
    await status_msg.edit_text(f"✅ Yuborildi: {sent}\n❌ Xato: {failed}")
    await callback.answer()

@dp.callback_query(F.data == "bc_no")
async def cancel_bc(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()

# ==================== AUDIO/VOICE HANDLER ====================
@dp.message(F.audio | F.voice)
async def handle_audio(message: types.Message):
    uid = message.from_user.id
    add_user(uid, message.from_user.username, message.from_user.full_name)
    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(REQUIRED_CHANNELS))
        return
    
    status = await message.answer(t(uid, "recognizing"))
    try:
        file_obj = message.audio or message.voice
        file = await bot.get_file(file_obj.file_id)
        file_path = f"/tmp/audio_{uid}.ogg"
        await bot.download_file(file.file_path, destination=file_path)
        result = await recognize_from_file(file_path)
        
        if result:
            year = str(result.get("release_date", "?"))[:4]
            await status.edit_text(t(uid, "found_shazam",
                title=result.get("title", "?"),
                artist=result.get("artist", "?"),
                album=result.get("album", "?"),
                year=year))
            
            query = f"{result.get('artist')} {result.get('title')}"
            yt_results = await search_youtube(query, limit=20)
            if yt_results:
                search_cache[uid] = yt_results
                total_pages = (len(yt_results) + 9) // 10
                text = t(uid, "page_info", cur=1, total=total_pages, count=len(yt_results))
                await message.answer(text, reply_markup=results_keyboard(yt_results, 0, uid))
            log_search(uid, "audio", result.get("title", ""))
        else:
            await status.edit_text(t(uid, "not_found"))
    except Exception as e:
        logger.error(f"Audio error: {e}")
        await status.edit_text(t(uid, "error"))

# ==================== TEXT HANDLER ====================
@dp.message(F.text)
async def handle_text(message: types.Message):
    uid = message.from_user.id
    text = message.text
    add_user(uid, message.from_user.username, message.from_user.full_name)
    
    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(REQUIRED_CHANNELS))
        return
    
    lang = get_user_lang(uid)
    menu = {
        "uz": ["🎵 Qo'shiq qidirish", "🌐 Til", "ℹ️ Yordam"],
        "ru": ["🎵 Поиск песни", "🌐 Язык", "ℹ️ Помощь"],
        "en": ["🎵 Search song", "🌐 Language", "ℹ️ Help"],
    }.get(lang, ["🎵 Qo'shiq qidirish", "🌐 Til", "ℹ️ Yordam"])
    
    if text == menu[0]:
        await message.answer(t(uid, "send_audio"))
        return
    if text == menu[1]:
        await message.answer(t(uid, "choose_lang"), reply_markup=lang_keyboard())
        return
    if text == menu[2]:
        await message.answer(t(uid, "help"))
        return
    
    # Instagram link
    if is_instagram_link(text):
        status = await message.answer(t(uid, "processing_instagram"))
        try:
            audio_url = await get_instagram_audio_url(text)
            if audio_url:
                result = await recognize_from_url(audio_url)
                if result:
                    year = str(result.get("release_date", "?"))[:4]
                    await status.edit_text(t(uid, "found_shazam",
                        title=result.get("title", "?"),
                        artist=result.get("artist", "?"),
                        album=result.get("album", "?"),
                        year=year))
                    query = f"{result.get('artist')} {result.get('title')}"
                    yt_results = await search_youtube(query, limit=20)
                    if yt_results:
                        search_cache[uid] = yt_results
                        total_pages = (len(yt_results) + 9) // 10
                        info = t(uid, "page_info", cur=1, total=total_pages, count=len(yt_results))
                        await message.answer(info, reply_markup=results_keyboard(yt_results, 0, uid))
                    log_search(uid, text, result.get("title", ""))
                    return
            await status.edit_text(t(uid, "instagram_error"))
        except Exception as e:
            logger.error(f"Instagram error: {e}")
            await status.edit_text(t(uid, "error"))
        return
    
    # YouTube search
    status = await message.answer(t(uid, "searching"))
    try:
        results = await search_youtube(text, limit=20)
        if results:
            search_cache[uid] = results
            total_pages = (len(results) + 9) // 10
            info = t(uid, "page_info", cur=1, total=total_pages, count=len(results))
            await status.edit_text(info, reply_markup=results_keyboard(results, 0, uid))
            log_search(uid, text, results[0].get("title", ""))
        else:
            await status.edit_text(t(uid, "no_result"))
    except Exception as e:
        logger.error(f"Search error: {e}")
        await status.edit_text(t(uid, "error"))

# ==================== MAIN ====================
async def main():
    init_db()
    logger.info("🎵 Nola Bot ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
