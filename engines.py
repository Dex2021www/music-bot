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
        params = {"q": query, "client_id": client_id, "limit": SEARCH_CANDIDATES_SC, "app_version": "1699953100"}
        
        async with self.sem:
            try:
                async with self.session.get("https://api-v2.soundcloud.com/search/tracks", params=params, timeout=4) as resp:
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
                        if len(title) > 150 or BAD_CHARS_RE.search(title): continue
                        
                        prog_url = next((t['url'] for t in item.get('media', {}).get('transcodings', []) 
                                       if t['format']['protocol'] == 'progressive'), None)
                        if not prog_url: continue

                        candidates.append({
                            'source': 'SC',
                            'id': item['id'],
                            'title': title,
                            'artist': item.get('user', {}).get('username', 'Unknown'),
                            'playback_count': item.get('playback_count', 0),
                            'duration': item.get('duration', 0),
                            'artwork_url': item.get('artwork_url'),
                            'media_url_template': prog_url 
                        })
                    del raw
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
                return await self.resolve_url(prog_url)
        except: return None

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

    async def _request(self, endpoint, params=None):
        mirrors = PIPED_MIRRORS.copy()
        random.shuffle(mirrors)

        for base_url in mirrors:
            url = f"{base_url}{endpoint}"
            try:
                # Увеличил таймаут до 4 сек, некоторые зеркала медленные
                async with self.session.get(url, params=params, timeout=4) as resp:
                    if resp.status == 200:
                        return await resp.json(loads=ujson.loads)
            except:
                pass 
        return None

    async def search_raw(self, query: str):
        # ИЗМЕНЕНИЕ: filter="videos" вместо "music_songs".
        # "videos" ищет всё, а фильтровать мусор будем сами по названию/длине
        params = {"q": query, "filter": "videos"}
        
        async with self.sem:
            data = await self._request("/search", params)
            
            if not data: return []
            
            items = data.get('items', [])
            candidates = []
            
            for item in items[:SEARCH_CANDIDATES_YT]:
                try:
                    url_part = item.get('url', '')
                    if "watch?v=" in url_part: 
                        vid_id = url_part.split("v=")[-1].split("&")[0]
                    else: continue

                    candidates.append({
                        'source': 'YT',
                        'id': vid_id,
                        'title': item.get('title', ''),
                        'artist': item.get('uploaderName') or 'YouTube',
                        'playback_count': item.get('views', 0),
                        'duration': item.get('duration', 0) * 1000 if isinstance(item.get('duration'), int) else 0,
                        'artwork_url': item.get('thumbnail')
                    })
                except: continue
            return candidates

    async def resolve_url(self, video_id):
        data = await self._request(f"/streams/{video_id}")
        if not data: return None
        try:
            streams = data.get('audioStreams', [])
            best = next((s for s in streams if s.get('format') == 'M4A'), None)
            if not best and streams: best = streams[0]
            return best['url'] if best else None
        except: return None

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
        
        sc_candidates = []
        yt_candidates = []

        # Разделяем результаты по спискам
        for i, res in enumerate(raw_results):
            if isinstance(res, list):
                if source_mode == 'all':
                    # Если запущено оба, 0 - SC, 1 - YT (по порядку добавления в tasks)
                    if i == 0: sc_candidates = res
                    else: yt_candidates = res
                elif source_mode == 'sc': sc_candidates = res
                elif source_mode == 'yt': yt_candidates = res

        # Фильтрация и оценка
        def process_list(candidates):
            processed = []
            for c in candidates:
                score = calculate_score(c, query)
                if score > 0:
                    c['score'] = score
                    processed.append(c)
            processed.sort(key=lambda x: x['score'], reverse=True)
            return processed

        sc_final = process_list(sc_candidates)
        yt_final = process_list(yt_candidates)

        # ЛОГИКА СМЕШИВАНИЯ (Interleaving)
        # Если режим 'all', мы чередуем: 1 SC, 1 YT, 1 SC, 1 YT...
        # Это гарантирует, что YouTube будет виден
        final_results = []
        
        if source_mode == 'all':
            max_len = max(len(sc_final), len(yt_final))
            for i in range(max_len):
                if i < len(sc_final): final_results.append(sc_final[i])
                if i < len(yt_final): final_results.append(yt_final[i])
        else:
            final_results = sc_final + yt_final

        gc.collect()
        return final_results