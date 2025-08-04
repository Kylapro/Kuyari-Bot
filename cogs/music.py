import logging
import re
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

import bot

GENERATE_MUSIC_PATTERNS = [
    re.compile(
        r"(?:generate|create|make|compose|write|produce).*?(?:music|song|audio) ?(?:about|of|on|for)? (?P<query>.+)",
        re.I,
    ),
    re.compile(r"^(?:music|song|audio)[: ]+(?P<query>.+)", re.I),
]

LINK_PATTERN = re.compile(r"https?://\S+")


class Music(commands.Cog):
    def __init__(self, bot_client: commands.Bot) -> None:
        self.bot = bot_client

    async def _play_url(
        self, guild: discord.Guild, channel: discord.VoiceChannel, url: str
    ) -> str:
        """Join ``channel`` and stream audio from ``url``. Return title."""
        ydl_opts = {"format": "bestaudio/best", "quiet": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stream_url = info["url"]
            title = info.get("title") or "audio"

        vc = guild.voice_client
        if not vc:
            vc = await channel.connect()
        elif vc.channel != channel:
            await vc.move_to(channel)

        if vc.is_playing():
            vc.stop()

        source = await discord.FFmpegOpusAudio.from_probe(stream_url, method="fallback")
        vc.play(source)
        return title

    async def maybe_handle_music_request(self, msg: discord.Message) -> bool:
        if msg.content.startswith("/"):
            return False

        if url_match := LINK_PATTERN.search(msg.content):
            if not msg.author.voice or not msg.author.voice.channel:
                await msg.reply("You must be in a voice channel to play music.")
                return True
            url = url_match.group(0)
            try:
                title = await self._play_url(msg.guild, msg.author.voice.channel, url)
            except Exception:
                logging.exception("Error playing music")
                await msg.reply("Failed to play music.")
            else:
                await msg.reply(f"Now playing: {title}")
            return True

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

    @app_commands.command(name="play", description="Play music from a URL")
    async def play_command(self, interaction: discord.Interaction, url: str) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "You must be in a voice channel to play music.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return

        await interaction.response.defer(
            thinking=True, ephemeral=(interaction.channel.type == discord.ChannelType.private)
        )
        try:
            title = await self._play_url(
                interaction.guild, interaction.user.voice.channel, url
            )
        except Exception:
            logging.exception("Error playing music")
            await interaction.followup.send(
                "Failed to play music.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return

        await interaction.followup.send(
            f"Now playing: {title}",
            ephemeral=(interaction.channel.type == discord.ChannelType.private),
        )


async def setup(bot_client: commands.Bot) -> None:
    await bot_client.add_cog(Music(bot_client))
