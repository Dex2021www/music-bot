import math
import difflib
from config import BANNED_WORDS, NOISE_WORDS, SEARCH_STOP_WORDS, BRACKETS_RE, ALPHANUM_RE

def format_plays(count):
    if not count: return ""
    if count >= 1_000_000: return f"{count / 1_000_000:.1f}M"
    if count >= 1_000: return f"{count / 1_000:.0f}K"
    return f"{count}"

def normalize_text(text):
    """Превращает 'Morgenshtern - Cadillac (Official Video)' в 'morgenshtern cadillac'"""
    if not text: return ""
    text = text.lower()
    
    # 1. Вырезаем всё в скобках (обычно там мусор типа feat. или Official)
    # Но делаем это аккуратно: иногда в скобках важная часть названия
    # Для простоты пока просто чистим известные мусорные фразы
    for word in NOISE_WORDS:
        text = text.replace(word, "")
    
    # 2. Убираем спецсимволы
    text = ALPHANUM_RE.sub(' ', text)
    return text.strip()

def clean_user_query(text):
    """Чистит запрос пользователя"""
    words = text.lower().split()
    clean = [w for w in words if w not in SEARCH_STOP_WORDS]
    return " ".join(clean) if clean else text.lower()

def is_banned(title, query):
    """Проверяет, нет ли в названии запрещенных слов (реакция, кавер)"""
    title_lower = title.lower()
    query_lower = query.lower()
    
    for bad_word in BANNED_WORDS:
        # Баним, ТОЛЬКО если юзер сам не написал это слово
        if bad_word in title_lower and bad_word not in query_lower:
            return True
    return False

def calculate_score(item, query_raw):
    """
    ALGORITHM V5 'CERBERUS'
    Приоритет: Точность > Артист > Популярность
    """
    
    # 1. ПРЕДВАРИТЕЛЬНАЯ ПРОВЕРКА
    # Если трек — это "Реакция на клип", а мы искали песню — удаляем нафиг (score = -1000)
    if is_banned(item['title'], query_raw):
        return -1000

    # Подготовка данных
    query_clean = clean_user_query(query_raw)
    
    # Формируем "чистую строку" трека: Артист + Название
    raw_full = f"{item['artist']} {item['title']}"
    track_clean = normalize_text(raw_full)
    
    # 2. ТЕКСТОВОЕ СРАВНЕНИЕ (Fuzzy Logic)
    # Используем ratio(), он вернет число от 0.0 до 1.0
    matcher = difflib.SequenceMatcher(None, query_clean, track_clean)
    similarity = matcher.ratio() # Общая похожесть
    
    # Поиск подстроки (если запрос короткий, а название длинное)
    # Например: q="Numb", track="Linkin Park - Numb" -> ratio будет низким, но вхождение 100%
    is_substring = query_clean in track_clean
    
    # --- СИСТЕМА ОЧКОВ ---
    score = 0
    
    # База: Очки за похожесть (0...100)
    score += similarity * 100
    
    # Бонус за полное вхождение фразы
    if is_substring:
        score += 50

    # 3. ГЛАВНЫЙ ФИЛЬТР (THRESHOLD)
    # Если сходство меньше 30% и это не подстрока — это мусор.
    if similarity < 0.3 and not is_substring:
        return -500 # Скрываем трек

    # 4. АРТИСТ (Artist Bias)
    # Если первое слово запроса совпадает с Артистом — это супер важно
    # q="Morgenshtern", artist="Morgenshtern"
    query_first_word = query_clean.split()[0] if query_clean else ""
    artist_clean = normalize_text(item['artist'])
    
    if query_first_word and query_first_word in artist_clean:
        score += 40

    # 5. ПОПУЛЯРНОСТЬ (Soft Logarithm)
    # Мы используем логарифм, чтобы сгладить разницу.
    # 1000 прослушиваний = 9 очков
    # 1 млн прослушиваний = 18 очков
    # 100 млн прослушиваний = 24 очка
    # Максимальный буст от популярности ограничен 30 очками.
    # Это не даст популярному "индусу" перебить точное совпадение (которое дает 100+ очков).
    plays = item.get('playback_count', 0) or 0
    if plays > 0:
        pop_score = math.log10(plays) * 3 
        score += min(pop_score, 30) # Кап (потолок) в 30 очков

    # 6. БОНУС ИСТОЧНИКА (SC надежнее для аудио)
    if item.get('source') == 'SC':
        score += 10

    # 7. ШТРАФ ЗА ДЛИТЕЛЬНОСТЬ
    dur = item.get('duration', 0) / 1000
    if dur < 40: score -= 50    # Рингтоны
    elif dur > 600: score -= 20 # Сеты

    return score