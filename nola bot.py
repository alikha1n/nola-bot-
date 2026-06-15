import os
import sys
import re
import json
import logging
import asyncio
import aiohttp
import sqlite3
import hashlib
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# ==================== CONFIG ====================
def _load_token():
    try:
        with open("/opt/nolabot/token.txt") as f:
            return f.read().strip()
    except Exception:
        return os.environ.get("BOT_TOKEN", "")

BOT_TOKEN = _load_token()
AUDD_API_KEY = os.environ.get("AUDD_API_KEY", "fd8bde2f5e826049cf8f3f0dbef54af0")
ADMIN_IDS = [7434706702]
YTDLP = "/opt/nolabot/venv/bin/yt-dlp"

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

# Til keshi — har bir xabarda DB ga bormaslik uchun (tezlik)
_lang_cache = {}

def get_user_lang(user_id):
    if user_id in _lang_cache:
        return _lang_cache[user_id]
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    lang = row[0] if row else "uz"
    _lang_cache[user_id] = lang
    return lang

def set_user_lang(user_id, lang):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, user_id))
    conn.commit()
    conn.close()
    _lang_cache[user_id] = lang

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
    except Exception:
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
        "welcome": "🎵 <b>Nola Bot</b>ga xush kelibsiz!\n\nQo'shiq nomini yozing, audio yuboring yoki Instagram/TikTok link tashlang — to'liq qo'shiqni topib beraman!",
        "choose_lang": "🌐 Tilni tanlang:",
        "lang_set": "✅ Til o'zgartirildi!",
        "send_audio": "🎵 Qo'shiq nomini yozing, audio yoki Instagram/TikTok link yuboring!",
        "searching": "🔍 Qidirilmoqda...",
        "recognizing": "🎵 Qo'shiq tanilmoqda...",
        "found_shazam": "✅ Topildi: <b>{title}</b> — {artist}\n⏳ Yuklanmoqda...",
        "not_found": "❌ Qo'shiq topilmadi.",
        "downloading": "⬇️ Yuklanmoqda...",
        "help": "ℹ️ <b>Yordam</b>\n\n• Qo'shiq nomini yozing → ro'yxatdan tanlang\n• Audio yuboring → taniydi\n• Instagram/TikTok link → qo'shiqni topadi\n• /lang → til",
        "error": "⚠️ Xatolik. Qaytadan urinib ko'ring.",
        "no_result": "❌ Natija topilmadi.",
        "page_info": "📄 Sahifa {cur}/{total} — {count} ta natija",
        "instagram_error": "❌ Linkdan qo'shiq topilmadi.",
        "dl_fail": "❌ Qo'shiq yuklab bo'lmadi.",
        "sub_required": "📢 Botdan foydalanish uchun kanal(lar)ga obuna bo'ling:",
        "sub_ok": "✅ Rahmat! Botdan foydalanishingiz mumkin.",
        "sub_fail": "❌ Hali obuna bo'lmagansiz.",
        "expired": "❌ Eskirgan. Qaytadan qidiring.",
    },
    "ru": {
        "welcome": "🎵 Добро пожаловать в <b>Nola Bot</b>!\n\nНапишите название песни, отправьте аудио или ссылку Instagram/TikTok — найду полную песню!",
        "choose_lang": "🌐 Выберите язык:",
        "lang_set": "✅ Язык изменён!",
        "send_audio": "🎵 Напишите название, отправьте аудио или ссылку Instagram/TikTok!",
        "searching": "🔍 Поиск...",
        "recognizing": "🎵 Распознаю...",
        "found_shazam": "✅ Найдено: <b>{title}</b> — {artist}\n⏳ Загрузка...",
        "not_found": "❌ Не найдена.",
        "downloading": "⬇️ Загрузка...",
        "help": "ℹ️ Напишите название, отправьте аудио или ссылку!\n/lang — язык",
        "error": "⚠️ Ошибка.",
        "no_result": "❌ Не найдено.",
        "page_info": "📄 Страница {cur}/{total} — {count} результатов",
        "instagram_error": "❌ Не найдено из ссылки.",
        "dl_fail": "❌ Не удалось загрузить.",
        "sub_required": "📢 Подпишитесь на канал(ы):",
        "sub_ok": "✅ Спасибо!",
        "sub_fail": "❌ Не подписались.",
        "expired": "❌ Устарело. Поищите снова.",
    },
    "en": {
        "welcome": "🎵 Welcome to <b>Nola Bot</b>!\n\nType a song name, send audio or an Instagram/TikTok link — I'll find the full song!",
        "choose_lang": "🌐 Choose language:",
        "lang_set": "✅ Language changed!",
        "send_audio": "🎵 Type a song name, send audio or an Instagram/TikTok link!",
        "searching": "🔍 Searching...",
        "recognizing": "🎵 Recognizing...",
        "found_shazam": "✅ Found: <b>{title}</b> — {artist}\n⏳ Downloading...",
        "not_found": "❌ Not found.",
        "downloading": "⬇️ Downloading...",
        "help": "ℹ️ Type a song name, send audio or a link!\n/lang — language",
        "error": "⚠️ Error.",
        "no_result": "❌ No results.",
        "page_info": "📄 Page {cur}/{total} — {count} results",
        "instagram_error": "❌ Not found from link.",
        "dl_fail": "❌ Could not download.",
        "sub_required": "📢 Subscribe to channel(s):",
        "sub_ok": "✅ Thank you!",
        "sub_fail": "❌ Not subscribed.",
        "expired": "❌ Expired. Search again.",
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
        title = (r.get("title") or "")[:30]
        artist = (r.get("artist") or "")[:15]
        dur = r.get("duration", "")
        label = f"🎵 {i-start+1}. {title}"
        if artist:
            label += f" — {artist}"
        if dur:
            label += f" [{dur}]"
        buttons.append([InlineKeyboardButton(text=label[:64], callback_data=f"dl|{i}")])
    nav = []
    total_pages = (len(results) + 9) // 10
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pg|{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pg|{page+1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== IN-MEMORY CACHE ====================
search_cache = {}        # uid -> [results]
media_link_cache = {}    # uid -> url

# Shared HTTP session (tezlik uchun)
_http_session: aiohttp.ClientSession | None = None

def http() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session

# ==================== YOUTUBE QIDIRUV ====================
async def youtube_search(query: str, limit=10) -> list:
    """yt-dlp orqali YouTube'dan qidiradi (yuklamasdan, faqat ro'yxat)."""
    cmd = [
        YTDLP, f"ytsearch{limit}:{query}",
        "--flat-playlist", "-J", "--no-warnings",
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
        data = json.loads(stdout.decode())
        results = []
        for e in data.get("entries", []):
            if not e:
                continue
            dur = e.get("duration") or 0
            try:
                dur = int(dur)
            except Exception:
                dur = 0
            results.append({
                "title": e.get("title", ""),
                "artist": e.get("uploader") or e.get("channel") or "",
                "duration": f"{dur//60}:{dur%60:02d}" if dur else "",
                "id": e.get("id", ""),
            })
        return results
    except asyncio.TimeoutError:
        if proc:
            try: proc.kill()
            except Exception: pass
    except Exception as e:
        logger.error(f"YT search error: {e}")
    return []

async def ytdlp_download_url(url: str, key: str) -> str | None:
    """Berilgan YouTube URL/ID dan to'liq MP3 yuklaydi."""
    out = f"/tmp/dl_{key}.mp3"
    if os.path.exists(out) and os.path.getsize(out) > 100_000:
        return out
    cmd = [
        YTDLP, "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "-f", "bestaudio/best",
        "--no-playlist", "--no-warnings", "--max-filesize", "50M",
        "--concurrent-fragments", "8", "-N", "8",
        "-o", f"/tmp/dl_{key}.%(ext)s", url,
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
        if os.path.exists(out) and os.path.getsize(out) > 100_000:
            return out
    except asyncio.TimeoutError:
        if proc:
            try: proc.kill()
            except Exception: pass
    except Exception as e:
        logger.error(f"ytdlp dl error: {e}")
    return None

async def ytdlp_download_query(title: str, artist: str) -> str | None:
    """Qo'shiq nomi bo'yicha YouTube'dan qidirib MP3 yuklaydi (Shazam natijasi uchun)."""
    query = f"{artist} {title}".strip()
    h = hashlib.md5(query.lower().encode()).hexdigest()[:16]
    return await ytdlp_download_url(f"ytsearch1:{query}", h)

# ==================== AUDD (SHAZAM) ====================
async def recognize_from_file(file_path: str) -> dict | None:
    try:
        with open(file_path, "rb") as f:
            data = aiohttp.FormData()
            data.add_field("api_token", AUDD_API_KEY)
            data.add_field("return", "apple_music,spotify")
            data.add_field("file", f, filename="audio.mp3")
            async with http().post("https://api.audd.io/", data=data,
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
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
        logger.error(f"AudD file error: {e}")
    return None

async def recognize_from_url(url: str) -> dict | None:
    try:
        data = aiohttp.FormData()
        data.add_field("api_token", AUDD_API_KEY)
        data.add_field("url", url)
        async with http().post("https://api.audd.io/", data=data,
                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
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

# ==================== INSTAGRAM / TIKTOK ====================
def detect_media_link(text: str) -> str | None:
    """Faqat Instagram va TikTok havolalarini aniqlaydi (YouTube YO'Q)."""
    patterns = [
        r'(https?://)?(www\.|vm\.|vt\.)?(tiktok\.com/\S+)',
        r'(https?://)?(www\.)?(instagram\.com/(p|reel|tv|reels)/[\w-]+\S*)',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            url = m.group(0)
            if not url.startswith("http"):
                url = "https://" + url
            return url
    return None

async def get_media_audio(url: str) -> str | None:
    """IG/TikTok dan audioni yuklaydi. Bir nechta usul bilan urinadi."""
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    out = f"/tmp/ig_{h}.mp3"
    if os.path.exists(out) and os.path.getsize(out) > 5_000:
        return out

    attempts = [
        [YTDLP, "-x", "--audio-format", "mp3", "--audio-quality", "0",
         "-f", "bestaudio/best", "--no-playlist", "--no-warnings",
         "--no-check-certificate", "--max-filesize", "50M",
         "--concurrent-fragments", "8", "-N", "8",
         "-o", f"/tmp/ig_{h}.%(ext)s", url],
        [YTDLP, "-x", "--audio-format", "mp3", "--audio-quality", "0",
         "-f", "bestaudio/best", "--no-playlist", "--no-warnings",
         "--no-check-certificate", "--max-filesize", "50M",
         "--add-header", "User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
         "--add-header", "Referer:https://www.instagram.com/",
         "-o", f"/tmp/ig_{h}.%(ext)s", url],
        [YTDLP, "-x", "--audio-format", "mp3",
         "--no-playlist", "--no-check-certificate",
         "-o", f"/tmp/ig_{h}.%(ext)s", url],
    ]

    for cmd in attempts:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=60)
            if os.path.exists(out) and os.path.getsize(out) > 5_000:
                return out
        except asyncio.TimeoutError:
            if proc:
                try: proc.kill()
                except Exception: pass
        except Exception as e:
            logger.error(f"IG attempt error: {e}")
    return None

# ==================== SUBSCRIPTION ====================
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

# ==================== LOADING ANIMATSIYA ====================
CLOCKS = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]

async def animate_loading(message, base_text: str):
    """Soat aylanib turadi (xabarni qayta-qayta tahrirlash orqali)."""
    i = 0
    try:
        while True:
            clock = CLOCKS[i % len(CLOCKS)]
            try:
                await message.edit_text(f"{clock} {base_text}")
            except Exception:
                pass
            i += 1
            await asyncio.sleep(0.6)
    except asyncio.CancelledError:
        return

async def stop_anim(task):
    """Animatsiya taskini xavfsiz to'xtatadi."""
    if task:
        task.cancel()
        try:
            await task
        except Exception:
            pass

# ==================== ORTAQ: qo'shiq yuborish ====================
async def send_full_song(uid: int, title: str, artist: str, extra_key: str | None = None) -> bool:
    """Cache'dan yoki YouTube'dan to'liq qo'shiqni topib yuboradi. True = muvaffaqiyat."""
    cap = f"🎵 {title} — {artist}\n\n🤖 @nolamusicbot"
    song_key = hashlib.md5(f"{artist}|{title}".lower().encode()).hexdigest()

    # 1) Cache
    cached = get_cached_audio(song_key)
    if cached:
        try:
            await bot.send_audio(uid, audio=cached, title=title[:64],
                                 performer=artist, caption=cap)
            if extra_key:
                save_cached_audio(extra_key, cached)
            return True
        except Exception:
            pass

    # 2) YouTube'dan yuklab yuborish
    path = await ytdlp_download_query(title, artist)
    if path:
        try:
            msg = await bot.send_audio(uid, audio=FSInputFile(path),
                                       title=title[:64], performer=artist, caption=cap)
            save_cached_audio(song_key, msg.audio.file_id)
            if extra_key:
                save_cached_audio(extra_key, msg.audio.file_id)
        except Exception as e:
            logger.error(f"send_full_song error: {e}")
            return False
        finally:
            try: os.remove(path)
            except Exception: pass
        return True
    return False

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
        await message.answer("🔄 Bot qayta ishga tushmoqda...")
        await asyncio.sleep(1)
        try:
            if _http_session and not _http_session.closed:
                await _http_session.close()
        except Exception:
            pass
        try:
            await bot.session.close()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)
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
    await callback.message.answer(t(callback.from_user.id, "welcome"),
                                  reply_markup=main_keyboard(callback.from_user.id))
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
    if not await check_subscription(bot, uid):
        await callback.answer("📢 Avval kanalga obuna bo'ling!", show_alert=True)
        return
    idx = int(callback.data.split("|")[1])
    results = search_cache.get(uid, [])
    if not results or idx >= len(results):
        await callback.answer(t(uid, "expired"), show_alert=True)
        return
    song = results[idx]
    await callback.answer()
    status = await callback.message.answer("🕐 Yuklanmoqda...")
    anim = asyncio.create_task(animate_loading(status, "Yuklanmoqda..."))
    try:
        title = song.get("title", "Qo'shiq")
        artist = song.get("artist", "")
        vid = song.get("id", "")
        cap = f"🎵 {title} — {artist}\n\n🤖 @nolamusicbot"
        song_key = hashlib.md5(f"{artist}|{title}".lower().encode()).hexdigest()

        # 1) Cache
        cached = get_cached_audio(song_key)
        if cached:
            try:
                await bot.send_audio(uid, audio=cached, title=title[:64],
                                     performer=artist, caption=cap)
                await stop_anim(anim)
                await status.delete()
                return
            except Exception:
                pass

        # 2) Aniq video ID bo'yicha yuklash (qidiruvda tanlangan natija)
        path = None
        if vid:
            path = await ytdlp_download_url(f"https://www.youtube.com/watch?v={vid}", vid)
        if not path:
            path = await ytdlp_download_query(title, artist)

        await stop_anim(anim)
        if path:
            msg = await bot.send_audio(uid, audio=FSInputFile(path),
                                       title=title[:64], performer=artist, caption=cap)
            try:
                save_cached_audio(song_key, msg.audio.file_id)
                os.remove(path)
            except Exception:
                pass
            await status.delete()
        else:
            await status.edit_text(t(uid, "dl_fail"))
        log_search(uid, title, title)
    except Exception as e:
        logger.error(f"DL error: {e}")
        await stop_anim(anim)
        await status.edit_text(t(uid, "error"))

@dp.callback_query(F.data.startswith("pg|"))
async def cb_page(callback: types.CallbackQuery):
    uid = callback.from_user.id
    page = int(callback.data.split("|")[1])
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
        await bot.get_chat(username)
        member = await bot.get_chat_member(username, bot.id)
        if member.status not in ["administrator", "creator"]:
            await message.answer(f"⚠️ Bot {username} da admin emas! Avval admin qiling.")
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
        text += "\n".join(channels) if channels else "❌ Hozircha kanal ulanmagan"
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
        for ch in get_channels():
            try:
                await bot.copy_message(ch, chat_id, msg_id)
                sent += 1
            except Exception as e:
                logger.error(f"BC channel error: {e}")
                failed += 1
    else:
        for uid in get_all_users():
            try:
                await bot.copy_message(uid, chat_id, msg_id)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

    await status_msg.edit_text(f"✅ Yuborildi: <b>{sent}</b>\n❌ Xato: <b>{failed}</b>")
    await callback.answer()

@dp.callback_query(F.data == "bc_no")
async def cancel_bc(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()

# ==================== AUDIO/VOICE → SHAZAM ====================
@dp.message(F.audio | F.voice)
async def handle_audio(message: types.Message):
    uid = message.from_user.id
    add_user(uid, message.from_user.username, message.from_user.full_name)
    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(get_channels()))
        return
    status = await message.answer("🕐 Qo'shiq tanilmoqda...")
    anim = asyncio.create_task(animate_loading(status, "Qo'shiq tanilmoqda..."))
    file_path = f"/tmp/audio_{uid}_{message.message_id}.ogg"
    try:
        file_obj = message.audio or message.voice
        file = await bot.get_file(file_obj.file_id)
        await bot.download_file(file.file_path, destination=file_path)
        result = await recognize_from_file(file_path)
        try: os.remove(file_path)
        except Exception: pass

        if not result:
            await stop_anim(anim)
            await status.edit_text(t(uid, "not_found"))
            return

        s_title = result.get("title", "?")
        s_artist = result.get("artist", "?")
        await stop_anim(anim)
        await status.edit_text(t(uid, "found_shazam", title=s_title, artist=s_artist))
        anim = asyncio.create_task(animate_loading(status, f"{s_title} — {s_artist} yuklanmoqda..."))
        ok = await send_full_song(uid, s_title, s_artist)
        await stop_anim(anim)
        if ok:
            await status.delete()
        else:
            await status.edit_text(t(uid, "dl_fail"))
        log_search(uid, "audio", s_title)
    except Exception as e:
        logger.error(f"Audio error: {e}")
        await stop_anim(anim)
        await status.edit_text(t(uid, "error"))

# ==================== TEXT / LINK ====================
@dp.message(F.text)
async def handle_text(message: types.Message):
    uid = message.from_user.id
    text = message.text
    add_user(uid, message.from_user.username, message.from_user.full_name)
    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(get_channels()))
        return

    # Menyu tugmalari
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

    # ---- Instagram / TikTok LINK → SHAZAM ----
    media_url = detect_media_link(text)
    if media_url:
        status = await message.answer("🕐 Qo'shiq aniqlanmoqda...")
        anim = asyncio.create_task(animate_loading(status, "Qo'shiq aniqlanmoqda..."))
        link_key = "lnk_" + hashlib.md5(media_url.encode()).hexdigest()
        media_link_cache[uid] = media_url

        # Cache: shu link oldin topilganmi?
        cached_song = get_cached_audio(link_key)
        if cached_song:
            try:
                await bot.send_audio(uid, audio=cached_song, caption="🎵 Tayyor!\n\n🤖 @nolamusicbot")
                await stop_anim(anim)
                await status.delete()
                return
            except Exception:
                pass

        try:
            # 1) AudD ga URL ni to'g'ridan-to'g'ri (tez)
            result = await recognize_from_url(media_url)
            # 2) Bo'lmasa — audio yuklab AudD ga
            if not result:
                audio_path = await get_media_audio(media_url)
                if audio_path:
                    result = await recognize_from_file(audio_path)
                    try: os.remove(audio_path)
                    except Exception: pass

            if not result:
                await stop_anim(anim)
                await status.edit_text(t(uid, "instagram_error"))
                return

            s_title = result.get("title", "?")
            s_artist = result.get("artist", "?")
            await stop_anim(anim)
            await status.edit_text(t(uid, "found_shazam", title=s_title, artist=s_artist))
            anim = asyncio.create_task(animate_loading(status, f"{s_title} — {s_artist} yuklanmoqda..."))
            ok = await send_full_song(uid, s_title, s_artist, extra_key=link_key)
            await stop_anim(anim)
            if ok:
                await status.delete()
            else:
                await status.edit_text(t(uid, "dl_fail"))
            log_search(uid, media_url, s_title)
        except Exception as e:
            logger.error(f"Media link error: {e}")
            await stop_anim(anim)
            await status.edit_text(t(uid, "error"))
        return

    # ---- Oddiy matn = QO'SHIQ NOMI → YouTube qidiruv ----
    status = await message.answer(t(uid, "searching"))
    try:
        songs = await youtube_search(text, limit=20)
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
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN topilmadi! token.txt yoki BOT_TOKEN env kerak.")
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
    try:
        await dp.start_polling(bot)
    finally:
        if _http_session and not _http_session.closed:
            await _http_session.close()

if __name__ == "__main__":
    asyncio.run(main())
