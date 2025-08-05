import asyncio
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp


YTDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "noplaylist": True,
}
FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}
ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)


@dataclass
class Song:
    source: discord.AudioSource
    title: str


class MusicCog(commands.Cog):
    """Simple music playback cog using yt_dlp and FFmpeg."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.queues: dict[int, list[Song]] = {}

    # ---- Helpers ----
    def _has_dj_role(self, interaction: discord.Interaction) -> bool:
        role_id: Optional[int] = (
            self.bot.config.get("music", {}).get("dj_role_id")
        )
        return (
            role_id is None
            or role_id == 0
            or any(r.id == role_id for r in interaction.user.roles)
        )

    async def _create_source(self, url: str) -> Song:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=False)
        )
        if "entries" in data:
            data = data["entries"][0]
        return Song(
            source=discord.FFmpegPCMAudio(data["url"], **FFMPEG_OPTS),
            title=data.get("title") or url,
        )

    def _play_next(self, guild_id: int) -> None:
        queue = self.queues.get(guild_id)
        if not queue:
            return
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return
        song = queue[0]
        guild.voice_client.play(
            song.source,
            after=lambda _: asyncio.run_coroutine_threadsafe(
                self._after_song(guild_id), self.bot.loop
            ),
        )

    async def _after_song(self, guild_id: int) -> None:
        queue = self.queues.get(guild_id)
        if queue:
            queue.pop(0)
        if queue:
            self._play_next(guild_id)

    async def _get_queue(self, guild_id: int) -> list[Song]:
        return self.queues.setdefault(guild_id, [])

    # ---- Commands ----
    @app_commands.command(name="play", description="Queue a song by URL")
    async def play_command(self, interaction: discord.Interaction, url: str) -> None:
        if not self._has_dj_role(interaction):
            await interaction.response.send_message(
                "You need the DJ role to use this command.", ephemeral=True
            )
            return
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "Join a voice channel first.", ephemeral=True
            )
            return
        ephemeral = interaction.channel.type == discord.ChannelType.private
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        try:
            song = await self._create_source(url)
        except yt_dlp.utils.DownloadError:
            await interaction.followup.send(
                "Could not process the provided URL (possibly DRM-protected or unsupported).",
                ephemeral=ephemeral,
            )
            return
        except discord.ClientException:
            await interaction.followup.send(
                "FFmpeg was not found. Please install FFmpeg to use this command.",
                ephemeral=ephemeral,
            )
            return
        except Exception:
            await interaction.followup.send(
                "An unexpected error occurred while processing the URL.",
                ephemeral=ephemeral,
            )
            return
        voice = interaction.guild.voice_client
        if not voice:
            try:
                voice = await interaction.user.voice.channel.connect()
            except discord.DiscordException:
                await interaction.followup.send(
                    "Failed to connect to the voice channel.", ephemeral=ephemeral
                )
                return
        queue = await self._get_queue(interaction.guild_id)
        queue.append(song)
        await interaction.followup.send(
            f"Enqueued: {song.title}",
            ephemeral=ephemeral,
        )
        if not voice.is_playing():
            self._play_next(interaction.guild_id)

    @app_commands.command(name="pause", description="Pause the current song")
    async def pause_command(self, interaction: discord.Interaction) -> None:
        if not self._has_dj_role(interaction):
            await interaction.response.send_message(
                "You need the DJ role to use this command.", ephemeral=True
            )
            return
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("Paused")
        else:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )

    @app_commands.command(name="resume", description="Resume the current song")
    async def resume_command(self, interaction: discord.Interaction) -> None:
        if not self._has_dj_role(interaction):
            await interaction.response.send_message(
                "You need the DJ role to use this command.", ephemeral=True
            )
            return
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("Resumed")
        else:
            await interaction.response.send_message(
                "Nothing is paused.", ephemeral=True
            )

    @app_commands.command(name="queue", description="View the music queue")
    async def queue_command(self, interaction: discord.Interaction) -> None:
        queue = await self._get_queue(interaction.guild_id)
        if not queue:
            desc = "Queue is empty."
        else:
            lines = [f"Now playing: {queue[0].title}"]
            for idx, song in enumerate(queue[1:], start=1):
                lines.append(f"{idx}. {song.title}")
            desc = "\n".join(lines)
        await interaction.response.send_message(
            desc, ephemeral=(interaction.channel.type == discord.ChannelType.private)
        )

    @app_commands.command(name="clear", description="Clear the music queue")
    async def clear_command(self, interaction: discord.Interaction) -> None:
        if not self._has_dj_role(interaction):
            await interaction.response.send_message(
                "You need the DJ role to use this command.", ephemeral=True
            )
            return
        queue = await self._get_queue(interaction.guild_id)
        queue.clear()
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await interaction.response.send_message("Queue cleared")

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip_command(self, interaction: discord.Interaction) -> None:
        if not self._has_dj_role(interaction):
            await interaction.response.send_message(
                "You need the DJ role to use this command.", ephemeral=True
            )
            return
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("Skipped")
        else:
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )

    @app_commands.command(name="skipto", description="Skip to a song in the queue")
    async def skipto_command(self, interaction: discord.Interaction, position: int) -> None:
        if not self._has_dj_role(interaction):
            await interaction.response.send_message(
                "You need the DJ role to use this command.", ephemeral=True
            )
            return
        queue = await self._get_queue(interaction.guild_id)
        if position < 1 or position > len(queue):
            await interaction.response.send_message(
                "Invalid position.", ephemeral=True
            )
            return
        if position > 1:
            del queue[1:position-1]
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await interaction.response.send_message(f"Skipped to position {position}")
