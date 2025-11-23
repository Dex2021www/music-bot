import asyncio
import aiohttp
import ujson
import logging
import gc
import random
import re
from config import (
    MAX_CONCURRENT_REQ, SEARCH_CANDIDATES_SC, SEARCH_CANDIDATES_YT,
    PIPED_MIRRORS, FALLBACK_CLIENT_ID, BAD_CHARS_RE
)
from utils import calculate_score

# Настраиваем логгер
logger = logging.getLogger("ENGINE")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://soundcloud.com/"
}

class KeyManager:
    def __init__(self, session):
        self.session = session
        self.client_id = FALLBACK_CLIENT_ID

    async def fetch_new_key(self):
        gc.collect()
        logger.info("🔑 SC: Запускаю обновление ключа...")
        try:
            async with self.session.get("https://soundcloud.com/discover", timeout=4) as resp:
                if resp.status != 200: 
                    logger.error(f"🔑 SC: Ошибка главной страницы (Status {resp.status})")
                    return
                text = await resp.text()
            
            js_urls = re.findall(r'src="(https://[^"]+/assets/[^"]+\.js)"', text)
            if not js_urls: 
                logger.error("🔑 SC: Не нашел JS файлы на странице")
                return
            
            logger.info(f"🔑 SC: Найдено {len(js_urls)} скриптов. Проверяю последние...")
            
            for url in js_urls[-3:]:
                try:
                    async with self.session.get(url, timeout=3) as js_resp:
                        if js_resp.status != 200: continue
                        content = await js_resp.text()
                        match = re.search(r'client_id:"([a-zA-Z0-9]{32})"', content)
                        if match:
                            self.client_id = match.group(1)
                            logger.info(f"🔑 SC: ✅ УСПЕХ! Новый ключ: {self.client_id}")
                            return
                except Exception as e:
                    logger.warning(f"🔑 SC: Ошибка проверки скрипта: {e}")
            logger.error("🔑 SC: Ключ так и не найден в скриптах")
        except Exception as e:
            logger.error(f"🔑 SC Key Error: {e}")
    
    def get_id(self): return self.client_id

class SoundCloudEngine:
    __slots__ = ('session', 'sem', 'key_manager')
    def __init__(self, session, key_manager):
        self.session = session
        self.sem = asyncio.Semaphore(MAX_CONCURRENT_REQ)
        self.key_manager = key_manager

    async def search_raw(self, query: str):
        client_id = self.key_manager.get_id()
        params = {"q": query, "client_id": client_id, "limit": SEARCH_CANDIDATES_SC, "app_version": "1699953100"}
        
        async with self.sem:
            try:
                # logger.info(f"☁️ SC: Search '{query}'")
                async with self.session.get("https://api-v2.soundcloud.com/search/tracks", params=params, timeout=4) as resp:
                    
                    if resp.status == 401:
                        logger.warning("☁️ SC: 401 Unauthorized -> Обновляю ключ")
                        await self.key_manager.fetch_new_key()
                        return [] # Юзер попробует еще раз
                    
                    if resp.status != 200: 
                        logger.error(f"☁️ SC: Ошибка API {resp.status}")
                        return []
                    
                    data = await resp.json(loads=ujson.loads)
                    collection = data.get('collection', [])
                    
                    # logger.info(f"☁️ SC: Найдено {len(collection)} сырых треков")
                    
                    candidates = []
                    for item in collection:
                        if not item.get('streamable'): continue
                        artwork = item.get('artwork_url') or item.get('user', {}).get('avatar_url')
                        if artwork: artwork = artwork.replace('large', 't500x500')
                        
                        prog_url = next((t['url'] for t in item.get('media', {}).get('transcodings', []) 
                                       if t['format']['protocol'] == 'progressive'), None)
                        if not prog_url: continue

                        candidates.append({
                            'source': 'SC',
                            'id': str(item['id']),
                            'title': item.get('title', '')[:100],
                            'artist': item.get('user', {}).get('username', 'Unknown')[:50],
                            'playback_count': item.get('playback_count', 0),
                            'duration': item.get('duration', 0),
                            'artwork_url': artwork,
                            'media_url_template': prog_url 
                        })
                    return candidates
            except Exception as e: 
                logger.error(f"☁️ SC Search Exception: {e}")
                return []

    async def resolve_url_by_id(self, track_id):
        client_id = self.key_manager.get_id()
        try:
            logger.info(f"☁️ SC: Получаю ссылку на трек {track_id}")
            info_url = f"https://api-v2.soundcloud.com/tracks/{track_id}"
            
            async with self.session.get(info_url, params={"client_id": client_id}, timeout=4) as resp:
                if resp.status != 200: 
                    logger.error(f"☁️ SC: Ошибка получения инфо трека {resp.status}")
                    return None
                
                data = await resp.json(loads=ujson.loads)
                
                prog_url = next((t['url'] for t in data.get('media', {}).get('transcodings', []) 
                               if t['format']['protocol'] == 'progressive'), None)
                
                if not prog_url: 
                    logger.warning(f"☁️ SC: Нет progressive ссылки для {track_id}")
                    return None
                
                final_url = await self.resolve_url(prog_url)
                if not final_url: 
                    logger.warning(f"☁️ SC: Не удалось разрешить final url")
                    return None

                artwork = data.get('artwork_url') or data.get('user', {}).get('avatar_url')
                if artwork: artwork = artwork.replace('large', 't500x500')

                logger.info(f"☁️ SC: ✅ Ссылка получена!")
                return {
                    'url': final_url,
                    'title': data.get('title', 'Track'),
                    'artist': data.get('user', {}).get('username', 'SoundCloud'),
                    'thumbnail': artwork
                }
        except Exception as e:
            logger.error(f"☁️ SC Resolve Error: {e}")
            return None

    async def resolve_url(self, url: str):
        try:
            async with self.session.get(url, params={"client_id": self.key_manager.get_id()}, timeout=4) as resp:
                if resp.status == 200: return (await resp.json(loads=ujson.loads)).get('url')
        except: return None

class YouTubeEngine:
    __slots__ = ('session', 'sem')
    def __init__(self, session):
        self.session = session
        self.sem = asyncio.Semaphore(4)

    async def search_raw(self, query: str):
        mirrors = PIPED_MIRRORS.copy()
        random.shuffle(mirrors)
        
        async with self.sem:
            for base in mirrors:
                try:
                    # logger.info(f"▶️ YT Search: Пробую {base}...")
                    async with self.session.get(f"{base}/search", params={"q": query, "filter": "videos"}, timeout=3) as resp:
                        if resp.status == 200:
                            data = await resp.json(loads=ujson.loads)
                            items = data.get('items', [])
                            candidates = []
                            for item in items[:SEARCH_CANDIDATES_YT]:
                                url_part = item.get('url', '')
                                if "watch?v=" not in url_part: continue
                                candidates.append({
                                    'source': 'YT',
                                    'id': url_part.split("v=")[-1].split("&")[0],
                                    'title': item.get('title', '')[:100],
                                    'artist': item.get('uploaderName', 'YouTube')[:50],
                                    'playback_count': item.get('views', 0),
                                    'duration': item.get('duration', 0) * 1000,
                                    'artwork_url': item.get('thumbnail')
                                })
                            if candidates:
                                # logger.info(f"▶️ YT: ✅ Найдено {len(candidates)} на {base}")
                                return candidates
                        else:
                            pass
                            # logger.debug(f"▶️ YT Search: {base} returned {resp.status}")
                except: continue
            
            logger.warning("▶️ YT Search: ❌ Все зеркала молчат!")
            return []

    async def resolve_url(self, video_id):
        mirrors = PIPED_MIRRORS.copy()
        random.shuffle(mirrors)

        logger.info(f"▶️ YT Resolve: Ищу потоки для {video_id}...")

        for base in mirrors:
            try:
                # logger.debug(f"▶️ YT Resolve: Пробую {base}")
                async with self.session.get(f"{base}/streams/{video_id}", timeout=4) as resp:
                    if resp.status != 200: 
                        # logger.debug(f"⚠️ {base} -> Status {resp.status}")
                        continue
                    
                    data = await resp.json(loads=ujson.loads)
                    
                    if not data or 'audioStreams' not in data or not data['audioStreams']:
                        logger.warning(f"⚠️ {base} -> Пустой список audioStreams (Блок)")
                        continue 

                    streams = data['audioStreams']
                    best = next((s for s in streams if s.get('format') == 'M4A'), None)
                    if not best and streams: best = streams[0]
                    
                    if not best: continue

                    logger.info(f"✅ YT: УСПЕХ! Поток найден на {base}")
                    return {
                        'url': best['url'],
                        'title': data.get('title', 'Track'),
                        'artist': data.get('uploader', 'YouTube'),
                        'thumbnail': data.get('thumbnailUrl')
                    }
            except Exception as e: 
                # logger.debug(f"❌ {base} error: {e}")
                continue
        
        logger.error(f"❌ YT: Не удалось найти рабочий поток на {len(mirrors)} зеркалах!")
        return None

class MultiEngine:
    def __init__(self, session, sc_key_manager):
        self.sc = SoundCloudEngine(session, sc_key_manager)
        self.yt = YouTubeEngine(session)

    async def search(self, query: str, source_mode='all'):
        logger.info(f"🔍 SEARCH START: '{query}'")
        tasks = []
        if source_mode in ['all', 'sc']: tasks.append(asyncio.create_task(self.sc.search_raw(query)))
        if source_mode in ['all', 'yt']: tasks.append(asyncio.create_task(self.yt.search_raw(query)))
        
        if not tasks: return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        final = []
        for r in results:
            if isinstance(r, list): final.extend(r)
        
        # Сортировка
        for c in final:
            c['score'] = calculate_score(c, query)
        final.sort(key=lambda x: x['score'], reverse=True)
        
        logger.info(f"🔍 SEARCH END: Найдено {len(final)} кандидатов")
        return final