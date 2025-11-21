import asyncpg
from config import DATABASE_URL

pool = None

async def init_db():
    global pool
    try:
        pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=5, max_size=20)
        
        async with pool.acquire() as connection:
            # Таблица юзеров
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    is_active BOOLEAN DEFAULT TRUE
                );
            """)
            
            # Таблица кэша музыки (СКОРОСТЬ!)
            # unique_id - это "SC_12345" или "YT_videoid"
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS music_cache (
                    unique_id TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    title TEXT
                );
            """)
            print("✅ Database connected & checked")
    except Exception as e:
        print(f"❌ DB Error: {e}")

async def add_user(user_id: int):
    if not pool: return
    try:
        # Быстрая вставка без создания новой транзакции на уровне Python
        await pool.execute("INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
    except: pass

async def get_active_users():
    if not pool: return []
    try:
        rows = await pool.fetch("SELECT user_id FROM users WHERE is_active = TRUE")
        return [(row['user_id'],) for row in rows]
    except: return []

async def mark_inactive(user_id: int):
    if not pool: return
    try:
        await pool.execute("UPDATE users SET is_active = FALSE WHERE user_id = $1", user_id)
    except: pass

# --- ФУНКЦИИ КЭША ---

async def get_cached_file(unique_id: str):
    """Ищет file_id по ID трека"""
    if not pool: return None
    try:
        return await pool.fetchval("SELECT file_id FROM music_cache WHERE unique_id = $1", unique_id)
    except: return None

async def save_cached_file(unique_id: str, file_id: str, title: str):
    """Сохраняет file_id после отправки"""
    if not pool: return
    try:
        await pool.execute(
            "INSERT INTO music_cache (unique_id, file_id, title) VALUES ($1, $2, $3) ON CONFLICT (unique_id) DO NOTHING",
            unique_id, file_id, title
        )
    except Exception as e:
        print(f"Cache Save Error: {e}")