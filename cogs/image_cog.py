import logging
import re
from base64 import b64decode
from io import BytesIO
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands
import httpx


class ImageCog(commands.Cog):
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

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def google_image_search(self, query: str) -> Optional[str]:
        google_key = self.bot.config.get("google_api_key")
        google_cx = self.bot.config.get("google_cse_id")
        if not google_key or not google_cx:
            return None
        try:
            resp = await self.bot.httpx_client.get(
                "https://www.googleapis.com/customsearch/v1",
                params=dict(q=query, searchType="image", num=1, key=google_key, cx=google_cx),
            )
            data = resp.json()
            items = data.get("items") or []
            return items[0]["link"] if items else None
        except Exception:
            logging.exception("Error searching Google Images")
            return None

    async def generate_image_bytes(self, prompt: str) -> bytes:
        provider_config = self.bot.config["providers"].get("stable_diffusion", {})
        api_key = provider_config.get("api_key")
        base_url = provider_config.get("base_url")
        if not api_key or not base_url:
            raise RuntimeError("Image generation is not configured.")
        engine_path = self.bot.config["engines"].get(self.bot.curr_engine)
        if not engine_path:
            raise RuntimeError("No engine configured.")
        payload: dict[str, Any] = {"text_prompts": [{"text": prompt}]}
        decoder = lambda data: b64decode(data["artifacts"][0]["base64"])
        req_kwargs: dict[str, Any] = {"json": payload}
        if engine_path.startswith("/v2beta"):
            payload = {"prompt": prompt, "mode": "text-to-image", "aspect_ratio": "1:1"}
            decoder = lambda data: b64decode(data["image"])
            req_kwargs = {"files": {k: (None, v) for k, v in payload.items()}}
        resp = await self.bot.httpx_client.post(
            f"{base_url}{engine_path}",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            **req_kwargs,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            logging.error("Stable Diffusion API error %s: %s", resp.status_code, resp.text)
            raise
        data = resp.json()
        return decoder(data)

    async def maybe_handle_image_request(self, msg: discord.Message) -> bool:
        if msg.content.startswith("/"):
            return False
        for pattern in self.GENERATE_IMAGE_PATTERNS:
            if match := pattern.search(msg.content):
                prompt = match.group("query").strip()
                try:
                    image_bytes = await self.generate_image_bytes(prompt)
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
        for pattern in self.GOOGLE_IMAGE_PATTERNS:
            if match := pattern.search(msg.content):
                query = match.group("query").strip()
                url = await self.google_image_search(query)
                if url:
                    embed = discord.Embed(title=query)
                    embed.set_image(url=url)
                    await msg.reply(embed=embed)
                else:
                    await msg.reply(
                        "No images found." if self.bot.config.get("google_api_key") and self.bot.config.get("google_cse_id") else "Google search is not configured."
                    )
                return True
        return False

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message) -> None:
        await self.maybe_handle_image_request(msg)

    @app_commands.command(name="image", description="Search Google images")
    async def image_command(self, interaction: discord.Interaction, *, query: str) -> None:
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
            embed=embed,
            ephemeral=(interaction.channel.type == discord.ChannelType.private),
        )

    @app_commands.command(name="imagine", description="Generate an image from a prompt")
    async def imagine_command(self, interaction: discord.Interaction, *, prompt: str) -> None:
        try:
            image_bytes = await self.generate_image_bytes(prompt)
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
            file=file,
            embed=embed,
            ephemeral=(interaction.channel.type == discord.ChannelType.private),
        )
