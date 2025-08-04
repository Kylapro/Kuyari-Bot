import asyncio
from base64 import b64encode
from datetime import datetime
import logging
import random
from typing import Any

import discord
from discord.ext import commands
from openai import AsyncOpenAI

import bot


class Chat(commands.Cog):
    def __init__(self, bot_client: commands.Bot) -> None:
        self.bot = bot_client

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if client_id := bot.config["client_id"]:
            logging.info(
                f"\n\nBOT INVITE URL:\nhttps://discord.com/oauth2/authorize?client_id={client_id}&permissions=412317273088&scope=bot\n"
            )
        await self.bot.tree.sync()

    @commands.Cog.listener()
    async def on_message(self, new_msg: discord.Message) -> None:
        is_dm = new_msg.channel.type == discord.ChannelType.private
        if new_msg.author.bot:
            return

        should_respond_passively = False
        if not is_dm and self.bot.user not in new_msg.mentions:
            bot.config = await asyncio.to_thread(bot.get_config)
            allow_passive = bot.config.get("allow_passive_chat", False)
            chance = bot.config.get("passive_chat_probability", 0.0)
            if allow_passive and random.random() < chance:
                should_respond_passively = True
            if not should_respond_passively:
                return

        role_ids = set(role.id for role in getattr(new_msg.author, "roles", ()))
        channel_ids = set(
            filter(
                None,
                (
                    new_msg.channel.id,
                    getattr(new_msg.channel, "parent_id", None),
                    getattr(new_msg.channel, "category_id", None),
                ),
            )
        )

        bot.config = await asyncio.to_thread(bot.get_config)
        allow_dms = bot.config.get("allow_dms", True)
        permissions = bot.config["permissions"]
        user_is_admin = new_msg.author.id in permissions["users"]["admin_ids"]
        (
            (allowed_user_ids, blocked_user_ids),
            (allowed_role_ids, blocked_role_ids),
            (allowed_channel_ids, blocked_channel_ids),
        ) = (
            (perm["allowed_ids"], perm["blocked_ids"]) for perm in (permissions["users"], permissions["roles"], permissions["channels"])
        )

        allow_all_users = (
            not allowed_user_ids if is_dm else not allowed_user_ids and not allowed_role_ids
        )
        is_good_user = (
            user_is_admin
            or allow_all_users
            or new_msg.author.id in allowed_user_ids
            or any(id in allowed_role_ids for id in role_ids)
        )
        is_bad_user = (
            not is_good_user
            or new_msg.author.id in blocked_user_ids
            or any(id in blocked_role_ids for id in role_ids)
        )

        allow_all_channels = not allowed_channel_ids
        is_good_channel = (
            user_is_admin
            or allow_dms
            if is_dm
            else allow_all_channels or any(id in allowed_channel_ids for id in channel_ids)
        )
        is_bad_channel = (
            not is_good_channel or any(id in blocked_channel_ids for id in channel_ids)
        )
        if is_bad_user or is_bad_channel:
            return

        music_cog = self.bot.get_cog("Music")
        if music_cog and await music_cog.maybe_handle_music_request(new_msg):
            return
        image_cog = self.bot.get_cog("Image")
        if image_cog and await image_cog.maybe_handle_image_request(new_msg):
            return

        provider_slash_model = bot.curr_model
        provider, model = provider_slash_model.removesuffix(":vision").split("/", 1)
        provider_config = bot.config["providers"][provider]
        base_url = provider_config["base_url"]
        api_key = provider_config.get("api_key", "sk-no-key-required")
        openai_client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        model_parameters = bot.config["models"].get(provider_slash_model, None)
        extra_headers = provider_config.get("extra_headers", None)
        extra_query = provider_config.get("extra_query", None)
        extra_body = (provider_config.get("extra_body", None) or {}) | (model_parameters or {}) or None
        reasoning_config = bot.config.get("reasoning")
        if reasoning_config:
            extra_body = {**(extra_body or {}), "reasoning": reasoning_config}
        accept_images = any(x in provider_slash_model.lower() for x in bot.VISION_MODEL_TAGS)
        accept_usernames = any(
            x in provider_slash_model.lower() for x in bot.PROVIDERS_SUPPORTING_USERNAMES
        )
        max_text = bot.config.get("max_text", 100000)
        max_images = bot.config.get("max_images", 5) if accept_images else 0
        max_messages = bot.config.get("max_messages", 25)

        messages = []
        user_warnings = set()
        curr_msg = new_msg
        while curr_msg is not None and len(messages) < max_messages:
            curr_node = bot.msg_nodes.setdefault(curr_msg.id, bot.MsgNode())
            async with curr_node.lock:
                if curr_node.text is None:
                    cleaned_content = (
                        curr_msg.content.removeprefix(self.bot.user.mention).lstrip()
                    )
                    good_attachments = [
                        att
                        for att in curr_msg.attachments
                        if att.content_type
                        and any(att.content_type.startswith(x) for x in ("text", "image"))
                    ]
                    attachment_responses = await asyncio.gather(
                        *[bot.httpx_client.get(att.url) for att in good_attachments]
                    )
                    curr_node.text = "\n".join(
                        ([cleaned_content] if cleaned_content else [])
                        + [
                            "\n".join(
                                filter(
                                    None,
                                    (embed.title, embed.description, embed.footer.text),
                                )
                            )
                            for embed in curr_msg.embeds
                        ]
                        + [
                            resp.text
                            for att, resp in zip(good_attachments, attachment_responses)
                            if att.content_type.startswith("text")
                        ]
                    )
                    curr_node.images = [
                        dict(
                            type="image_url",
                            image_url=dict(
                                url=f"data:{att.content_type};base64,{b64encode(resp.content).decode('utf-8')}"
                            ),
                        )
                        for att, resp in zip(good_attachments, attachment_responses)
                        if att.content_type.startswith("image")
                    ]
                    curr_node.role = (
                        "user" if curr_msg.author.id != self.bot.user.id else "assistant"
                    )
                    if curr_node.role == "user" and accept_usernames:
                        curr_node.user_id = curr_msg.author.id
                    if bad_attachments := [
                        att
                        for att in curr_msg.attachments
                        if att.content_type and not any(
                            att.content_type.startswith(x) for x in ("text", "image")
                        )
                    ]:
                        curr_node.has_bad_attachments = True
                        user_warnings.add(
                            f"Ignored {len(bad_attachments)} attachment(s) from message {curr_msg.id}"
                        )
                    if curr_msg.reference:
                        try:
                            curr_node.parent_msg = await curr_msg.channel.fetch_message(
                                curr_msg.reference.message_id
                            )
                        except Exception:
                            curr_node.fetch_parent_failed = True
                            user_warnings.add(
                                f"Failed to fetch parent message {curr_msg.reference.message_id}"
                            )
            if curr_msg.author.id != self.bot.user.id:
                messages.append(
                    dict(role="user", content=[{"type": "text", "text": curr_node.text}])
                    if curr_node.images == []
                    else dict(
                        role="user",
                        content=[
                            {"type": "text", "text": curr_node.text},
                            *curr_node.images[:max_images],
                        ],
                    )
                )
            else:
                messages.append(dict(role="assistant", content=curr_node.text))
            curr_msg = curr_node.parent_msg
        reasoning_instruction = (
            "Please reason step by step before giving the final answer."
        )
        for msg in reversed(messages):
            if msg.get("role") == "user":
                if isinstance(msg["content"], list):
                    for part in msg["content"]:
                        if isinstance(part, dict) and part.get("type") == "text":
                            part["text"] += "\n\n" + reasoning_instruction
                            break
                    else:
                        msg["content"].insert(0, {"type": "text", "text": reasoning_instruction})
                else:
                    msg["content"] += "\n\n" + reasoning_instruction
                break

        curr_content = curr_reasoning = finish_reason = edit_task = None
        response_msgs = []
        response_contents: list[str] = []
        reasoning_contents = ""
        embed = discord.Embed()
        for warning in sorted(user_warnings):
            embed.add_field(name=warning, value="", inline=False)
        use_plain_responses = bot.config.get("use_plain_responses", False)
        max_message_length = (
            2000 if use_plain_responses else (4096 - len(bot.STREAMING_INDICATOR))
        )
        kwargs = dict(
            model=model,
            messages=messages[::-1],
            stream=True,
            extra_headers=extra_headers,
            extra_query=extra_query,
            extra_body=extra_body,
        )
        try:
            async with new_msg.channel.typing():
                async for chunk in await openai_client.chat.completions.create(**kwargs):
                    if finish_reason is not None:
                        break
                    if not (choice := chunk.choices[0] if chunk.choices else None):
                        continue
                    finish_reason = choice.finish_reason
                    prev_content = curr_content or ""
                    curr_content = choice.delta.content or ""
                    curr_reasoning = getattr(choice.delta, "reasoning", "") or ""
                    new_content = (
                        prev_content if finish_reason is None else (prev_content + curr_content)
                    )
                    if response_contents == [] and new_content == "":
                        continue
                    if (
                        start_next_msg := response_contents == []
                        or len(response_contents[-1] + new_content) > max_message_length
                    ):
                        response_contents.append("")
                    response_contents[-1] += new_content
                    if curr_reasoning:
                        reasoning_contents += curr_reasoning
                    if not use_plain_responses:
                        ready_to_edit = (
                            (edit_task is None or edit_task.done())
                            and datetime.now().timestamp() - bot.last_task_time
                            >= bot.EDIT_DELAY_SECONDS
                        )
                        msg_split_incoming = (
                            finish_reason is None
                            and len(response_contents[-1] + curr_content)
                            > max_message_length
                        )
                        is_final_edit = (
                            finish_reason is not None or msg_split_incoming
                        )
                        is_good_finish = (
                            finish_reason is not None
                            and finish_reason.lower() in ("stop", "end_turn")
                        )
                        if start_next_msg or ready_to_edit or is_final_edit:
                            if edit_task is not None:
                                await edit_task
                            embed.description = (
                                response_contents[-1]
                                if is_final_edit
                                else (response_contents[-1] + bot.STREAMING_INDICATOR)
                            )
                            embed.color = (
                                bot.EMBED_COLOR_COMPLETE
                                if msg_split_incoming or is_good_finish
                                else bot.EMBED_COLOR_INCOMPLETE
                            )
                            if start_next_msg:
                                reply_to_msg = (
                                    new_msg if response_msgs == [] else response_msgs[-1]
                                )
                                response_msg = await reply_to_msg.reply(
                                    embed=embed, silent=True
                                )
                                response_msgs.append(response_msg)
                                bot.msg_nodes[response_msg.id] = bot.MsgNode(
                                    parent_msg=new_msg
                                )
                                await bot.msg_nodes[response_msg.id].lock.acquire()
                            else:
                                edit_task = asyncio.create_task(
                                    response_msgs[-1].edit(embed=embed)
                                )
                            bot.last_task_time = datetime.now().timestamp()
            if reasoning_contents:
                response_contents.append(f"Reasoning:\n{reasoning_contents}")
                if not use_plain_responses:
                    embed.add_field(
                        name="Reasoning", value=reasoning_contents, inline=False
                    )
                    if edit_task is not None:
                        await edit_task
                    if response_msgs:
                        await response_msgs[-1].edit(embed=embed)
            if use_plain_responses:
                for content in response_contents:
                    reply_to_msg = (
                        new_msg if response_msgs == [] else response_msgs[-1]
                    )
                    response_msg = await reply_to_msg.reply(
                        content=content, suppress_embeds=True
                    )
                    response_msgs.append(response_msg)
                    bot.msg_nodes[response_msg.id] = bot.MsgNode(parent_msg=new_msg)
                    await bot.msg_nodes[response_msg.id].lock.acquire()
        except Exception:
            logging.exception("Error while generating response")
        for response_msg in response_msgs:
            bot.msg_nodes[response_msg.id].text = "".join(response_contents)
            bot.msg_nodes[response_msg.id].lock.release()
        if (num_nodes := len(bot.msg_nodes)) > bot.MAX_MESSAGE_NODES:
            for msg_id in sorted(bot.msg_nodes.keys())[
                : num_nodes - bot.MAX_MESSAGE_NODES
            ]:
                async with bot.msg_nodes.setdefault(msg_id, bot.MsgNode()).lock:
                    bot.msg_nodes.pop(msg_id, None)


async def setup(bot_client: commands.Bot) -> None:
    await bot_client.add_cog(Chat(bot_client))
