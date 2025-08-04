import asyncio

import bot


async def main() -> None:
    await bot.discord_bot.load_extension("cogs.admin")
    await bot.discord_bot.load_extension("cogs.image")
    await bot.discord_bot.load_extension("cogs.music")
    await bot.discord_bot.load_extension("cogs.chat")
    await bot.discord_bot.start(bot.config["bot_token"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
