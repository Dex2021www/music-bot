import os
import re

# --- TOKENS ---
TG_TOKEN = os.getenv("TG_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FALLBACK_CLIENT_ID = os.getenv("FALLBACK_CLIENT_ID", "LMlJPYvzQSVyjYv7faMQl9W7OjTBCaq4")

# --- API ---
PIPED_API_URL = "https://api.piped.private.coffee"

# --- LIMITS ---
MAX_CONCURRENT_REQ = 6
SEARCH_CANDIDATES_SC = 60  # Берем много
SEARCH_CANDIDATES_YT = 40  # Чтобы было из чего выбирать
FINAL_LIMIT = 10
INLINE_LIMIT = 10
CACHE_TTL = 600
DB_NAME = "users.db"

# --- CLEANING REGEX ---
# Удаляем всё, что в скобках [], () если там служебная инфа
BRACKETS_RE = re.compile(r'\s*[\(\[].*?[\)\]]') 
# Оставляем только буквы и цифры для сравнения
ALPHANUM_RE = re.compile(r'\W+') 

# --- BLACKLIST (Если это есть в названии - сразу бан) ---
# Это убирает реакции, обзоры, уроки, караоке (если юзер сам не попросил)
BANNED_WORDS = {
    'reaction', 'review', 'tutorial', 'lesson', 'урок', 'разбор', 
    'cover by', 'кавер', 'parody', 'пародия', 'реакция', 'instrumental', 
    'karaoke', 'караоке', 'minus', 'минус', 'speed up', 'slowed', 'reverb'
}

# --- NOISE (Это мы вырезаем перед сравнением) ---
NOISE_WORDS = {
    'official video', 'official audio', 'lyrics', 'video', 'audio', 
    'hq', 'hd', '4k', 'music', 'mv', 'clip', 'клип', 'премьера', 
    'premiere', 'single', 'album', 'full'
}

# --- STOP WORDS (Чистим запрос юзера) ---
SEARCH_STOP_WORDS = {
    'скачать', 'download', 'mp3', 'listen', 'слушать', 'free', 'track', 'песня'
}