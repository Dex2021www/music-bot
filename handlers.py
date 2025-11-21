import asyncio
import hashlib
from cachetools import TTLCache
from aiogram import Router, types
from aiogram.types import InlineQuery, InlineQueryResultAudio, Message, CallbackQuery
from aiogram.filters import Command
from config import FINAL_LIMIT, INLINE_LIMIT, ADMIN_ID
from database import add_user, get_users_count, get_active_users_cursor, mark_inactive
from utils import format_plays

router = Router()

engine = None
bot_instance = None 

# ОПТИМИЗАЦИЯ RAM:
# Храним настройки только 1000 последних активных пользователей.
# Если юзера нет в кэше, считаем 'all'.
# TTL = 1 час.
USER_SOURCES = TTLCache(maxsize=1000, ttl=3600)

def setup_handlers(main_engine, main_bot):
    global engine, bot_instance
    engine = main_engine
    bot_instance = main_bot

# --- КОМАНДЫ ---

@router.message(Command("start"))
async def start_command(message: Message):
    # Fire and forget (не ждем записи в БД)
    asyncio.create_task(add_user(message.from_user.id))
    await message.answer(
        "<b>Музыкальный бот</b>\n\n"
        "Просто напиши название песни в чат.\n"
        "Выбрать источники: /source",
        parse_mode="HTML"
    )

@router.message(Command("send"))
async def send_ad(message: Message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text[5:].strip()
    if not text: 
        await message.answer("Пустое сообщение")
        return
        
    msg = await message.answer("🚀 Начинаю рассылку (cursor mode)...")
    
    count = 0
    # Используем курсор, чтобы не грузить всех юзеров в RAM
    conn_ctx = await get_active_users_cursor()
    if not conn_ctx:
        await msg.edit_text("❌ Нет соединения с БД")
        return

    try:
        async with conn_ctx as connection:
            # Создаем транзакцию для курсора
            async with connection.transaction():
                # Читаем пачками по 100 штук
                async for record in connection.cursor("SELECT user_id FROM users WHERE is_active = TRUE"):
                    uid = record['user_id']
                    try:
                        await bot_instance.send_message(uid, text)
                        count += 1
                        # Небольшая пауза, чтобы не словить FloodWait от Telegram
                        await asyncio.sleep(0.05) 
                    except Exception as e:
                        err = str(e)
                        if "Forbidden" in err or "blocked" in err:
                            asyncio.create_task(mark_inactive(uid))
                    
                    if count % 100 == 0:
                        await asyncio.sleep(1) # Даем передышку CPU и сети
    except Exception as e:
        await message.answer(f"Ошибка рассылки: {e}")
    
    await message.answer(f"✅ Готово. Отправлено: {count}")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    # Count делается в БД, в RAM прилетает только одно число
    count = await get_users_count()
    await message.answer(f"📊 Пользователей в базе: <b>{count}</b>", parse_mode="HTML")

@router.message(Command("source"))
async def cmd_source(message: Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Все источники", callback_data="src_all")],
        [types.InlineKeyboardButton(text="☁️ Только SoundCloud", callback_data="src_sc")],
        [types.InlineKeyboardButton(text="▶️ Только YouTube", callback_data="src_yt")]
    ])
    # По умолчанию 'all', если нет в кэше
    current = USER_SOURCES.get(message.from_user.id, 'all').upper()
    await message.answer(f"⚙️ <b>Фильтр поиска</b>\nСейчас: {current}", reply_markup=kb, parse_mode="HTML")

@router.callback_query(lambda c: c.data.startswith("src_"))
async def set_source(call: CallbackQuery):
    mode = call.data.split("_")[1]
    USER_SOURCES[call.from_user.id] = mode
    text_map = {'all': "ВЕЗДЕ", 'sc': "☁️ SOUNDCLOUD", 'yt': "▶️ YOUTUBE"}
    await call.message.edit_text(f"✅ Режим установлен: <b>{text_map[mode]}</b>", parse_mode="HTML")
    await call.answer()

@router.message()
async def search_handler(message: Message):
    if not message.text or message.text.startswith("/"): return
    query = message.text.strip()
    if len(query) < 2: return

    # Fire-and-forget добавление юзера
    asyncio.create_task(add_user(message.from_user.id))
    mode = USER_SOURCES.get(message.from_user.id, 'all')
    
    await bot_instance.send_chat_action(message.chat.id, "typing")
    
    all_candidates = await engine.search(query, mode)
    results = all_candidates[:FINAL_LIMIT]
    
    if not results:
        await message.answer("❌ Ничего не найдено.")
        return

    res_text = f"🔎 <b>Результаты:</b> {query}\n\n"
    buttons_row_1 = []
    buttons_row_2 = []
    
    for i, item in enumerate(results):
        num = i + 1
        icon = "☁️" if item['source'] == 'SC' else "▶️"
        
        clean_title = item['title'].replace(item['artist'], "").strip(" -|")
        if not clean_title: clean_title = item['title']
        
        # Лимитируем длину строки в RAM
        res_text += f"<b>{num}.</b> {icon} {item['artist']} — {clean_title[:40]}\n"
        
        # Используем короткий callback data
        btn = types.InlineKeyboardButton(text=f"{num}", callback_data=f"dl|{item['source']}|{item['id']}")
        
        if i < 5: buttons_row_1.append(btn)
        else: buttons_row_2.append(btn)

    kb_rows = []
    if buttons_row_1: kb_rows.append(buttons_row_1)
    if buttons_row_2: kb_rows.append(buttons_row_2)
    
    await message.answer(res_text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

@router.callback_query(lambda c: c.data.startswith("dl|"))
async def download_callback(call: CallbackQuery):
    _, source, item_id = call.data.split("|")
    # Сразу даем фидбек, чтобы телеграм не крутил часики
    await call.answer("🚀 Загружаю...")
    
    try:
        url = None
        if source == 'SC':
            url = await engine.sc.resolve_url_by_id(item_id)
        else:
            url = await engine.yt.resolve_url(item_id)
            
        if not url:
            await call.message.answer("❌ Не удалось получить ссылку.")
            return
            
        await bot_instance.send_audio(
            chat_id=call.from_user.id,
            audio=url,
            caption=f"🤖 @{(await bot_instance.get_me()).username}"
        )
    except Exception:
        await call.message.answer("❌ Ошибка отправки файла.")

@router.inline_query()
async def inline_handler(query: InlineQuery):
    text = query.query.strip()
    if len(text) < 2: return
    
    mode = USER_SOURCES.get(query.from_user.id, 'all')
    all_results = await engine.search(text, mode)
    top_results = all_results[:INLINE_LIMIT]
    
    tasks = []
    for item in top_results:
        if item['source'] == 'SC': 
            tasks.append(engine.sc.resolve_url_by_id(item['id']))
        else: 
            tasks.append(engine.yt.resolve_url(item['id']))
            
    urls = await asyncio.gather(*tasks, return_exceptions=True)
    iq_results = []
    
    for item, real_url in zip(top_results, urls):
        if not real_url or not isinstance(real_url, str): continue
        
        res_id = hashlib.md5(f"{item['source']}_{item['id']}".encode()).hexdigest()
        icon = "☁️" if item['source'] == 'SC' else "▶️"
        
        iq_results.append(InlineQueryResultAudio(
            id=res_id, 
            audio_url=real_url, 
            title=item['title'],
            performer=f"{icon} {item['artist']}",
            audio_duration=int(item['duration'] / 1000)
            # Убрал artwork, иногда они тяжелые и ломают инлайн превью, если URL кривой
        ))
        
    try: 
        await query.answer(iq_results, cache_time=300, is_personal=True)
    except: pass