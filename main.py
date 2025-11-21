import os
import logging
import asyncio
import hashlib
import re
import time
import gc
import math
import difflib
import aiohttp
import ujson
import aiosqlite
import ssl
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineQuery, InlineQueryResultAudio, Message, CallbackQuery
from aiogram.filters import Command

# CFG
TG_TOKEN = os.getenv("TG_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FALLBACK_CLIENT_ID = os.getenv("FALLBACK_CLIENT_ID",
                               "LMlJPYvzQSVyjYv7faMQl9W7OjTBCaq4")

PIPED_API_URL = "https://api.piped.private.coffee"

# Лимиты
MAX_CONCURRENT_REQ = 6
SEARCH_CANDIDATES_SC = 60
SEARCH_CANDIDATES_YT = 40
FINAL_LIMIT = 10
INLINE_LIMIT = 5

CACHE_TTL = 600
DB_NAME = "users.db"

BAD_CHARS_RE = re.compile(
    r'[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f\u0900-\u097f\u0e00-\u0e7f\u4e00-\u9fff\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u1ea0-\u1eff]'
)
NOISE_WORDS = [
    "(official video)", "[official video]", "(official audio)",
    "[official audio]", "(lyrics)", "[lyrics]", "(video)", "[video]",
    "official video", "official audio", "hd", "4k", "hq", "remastered"
]
TRASH_WORDS = {
    'slowed', 'reverb', 'remix', 'cover', 'bassboosted', 'tik tok', 'speed up',
    'nightcore', 'live', 'concert', 'instrumental'
}

USER_SOURCES = {}

logging.basicConfig(level=logging.ERROR)


# WEB SERVER
async def health_check(request):
    return web.Response(text="Alive")


async def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8080).start()
    print("Web server started")


# DATABASEввв
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_active BOOLEAN DEFAULT 1)"
        )
        await db.commit()


async def add_user(user_id: int):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id, ))
            await db.commit()
    except:
        pass


async def get_active_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
                "SELECT user_id FROM users WHERE is_active = 1") as cursor:
            return await cursor.fetchall()


async def mark_inactive(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET is_active = 0 WHERE user_id = ?",
                         (user_id, ))
        await db.commit()


# KEY MANAGER
class KeyManager:

    def __init__(self, session):
        self.session = session
        self.client_id = FALLBACK_CLIENT_ID

    async def fetch_new_key(self):
        gc.collect()
        try:
            async with self.session.get("https://soundcloud.com/discover",
                                        timeout=3) as resp:
                if resp.status != 200: return
                text = await resp.text()
            js_urls = re.findall(r'src="(https://[^"]+/assets/[^"]+\.js)"',
                                 text)
            del text
            if not js_urls: return
            for url in js_urls[-2:]:
                async with self.session.get(url, timeout=3) as js_resp:
                    if js_resp.status != 200: continue
                    match = re.search(r'client_id:"([a-zA-Z0-9]{32})"', await
                                      js_resp.text())
                    if match:
                        self.client_id = match.group(1)
                        print(f"✅ Key updated: {self.client_id}")
                        return
        except:
            pass

    def get_id(self):
        return self.client_id


# LOGIC
def format_plays(count):
    if not count: return ""
    if count >= 1_000_000: return f"{count / 1_000_000:.1f}M"
    if count >= 1_000: return f"{count / 1_000:.0f}K"
    return f"{count}"


def clean_title(text):
    text = text.lower()
    for w in NOISE_WORDS:
        text = text.replace(w, "")
    return text.strip()


def calculate_score(item, query_lower, query_words):
    score = 0
    raw_title = item['title'].lower()
    clean_t = clean_title(raw_title)
    artist_lower = item['artist'].lower()
    full_text = f"{clean_t} {artist_lower}"
    item_words = set(full_text.split())

    common = query_words.intersection(item_words)
    if len(query_words) > 0: coverage = len(common) / len(query_words)
    else: coverage = 0

    if coverage == 1.0: score += 200
    elif coverage >= 0.66: score += 50
    else: score += len(common) * 10

    if query_lower in full_text: score += 60
    if artist_lower in query_lower: score += 40

    plays = item.get('playback_count', 0) or 0
    if plays > 0:
        try:
            score += math.log10(plays) * 15
        except:
            pass

    if item.get('source') == 'SC': score += 15

    dur_sec = item.get('duration', 0) / 1000
    if dur_sec < 45: score -= 30
    elif dur_sec > 900: score -= 20

    if not any(w in query_lower for w in TRASH_WORDS):
        for bad in TRASH_WORDS:
            if bad in raw_title: score -= 30
    return score


# ENGINES
class SoundCloudEngine:
    __slots__ = ('session', 'sem', 'key_manager')

    def __init__(self, session, key_manager):
        self.session = session
        self.sem = asyncio.Semaphore(MAX_CONCURRENT_REQ)
        self.key_manager = key_manager

    async def search_raw(self, query: str):
        client_id = self.key_manager.get_id()
        params = {
            "q": query,
            "client_id": client_id,
            "limit": SEARCH_CANDIDATES_SC,
            "app_version": "1699953100"
        }
        async with self.sem:
            try:
                async with self.session.get(
                        "https://api-v2.soundcloud.com/search/tracks",
                        params=params,
                        timeout=2.5) as resp:
                    if resp.status == 401:
                        asyncio.create_task(self.key_manager.fetch_new_key())
                        return []
                    if resp.status != 200: return []
                    data = await resp.json(loads=ujson.loads)
                    raw = data.get('collection', [])
                    del data
                    candidates = []
                    for item in raw:
                        if not item.get('streamable'): continue
                        title = item.get('title', '')
                        if len(title) > 150 or BAD_CHARS_RE.search(title):
                            continue
                        prog_url = next(
                            (t['url'] for t in item.get('media', {}).get(
                                'transcodings', [])
                             if t['format']['protocol'] == 'progressive'),
                            None)
                        if not prog_url: continue
                        candidates.append({
                            'source':
                            'SC',
                            'id':
                            item['id'],
                            'title':
                            title,
                            'artist':
                            item.get('user', {}).get('username', 'Unknown'),
                            'playback_count':
                            item.get('playback_count', 0),
                            'duration':
                            item.get('duration', 0),
                            'artwork_url':
                            item.get('artwork_url'),
                            'media_url_template':
                            prog_url
                        })
                    del raw
                    return candidates
            except:
                return []

    async def resolve_url_by_id(self, track_id):
        client_id = self.key_manager.get_id()
        try:
            info_url = f"https://api-v2.soundcloud.com/tracks/{track_id}"
            async with self.session.get(info_url,
                                        params={"client_id": client_id},
                                        timeout=3) as resp:
                if resp.status != 200: return None
                data = await resp.json(loads=ujson.loads)
                prog_url = next(
                    (t['url']
                     for t in data.get('media', {}).get('transcodings', [])
                     if t['format']['protocol'] == 'progressive'), None)
                if not prog_url: return None

                async with self.session.get(prog_url,
                                            params={"client_id": client_id},
                                            timeout=3) as r2:
                    if r2.status == 200:
                        return (await r2.json(loads=ujson.loads)).get('url')
        except:
            return None


class YouTubeEngine:
    __slots__ = ('session', 'sem')

    def __init__(self, session):
        self.session = session
        self.sem = asyncio.Semaphore(4)

    async def search_raw(self, query: str):
        params = {"q": query, "filter": "all"}
        async with self.sem:
            try:
                async with self.session.get(f"{PIPED_API_URL}/search",
                                            params=params,
                                            timeout=3) as resp:
                    if resp.status != 200: return []
                    data = await resp.json(loads=ujson.loads)
                    items = data.get('items', [])
                    del data
                    candidates = []
                    for item in items[:SEARCH_CANDIDATES_YT]:
                        try:
                            url_part = item.get('url', '')
                            if "/watch?v=" in url_part:
                                vid_id = url_part.split("v=")[-1].split("&")[0]
                            elif "/shorts/" in url_part:
                                vid_id = url_part.split("/shorts/")[-1]
                            else:
                                continue

                            candidates.append({
                                'source':
                                'YT',
                                'id':
                                vid_id,
                                'title':
                                item.get('title', ''),
                                'artist':
                                item.get('uploaderName') or 'YouTube',
                                'playback_count':
                                item.get('views', 0),
                                'duration':
                                item.get('duration', 0) * 1000 if isinstance(
                                    item.get('duration'), int) else 0,
                                'artwork_url':
                                item.get('thumbnail')
                            })
                        except:
                            continue
                    return candidates
            except:
                return []

    async def resolve_url(self, video_id):
        try:
            async with self.session.get(f"{PIPED_API_URL}/streams/{video_id}",
                                        timeout=5) as resp:
                if resp.status != 200: return None
                data = await resp.json(loads=ujson.loads)
                streams = data.get('audioStreams', [])
                best = next((s for s in streams if s.get('format') == 'M4A'),
                            streams[0] if streams else None)
                return best['url'] if best else None
        except:
            return None


# ORCHESTRATOR
class MultiEngine:

    def __init__(self, session, sc_key_manager):
        self.sc = SoundCloudEngine(session, sc_key_manager)
        self.yt = YouTubeEngine(session)

    async def search(self, query: str, source_mode='all'):
        tasks = []
        if source_mode in ['all', 'sc']:
            tasks.append(asyncio.create_task(self.sc.search_raw(query)))
        if source_mode in ['all', 'yt']:
            tasks.append(asyncio.create_task(self.yt.search_raw(query)))

        if not tasks: return []

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        candidates = []
        for res in raw_results:
            if isinstance(res, list): candidates.extend(res)

        if not candidates: return []

        q_lower = query.lower().strip()
        q_words = set(q_lower.split())

        for c in candidates:
            c['score'] = calculate_score(c, q_lower, q_words)

        candidates.sort(key=lambda x: x['score'], reverse=True)
        gc.collect()
        return candidates


# HANDLERS
async def main():
    await start_web_server()
    global engine

    bot = Bot(token=TG_TOKEN)
    dp = Dispatcher()
    await init_db()

    # SSL True
    connector = aiohttp.TCPConnector(limit=0,
                                     ttl_dns_cache=300,
                                     use_dns_cache=True)
    session = aiohttp.ClientSession(connector=connector,
                                    json_serialize=ujson.dumps)

    key_manager = KeyManager(session)
    await key_manager.fetch_new_key()
    engine = MultiEngine(session, key_manager)

    @dp.message(Command("start"))
    async def start_command(message: Message):
        asyncio.create_task(add_user(message.from_user.id))
        await message.answer("👋 <b>Музыкальный бот</b>\nПиши название трека",
                             parse_mode="HTML")

    @dp.message(Command("send"))
    async def send_ad(message: Message):
        if message.from_user.id != ADMIN_ID: return
        text = message.text[5:].strip()
        if not text:
            await message.answer("Пусто")
            return
        users = await get_active_users()
        count = 0
        for (uid, ) in users:
            try:
                await bot.send_message(uid, text)
                count += 1
                await asyncio.sleep(0.03)
            except Exception as e:
                if "Forbidden" in str(e): await mark_inactive(uid)
        await message.answer(f"Отправлено: {count}")

    @dp.message(Command("source"))
    async def cmd_source(message: Message):
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="🌍 Все", callback_data="src_all"),
            types.InlineKeyboardButton(text="☁️ SC", callback_data="src_sc"),
            types.InlineKeyboardButton(text="▶️ YT", callback_data="src_yt")
        ]])
        current = USER_SOURCES.get(message.from_user.id, 'all').upper()
        await message.answer(f"⚙️ <b>Фильтр:</b> {current}",
                             reply_markup=kb,
                             parse_mode="HTML")

    @dp.callback_query(lambda c: c.data.startswith("src_"))
    async def set_source(call: CallbackQuery):
        mode = call.data.split("_")[1]
        USER_SOURCES[call.from_user.id] = mode
        text_map = {
            'all': "🌍 ВЕЗДЕ",
            'sc': "☁️ SOUNDCLOUD",
            'yt': "▶️ YOUTUBE"
        }
        await call.message.edit_text(f"✅ Режим: <b>{text_map[mode]}</b>",
                                     parse_mode="HTML")
        await call.answer()

    # ПОИСК В ЧАТЕ
    @dp.message()
    async def search_handler(message: Message):
        if not message.text or message.text.startswith("/"): return
        query = message.text.strip()
        if len(query) < 2: return

        asyncio.create_task(add_user(message.from_user.id))
        mode = USER_SOURCES.get(message.from_user.id, 'all')

        await bot.send_chat_action(message.chat.id, "typing")

        all_candidates = await engine.search(query, mode)
        results = all_candidates[:FINAL_LIMIT]

        if not results:
            await message.answer("❌ Ничего не найдено.")
            return

        res_text = f"🔎 <b>Результаты:</b> {query}\n\n"
        buttons_row_1 = []
        buttons_row_2 = []

        for i, item in enumerate(results):
            num = i + 1
            icon = "☁️" if item['source'] == 'SC' else "▶️"
            clean_t = item['title'].replace(item['artist'], "").strip(" -|")
            if not clean_t: clean_t = item['title']

            res_text += f"<b>{num}.</b> {icon} {item['artist']} — {clean_t[:35]}\n"

            btn = types.InlineKeyboardButton(
                text=f"{num}",
                callback_data=f"dl|{item['source']}|{item['id']}")
            if i < 5: buttons_row_1.append(btn)
            else: buttons_row_2.append(btn)

        kb_rows = []
        if buttons_row_1: kb_rows.append(buttons_row_1)
        if buttons_row_2: kb_rows.append(buttons_row_2)

        await message.answer(
            res_text,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb_rows),
            parse_mode="HTML")

    @dp.callback_query(lambda c: c.data.startswith("dl|"))
    async def download_callback(call: CallbackQuery):
        _, source, item_id = call.data.split("|")
        await call.answer("🚀 Скачиваю...")
        try:
            url = None
            if source == 'SC': url = await engine.sc.resolve_url_by_id(item_id)
            else: url = await engine.yt.resolve_url(item_id)

            if not url:
                await call.message.answer("❌ Ссылка истекла.")
                return

            await bot.send_audio(
                chat_id=call.from_user.id,
                audio=url,
                caption=f"via @{(await bot.get_me()).username}")
        except:
            await call.message.answer("❌ Ошибка отправки.")

    # INLINE (FIXED ICONS)
    @dp.inline_query()
    async def inline_handler(query: InlineQuery):
        text = query.query.strip()
        if len(text) < 2: return

        all_results = await engine.search(
            text, USER_SOURCES.get(query.from_user.id, 'all'))
        top_results = all_results[:INLINE_LIMIT]

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
            res_id = hashlib.md5(
                f"{item['source']}_{item['id']}".encode()).hexdigest()

            # ДОБАВЛЕНЫ ИКОНКИ В INLINE
            icon = "☁️" if item['source'] == 'SC' else "▶️"

            iq_results.append(
                InlineQueryResultAudio(
                    id=res_id,
                    audio_url=real_url,
                    title=item['title'],
                    performer=
                    f"{icon} {item['artist']} | {format_plays(item['playback_count'])}",
                    audio_duration=int(item['duration'] / 1000),
                    thumbnail_url=item['artwork_url']))

        try:
            await query.answer(iq_results, cache_time=300, is_personal=True)
        except:
            pass

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("🚀 Bot Started (Final Fixed Version)")
        await dp.start_polling(bot)
    finally:
        await session.close()
        await bot.session.close()


if __name__ == "__main__":
    import sys
    
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        import uvloop
        uvloop.install()
        print("✅ uvloop installed & running!")
    except ImportError:
        print("⚠️ uvloop not found, using default asyncio loop")
        pass

    asyncio.run(main())
