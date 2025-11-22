import asyncio
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

# ОТОБРАЖЕНИЕ (ЧИСТЫЙ ТЕКСТ ЕСЛИ НЕТ ОБЛОЖКИ)
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
        
        # Если картинки нет - будет None. Телеграм покажет просто текст
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

    await query.answer(iq_results, cache_time=600, is_personal=True)

# ЗАГРУЗКА (БЕЗ ИЗМЕНЕНИЙ, НО ЧИЩЕ)
async def fast_swap(im_id, source, item_id):
    # 1. КЭШ
    cached = await get_cached_info(source, item_id)
    file_id = cached.get('file_id') if cached else None
    msg_id = cached.get('message_id') if cached else None

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

    title = track['title'][:100] if track else "Track"
    performer = track['artist'][:64] if track else "Artist"
    filename = f"{performer} - {title}.mp3".replace('"', '').replace('/', '')
    thumb_url = track.get('thumbnail') if track else None

    # 2. ПОПЫТКА (БЕЗ СОХРАНЕНИЯ В КАНАЛ)
    try:
        media_obj = file_id if file_id else URLInputFile(track['url'], filename=filename)
        
        # Если thumb_url нет (None), Телеграм сам поставит иконку ноты
        thumb_obj = URLInputFile(thumb_url) if thumb_url else None

        await bot_instance.edit_message_media(
            inline_message_id=im_id,
            media=InputMediaAudio(
                media=media_obj,
                caption=f"@{ (await bot_instance.get_me()).username }",
                title=title,
                performer=performer,
                thumbnail=thumb_obj
            ),
            reply_markup=None
        )
    except TelegramBadRequest:
        # 3. ЗАГРУЗКА В КАНАЛ (ЕСЛИ ЗАПРЕТ)
        if not file_id and track:
            try:
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
                asyncio.create_task(save_cached_info(source, item_id, file_id, msg_id))
            except Exception: return 

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