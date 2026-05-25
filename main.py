import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("topupfast.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def main() -> None:
    from config import DISCORD_TOKEN
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN chưa được cấu hình trong .env")
        sys.exit(1)

    import services.database as db
    await db.init_db()
    logger.info("Database đã sẵn sàng.")

    from bot.client import TopUpBot
    from webhooks.server import WebhookServer

    bot = TopUpBot()
    webhook_server = WebhookServer(bot)
    # Store reference on bot so AdminCog can relay Discord messages to WS clients
    bot._webhook_server = webhook_server

    try:
        await asyncio.gather(
            bot.start(DISCORD_TOKEN),
            webhook_server.start(),
        )
    except KeyboardInterrupt:
        logger.info("Đang tắt bot...")
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
