import asyncio
import re
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
}
FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


@dataclass
class Song:
    source: discord.AudioSource
    title: str


class MusicCog(commands.Cog):
    """Simple music playback cog using yt_dlp and FFmpeg."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.queues: dict[int, list[Song]] = {}

        self.music_cfg = bot.config.get("music", {})
        opts = YTDL_OPTS.copy()
        browser = self.music_cfg.get("cookies_browser")
        if browser:
            opts["cookiesfrombrowser"] = (browser,)
        self.ytdl = yt_dlp.YoutubeDL(opts)

    # ---- Helpers ----
    def _has_dj_role(self, interaction: discord.Interaction) -> bool:
        role_id: Optional[int] = self.music_cfg.get("dj_role_id")
        return (
            role_id is None
            or role_id == 0
            or any(r.id == role_id for r in interaction.user.roles)
        )

    async def _create_sources(self, url: str) -> list[Song]:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, lambda: self.ytdl.extract_info(url, download=False)
        )
        entries = data.get("entries")
        if entries:
            songs = [
                Song(
                    source=discord.FFmpegPCMAudio(entry["url"], **FFMPEG_OPTS),
                    title=entry.get("title") or url,
                )
                for entry in entries
                if entry
            ]
            return songs
        return [
            Song(
                source=discord.FFmpegPCMAudio(data["url"], **FFMPEG_OPTS),
                title=data.get("title") or url,
            )
        ]

    async def _fetch_page_title(self, url: str) -> Optional[str]:
        """Return the page title for a given URL."""
        try:
            resp = await self.bot.httpx_client.get(url)
        except Exception:
            return None
        match = re.search(r"<title>(.*?)</title>", resp.text, re.I | re.S)
        return match.group(1).strip() if match else None

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

    async def _safe_send(self, interaction: discord.Interaction, *args, **kwargs) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(*args, **kwargs)
            else:
                await interaction.response.send_message(*args, **kwargs)
        except discord.HTTPException:
            pass

    # ---- Commands ----
    @app_commands.command(name="play", description="Queue a song or playlist by URL")
    async def play_command(self, interaction: discord.Interaction, url: str) -> None:
        if not self._has_dj_role(interaction):
            await self._safe_send(
                interaction,
                "You need the DJ role to use this command.",
                ephemeral=True,
            )
            return
        if not interaction.user.voice or not interaction.user.voice.channel:
            await self._safe_send(
                interaction,
                "Join a voice channel first.",
                ephemeral=True,
            )
            return
        ephemeral = interaction.channel.type == discord.ChannelType.private
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        try:
            songs = await self._create_sources(url)
        except yt_dlp.utils.DownloadError as exc:
            songs: list[Song] = []
            if "drm" in str(exc).lower():
                title = await self._fetch_page_title(url)
                if title:
                    try:
                        songs = await self._create_sources(title)
                    except yt_dlp.utils.DownloadError:
                        pass
                    except discord.ClientException:
                        await self._safe_send(
                            interaction,
                            "FFmpeg was not found. Please install FFmpeg to use this command.",
                            ephemeral=ephemeral,
                        )
                        return
                    except Exception:
                        await self._safe_send(
                            interaction,
                            "An unexpected error occurred while processing the URL.",
                            ephemeral=ephemeral,
                        )
                        return
            if not songs:
                await self._safe_send(
                    interaction,
                    "Could not process the provided URL (possibly DRM-protected or unsupported).",
                    ephemeral=ephemeral,
                )
                return
        except discord.ClientException:
            await self._safe_send(
                interaction,
                "FFmpeg was not found. Please install FFmpeg to use this command.",
                ephemeral=ephemeral,
            )
            return
        except Exception:
            await self._safe_send(
                interaction,
                "An unexpected error occurred while processing the URL.",
                ephemeral=ephemeral,
            )
            return
        voice = interaction.guild.voice_client
        if not voice:
            try:
                voice = await interaction.user.voice.channel.connect()
            except discord.DiscordException:
                await self._safe_send(
                    interaction,
                    "Failed to connect to the voice channel.",
                    ephemeral=ephemeral,
                )
                return
        queue = await self._get_queue(interaction.guild_id)
        queue.extend(songs)
        if len(songs) == 1:
            msg = f"Enqueued: {songs[0].title}"
        else:
            msg = f"Enqueued {len(songs)} songs"
        await self._safe_send(interaction, msg, ephemeral=ephemeral)
        if not voice.is_playing():
            self._play_next(interaction.guild_id)

    @app_commands.command(name="pause", description="Pause the current song")
    async def pause_command(self, interaction: discord.Interaction) -> None:
        if not self._has_dj_role(interaction):
            await self._safe_send(
                interaction,
                "You need the DJ role to use this command.",
                ephemeral=True,
            )
            return
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await self._safe_send(interaction, "Paused")
        else:
            await self._safe_send(
                interaction,
                "Nothing is playing.",
                ephemeral=True,
            )

    @app_commands.command(name="resume", description="Resume the current song")
    async def resume_command(self, interaction: discord.Interaction) -> None:
        if not self._has_dj_role(interaction):
            await self._safe_send(
                interaction,
                "You need the DJ role to use this command.",
                ephemeral=True,
            )
            return
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await self._safe_send(interaction, "Resumed")
        else:
            await self._safe_send(
                interaction,
                "Nothing is paused.",
                ephemeral=True,
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
        await self._safe_send(
            interaction,
            desc,
            ephemeral=(interaction.channel.type == discord.ChannelType.private),
        )

    @app_commands.command(name="clear", description="Clear the music queue")
    async def clear_command(self, interaction: discord.Interaction) -> None:
        if not self._has_dj_role(interaction):
            await self._safe_send(
                interaction,
                "You need the DJ role to use this command.",
                ephemeral=True,
            )
            return
        ephemeral = interaction.channel.type == discord.ChannelType.private
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        queue = await self._get_queue(interaction.guild_id)
        queue.clear()
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await self._safe_send(interaction, "Queue cleared", ephemeral=ephemeral)

    @app_commands.command(name="stop", description="Stop playback and clear the queue")
    async def stop_command(self, interaction: discord.Interaction) -> None:
        if not self._has_dj_role(interaction):
            await self._safe_send(
                interaction,
                "You need the DJ role to use this command.",
                ephemeral=True,
            )
            return
        ephemeral = interaction.channel.type == discord.ChannelType.private
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        queue = await self._get_queue(interaction.guild_id)
        queue.clear()
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await self._safe_send(interaction, "Stopped", ephemeral=ephemeral)
        else:
            await self._safe_send(
                interaction,
                "Nothing is playing.",
                ephemeral=ephemeral,
            )

    @app_commands.command(name="leave", description="Disconnect from the voice channel")
    async def leave_command(self, interaction: discord.Interaction) -> None:
        if not self._has_dj_role(interaction):
            await self._safe_send(
                interaction,
                "You need the DJ role to use this command.",
                ephemeral=True,
            )
            return
        ephemeral = interaction.channel.type == discord.ChannelType.private
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        vc = interaction.guild.voice_client
        if vc:
            queue = await self._get_queue(interaction.guild_id)
            queue.clear()
            await vc.disconnect()
            await self._safe_send(interaction, "Disconnected", ephemeral=ephemeral)
        else:
            await self._safe_send(
                interaction,
                "I'm not in a voice channel.",
                ephemeral=True,
            )

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip_command(self, interaction: discord.Interaction) -> None:
        if not self._has_dj_role(interaction):
            await self._safe_send(
                interaction,
                "You need the DJ role to use this command.",
                ephemeral=True,
            )
            return
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await self._safe_send(interaction, "Skipped")
        else:
            await self._safe_send(
                interaction,
                "Nothing is playing.",
                ephemeral=True,
            )

    @app_commands.command(name="skipto", description="Skip to a song in the queue")
    async def skipto_command(self, interaction: discord.Interaction, position: int) -> None:
        if not self._has_dj_role(interaction):
            await self._safe_send(
                interaction,
                "You need the DJ role to use this command.",
                ephemeral=True,
            )
            return
        queue = await self._get_queue(interaction.guild_id)
        if position < 1 or position > len(queue):
            await self._safe_send(
                interaction,
                "Invalid position.",
                ephemeral=True,
            )
            return
        if position > 1:
            del queue[1:position-1]
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await self._safe_send(interaction, f"Skipped to position {position}")
