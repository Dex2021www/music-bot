import os
import re

# ================= TOKENS =================
TG_TOKEN = os.getenv("TG_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FALLBACK_CLIENT_ID = os.getenv("FALLBACK_CLIENT_ID", "LMlJPYvzQSVyjYv7faMQl9W7OjTBCaq4")
DATABASE_URL = os.getenv("DATABASE_URL")

# ================= API URLS =================
# Список зеркал. Бот будет перебирать их, если одно упадет
PIPED_MIRRORS = [
    "https://api.piped.private.coffee",  # Быстрое
    "https://api.piped.bot",             # Основное
    "https://pipedapi.kavin.rocks",      # Классика
    "https://pipedapi.drgns.space",      # Запасное
    "https://pipedapi.system41.com"      # Запасное
]

# ================= LIMITS =================
MAX_CONCURRENT_REQ = 6
SEARCH_CANDIDATES_SC = 60
SEARCH_CANDIDATES_YT = 40
FINAL_LIMIT = 10
INLINE_LIMIT = 10
CACHE_TTL = 600
DB_NAME = "users.db"

# ================= REGEX (РЕГУЛЯРКИ) =================

# 1. Фильтр иероглифов (Для engines.py)
BAD_CHARS_RE = re.compile(r'[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f\u0900-\u097f\u0e00-\u0e7f\u4e00-\u9fff\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u1ea0-\u1eff]')

# 2. Очистка скобок (Для utils.py)
BRACKETS_RE = re.compile(r'\s*[\(\[].*?[\)\]]') 

# 3. Только буквы и цифры (Для utils.py)
ALPHANUM_RE = re.compile(r'\W+') 

# ================= LISTS (СПИСКИ СЛОВ) =================

# БАН-ЛИСТ: Если это есть в названии, трек скрывается
BANNED_WORDS = {
    'reaction', 'review', 'tutorial', 'lesson', 'урок', 'разбор', 
    'cover by', 'кавер', 'parody', 'пародия', 'реакция', 'instrumental', 
    'karaoke', 'караоке', 'minus', 'минус', 'speed up', 'slowed', 'reverb'
}

# ШУМ: Эти слова вырезаются перед сравнением
NOISE_WORDS = {
    'official video', 'official audio', 'lyrics', 'video', 'audio', 
    'hq', 'hd', '4k', 'music', 'mv', 'clip', 'клип', 'премьера', 
    'premiere', 'single', 'album', 'full', 'live performance', 'live'
}

# СТОП-СЛОВА: Удаляются из запроса пользователя
SEARCH_STOP_WORDS = {
    'скачать', 'download', 'mp3', 'music', 'музыка', 'песня', 'song', 
    'track', 'трек', 'слушать', 'listen', 'free', 'бесплатно', 'audio', 'аудио'
}