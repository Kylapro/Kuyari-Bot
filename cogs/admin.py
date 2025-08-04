import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import bot


class Admin(commands.Cog):
    def __init__(self, bot_client: commands.Bot) -> None:
        self.bot = bot_client

    @app_commands.command(name="engine", description="View or switch the current Stability AI engine")
    async def engine_command(self, interaction: discord.Interaction, engine: str) -> None:
        if engine == bot.curr_engine:
            output = f"Current engine: `{bot.curr_engine}`"
        else:
            if interaction.user.id in bot.config["permissions"]["users"]["admin_ids"]:
                bot.curr_engine = engine
                output = f"Engine switched to: `{engine}`"
                logging.info(output)
            else:
                output = "You don't have permission to change the engine."

        await interaction.response.send_message(
            output, ephemeral=(interaction.channel.type == discord.ChannelType.private)
        )

    @engine_command.autocomplete("engine")
    async def engine_autocomplete(
        self, interaction: discord.Interaction, curr_str: str
    ) -> list[app_commands.Choice[str]]:
        if curr_str == "":
            bot.config = await asyncio.to_thread(bot.get_config)

        choices = [
            app_commands.Choice(name=f"○ {engine}", value=engine)
            for engine in bot.config["engines"]
            if engine != bot.curr_engine and curr_str.lower() in engine.lower()
        ][:24]
        if curr_str.lower() in bot.curr_engine.lower():
            choices.append(
                app_commands.Choice(
                    name=f"◉ {bot.curr_engine} (current)", value=bot.curr_engine
                )
            )
        return choices

    @app_commands.command(name="model", description="View or switch the current model")
    async def model_command(self, interaction: discord.Interaction, model: str) -> None:
        if model == bot.curr_model:
            output = f"Current model: `{bot.curr_model}`"
        else:
            if interaction.user.id in bot.config["permissions"]["users"]["admin_ids"]:
                bot.curr_model = model
                output = f"Model switched to: `{model}`"
                logging.info(output)
            else:
                output = "You don't have permission to change the model."

        await interaction.response.send_message(
            output, ephemeral=(interaction.channel.type == discord.ChannelType.private)
        )

    @model_command.autocomplete("model")
    async def model_autocomplete(
        self, interaction: discord.Interaction, curr_str: str
    ) -> list[app_commands.Choice[str]]:
        if curr_str == "":
            bot.config = await asyncio.to_thread(bot.get_config)

        choices = [
            app_commands.Choice(name=f"○ {model}", value=model)
            for model in bot.config["models"]
            if model != bot.curr_model and curr_str.lower() in model.lower()
        ][:24]
        if curr_str.lower() in bot.curr_model.lower():
            choices.append(
                app_commands.Choice(
                    name=f"◉ {bot.curr_model} (current)", value=bot.curr_model
                )
            )
        return choices


async def setup(bot_client: commands.Bot) -> None:
    await bot_client.add_cog(Admin(bot_client))
