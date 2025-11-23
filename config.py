import os
import re

TG_TOKEN = os.getenv("TG_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CACHE_CHANNEL_ID = int(os.getenv("CACHE_CHANNEL_ID", "0")) 
BYPASS_CHANNEL_ID = int(os.getenv("BYPASS_CHANNEL_ID", "0")) 
BYPASS_CHANNEL_USERNAME = os.getenv("BYPASS_CHANNEL_USERNAME", "") 

DUMP_CHANNEL_ID = CACHE_CHANNEL_ID
DUMP_CHANNEL_USERNAME = ""

DATABASE_URL = os.getenv("DATABASE_URL")
FALLBACK_CLIENT_ID = os.getenv("FALLBACK_CLIENT_ID", "iY812d33303z321321")

# --- LIMITS ---
MAX_CONCURRENT_REQ = 4
SEARCH_CANDIDATES_SC = 10
SEARCH_CANDIDATES_YT = 10 
INLINE_LIMIT = 10
CACHE_TTL = 300

PIPED_MIRRORS = [
    "https://api.piped.private.coffee"
]
# --- FILTERS ---
BAD_CHARS_RE = re.compile(r'[\u0590-\u05ff\u0600-\u06ff\u4e00-\u9fff]')
SEARCH_STOP_WORDS = frozenset({
    'скачать', 'download', 'mp3', 'music', 'музыка', 'песня', 'song', 
    'track', 'трек', 'слушать', 'listen', 'free', 'бесплатно'
})
BANNED_WORDS = frozenset({
    'reaction', 'review', 'tutorial', 'lesson', 'урок', 'разбор', 
    'cover by', 'кавер', 'parody', 'пародия', 'реакция', 
    'speed up', 'slowed', 'reverb', 'remix', 'live', 'acoustic', 'karaoke', 'instrumental',
    'guide'
})