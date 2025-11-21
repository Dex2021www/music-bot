import asyncpg
import logging
from config import DATABASE_URL

pool = None

async def init_db():
    """Подключение к PostgreSQL и создание таблицы"""
    global pool
    try:
        # Создаем пул соединений (это эффективно для высоких нагрузок)
        pool = await asyncpg.create_pool(dsn=DATABASE_URL)
        
        async with pool.acquire() as connection:
            # BIGINT, т.к. Telegram ID большие цифры
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    is_active BOOLEAN DEFAULT TRUE
                );
            """)
            print("✅ Database connected & checked")
    except Exception as e:
        print(f"❌ DB Error: {e}")

async def add_user(user_id: int):
    """Добавляем юзера (если есть - игнорируем)"""
    if not pool: return
    try:
        async with pool.acquire() as connection:
            # Синтаксис Postgres: $1 вместо ?
            # ON CONFLICT DO NOTHING = INSERT OR IGNORE
            await connection.execute(
                "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", 
                user_id
            )
    except Exception as e:
        print(f"DB Add Error: {e}")

async def get_active_users():
    """Получаем список всех активных"""
    if not pool: return []
    try:
        async with pool.acquire() as connection:
            rows = await connection.fetch("SELECT user_id FROM users WHERE is_active = TRUE")
            # Возвращаем список кортежей, чтобы не ломать логику main.py
            return [(row['user_id'],) for row in rows]
    except Exception as e:
        print(f"DB Get Error: {e}")
        return []

async def mark_inactive(user_id: int):
    """Помечаем заблокировавшего"""
    if not pool: return
    try:
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE users SET is_active = FALSE WHERE user_id = $1", 
                user_id
            )
    except: pass