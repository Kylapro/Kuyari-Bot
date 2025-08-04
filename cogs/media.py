import logging
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands


class MediaCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="image", description="Search Google images")
    async def image_command(self, interaction: discord.Interaction, *, query: str) -> None:
        """Search Google Images using the Custom Search API."""
        google_key = self.bot.config.get("google_api_key")
        google_cx = self.bot.config.get("google_cse_id")

        if not google_key or not google_cx:
            await interaction.response.send_message(
                "Google search is not configured.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return

        try:
            resp = await self.bot.httpx_client.get(
                "https://www.googleapis.com/customsearch/v1",
                params=dict(q=query, searchType="image", num=1, key=google_key, cx=google_cx),
            )
            data = resp.json()
            items = data.get("items") or []
        except Exception:
            logging.exception("Error searching Google Images")
            await interaction.response.send_message(
                "Failed to search images.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return

        if not items:
            await interaction.response.send_message(
                "No images found.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return

        embed = discord.Embed(title=query)
        embed.set_image(url=items[0]["link"])
        await interaction.response.send_message(
            embed=embed, ephemeral=(interaction.channel.type == discord.ChannelType.private)
        )

    @app_commands.command(name="imagine", description="Generate an image from a prompt")
    async def imagine_command(self, interaction: discord.Interaction, *, prompt: str) -> None:
        """Generate an image using the Stable Diffusion API."""
        try:
            image_bytes = await self.bot.generate_image_bytes(prompt)
        except RuntimeError:
            await interaction.response.send_message(
                "Image generation is not configured.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return
        except Exception:
            logging.exception("Error generating image")
            await interaction.response.send_message(
                "Failed to generate image.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return

        file = discord.File(BytesIO(image_bytes), filename="image.png")
        embed = discord.Embed(title=prompt)
        embed.set_image(url="attachment://image.png")
        await interaction.response.send_message(
            file=file, embed=embed, ephemeral=(interaction.channel.type == discord.ChannelType.private)
        )

    @app_commands.command(name="music", description="Generate music from a prompt")
    async def music_command(self, interaction: discord.Interaction, *, prompt: str, duration: int = 20) -> None:
        """Generate an audio clip using the Stable Audio API."""
        await interaction.response.defer(
            thinking=True, ephemeral=(interaction.channel.type == discord.ChannelType.private)
        )
        try:
            audio_bytes = await self.bot.generate_music_bytes(prompt, duration=duration)
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

