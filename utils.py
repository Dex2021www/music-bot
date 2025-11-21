import math
import difflib
import re
from transliterate import translit
from config import TRASH_WORDS, NOISE_WORDS, SEARCH_STOP_WORDS

def format_plays(count):
    if not count: return ""
    if count >= 1_000_000: return f"{count / 1_000_000:.1f}M"
    if count >= 1_000: return f"{count / 1_000:.0f}K"
    return f"{count}"

def get_translit_variants(text):
    """
    Генерирует варианты: 
    1. Оригинал ("кадиллак")
    2. Транслит ("kadillak")
    """
    variants = {text.lower()}
    try:
        # Пытаемся русский -> латиница
        tr = translit(text, 'ru', reversed=True)
        variants.add(tr.lower())
    except: pass
    return list(variants)

def clean_query(text):
    """Убирает слова типа 'скачать', 'mp3'"""
    words = text.lower().split()
    clean_words = [w for w in words if w not in SEARCH_STOP_WORDS]
    if not clean_words: return text.lower()
    return " ".join(clean_words)

def clean_title(text):
    """Убирает (Official Video) и спецсимволы для чистого сравнения"""
    text = text.lower()
    for w in NOISE_WORDS: 
        text = text.replace(w, "")
    # Оставляем только буквы и цифры для сравнения
    return re.sub(r'[^\w\s]', '', text).strip()

def calculate_score(item, query_raw):
    score = 0
    
    # --- ПОДГОТОВКА ДАННЫХ ---
    raw_title = item['title'].lower()
    clean_t = clean_title(raw_title)
    artist_lower = item['artist'].lower()
    
    # Полный текст трека (чистый)
    full_text = f"{artist_lower} {clean_t}"
    item_words = set(full_text.split())

    # Подготовка запроса
    query_clean = clean_query(query_raw)
    # Получаем варианты (оригинал + транслит)
    query_variants = get_translit_variants(query_clean)

    # --- 1. ТЕКСТОВОЕ СОВПАДЕНИЕ (Coverage) ---
    best_coverage = 0
    
    for q_var in query_variants:
        # Чистим вариант запроса от спецсимволов тоже
        q_clean = re.sub(r'[^\w\s]', '', q_var)
        q_words = set(q_clean.split())
        if not q_words: continue
        
        common = q_words.intersection(item_words)
        cov = len(common) / len(q_words)
        
        if cov > best_coverage:
            best_coverage = cov

    # Начисляем очки (Максимум 200)
    if best_coverage == 1.0: score += 200
    elif best_coverage >= 0.75: score += 100
    elif best_coverage >= 0.5: score += 50
    else: score += 10

    # --- 2. ТОЧНАЯ ФРАЗА (Exact Match) ---
    # Бонус, если слова идут в правильном порядке
    # Даем приоритет ОРИГИНАЛЬНОМУ запросу (+80), транслиту чуть меньше (+50)
    if query_clean in full_text:
        score += 80
    else:
        # Проверяем транслит варианты
        for q_var in query_variants:
            if q_var != query_clean and q_var in full_text:
                score += 50
                break

    # --- 3. АРТИСТ (Artist Bonus) ---
    # Если имя артиста есть в запросе - это очень важно
    for q_var in query_variants:
        if artist_lower in q_var or q_var in artist_lower:
            score += 60
            break

    # --- 4. ПОПУЛЯРНОСТЬ (HEAVY WEIGHT) ---
    # ТЕПЕРЬ ЭТО РЕШАЮЩИЙ ФАКТОР
    plays = item.get('playback_count', 0) or 0
    is_viral = False
    
    if plays > 0:
        try:
            # Log10(100M) = 8.   8 * 30 = 240 очков!
            # Log10(10K) = 4.    4 * 30 = 120 очков.
            # Разница в 120 очков перебьет любые мелкие несовпадения текста.
            score += math.log10(plays) * 30
            
            # БОНУС ЗА ХИТ (> 5 млн)
            if plays > 5_000_000:
                is_viral = True
                score += 50  # Несгораемый бонус "Легенда"
        except: pass

    # --- 5. ИСТОЧНИК ---
    if item.get('source') == 'SC': score += 15

    # --- 6. ШТРАФЫ ---
    dur_sec = item.get('duration', 0) / 1000
    
    # Фильтр длительности
    if dur_sec < 45: score -= 50
    elif dur_sec > 900: score -= 30
    else: score += 10

    # Фильтр ремиксов (не штрафуем Хиты!)
    if not any(w in query_clean for w in TRASH_WORDS):
        for bad in TRASH_WORDS:
            if bad in raw_title:
                if is_viral:
                    score += 10 # Хит-ремикс (Roses Imanbek) - поощряем
                else:
                    score -= 50 # Мусор-ремикс - караем жестко

    return score