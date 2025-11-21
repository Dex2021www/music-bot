import asyncio
import aiohttp
import ujson
import ssl
import sys
import gc
from aiohttp import web
from aiogram import Bot, Dispatcher
from config import TG_TOKEN
from database import init_db, pool
from engines import KeyManager, MultiEngine
from handlers import router, setup_handlers

async def health_check(request):
    return web.Response(text="Alive")

async def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    # Используем порт из ENV если есть, для совместимости с облаками
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    await web.TCPSite(runner, '0.0.0.0', port).start()
    print("🌍 Web server started")

async def main():
    # Принудительная очистка перед запуском
    gc.collect()
    
    await start_web_server()
    await init_db()

    bot = Bot(token=TG_TOKEN)
    dp = Dispatcher()

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    
    # Limit=100, чтобы не переполнить RAM буферами сокетов при пиковой нагрузке
    connector = aiohttp.TCPConnector(
        limit=100, 
        ttl_dns_cache=300, 
        use_dns_cache=True, 
        ssl=ssl_ctx
    )
    
    session = aiohttp.ClientSession(connector=connector, json_serialize=ujson.dumps)

    key_manager = KeyManager(session)
    await key_manager.fetch_new_key()
    
    engine = MultiEngine(session, key_manager)

    setup_handlers(engine, bot) 
    dp.include_router(router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("🚀 Bot Started with Optimized RAM/DB settings")
        await dp.start_polling(bot)
    finally:
        await session.close()
        await bot.session.close()
        if pool:
            await pool.close()
            print("📴 DB Connection closed")

if __name__ == "__main__":
    # Включаем uvloop только если он доступен (на Windows его нет)
    if sys.platform != 'win32':
        try:
            import uvloop
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            print("✅ uvloop enabled")
        except ImportError: pass
    
    asyncio.run(main())