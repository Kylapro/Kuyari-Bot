import logging
import re
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands

import bot

GENERATE_MUSIC_PATTERNS = [
    re.compile(
        r"(?:generate|create|make|compose|write|produce).*?(?:music|song|audio) ?(?:about|of|on|for)? (?P<query>.+)",
        re.I,
    ),
    re.compile(r"^(?:music|song|audio)[: ]+(?P<query>.+)", re.I),
]


class Music(commands.Cog):
    def __init__(self, bot_client: commands.Bot) -> None:
        self.bot = bot_client

    async def maybe_handle_music_request(self, msg: discord.Message) -> bool:
        if msg.content.startswith("/"):
            return False
        for pattern in GENERATE_MUSIC_PATTERNS:
            if match := pattern.search(msg.content):
                prompt = match.group("query").strip()
                try:
                    audio_bytes = await bot.generate_music_bytes(prompt)
                except RuntimeError:
                    await msg.reply("Music generation is not configured.")
                except Exception:
                    logging.exception("Error generating music")
                    await msg.reply("Failed to generate music.")
                else:
                    file = discord.File(BytesIO(audio_bytes), filename="music.mp3")
                    await msg.reply(file=file)
                return True
        return False

    @app_commands.command(name="music", description="Generate music from a prompt")
    async def music_command(
        self, interaction: discord.Interaction, *, prompt: str, duration: int = 20
    ) -> None:
        await interaction.response.defer(
            thinking=True, ephemeral=(interaction.channel.type == discord.ChannelType.private)
        )
        try:
            audio_bytes = await bot.generate_music_bytes(prompt, duration=duration)
        except RuntimeError:
            await interaction.followup.send(
                "Music generation is not configured.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return
        except Exception:
            logging.exception("Error generating music")
            await interaction.followup.send(
                "Failed to generate music.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return
        file = discord.File(BytesIO(audio_bytes), filename="music.mp3")
        await interaction.followup.send(
            file=file, ephemeral=(interaction.channel.type == discord.ChannelType.private)
        )


async def setup(bot_client: commands.Bot) -> None:
    await bot_client.add_cog(Music(bot_client))
