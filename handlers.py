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
    """Убирает спецсимволы, чтобы Телеграм принял имя файла"""
    # Транслит не обязателен, но убираем кавычки и слеши
    s = re.sub(r'[\\/*?:"<>|]', '', text)
    return s.strip()[:50] + ".mp3" # Ограничим длину 50 символами

# --- 1. СПИСОК (Article - Мгновенно) ---
@router.inline_query()
async def inline_handler(query: InlineQuery):
    text = query.query.strip()
    if len(text) < 2: return

    results = await engine.search(text, 'all')
    if not results: return

    iq_results = []
    for item in results[:INLINE_LIMIT]:
        result_id = f"dl:{item['source']}:{item['id']}"
        
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
                message_text="⌛", 
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=".", callback_data=f"f:{item['source']}:{item['id']}")
            ]])
        ))

    await query.answer(iq_results, cache_time=300, is_personal=True)

# --- 2. ЛОГИКА "TOP BOT" (STEALTH -> FALLBACK) ---
async def process_track(im_id, source, item_id):
    # А. ПРОВЕРКА КЭША (Может этот трек уже загружали в канал раньше?)
    cached = await get_cached_info(source, item_id)
    file_id = cached.get('file_id') if cached else None
    msg_id = cached.get('message_id') if cached else None

    # Подготовка данных (URL, Title, Artist)
    track = None
    if not file_id:
        try:
            if source == 'SC': track = await engine.sc.resolve_url_by_id(item_id)
            else: track = await engine.yt.resolve_url(item_id)
            
            if not track or not track.get('url'):
                try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌")
                except: pass
                return
        except: return

    # Метаданные
    title = track['title'][:100] if track else "Track"
    performer = track['artist'][:64] if track else "Artist"
    thumb_url = track.get('thumbnail') if track else None
    
    # ВАЖНО: Формируем красивое имя файла "Artist - Title.mp3"
    # Именно это убирает "рандомные буквы" при прямой отправке
    safe_name = clean_filename(f"{performer} - {title}")
    
    # Создаем объекты для отправки
    # Если есть file_id (из кэша), используем его (это быстро)
    # Если нет - используем URLInputFile с явным именем файла (это Стелс)
    if file_id:
        media_obj = file_id
    else:
        media_obj = URLInputFile(track['url'], filename=safe_name)
    
    thumb_obj = URLInputFile(thumb_url) if thumb_url else None

    # --- ПОПЫТКА 1: STEALTH (ПРЯМО В ЧАТ) ---
    # Пытаемся заменить "⌛" на Аудио.
    # Если чат обычный - это сработает. В канал ничего не пойдет.
    try:
        await bot_instance.edit_message_media(
            inline_message_id=im_id,
            media=InputMediaAudio(
                media=media_obj,
                thumbnail=thumb_obj,
                title=title,         # Мета для плеера
                performer=performer, # Мета для плеера
                caption=f"@{ (await bot_instance.get_me()).username }"
            ),
            reply_markup=None
        )
        return # Успех! Выходим. Канал чист.
    except TelegramBadRequest as e:
        # Ловим конкретную ошибку: "Audio messages are forbidden" (Запрет музыки)
        # Только в этом случае идем дальше, к загрузке в канал.
        if "forbidden" not in str(e).lower() and "rights" not in str(e).lower():
            # Если ошибка другая (например, битая картинка) - пробуем без картинки
             pass 
    except Exception:
        pass

    # --- ПОПЫТКА 1.5: STEALTH БЕЗ КАРТИНКИ ---
    # (Если вдруг упало из-за кривой обложки, но музыка разрешена)
    if not file_id and thumb_obj:
        try:
            await bot_instance.edit_message_media(
                inline_message_id=im_id,
                media=InputMediaAudio(
                    media=media_obj, # Тот же URL/ID
                    title=title,
                    performer=performer,
                    caption=f"@{ (await bot_instance.get_me()).username }"
                    # Без thumbnail
                ),
                reply_markup=None
            )
            return # Успех без картинки. Канал чист.
        except TelegramBadRequest:
            pass # Значит точно запрет музыки
        except: pass

    # --- ПОПЫТКА 2: ЗАГРУЗКА В КАНАЛ (FALLBACK) ---
    # Мы здесь, только если edit_message_media вернул ошибку (запрет музыки).
    # Теперь мы ОБЯЗАНЫ загрузить файл в канал, чтобы дать ссылку-обход.
    
    if not file_id and track:
        try:
            # Грузим в DUMP
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
            
            # Запоминаем в кэш
            asyncio.create_task(save_cached_info(source, item_id, file_id, msg_id))
        except Exception:
             # Если даже в канал не лезет (например, файл > 50МБ)
             return

    # --- ПОПЫТКА 3: ССЫЛКА-ОБХОД ---
    # Раз мы загрузили (или нашли) файл в канале, даем ссылку на него
    if msg_id:
        link = f"https://t.me/{DUMP_CHANNEL_USERNAME}/{msg_id}" if DUMP_CHANNEL_USERNAME \
               else f"https://t.me/c/{str(DUMP_CHANNEL_ID).replace('-100', '')}/{msg_id}"
        
        try:
            await bot_instance.edit_message_text(
                inline_message_id=im_id,
                text=f"<a href='{link}'>&#8203;</a>🚫 <b>Музыка запрещена</b>", 
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="▶ Слушать здесь", url=link)
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