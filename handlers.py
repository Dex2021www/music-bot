import asyncio
from aiogram import Router, types
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    InputMediaAudio, ChosenInlineResult, InlineKeyboardMarkup, InlineKeyboardButton,
    URLInputFile
)
from aiogram.exceptions import TelegramBadRequest
from config import INLINE_LIMIT, DUMP_CHANNEL_ID, DUMP_CHANNEL_USERNAME, DEFAULT_ICON_URL
from database import get_cached_info, save_cached_info
from utils import format_plays

router = Router()
engine = None
bot_instance = None 

def setup_handlers(main_engine, main_bot):
    global engine, bot_instance
    engine = main_engine
    bot_instance = main_bot

# ОТОБРАЖЕНИЕ (Списка)
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


# УМНАЯ ЗАГРУЗКА (STEALTH v2.0)
async def fast_swap(im_id, source, item_id):
    # 1. ПРОВЕРЯЕМ КЭШ (Если файл уже был в канале - это идеально)
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

    # Подготовка метаданных (ОБЯЗАТЕЛЬНО для красивого вида)
    # Если есть track (мы только что нашли), берем из него. Если file_id (из кэша), берем дефолт.
    title = track['title'][:100] if track else "Track"
    performer = track['artist'][:64] if track else "Artist"
    
    # Чистим имя файла от мусора
    safe_filename = f"{performer} - {title}.mp3".replace('/', '').replace('\\', '').replace('"', '')
    
    thumb_url = track.get('thumbnail') if track else None
    
    # Объект обложки (если есть)
    thumb_obj = URLInputFile(thumb_url) if thumb_url else None

    # Объект аудио (URL или ID)
    if file_id:
        media_obj = file_id
    else:
        # ВАЖНО: filename тут помогает Телеграму понять, как назвать файл при скачивании
        media_obj = URLInputFile(track['url'], filename=safe_filename)

    # 2. ПОПЫТКА 1: STEALTH (Напрямую в чат)
    try:
        await bot_instance.edit_message_media(
            inline_message_id=im_id,
            media=InputMediaAudio(
                media=media_obj,
                thumbnail=thumb_obj,
                title=title,        # <-- Заголовок в плеере
                performer=performer,# <-- Артист в плеере
                caption=f"@{ (await bot_instance.get_me()).username }"
            ),
            reply_markup=None
        )
        # Успех! Выходим. В канал ничего не попало
        return
    except Exception as e:
        # ОШИБКА СТЕЛСА. Часто бывает, если Telegram не может скачать обложку
        # Попробуем еще раз БЕЗ обложки, но всё еще СТЕЛС (без канала)
        if not file_id and thumb_obj:
            try:
                await bot_instance.edit_message_media(
                    inline_message_id=im_id,
                    media=InputMediaAudio(
                        media=media_obj, # Тот же URL
                        title=title,
                        performer=performer,
                        caption=f"@{ (await bot_instance.get_me()).username }"
                        # Без thumbnail
                    ),
                    reply_markup=None
                )
                return # Успех без обложки
            except: pass

    # 3. ПОПЫТКА 2: ЗАГРУЗКА В КАНАЛ (PLAN B)
    # Сюда попадаем только если:
    # а) В чате запрещена музыка (TelegramBadRequest)
    # б) Прямая ссылка протухла или недоступна для серверов Telegram
    
    if not file_id and track:
        try:
            # Грузим в канал. Тут метаданные применятся 100%
            dump_msg = await bot_instance.send_audio(
                chat_id=DUMP_CHANNEL_ID,
                audio=track['url'],
                thumbnail=URLInputFile(thumb_url) if thumb_url else None,
                title=title,
                performer=performer,
                caption=f"#{source}|{item_id}"
            )
            file_id = dump_msg.audio.file_id
            msg_id = dump_msg.message_id
            
            # Сохраняем в кэш, раз уж скачали
            asyncio.create_task(save_cached_info(source, item_id, file_id, msg_id))
        except Exception: 
            # Если даже в канал не грузит - всё, финиш
            try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌ Err")
            except: pass
            return 

    # 4. ФИНАЛЬНАЯ ПОДМЕНА (Используя ID из канала)
    # Если мы здесь, значит файл теперь есть в канале
    if file_id:
        try:
            # Пробуем подменить еще раз, теперь с file_id
            await bot_instance.edit_message_media(
                inline_message_id=im_id,
                media=InputMediaAudio(
                    media=file_id,
                    caption=f"@{ (await bot_instance.get_me()).username }",
                    title=title,
                    performer=performer,
                    thumbnail=thumb_obj
                ),
                reply_markup=None
            )
        except TelegramBadRequest:
            # 5. ОБХОД БЛОКИРОВКИ
            # Если даже edit_message_media с file_id не сработал -> Чат запрещает музыку
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

# ТРИГГЕРЫ
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