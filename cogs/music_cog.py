import logging
import re
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands
import httpx


class MusicCog(commands.Cog):
    GENERATE_MUSIC_PATTERNS = [
        re.compile(
            r"(?:generate|create|make|compose|write|produce).*?(?:music|song|audio) ?(?:about|of|on|for)? (?P<query>.+)",
            re.I,
        ),
        re.compile(r"^(?:music|song|audio)[: ]+(?P<query>.+)", re.I),
    ]

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def generate_music_bytes(self, prompt: str, *, duration: int = 20) -> bytes:
        provider_config = self.bot.config["providers"].get("stable_diffusion", {})
        api_key = provider_config.get("api_key")
        base_url = provider_config.get("base_url")
        if not api_key or not base_url:
            raise RuntimeError("Music generation is not configured.")
        data = {"prompt": prompt, "duration": str(duration), "model": "stable-audio-2.5"}
        files = {"none": ""}
        resp = await self.bot.httpx_client.post(
            f"{base_url}/v2beta/audio/stable-audio-2/text-to-audio",
            headers={"Authorization": f"Bearer {api_key}", "accept": "audio/*"},
            data=data,
            files=files,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            logging.error("Stable Audio API error %s: %s", resp.status_code, resp.text)
            raise
        return resp.content

    async def maybe_handle_music_request(self, msg: discord.Message) -> bool:
        if msg.content.startswith("/"):
            return False
        for pattern in self.GENERATE_MUSIC_PATTERNS:
            if match := pattern.search(msg.content):
                prompt = match.group("query").strip()
                try:
                    audio_bytes = await self.generate_music_bytes(prompt)
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

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message) -> None:
        await self.maybe_handle_music_request(msg)

    @app_commands.command(name="music", description="Generate music from a prompt")
    async def music_command(self, interaction: discord.Interaction, *, prompt: str, duration: int = 20) -> None:
        await interaction.response.defer(
            thinking=True, ephemeral=(interaction.channel.type == discord.ChannelType.private)
        )
        try:
            audio_bytes = await self.generate_music_bytes(prompt, duration=duration)
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
