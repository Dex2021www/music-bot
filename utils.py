import math
import difflib
from config import TRASH_WORDS, NOISE_WORDS, SEARCH_STOP_WORDS

def format_plays(count):
    if not count: return ""
    if count >= 1_000_000: return f"{count / 1_000_000:.1f}M"
    if count >= 1_000: return f"{count / 1_000:.0f}K"
    return f"{count}"

def clean_query(text):
    """Чистит запрос пользователя от слов 'скачать', 'mp3' и т.д."""
    words = text.lower().split()
    # Оставляем только те слова, которых нет в стоп-листе
    clean_words = [w for w in words if w not in SEARCH_STOP_WORDS]
    # Если юзер написал только "скачать mp3", возвращаем оригинал (чтобы не искать пустоту)
    if not clean_words: return text.lower()
    return " ".join(clean_words)

def clean_title(text):
    """Чистит название трека от мусора (Official Video)"""
    text = text.lower()
    for w in NOISE_WORDS: text = text.replace(w, "")
    return text.strip()

def calculate_score(item, query_raw, query_words_set):
    score = 0
    
    # 1. ДАННЫЕ ТРЕКА
    # Разделяем исполнителя и название
    title_raw = item['title'].lower()
    artist_raw = item['artist'].lower()
    
    # Чистим название от (Official Video)
    title_clean = clean_title(title_raw)
    
    # Полный текст для поиска
    full_text = f"{artist_raw} {title_clean}"
    item_words = set(full_text.split())
    
    # 2. ДАННЫЕ ЗАПРОСА
    # Чистим запрос от "скачать mp3"
    query_clean = clean_query(query_raw)
    query_words = set(query_clean.split())

    # --- ЛОГИКА ОЧКОВ ---

    # A. ПОКРЫТИЕ (Насколько запрос совпадает с треком)
    common_words = query_words.intersection(item_words)
    if len(query_words) > 0:
        coverage = len(common_words) / len(query_words)
    else:
        coverage = 0

    if coverage == 1.0: score += 200      # Идеальное совпадение
    elif coverage >= 0.75: score += 80    # Очень хорошее
    elif coverage >= 0.5: score += 40     # Нормальное
    else: score += len(common_words) * 10 # Слабое

    # B. ПРИОРИТЕТ АРТИСТА (Новая фича!)
    # Если слова из запроса есть в ИМЕНИ АРТИСТА — это важнее, чем в названии
    artist_matches = query_words.intersection(set(artist_raw.split()))
    if len(artist_matches) > 0:
        score += 30 * len(artist_matches)

    # C. ТОЧНАЯ ФРАЗА
    # Если юзер ввел "Billie Jean", и в треке эти слова идут подряд
    if query_clean in full_text:
        score += 60

    # D. ПОПУЛЯРНОСТЬ (СМАРТ)
    plays = item.get('playback_count', 0) or 0
    is_viral = False
    
    if plays > 0:
        try:
            log_score = math.log10(plays) * 15
            score += log_score
            
            # Если больше 5 млн прослушиваний — считаем хитом
            if plays > 5_000_000: 
                is_viral = True
                score += 20 # Бонус за "Легендарность"
        except: pass

    # E. ИСТОЧНИК
    if item.get('source') == 'SC': 
        score += 15

    # F. ШТРАФЫ (Умные)
    dur_sec = item.get('duration', 0) / 1000
    
    # Слишком коротко/длинно
    if dur_sec < 45: score -= 40
    elif dur_sec > 900: score -= 30
    else: score += 5

    # Фильтр ремиксов и каверов
    # Штрафуем, ТОЛЬКО если:
    # 1. Юзер САМ не написал слово "remix" в запросе
    # 2. Трек НЕ является вирусным хитом (> 5 млн)
    if not any(w in query_clean for w in TRASH_WORDS):
        for bad in TRASH_WORDS:
            if bad in title_raw:
                if is_viral:
                    # Если это "Roses (Imanbek Remix)" (Хит) — НЕ штрафуем, а даже чуть поощряем
                    score += 10 
                else:
                    # Если это "Cadillac (Vasya Remix)" (Мусор) — караем
                    score -= 40

    return score