import math
import difflib
from transliterate import translit
from config import TRASH_WORDS, NOISE_WORDS, SEARCH_STOP_WORDS

def format_plays(count):
    if not count: return ""
    if count >= 1_000_000: return f"{count / 1_000_000:.1f}M"
    if count >= 1_000: return f"{count / 1_000:.0f}K"
    return f"{count}"

def get_translit_variants(text):
    """Возвращает список: [Оригинал, Транслит]"""
    variants = {text.lower()} # Используем set, чтобы убрать дубли
    try:
        # Пытаемся превратить кириллицу в латиницу (Кадиллак -> Kadillak)
        tr = translit(text, 'ru', reversed=True)
        variants.add(tr.lower())
    except: pass
    return list(variants)

def clean_query(text):
    words = text.lower().split()
    clean_words = [w for w in words if w not in SEARCH_STOP_WORDS]
    if not clean_words: return text.lower()
    return " ".join(clean_words)

def clean_title(text):
    text = text.lower()
    for w in NOISE_WORDS: text = text.replace(w, "")
    return text.strip()

def calculate_score(item, query_raw):
    """
    Теперь функция принимает query_raw (исходный текст), 
    а внутри сама генерирует варианты транслита для сравнения.
    """
    score = 0
    
    # Данные трека
    raw_title = item['title'].lower()
    clean_t = clean_title(raw_title)
    artist_raw = item['artist'].lower()
    full_text = f"{clean_t} {artist_raw}"
    item_words = set(full_text.split())

    # Данные запроса (Генерируем варианты: 'кадиллак' и 'kadillak')
    query_clean = clean_query(query_raw)
    query_variants = get_translit_variants(query_clean)

    # --- 1. МАКСИМАЛЬНОЕ ПОКРЫТИЕ ---
    # Мы проверяем каждый вариант запроса и берем ЛУЧШИЙ результат совпадения
    max_coverage = 0
    
    for q_var in query_variants:
        q_words = set(q_var.split())
        if not q_words: continue
        
        common = q_words.intersection(item_words)
        cov = len(common) / len(q_words)
        
        # Доп. бонус: если слова похожи (Fuzzy match), но не равны (Kadillak != Cadillac)
        # difflib поможет найти схожесть
        if cov == 0 and len(q_words) == 1 and len(item_words) > 0:
             # Сравниваем одно слово запроса с каждым словом трека
             for w in item_words:
                 if difflib.SequenceMatcher(None, list(q_words)[0], w).ratio() > 0.8:
                     cov = 0.9 # Почти точное совпадение
                     break

        if cov > max_coverage:
            max_coverage = cov

    # Начисляем очки за лучшее совпадение
    if max_coverage >= 1.0: score += 200
    elif max_coverage >= 0.75: score += 80
    elif max_coverage >= 0.5: score += 40
    else: score += 10  # Минимальный балл

    # --- 2. ТОЧНАЯ ФРАЗА ---
    for q_var in query_variants:
        if q_var in full_text:
            score += 60
            break

    # --- 3. АРТИСТ ---
    # Проверяем, есть ли артист в любом из вариантов запроса
    for q_var in query_variants:
        if artist_raw in q_var:
            score += 40
            break

    # --- 4. ПОПУЛЯРНОСТЬ ---
    plays = item.get('playback_count', 0) or 0
    if plays > 0:
        try: score += math.log10(plays) * 15
        except: pass

    # --- 5. ИСТОЧНИК & ШТРАФЫ ---
    if item.get('source') == 'SC': score += 15

    dur_sec = item.get('duration', 0) / 1000
    if dur_sec < 45: score -= 30
    elif dur_sec > 900: score -= 20
    else: score += 5

    if not any(w in query_clean for w in TRASH_WORDS):
        for bad in TRASH_WORDS:
            if bad in raw_title: score -= 30

    return score