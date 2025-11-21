import asyncpg
import logging
from config import DATABASE_URL

pool = None

async def init_db():
    """Подключение к Neon PostgreSQL"""
    global pool
    try:
        # ОПТИМИЗАЦИЯ ПОД NEON:
        # min_size=0: Не держать соединения, если нет нагрузки
        # max_size=6: Не открывать слишком много соединений
        # max_inactive_connection_lifetime=300: Закрывать соединение, если оно висит 5 минут
        pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=0, 
            max_size=6,
            max_inactive_connection_lifetime=300,
            command_timeout=10
        )
        
        async with pool.acquire() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    is_active BOOLEAN DEFAULT TRUE
                );
                -- Индекс для ускорения выборки активных юзеров
                CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active);
            """)
            print("✅ Database connected (Neon Optimized)")
    except Exception as e:
        print(f"❌ DB Error: {e}")

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
    """Легкий запрос только для статистики"""
    if not pool: return 0
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_active = TRUE")
    except: return 0

async def get_active_users_cursor():
    """
    Генератор для массовой рассылки.
    Не грузит всех в память, а отдает соединение и курсор.
    """
    if not pool: return None
    # Возвращаем контекстный менеджер соединения, чтобы caller мог использовать cursor
    return pool.acquire()

async def mark_inactive(user_id: int):
    if not pool: return
    try:
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE users SET is_active = FALSE WHERE user_id = $1", 
                user_id
            )
    except: pass