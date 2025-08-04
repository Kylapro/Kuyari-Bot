import asyncio
import logging

import discord
from discord.ext import commands
import httpx

from utils import get_config
from cogs.chat_cog import ChatCog
from cogs.config_cog import ConfigCog
from cogs.image_cog import ImageCog
from cogs.music_cog import MusicCog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

config = get_config()
curr_model = next(iter(config["models"]))
curr_engine = next(iter(config["engines"]))

intents = discord.Intents.default()
intents.message_content = True
activity = discord.CustomActivity(name=(config["status_message"] or "github.com/Kylapro/Kuyari-Bot")[:128])
discord_bot = commands.Bot(intents=intents, activity=activity, command_prefix=None)

# shared state
discord_bot.config = config  # type: ignore[attr-defined]
discord_bot.curr_model = curr_model  # type: ignore[attr-defined]
discord_bot.curr_engine = curr_engine  # type: ignore[attr-defined]
discord_bot.msg_nodes = {}  # type: ignore[attr-defined]
discord_bot.last_task_time = 0  # type: ignore[attr-defined]
discord_bot.httpx_client = httpx.AsyncClient(timeout=120.0)  # type: ignore[attr-defined]


async def main() -> None:
    await discord_bot.add_cog(ConfigCog(discord_bot))
    await discord_bot.add_cog(ImageCog(discord_bot))
    await discord_bot.add_cog(MusicCog(discord_bot))
    await discord_bot.add_cog(ChatCog(discord_bot))
    await discord_bot.start(config["bot_token"])


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
