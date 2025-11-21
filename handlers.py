import asyncio
import hashlib
from aiogram import Router, types, Bot
from aiogram.types import InlineQuery, InlineQueryResultAudio, Message, CallbackQuery
from aiogram.filters import Command
from config import FINAL_LIMIT, INLINE_LIMIT, ADMIN_ID
from database import add_user, get_active_users, mark_inactive
from utils import format_plays

router = Router()

# Глобальные переменные (будут установлены через setup_handlers)
engine = None
bot_instance = None 
USER_SOURCES = {}

def setup_handlers(main_engine, main_bot):
    global engine, bot_instance
    engine = main_engine
    bot_instance = main_bot

# --- КОМАНДЫ ---

@router.message(Command("start"))
async def start_command(message: Message):
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
        
    users = await get_active_users()
    await message.answer(f"Рассылка на {len(users)} юзеров...")
    
    count = 0
    for (uid,) in users:
        try:
            await bot_instance.send_message(uid, text)
            count += 1
            await asyncio.sleep(0.03)
        except Exception as e:
            if "Forbidden" in str(e): await mark_inactive(uid)
    await message.answer(f"Готово. Отправлено: {count}")

@router.message(Command("source"))
async def cmd_source(message: Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Все источники", callback_data="src_all")],
        [types.InlineKeyboardButton(text="☁️ Только SoundCloud", callback_data="src_sc")],
        [types.InlineKeyboardButton(text="▶️ Только YouTube", callback_data="src_yt")]
    ])
    current = USER_SOURCES.get(message.from_user.id, 'all').upper()
    await message.answer(f"⚙️ <b>Фильтр поиска</b>\nСейчас: {current}", reply_markup=kb, parse_mode="HTML")

@router.callback_query(lambda c: c.data.startswith("src_"))
async def set_source(call: CallbackQuery):
    mode = call.data.split("_")[1]
    USER_SOURCES[call.from_user.id] = mode
    text_map = {'all': "ВЕЗДЕ", 'sc': "☁️ SOUNDCLOUD", 'yt': "▶️ YOUTUBE"}
    await call.message.edit_text(f"✅ Режим установлен: <b>{text_map[mode]}</b>", parse_mode="HTML")
    await call.answer()

# --- ПОИСК В ЧАТЕ ---

@router.message()
async def search_handler(message: Message):
    if not message.text or message.text.startswith("/"): return
    query = message.text.strip()
    if len(query) < 2: return

    asyncio.create_task(add_user(message.from_user.id))
    mode = USER_SOURCES.get(message.from_user.id, 'all')
    
    await bot_instance.send_chat_action(message.chat.id, "typing")
    
    # Ищем, сортируем и берем топ
    all_candidates = await engine.search(query, mode)
    results = all_candidates[:FINAL_LIMIT]
    
    if not results:
        await message.answer("❌ Ничего не найдено.")
        return

    # Формируем текст и компактные кнопки
    res_text = f"🔎 <b>Результаты:</b> {query}\n\n"
    buttons_row_1 = []
    buttons_row_2 = []
    
    for i, item in enumerate(results):
        num = i + 1
        icon = "☁️" if item['source'] == 'SC' else "▶️"
        
        # Очищаем название от дублей (Artist - Artist Title -> Artist - Title)
        clean_title = item['title'].replace(item['artist'], "").strip(" -|")
        if not clean_title: clean_title = item['title']
        
        res_text += f"<b>{num}.</b> {icon} {item['artist']} — {clean_title[:40]}\n"
        
        # Кнопка: dl|SOURCE|ID
        btn = types.InlineKeyboardButton(text=f"{num}", callback_data=f"dl|{item['source']}|{item['id']}")
        
        if i < 5: buttons_row_1.append(btn)
        else: buttons_row_2.append(btn)

    kb_rows = []
    if buttons_row_1: kb_rows.append(buttons_row_1)
    if buttons_row_2: kb_rows.append(buttons_row_2)
    
    await message.answer(res_text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

# --- СКАЧИВАНИЕ ---

@router.callback_query(lambda c: c.data.startswith("dl|"))
async def download_callback(call: CallbackQuery):
    _, source, item_id = call.data.split("|")
    await call.answer("🚀 Загружаю...")
    
    try:
        url = None
        if source == 'SC':
            url = await engine.sc.resolve_url_by_id(item_id)
        else:
            url = await engine.yt.resolve_url(item_id)
            
        if not url:
            await call.message.answer("❌ Не удалось получить ссылку (истекла или блок).")
            return
            
        await bot_instance.send_audio(
            chat_id=call.from_user.id,
            audio=url,
            caption=f"🤖 via @{(await bot_instance.get_me()).username}"
        )
    except Exception as e:
        await call.message.answer("❌ Ошибка отправки файла.")

# --- ИНЛАЙН РЕЖИМ ---

@router.inline_query()
async def inline_handler(query: InlineQuery):
    text = query.query.strip()
    if len(text) < 2: return
    
    # Поиск
    mode = USER_SOURCES.get(query.from_user.id, 'all')
    all_results = await engine.search(text, mode)
    top_results = all_results[:INLINE_LIMIT]
    
    # Для инлайна нужно получить ссылки СРАЗУ
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
            performer=f"{icon} {item['artist']} | {format_plays(item['playback_count'])}",
            audio_duration=int(item['duration'] / 1000), 
            thumbnail_url=item['artwork_url']
        ))
        
    try: 
        await query.answer(iq_results, cache_time=300, is_personal=True)
    except: pass