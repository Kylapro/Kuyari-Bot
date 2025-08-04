import asyncio
from base64 import b64decode
from dataclasses import dataclass, field
import logging
from typing import Any, Literal, Optional

import discord
from discord.ext import commands
import httpx
import yaml

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
curr_model = next(iter(config["models"]))
curr_engine = next(iter(config["engines"]))

msg_nodes = {}
last_task_time = 0

intents = discord.Intents.default()
intents.message_content = True
activity = discord.CustomActivity(name=(config["status_message"] or "github.com/jakobdylanc/llmcord")[:128])
discord_bot = commands.Bot(intents=intents, activity=activity, command_prefix=None)

httpx_client = httpx.AsyncClient(timeout=120.0)


async def google_image_search(query: str) -> Optional[str]:
    """Return the first image URL from Google Custom Search."""
    google_key = config.get("google_api_key")
    google_cx = config.get("google_cse_id")

    if not google_key or not google_cx:
        return None

    try:
        resp = await httpx_client.get(
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
    provider_config = config["providers"].get("stable_diffusion", {})
    api_key = provider_config.get("api_key")
    base_url = provider_config.get("base_url")
    if not api_key or not base_url:
        raise RuntimeError("Image generation is not configured.")
    engine_path = config["engines"].get(curr_engine)
    if not engine_path:
        raise RuntimeError("No engine configured.")

    payload: dict[str, Any] = {"text_prompts": [{"text": prompt}]}
    decoder = lambda data: b64decode(data["artifacts"][0]["base64"])
    req_kwargs: dict[str, Any] = {"json": payload}
    if engine_path.startswith("/v2beta"):
        payload = {"prompt": prompt, "mode": "text-to-image", "aspect_ratio": "1:1"}
        decoder = lambda data: b64decode(data["image"])
        req_kwargs = {"files": {k: (None, v) for k, v in payload.items()}}

    resp = await httpx_client.post(
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
    provider_config = config["providers"].get("stable_diffusion", {})
    api_key = provider_config.get("api_key")
    base_url = provider_config.get("base_url")
    if not api_key or not base_url:
        raise RuntimeError("Music generation is not configured.")

    # Stable Audio accepts multipart/form-data with at least one file field
    data = {"prompt": prompt, "duration": str(duration), "model": "stable-audio-2.5"}
    files = {"none": ""}

    resp = await httpx_client.post(
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
