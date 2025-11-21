import math
from config import BANNED_WORDS, SEARCH_STOP_WORDS

def format_plays(count):
    if not count: return ""
    if count >= 1_000_000: return f"{count / 1_000_000:.1f}M"
    if count >= 1_000: return f"{count / 1_000:.0f}K"
    return f"{count}"

def clean_query(text):
    text = text.lower()
    words = text.split()
    clean_words = [w for w in words if w not in SEARCH_STOP_WORDS]
    if not clean_words: return text
    return " ".join(clean_words)

def calculate_score(item, query_raw):
    score = 0
    query_clean = clean_query(query_raw)
    query_words = set(query_clean.split())
    
    title_lower = item['title'].lower()
    artist_lower = item['artist'].lower()
    full_text = f"{artist_lower} {title_lower}"
    
    import re
    item_words = set(re.findall(r'\w+', full_text))

    # 1. СОВПАДЕНИЕ СЛОВ
    if not query_words: return 0

    common_words = query_words.intersection(item_words)
    coverage = len(common_words) / len(query_words)

    if coverage == 1.0:      
        score += 300 
    elif coverage >= 0.66:   
        score += 100 
    elif coverage > 0:
        score += 50
    else:
        return -100

    # 2. ТОЧНАЯ ФРАЗА
    if query_clean in full_text:
        score += 100

    # 3. ПОПУЛЯРНОСТЬ
    plays = item.get('playback_count', 0) or 0
    if plays > 0:
        try:
            score += math.log10(plays) * 20
        except: pass

    # 4. БОНУС SC УБРАН!
    # Раньше здесь было +10 для SC, теперь условия равны.

    # 5. ШТРАФЫ
    dur = item.get('duration', 0) / 1000
    if dur < 40: score -= 50
    elif dur > 900: score -= 30

    is_clean_search = not any(w in query_clean for w in BANNED_WORDS)
    if is_clean_search:
        for bad in BANNED_WORDS:
            if bad in title_lower:
                score -= 50

    return score