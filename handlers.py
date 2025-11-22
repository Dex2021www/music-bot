import asyncio
import re
from aiogram import Router, types
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    InputMediaAudio, ChosenInlineResult, InlineKeyboardMarkup, InlineKeyboardButton,
    URLInputFile
)
from aiogram.exceptions import TelegramBadRequest
from config import INLINE_LIMIT, DUMP_CHANNEL_ID, DUMP_CHANNEL_USERNAME
from database import get_cached_info, save_cached_info
from utils import format_plays

router = Router()
engine = None
bot_instance = None 

def setup_handlers(main_engine, main_bot):
    global engine, bot_instance
    engine = main_engine
    bot_instance = main_bot

def clean_filename(text):
    """Создает красивое имя файла: 'Artist - Track.mp3'"""
    # Убираем запрещенные символы, оставляем красоту
    s = re.sub(r'[\\/*?:"<>|]', '', text)
    return s.strip()[:60] + ".mp3"

# --- 1. СПИСОК (МГНОВЕННЫЙ) ---
@router.inline_query()
async def inline_handler(query: InlineQuery):
    text = query.query.strip()
    if len(text) < 2: return

    results = await engine.search(text, 'all')
    if not results: return

    iq_results = []
    for item in results[:INLINE_LIMIT]:
        result_id = f"dl:{item['source']}:{item['id']}"
        
        # Красивый заголовок в поиске
        clean_title = item['title'].replace(item['artist'], '').strip(' -|:').replace('.mp3', '')
        if not clean_title: clean_title = item['title']
        
        m, s = divmod(item['duration'] // 1000, 60)
        thumb = item.get('artwork_url')

        iq_results.append(InlineQueryResultArticle(
            id=result_id,
            title=clean_title,
            description=f"{item['artist']}\n{m:02d}:{s:02d} • {format_plays(item['playback_count'])}",
            thumbnail_url=thumb, 
            input_message_content=InputTextMessageContent(
                message_text="⌛", # Заглушка
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=".", callback_data=f"f:{item['source']}:{item['id']}")
            ]])
        ))

    await query.answer(iq_results, cache_time=300, is_personal=True)


# --- 2. ОБРАБОТКА (РЕЖИМ ФАНТОМ) ---
async def process_track(im_id, source, item_id):
    # А. ПРОВЕРКА КЭША
    cached = await get_cached_info(source, item_id)
    file_id = cached.get('file_id') if cached else None
    msg_id = cached.get('message_id') if cached else None

    # Б. ЕСЛИ НЕТ В КЭШЕ - ГРУЗИМ
    if not file_id:
        try:
            # 1. Получаем ссылки
            track = None
            if source == 'SC': track = await engine.sc.resolve_url_by_id(item_id)
            else: track = await engine.yt.resolve_url(item_id)
            
            if not track or not track.get('url'):
                try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌")
                except: pass
                return

            # 2. Подготовка данных
            title = track['title'][:100]
            performer = track['artist'][:64]
            thumb_url = track.get('thumbnail')
            
            # Имя файла, которое увидит юзер при скачивании
            safe_name = clean_filename(f"{performer} - {title}")

            # 3. ФАНТОМНАЯ ЗАГРУЗКА (В канал -> Получить ID -> Удалить)
            # Это единственный способ получить обложку и теги!
            dump_msg = await bot_instance.send_audio(
                chat_id=DUMP_CHANNEL_ID,
                audio=URLInputFile(track['url'], filename=safe_name), # <-- ИМЯ ФАЙЛА
                thumbnail=URLInputFile(thumb_url) if thumb_url else None, # <-- ОБЛОЖКА
                title=title,        # <-- ТЕГ НАЗВАНИЯ
                performer=performer,# <-- ТЕГ АВТОРА
                caption=f"#{source}|{item_id}"
            )
            
            file_id = dump_msg.audio.file_id
            msg_id = dump_msg.message_id
            
            # 4. МГНОВЕННО УДАЛЯЕМ ИЗ КАНАЛА (ЧИСТОТА)
            # Задержка 0.1 сек, чтобы телеграм успел обработать
            asyncio.create_task(delete_phantom_msg(DUMP_CHANNEL_ID, msg_id))
            
            # Сохраняем в кэш (file_id живет даже после удаления сообщения некоторое время)
            asyncio.create_task(save_cached_info(source, item_id, file_id, msg_id))

        except Exception as e:
            print(f"DL Error: {e}")
            try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌ Err")
            except: pass
            return

    # В. ОТДАЕМ ФАЙЛ ЮЗЕРУ
    if file_id:
        try:
            await bot_instance.edit_message_media(
                inline_message_id=im_id,
                media=InputMediaAudio(
                    media=file_id,
                    caption=f"@{ (await bot_instance.get_me()).username }"
                ),
                reply_markup=None
            )
        except TelegramBadRequest:
            # Если запрет музыки - кидаем ссылку (она может не сработать, если сообщение удалено, но это компромисс)
            # В этом случае (запрет музыки) сообщение лучше не удалять, но мы выбрали чистоту.
            try:
                 await bot_instance.edit_message_text(
                    inline_message_id=im_id,
                    text=f"🚫 <b>Музыка запрещена в этом чате.</b>", 
                    parse_mode="HTML"
                )
            except: pass
        except Exception: pass

async def delete_phantom_msg(chat_id, msg_id):
    """Удаляет сообщение из канала, чтобы не мусорить"""
    try:
        await asyncio.sleep(2) # Даем пару секунд на всякий случай
        await bot_instance.delete_message(chat_id, msg_id)
    except: pass

# --- ТРИГГЕРЫ ---
@router.chosen_inline_result()
async def chosen_handler(chosen: ChosenInlineResult):
    if chosen.result_id.startswith("dl:"):
        p = chosen.result_id.split(":")
        await process_track(chosen.inline_message_id, p[1], p[2])

@router.callback_query(lambda c: c.data.startswith("f:"))
async def force_dl(call: types.CallbackQuery):
    _, src, iid = call.data.split(":")
    if call.inline_message_id:
        await process_track(call.inline_message_id, src, iid)
    await call.answer()