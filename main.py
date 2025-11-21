import asyncio
import aiohttp
import ujson
import ssl
import sys
from aiohttp import web
from aiogram import Bot, Dispatcher
from config import TG_TOKEN
from database import init_db
from engines import KeyManager, MultiEngine
from handlers import router, setup_handlers # Импортируем роутер

# Веб-сервер для пинга
async def health_check(request):
    return web.Response(text="Alive")

async def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8080).start()
    print("🌍 Web server started")

async def main():
    await start_web_server()
    
    # Инициализация базы
    await init_db()

    # Инициализация бота
    bot = Bot(token=TG_TOKEN)
    dp = Dispatcher()

    # Настройка сети
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(limit=0, ttl_dns_cache=300, use_dns_cache=True, ssl=ssl_ctx) # Ускорение
    session = aiohttp.ClientSession(connector=connector, json_serialize=ujson.dumps)

    # Запуск движков
    key_manager = KeyManager(session)
    await key_manager.fetch_new_key()
    engine = MultiEngine(session, key_manager)

    # Передаем engine в handlers и подключаем роутер
    setup_handlers(engine, bot) 
    dp.include_router(router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("🚀 Bot Started")
        await dp.start_polling(bot)
    finally:
        await session.close()
        await bot.session.close()

                # Закрываем базу данных
        from database import pool
        if pool:
            await pool.close()
            print("📴 DB Connection closed")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        import uvloop
        uvloop.install()
        print("✅ uvloop enabled")
    except: pass

    asyncio.run(main())