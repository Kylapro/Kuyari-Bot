import discord
from discord import app_commands
from discord.ext import commands
from io import BytesIO


class EmojiCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="emojis", description="List this server's emojis with IDs")
    async def emojis_command(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        emojis = interaction.guild.emojis
        if not emojis:
            await interaction.response.send_message(
                "This server has no custom emojis.",
                ephemeral=(interaction.channel.type == discord.ChannelType.private),
            )
            return

        lines = [f"{emoji} `:{emoji.name}:` `{emoji.id}`" for emoji in emojis]
        content = "\n".join(lines)
        ephemeral = interaction.channel.type == discord.ChannelType.private

        if len(content) <= 2000:
            await interaction.response.send_message(content, ephemeral=ephemeral)
        else:
            file = discord.File(BytesIO(content.encode("utf-8")), filename="emojis.txt")
            await interaction.response.send_message(file=file, ephemeral=ephemeral)
