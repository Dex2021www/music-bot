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
    """Делает имя файла красивым и безопасным"""
    # Убираем всё кроме букв, цифр, скобок и дефисов
    s = re.sub(r'[\\/*?:"<>|]', '', text)
    return s.strip()[:60] + ".mp3"

# --- 1. СПИСОК (КАК НА ВИДЕО 00:01) ---
@router.inline_query()
async def inline_handler(query: InlineQuery):
    text = query.query.strip()
    if len(text) < 2: return

    results = await engine.search(text, 'all')
    if not results: return

    iq_results = []
    for item in results[:INLINE_LIMIT]:
        result_id = f"dl:{item['source']}:{item['id']}"
        
        # Чистим заголовок для красивого списка
        clean_title = item['title'].replace(item['artist'], '').strip(' -|:').replace('.mp3', '')
        if not clean_title: clean_title = item['title']
        
        m, s = divmod(item['duration'] // 1000, 60)
        thumb = item.get('artwork_url') # Картинка для списка

        iq_results.append(InlineQueryResultArticle(
            id=result_id,
            title=clean_title,
            description=f"{item['artist']}\n{m:02d}:{s:02d} • {format_plays(item['playback_count'])}",
            thumbnail_url=thumb, 
            # Сообщение "Загрузка" (КАК НА ВИДЕО 00:04)
            input_message_content=InputTextMessageContent(
                message_text=f"💿 <b>{item['artist']} - {clean_title}</b>\n⏳ Загрузка...", 
                parse_mode="HTML"
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=".", callback_data=f"f:{item['source']}:{item['id']}")
            ]])
        ))

    await query.answer(iq_results, cache_time=300, is_personal=True)

# --- 2. ЛОГИКА ПРЕВРАЩЕНИЯ (КАК НА ВИДЕО 00:06) ---
async def process_track(im_id, source, item_id):
    # А. ПРОВЕРКА КЭША
    cached = await get_cached_info(source, item_id)
    file_id = cached.get('file_id') if cached else None
    msg_id = cached.get('message_id') if cached else None

    # Подготовка данных
    track = None
    if not file_id:
        try:
            if source == 'SC': track = await engine.sc.resolve_url_by_id(item_id)
            else: track = await engine.yt.resolve_url(item_id)
            
            if not track or not track.get('url'):
                try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌ Ошибка доступа")
                except: pass
                return
        except: return

    # Метаданные (ДЛЯ ПЛЕЕРА)
    title = track['title'][:100] if track else "Track"
    performer = track['artist'][:64] if track else "Artist"
    
    # Ссылка на обложку
    thumb_url = track.get('thumbnail') if track else None
    
    # Имя файла (Чтобы не было рандомных букв!)
    # Мы говорим телеграму: "Назови файл вот так"
    safe_name = clean_filename(f"{performer} - {title}")
    
    # --- СБОРКА ОБЪЕКТОВ ---
    if file_id:
        # Если есть в кэше - используем ID (мгновенно)
        media_obj = file_id
        thumb_obj = None # Обложка уже внутри файла
    else:
        # Если качаем с нуля - используем URLInputFile
        # ВАЖНО: передаем filename!
        media_obj = URLInputFile(track['url'], filename=safe_name)
        # ВАЖНО: передаем thumbnail!
        thumb_obj = URLInputFile(thumb_url) if thumb_url else None

    # --- ПОПЫТКА 1: ИДЕАЛЬНЫЙ СТЕЛС (С КАРТИНКОЙ) ---
    try:
        await bot_instance.edit_message_media(
            inline_message_id=im_id,
            media=InputMediaAudio(
                media=media_obj,
                thumbnail=thumb_obj,    # <-- Вот тут магия обложки
                title=title,            # <-- Красивое название
                performer=performer,    # <-- Красивый автор
                caption=f"@{ (await bot_instance.get_me()).username }"
            ),
            reply_markup=None
        )
        # Если сработало - мы победили. В канале ничего нет. Обложка есть.
        return 
    except TelegramBadRequest as e:
        # Если ошибка "Forbidden" (запрет музыки) -> идем в Plan B
        if "forbidden" in str(e).lower():
            pass 
        # Если ошибка другая (например, телеграм не смог скачать картинку) -> пробуем без картинки
        elif not file_id:
            try:
                await bot_instance.edit_message_media(
                    inline_message_id=im_id,
                    media=InputMediaAudio(
                        media=media_obj, # Тот же файл с красивым именем
                        title=title,
                        performer=performer,
                        caption=f"@{ (await bot_instance.get_me()).username }"
                        # Без thumbnail
                    ),
                    reply_markup=None
                )
                return
            except: pass
    except Exception: pass

    # --- ПОПЫТКА 2: ЗАГРУЗКА В КАНАЛ (ТОЛЬКО ЕСЛИ ЗАПРЕТ) ---
    if not file_id and track:
        try:
            dump_msg = await bot_instance.send_audio(
                chat_id=DUMP_CHANNEL_ID,
                audio=URLInputFile(track['url'], filename=safe_name),
                thumbnail=URLInputFile(thumb_url) if thumb_url else None,
                title=title,
                performer=performer,
                caption=f"#{source}|{item_id}"
            )
            file_id = dump_msg.audio.file_id
            msg_id = dump_msg.message_id
            
            # Сохраняем в кэш
            asyncio.create_task(save_cached_info(source, item_id, file_id, msg_id))
        except Exception: return

    # --- ПОПЫТКА 3: ССЫЛКА-ОБХОД ---
    if msg_id:
        link = f"https://t.me/{DUMP_CHANNEL_USERNAME}/{msg_id}" if DUMP_CHANNEL_USERNAME \
               else f"https://t.me/c/{str(DUMP_CHANNEL_ID).replace('-100', '')}/{msg_id}"
        
        try:
            await bot_instance.edit_message_text(
                inline_message_id=im_id,
                text=f"<a href='{link}'>&#8203;</a>🚫 <b>Музыка запрещена</b>", 
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="▶ Слушать в канале", url=link)
                ]])
            )
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