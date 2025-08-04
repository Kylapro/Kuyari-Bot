import asyncio
import logging
import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands


class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="engine", description="View or switch the current Stability AI engine")
    async def engine_command(self, interaction: discord.Interaction, engine: str) -> None:
        if engine == self.bot.curr_engine:
            output = f"Current engine: `{self.bot.curr_engine}`"
        else:
            if interaction.user.id in self.bot.config["permissions"]["users"]["admin_ids"]:
                self.bot.curr_engine = engine
                output = f"Engine switched to: `{engine}`"
                logging.info(output)
            else:
                output = "You don't have permission to change the engine."
        await interaction.response.send_message(
            output,
            ephemeral=(interaction.channel.type == discord.ChannelType.private),
        )

    @engine_command.autocomplete("engine")
    async def engine_autocomplete(self, interaction: discord.Interaction, curr_str: str) -> list[Choice[str]]:
        if curr_str == "":
            self.bot.config = await asyncio.to_thread(self.bot.get_config)
        choices = [
            Choice(name=f"○ {engine}", value=engine)
            for engine in self.bot.config["engines"]
            if engine != self.bot.curr_engine and curr_str.lower() in engine.lower()
        ][:24]
        if curr_str.lower() in self.bot.curr_engine.lower():
            choices += [Choice(name=f"◉ {self.bot.curr_engine} (current)", value=self.bot.curr_engine)]
        return choices

    @app_commands.command(name="model", description="View or switch the current model")
    async def model_command(self, interaction: discord.Interaction, model: str) -> None:
        if model == self.bot.curr_model:
            output = f"Current model: `{self.bot.curr_model}`"
        else:
            if interaction.user.id in self.bot.config["permissions"]["users"]["admin_ids"]:
                self.bot.curr_model = model
                output = f"Model switched to: `{model}`"
                logging.info(output)
            else:
                output = "You don't have permission to change the model."
        await interaction.response.send_message(
            output,
            ephemeral=(interaction.channel.type == discord.ChannelType.private),
        )

    @model_command.autocomplete("model")
    async def model_autocomplete(self, interaction: discord.Interaction, curr_str: str) -> list[Choice[str]]:
        if curr_str == "":
            self.bot.config = await asyncio.to_thread(self.bot.get_config)
        choices = [
            Choice(name=f"○ {model}", value=model)
            for model in self.bot.config["models"]
            if model != self.bot.curr_model and curr_str.lower() in model.lower()
        ][:24]
        if curr_str.lower() in self.bot.curr_model.lower():
            choices += [Choice(name=f"◉ {self.bot.curr_model} (current)", value=self.bot.curr_model)]
        return choices

