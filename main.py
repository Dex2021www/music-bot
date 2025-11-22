import asyncio
import aiohttp
import ujson
import ssl
import sys
import gc
import logging
from aiohttp import web, AsyncResolver
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from config import TG_TOKEN
from database import init_db, pool
from engines import KeyManager, MultiEngine
from handlers import router, setup_handlers

# Отключаем лишний логгинг, оставляем только ошибки
logging.basicConfig(level=logging.ERROR)

async def health_check(request):
    """Простой ответ 200 OK для мониторинга"""
    return web.Response(text="Alive")

async def start_web_server():
    """Запуск мини-сервера для Health Check"""
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    # Порт берется из аргументов (для облаков) или 8080
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    await web.TCPSite(runner, '0.0.0.0', port).start()
    print(f"🌍 Web server running on port {port}")

async def main():
    # Принудительная очистка памяти перед стартом
    gc.collect()
    
    # Запуск БД и Веб-сервера
    await init_db()
    await start_web_server()

    # Инициализация бота с HTML по умолчанию
    bot = Bot(
        token=TG_TOKEN, 
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher()

    # СЕТЕВАЯ ОПТИМИЗАЦИЯ
    
    # 1. SSL без строгой проверки (быстрее Handshake)
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    
    # 2. Проверка наличия aiodns для быстрого резолвинга
    resolver = None
    try:
        import aiodns
        resolver = AsyncResolver()
    except ImportError:
        pass # Будет использоваться стандартный медленный DNS

    # 3. Настройка пула соединений
    connector = aiohttp.TCPConnector(
        limit=100,           # Лимит одновременных соединений
        ttl_dns_cache=300,   # Кэш DNS на 5 минут
        use_dns_cache=True, 
        ssl=ssl_ctx,
        resolver=resolver    # Подключаем aiodns
    )
    
    # 4. Сессия с быстрым JSON парсером
    session = aiohttp.ClientSession(
        connector=connector, 
        json_serialize=ujson.dumps
    )

    # Инициализация движков
    key_manager = KeyManager(session)
    await key_manager.fetch_new_key()
    
    engine = MultiEngine(session, key_manager)

    # Подключение логики
    setup_handlers(engine, bot) 
    dp.include_router(router)

    try:
        # Удаляем вебхук, чтобы поллинг заработал мгновенно
        await bot.delete_webhook(drop_pending_updates=True)
        print("🚀 Bot Started [High Performance Mode]")
        
        # Запуск
        await dp.start_polling(bot)
    finally:
        # Корректное закрытие ресурсов
        await session.close()
        await bot.session.close()
        if pool:
            await pool.close()
        print("📴 Shutdown complete")

if __name__ == "__main__":
    # ОПРЕДЕЛЕНИЕ ПЛАТФОРМЫ ДЛЯ EVENTLOOP
    if sys.platform == 'win32':
        # Windows требует особого лупа
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    else:
        # На Linux используем uvloop (в разы быстрее)
        try:
            import uvloop
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            print("✅ uvloop enabled")
        except ImportError:
            pass
    
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass