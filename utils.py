import math
from config import BANNED_WORDS, SEARCH_STOP_WORDS

def format_plays(count):
    """Красивое число (1.5M, 300K)"""
    if not count: return ""
    if count >= 1_000_000: return f"{count / 1_000_000:.1f}M"
    if count >= 1_000: return f"{count / 1_000:.0f}K"
    return f"{count}"

def clean_query(text):
    """Удаляет мусор типа 'скачать', 'mp3' из запроса"""
    text = text.lower()
    words = text.split()
    # Оставляем только слова, которых НЕТ в стоп-листе
    clean_words = [w for w in words if w not in SEARCH_STOP_WORDS]
    
    # Если после чистки ничего не осталось (юзер написал просто "скачать"), возвращаем оригинал
    if not clean_words: return text
    return " ".join(clean_words)

def calculate_score(item, query_raw):
    """
    LEGACY V3 LOGIC:
    1. Точное совпадение слов = Огромный бонус.
    2. Популярность = Огромный бонус.
    3. Никаких скрытых фильтров.
    """
    score = 0
    
    # Подготовка данных
    query_clean = clean_query(query_raw)
    query_words = set(query_clean.split())
    
    # Полный текст трека (Название + Артист)
    title_lower = item['title'].lower()
    artist_lower = item['artist'].lower()
    full_text = f"{artist_lower} {title_lower}"
    
    # Разбиваем трек на слова (убираем запятые и скобки для простоты)
    import re
    # Оставляем только буквы и цифры для разбиения на слова
    item_words = set(re.findall(r'\w+', full_text))

    # --- 1. СОВПАДЕНИЕ СЛОВ (WORD COVERAGE)
    # Это самое важное. Если я ищу "Faceless Ask Eternity", я хочу трек, где есть эти 3 слова
    
    if not query_words:
        return 0

    common_words = query_words.intersection(item_words)
    coverage = len(common_words) / len(query_words)

    if coverage == 1.0:      
        score += 300  # КОРОЛЕВСКИЙ БОНУС (Все слова найдены)
    elif coverage >= 0.66:   
        score += 100  # Найдено 2 из 3 слов
    elif coverage > 0:
        score += 50   # Найдено хоть что-то
    else:
        return -100   # Вообще нет совпадений слов -> в конец списка

    # --- 2. ТОЧНАЯ ФРАЗА
    # Бонус, если слова идут именно в том порядке, как в запросе
    if query_clean in full_text:
        score += 100

    # --- 3. ПОПУЛЯРНОСТЬ (HEAVY WEIGHT)
    # Чтобы Моргенштерн (100М) побеждал каверы (1К), даже если слова совпали у обоих
    plays = item.get('playback_count', 0) or 0
    if plays > 0:
        try:
            # Log10(100M) = 8.   8 * 20 = 160 очков
            # Log10(1000) = 3.   3 * 20 = 60 очков
            # Разница в 100 очков. Это мощно, но не перебьет "Полное совпадение слов" (300 очков)
            score += math.log10(plays) * 20
        except: pass

    # --- 4. БОНУС ЗА SC
    if item.get('source') == 'SC':
        score += 10

    # --- 5. ШТРАФЫ
    dur = item.get('duration', 0) / 1000
    if dur < 40: score -= 50    # Рингтоны
    elif dur > 900: score -= 30 # Сеты

    # Штраф за ремиксы/каверы, ТОЛЬКО если юзер сам не написал "remix"
    is_clean_search = not any(w in query_clean for w in BANNED_WORDS)
    
    if is_clean_search:
        for bad in BANNED_WORDS:
            if bad in title_lower:
                score -= 50 # Сильный штраф за мусор

    return score