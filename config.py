import os
import re

# ================= TOKENS & KEYS =================
# Токены берутся из переменных окружения
# Это безопасно для GitHub
TG_TOKEN = os.getenv("TG_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Запасной ключ для SoundCloud (если авто-поиск ключа не сработает)
FALLBACK_CLIENT_ID = os.getenv("FALLBACK_CLIENT_ID", "LMlJPYvzQSVyjYv7faMQl9W7OjTBCaq4")

# ================= API URLS =================
# Зеркало Piped для YouTube.
# Если перестанет искать, меняй на "https://pipedapi.kavin.rocks" или "https://pipedapi.drgns.space"
PIPED_API_URL = "https://api.piped.private.coffee"

# ================= LIMITS & PERFORMANCE =================
# Максимум одновременных тяжелых запросов (чтобы не превысить 512 МБ RAM)
MAX_CONCURRENT_REQ = 6

# Глубина поиска: сколько треков запрашиваем у API для анализа
SEARCH_CANDIDATES_SC = 60
SEARCH_CANDIDATES_YT = 40

# Сколько результатов показывать пользователю
FINAL_LIMIT = 10   # В чате (кнопки)
INLINE_LIMIT = 10  # В инлайн-режиме

# Время жизни кэша в секундах (10 минут)
CACHE_TTL = 600

# Имя файла базы данных
DB_NAME = "users.db"

# ================= FILTERS & REGEX =================

# Регулярка для фильтрации треков с иероглифами и арабской вязью (часто мусор)
BAD_CHARS_RE = re.compile(r'[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f\u0900-\u097f\u0e00-\u0e7f\u4e00-\u9fff\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u1ea0-\u1eff]')

# "Шум" в названиях треков.
# Эти слова удаляются из названия ПЕРЕД сравнением, чтобы улучшить точность поиска.
NOISE_WORDS = [
    "(official video)", "[official video]", "(official audio)", "[official audio]",
    "(lyrics)", "[lyrics]", "(video)", "[video]", "official video", "official audio",
    "hd", "4k", "hq", "remastered", "music video", "live performance"
]

# "Мусорные" слова.
# Если этих слов нет в запросе юзера, но они есть в названии трека -> понижаем рейтинг.
TRASH_WORDS = {
    'slowed', 'reverb', 'remix', 'cover', 'bassboosted', 'tik tok',
    'speed up', 'nightcore', 'live', 'concert', 'instrumental', 'karaoke', 'edit', 'sped up', 
    'download', 'free', 'free download', 'remastered', 'reaction', 'meme', 'parody', 'amv', 'fanmade',
    'bootleg', 'mashup', 'dj mix', 'dj set', 'compilation', 'medley', 'mash up', 'lyric video', 
    'visualizer', 'audiovisualizer', 'audio visualizer', 'slowed + reverb', 'slowed reverb',
    'bass'
}

# Стоп-слова для поиска.
# Если юзер пишет "скачать моргенштерн", мы удаляем слово "скачать", чтобы искать только артиста.
SEARCH_STOP_WORDS = {
    'скачать', 'download', 'mp3', 'music', 'музыка', 'песня', 'song',
    'track', 'трек', 'слушать', 'listen', 'free', 'бесплатно', 'audio', 'аудио', 'video', 'видео',
    'official', 'офиц', 'клип', 'clip'
}