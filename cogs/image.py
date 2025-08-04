import logging
import re
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands

import bot

GENERATE_IMAGE_PATTERNS = [
    re.compile(r"(?:generate|create|make|draw|imagine).*?(?:image|picture|pic|photo) of (?P<query>.+)", re.I),
    re.compile(r"^(?:please )?(?:generate|create|make|draw|imagine) (?P<query>.+)", re.I),
]

GOOGLE_IMAGE_PATTERNS = [
    re.compile(r"^(?:image|picture|pic|photo)[: ]+(?P<query>.+)", re.I),
    re.compile(r"(?:send|show|find|get).*?(?:image|picture|pic|photo) of (?P<query>.+)", re.I),
    re.compile(r"(?:image|picture|pic|photo) of (?P<query>.+)", re.I),
    re.compile(r"^(?:image|picture|pic|photo)[: ]+(?P<query>.+)", re.I),
    re.compile(r"(?:send|show|find|get).*?(?:image|picture|pic|photo) of (?P<query>.+)", re.I),
    re.compile(r"(?:image|picture|pic|photo) of (?P<query>.+)", re.I),
]


class Image(commands.Cog):
    def __init__(self, bot_client: commands.Bot) -> None:
        self.bot = bot_client

    async def maybe_handle_image_request(self, msg: discord.Message) -> bool:
        if msg.content.startswith("/"):
            return False
        for pattern in GENERATE_IMAGE_PATTERNS:
            if match := pattern.search(msg.content):
                prompt = match.group("query").strip()
                try:
                    image_bytes = await bot.generate_image_bytes(prompt)
                except RuntimeError:
                    await msg.reply("Image generation is not configured.")
                except Exception:
                    logging.exception("Error generating image")
                    await msg.reply("Failed to generate image.")
                else:
                    file = discord.File(BytesIO(image_bytes), filename="image.png")
                    embed = discord.Embed(title=prompt)
                    embed.set_image(url="attachment://image.png")
                    await msg.reply(file=file, embed=embed)
                return True
        for pattern in GOOGLE_IMAGE_PATTERNS:
            if match := pattern.search(msg.content):
                query = match.group("query").strip()
                url = await bot.google_image_search(query)
                if url:
                    embed = discord.Embed(title=query)
                    embed.set_image(url=url)
                    await msg.reply(embed=embed)
                else:
                    await msg.reply(
                        "No images found."
                        if bot.config.get("google_api_key") and bot.config.get("google_cse_id")
                        else "Google search is not configured.",
                    )
                return True
        return False

    @app_commands.command(name="image", description="Search Google images")
    async def image_command(self, interaction: discord.Interaction, *, query: str) -> None:
        google_key = bot.config.get("google_api_key")
        google_cx = bot.config.get("google_cse_id")
        if not google_key or not google_cx:
            await interaction.response.send_message(
                "Google search is not configured.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return
        try:
            resp = await bot.httpx_client.get(
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
        try:
            image_bytes = await bot.generate_image_bytes(prompt)
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


async def setup(bot_client: commands.Bot) -> None:
    await bot_client.add_cog(Image(bot_client))
