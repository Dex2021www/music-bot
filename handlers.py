import asyncio
import re
import logging # <--- ЛОГИ
from aiogram import Router, types
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    InputMediaAudio, ChosenInlineResult, InlineKeyboardMarkup, InlineKeyboardButton,
    URLInputFile
)
from aiogram.exceptions import TelegramBadRequest
from config import INLINE_LIMIT, CACHE_CHANNEL_ID, BYPASS_CHANNEL_ID, BYPASS_CHANNEL_USERNAME
from database import get_cached_info, save_cached_info
from utils import format_plays

router = Router()
engine = None
bot_instance = None 

# Настраиваем логгер
logger = logging.getLogger("HANDLERS")

def setup_handlers(main_engine, main_bot):
    global engine, bot_instance
    engine = main_engine
    bot_instance = main_bot

def clean_filename(text):
    s = re.sub(r'[\\/*?:"<>|]', '', text)
    return s.strip()[:60] + ".mp3"

# --- 1. СПИСОК ---
@router.inline_query()
async def inline_handler(query: InlineQuery):
    text = query.query.strip()
    if len(text) < 2: return
    # Лог только при старте поиска, чтобы не спамить
    # logger.info(f"IQ: {text}") 

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


# --- 2. ГЛАВНАЯ ЛОГИКА ---
async def process_track(im_id, source, item_id):
    logger.info(f"👉 CLICK: {source} {item_id}")
    
    # А. ПРОВЕРКА КЭША
    cached = await get_cached_info(source, item_id)
    file_id = cached.get('file_id') if cached else None
    cache_msg_id = cached.get('message_id') if cached else None

    if file_id:
        logger.info(f"💾 CACHE HIT: {file_id[:10]}...")
    else:
        logger.info("🌍 DOWNLOADING (No cache)...")
        try:
            track = None
            if source == 'SC': track = await engine.sc.resolve_url_by_id(item_id)
            else: track = await engine.yt.resolve_url(item_id)
            
            if not track or not track.get('url'):
                logger.error("❌ FAILED to resolve URL")
                try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌")
                except: pass
                return

            title = track['title'][:100]
            performer = track['artist'][:64]
            thumb_url = track.get('thumbnail')
            safe_name = clean_filename(f"{performer} - {title}")

            logger.info(f"📤 UPLOADING to CACHE CHANNEL ({CACHE_CHANNEL_ID})")
            
            dump_msg = await bot_instance.send_audio(
                chat_id=CACHE_CHANNEL_ID,
                audio=URLInputFile(track['url'], filename=safe_name),
                thumbnail=URLInputFile(thumb_url) if thumb_url else None,
                title=title,
                performer=performer,
                caption=f"#{source}|{item_id}"
            )
            
            file_id = dump_msg.audio.file_id
            cache_msg_id = dump_msg.message_id
            
            asyncio.create_task(save_cached_info(source, item_id, file_id, cache_msg_id))
            logger.info("✅ UPLOAD SUCCESS")

        except Exception as e:
            logger.error(f"❌ UPLOAD ERROR: {e}")
            try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌ Err")
            except: pass
            return

    # В. ПОКАЗ ЮЗЕРУ
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
        except TelegramBadRequest as e:
            if "forbidden" in str(e).lower() or "rights" in str(e).lower():
                logger.warning(f"🚫 RESTRICTED CHAT: Copying to Bypass...")
                bypass_link = None
                if cache_msg_id:
                    try:
                        copy = await bot_instance.copy_message(
                            chat_id=BYPASS_CHANNEL_ID,
                            from_chat_id=CACHE_CHANNEL_ID,
                            message_id=cache_msg_id
                        )
                        bypass_link = f"https://t.me/{BYPASS_CHANNEL_USERNAME}/{copy.message_id}"
                    except Exception as err:
                        logger.error(f"COPY ERROR: {err}")

                if bypass_link:
                    try:
                        await bot_instance.edit_message_text(
                            inline_message_id=im_id,
                            text=f"<a href='{bypass_link}'>&#8203;</a>🚫 <b>Музыка запрещена</b>", 
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                InlineKeyboardButton(text="▶ Слушать", url=bypass_link)
                            ]])
                        )
                    except: pass
            else:
                logger.error(f"⚠️ EDIT MEDIA ERROR: {e}")
        except Exception as e:
             logger.error(f"⚠️ UNKNOWN EDIT ERROR: {e}")

# --- ТРИГГЕРЫ ---
@router.chosen_inline_result()
async def chosen_handler(chosen: ChosenInlineResult):
    if chosen.result_id.startswith("dl:"):
        p = chosen.result_id.split(":")
        await process_track(chosen.inline_message_id, p[1], p[2])

@router.callback_query(lambda c: c.data.startswith("f:"))
async def force_dl(call: types.CallbackQuery):
    try: await call.answer()
    except: pass
    
    _, src, iid = call.data.split(":")
    if call.inline_message_id:
        await process_track(call.inline_message_id, src, iid)