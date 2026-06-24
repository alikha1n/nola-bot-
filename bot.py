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
try:
    from shazamio import Shazam
    _shazam = Shazam()
    _HAS_SHAZAMIO = True
except Exception:
    _shazam = None
    _HAS_SHAZAMIO = False
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile,
    InlineQueryResultArticle, InputTextMessageContent,
    InlineQueryResultCachedAudio,
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
BOT_USERNAME = "nolamusicbot"  # main() da bot.get_me() bilan yangilanadi
YTDLP = "/opt/nolabot/venv/bin/yt-dlp"
_COOKIE_FILE = "/opt/nolabot/cookies.txt"
COOKIES = _COOKIE_FILE if os.path.exists(_COOKIE_FILE) else None
# Instagram uchun ALOHIDA cookie fayli (login talab qiladi).
# Bo'lmasa — umumiy cookies.txt ishlatiladi.
_IG_COOKIE_FILE = "/opt/nolabot/ig_cookies.txt"
IG_COOKIES = _IG_COOKIE_FILE if os.path.exists(_IG_COOKIE_FILE) else None

def ck():
    """yt-dlp uchun umumiy argumentlar (qidiruv va yuklash uchun ham xavfsiz):
    - cookie fayli bor bo'lsa --cookies
    - YouTube bot-tekshiruvini chetlab o'tish uchun android/ios/web klientlari
    Eslatma: user-agent BU YERGA QO'SHILMAYDI, chunki u qidiruvni buzadi.
    User-agent faqat yuklash (download) funksiyalarida ishlatiladi.
    """
    args = []
    if COOKIES:
        args += ["--cookies", COOKIES]
    args += ["--extractor-args", "youtube:player_client=android,ios,web"]
    return args

def dl_args(url: str):
    """Link turiga qarab yt-dlp argumentlari.
    - YouTube → cookie + youtube client argumentlari
    - Instagram → ig_cookies.txt (bo'lsa), aks holda umumiy cookie
    - TikTok → faqat umumiy cookie (login shart emas)
    """
    url = url or ""
    is_yt = bool(re.search(r'youtube\.com|youtu\.be', url))
    is_ig = "instagram.com" in url
    cookie = IG_COOKIES if (is_ig and IG_COOKIES) else COOKIES
    args = []
    if cookie:
        args += ["--cookies", cookie]
    if is_yt:
        args += ["--extractor-args", "youtube:player_client=android,ios,web"]
    return args

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
    # Qo'shimcha adminlar (tugma orqali qo'shiladiganlar)
    c.execute("""CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER UNIQUE,
        added_at TEXT
    )""")
    # Platforma yuklash sanog'i (Instagram / TikTok / YouTube)
    c.execute("""CREATE TABLE IF NOT EXISTS link_stats (
        platform TEXT UNIQUE,
        cnt INTEGER DEFAULT 0
    )""")
    # Migratsiya: eski bazaga 'source' (reklama manbasi) ustunini qo'shamiz
    try:
        c.execute("ALTER TABLE users ADD COLUMN source TEXT")
    except Exception:
        pass  # ustun allaqachon bor
    conn.commit()
    conn.close()

# ---------- Adminlar (DB orqali) ----------
def get_db_admins():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("SELECT user_id FROM admins")
        rows = [r[0] for r in c.fetchall()]
    except Exception:
        rows = []
    conn.close()
    return rows

def is_admin(user_id):
    """Asosiy ADMIN_IDS yoki tugma orqali qo'shilgan adminlar."""
    return user_id in ADMIN_IDS or user_id in get_db_admins()

def add_admin(user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("INSERT OR IGNORE INTO admins (user_id, added_at) VALUES (?,?)",
                  (user_id, datetime.now().isoformat()))
        conn.commit()
        ok = c.rowcount > 0
    except Exception:
        ok = False
    conn.close()
    return ok

def remove_admin(user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    ok = c.rowcount > 0
    conn.close()
    return ok

# ---------- Platforma yuklash statistikasi ----------
def bump_platform(url: str):
    """Link turini aniqlab, sanog'ini +1 qiladi."""
    u = (url or "").lower()
    if "instagram.com" in u:
        p = "Instagram"
    elif "tiktok.com" in u:
        p = "TikTok"
    elif "youtube.com" in u or "youtu.be" in u:
        p = "YouTube"
    else:
        p = "Boshqa"
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("INSERT OR IGNORE INTO link_stats (platform, cnt) VALUES (?, 0)", (p,))
        c.execute("UPDATE link_stats SET cnt = cnt + 1 WHERE platform=?", (p,))
        conn.commit()
    except Exception:
        pass
    conn.close()

def get_link_stats():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("SELECT platform, cnt FROM link_stats ORDER BY cnt DESC")
        rows = c.fetchall()
    except Exception:
        rows = []
    conn.close()
    return rows

def get_top_songs(limit=10):
    """Eng ko'p qidirilgan/yuklangan qo'shiqlar."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("""SELECT result, COUNT(*) AS n FROM searches
                     WHERE result IS NOT NULL AND result != ''
                       AND result NOT LIKE 'http%' AND result != 'audio'
                     GROUP BY result ORDER BY n DESC LIMIT ?""", (limit,))
        rows = c.fetchall()
    except Exception:
        rows = []
    conn.close()
    return rows
def get_source_stats():
    """Har bir ?start=manba bo'yicha nechta foydalanuvchi kelganini qaytaradi."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("""SELECT COALESCE(source,'(to''g''ridan)') AS s, COUNT(*)
                     FROM users GROUP BY s ORDER BY COUNT(*) DESC""")
        rows = c.fetchall()
    except Exception:
        rows = []
    conn.close()
    return rows

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

def add_user(user_id, username, full_name, source=None):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # source faqat YANGI foydalanuvchida yoziladi (INSERT OR IGNORE)
    c.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, joined_at, last_active, source) VALUES (?, ?, ?, ?, ?, ?)",
              (user_id, username, full_name, now, now, source))
    # Takror kelganda source O'ZGARTIRILMAYDI — faqat faollik yangilanadi
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
        "found_shazam_btn": "✅ Topildi: <b>{title}</b> — {artist}\n\n⬇️ Yuklash uchun tugmani bosing:",
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
        "found_shazam_btn": "✅ Найдено: <b>{title}</b> — {artist}\n\n⬇️ Нажмите кнопку для загрузки:",
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
        "found_shazam_btn": "✅ Found: <b>{title}</b> — {artist}\n\n⬇️ Tap the button to download:",
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
    },
    "kk": {
        "welcome": "🎵 <b>Nola Bot</b>-қа қош келдіңіз!\n\nӘн атауын жазыңыз, аудио немесе Instagram/TikTok сілтемесін жіберіңіз — толық әнді тауып беремін!",
        "choose_lang": "🌐 Тілді таңдаңыз:",
        "lang_set": "✅ Тіл өзгертілді!",
        "send_audio": "🎵 Ән атауын жазыңыз, аудио немесе Instagram/TikTok сілтемесін жіберіңіз!",
        "searching": "🔍 Ізделуде...",
        "recognizing": "🎵 Ән танылуда...",
        "found_shazam": "✅ Табылды: <b>{title}</b> — {artist}\n⏳ Жүктелуде...",
        "found_shazam_btn": "✅ Табылды: <b>{title}</b> — {artist}\n\n⬇️ Жүктеу үшін батырманы басыңыз:",
        "not_found": "❌ Ән табылмады.",
        "downloading": "⬇️ Жүктелуде...",
        "help": "ℹ️ Ән атауын жазыңыз, аудио немесе сілтеме жіберіңіз!\n/lang — тіл",
        "error": "⚠️ Қате. Қайталап көріңіз.",
        "no_result": "❌ Нәтиже табылмады.",
        "page_info": "📄 Бет {cur}/{total} — {count} нәтиже",
        "instagram_error": "❌ Сілтемеден ән табылмады.",
        "dl_fail": "❌ Әнді жүктеу мүмкін болмады.",
        "sub_required": "📢 Ботты пайдалану үшін канал(дар)ға жазылыңыз:",
        "sub_ok": "✅ Рахмет! Ботты пайдалана аласыз.",
        "sub_fail": "❌ Әлі жазылмадыңыз.",
        "expired": "❌ Ескірген. Қайта іздеңіз.",
    },
    "ky": {
        "welcome": "🎵 <b>Nola Bot</b>ко кош келиңиз!\n\nЫр аталышын жазыңыз, аудио же Instagram/TikTok шилтемесин жибериңиз — толук ырды таап беремин!",
        "choose_lang": "🌐 Тилди тандаңыз:",
        "lang_set": "✅ Тил өзгөртүлдү!",
        "send_audio": "🎵 Ыр аталышын жазыңыз, аудио же Instagram/TikTok шилтемесин жибериңиз!",
        "searching": "🔍 Изделүүдө...",
        "recognizing": "🎵 Ыр таанылууда...",
        "found_shazam": "✅ Табылды: <b>{title}</b> — {artist}\n⏳ Жүктөлүүдө...",
        "found_shazam_btn": "✅ Табылды: <b>{title}</b> — {artist}\n\n⬇️ Жүктөө үчүн баскычты басыңыз:",
        "not_found": "❌ Ыр табылган жок.",
        "downloading": "⬇️ Жүктөлүүдө...",
        "help": "ℹ️ Ыр аталышын жазыңыз, аудио же шилтеме жибериңиз!\n/lang — тил",
        "error": "⚠️ Ката. Кайра аракет кылыңыз.",
        "no_result": "❌ Натыйжа табылган жок.",
        "page_info": "📄 Барак {cur}/{total} — {count} натыйжа",
        "instagram_error": "❌ Шилтемеден ыр табылган жок.",
        "dl_fail": "❌ Ырды жүктөө мүмкүн болбоду.",
        "sub_required": "📢 Ботту колдонуу үчүн канал(дар)га жазылыңыз:",
        "sub_ok": "✅ Рахмат! Ботту колдонсоңуз болот.",
        "sub_fail": "❌ Азырынча жазылган жоксуз.",
        "expired": "❌ Эскирген. Кайра издеңиз.",
    },
    "tg": {
        "welcome": "🎵 Хуш омадед ба <b>Nola Bot</b>!\n\nНоми сурудро нависед, аудио ё пайванди Instagram/TikTok фиристед — суруди пурраро меёбам!",
        "choose_lang": "🌐 Забонро интихоб кунед:",
        "lang_set": "✅ Забон иваз шуд!",
        "send_audio": "🎵 Номи сурудро нависед, аудио ё пайванди Instagram/TikTok фиристед!",
        "searching": "🔍 Ҷустуҷӯ...",
        "recognizing": "🎵 Суруд шинохта мешавад...",
        "found_shazam": "✅ Ёфт шуд: <b>{title}</b> — {artist}\n⏳ Боргирӣ...",
        "found_shazam_btn": "✅ Ёфт шуд: <b>{title}</b> — {artist}\n\n⬇️ Барои боргирӣ тугмаро пахш кунед:",
        "not_found": "❌ Суруд ёфт нашуд.",
        "downloading": "⬇️ Боргирӣ...",
        "help": "ℹ️ Номи суруд, аудио ё пайванд фиристед!\n/lang — забон",
        "error": "⚠️ Хатогӣ. Аз нав кӯшиш кунед.",
        "no_result": "❌ Натиҷа ёфт нашуд.",
        "page_info": "📄 Саҳифа {cur}/{total} — {count} натиҷа",
        "instagram_error": "❌ Аз пайванд суруд ёфт нашуд.",
        "dl_fail": "❌ Сурудро боргирӣ карда нашуд.",
        "sub_required": "📢 Барои истифода ба канал(ҳо) обуна шавед:",
        "sub_ok": "✅ Ташаккур! Метавонед истифода баред.",
        "sub_fail": "❌ Ҳанӯз обуна нашудаед.",
        "expired": "❌ Кӯҳна шуд. Аз нав ҷустуҷӯ кунед.",
    },
    "tr": {
        "welcome": "🎵 <b>Nola Bot</b>'a hoş geldiniz!\n\nŞarkı adını yazın, ses gönderin veya Instagram/TikTok bağlantısı gönderin — şarkının tamamını bulurum!",
        "choose_lang": "🌐 Dil seçin:",
        "lang_set": "✅ Dil değiştirildi!",
        "send_audio": "🎵 Şarkı adını yazın, ses veya Instagram/TikTok bağlantısı gönderin!",
        "searching": "🔍 Aranıyor...",
        "recognizing": "🎵 Şarkı tanınıyor...",
        "found_shazam": "✅ Bulundu: <b>{title}</b> — {artist}\n⏳ İndiriliyor...",
        "found_shazam_btn": "✅ Bulundu: <b>{title}</b> — {artist}\n\n⬇️ İndirmek için butona dokunun:",
        "not_found": "❌ Şarkı bulunamadı.",
        "downloading": "⬇️ İndiriliyor...",
        "help": "ℹ️ Şarkı adı, ses veya bağlantı gönderin!\n/lang — dil",
        "error": "⚠️ Hata. Tekrar deneyin.",
        "no_result": "❌ Sonuç bulunamadı.",
        "page_info": "📄 Sayfa {cur}/{total} — {count} sonuç",
        "instagram_error": "❌ Bağlantıdan şarkı bulunamadı.",
        "dl_fail": "❌ Şarkı indirilemedi.",
        "sub_required": "📢 Kullanmak için kanal(lar)a abone olun:",
        "sub_ok": "✅ Teşekkürler! Kullanabilirsiniz.",
        "sub_fail": "❌ Henüz abone olmadınız.",
        "expired": "❌ Süresi doldu. Tekrar arayın.",
    },
    "tk": {
        "welcome": "🎵 <b>Nola Bot</b>-a hoş geldiňiz!\n\nAýdym adyny ýazyň, ses ýa-da Instagram/TikTok salgysyny iberiň — doly aýdymy tapyp bererin!",
        "choose_lang": "🌐 Dili saýlaň:",
        "lang_set": "✅ Dil üýtgedildi!",
        "send_audio": "🎵 Aýdym adyny ýazyň, ses ýa-da Instagram/TikTok salgysyny iberiň!",
        "searching": "🔍 Gözlenýär...",
        "recognizing": "🎵 Aýdym tanalýar...",
        "found_shazam": "✅ Tapyldy: <b>{title}</b> — {artist}\n⏳ Ýüklenýär...",
        "found_shazam_btn": "✅ Tapyldy: <b>{title}</b> — {artist}\n\n⬇️ Ýüklemek üçin düwmäni basyň:",
        "not_found": "❌ Aýdym tapylmady.",
        "downloading": "⬇️ Ýüklenýär...",
        "help": "ℹ️ Aýdym ady, ses ýa-da salgy iberiň!\n/lang — dil",
        "error": "⚠️ Ýalňyşlyk. Gaýtadan synanyşyň.",
        "no_result": "❌ Netije tapylmady.",
        "page_info": "📄 Sahypa {cur}/{total} — {count} netije",
        "instagram_error": "❌ Salgydan aýdym tapylmady.",
        "dl_fail": "❌ Aýdymy ýükläp bolmady.",
        "sub_required": "📢 Ulanmak üçin kanal(lar)a agza boluň:",
        "sub_ok": "✅ Sag boluň! Ulanyp bilersiňiz.",
        "sub_fail": "❌ Heniz agza bolmadyňyz.",
        "expired": "❌ Köneldi. Gaýtadan gözläň.",
    },
    "az": {
        "welcome": "🎵 <b>Nola Bot</b>-a xoş gəlmisiniz!\n\nMahnı adını yazın, audio və ya Instagram/TikTok linki göndərin — tam mahnını tapıram!",
        "choose_lang": "🌐 Dil seçin:",
        "lang_set": "✅ Dil dəyişdirildi!",
        "send_audio": "🎵 Mahnı adını yazın, audio və ya Instagram/TikTok linki göndərin!",
        "searching": "🔍 Axtarılır...",
        "recognizing": "🎵 Mahnı tanınır...",
        "found_shazam": "✅ Tapıldı: <b>{title}</b> — {artist}\n⏳ Yüklənir...",
        "found_shazam_btn": "✅ Tapıldı: <b>{title}</b> — {artist}\n\n⬇️ Yükləmək üçün düyməni basın:",
        "not_found": "❌ Mahnı tapılmadı.",
        "downloading": "⬇️ Yüklənir...",
        "help": "ℹ️ Mahnı adı, audio və ya link göndərin!\n/lang — dil",
        "error": "⚠️ Xəta. Yenidən cəhd edin.",
        "no_result": "❌ Nəticə tapılmadı.",
        "page_info": "📄 Səhifə {cur}/{total} — {count} nəticə",
        "instagram_error": "❌ Linkdən mahnı tapılmadı.",
        "dl_fail": "❌ Mahnı yüklənə bilmədi.",
        "sub_required": "📢 İstifadə üçün kanal(lar)a abunə olun:",
        "sub_ok": "✅ Təşəkkürlər! İstifadə edə bilərsiniz.",
        "sub_fail": "❌ Hələ abunə olmamısınız.",
        "expired": "❌ Vaxtı keçib. Yenidən axtarın.",
    },
    "ar": {
        "welcome": "🎵 مرحبًا بك في <b>Nola Bot</b>!\n\nاكتب اسم الأغنية، أرسل مقطعًا صوتيًا أو رابط Instagram/TikTok — سأجد الأغنية كاملة!",
        "choose_lang": "🌐 اختر اللغة:",
        "lang_set": "✅ تم تغيير اللغة!",
        "send_audio": "🎵 اكتب اسم الأغنية، أرسل صوتًا أو رابط Instagram/TikTok!",
        "searching": "🔍 جاري البحث...",
        "recognizing": "🎵 جاري التعرف على الأغنية...",
        "found_shazam": "✅ تم العثور: <b>{title}</b> — {artist}\n⏳ جاري التحميل...",
        "found_shazam_btn": "✅ تم العثور: <b>{title}</b> — {artist}\n\n⬇️ اضغط الزر للتحميل:",
        "not_found": "❌ لم يتم العثور على الأغنية.",
        "downloading": "⬇️ جاري التحميل...",
        "help": "ℹ️ أرسل اسم الأغنية أو صوتًا أو رابطًا!\n/lang — اللغة",
        "error": "⚠️ خطأ. حاول مرة أخرى.",
        "no_result": "❌ لا توجد نتائج.",
        "page_info": "📄 صفحة {cur}/{total} — {count} نتيجة",
        "instagram_error": "❌ لم يتم العثور على أغنية من الرابط.",
        "dl_fail": "❌ تعذر تحميل الأغنية.",
        "sub_required": "📢 اشترك في القناة (القنوات) للاستخدام:",
        "sub_ok": "✅ شكرًا! يمكنك الاستخدام الآن.",
        "sub_fail": "❌ لم تشترك بعد.",
        "expired": "❌ انتهت الصلاحية. ابحث مرة أخرى.",
    },
}

# ==================== QO'SHIMCHA MATNLAR (animatsiya/caption uchun) ====================
# Bu kalitlar handlerlardagi hardcode o'zbekcha matnlarni almashtiradi —
# endi tanlangan tilga to'liq mos chiqadi.
_EXTRA_TEXTS = {
    "uz": {
        "anim_video": "Video yuklanmoqda", "anim_recognizing": "Qo'shiq aniqlanmoqda",
        "anim_downloading": "Yuklanmoqda", "anim_searching": "Qidirilmoqda",
        "btn_find_song": "🎵 Qo'shiqni top", "audio_ready": "Tayyor!",
        "video_ready": "📹 Video tayyor!\n\n🎵 Qo'shiqni topish uchun tugmani bosing 👇",
        "sub_first": "📢 Avval kanalga obuna bo'ling!",
        "link_expired": "❌ Eskirgan, linkni qaytadan tashlang",
        "searching_song": "🎵 Qo'shiq qidirilmoqda...",
        "not_recognized": "❌ Qo'shiq tanilmadi. Boshqa video sinab ko'ring.",
        "results_suffix": "ta natija",
    },
    "ru": {
        "anim_video": "Видео загружается", "anim_recognizing": "Распознаю песню",
        "anim_downloading": "Загрузка", "anim_searching": "Поиск",
        "btn_find_song": "🎵 Найти песню", "audio_ready": "Готово!",
        "video_ready": "📹 Видео готово!\n\n🎵 Нажмите кнопку, чтобы найти песню 👇",
        "sub_first": "📢 Сначала подпишитесь на канал!",
        "link_expired": "❌ Устарело, отправьте ссылку снова",
        "searching_song": "🎵 Ищу песню...",
        "not_recognized": "❌ Песня не распознана. Попробуйте другое видео.",
        "results_suffix": "результатов",
    },
    "en": {
        "anim_video": "Downloading video", "anim_recognizing": "Recognizing song",
        "anim_downloading": "Downloading", "anim_searching": "Searching",
        "btn_find_song": "🎵 Find song", "audio_ready": "Ready!",
        "video_ready": "📹 Video ready!\n\n🎵 Tap the button to find the song 👇",
        "sub_first": "📢 Subscribe to the channel first!",
        "link_expired": "❌ Expired, send the link again",
        "searching_song": "🎵 Searching for the song...",
        "not_recognized": "❌ Song not recognized. Try another video.",
        "results_suffix": "results",
    },
    "kk": {
        "anim_video": "Видео жүктелуде", "anim_recognizing": "Ән танылуда",
        "anim_downloading": "Жүктелуде", "anim_searching": "Ізделуде",
        "btn_find_song": "🎵 Әнді табу", "audio_ready": "Дайын!",
        "video_ready": "📹 Видео дайын!\n\n🎵 Әнді табу үшін батырманы басыңыз 👇",
        "sub_first": "📢 Алдымен арнаға жазылыңыз!",
        "link_expired": "❌ Ескірген, сілтемені қайта жіберіңіз",
        "searching_song": "🎵 Ән ізделуде...",
        "not_recognized": "❌ Ән танылмады. Басқа видеоны байқап көріңіз.",
        "results_suffix": "нәтиже",
    },
    "ky": {
        "anim_video": "Видео жүктөлүүдө", "anim_recognizing": "Ыр таанылууда",
        "anim_downloading": "Жүктөлүүдө", "anim_searching": "Изделүүдө",
        "btn_find_song": "🎵 Ырды табуу", "audio_ready": "Даяр!",
        "video_ready": "📹 Видео даяр!\n\n🎵 Ырды табуу үчүн баскычты басыңыз 👇",
        "sub_first": "📢 Адегенде каналга жазылыңыз!",
        "link_expired": "❌ Эскирген, шилтемени кайра жибериңиз",
        "searching_song": "🎵 Ыр изделүүдө...",
        "not_recognized": "❌ Ыр таанылган жок. Башка видеону сынап көрүңүз.",
        "results_suffix": "натыйжа",
    },
    "tg": {
        "anim_video": "Видео боргирӣ мешавад", "anim_recognizing": "Суруд шинохта мешавад",
        "anim_downloading": "Боргирӣ", "anim_searching": "Ҷустуҷӯ",
        "btn_find_song": "🎵 Ёфтани суруд", "audio_ready": "Тайёр!",
        "video_ready": "📹 Видео тайёр!\n\n🎵 Барои ёфтани суруд тугмаро пахш кунед 👇",
        "sub_first": "📢 Аввал ба канал обуна шавед!",
        "link_expired": "❌ Кӯҳна шуд, пайвандро аз нав фиристед",
        "searching_song": "🎵 Суруд ҷустуҷӯ мешавад...",
        "not_recognized": "❌ Суруд шинохта нашуд. Видеои дигарро санҷед.",
        "results_suffix": "натиҷа",
    },
    "tr": {
        "anim_video": "Video indiriliyor", "anim_recognizing": "Şarkı tanınıyor",
        "anim_downloading": "İndiriliyor", "anim_searching": "Aranıyor",
        "btn_find_song": "🎵 Şarkıyı bul", "audio_ready": "Hazır!",
        "video_ready": "📹 Video hazır!\n\n🎵 Şarkıyı bulmak için butona dokunun 👇",
        "sub_first": "📢 Önce kanala abone olun!",
        "link_expired": "❌ Süresi doldu, bağlantıyı tekrar gönderin",
        "searching_song": "🎵 Şarkı aranıyor...",
        "not_recognized": "❌ Şarkı tanınamadı. Başka bir video deneyin.",
        "results_suffix": "sonuç",
    },
    "tk": {
        "anim_video": "Wideo ýüklenýär", "anim_recognizing": "Aýdym tanalýar",
        "anim_downloading": "Ýüklenýär", "anim_searching": "Gözlenýär",
        "btn_find_song": "🎵 Aýdymy tap", "audio_ready": "Taýýar!",
        "video_ready": "📹 Wideo taýýar!\n\n🎵 Aýdymy tapmak üçin düwmäni basyň 👇",
        "sub_first": "📢 Ilki kanala agza boluň!",
        "link_expired": "❌ Köneldi, salgyny gaýtadan iberiň",
        "searching_song": "🎵 Aýdym gözlenýär...",
        "not_recognized": "❌ Aýdym tanalmady. Başga wideo synanyşyň.",
        "results_suffix": "netije",
    },
    "az": {
        "anim_video": "Video yüklənir", "anim_recognizing": "Mahnı tanınır",
        "anim_downloading": "Yüklənir", "anim_searching": "Axtarılır",
        "btn_find_song": "🎵 Mahnını tap", "audio_ready": "Hazır!",
        "video_ready": "📹 Video hazır!\n\n🎵 Mahnını tapmaq üçün düyməni basın 👇",
        "sub_first": "📢 Əvvəlcə kanala abunə olun!",
        "link_expired": "❌ Vaxtı keçib, linki yenidən göndərin",
        "searching_song": "🎵 Mahnı axtarılır...",
        "not_recognized": "❌ Mahnı tanınmadı. Başqa video sınayın.",
        "results_suffix": "nəticə",
    },
    "ar": {
        "anim_video": "جاري تحميل الفيديو", "anim_recognizing": "جاري التعرف على الأغنية",
        "anim_downloading": "جاري التحميل", "anim_searching": "جاري البحث",
        "btn_find_song": "🎵 ابحث عن الأغنية", "audio_ready": "جاهز!",
        "video_ready": "📹 الفيديو جاهز!\n\n🎵 اضغط الزر للعثور على الأغنية 👇",
        "sub_first": "📢 اشترك في القناة أولاً!",
        "link_expired": "❌ انتهت الصلاحية، أرسل الرابط مرة أخرى",
        "searching_song": "🎵 جاري البحث عن الأغنية...",
        "not_recognized": "❌ لم يتم التعرف على الأغنية. جرب فيديو آخر.",
        "results_suffix": "نتيجة",
    },
}
for _lng, _d in _EXTRA_TEXTS.items():
    TEXTS.setdefault(_lng, {}).update(_d)
# Zaxira: biror tilda kalit yetishmasa — o'zbekchadan oladi (xom kalit chiqmasin)
for _lng in TEXTS:
    for _k, _v in TEXTS["uz"].items():
        TEXTS[_lng].setdefault(_k, _v)

def t(user_id, key, **kwargs):
    lang = get_user_lang(user_id)
    text = TEXTS.get(lang, TEXTS["uz"]).get(key, key)
    return text.format(**kwargs) if kwargs else text

# ==================== KEYBOARDS ====================
def lang_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data="lang_uz"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
        ],
        [
            InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="lang_kk"),
            InlineKeyboardButton(text="🇰🇬 Кыргызча", callback_data="lang_ky"),
            InlineKeyboardButton(text="🇹🇯 Тоҷикӣ", callback_data="lang_tg"),
        ],
        [
            InlineKeyboardButton(text="🇹🇷 Türkçe", callback_data="lang_tr"),
            InlineKeyboardButton(text="🇹🇲 Türkmençe", callback_data="lang_tk"),
            InlineKeyboardButton(text="🇦🇿 Azərbaycan", callback_data="lang_az"),
        ],
        [
            InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar"),
        ],
    ])

# Pastki menyu tugmalari — barcha tillar uchun (qidirish, til, yordam)
MENU_LABELS = {
    "uz": ["🎵 Qo'shiq qidirish", "🌐 Til", "ℹ️ Yordam", "🔥 Top qo'shiqlar"],
    "ru": ["🎵 Поиск песни", "🌐 Язык", "ℹ️ Помощь", "🔥 Топ песни"],
    "en": ["🎵 Search song", "🌐 Language", "ℹ️ Help", "🔥 Top songs"],
    "kk": ["🎵 Ән іздеу", "🌐 Тіл", "ℹ️ Көмек", "🔥 Үздік әндер"],
    "ky": ["🎵 Ыр издөө", "🌐 Тил", "ℹ️ Жардам", "🔥 Топ ырлар"],
    "tg": ["🎵 Ҷустуҷӯи суруд", "🌐 Забон", "ℹ️ Кӯмак", "🔥 Топ сурудҳо"],
    "tr": ["🎵 Şarkı ara", "🌐 Dil", "ℹ️ Yardım", "🔥 En iyi şarkılar"],
    "tk": ["🎵 Aýdym gözle", "🌐 Dil", "ℹ️ Kömek", "🔥 Iň gowy aýdymlar"],
    "az": ["🎵 Mahnı axtar", "🌐 Dil", "ℹ️ Kömək", "🔥 Top mahnılar"],
    "ar": ["🎵 بحث عن أغنية", "🌐 اللغة", "ℹ️ مساعدة", "🔥 أفضل الأغاني"],
}

def main_keyboard(user_id):
    lang = get_user_lang(user_id)
    lb = MENU_LABELS.get(lang, MENU_LABELS["uz"])
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=lb[0])],
            [KeyboardButton(text=lb[3])],
            [KeyboardButton(text=lb[1]), KeyboardButton(text=lb[2])],
        ],
        resize_keyboard=True
    )

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="adm_stats")],
        [InlineKeyboardButton(text="📥 Yuklash statistikasi", callback_data="adm_links")],
        [InlineKeyboardButton(text="🔥 Top 10 qo'shiq", callback_data="adm_topsongs")],
        [InlineKeyboardButton(text="📈 Reklama manbalari", callback_data="adm_sources")],
        [InlineKeyboardButton(text="📢 Kanal boshqaruvi", callback_data="adm_channels")],
        [InlineKeyboardButton(text="👮 Adminlar", callback_data="adm_admins")],
        [InlineKeyboardButton(text="📣 Reklama → Kanallar", callback_data="adm_bc_channel")],
        [InlineKeyboardButton(text="💬 Reklama → Lichniy", callback_data="adm_bc_private")],
    ])

def admins_admin_keyboard():
    buttons = []
    for aid in get_db_admins():
        buttons.append([InlineKeyboardButton(text=f"🗑 {aid}", callback_data=f"deladm|{aid}")])
    buttons.append([InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="addadm")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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

def results_text(results, page, uid, query=""):
    """Natijalarni tartibli matn ro'yxati qilib qaytaradi."""
    start = page * 10
    end = min(start + 10, len(results))
    total_pages = (len(results) + 9) // 10
    lines = []
    if query:
        lines.append(f"🔍 <b>{query}</b>\n")
    for i in range(start, end):
        r = results[i]
        title = r.get("title") or ""
        artist = r.get("artist") or ""
        dur = r.get("duration", "")
        n = i - start + 1
        line = f"<b>{n}.</b> {title}"
        if artist:
            line += f" — {artist}"
        if dur:
            line += f"  <code>{dur}</code>"
        lines.append(line)
    lines.append(f"\n📄 {page+1}/{total_pages} — {len(results)} {t(uid, 'results_suffix')}")
    return "\n".join(lines)

def results_keyboard(results, page, uid):
    """Faqat raqamli tugmalar paneli (1-10) + sahifa o'qlari."""
    start = page * 10
    end = min(start + 10, len(results))
    buttons = []
    row = []
    for i in range(start, end):
        n = i - start + 1
        row.append(InlineKeyboardButton(text=str(n), callback_data=f"dl|{i}"))
        if len(row) == 5:           # har qatorda 5 ta raqam
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    nav = []
    total_pages = (len(results) + 9) // 10
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pg|{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"pg|{page+1}"))
    if nav:
        buttons.append(nav)
    # Pleylist: birinchi 5 tasini ketma-ket yuklash
    if len(results) >= 2:
        buttons.append([InlineKeyboardButton(text="⬇️ Hammasini yuklash (5 ta)", callback_data="dlall")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== IN-MEMORY CACHE ====================
search_cache = {}        # uid -> [results]
search_query_cache = {}  # uid -> qidiruv so'zi (sarlavha uchun)
media_link_cache = {}    # uid -> url

# Shared HTTP session (tezlik uchun)
_http_session: aiohttp.ClientSession | None = None

def http() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session

# ==================== SARLAVHANI AJRATISH ====================
# Keraksiz qismlar (Official Video, Lyrics, music video, klip ...)
_JUNK_RE = re.compile(
    r"\((?:[^()]*?(?:official|music|lyric|audio|video|clip|klip|премьера|"
    r"premyera|mood\s*video|tizer|teaser|hd|4k|remix|cover|karaoke)[^()]*)\)"
    r"|\[[^\]]*\]"
    r"|\b(?:official\s*video|music\s*video|lyrics?|audio|klip|клип|premyera|tizer)\b",
    re.IGNORECASE,
)

def parse_song_title(raw: str, channel: str = "") -> tuple[str, str]:
    """YouTube video sarlavhasidan (qo'shiq_nomi, ijrochi) ni ajratadi.
    YouTube'da sarlavha odatda 'Ijrochi - Qo'shiq' ko'rinishida bo'ladi,
    shuning uchun ularni almashtirib qaytaramiz: avval qo'shiq, keyin ijrochi."""
    t = (raw or "").strip()
    t = _JUNK_RE.sub("", t)                 # keraksiz so'zlarni olib tashlash
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—|·")
    # Ijrochi va qo'shiqni ajratuvchi belgilar (uzunidan qisqasiga)
    for sep in (" - ", " – ", " — ", " | ", " · ", "-", "–", "—"):
        if sep in t:
            left, right = t.split(sep, 1)
            left, right = left.strip(), right.strip()
            if left and right:
                return right, left          # (qo'shiq nomi, ijrochi)
            break
    # Ajralmasa: butun sarlavha = qo'shiq nomi, kanal nomi = ijrochi
    return (t or raw or "").strip(), (channel or "").strip()

# ==================== YOUTUBE QIDIRUV ====================
async def youtube_search(query: str, limit=10) -> list:
    """yt-dlp orqali YouTube'dan qidiradi (yuklamasdan, faqat ro'yxat).
    Qidiruv uchun faqat cookie ishlatamiz (client argumentisiz) —
    bu eng barqaror ishlaydigan usul ekani sinab ko'rilgan."""
    cookie_args = ["--cookies", COOKIES] if COOKIES else []
    cmd = [
        YTDLP, *cookie_args, f"ytsearch{limit}:{query}",
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
            raw_title = e.get("title", "")
            channel = e.get("uploader") or e.get("channel") or ""
            song, performer = parse_song_title(raw_title, channel)
            results.append({
                "title": song,
                "artist": performer,
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
    """Berilgan YouTube URL/ID dan MP3 yuklaydi — TEZLASHTIRILGAN.
    Avval eng tez ishlaydigan 'android' client bilan urinadi (test'da
    har doim ishlagan), faqat u ishlamasa qolgan variantlarga o'tadi."""
    out = f"/tmp/dl_{key}.mp3"
    if os.path.exists(out) and os.path.getsize(out) > 100_000:
        return out

    # Tezlik uchun: android birinchi va alohida (ko'pincha shu yetadi),
    # zaxira sifatida ios va web. 5 emas, 3 variant — kam urinish, tez.
    client_variants = ["android", "ios", "web_safari"]
    base = COOKIES and ["--cookies", COOKIES] or []

    for clients in client_variants:
        cmd = [
            YTDLP, *base,
            "--extractor-args", f"youtube:player_client={clients}",
            "-x", "--audio-format", "mp3", "--audio-quality", "5",
            # m4a/bestaudio'ni afzal ko'ramiz — kichikroq, tezroq yuklanadi
            "-f", "bestaudio[ext=m4a]/bestaudio/best",
            "--no-playlist", "--no-warnings", "--max-filesize", "50M",
            "--concurrent-fragments", "16", "-N", "16",
            "--no-part", "--no-mtime",
            "-o", f"/tmp/dl_{key}.%(ext)s", url,
        ]
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            # Timeout 120 -> 75s (qotib qolmaslik uchun, lekin yetarli)
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=75)
            if os.path.exists(out) and os.path.getsize(out) > 100_000:
                return out
            err = (stderr or b"").decode("utf-8", "ignore")[:200]
            if err:
                logger.error(f"ytdlp [{clients}] fail: {err}")
        except asyncio.TimeoutError:
            if proc:
                try: proc.kill()
                except Exception: pass
        except Exception as e:
            logger.error(f"ytdlp dl error [{clients}]: {e}")
    return None

async def ytdlp_download_query(title: str, artist: str) -> str | None:
    """Qo'shiq nomi bo'yicha YouTube'dan qidirib MP3 yuklaydi (Shazam natijasi uchun).
    'official audio' qo'shib asl/original versiyani topadi (remix/cover emas)."""
    base = f"{artist} {title}".strip()
    h = hashlib.md5(base.lower().encode()).hexdigest()[:16]
    # Avval "official audio" bilan — asl versiya
    path = await ytdlp_download_url(f"ytsearch1:{base} official audio", h)
    if path:
        return path
    # Topilmasa — oddiy nom bilan
    return await ytdlp_download_url(f"ytsearch1:{base}", h)

async def ytdlp_get_info(url: str) -> tuple[str, str]:
    """YouTube URL dan (qo'shiq nomi, ijrochi) ni oladi — yuklamasdan, faqat metadata.
    Yuklash boshlanishidan oldin sarlavhani ko'rsatish uchun ishlatiladi."""
    base = ["--cookies", COOKIES] if COOKIES else []
    cmd = [
        YTDLP, *base,
        "--extractor-args", "youtube:player_client=android,ios,web",
        "--no-warnings", "--no-playlist",
        "--print", "%(title)s\n%(uploader)s", url,
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
        parts = out.decode("utf-8", "ignore").strip().split("\n")
        raw_title = parts[0].strip() if parts else ""
        uploader = parts[1].strip() if len(parts) > 1 else ""
        if raw_title:
            return parse_song_title(raw_title, uploader)
    except asyncio.TimeoutError:
        if proc:
            try: proc.kill()
            except Exception: pass
    except Exception as e:
        logger.error(f"yt info error: {e}")
    return "", ""

# ==================== AUDD (SHAZAM) ====================
async def recognize_from_file(file_path: str) -> dict | None:
    """Ovoz fayldan qo'shiqni taniydi.
    1) Avval shazamio (bepul, aniq, limit yo'q) bilan urinadi.
    2) Topilmasa yoki xato bo'lsa — AudD (zaxira) bilan urinadi.
    """
    # ---------- 1) SHAZAMIO (asosiy) ----------
    if _HAS_SHAZAMIO and _shazam is not None:
        try:
            out = await _shazam.recognize(file_path)
            track = out.get("track") if out else None
            if track:
                cover = None
                # Rasm (cover) — bir necha joydan qidiramiz
                images = track.get("images") or {}
                cover = images.get("coverarthq") or images.get("coverart")
                title = track.get("title", "?")
                artist = track.get("subtitle", "?")
                # Albom nomi (metadata ichida bo'lishi mumkin)
                album = "?"
                try:
                    sections = track.get("sections") or []
                    for sec in sections:
                        for md in (sec.get("metadata") or []):
                            if md.get("title", "").lower() in ("album", "альбом"):
                                album = md.get("text", "?")
                except Exception:
                    pass
                return {
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "release_date": "?",
                    "cover": cover,
                }
        except Exception as e:
            logger.error(f"shazamio error: {e}")

    # ---------- 2) AudD (zaxira) ----------
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
                    cover = None
                    am = r.get("apple_music") or {}
                    if am.get("artwork", {}).get("url"):
                        cover = am["artwork"]["url"].replace("{w}", "600").replace("{h}", "600")
                    if not cover:
                        sp = r.get("spotify") or {}
                        imgs = (sp.get("album") or {}).get("images") or []
                        if imgs:
                            cover = imgs[0].get("url")
                    return {
                        "title": r.get("title", "?"),
                        "artist": r.get("artist", "?"),
                        "album": r.get("album", "?"),
                        "release_date": r.get("release_date", "?"),
                        "cover": cover,
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

async def find_by_lyrics(query: str) -> list:
    """AudD findLyrics — qo'shiq MATNI (lyrics) bo'yicha qo'shiqlarni topadi.
    Natija: [{'title':..., 'artist':...}, ...] yoki bo'sh ro'yxat."""
    try:
        params = {"api_token": AUDD_API_KEY, "q": query[:300]}
        async with http().get("https://api.audd.io/findLyrics/", params=params,
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            data = await resp.json()
            if data.get("status") == "success" and data.get("result"):
                out, seen = [], set()
                for r in data["result"]:
                    title = (r.get("title") or "").strip()
                    artist = (r.get("artist") or "").strip()
                    key = (title.lower(), artist.lower())
                    if title and key not in seen:
                        seen.add(key)
                        out.append({"title": title, "artist": artist})
                return out[:10]
    except Exception as e:
        logger.error(f"AudD lyrics error: {e}")
    return []

# ==================== INSTAGRAM / TIKTOK ====================
def detect_media_link(text: str) -> str | None:
    """Instagram, TikTok va YouTube havolalarini aniqlaydi."""
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
            # Instagram: keraksiz ?igsh=... tracking qismini kesib tashlaymiz
            # (yt-dlp uni xato deb qabul qilib, yuklamaydi)
            if "instagram.com" in url:
                url = url.split("?")[0].rstrip("/") + "/"
            return url
    return None

async def get_media_audio(url: str) -> str | None:
    """IG/TikTok dan audioni yuklaydi. Bir nechta usul bilan urinadi."""
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    out = f"/tmp/ig_{h}.mp3"
    if os.path.exists(out) and os.path.getsize(out) > 5_000:
        return out

    attempts = [
        [YTDLP, *dl_args(url),
         "-x", "--audio-format", "mp3", "--audio-quality", "0",
         "-f", "bestaudio/best", "--no-playlist", "--no-warnings",
         "--no-check-certificate", "--max-filesize", "50M",
         "--concurrent-fragments", "8", "-N", "8",
         "-o", f"/tmp/ig_{h}.%(ext)s", url],
        [YTDLP, *dl_args(url),
         "-x", "--audio-format", "mp3", "--audio-quality", "0",
         "-f", "bestaudio/best", "--no-playlist", "--no-warnings",
         "--no-check-certificate", "--max-filesize", "50M",
         "--add-header", "User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
         "--add-header", "Referer:https://www.instagram.com/",
         "-o", f"/tmp/ig_{h}.%(ext)s", url],
        [YTDLP, *dl_args(url), "-x", "--audio-format", "mp3",
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

async def get_media_video(url: str) -> str | None:
    """IG/TikTok dan VIDEO yuklaydi (max 50MB). Fayl yo'lini qaytaradi."""
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    for ext in ("mp4", "webm", "mkv", "mov"):
        p = f"/tmp/vid_{h}.{ext}"
        if os.path.exists(p) and os.path.getsize(p) > 10_000:
            return p

    attempts = [
        # 1) Hajmi cheklangan format (YouTube'ning uzun videolari ham 48MB ga sig'sin)
        [YTDLP, *dl_args(url),
         "-f", "best[filesize<48M]/best[height<=720]/b[height<=480]/best",
         "--no-playlist", "--no-warnings",
         "--no-check-certificate", "--max-filesize", "48M",
         "-o", f"/tmp/vid_{h}.%(ext)s", url],
        # 2) Oddiy "best" (IG/TikTok qisqa videolar uchun)
        [YTDLP, *dl_args(url), "-f", "best",
         "--no-playlist", "--no-warnings", "--no-check-certificate",
         "--max-filesize", "48M",
         "--add-header", "User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
         "--add-header", "Referer:https://www.instagram.com/",
         "-o", f"/tmp/vid_{h}.%(ext)s", url],
    ]

    for cmd in attempts:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=90)
            for ext in ("mp4", "webm", "mkv", "mov"):
                p = f"/tmp/vid_{h}.{ext}"
                if os.path.exists(p) and os.path.getsize(p) > 10_000:
                    return p
        except asyncio.TimeoutError:
            if proc:
                try: proc.kill()
                except Exception: pass
        except Exception as e:
            logger.error(f"Video error: {e}")
    return None

async def extract_audio_from_video(video_path: str) -> str | None:
    """Videodan o'rta qismdan 24 soniyalik audio ajratadi (Shazam aniqroq taniydi)."""
    out = video_path.rsplit(".", 1)[0] + "_audio.mp3"
    if os.path.exists(out) and os.path.getsize(out) > 5_000:
        return out
    # video uzunligini aniqlaymiz
    start = "0"
    try:
        p = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        so, _ = await asyncio.wait_for(p.communicate(), timeout=15)
        dur = float(so.decode().strip())
        if dur > 30:
            start = str(max(0, int(dur / 2) - 12))  # o'rtadan boshlaymiz
    except Exception:
        pass
    cmd = ["ffmpeg", "-y", "-ss", start, "-t", "24", "-i", video_path,
           "-vn", "-acodec", "libmp3lame", "-q:a", "2", out]
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
        logger.error(f"ffmpeg extract error: {e}")
    return None

# ==================== SUBSCRIPTION ====================
async def check_subscription(bot: Bot, user_id: int) -> bool:
    if is_admin(user_id):
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

class AdminState(StatesGroup):
    waiting_admin_id = State()

# ==================== BOT ====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# ==================== LOADING ANIMATSIYA ====================
CLOCKS = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]

async def animate_loading(message, base_text: str):
    """Soat aylanib turadi. Har kadr matni o'zgaradi (not modified xatosini oldini olish)."""
    i = 0
    try:
        while True:
            clock = CLOCKS[i % len(CLOCKS)]
            dots = "." * (1 + i % 3)
            try:
                await message.edit_text(f"{clock} {base_text}{dots}")
            except Exception as e:
                logger.warning(f"ANIM edit error: {e}")
            i += 1
            await asyncio.sleep(0.7)
    except asyncio.CancelledError:
        return

async def animate_search(message, base_text: str):
    """Qum soat aylanadi (qidiruv uchun)."""
    frames = ["⌛", "⏳"]
    i = 0
    try:
        while True:
            dots = "." * (1 + i % 3)
            try:
                await message.edit_text(f"{frames[i % 2]} {base_text}{dots}")
            except Exception as e:
                logger.warning(f"ANIM search error: {e}")
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
    # ?start=MANBA → reklama qaysi kanaldan kelganini aniqlaymiz
    source = None
    download_vid = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1:
        source = parts[1].strip()[:64]  # masalan: t.me/NolaMusicBot?start=kanal1
        # Inline tugmadan kelgan deep-link: ?start=dl_VIDEOID → o'sha qo'shiqni yuklaydi
        if source.startswith("dl_"):
            download_vid = source[3:]
            source = "inline"  # reklama statistikasida "inline" deb belgilanadi
    add_user(uid, message.from_user.username, message.from_user.full_name, source=source)
    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(get_channels()))
        return
    # Inline deep-link → qo'shiqni darrov yuklab beramiz
    if download_vid:
        status = await message.answer("⏳")
        anim = asyncio.create_task(animate_loading(status, t(uid, "anim_downloading")))
        try:
            url = f"https://www.youtube.com/watch?v={download_vid}"
            info_task = asyncio.create_task(ytdlp_get_info(url))
            path = await ytdlp_download_url(url, download_vid)
            title, artist = await info_task
            await stop_anim(anim)
            if path:
                title = title or "Audio"
                cap = f"🎵 {title}"
                if artist:
                    cap += f" — {artist}"
                cap += f"\n\n🤖 @{BOT_USERNAME}"
                try:
                    msg = await bot.send_audio(uid, audio=FSInputFile(path),
                                               title=title[:64], performer=artist or None, caption=cap)
                    song_key = hashlib.md5(f"{artist}|{title}".lower().encode()).hexdigest()
                    save_cached_audio(song_key, msg.audio.file_id)
                finally:
                    try: os.remove(path)
                    except Exception: pass
                await status.delete()
                log_search(uid, url, title)
            else:
                await status.edit_text(t(uid, "dl_fail"))
        except Exception as e:
            logger.error(f"inline dl error: {e}")
            await stop_anim(anim)
            await status.edit_text(t(uid, "error"))
        await message.answer(t(uid, "welcome"), reply_markup=main_keyboard(uid))
        return
    await message.answer(t(uid, "welcome"), reply_markup=main_keyboard(uid))

@dp.message(Command("lang"))
async def cmd_lang(message: types.Message):
    await message.answer(t(message.from_user.id, "choose_lang"), reply_markup=lang_keyboard())

@dp.message(Command("restart"))
async def cmd_restart(message: types.Message):
    uid = message.from_user.id
    if is_admin(uid):
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
    if not is_admin(message.from_user.id):
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

@dp.callback_query(F.data == "findsong")
async def cb_findsong(callback: types.CallbackQuery):
    """Video tagidagi 'Qo'shiqni top' tugmasi: videodan ovoz → Shazam → to'liq qo'shiq."""
    uid = callback.from_user.id
    if not await check_subscription(bot, uid):
        await callback.answer(t(uid, "sub_first"), show_alert=True)
        return
    data = media_link_cache.get(uid)
    if not data:
        await callback.answer(t(uid, "link_expired"), show_alert=True)
        return
    await callback.answer(t(uid, "searching_song"))
    media_url = data["url"]
    video_path = data.get("video")
    link_key = "lnk_" + hashlib.md5(media_url.encode()).hexdigest()

    status = await callback.message.answer("⏳")
    anim = asyncio.create_task(animate_loading(status, t(uid, "anim_recognizing")))

    # Cache: shu link oldin topilganmi?
    cached_song = get_cached_audio(link_key)
    if cached_song:
        try:
            await bot.send_audio(uid, audio=cached_song,
                                 caption=f"🎵 {t(uid, 'audio_ready')}\n\n🤖 @nolamusicbot")
            await stop_anim(anim)
            await status.delete()
            return
        except Exception:
            pass

    try:
        result = None
        # 1) ASOSIY: yuklangan videodan ovoz ajratib → shazamio (limit yo'q, aniq)
        if video_path and os.path.exists(video_path):
            audio_path = await extract_audio_from_video(video_path)
            if audio_path:
                result = await recognize_from_file(audio_path)
                try: os.remove(audio_path)
                except Exception: pass
        # 2) Bo'lmasa — linkdan to'g'ridan audio yuklab → shazamio
        if not result:
            audio_path = await get_media_audio(media_url)
            if audio_path:
                result = await recognize_from_file(audio_path)
                try: os.remove(audio_path)
                except Exception: pass
        # 3) Oxirgi chora — AudD ga URL (shazamio ishlamasa)
        if not result:
            result = await recognize_from_url(media_url)

        if not result:
            await stop_anim(anim)
            await status.edit_text(t(uid, "not_recognized"))
            return

        s_title = result.get("title", "?")
        s_artist = result.get("artist", "?")
        await stop_anim(anim)
        await status.edit_text(t(uid, "found_shazam", title=s_title, artist=s_artist))
        anim = asyncio.create_task(animate_loading(status, f"{s_title} — {t(uid, 'anim_downloading')}"))
        ok = await send_full_song(uid, s_title, s_artist, extra_key=link_key)
        await stop_anim(anim)
        if ok:
            await status.delete()
        else:
            await status.edit_text(t(uid, "dl_fail"))
        log_search(uid, media_url, s_title)
    except Exception as e:
        logger.error(f"findsong error: {e}")
        await stop_anim(anim)
        await status.edit_text(t(uid, "error"))

@dp.callback_query(F.data == "dlall")
async def cb_dlall(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await check_subscription(bot, uid):
        await callback.answer(t(uid, "sub_first"), show_alert=True)
        return
    results = search_cache.get(uid, [])[:5]
    if not results:
        await callback.answer(t(uid, "expired"), show_alert=True)
        return
    await callback.answer("⬇️ 5 ta qo'shiq yuklanmoqda...")
    status = await callback.message.answer("⬇️ Pleylist yuklanmoqda... (5 ta)")
    ok = 0
    for s in results:
        title = s.get("title", "")
        artist = s.get("artist", "")
        if not title:
            continue
        try:
            if await send_full_song(uid, title, artist):
                ok += 1
        except Exception as e:
            logger.error(f"dlall error: {e}")
    try:
        await status.edit_text(f"✅ {ok} ta qo'shiq yuborildi!")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("dl|"))
async def cb_download(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await check_subscription(bot, uid):
        await callback.answer(t(uid, "sub_first"), show_alert=True)
        return
    idx = int(callback.data.split("|")[1])
    results = search_cache.get(uid, [])
    if not results or idx >= len(results):
        await callback.answer(t(uid, "expired"), show_alert=True)
        return
    song = results[idx]
    await callback.answer()
    status = await callback.message.answer("⏳")
    anim = asyncio.create_task(animate_loading(status, t(uid, "anim_downloading")))
    try:
        title = song.get("title", "Audio")
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
    query = search_query_cache.get(uid, "")
    body = results_text(results, page, uid, query=query)
    await callback.message.edit_text(body, reply_markup=results_keyboard(results, page, uid))
    await callback.answer()

# ==================== ADMIN ====================
@dp.callback_query(F.data == "adm_back")
async def cb_adm_back(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("👑 <b>Admin Panel</b>", reply_markup=admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "adm_stats")
async def cb_stats(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
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

@dp.callback_query(F.data == "adm_links")
async def cb_links(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    rows = get_link_stats()
    text = "📥 <b>Yuklash statistikasi</b>\n<i>(qaysi platformadan necha marta link tashlangan)</i>\n\n"
    if rows:
        total = sum(c for _, c in rows)
        icons = {"Instagram": "📸", "TikTok": "🎵", "YouTube": "▶️", "Boshqa": "🔗"}
        for plat, cnt in rows:
            text += f"{icons.get(plat, '🔗')} {plat} — <b>{cnt}</b>\n"
        text += f"\n📊 Jami: <b>{total}</b>"
    else:
        text += "❌ Hozircha ma'lumot yo'q"
    await callback.message.edit_text(text, reply_markup=admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "adm_topsongs")
async def cb_topsongs(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    rows = get_top_songs(10)
    text = "🔥 <b>Top 10 qidirilgan qo'shiq</b>\n\n"
    if rows:
        medals = ["🥇", "🥈", "🥉"]
        for i, (song, n) in enumerate(rows):
            num = medals[i] if i < 3 else f"{i+1}."
            text += f"{num} {song} — <b>{n}</b>\n"
    else:
        text += "❌ Hozircha ma'lumot yo'q"
    await callback.message.edit_text(text, reply_markup=admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "adm_sources")
async def cb_sources(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    rows = get_source_stats()
    text = "📈 <b>Reklama manbalari</b>\n<i>(qaysi havoladan necha kishi kelgan)</i>\n\n"
    if rows:
        for src, cnt in rows:
            text += f"• <code>{src}</code> — <b>{cnt}</b>\n"
    else:
        text += "❌ Hozircha ma'lumot yo'q"
    text += ("\n\n💡 Havola ko'rinishi:\n"
             "<code>t.me/NolaMusicBot?start=kanal1</code>\n"
             "Har reklamaga boshqa nom ber (kanal1, kanal2...) — "
             "shunda qaysi reklama qancha odam keltirganini ko'rasan.")
    await callback.message.edit_text(text, reply_markup=admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "adm_admins")
async def cb_admins(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    extra = get_db_admins()
    text = "👮 <b>Adminlar</b>\n\n"
    text += f"👑 Asosiy: <code>{', '.join(str(a) for a in ADMIN_IDS)}</code>\n\n"
    if extra:
        text += "➕ Qo'shilganlar:\n" + "\n".join(f"• <code>{a}</code>" for a in extra)
        text += "\n\n🗑 O'chirish uchun pastdagi tugmani bos"
    else:
        text += "➕ Qo'shilgan admin yo'q"
    await callback.message.edit_text(text, reply_markup=admins_admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "addadm")
async def cb_addadm(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminState.waiting_admin_id)
    await callback.message.answer(
        "➕ Yangi adminning <b>user ID</b> raqamini yuboring (faqat raqam).\n\n"
        "💡 ID ni bilish uchun: o'sha odam @userinfobot ga /start bossin — "
        "bot uning ID raqamini ko'rsatadi."
    )
    await callback.answer()

@dp.message(AdminState.waiting_admin_id)
async def process_add_admin(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("❌ Bu raqam emas. Faqat ID raqam yuboring.", reply_markup=admin_keyboard())
        return
    new_id = int(txt)
    if is_admin(new_id):
        await message.answer("⚠️ Bu odam allaqachon admin.", reply_markup=admin_keyboard())
        return
    if add_admin(new_id):
        await message.answer(f"✅ <code>{new_id}</code> admin qilib qo'shildi!", reply_markup=admin_keyboard())
        try:
            await bot.send_message(new_id, "👮 Sizga admin huquqi berildi! /admin")
        except Exception:
            pass
    else:
        await message.answer("❌ Qo'shilmadi, qaytadan urinib ko'ring.", reply_markup=admin_keyboard())

@dp.callback_query(F.data.startswith("deladm|"))
async def cb_deladm(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    aid = int(callback.data.split("|")[1])
    if remove_admin(aid):
        await callback.answer(f"🗑 {aid} o'chirildi!", show_alert=True)
        await cb_admins(callback)
    else:
        await callback.answer("❌ Xato", show_alert=True)

@dp.callback_query(F.data == "adm_channels")
async def cb_channels(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
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
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(ChannelState.waiting_channel)
    await callback.message.answer(
        "➕ Kanal username ni yuboring (masalan: <code>@mychannel</code>)\n\n"
        "⚠️ <b>Muhim:</b> Botni avval kanalga ADMIN qilib qo'shing!"
    )
    await callback.answer()

@dp.message(ChannelState.waiting_channel)
async def process_add_channel(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
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
    if not is_admin(callback.from_user.id):
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
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(BroadcastState.waiting_message)
    await state.update_data(target="private")
    await callback.message.answer("💬 Barcha foydalanuvchilarga yuboriladigan xabarni yozing:\n(Rasm, video ham bo'ladi)")
    await callback.answer()

@dp.callback_query(F.data == "adm_bc_channel")
async def cb_bc_channel(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(BroadcastState.waiting_message)
    await state.update_data(target="channel")
    await callback.message.answer("📣 Ulangan kanallarga yuboriladigan xabarni yozing:")
    await callback.answer()

@dp.message(BroadcastState.waiting_message)
async def process_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
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
    if not is_admin(callback.from_user.id):
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
    status = await message.answer("⏳")
    anim = asyncio.create_task(animate_loading(status, "Qo'shiq tanilmoqda"))
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

        # === 3 XIL VARIANT: Original, Slowed, Remix ===
        # Har bir variant uchun alohida qidiruv — toza va aniq natija.
        base_query = f"{s_artist} {s_title}".strip()
        variants = [
            ("🎵 Original", f"{base_query} official audio"),
            ("🐢 Slowed", f"{base_query} slowed reverb"),
            ("🔥 Remix", f"{base_query} remix"),
        ]
        songs = []
        for label, q in variants:
            res = await youtube_search(q, limit=1)
            if res:
                r = res[0]
                r["variant"] = label
                songs.append(r)
        # Hech narsa topilmasa — oddiy qidiruv (zaxira)
        if not songs:
            songs = await youtube_search(base_query, limit=8)
        await stop_anim(anim)

        if not songs:
            await status.edit_text(t(uid, "found_shazam", title=s_title, artist=s_artist) +
                                   "\n\n" + t(uid, "no_result"))
            log_search(uid, "audio", s_title)
            return

        search_cache[uid] = songs
        search_query_cache[uid] = base_query

        # === TINGLA USLUBI: albom rasmi + ijrochi/nom + variant ro'yxati ===
        cover = result.get("cover")
        header = (f"🎤 <b>Ijrochi:</b> {s_artist}\n"
                  f"🎵 <b>Qo'shiq nomi:</b> {s_title}\n\n"
                  f"⬇️ Variantlardan birini tanlang:\n\n")
        # Variant yorlig'i bilan ro'yxat
        lines = []
        for i, song in enumerate(songs):
            v = song.get("variant", "🎵")
            ttl = song.get("title", "")
            dur = song.get("duration", "")
            line = f"<b>{i+1}.</b> {v} — {ttl}"
            if dur:
                line += f"  <code>{dur}</code>"
            lines.append(line)
        body = header + "\n".join(lines)
        kb = results_keyboard(songs, 0, uid)

        # Rasm bo'lsa — rasm bilan, bo'lmasa oddiy matn
        if cover:
            try:
                await status.delete()
                await bot.send_photo(uid, photo=cover, caption=body, reply_markup=kb)
            except Exception:
                await status.edit_text(body, reply_markup=kb)
        else:
            await status.edit_text(body, reply_markup=kb)
        log_search(uid, "audio", s_title)
    except Exception as e:
        logger.error(f"Audio error: {e}")
        await stop_anim(anim)
        await status.edit_text(t(uid, "error"))

# ==================== TEXT / LINK ====================
async def show_top_chart(message: types.Message, uid: int):
    """Foydalanuvchiga eng ko'p qidirilgan qo'shiqlarni ko'rsatadi (raqamdan yuklab oladi)."""
    rows = get_top_songs(10)
    lb = MENU_LABELS.get(get_user_lang(uid), MENU_LABELS["uz"])
    if not rows:
        await message.answer(f"{lb[3]}\n\n❌ —", reply_markup=main_keyboard(uid))
        return
    # Raqamli tugmalar ishlashi uchun natijalarni cache'ga solamiz
    results = [{"title": r[0], "artist": "", "id": ""} for r in rows]
    search_cache[uid] = results
    search_query_cache[uid] = "top"
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"<b>{lb[3]}</b>\n"]
    for i, (song, n) in enumerate(rows):
        num = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{num} {song}")
    lines.append("\n⬇️ Raqamni bosib yuklab ol")
    await message.answer("\n".join(lines), reply_markup=results_keyboard(results, 0, uid))

@dp.inline_query()
async def inline_search(query: types.InlineQuery):
    """Istalgan chatda @bot qo'shiq nomi → qidiruv natijalari.
    - Cache'dagi qo'shiq → to'g'ridan audio (darrov chaladi)
    - Yangi qo'shiq → tugmali xabar (do'sting bossa botga kirib yuklaydi → reklama tarqaladi)
    """
    q = (query.query or "").strip()
    if len(q) < 2:
        try:
            await query.answer([], cache_time=5, is_personal=True)
        except Exception:
            pass
        return
    try:
        songs = await youtube_search(q, limit=10)
    except Exception:
        songs = []
    items = []
    for i, s in enumerate(songs):
        title = (s.get("title") or "")[:90]
        artist = (s.get("artist") or "")[:90]
        vid = s.get("id", "")
        dur = s.get("duration", "")
        song_key = hashlib.md5(f"{artist}|{title}".lower().encode()).hexdigest()
        cached = get_cached_audio(song_key)
        if cached:
            # Cache'da bor — to'g'ridan audio yuboradi (darrov chaladi)
            items.append(InlineQueryResultCachedAudio(
                id=str(i), audio_file_id=cached,
                caption=f"🎵 {title} — {artist}\n🤖 @{BOT_USERNAME}",
            ))
        else:
            # Yangi qo'shiq — tugmali xabar (botga deep-link)
            link = f"https://t.me/{BOT_USERNAME}?start=dl_{vid}" if vid else f"https://t.me/{BOT_USERNAME}"
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬇️ Yuklab olish", url=link)
            ]])
            items.append(InlineQueryResultArticle(
                id=str(i),
                title=title or "—",
                description=f"{artist}  {dur}".strip(),
                input_message_content=InputTextMessageContent(
                    message_text=(f"🎵 <b>{title}</b>\n👤 {artist}\n\n"
                                  f"⬇️ Yuklab olish uchun tugmani bos 👇\n🤖 @{BOT_USERNAME}"),
                    parse_mode="HTML",
                ),
                reply_markup=kb,
            ))
    await query.answer(items, cache_time=15, is_personal=True)

@dp.message(F.text)
async def handle_text(message: types.Message):
    uid = message.from_user.id
    text = message.text
    add_user(uid, message.from_user.username, message.from_user.full_name)
    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(get_channels()))
        return

    # Menyu tugmalari (barcha tillar)
    search_btns = [m[0] for m in MENU_LABELS.values()]
    lang_btns = [m[1] for m in MENU_LABELS.values()]
    help_btns = [m[2] for m in MENU_LABELS.values()]
    top_btns = [m[3] for m in MENU_LABELS.values()]

    if text in search_btns:
        await message.answer(t(uid, "send_audio"), reply_markup=main_keyboard(uid))
        return
    if text in top_btns:
        await show_top_chart(message, uid)
        return
    if text in lang_btns:
        await message.answer(t(uid, "choose_lang"), reply_markup=lang_keyboard())
        return
    if text in help_btns:
        await message.answer(t(uid, "help"), reply_markup=main_keyboard(uid))
        return

    # ---- LINK aniqlash ----
    media_url = detect_media_link(text)
    if media_url:
        # === HAMMA LINK (YouTube/TikTok/Instagram) → VIDEO + "Qo'shiqni yuklash" tugmasi ===
        # Avval videoni yuklab, ostiga tugma qo'yamiz. Tugma bosilganda shazamio ishlaydi.
        bump_platform(media_url)  # statistika: qaysi platformadan necha marta
        status = await message.answer("⏳")
        anim = asyncio.create_task(animate_loading(status, t(uid, "anim_video")))
        try:
            video_path = await get_media_video(media_url)
            await stop_anim(anim)
            if not video_path:
                await status.edit_text(t(uid, "instagram_error"))
                return
            # Videoni keshlaymiz (tugma bosilganda ovoz ajratishda kerak bo'ladi)
            media_link_cache[uid] = {"url": media_url, "video": video_path}
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=t(uid, "btn_find_song"), callback_data="findsong")
            ]])
            await bot.send_video(uid, video=FSInputFile(video_path),
                                 caption=f"{t(uid, 'video_ready')}\n\n🤖 @nolamusicbot",
                                 reply_markup=kb)
            await status.delete()
        except Exception as e:
            logger.error(f"Media video error: {e}")
            await stop_anim(anim)
            await status.edit_text(t(uid, "error"))
        return

    # ---- Oddiy matn = QO'SHIQ NOMI → YouTube qidiruv ----
    status = await message.answer("⌛")
    anim = asyncio.create_task(animate_search(status, t(uid, "anim_searching")))
    try:
        songs = []
        # Matn uzun yoki ko'p qatorli bo'lsa — qo'shiq MATNI (lyrics) bo'lishi mumkin.
        # Avval matn bo'yicha qo'shiqni aniqlab, keyin YouTube'dan qidiramiz.
        looks_like_lyrics = ("\n" in text) or (len(text.split()) >= 5)
        if looks_like_lyrics:
            matches = await find_by_lyrics(text)
            if matches:
                top = matches[0]
                yt_query = f"{top['artist']} {top['title']}".strip()
                songs = await youtube_search(yt_query, limit=12)
        # Lyrics topilmasa yoki oddiy nom qidiruvi bo'lsa — to'g'ridan YouTube
        if not songs:
            songs = await youtube_search(text, limit=12)
        await stop_anim(anim)
        if songs:
            search_cache[uid] = songs
            search_query_cache[uid] = text
            body = results_text(songs, 0, uid, query=text)
            await status.edit_text(body, reply_markup=results_keyboard(songs, 0, uid))
            log_search(uid, text, songs[0].get("title", ""))
        else:
            await status.edit_text(t(uid, "no_result"))
    except Exception as e:
        logger.error(f"Search error: {e}")
        await stop_anim(anim)
        await status.edit_text(t(uid, "error"))

# ==================== MAIN ====================
async def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN topilmadi! token.txt yoki BOT_TOKEN env kerak.")
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    global BOT_USERNAME
    try:
        me = await bot.get_me()
        if me.username:
            BOT_USERNAME = me.username
    except Exception:
        pass
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
