import os
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
    ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ==================== CONFIG ====================
BOT_TOKEN = "8589753213:AAEElVXtq9KY-TwopTWxez5tqQMV08RJd4s"          # @BotFather dan oling
AUDD_API_KEY = "YOUR_AUDD_API_KEY_HERE"    # audd.io dan oling (bepul)
ADMIN_IDS = [7434706702]                     # Sizning Telegram ID ingiz
REQUIRED_CHANNELS = []                      # Majburiy kanallar: ["@channel1", "@channel2"]

# ==================== LOGGING ====================
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
    c.execute("""CREATE TABLE IF NOT EXISTS broadcasts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT,
        target TEXT,
        sent_count INTEGER DEFAULT 0,
        sent_at TEXT
    )""")
    conn.commit()
    conn.close()

def add_user(user_id, username, full_name, lang="uz"):
    conn = sqlite3.connect("nola_bot.db")
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""INSERT OR IGNORE INTO users 
                 (user_id, username, full_name, lang, joined_at, last_active) 
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (user_id, username, full_name, lang, now, now))
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
    total_searches = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM searches WHERE searched_at LIKE ?", (f"{today}%",))
    today_searches = c.fetchone()[0]
    conn.close()
    return total, today_new, total_searches, today_searches

def get_all_users(only_private=False):
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
        "welcome": "🎵 <b>Nola Bot</b>ga xush kelibsiz!\n\nMen qo'shiq tanishaman. Audio yuboring yoki qo'shiq nomini yozing!",
        "choose_lang": "🌐 Tilni tanlang / Choose language / Выберите язык:",
        "lang_set": "✅ Til o'zgartirildi!",
        "send_audio": "🎵 Audio fayl yoki ovozli xabar yuboring, men qo'shiqni topaman!",
        "searching": "🔍 Qidirilmoqda...",
        "found": "✅ Qo'shiq topildi!\n\n🎵 <b>Nom:</b> {title}\n🎤 <b>Artist:</b> {artist}\n💿 <b>Album:</b> {album}\n📅 <b>Yil:</b> {year}",
        "not_found": "❌ Qo'shiq topilmadi. Boshqa audio yuboring yoki qo'shiq nomini yozing.",
        "text_search": "🔍 <b>{query}</b> qidirilyapti...",
        "text_result": "🎵 <b>{title}</b>\n🎤 {artist}\n💿 {album}",
        "no_result": "❌ Natija topilmadi.",
        "sub_required": "📢 Botdan foydalanish uchun kanalga obuna bo'ling:",
        "sub_check": "✅ Obunani tekshirish",
        "sub_ok": "✅ Rahmat! Botdan foydalanishingiz mumkin.",
        "sub_fail": "❌ Hali obuna bo'lmagansiz.",
        "help": "ℹ️ <b>Yordam</b>\n\n• Audio yuboring → qo'shiq topiladi\n• Matn yozing → qo'shiq qidiriladi\n• /lang → til o'zgartirish\n• /start → boshidan boshlash",
        "error": "⚠️ Xatolik yuz berdi. Qaytadan urinib ko'ring.",
    },
    "ru": {
        "welcome": "🎵 Добро пожаловать в <b>Nola Bot</b>!\n\nЯ распознаю музыку. Отправьте аудио или напишите название песни!",
        "choose_lang": "🌐 Tilni tanlang / Choose language / Выберите язык:",
        "lang_set": "✅ Язык изменён!",
        "send_audio": "🎵 Отправьте аудио файл или голосовое сообщение, я найду песню!",
        "searching": "🔍 Поиск...",
        "found": "✅ Песня найдена!\n\n🎵 <b>Название:</b> {title}\n🎤 <b>Исполнитель:</b> {artist}\n💿 <b>Альбом:</b> {album}\n📅 <b>Год:</b> {year}",
        "not_found": "❌ Песня не найдена. Попробуйте другое аудио или введите название.",
        "text_search": "🔍 Ищем <b>{query}</b>...",
        "text_result": "🎵 <b>{title}</b>\n🎤 {artist}\n💿 {album}",
        "no_result": "❌ Результат не найден.",
        "sub_required": "📢 Для использования бота подпишитесь на канал:",
        "sub_check": "✅ Проверить подписку",
        "sub_ok": "✅ Спасибо! Теперь вы можете пользоваться ботом.",
        "sub_fail": "❌ Вы ещё не подписались.",
        "help": "ℹ️ <b>Помощь</b>\n\n• Отправьте аудио → распознаёт песню\n• Напишите текст → поиск песни\n• /lang → сменить язык\n• /start → начать заново",
        "error": "⚠️ Произошла ошибка. Попробуйте снова.",
    },
    "en": {
        "welcome": "🎵 Welcome to <b>Nola Bot</b>!\n\nI recognize music. Send an audio or type a song name!",
        "choose_lang": "🌐 Tilni tanlang / Choose language / Выберите язык:",
        "lang_set": "✅ Language changed!",
        "send_audio": "🎵 Send an audio file or voice message, I'll find the song!",
        "searching": "🔍 Searching...",
        "found": "✅ Song found!\n\n🎵 <b>Title:</b> {title}\n🎤 <b>Artist:</b> {artist}\n💿 <b>Album:</b> {album}\n📅 <b>Year:</b> {year}",
        "not_found": "❌ Song not found. Try another audio or type the name.",
        "text_search": "🔍 Searching for <b>{query}</b>...",
        "text_result": "🎵 <b>{title}</b>\n🎤 {artist}\n💿 {album}",
        "no_result": "❌ No results found.",
        "sub_required": "📢 Please subscribe to the channel to use the bot:",
        "sub_check": "✅ Check subscription",
        "sub_ok": "✅ Thank you! You can now use the bot.",
        "sub_fail": "❌ You have not subscribed yet.",
        "help": "ℹ️ <b>Help</b>\n\n• Send audio → recognizes song\n• Type text → search song\n• /lang → change language\n• /start → restart",
        "error": "⚠️ An error occurred. Please try again.",
    }
}

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
        ]
    ])

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

def sub_keyboard(channels):
    buttons = []
    for ch in channels:
        buttons.append([InlineKeyboardButton(text=f"📢 {ch}", url=f"https://t.me/{ch.lstrip('@')}")])
    buttons.append([InlineKeyboardButton(text="✅ Tekshirish / Check", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Kanal ulash", callback_data="admin_channels")],
        [InlineKeyboardButton(text="📣 Reklama (Kanallar)", callback_data="admin_broadcast_channel")],
        [InlineKeyboardButton(text="💬 Reklama (Lichniy)", callback_data="admin_broadcast_private")],
    ])

# ==================== SUBSCRIPTION CHECK ====================
async def check_subscription(bot: Bot, user_id: int) -> bool:
    if not REQUIRED_CHANNELS:
        return True
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status in ["left", "kicked", "banned"]:
                return False
        except Exception:
            pass
    return True

# ==================== SHAZAM (AudD API) ====================
async def recognize_audio(file_path: str) -> dict | None:
    if AUDD_API_KEY == "YOUR_AUDD_API_KEY_HERE":
        # Demo mode - return fake result for testing
        return {"title": "Demo Song", "artist": "Demo Artist", "album": "Demo Album", "release_date": "2024"}
    
    async with aiohttp.ClientSession() as session:
        with open(file_path, "rb") as f:
            data = aiohttp.FormData()
            data.add_field("api_token", AUDD_API_KEY)
            data.add_field("file", f, filename="audio.ogg")
            data.add_field("return", "spotify,apple_music,deezer")
            async with session.post("https://api.audd.io/", data=data) as resp:
                result = await resp.json()
                if result.get("status") == "success" and result.get("result"):
                    r = result["result"]
                    return {
                        "title": r.get("title", "?"),
                        "artist": r.get("artist", "?"),
                        "album": r.get("album", "?"),
                        "release_date": r.get("release_date", "?"),
                        "spotify": r.get("spotify", {}).get("external_urls", {}).get("spotify"),
                        "apple_music": r.get("apple_music", {}).get("url"),
                        "deezer": r.get("deezer", {}).get("link"),
                    }
    return None

# ==================== DEEZER SEARCH ====================
async def search_deezer(query: str) -> list:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.deezer.com/search?q={query}&limit=5") as resp:
            data = await resp.json()
            results = []
            for track in data.get("data", []):
                results.append({
                    "title": track.get("title"),
                    "artist": track.get("artist", {}).get("name"),
                    "album": track.get("album", {}).get("title"),
                    "preview": track.get("preview"),
                    "link": track.get("link"),
                })
            return results

# ==================== FSM STATES ====================
class BroadcastState(StatesGroup):
    waiting_message = State()
    confirm = State()

# ==================== BOT INIT ====================
from aiogram.client.default import DefaultBotProperties
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# ==================== HANDLERS ====================

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    add_user(user.id, user.username, user.full_name)
    
    if not await check_subscription(bot, user.id):
        await message.answer(
            t(user.id, "sub_required"),
            reply_markup=sub_keyboard(REQUIRED_CHANNELS)
        )
        return
    
    await message.answer(t(user.id, "welcome"), reply_markup=main_keyboard(user.id))

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

# Language buttons
@dp.callback_query(F.data.startswith("lang_"))
async def cb_lang(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    set_user_lang(callback.from_user.id, lang)
    await callback.message.edit_text(t(callback.from_user.id, "lang_set"))
    await callback.message.answer(t(callback.from_user.id, "welcome"), reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()

# Subscription check
@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if await check_subscription(bot, uid):
        await callback.message.edit_text(t(uid, "sub_ok"))
        await callback.message.answer(t(uid, "welcome"), reply_markup=main_keyboard(uid))
    else:
        await callback.answer(t(uid, "sub_fail"), show_alert=True)

# Admin: Stats
@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    total, today_new, total_s, today_s = get_stats()
    text = (
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total}</b>\n"
        f"🆕 Bugun yangilar: <b>{today_new}</b>\n"
        f"🔍 Jami qidiruvlar: <b>{total_s}</b>\n"
        f"🔎 Bugun qidiruvlar: <b>{today_s}</b>\n"
        f"📅 Sana: <b>{datetime.now().strftime('%Y-%m-%d %H:%M')}</b>"
    )
    await callback.message.edit_text(text, reply_markup=admin_keyboard())
    await callback.answer()

# Admin: Channels
@dp.callback_query(F.data == "admin_channels")
async def cb_admin_channels(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    channels = "\n".join(REQUIRED_CHANNELS) if REQUIRED_CHANNELS else "Hali kanal ulangan emas"
    text = (
        f"📢 <b>Ulangan kanallar:</b>\n{channels}\n\n"
        f"Kanal qo'shish uchun <code>bot.py</code> faylida <code>REQUIRED_CHANNELS</code> ni o'zgartiring.\n"
        f"Misol: <code>REQUIRED_CHANNELS = [\"@sizning_kanalingiz\"]</code>"
    )
    await callback.message.edit_text(text, reply_markup=admin_keyboard())
    await callback.answer()

# Admin: Broadcast to private
@dp.callback_query(F.data == "admin_broadcast_private")
async def cb_broadcast_private(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastState.waiting_message)
    await state.update_data(target="private")
    await callback.message.answer("💬 Lichniy foydalanuvchilarga yuboriladigan xabarni kiriting:")
    await callback.answer()

# Admin: Broadcast to channels
@dp.callback_query(F.data == "admin_broadcast_channel")
async def cb_broadcast_channel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(BroadcastState.waiting_message)
    await state.update_data(target="channel")
    await callback.message.answer("📣 Kanalga yuboriladigan xabar matnini kiriting:")
    await callback.answer()

# Broadcast message handler
@dp.message(BroadcastState.waiting_message)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    target = data.get("target", "private")
    await state.clear()

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ha, yuborish", callback_data=f"confirm_broadcast_{target}"),
            InlineKeyboardButton(text="❌ Bekor", callback_data="cancel_broadcast"),
        ]
    ])
    await state.update_data(broadcast_text=message.text, target=target)
    await state.set_state(BroadcastState.confirm)
    await message.answer(f"📤 Xabar:\n\n{message.text}\n\nYuborishni tasdiqlaysizmi?", reply_markup=confirm_kb)

@dp.callback_query(F.data.startswith("confirm_broadcast_"))
async def confirm_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    target = callback.data.replace("confirm_broadcast_", "")
    await state.clear()

    users = get_all_users()
    sent = 0
    failed = 0

    status_msg = await callback.message.answer("📤 Yuborilmoqda...")

    if target == "channel" and REQUIRED_CHANNELS:
        for ch in REQUIRED_CHANNELS:
            try:
                await bot.send_message(ch, text)
                sent += 1
            except Exception:
                failed += 1
    else:
        for uid in users:
            try:
                await bot.send_message(uid, text)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

    await status_msg.edit_text(f"✅ Yuborildi: {sent}\n❌ Xato: {failed}")
    await callback.answer()

@dp.callback_query(F.data == "cancel_broadcast")
async def cancel_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()

# Audio / Voice recognition
@dp.message(F.audio | F.voice)
async def handle_audio(message: types.Message):
    uid = message.from_user.id
    add_user(uid, message.from_user.username, message.from_user.full_name)

    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(REQUIRED_CHANNELS))
        return

    status = await message.answer(t(uid, "searching"))

    try:
        file_obj = message.audio or message.voice
        file = await bot.get_file(file_obj.file_id)
        file_path = f"/tmp/audio_{uid}.ogg"
        await bot.download_file(file.file_path, destination=file_path)

        result = await recognize_audio(file_path)

        if result:
            year = result.get("release_date", "?")
            if year and len(str(year)) > 4:
                year = str(year)[:4]

            text = t(uid, "found",
                     title=result.get("title", "?"),
                     artist=result.get("artist", "?"),
                     album=result.get("album", "?"),
                     year=year)

            # Add streaming links if available
            links = []
            if result.get("spotify"):
                links.append(f"<a href='{result['spotify']}'>🟢 Spotify</a>")
            if result.get("apple_music"):
                links.append(f"<a href='{result['apple_music']}'>🎵 Apple Music</a>")
            if result.get("deezer"):
                links.append(f"<a href='{result['deezer']}'>🎧 Deezer</a>")
            if links:
                text += "\n\n🔗 " + " | ".join(links)

            await status.edit_text(text, disable_web_page_preview=True)
            log_search(uid, "audio", result.get("title", "unknown"))
        else:
            await status.edit_text(t(uid, "not_found"))

    except Exception as e:
        logger.error(f"Audio error: {e}")
        await status.edit_text(t(uid, "error"))

# Text search
@dp.message(F.text)
async def handle_text(message: types.Message):
    uid = message.from_user.id
    text = message.text
    add_user(uid, message.from_user.username, message.from_user.full_name)

    if not await check_subscription(bot, uid):
        await message.answer(t(uid, "sub_required"), reply_markup=sub_keyboard(REQUIRED_CHANNELS))
        return

    # Menu buttons
    lang = get_user_lang(uid)
    search_labels = {"uz": "🎵 Qo'shiq qidirish", "ru": "🎵 Поиск песни", "en": "🎵 Search song"}
    lang_labels = {"uz": "🌐 Til", "ru": "🌐 Язык", "en": "🌐 Language"}
    help_labels = {"uz": "ℹ️ Yordam", "ru": "ℹ️ Помощь", "en": "ℹ️ Help"}

    if text == search_labels.get(lang):
        await message.answer(t(uid, "send_audio"))
        return
    if text == lang_labels.get(lang):
        await message.answer(t(uid, "choose_lang"), reply_markup=lang_keyboard())
        return
    if text == help_labels.get(lang):
        await message.answer(t(uid, "help"))
        return

    # Search query
    status = await message.answer(t(uid, "text_search", query=text))
    try:
        results = await search_deezer(text)
        if results:
            response = f"🔍 <b>{text}</b> uchun natijalar:\n\n"
            for i, r in enumerate(results[:5], 1):
                link = r.get("link", "")
                response += f"{i}. <a href='{link}'><b>{r['title']}</b></a> - {r['artist']}\n"
                response += f"   💿 {r['album']}\n\n"
            await status.edit_text(response, disable_web_page_preview=True)
            log_search(uid, text, results[0].get("title", ""))
        else:
            await status.edit_text(t(uid, "no_result"))
    except Exception as e:
        logger.error(f"Search error: {e}")
        await status.edit_text(t(uid, "error"))

# ==================== MAIN ====================
async def main():
    init_db()
    logger.info("Nola Bot ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
