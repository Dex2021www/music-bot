import asyncio
import aiohttp
import ujson
import ssl
import sys
import gc
import logging # <--- ВАЖНО
from aiohttp import web, AsyncResolver
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from config import TG_TOKEN
from database import init_db, pool
from engines import KeyManager, MultiEngine
from handlers import router, setup_handlers

# --- НАСТРОЙКА ЛОГОВ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MAIN")

async def health_check(request):
    return web.Response(text="Alive")

async def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    await web.TCPSite(runner, '0.0.0.0', port).start()
    logger.info(f"🌍 Web server running on port {port}")

async def main():
    gc.collect()
    logger.info("🚀 Initializing Bot...")
    
    await init_db()
    await start_web_server()

    bot = Bot(
        token=TG_TOKEN, 
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher()

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    
    resolver = None
    try:
        import aiodns
        resolver = AsyncResolver()
        logger.info("✅ aiodns found and enabled")
    except ImportError:
        logger.warning("⚠️ aiodns not found, using default resolver")

    connector = aiohttp.TCPConnector(
        limit=100, 
        ttl_dns_cache=300, 
        use_dns_cache=True, 
        ssl=ssl_ctx,
        resolver=resolver
    )
    
    session = aiohttp.ClientSession(
        connector=connector, 
        json_serialize=ujson.dumps
    )

    key_manager = KeyManager(session)
    await key_manager.fetch_new_key()
    
    engine = MultiEngine(session, key_manager)

    setup_handlers(engine, bot) 
    dp.include_router(router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Bot Started & Polling...")
        await dp.start_polling(bot)
    finally:
        await session.close()
        await bot.session.close()
        if pool:
            await pool.close()
        logger.info("📴 Shutdown complete")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    else:
        try:
            import uvloop
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            print("✅ uvloop enabled")
        except ImportError: pass
    
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass