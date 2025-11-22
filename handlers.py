import asyncio
import re
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

# --- 1. ПОИСК (Мгновенный список) ---
@router.inline_query()
async def inline_handler(query: InlineQuery):
    text = query.query.strip()
    if len(text) < 2: return

    results = await engine.search(text, 'all')
    if not results: return

    iq_results = []
    for item in results[:INLINE_LIMIT]:
        result_id = f"dl:{item['source']}:{item['id']}"
        
        # Форматируем заголовок
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
                message_text="⏳", # Заглушка
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=".", callback_data=f"f:{item['source']}:{item['id']}")
            ]])
        ))

    await query.answer(iq_results, cache_time=300, is_personal=True)


# --- 2. ОБРАБОТКА (FIX МЕТАДАННЫХ) ---
async def process_track(im_id, source, item_id):
    # А. Ищем в кэше
    cached = await get_cached_info(source, item_id)
    file_id = cached.get('file_id') if cached else None
    msg_id = cached.get('message_id') if cached else None

    # Б. Если нет - загружаем в DUMP (Это единственный способ зашить Имя и Обложку)
    if not file_id:
        try:
            track = None
            if source == 'SC': track = await engine.sc.resolve_url_by_id(item_id)
            else: track = await engine.yt.resolve_url(item_id)
            
            if not track or not track.get('url'):
                try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌")
                except: pass
                return

            # Данные для файла
            title = track['title'][:100]
            performer = track['artist'][:64]
            # Чистим имя файла для Телеграма (внутри), чтобы не сбоил
            safe_filename = re.sub(r'[^a-zA-Z0-9\-\. ]', '', f"{performer} - {title}") + ".mp3"
            thumb = track.get('thumbnail')

            # ВАЖНО: send_audio "припекает" метаданные к файлу
            dump_msg = await bot_instance.send_audio(
                chat_id=DUMP_CHANNEL_ID,
                audio=URLInputFile(track['url'], filename=safe_filename),
                title=title,            # Красивое название
                performer=performer,    # Красивый автор
                thumbnail=URLInputFile(thumb) if thumb else None, # Обложка
                caption=f"#{source}|{item_id}"
            )
            
            file_id = dump_msg.audio.file_id
            msg_id = dump_msg.message_id
            
            # Сохраняем готовый, красивый файл в базу
            asyncio.create_task(save_cached_info(source, item_id, file_id, msg_id))

        except Exception as e:
            print(f"DL Err: {e}")
            try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌ Err")
            except: pass
            return

    # В. ОТДАЕМ ЮЗЕРУ (ПОДМЕНА)
    # Теперь у нас есть file_id, в котором УЖЕ зашиты картинка и название
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
            # Если в чате запрещена музыка -> даем ссылку на канал
            if msg_id:
                link = f"https://t.me/{DUMP_CHANNEL_USERNAME}/{msg_id}" if DUMP_CHANNEL_USERNAME \
                       else f"https://t.me/c/{str(DUMP_CHANNEL_ID).replace('-100', '')}/{msg_id}"
                try:
                    await bot_instance.edit_message_text(
                        inline_message_id=im_id,
                        text=f"<a href='{link}'>&#8203;</a>🚫 <b>Музыка запрещена</b>", 
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
        await process_track(chosen.inline_message_id, p[1], p[2])

@router.callback_query(lambda c: c.data.startswith("f:"))
async def force_dl(call: types.CallbackQuery):
    _, src, iid = call.data.split(":")
    if call.inline_message_id:
        await process_track(call.inline_message_id, src, iid)
    await call.answer()