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
from aiogram.types import FSInputFile
import subprocess
import hashlib

# ==================== CONFIG ====================
def _load_token():
    try:
        with open("/opt/nolabot/token.txt") as f:
            return f.read().strip()
    except Exception:
        return os.environ.get("BOT_TOKEN", "")

BOT_TOKEN = _load_token()
AUDD_API_KEY = "fd8bde2f5e826049cf8f3f0dbef54af0"
ADMIN_IDS = [7434706702]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== DATABASE ====================
DB = "nola_bot.db"

def init_db():
    conn = sqlite3.connect(DB)
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
    c.execute("""CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_username TEXT UNIQUE,
        added_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS audio_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_key TEXT UNIQUE,
        file_id TEXT,
        cached_at TEXT
    )""")
    conn.commit()
    conn.close()

def get_cached_audio(song_key):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT file_id FROM audio_cache WHERE song_key=?", (song_key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def save_cached_audio(song_key, file_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("INSERT OR REPLACE INTO audio_cache (song_key, file_id, cached_at) VALUES (?,?,?)",
                  (song_key, file_id, datetime.now().isoformat()))
        conn.commit()
    except Exception:
        pass
    conn.close()

def add_user(user_id, username, full_name):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, joined_at, last_active) VALUES (?, ?, ?, ?, ?)",
              (user_id, username, full_name, now, now))
    c.execute("UPDATE users SET last_active=?, username=?, full_name=? WHERE user_id=?",
              (now, username, full_name, user_id))
    conn.commit()
    conn.close()

def get_user_lang(user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else "uz"

def set_user_lang(user_id, lang):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, user_id))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE is_blocked=0")
    total = c.fetchone()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE joined_at LIKE ?", (f"{today}%",))
    today_new = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE last_active LIKE ?", (f"{today}%",))
    today_active = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM searches")
    total_s = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM searches WHERE searched_at LIKE ?", (f"{today}%",))
    today_s = c.fetchone()[0]
    conn.close()
    return total, today_new, today_active, total_s, today_s

def get_all_users():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_blocked=0")
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

def log_search(user_id, query, result):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO searches (user_id, query, result, searched_at) VALUES (?, ?, ?, ?)",
              (user_id, query, result, now))
    conn.commit()
    conn.close()

def get_channels():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT channel_username FROM channels")
    channels = [row[0] for row in c.fetchall()]
    conn.close()
    return channels

def add_channel(username):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        c.execute("INSERT INTO channels (channel_username, added_at) VALUES (?, ?)", (username, now))
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

def remove_channel(username):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM channels WHERE channel_username=?", (username,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

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
        "downloading": "⬇️ Yuklanmoqda...",
        "help": "ℹ️ <b>Yordam</b>\n\n• Qo'shiq nomi yozing → qidiradi\n• Audio yuboring → taniydi\n• Instagram link → qo'shiqni topadi\n• /lang → til",
        "error": "⚠️ Xatolik. Qaytadan urinib ko'ring.",
        "no_result": "❌ Natija topilmadi.",
        "page_info": "📄 Sahifa {cur}/{total} — {count} ta natija",
        "processing_instagram": "📱 Instagram qayta ishlanmoqda...",
        "instagram_error": "❌ Instagram dan qo'shiq topilmadi.",
        "sub_required": "📢 Botdan foydalanish uchun kanal(lar)ga obuna bo'ling:",
        "sub_ok": "✅ Rahmat! Botdan foydalanishingiz mumkin.",
        "sub_fail": "❌ Hali obuna bo'lmagansiz.",
        "preview_note": "⚠️ 30 soniyalik namuna",
    },
    "ru": {
        "welcome": "🎵 Добро пожаловать в <b>Nola Bot</b>!\n\nНапишите название песни или отправьте аудио!",
        "choose_lang": "🌐 Выберите язык:",
        "lang_set": "✅ Язык изменён!",
        "send_audio": "🎵 Отправьте аудио, голосовое или ссылку Instagram!",
        "searching": "🔍 Поиск...",
        "recognizing": "🎵 Распознаю...",
        "found_shazam": "✅ Песня найдена!\n\n🎵 <b>{title}</b>\n🎤 {artist}\n💿 {album}\n📅 {year}\n\n⬇️ Выберите:",
        "not_found": "❌ Не найдена.",
        "downloading": "⬇️ Загрузка...",
        "help": "ℹ️ Напишите название или отправьте аудио!\n/lang — язык",
        "error": "⚠️ Ошибка.",
        "no_result": "❌ Не найдено.",
        "page_info": "📄 Страница {cur}/{total} — {count} результатов",
        "processing_instagram": "📱 Обработка Instagram...",
        "instagram_error": "❌ Не найдено из Instagram.",
        "sub_required": "📢 Подпишитесь на канал(ы):",
        "sub_ok": "✅ Спасибо!",
        "sub_fail": "❌ Не подписались.",
        "preview_note": "⚠️ 30-секундное превью",
    },
    "en": {
        "welcome": "🎵 Welcome to <b>Nola Bot</b>!\n\nType a song name or send audio!",
        "choose_lang": "🌐 Choose language:",
        "lang_set": "✅ Language changed!",
        "send_audio": "🎵 Send audio, voice or Instagram link!",
        "searching": "🔍 Searching...",
        "recognizing": "🎵 Recognizing...",
        "found_shazam": "✅ Song found!\n\n🎵 <b>{title}</b>\n🎤 {artist}\n💿 {album}\n📅 {year}\n\n⬇️ Select:",
        "not_found": "❌ Not found.",
        "downloading": "⬇️ Downloading...",
        "help": "ℹ️ Type a song name or send audio!\n/lang — language",
        "error": "⚠️ Error.",
        "no_result": "❌ No results.",
        "page_info": "📄 Page {cur}/{total} — {count} results",
        "processing_instagram": "📱 Processing Instagram...",
        "instagram_error": "❌ Not found from Instagram.",
        "sub_required": "📢 Subscribe to channel(s):",
        "sub_ok": "✅ Thank you!",
        "sub_fail": "❌ Not subscribed.",
        "preview_note": "⚠️ 30-second preview",
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
        [InlineKeyboardButton(text="📊 Statistika", callback_data="adm_stats")],
        [InlineKeyboardButton(text="📢 Kanal boshqaruvi", callback_data="adm_channels")],
        [InlineKeyboardButton(text="📣 Reklama → Kanallar", callback_data="adm_bc_channel")],
        [InlineKeyboardButton(text="💬 Reklama → Lichniy", callback_data="adm_bc_private")],
    ])

def channels_admin_keyboard():
    channels = get_channels()
    buttons = []
    for ch in channels:
        buttons.append([InlineKeyboardButton(text=f"🗑 {ch}", callback_data=f"delch|{ch}")])
    buttons.append([InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="addch")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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
        artist = (r.get("artist") or "")[:15]
        dur = r.get("duration", "")
        label = f"🎵 {i-start+1}. {title} — {artist}"
        if dur:
            label += f" [{dur}]"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"dl|{uid}|{i}")])
    nav = []
    total_pages = (len(results) + 9) // 10
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pg|{uid}|{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pg|{uid}|{page+1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== CACHE ====================
search_cache = {}
media_link_cache = {}

# ==================== SEARCH (Deezer) ====================
async def search_songs(query: str, limit=20) -> list:
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.deezer.com/search?q={query.replace(' ', '+')}&limit={limit}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = []
                    for item in data.get("data", []):
                        dur = item.get("duration", 0)
                        results.append({
                            "title": item.get("title", ""),
                            "artist": item.get("artist", {}).get("name", ""),
                            "duration": f"{dur//60}:{dur%60:02d}" if dur else "",
                            "url": item.get("link", ""),
                            "preview": item.get("preview", ""),
                        })
                    return results
    except Exception as e:
        logger.error(f"Deezer error: {e}")
    return []

# ==================== AUDD (SHAZAM) ====================
async def recognize_from_file(file_path: str) -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            with open(file_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("api_token", AUDD_API_KEY)
                data.add_field("return", "apple_music,spotify")
                data.add_field("file", f, filename="audio.mp3")
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
YTDLP = "/opt/nolabot/venv/bin/yt-dlp"

async def ytdlp_download(title: str, artist: str) -> str | None:
    """YouTube dan to'liq MP3 yuklash. Fayl yo'lini qaytaradi yoki None."""
    query = f"{artist} {title}".strip()
    h = hashlib.md5(query.encode()).hexdigest()[:16]
    out = f"/tmp/nola_{h}.mp3"
    if os.path.exists(out):
        return out
    cmd = [
        YTDLP, "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "-f", "bestaudio/best",
        "--no-playlist", "--no-warnings", "--max-filesize", "50M",
        "--concurrent-fragments", "8", "-N", "8",
        "-o", f"/tmp/nola_{h}.%(ext)s",
        f"ytsearch1:{query}",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
        if os.path.exists(out) and os.path.getsize(out) > 100_000:
            return out
    except Exception as e:
        logger.error(f"ytdlp error: {e}")
    return None

def is_instagram_link(text: str) -> bool:
    return bool(re.search(r'instagram\.com/(p|reel|tv|reels)/[\w-]+', text))

def detect_media_link(text: str) -> str | None:
    """Instagram/TikTok/YouTube havolasini aniqlaydi."""
    patterns = [
        r'(https?://)?(www\.|vm\.|vt\.)?(tiktok\.com/\S+)',
        r'(https?://)?(www\.)?(instagram\.com/(p|reel|tv|reels)/[\w-]+\S*)',
        r'(https?://)?(www\.)?(youtube\.com/(watch\?v=|shorts/)[\w-]+\S*)',
        r'(https?://)?(youtu\.be/[\w-]+\S*)',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            url = m.group(0)
            if not url.startswith("http"):
                url = "https://" + url
            return url
    return None

async def get_media_video(url: str) -> str | None:
    """IG/TikTok dan VIDEO yuklaydi (max 50MB). Fayl yo'lini qaytaradi."""
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    out = f"/tmp/vid_{h}.mp4"
    if os.path.exists(out):
        return out
    cmd = [
        YTDLP,
        "-f", "best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--no-playlist", "--no-warnings", "--no-check-certificate",
        "--max-filesize", "45M",
        "--concurrent-fragments", "8", "-N", "8",
        "-o", f"/tmp/vid_{h}.%(ext)s", url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), timeout=90)
        # .mp4 yoki boshqa kengaytma bo'lishi mumkin
        for ext in ("mp4", "webm", "mkv", "mov"):
            p = f"/tmp/vid_{h}.{ext}"
            if os.path.exists(p) and os.path.getsize(p) > 5_000:
                return p
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Video error: {e}")
    return None

async def get_instagram_audio(ig_url: str) -> str | None:
    """Media (IG/TikTok/YT) dan audioni TEZ yuklaydi. Faqat Shazam uchun qisqa qism kifoya."""
    h = hashlib.md5(ig_url.encode()).hexdigest()[:16]
    out = f"/tmp/ig_{h}.mp3"
    if os.path.exists(out):
        return out
    # Yuqori sifatli audio (tanish ham aniqroq, tez ham — parallel oqim bilan)
    cmd = [
        YTDLP, "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "-f", "bestaudio/best",
        "--no-playlist", "--no-warnings", "--no-check-certificate",
        "--max-filesize", "50M",
        "--concurrent-fragments", "8",
        "-N", "8",
        "-o", f"/tmp/ig_{h}.%(ext)s", ig_url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), timeout=60)
        if os.path.exists(out) and os.path.getsize(out) > 5_000:
            return out
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Instagram error: {e}")
    return None

# ==================== SUBSCRIPTION CHECK ====================
async def check_subscription(bot: Bot, user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    channels = get_channels()
    if not channels:
        return True
    for channel in channels:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception as e:
            logger.error(f"Sub check error {channel}: {e}")
    return True

# ==================== FSM ====================
class BroadcastState(StatesGroup):
    waiting_message = State()
    confirm = State()

class ChannelState(StatesGroup):
    waiting_channel = State()

# ==================== BOT ====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# ==================== HANDLERS ====================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    add_user(uid, message.from_user.username, message.from_user.full_name)
    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(get_channels()))
        return
    await message.answer(t(uid, "welcome"), reply_markup=main_keyboard(uid))

@dp.message(Command("lang"))
async def cmd_lang(message: types.Message):
    await message.answer(t(message.from_user.id, "choose_lang"), reply_markup=lang_keyboard())

@dp.message(Command("restart"))
async def cmd_restart(message: types.Message):
    uid = message.from_user.id
    if uid in ADMIN_IDS:
        await message.answer("🔄 Bot qayta ishga tushmoqda... (5 soniya)")
        await asyncio.sleep(1)
        os._exit(0)  # systemd Restart=always avtomatik qayta ishga tushiradi
    else:
        add_user(uid, message.from_user.username, message.from_user.full_name)
        await message.answer(t(uid, "welcome"), reply_markup=main_keyboard(uid))

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("👑 <b>Admin Panel</b>", reply_markup=admin_keyboard())

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(t(message.from_user.id, "help"))

@dp.callback_query(F.data.startswith("lang_"))
async def cb_lang(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    set_user_lang(callback.from_user.id, lang)
    await callback.message.edit_text(t(callback.from_user.id, "lang_set"))
    await callback.message.answer(t(callback.from_user.id, "welcome"), reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "getsong")
async def cb_getsong(callback: types.CallbackQuery):
    uid = callback.from_user.id
    url = media_link_cache.get(uid)
    if not url:
        await callback.answer("Havola eskirgan, qaytadan yuboring", show_alert=True)
        return
    await callback.answer("🎵 Qo'shiq qidirilmoqda...")
    status = await callback.message.answer("⏳ Qo'shiq aniqlanmoqda...")
    link_key = "lnk_" + hashlib.md5(url.encode()).hexdigest()
    # Cache: oldin tanilganmi?
    cached_song = get_cached_audio(link_key)
    if cached_song:
        try:
            await bot.send_audio(uid, audio=cached_song, caption="🎵 Tayyor!\n\n🤖 @nolamusicbot")
            await status.delete()
            return
        except Exception:
            pass
    try:
        audio_path = await get_instagram_audio(url)
        if not audio_path:
            await status.edit_text(t(uid, "instagram_error"))
            return
        result = await recognize_from_file(audio_path)
        try:
            os.remove(audio_path)
        except Exception:
            pass
        if not result:
            await status.edit_text("❌ Qo'shiq tanilmadi. 🎧 Audioni yuklashni sinab ko'ring.")
            return
        s_title = result.get("title", "")
        s_artist = result.get("artist", "")
        year = str(result.get("release_date", "?"))[:4]
        await status.edit_text(t(uid, "found_shazam", title=s_title or "?",
            artist=s_artist or "?", album=result.get("album", "?"), year=year))
        cap = f"🎵 {s_title} — {s_artist}\n\n🤖 @nolamusicbot"
        song_key = hashlib.md5(f"{s_artist}|{s_title}".lower().encode()).hexdigest()
        cached = get_cached_audio(song_key)
        if cached:
            try:
                await bot.send_audio(uid, audio=cached, title=s_title[:64],
                                     performer=s_artist, caption=cap)
                save_cached_audio(link_key, cached)
                return
            except Exception:
                pass
        full = await ytdlp_download(s_title, s_artist)
        if full:
            msg = await bot.send_audio(uid, audio=FSInputFile(full),
                title=s_title[:64], performer=s_artist, caption=cap)
            try:
                save_cached_audio(song_key, msg.audio.file_id)
                save_cached_audio(link_key, msg.audio.file_id)
                os.remove(full)
            except Exception:
                pass
        else:
            await callback.message.answer("❌ To'liq qo'shiq topilmadi.")
        log_search(uid, url, s_title)
    except Exception as e:
        logger.error(f"getsong error: {e}")
        await status.edit_text(t(uid, "error"))

@dp.callback_query(F.data == "getaudio")
async def cb_getaudio(callback: types.CallbackQuery):
    uid = callback.from_user.id
    url = media_link_cache.get(uid)
    if not url:
        await callback.answer("Havola eskirgan, qaytadan yuboring", show_alert=True)
        return
    await callback.answer("🎧 Audio yuklanmoqda...")
    status = await callback.message.answer("⏳ Audio tayyorlanmoqda...")
    try:
        audio_path = await get_instagram_audio(url)
        if audio_path:
            await bot.send_audio(uid, audio=FSInputFile(audio_path),
                caption="🎧 Audio tayyor!\n\n🤖 @nolamusicbot")
            await status.delete()
            try:
                os.remove(audio_path)
            except Exception:
                pass
        else:
            await status.edit_text(t(uid, "instagram_error"))
    except Exception as e:
        logger.error(f"getaudio error: {e}")
        await status.edit_text(t(uid, "error"))

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
    if not await check_subscription(bot, uid):
        await callback.answer("📢 Avval kanalga obuna bo'ling!", show_alert=True)
        return
    parts = callback.data.split("|")
    idx = int(parts[2])
    results = search_cache.get(uid, [])
    if not results or idx >= len(results):
        await callback.answer("❌ Eskirgan. Qaytadan qidiring.", show_alert=True)
        return
    song = results[idx]
    status = await callback.message.answer(t(uid, "downloading"))
    try:
        title = song.get("title", "Qo'shiq")
        artist = song.get("artist", "")
        cap = f"🎵 {title} — {artist}\n\n🤖 @nolamusicbot"
        song_key = hashlib.md5(f"{artist}|{title}".lower().encode()).hexdigest()

        # 1) CACHE: oldin yuklangan bo'lsa — bir zumda yuboramiz
        cached = get_cached_audio(song_key)
        if cached:
            try:
                await bot.send_audio(uid, audio=cached, title=title[:64],
                                     performer=artist, caption=cap)
                await status.delete()
                await callback.answer()
                return
            except Exception:
                pass

        # 2) YT-DLP: to'liq qo'shiqni yuklash
        path = await ytdlp_download(title, artist)
        if path:
            msg = await bot.send_audio(
                uid, audio=FSInputFile(path),
                title=title[:64], performer=artist, caption=cap
            )
            try:
                save_cached_audio(song_key, msg.audio.file_id)
                os.remove(path)
            except Exception:
                pass
            await status.delete()
            await callback.answer()
            return

        # 3) FALLBACK: 30s preview
        preview = song.get("preview", "")
        if preview:
            await bot.send_audio(
                uid, audio=preview,
                title=title[:64], performer=artist,
                caption=f"🎵 {title} — {artist}\n{t(uid, 'preview_note')}\n\n🤖 @nolamusicbot"
            )
            await status.delete()
        else:
            url = song.get("url", "")
            await status.edit_text(f"🔗 <a href=\"{url}\">Tinglash</a>")
    except Exception as e:
        logger.error(f"DL error: {e}")
        url = song.get("url", "")
        await status.edit_text(f"🔗 <a href=\"{url}\">Tinglash</a>")
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

# ==================== ADMIN ====================
@dp.callback_query(F.data == "adm_back")
async def cb_adm_back(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text("👑 <b>Admin Panel</b>", reply_markup=admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "adm_stats")
async def cb_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    total, today_new, today_active, total_s, today_s = get_stats()
    text = (f"📊 <b>Statistika</b>\n\n"
            f"👥 Jami foydalanuvchilar: <b>{total}</b>\n"
            f"🆕 Bugun qo'shilganlar: <b>{today_new}</b>\n"
            f"🟢 Bugun faollar: <b>{today_active}</b>\n"
            f"🔍 Jami qidiruvlar: <b>{total_s}</b>\n"
            f"🔎 Bugungi qidiruvlar: <b>{today_s}</b>\n\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    await callback.message.edit_text(text, reply_markup=admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "adm_channels")
async def cb_channels(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    channels = get_channels()
    text = "📢 <b>Majburiy obuna kanallari:</b>\n\n"
    if channels:
        text += "\n".join(channels)
        text += "\n\n🗑 O'chirish uchun kanal ustiga bosing"
    else:
        text += "❌ Hozircha kanal ulanmagan"
    await callback.message.edit_text(text, reply_markup=channels_admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "addch")
async def cb_addch(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(ChannelState.waiting_channel)
    await callback.message.answer(
        "➕ Kanal username ni yuboring (masalan: <code>@mychannel</code>)\n\n"
        "⚠️ <b>Muhim:</b> Botni avval kanalga ADMIN qilib qo'shing!"
    )
    await callback.answer()

@dp.message(ChannelState.waiting_channel)
async def process_add_channel(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    username = message.text.strip()
    if not username.startswith("@"):
        username = "@" + username
    try:
        chat = await bot.get_chat(username)
        member = await bot.get_chat_member(username, bot.id)
        if member.status not in ["administrator", "creator"]:
            await message.answer(f"⚠️ Bot {username} da admin emas! Avval admin qiling, keyin qaytadan qo'shing.")
            return
        if add_channel(username):
            await message.answer(f"✅ {username} qo'shildi!", reply_markup=admin_keyboard())
        else:
            await message.answer(f"⚠️ {username} allaqachon ro'yxatda.", reply_markup=admin_keyboard())
    except Exception as e:
        logger.error(f"Add channel error: {e}")
        await message.answer(
            f"❌ Xato! {username} topilmadi yoki bot u yerda yo'q.\n"
            f"Botni kanalga ADMIN qilib qo'shing va qaytadan urinib ko'ring."
        )

@dp.callback_query(F.data.startswith("delch|"))
async def cb_delch(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    username = callback.data.split("|")[1]
    if remove_channel(username):
        await callback.answer(f"🗑 {username} o'chirildi!", show_alert=True)
        channels = get_channels()
        text = "📢 <b>Majburiy obuna kanallari:</b>\n\n"
        if channels:
            text += "\n".join(channels)
        else:
            text += "❌ Hozircha kanal ulanmagan"
        await callback.message.edit_text(text, reply_markup=channels_admin_keyboard())
    else:
        await callback.answer("❌ Xato", show_alert=True)

@dp.callback_query(F.data == "adm_bc_private")
async def cb_bc_private(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastState.waiting_message)
    await state.update_data(target="private")
    await callback.message.answer("💬 Barcha foydalanuvchilarga yuboriladigan xabarni yozing:\n(Rasm, video ham bo'ladi)")
    await callback.answer()

@dp.callback_query(F.data == "adm_bc_channel")
async def cb_bc_channel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastState.waiting_message)
    await state.update_data(target="channel")
    await callback.message.answer("📣 Ulangan kanallarga yuboriladigan xabarni yozing:")
    await callback.answer()

@dp.message(BroadcastState.waiting_message)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    await state.update_data(broadcast_msg_id=message.message_id, broadcast_chat_id=message.chat.id)
    await state.set_state(BroadcastState.confirm)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Yuborish", callback_data=f"bc_yes_{data['target']}"),
        InlineKeyboardButton(text="❌ Bekor", callback_data="bc_no"),
    ]])
    await message.answer("📤 Yuqoridagi xabarni yuborishni tasdiqlaysizmi?", reply_markup=kb)

@dp.callback_query(F.data.startswith("bc_yes_"))
async def confirm_bc(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    msg_id = data.get("broadcast_msg_id")
    chat_id = data.get("broadcast_chat_id")
    target = callback.data.replace("bc_yes_", "")
    await state.clear()
    
    sent = failed = 0
    status_msg = await callback.message.answer("📤 Yuborilmoqda...")
    
    if target == "channel":
        channels = get_channels()
        for ch in channels:
            try:
                await bot.copy_message(ch, chat_id, msg_id)
                sent += 1
            except Exception as e:
                logger.error(f"BC channel error: {e}")
                failed += 1
    else:
        users = get_all_users()
        for uid in users:
            try:
                await bot.copy_message(uid, chat_id, msg_id)
                sent += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
    
    await status_msg.edit_text(f"✅ Yuborildi: <b>{sent}</b>\n❌ Xato: <b>{failed}</b>")
    await callback.answer()

@dp.callback_query(F.data == "bc_no")
async def cancel_bc(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()

# ==================== AUDIO/VOICE ====================
@dp.message(F.audio | F.voice)
async def handle_audio(message: types.Message):
    uid = message.from_user.id
    add_user(uid, message.from_user.username, message.from_user.full_name)
    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(get_channels()))
        return
    status = await message.answer(t(uid, "recognizing"))
    try:
        file_obj = message.audio or message.voice
        file = await bot.get_file(file_obj.file_id)
        file_path = f"/tmp/audio_{uid}.ogg"
        await bot.download_file(file.file_path, destination=file_path)
        result = await recognize_from_file(file_path)
        try:
            os.remove(file_path)
        except:
            pass
        if result:
            year = str(result.get("release_date", "?"))[:4]
            await status.edit_text(t(uid, "found_shazam",
                title=result.get("title", "?"),
                artist=result.get("artist", "?"),
                album=result.get("album", "?"),
                year=year))
            query = f"{result.get('artist')} {result.get('title')}"
            songs = await search_songs(query, limit=20)
            if songs:
                search_cache[uid] = songs
                total_pages = (len(songs) + 9) // 10
                info = t(uid, "page_info", cur=1, total=total_pages, count=len(songs))
                await message.answer(info, reply_markup=results_keyboard(songs, 0, uid))
            log_search(uid, "audio", result.get("title", ""))
        else:
            await status.edit_text(t(uid, "not_found"))
    except Exception as e:
        logger.error(f"Audio error: {e}")
        await status.edit_text(t(uid, "error"))

# ==================== TEXT ====================
@dp.message(F.text)
async def handle_text(message: types.Message):
    uid = message.from_user.id
    text = message.text
    add_user(uid, message.from_user.username, message.from_user.full_name)
    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(get_channels()))
        return

    lang = get_user_lang(uid)
    ALL_MENUS = {
        "uz": ["🎵 Qo'shiq qidirish", "🌐 Til", "ℹ️ Yordam"],
        "ru": ["🎵 Поиск песни", "🌐 Язык", "ℹ️ Помощь"],
        "en": ["🎵 Search song", "🌐 Language", "ℹ️ Help"],
    }
    search_btns = [m[0] for m in ALL_MENUS.values()]
    lang_btns = [m[1] for m in ALL_MENUS.values()]
    help_btns = [m[2] for m in ALL_MENUS.values()]

    if text in search_btns:
        await message.answer(t(uid, "send_audio"), reply_markup=main_keyboard(uid))
        return
    if text in lang_btns:
        await message.answer(t(uid, "choose_lang"), reply_markup=lang_keyboard())
        return
    if text in help_btns:
        await message.answer(t(uid, "help"), reply_markup=main_keyboard(uid))
        return

    media_url = detect_media_link(text)
    if media_url:
        status = await message.answer("🔍 Qo'shiq aniqlanmoqda...")
        media_link_cache[uid] = media_url
        link_key = "lnk_" + hashlib.md5(media_url.encode()).hexdigest()

        # Cache: bu link oldin topilganmi? — bir zumda!
        cached_song = get_cached_audio(link_key)
        if cached_song:
            try:
                await bot.send_audio(uid, audio=cached_song,
                    caption="🎵 Tayyor!\n\n🤖 @nolamusicbot")
                await status.delete()
                return
            except Exception:
                pass

        try:
            # 1) Audio yuklab Shazam ga yuboramiz
            audio_path = await get_instagram_audio(media_url)
            if not audio_path:
                await status.edit_text("❌ Havola ochilmadi. Yopiq yoki xato link.")
                return

            # 2) Shazam bilan aniqlaymiz
            result = await recognize_from_file(audio_path)
            try:
                os.remove(audio_path)
            except Exception:
                pass

            if not result:
                await status.edit_text("❌ Qo'shiq tanilmadi.")
                return

            s_title = result.get("title", "?")
            s_artist = result.get("artist", "?")
            song_key = hashlib.md5(f"{s_artist}|{s_title}".lower().encode()).hexdigest()
            cap = f"🎵 {s_title} — {s_artist}\n\n🤖 @nolamusicbot"

            await status.edit_text(f"✅ Topildi: <b>{s_title}</b> — {s_artist}\n⏳ Yuklanmoqda...")

            # 3) Cache da to'liq qo'shiq bormi?
            cached = get_cached_audio(song_key)
            if cached:
                try:
                    msg = await bot.send_audio(uid, audio=cached,
                        title=s_title[:64], performer=s_artist, caption=cap)
                    save_cached_audio(link_key, cached)
                    await status.delete()
                    return
                except Exception:
                    pass

            # 4) YouTube dan to'liq 320K yuklaymiz
            full = await ytdlp_download(s_title, s_artist)
            if full:
                msg = await bot.send_audio(uid, audio=FSInputFile(full),
                    title=s_title[:64], performer=s_artist, caption=cap)
                try:
                    save_cached_audio(song_key, msg.audio.file_id)
                    save_cached_audio(link_key, msg.audio.file_id)
                    os.remove(full)
                except Exception:
                    pass
                await status.delete()
            else:
                await status.edit_text("❌ Qo'shiq yuklab bo'lmadi.")

            log_search(uid, media_url, s_title)

        except Exception as e:
            logger.error(f"Media link error: {e}")
            await status.edit_text(t(uid, "error"))
        return

    status = await message.answer(t(uid, "searching"))
    try:
        songs = await search_songs(text, limit=20)
        if songs:
            search_cache[uid] = songs
            total_pages = (len(songs) + 9) // 10
            info = t(uid, "page_info", cur=1, total=total_pages, count=len(songs))
            await status.edit_text(info, reply_markup=results_keyboard(songs, 0, uid))
            log_search(uid, text, songs[0].get("title", ""))
        else:
            await status.edit_text(t(uid, "no_result"))
    except Exception as e:
        logger.error(f"Search error: {e}")
        await status.edit_text(t(uid, "error"))

# ==================== MAIN ====================
async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start", description="🎵 Botni boshlash / Старт"),
        BotCommand(command="lang", description="🌐 Til / Язык / Language"),
        BotCommand(command="help", description="ℹ️ Yordam / Помощь / Help"),
        BotCommand(command="restart", description="🔄 Qayta boshlash / Перезапуск"),
    ])
    logger.info("🎵 Nola Bot ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
