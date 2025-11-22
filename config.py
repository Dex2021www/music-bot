import os
import re

# TOKENS & ADMIN
TG_TOKEN = os.getenv("TG_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# DUMP CHANNEL
DUMP_CHANNEL_ID = int(os.getenv("DUMP_CHANNEL_ID", "-100...")) 
DUMP_CHANNEL_USERNAME = os.getenv("DUMP_CHANNEL_USERNAME", "") 

# DATABASE
DATABASE_URL = os.getenv("DATABASE_URL")

# CLIENTS
FALLBACK_CLIENT_ID = os.getenv("FALLBACK_CLIENT_ID", "LMlJPYvzQSVyjYv7faMQl9W7OjTBCaq4")

# LIMITS
MAX_CONCURRENT_REQ = 4
SEARCH_CANDIDATES_SC = 10
SEARCH_CANDIDATES_YT = 10
FINAL_LIMIT = 10
INLINE_LIMIT = 10
CACHE_TTL = 300

# URLS
PIPED_MIRRORS = [
    "https://pipedapi.kavin.rocks",
    "https://api.piped.private.coffee",
    "https://pa.il.ax",
    "https://pipedapi.drgns.space"
]

# FILTERS
BAD_CHARS_RE = re.compile(r'[\u0590-\u05ff\u0600-\u06ff\u4e00-\u9fff]')

SEARCH_STOP_WORDS = frozenset({
    'скачать', 'download', 'mp3', 'music', 'музыка', 'песня', 'song', 
    'track', 'трек', 'слушать', 'listen', 'free', 'бесплатно'
})

BANNED_WORDS = frozenset({
    'reaction', 'review', 'tutorial', 'lesson', 'урок', 'разбор', 
    'cover by', 'кавер', 'parody', 'пародия', 'реакция', 
    'speed up', 'slowed', 'reverb'
})