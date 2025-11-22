import asyncio
import re
from aiogram import Router, types, F
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

# --- ОЧИСТКА ИМЕНИ ФАЙЛА ---
def clean_filename(text):
    # Оставляем только латиницу и цифры для имени файла (внутреннего)
    # Это нужно, чтобы Телеграм не поперхнулся
    s = re.sub(r'[^a-zA-Z0-9\-\. ]', '', text)
    return s.strip() + ".mp3"

# ==========================================
# 1. ПОИСК (ОСТАВЛЯЕМ КАК БЫЛО, ТУТ ВСЁ ОК)
# ==========================================
@router.inline_query()
async def inline_handler(query: InlineQuery):
    text = query.query.strip()
    if len(text) < 2: return

    results = await engine.search(text, 'all')
    if not results: return

    iq_results = []
    for item in results[:INLINE_LIMIT]:
        result_id = f"dl:{item['source']}:{item['id']}"
        
        # Красивый заголовок для списка
        display_title = item['title'].replace(item['artist'], '').strip(' -|:').replace('.mp3', '')
        if not display_title: display_title = item['title']
        
        m, s = divmod(item['duration'] // 1000, 60)
        thumb = item.get('artwork_url') 

        iq_results.append(InlineQueryResultArticle(
            id=result_id,
            title=display_title,
            description=f"{item['artist']}\n{m:02d}:{s:02d} • {format_plays(item['playback_count'])}",
            thumbnail_url=thumb, 
            input_message_content=InputTextMessageContent(
                message_text="⌛", # Просто часы
            ),
            # Кнопка на случай сбоя
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=".", callback_data=f"f:{item['source']}:{item['id']}")
            ]])
        ))

    await query.answer(iq_results, cache_time=300, is_personal=True)


# ==========================================
# 2. ЗАГРУЗКА (ЧЕРЕЗ КАНАЛ РАДИ КАЧЕСТВА)
# ==========================================
async def process_track(im_id, source, item_id):
    # --- ШАГ 1: ПРОВЕРКА КЭША ---
    cached = await get_cached_info(source, item_id)
    file_id = cached.get('file_id') if cached else None
    msg_id = cached.get('message_id') if cached else None

    # Если файла нет в кэше - будем качать
    if not file_id:
        try:
            # 1. Получаем прямые ссылки
            track = None
            if source == 'SC': track = await engine.sc.resolve_url_by_id(item_id)
            else: track = await engine.yt.resolve_url(item_id)
            
            if not track or not track.get('url'):
                try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌ Недоступно")
                except: pass
                return

            # 2. Подготовка метаданных
            # Telegram возьмет эти данные и "вшит" их в MP3
            title = track['title'][:100]
            performer = track['artist'][:64]
            thumb_url = track.get('thumbnail')
            
            # ВАЖНО: send_audio - единственный метод, который ГАРАНТИРУЕТ 
            # применение обложки и названия при загрузке по URL.
            # Мы грузим в DUMP канал.
            dump_msg = await bot_instance.send_audio(
                chat_id=DUMP_CHANNEL_ID,
                audio=URLInputFile(track['url'], filename=clean_filename(f"{performer} - {title}")),
                title=title,
                performer=performer,
                thumbnail=URLInputFile(thumb_url) if thumb_url else None,
                caption=f"#{source}|{item_id}"
            )
            
            # Получаем красивый, готовый File ID
            file_id = dump_msg.audio.file_id
            msg_id = dump_msg.message_id
            
            # Сохраняем его навсегда
            asyncio.create_task(save_cached_info(source, item_id, file_id, msg_id))

        except Exception as e:
            print(f"Upload Error: {e}")
            try: await bot_instance.edit_message_text(inline_message_id=im_id, text="❌ Ошибка загрузки")
            except: pass
            return

    # --- ШАГ 2: ПОДМЕНА СООБЩЕНИЯ ---
    # Теперь у нас есть file_id (или из кэша, или только что созданный).
    # Он содержит правильную обложку и название.
    if file_id:
        try:
            await bot_instance.edit_message_media(
                inline_message_id=im_id,
                media=InputMediaAudio(
                    media=file_id,
                    caption=f"@{ (await bot_instance.get_me()).username }"
                    # Метаданные тут указывать не обязательно, они уже внутри file_id
                ),
                reply_markup=None
            )
        except TelegramBadRequest:
            # Если чат запретил музыку
            if msg_id:
                link = f"https://t.me/{DUMP_CHANNEL_USERNAME}/{msg_id}" if DUMP_CHANNEL_USERNAME \
                       else f"https://t.me/c/{str(DUMP_CHANNEL_ID).replace('-100', '')}/{msg_id}"
                try:
                    await bot_instance.edit_message_text(
                        inline_message_id=im_id,
                        text=f"<a href='{link}'>&#8203;</a>🚫 <b>Музыка запрещена</b>", 
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="▶ Слушать", url=link)
                        ]])
                    )
                except: pass
        except Exception: pass

# --- ТРИГГЕРЫ ---

@router.chosen_inline_result()
async def chosen_handler(chosen: ChosenInlineResult):
    # Срабатывает когда юзер нажал на список
    if chosen.result_id.startswith("dl:"):
        p = chosen.result_id.split(":")
        # Запускаем процесс
        await process_track(chosen.inline_message_id, p[1], p[2])

@router.callback_query(lambda c: c.data.startswith("f:"))
async def force_dl(call: types.CallbackQuery):
    # Ручная кнопка (точка)
    _, src, iid = call.data.split(":")
    if call.inline_message_id:
        await process_track(call.inline_message_id, src, iid)
    await call.answer()