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

# --- ХЕЛПЕР ДЛЯ ИМЕНИ ФАЙЛА ---
def sanitize_filename(text):
    """
    Превращает "ЛСП - Монетка.mp3" в "LSP - Monetka.mp3" (грубо говоря),
    чтобы Телеграм не сходил с ума от кириллицы в заголовках URLInputFile.
    Оставляет только латиницу, цифры и базовые символы.
    """
    # Транслит "на минималках" или просто очистка
    # Телеграму плевать на имя файла внутри, главное чтобы расширение было .mp3
    # А юзер увидит красивые Title и Performer в плеере.
    clean = re.sub(r'[^\w\s-]', '', text) # Убираем эмодзи и спецсимволы
    return clean.strip() + ".mp3"

# --- ОТОБРАЖЕНИЕ (Списка) ---
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


# --- УМНАЯ ЗАГРУЗКА (STEALTH v3.0 - Metadata Fix) ---
async def fast_swap(im_id, source, item_id):
    # 1. ПРОВЕРЯЕМ КЭШ
    cached = await get_cached_info(source, item_id)
    file_id = cached.get('file_id') if cached else None
    msg_id = cached.get('message_id') if cached else None

    track = None
    # Если в кэше нет, готовимся качать
    if not file_id:
        try:
            if source == 'SC': track = await engine.sc.resolve_url_by_id(item_id)
            else: track = await engine.yt.resolve_url(item_id)
            
            if not track or not track.get('url'):
                try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌")
                except: pass
                return
        except: return

    # --- ПОДГОТОВКА МЕТАДАННЫХ ---
    # Красивые данные для глаз (в плеере)
    display_title = track['title'][:100] if track else "Track"
    display_performer = track['artist'][:64] if track else "Artist"
    
    # Техническое имя файла (безопасное, чтобы Телеграм принял)
    raw_filename_str = f"{display_performer} - {display_title}"
    safe_filename = sanitize_filename(raw_filename_str)
    
    thumb_url = track.get('thumbnail') if track else None
    thumb_obj = URLInputFile(thumb_url) if thumb_url else None

    # --- ПОПЫТКА 1: СТЕЛС (ПРЯМОЙ URL + КАРТИНКА) ---
    # Пытаемся сделать красиво сразу в чате
    if not file_id:
        try:
            await bot_instance.edit_message_media(
                inline_message_id=im_id,
                media=InputMediaAudio(
                    media=URLInputFile(track['url'], filename=safe_filename), # <-- ВАЖНО: filename
                    thumbnail=thumb_obj,
                    title=display_title,        # <-- ВАЖНО: Красивое название
                    performer=display_performer,# <-- ВАЖНО: Красивый артист
                    caption=f"@{ (await bot_instance.get_me()).username }"
                ),
                reply_markup=None
            )
            return # Успех, выходим
        except TelegramBadRequest:
            # Если ошибка "Forbidden" - идем в канал
            pass 
        except Exception:
            # Если ошибка другая (например, картинка битая), пробуем без картинки
            pass

    # --- ПОПЫТКА 2: СТЕЛС (ПРЯМОЙ URL, БЕЗ КАРТИНКИ) ---
    # Часто бывает, что URL аудио рабочий, а картинка - нет.
    # Спасаем ситуацию, чтобы не гадить в канал.
    if not file_id:
        try:
            await bot_instance.edit_message_media(
                inline_message_id=im_id,
                media=InputMediaAudio(
                    media=URLInputFile(track['url'], filename=safe_filename),
                    # thumbnail=None, # Без картинки
                    title=display_title,
                    performer=display_performer,
                    caption=f"@{ (await bot_instance.get_me()).username }"
                ),
                reply_markup=None
            )
            return # Успех без картинки
        except Exception:
            pass # Если и тут беда, значит проблема с аудио-ссылкой или запрет

    # --- ПОПЫТКА 3: ЗАГРУЗКА В КАНАЛ (ПОСЛЕДНЯЯ НАДЕЖДА) ---
    # Только если предыдущие методы упали (или файл новый и запрет в чате)
    
    if not file_id and track:
        try:
            # Грузим в канал
            dump_msg = await bot_instance.send_audio(
                chat_id=DUMP_CHANNEL_ID,
                audio=track['url'],
                thumbnail=thumb_obj, # Пробуем с картинкой
                title=display_title,
                performer=display_performer,
                caption=f"#{source}|{item_id}"
            )
            file_id = dump_msg.audio.file_id
            msg_id = dump_msg.message_id
            
            # Кэшируем
            asyncio.create_task(save_cached_info(source, item_id, file_id, msg_id))
        except Exception: 
            # Если не вышло - пробуем без картинки в канал
            try:
                dump_msg = await bot_instance.send_audio(
                    chat_id=DUMP_CHANNEL_ID,
                    audio=track['url'],
                    title=display_title,
                    performer=display_performer,
                    caption=f"#{source}|{item_id}"
                )
                file_id = dump_msg.audio.file_id
                msg_id = dump_msg.message_id
                asyncio.create_task(save_cached_info(source, item_id, file_id, msg_id))
            except:
                try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌ Err")
                except: pass
                return 

    # --- ПОПЫТКА 4: ФИНАЛЬНАЯ ПОДМЕНА (ПОСЛЕ КАНАЛА) ---
    if file_id:
        try:
            # Используем file_id. Тут метаданные подтянутся из самого файла (который в канале)
            # Но лучше продублировать для надежности
            await bot_instance.edit_message_media(
                inline_message_id=im_id,
                media=InputMediaAudio(
                    media=file_id,
                    caption=f"@{ (await bot_instance.get_me()).username }",
                    title=display_title,
                    performer=display_performer,
                    thumbnail=thumb_obj
                ),
                reply_markup=None
            )
        except TelegramBadRequest:
            # --- ОБХОД БЛОКИРОВКИ (ЕСЛИ В ЧАТЕ ЗАПРЕТ) ---
            if msg_id:
                link = f"https://t.me/{DUMP_CHANNEL_USERNAME}/{msg_id}" if DUMP_CHANNEL_USERNAME \
                       else f"https://t.me/c/{str(DUMP_CHANNEL_ID).replace('-100', '')}/{msg_id}"
                try:
                    await bot_instance.edit_message_text(
                        inline_message_id=im_id,
                        text=f"<a href='{link}'>&#8203;</a>🚫", 
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="▶ Play", url=link)
                        ]])
                    )
                except: pass
        except Exception: pass

# --- ТРИГГЕРЫ ---
@router.chosen_inline_result()
async def chosen_handler(chosen: ChosenInlineResult):
    if chosen.result_id.startswith("dl:"):
        p = chosen.result_id.split(":")
        await fast_swap(chosen.inline_message_id, p[1], p[2])

@router.callback_query(lambda c: c.data.startswith("f:"))
async def force_dl(call: types.CallbackQuery):
    _, src, iid = call.data.split(":")
    if call.inline_message_id:
        await fast_swap(call.inline_message_id, src, iid)
    await call.answer()