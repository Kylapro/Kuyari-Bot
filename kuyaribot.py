import asyncio
from base64 import b64encode, b64decode
from dataclasses import dataclass, field
from datetime import datetime
import logging
import re
from typing import Any, Literal, Optional

from io import BytesIO
import random
import discord
from discord.ext import commands
import httpx
from openai import AsyncOpenAI
import yaml

from cogs.config import ConfigCog
from cogs.media import MediaCog
from cogs.music import MusicCog
from cogs.emojis import EmojiCog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

VISION_MODEL_TAGS = ("claude", "gemini", "gemma", "gpt-4", "grok-4", "llama", "llava", "mistral", "o3", "o4", "vision", "vl")
PROVIDERS_SUPPORTING_USERNAMES = ("openai", "x-ai")

EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()

STREAMING_INDICATOR = " ⚪"
EDIT_DELAY_SECONDS = 1

MAX_MESSAGE_NODES = 500


def get_config(filename: str = "config.yaml") -> dict[str, Any]:
    with open(filename, encoding="utf-8") as file:
        return yaml.safe_load(file)


config = get_config()

msg_nodes = {}
last_task_time = 0

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
activity = discord.CustomActivity(name=(config["status_message"] or "github.com/kylapro/Kuyari-Bot")[:128])
discord_bot = commands.Bot(intents=intents, activity=activity, command_prefix=None)

discord_bot.config = config
discord_bot.curr_model = next(iter(config["models"]))
discord_bot.curr_engine = next(iter(config["engines"]))

httpx_client = httpx.AsyncClient(timeout=120.0)
discord_bot.httpx_client = httpx_client
discord_bot.get_config = get_config


async def google_image_search(query: str) -> Optional[str]:
    """Return the first image URL from Google Custom Search."""
    google_key = discord_bot.config.get("google_api_key")
    google_cx = discord_bot.config.get("google_cse_id")

    if not google_key or not google_cx:
        return None

    try:
        resp = await discord_bot.httpx_client.get(
            "https://www.googleapis.com/customsearch/v1",
            params=dict(q=query, searchType="image", num=1, key=google_key, cx=google_cx),
        )
        data = resp.json()
        items = data.get("items") or []
        return items[0]["link"] if items else None
    except Exception:
        logging.exception("Error searching Google Images")
        return None


async def generate_image_bytes(prompt: str) -> bytes:
    provider_config = discord_bot.config["providers"].get("stable_diffusion", {})
    api_key = provider_config.get("api_key")
    base_url = provider_config.get("base_url")
    if not api_key or not base_url:
        raise RuntimeError("Image generation is not configured.")
    engine_path = discord_bot.config["engines"].get(discord_bot.curr_engine)
    if not engine_path:
        raise RuntimeError("No engine configured.")

    payload: dict[str, Any] = {"text_prompts": [{"text": prompt}]}
    decoder = lambda data: b64decode(data["artifacts"][0]["base64"])
    req_kwargs: dict[str, Any] = {"json": payload}
    if engine_path.startswith("/v2beta"):
        payload = {"prompt": prompt, "mode": "text-to-image", "aspect_ratio": "1:1"}
        decoder = lambda data: b64decode(data["image"])
        req_kwargs = {"files": {k: (None, v) for k, v in payload.items()}}

    resp = await discord_bot.httpx_client.post(
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


async def generate_music_bytes(prompt: str, *, duration: int = 20) -> bytes:
    """Generate music using the Stability Audio API."""
    provider_config = discord_bot.config["providers"].get("stable_diffusion", {})
    api_key = provider_config.get("api_key")
    base_url = provider_config.get("base_url")
    if not api_key or not base_url:
        raise RuntimeError("Music generation is not configured.")

    # Stable Audio accepts multipart/form-data with at least one file field
    data = {"prompt": prompt, "duration": str(duration), "model": "stable-audio-2.5"}
    files = {"none": ""}

    resp = await discord_bot.httpx_client.post(
        f"{base_url}/v2beta/audio/stable-audio-2/text-to-audio",
        headers={"Authorization": f"Bearer {api_key}", "accept": "audio/*"},
        data=data,
        files=files,
    )
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        logging.error("Stable Audio API error %s: %s", resp.status_code, resp.text)
        raise
    return resp.content


GENERATE_MUSIC_PATTERNS = [
    re.compile(
        r"(?:generate|create|make|compose|write|produce).*?(?:music|song|audio) ?(?:about|of|on|for)? (?P<query>.+)",
        re.I,
    ),
    re.compile(r"^(?:music|song|audio)[: ]+(?P<query>.+)", re.I),
]


async def maybe_handle_music_request(msg: discord.Message) -> bool:
    """Check if message requests music and respond with an audio clip if so."""
    if msg.content.startswith("/"):
        return False

    for pattern in GENERATE_MUSIC_PATTERNS:
        if match := pattern.search(msg.content):
            prompt = match.group("query").strip()
            try:
                audio_bytes = await generate_music_bytes(prompt)
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


async def maybe_handle_image_request(msg: discord.Message) -> bool:
    """Check if message requests an image and respond with one if so."""
    if msg.content.startswith("/"):
        return False

    for pattern in GENERATE_IMAGE_PATTERNS:
        if match := pattern.search(msg.content):
            prompt = match.group("query").strip()
            try:
                image_bytes = await generate_image_bytes(prompt)
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
            url = await google_image_search(query)
            if url:
                embed = discord.Embed(title=query)
                embed.set_image(url=url)
                await msg.reply(embed=embed)
            else:
                await msg.reply("No images found." if config.get("google_api_key") and config.get("google_cse_id") else "Google search is not configured.")
            return True

    return False


@dataclass
class MsgNode:
    text: Optional[str] = None
    images: list[dict[str, Any]] = field(default_factory=list)

    role: Literal["user", "assistant"] = "assistant"
    user_id: Optional[int] = None

    has_bad_attachments: bool = False
    fetch_parent_failed: bool = False

    parent_msg: Optional[discord.Message] = None

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)






@discord_bot.event
async def on_ready() -> None:
    if client_id := config["client_id"]:
        logging.info(f"\n\nBOT INVITE URL:\nhttps://discord.com/oauth2/authorize?client_id={client_id}&permissions=412317273088&scope=bot\n")

    await discord_bot.tree.sync()


@discord_bot.event
async def on_message(new_msg: discord.Message) -> None:
    global last_task_time

    is_dm = new_msg.channel.type == discord.ChannelType.private

    if new_msg.author.bot:
        return

    should_respond_passively = False
    if not is_dm and discord_bot.user not in new_msg.mentions:
        config = await asyncio.to_thread(get_config)
        discord_bot.config = config
        allow_passive = config.get("allow_passive_chat", False)
        chance = config.get("passive_chat_probability", 0.0)

        if allow_passive and random.random() < chance:
            should_respond_passively = True

        if not should_respond_passively:
            return

    role_ids = set(role.id for role in getattr(new_msg.author, "roles", ()))
    channel_ids = set(filter(None, (new_msg.channel.id, getattr(new_msg.channel, "parent_id", None), getattr(new_msg.channel, "category_id", None))))

    config = await asyncio.to_thread(get_config)
    discord_bot.config = config

    allow_dms = config.get("allow_dms", True)

    permissions = config["permissions"]

    user_is_admin = new_msg.author.id in permissions["users"]["admin_ids"]

    (allowed_user_ids, blocked_user_ids), (allowed_role_ids, blocked_role_ids), (allowed_channel_ids, blocked_channel_ids) = (
        (perm["allowed_ids"], perm["blocked_ids"]) for perm in (permissions["users"], permissions["roles"], permissions["channels"])
    )

    allow_all_users = not allowed_user_ids if is_dm else not allowed_user_ids and not allowed_role_ids
    is_good_user = user_is_admin or allow_all_users or new_msg.author.id in allowed_user_ids or any(id in allowed_role_ids for id in role_ids)
    is_bad_user = not is_good_user or new_msg.author.id in blocked_user_ids or any(id in blocked_role_ids for id in role_ids)

    allow_all_channels = not allowed_channel_ids
    is_good_channel = user_is_admin or allow_dms if is_dm else allow_all_channels or any(id in allowed_channel_ids for id in channel_ids)
    is_bad_channel = not is_good_channel or any(id in blocked_channel_ids for id in channel_ids)

    if is_bad_user or is_bad_channel:
        return

    if await maybe_handle_music_request(new_msg) or await maybe_handle_image_request(new_msg):
        return

    provider_slash_model = discord_bot.curr_model
    provider, model = provider_slash_model.removesuffix(":vision").split("/", 1)

    provider_config = config["providers"][provider]

    base_url = provider_config["base_url"]
    api_key = provider_config.get("api_key", "sk-no-key-required")
    openai_client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    model_parameters = config["models"].get(provider_slash_model, None)

    extra_headers = provider_config.get("extra_headers", None)
    extra_query = provider_config.get("extra_query", None)
    extra_body = (provider_config.get("extra_body", None) or {}) | (model_parameters or {}) or None

    reasoning_config = config.get("reasoning")
    if reasoning_config:
        extra_body = {**(extra_body or {}), "reasoning": reasoning_config}

    accept_images = any(x in provider_slash_model.lower() for x in VISION_MODEL_TAGS)
    accept_usernames = any(x in provider_slash_model.lower() for x in PROVIDERS_SUPPORTING_USERNAMES)

    max_text = config.get("max_text", 100000)
    max_images = config.get("max_images", 5) if accept_images else 0
    max_messages = config.get("max_messages", 25)

    # Build message chain and set user warnings
    messages = []
    user_warnings = set()
    curr_msg = new_msg

    while curr_msg != None and len(messages) < max_messages:
        curr_node = msg_nodes.setdefault(curr_msg.id, MsgNode())

        async with curr_node.lock:
            if curr_node.text == None:
                cleaned_content = curr_msg.content.removeprefix(discord_bot.user.mention).lstrip()

                good_attachments = [
                    att
                    for att in curr_msg.attachments
                    if att.content_type and any(att.content_type.startswith(x) for x in ("text", "image"))
                ]

                attachment_responses = await asyncio.gather(
                    *[httpx_client.get(att.url) for att in good_attachments]
                )

                embed_urls = []
                for embed in curr_msg.embeds:
                    urls = [
                        getattr(embed, "url", None),
                        getattr(getattr(embed, "image", None), "url", None),
                        getattr(getattr(embed, "thumbnail", None), "url", None),
                    ]
                    embed_urls.extend([u for u in urls if u and u.startswith("http")])

                embed_responses = await asyncio.gather(
                    *[httpx_client.get(url) for url in embed_urls]
                )

                curr_node.text = "\n".join(
                    ([cleaned_content] if cleaned_content else [])
                    + [
                        "\n".join(
                            filter(None, (embed.title, embed.description, embed.footer.text))
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
                        type="input_image",
                        image_url=dict(
                            url=f"data:{att.content_type};base64,{b64encode(resp.content).decode('utf-8')}"
                        ),
                    )
                    for att, resp in zip(good_attachments, attachment_responses)
                    if att.content_type.startswith("image")
                ] + [
                    dict(
                        type="input_image",
                        image_url=dict(
                            url=f"data:{resp.headers.get('content-type', 'image/gif')};base64,{b64encode(resp.content).decode('utf-8')}"
                        ),
                    )
                    for resp in embed_responses
                    if resp.headers.get("content-type", "").startswith("image")
                ]

                curr_node.role = "assistant" if curr_msg.author == discord_bot.user else "user"

                curr_node.user_id = curr_msg.author.id if curr_node.role == "user" else None

                curr_node.has_bad_attachments = len(curr_msg.attachments) > len(good_attachments)

                try:
                    if (
                        curr_msg.reference == None
                        and discord_bot.user.mention not in curr_msg.content
                        and (prev_msg_in_channel := ([m async for m in curr_msg.channel.history(before=curr_msg, limit=1)] or [None])[0])
                        and prev_msg_in_channel.type in (discord.MessageType.default, discord.MessageType.reply)
                        and prev_msg_in_channel.author == (discord_bot.user if curr_msg.channel.type == discord.ChannelType.private else curr_msg.author)
                    ):
                        curr_node.parent_msg = prev_msg_in_channel
                    else:
                        is_public_thread = curr_msg.channel.type == discord.ChannelType.public_thread
                        parent_is_thread_start = is_public_thread and curr_msg.reference == None and curr_msg.channel.parent.type == discord.ChannelType.text

                        if parent_msg_id := curr_msg.channel.id if parent_is_thread_start else getattr(curr_msg.reference, "message_id", None):
                            if parent_is_thread_start:
                                curr_node.parent_msg = curr_msg.channel.starter_message or await curr_msg.channel.parent.fetch_message(parent_msg_id)
                            else:
                                curr_node.parent_msg = curr_msg.reference.cached_message or await curr_msg.channel.fetch_message(parent_msg_id)

                except (discord.NotFound, discord.HTTPException):
                    logging.exception("Error fetching next message in the chain")
                    curr_node.fetch_parent_failed = True

            content = []
            if curr_node.text[:max_text]:
                content.append(dict(type="input_text", text=curr_node.text[:max_text]))
            content.extend(curr_node.images[:max_images])

            if content:
                message = dict(content=content, role=curr_node.role)
                if accept_usernames and curr_node.user_id != None:
                    message["name"] = str(curr_node.user_id)

                messages.append(message)

            if len(curr_node.text) > max_text:
                user_warnings.add(f"⚠️ Max {max_text:,} characters per message")
            if len(curr_node.images) > max_images:
                user_warnings.add(f"⚠️ Max {max_images} image{'' if max_images == 1 else 's'} per message" if max_images > 0 else "⚠️ Can't see images")
            if curr_node.has_bad_attachments:
                user_warnings.add("⚠️ Unsupported attachments")
            if curr_node.fetch_parent_failed or (curr_node.parent_msg != None and len(messages) == max_messages):
                user_warnings.add(f"⚠️ Only using last {len(messages)} message{'' if len(messages) == 1 else 's'}")

            curr_msg = curr_node.parent_msg

    logging.info(f"Message received (user ID: {new_msg.author.id}, attachments: {len(new_msg.attachments)}, conversation length: {len(messages)}):\n{new_msg.content}")

    if system_prompt := config["system_prompt"]:
        now = datetime.now().astimezone()

        system_prompt = system_prompt.replace("{date}", now.strftime("%B %d %Y")).replace("{time}", now.strftime("%H:%M:%S %Z%z")).strip()
        if accept_usernames:
            system_prompt += "\nUser's names are their Discord IDs and should be typed as '<@ID>'."

        messages.append(dict(role="system", content=[{"type": "input_text", "text": system_prompt}]))

    reasoning_instruction = "Please reason step by step before giving the final answer."
    for msg in reversed(messages):
        if msg.get("role") == "user":
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "input_text":
                    part["text"] += "\n\n" + reasoning_instruction
                    break
            else:
                msg["content"].insert(0, {"type": "input_text", "text": reasoning_instruction})
            break

    # Generate and send response message(s) (can be multiple if response is long)
    curr_content = curr_reasoning = finish_reason = edit_task = None
    response_msgs = []
    response_contents = []
    reasoning_contents = ""

    embed = discord.Embed()
    for warning in sorted(user_warnings):
        embed.add_field(name=warning, value="", inline=False)

    use_plain_responses = config.get("use_plain_responses", False)
    max_message_length = 2000 if use_plain_responses else (4096 - len(STREAMING_INDICATOR))
    kwargs = dict(model=model, messages=messages[::-1], stream=True, extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body)
    try:
        async with new_msg.channel.typing():
            async for chunk in await openai_client.chat.completions.create(**kwargs):
                if finish_reason != None:
                    break

                if not (choice := chunk.choices[0] if chunk.choices else None):
                    continue

                finish_reason = choice.finish_reason

                prev_content = curr_content or ""
                curr_content = choice.delta.content or ""
                curr_reasoning = getattr(choice.delta, "reasoning", "") or ""

                new_content = prev_content if finish_reason == None else (prev_content + curr_content)

                if response_contents == [] and new_content == "":
                    continue

                if start_next_msg := response_contents == [] or len(response_contents[-1] + new_content) > max_message_length:
                    response_contents.append("")

                response_contents[-1] += new_content
                if curr_reasoning:
                    reasoning_contents += curr_reasoning

                if not use_plain_responses:
                    ready_to_edit = (edit_task == None or edit_task.done()) and datetime.now().timestamp() - last_task_time >= EDIT_DELAY_SECONDS
                    msg_split_incoming = finish_reason == None and len(response_contents[-1] + curr_content) > max_message_length
                    is_final_edit = finish_reason != None or msg_split_incoming
                    is_good_finish = finish_reason != None and finish_reason.lower() in ("stop", "end_turn")

                    if start_next_msg or ready_to_edit or is_final_edit:
                        if edit_task != None:
                            await edit_task

                        embed.description = response_contents[-1] if is_final_edit else (response_contents[-1] + STREAMING_INDICATOR)
                        embed.color = EMBED_COLOR_COMPLETE if msg_split_incoming or is_good_finish else EMBED_COLOR_INCOMPLETE

                        if start_next_msg:
                            reply_to_msg = new_msg if response_msgs == [] else response_msgs[-1]
                            response_msg = await reply_to_msg.reply(embed=embed, silent=True)
                            response_msgs.append(response_msg)

                            msg_nodes[response_msg.id] = MsgNode(parent_msg=new_msg)
                            await msg_nodes[response_msg.id].lock.acquire()
                        else:
                            edit_task = asyncio.create_task(response_msgs[-1].edit(embed=embed))

                        last_task_time = datetime.now().timestamp()

            if reasoning_contents:
                response_contents.append(f"Reasoning:\n{reasoning_contents}")
                if not use_plain_responses:
                    embed.add_field(name="Reasoning", value=reasoning_contents, inline=False)
                    if edit_task != None:
                        await edit_task
                    if response_msgs:
                        await response_msgs[-1].edit(embed=embed)

            if use_plain_responses:
                for content in response_contents:
                    reply_to_msg = new_msg if response_msgs == [] else response_msgs[-1]
                    response_msg = await reply_to_msg.reply(content=content, suppress_embeds=True)
                    response_msgs.append(response_msg)

                    msg_nodes[response_msg.id] = MsgNode(parent_msg=new_msg)
                    await msg_nodes[response_msg.id].lock.acquire()

    except Exception:
        logging.exception("Error while generating response")

    for response_msg in response_msgs:
        msg_nodes[response_msg.id].text = "".join(response_contents)
        msg_nodes[response_msg.id].lock.release()

    # Delete oldest MsgNodes (lowest message IDs) from the cache
    if (num_nodes := len(msg_nodes)) > MAX_MESSAGE_NODES:
        for msg_id in sorted(msg_nodes.keys())[: num_nodes - MAX_MESSAGE_NODES]:
            async with msg_nodes.setdefault(msg_id, MsgNode()).lock:
                msg_nodes.pop(msg_id, None)


async def main() -> None:
    discord_bot.generate_image_bytes = generate_image_bytes
    discord_bot.generate_music_bytes = generate_music_bytes
    await discord_bot.add_cog(ConfigCog(discord_bot))
    await discord_bot.add_cog(MediaCog(discord_bot))
    await discord_bot.add_cog(MusicCog(discord_bot))
    await discord_bot.add_cog(EmojiCog(discord_bot))
    await discord_bot.start(discord_bot.config["bot_token"])


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
