import asyncpg
from config import DATABASE_URL

pool = None

async def init_db():
    global pool
    try:
        pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=0, 
            max_size=6,
            max_inactive_connection_lifetime=300,
            command_timeout=10
        )
        
        async with pool.acquire() as connection:
            # Основная таблица юзеров
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    is_active BOOLEAN DEFAULT TRUE
                );
                CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active);
            """)

            # Таблица кэша
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS file_cache (
                    uniq_id TEXT PRIMARY KEY, 
                    file_id TEXT NOT NULL
                );
            """)
            
            # МИГРАЦИЯ: Добавляем колонку message_id, если её нет (чтобы старая база не сломалась)
            try:
                await connection.execute("ALTER TABLE file_cache ADD COLUMN IF NOT EXISTS message_id BIGINT;")
            except Exception as e:
                print(f"⚠️ Migration notice: {e}")

            print("✅ Database connected & Updated")
    except Exception as e:
        print(f"❌ DB Error: {e}")

# ОБНОВЛЕННЫЕ ФУНКЦИИ КЭША

async def get_cached_info(source: str, item_id: str):
    """Возвращает (file_id, message_id)"""
    if not pool: return None
    uniq_id = f"{source}_{item_id}"
    try:
        async with pool.acquire() as conn:
            # Возвращаем всю строку
            row = await conn.fetchrow("SELECT file_id, message_id FROM file_cache WHERE uniq_id = $1", uniq_id)
            if row:
                return dict(row) # {'file_id': '...', 'message_id': 123}
            return None
    except: return None

async def save_cached_info(source: str, item_id: str, file_id: str, message_id: int):
    """Сохраняем file_id и message_id"""
    if not pool: return
    uniq_id = f"{source}_{item_id}"
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO file_cache (uniq_id, file_id, message_id) 
                VALUES ($1, $2, $3) 
                ON CONFLICT (uniq_id) DO UPDATE 
                SET file_id = EXCLUDED.file_id, message_id = EXCLUDED.message_id
                """,
                uniq_id, file_id, message_id
            )
    except: pass

# ЮЗЕР ФУНКЦИИ

async def add_user(user_id: int):
    if not pool: return
    try:
        async with pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", 
                user_id
            )
    except Exception: pass

async def get_users_count():
    if not pool: return 0
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_active = TRUE")
    except: return 0

async def get_active_users_cursor():
    if not pool: return None
    return pool.acquire()

async def mark_inactive(user_id: int):
    if not pool: return
    try:
        async with pool.acquire() as connection:
            await connection.execute("UPDATE users SET is_active = FALSE WHERE user_id = $1", user_id)
    except: pass