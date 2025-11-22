import asyncio
import aiohttp
import ujson
import logging
import gc
import random
from config import (
    MAX_CONCURRENT_REQ, SEARCH_CANDIDATES_SC, SEARCH_CANDIDATES_YT,
    PIPED_MIRRORS, FALLBACK_CLIENT_ID, BAD_CHARS_RE
)
from utils import calculate_score

logger = logging.getLogger(__name__)

class KeyManager:
    def __init__(self, session):
        self.session = session
        self.client_id = FALLBACK_CLIENT_ID

    async def fetch_new_key(self):
        gc.collect()
        try:
            async with self.session.get("https://soundcloud.com/discover", timeout=3) as resp:
                if resp.status != 200: return
                text = await resp.text()
            import re
            js_urls = re.findall(r'src="(https://[^"]+/assets/[^"]+\.js)"', text)
            del text
            if not js_urls: return
            for url in js_urls[-2:]:
                async with self.session.get(url, timeout=3) as js_resp:
                    if js_resp.status != 200: continue
                    match = re.search(r'client_id:"([a-zA-Z0-9]{32})"', await js_resp.text())
                    if match:
                        self.client_id = match.group(1)
                        return
        except: pass
    
    def get_id(self): return self.client_id

class SoundCloudEngine:
    __slots__ = ('session', 'sem', 'key_manager')
    def __init__(self, session, key_manager):
        self.session = session
        self.sem = asyncio.Semaphore(MAX_CONCURRENT_REQ)
        self.key_manager = key_manager

    async def search_raw(self, query: str):
        client_id = self.key_manager.get_id()
        params = {"q": query, "client_id": client_id, "limit": SEARCH_CANDIDATES_SC, "app_version": "1700000000"}
        
        async with self.sem:
            try:
                async with self.session.get("https://api-v2.soundcloud.com/search/tracks", params=params, timeout=3) as resp:
                    if resp.status == 401:
                        asyncio.create_task(self.key_manager.fetch_new_key())
                        return []
                    if resp.status != 200: return []
                    
                    data = await resp.json(loads=ujson.loads)
                    collection = data.get('collection', [])
                    del data
                    
                    candidates = []
                    for item in collection:
                        if not item.get('streamable'): continue
                        
                        # Оптимизация обложки (берем t500x500 для качества)
                        artwork = item.get('artwork_url') or item.get('user', {}).get('avatar_url')
                        if artwork: artwork = artwork.replace('large', 't500x500')

                        prog_url = next((t['url'] for t in item.get('media', {}).get('transcodings', []) 
                                       if t['format']['protocol'] == 'progressive'), None)
                        if not prog_url: continue

                        candidates.append({
                            'source': 'SC',
                            'id': item['id'],
                            'title': item.get('title', '')[:100],
                            'artist': item.get('user', {}).get('username', 'Unknown')[:50],
                            'playback_count': item.get('playback_count', 0),
                            'duration': item.get('duration', 0),
                            'artwork_url': artwork,
                            'media_url_template': prog_url 
                        })
                    return candidates
            except: return []

    async def resolve_url_by_id(self, track_id):
        client_id = self.key_manager.get_id()
        try:
            info_url = f"https://api-v2.soundcloud.com/tracks/{track_id}"
            async with self.session.get(info_url, params={"client_id": client_id}, timeout=4) as resp:
                if resp.status != 200: return None
                data = await resp.json(loads=ujson.loads)
                
                prog_url = next((t['url'] for t in data.get('media', {}).get('transcodings', []) 
                               if t['format']['protocol'] == 'progressive'), None)
                if not prog_url: return None
                
                final_url = await self.resolve_url(prog_url)
                if not final_url: return None

                artwork = data.get('artwork_url') or data.get('user', {}).get('avatar_url')
                if artwork: artwork = artwork.replace('large', 't500x500')

                return {
                    'url': final_url,
                    'title': data.get('title', 'Track'),
                    'artist': data.get('user', {}).get('username', 'SoundCloud'),
                    'thumbnail': artwork
                }
        except: return None

    async def resolve_url(self, url: str):
        try:
            async with self.session.get(url, params={"client_id": self.key_manager.get_id()}, timeout=4) as resp:
                if resp.status == 200: return (await resp.json(loads=ujson.loads)).get('url')
        except: return None

# (импорты те же, добавь FIRST_COMPLETED)
from asyncio import FIRST_COMPLETED

class YouTubeEngine:
    __slots__ = ('session', 'sem')
    def __init__(self, session):
        self.session = session
        self.sem = asyncio.Semaphore(5) # Чуть подняли лимит

    async def _fetch(self, url, params):
        """Одиночный запрос к зеркалу"""
        try:
            async with self.session.get(url, params=params, timeout=2.5) as resp:
                if resp.status == 200:
                    return await resp.json(loads=ujson.loads)
        except: pass
        return None

    async def _race_request(self, endpoint, params=None):
        """ГОНКА: Запускаем 3 запроса, берем первый успешный"""
        # Берем 3 случайных зеркала, чтобы не ддосить одно и то же
        mirrors = random.sample(PIPED_MIRRORS, min(len(PIPED_MIRRORS), 3))
        
        tasks = []
        for base in mirrors:
            url = f"{base}{endpoint}"
            tasks.append(asyncio.create_task(self._fetch(url, params)))

        try:
            # Ждем, пока завершится ХОТЯ БЫ ОДИН
            done, pending = await asyncio.wait(tasks, return_when=FIRST_COMPLETED)
            
            # Ищем успешный результат среди завершенных
            for task in done:
                result = task.result()
                if result:
                    # Отменяем остальные, они нам не нужны
                    for p in pending: p.cancel()
                    return result
            
            # Если победителей нет, ждем остальных (редкий кейс)
            if pending:
                done, _ = await asyncio.wait(pending, return_when=FIRST_COMPLETED)
                for task in done:
                    result = task.result()
                    if result: return result
                    
        except: pass
        return None

    async def search_raw(self, query: str):
        async with self.sem:
            # Используем гонку
            data = await self._race_request("/search", {"q": query, "filter": "videos"})
            
            if not data: return []
            items = data.get('items', [])
            candidates = []
            
            # Лимит цикла жесткий, чтобы не тратить CPU
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
            return candidates

    async def resolve_url(self, video_id):
        # Тоже используем гонку для получения ссылки
        data = await self._race_request(f"/streams/{video_id}")
        if not data: return None
        try:
            streams = data.get('audioStreams', [])
            # Берем первый попавшийся M4A (это быстрее сортировки)
            best = next((s for s in streams if s.get('format') == 'M4A'), None)
            if not best and streams: best = streams[0]
            
            if not best: return None

            return {
                'url': best['url'],
                'title': data.get('title', 'Track'),
                'artist': data.get('uploader', 'YouTube'),
                'thumbnail': data.get('thumbnailUrl')
            }
        except: return None

class MultiEngine:
    def __init__(self, session, sc_key_manager):
        self.sc = SoundCloudEngine(session, sc_key_manager)
        self.yt = YouTubeEngine(session)

    async def search(self, query: str, source_mode='all'):
        tasks = []
        if source_mode in ['all', 'sc']: tasks.append(asyncio.create_task(self.sc.search_raw(query)))
        if source_mode in ['all', 'yt']: tasks.append(asyncio.create_task(self.yt.search_raw(query)))
        
        if not tasks: return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Flatten list
        final = []
        for r in results:
            if isinstance(r, list): final.extend(r)
            
        # Оптимизированная сортировка (сначала по совпадению, потом по просмотрам)
        for c in final:
            c['score'] = calculate_score(c, query)
        
        final.sort(key=lambda x: x['score'], reverse=True)
        return final