import os
import re

# TOKENS
TG_TOKEN = os.getenv("TG_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FALLBACK_CLIENT_ID = os.getenv("FALLBACK_CLIENT_ID", "LMlJPYvzQSVyjYv7faMQl9W7OjTBCaq4")
DATABASE_URL = os.getenv("DATABASE_URL")

# API URLS 
PIPED_MIRRORS = [
    "https://api.piped.private.coffee"
]

# LIMITS
MAX_CONCURRENT_REQ = 5 # Снижаем нагрузку на сеть
SEARCH_CANDIDATES_SC = 60
SEARCH_CANDIDATES_YT = 30
FINAL_LIMIT = 10
INLINE_LIMIT = 10
CACHE_TTL = 600

# REGEX
# Используем raw string для всех regex
BAD_CHARS_RE = re.compile(r'[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f\u0900-\u097f\u0e00-\u0e7f\u4e00-\u9fff\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u1ea0-\u1eff]')
BRACKETS_RE = re.compile(r'\s*[\(\[].*?[\)\]]') 
ALPHANUM_RE = re.compile(r'\W+') 

# LISTS (Frozenset экономит память и ускоряет поиск)
BANNED_WORDS = frozenset({
    'reaction', 'review', 'tutorial', 'lesson', 'урок', 'разбор', 
    'cover by', 'кавер', 'parody', 'пародия', 'реакция', 'instrumental', 
    'karaoke', 'караоке', 'minus', 'минус', 'speed up', 'slowed', 'reverb'
})

NOISE_WORDS = frozenset({
    'official video', 'official audio', 'lyrics', 'video', 'audio', 
    'hq', 'hd', '4k', 'music', 'mv', 'clip', 'клип', 'премьера', 
    'premiere', 'single', 'album', 'full', 'live performance', 'live'
})

SEARCH_STOP_WORDS = frozenset({
    'скачать', 'download', 'mp3', 'music', 'музыка', 'песня', 'song', 
    'track', 'трек', 'слушать', 'listen', 'free', 'бесплатно', 'audio', 'аудио'
})