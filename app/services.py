# app/services.py
import asyncio
import os
import logging
import hashlib
from typing import Dict, Any, List

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.memory import MemoryStore

logger = logging.getLogger(__name__)

# environment / tuning
VAPI_API_KEY = os.getenv("VAPI_API_KEY", "")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID", "")
VAPI_BASE_URL = os.getenv("VAPI_BASE_URL", "https://api.vapi.ai")
SERVER_URL = os.getenv("SERVER_URL", "https://your-server.ngrok.io")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

BULK_BATCH_SIZE = int(os.getenv("BULK_BATCH_SIZE", "10"))
BULK_BATCH_DELAY_SECONDS = float(os.getenv("BULK_BATCH_DELAY_SECONDS", "2"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "10"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))

# tts cache dir (served via /static)
TTS_CACHE_DIR = os.path.join(os.getcwd(), "cache", "tts")
os.makedirs(TTS_CACHE_DIR, exist_ok=True)


def init_tts_cache_dir(base_dir: str):
    os.makedirs(os.path.join(base_dir, "tts"), exist_ok=True)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def get_or_create_tts_audio(text: str, voice_id: str = ELEVENLABS_VOICE_ID) -> str:
    """
    Return a relative public URL to cached TTS audio (served at /static/tts/<file>).
    If ElevenLabs API key is missing, returns empty string and callers should fall back to text-firstMessage.
    """
    if not text:
        return ""

    if not ELEVENLABS_API_KEY:
        logger.warning("ELEVENLABS_API_KEY not set - skipping TTS pre-generation.")
        return ""

    h = _hash_text(text + voice_id)
    filename = f"{h}.mp3"
    filepath = os.path.join(TTS_CACHE_DIR, filename)

    # if exists, return URL
    if os.path.exists(filepath):
        return f"{SERVER_URL}/static/tts/{filename}"

    # generate via ElevenLabs TTS - use async client
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {"text": text, "model_id": "eleven_multilingual_v2"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
        except Exception as e:
            logger.exception("TTS generation failed")
            return ""

        # write file synchronously (small file)
        with open(filepath, "wb") as f:
            f.write(resp.content)
        return f"{SERVER_URL}/static/tts/{filename}"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), retry=retry_if_exception_type(httpx.RequestError))
async def _post_to_vapi(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.post(f"{VAPI_BASE_URL}/call", json=payload, headers={"Authorization": f"Bearer {VAPI_API_KEY}", "Content-Type": "application/json"})
        r.raise_for_status()
        return r.json()


def build_system_prompt(user_id: str, user_data: Dict[str, Any], construction_data: Dict[str, Any], previous_context: str) -> str:
    lang = user_data.get("language", "en")
    language_instruction = "The customer prefers Hindi. Greet and converse primarily in natural Hindi/Hinglish." if lang == "hi" else "The customer prefers English. Converse in clear, friendly English."

    state = MemoryStore.get_user_state(user_id)
    stage = state.get("conversation_stage", "initial_update")
    visit_interest = state.get("visit_interest", "Unknown")

    visit_info = f"Site visits available: {construction_data['site_visit_timings']}" if construction_data.get("site_visit_available") else "Site visits are currently not available."

    # Short and focused system prompt for prototype
    return f"""You are Aditya, a friendly AI calling assistant for Riverwood Projects LLP.
Customer: {user_data['name']} | Project: {user_data['project']}
Language instruction: {language_instruction}
Conversation Stage: {stage} | Visit Interest: {visit_interest}
Latest Update: {construction_data['current_phase']} ({construction_data['completion_percentage']}%) — {construction_data['recent_milestone']}
{visit_info}
Previous Interaction Summary:
{previous_context}

Flow:
1. Greet the customer by name.
2. Share the construction update conversationally.
3. Ask if they'd like to visit the site.
4. If yes, call the 'record_site_visit' tool with wants_to_visit=true.
5. If no, call the 'record_site_visit' tool with wants_to_visit=false.
6. Thank them and end the call.
Keep responses concise (2-3 short sentences)."""

def build_first_message(user_data: Dict[str, Any]) -> str:
    first_name = user_data["name"].split()[0]
    if user_data.get("language") == "hi":
        return f"Namaste! Kya main {first_name} ji se baat kar raha hoon? Main Aditya hoon Riverwood Projects se."
    return f"Hi {first_name}! I'm Aditya from Riverwood Projects. Am I speaking with {user_data['name']}?"


async def trigger_outbound_call(user_id: str) -> Dict[str, Any]:
    """
    Build assistant config, optionally pre-generate greeting audio, and call VAPI.
    Registers active call in DB if call id returned.
    """
    user_data = MemoryStore.get_user(user_id)
    if not user_data:
        raise ValueError(f"User {user_id} not found")

    construction_data = MemoryStore.get_construction_update(user_data["project"])
    previous_context = MemoryStore.get_previous_context(user_id)

    first_msg_text = build_first_message(user_data)
    greeting_audio_url = await get_or_create_tts_audio(first_msg_text)

    webhook_url = f"{SERVER_URL}/api/webhook"

    # assistant configuration (adjust provider if needed)
    assistant_config = {
        "name": f"Riverwood Agent - {user_data['name']}",
        "firstMessage": first_msg_text,
        "model": {
            # user can set which backend to use in provider, default left as 'openai' for VAPI compatibility
            "provider": os.getenv("LLM_PROVIDER", "elevenlabs"),
            "model": os.getenv("LLM_MODEL", "eleven_conversation"),
            "messages": [{"role": "system", "content": build_system_prompt(user_id, user_data, construction_data, previous_context)}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "record_site_visit",
                        "description": "Record if the customer wants to visit.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "wants_to_visit": {"type": "boolean"},
                                "preferred_date": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                            "required": ["wants_to_visit"],
                        },
                    },
                    "async": False,
                    "server": {"url": webhook_url},
                },
                {
                    "type": "function",
                    "function": {
                        "name": "schedule_callback",
                        "description": "Schedule callback if user is busy.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "preferred_time": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                            "required": ["preferred_time"],
                        },
                    },
                    "async": False,
                    "server": {"url": webhook_url},
                },
            ],
        },
        "voice": {"provider": "11labs", "voiceId": ELEVENLABS_VOICE_ID},
        "serverUrl": webhook_url,
        "endCallFunctionEnabled": True,
    }

    # If we generated a cached greeting audio URL, add it to payload (provider-specific; VAPI may support custom field)
    payload = {
        "assistant": assistant_config,
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "customer": {"number": user_data["phone"], "name": user_data["name"]},
    }
    if greeting_audio_url:
        # friendly optional field - many voice platforms accept a start audio or prompt URL; if not supported they'll ignore
        payload["greetingAudioUrl"] = greeting_audio_url

    call_data = await _post_to_vapi(payload)
    call_id = call_data.get("id")
    if call_id:
        MemoryStore.register_call(call_id, user_id)
    return call_data


async def _call_worker(user_id: str, semaphore: asyncio.Semaphore) -> Dict[str, Any]:
    async with semaphore:
        try:
            result = await trigger_outbound_call(user_id)
            return {"user_id": user_id, "status": "initiated", "call_id": result.get("id")}
        except Exception as e:
            logger.exception("Call failed")
            return {"user_id": user_id, "status": "failed", "error": str(e)}


async def trigger_bulk_calls(user_ids: List[str]) -> List[Dict[str, Any]]:
    """Batch + concurrency-limited bulk caller."""
    results = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    for i in range(0, len(user_ids), BULK_BATCH_SIZE):
        batch = user_ids[i : i + BULK_BATCH_SIZE]
        logger.info(f"Processing batch of {len(batch)} calls.")
        tasks = [asyncio.create_task(_call_worker(uid, semaphore)) for uid in batch]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        if i + BULK_BATCH_SIZE < len(user_ids):
            await asyncio.sleep(BULK_BATCH_DELAY_SECONDS)

    return results