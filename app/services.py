import asyncio
import os
import logging
import hashlib
from typing import Dict, Any, List

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv

from app.memory import MemoryStore

load_dotenv()

logger = logging.getLogger(__name__)

VAPI_API_KEY = os.getenv("VAPI_API_KEY")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID")
VAPI_BASE_URL = "https://api.vapi.ai"
SERVER_URL = os.getenv("SERVER_URL", "https://your-server.ngrok-free.app")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

BULK_BATCH_SIZE = int(os.getenv("BULK_BATCH_SIZE", "10"))
BULK_BATCH_DELAY_SECONDS = float(os.getenv("BULK_BATCH_DELAY_SECONDS", "2"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "10"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))

TTS_CACHE_DIR = os.path.join(os.getcwd(), "cache", "tts")
os.makedirs(TTS_CACHE_DIR, exist_ok=True)

def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

async def get_or_create_tts_audio(text: str, voice_id: str = ELEVENLABS_VOICE_ID) -> str:
    """Returns a local path to cached TTS audio. Generates via ElevenLabs if missing."""
    if not ELEVENLABS_API_KEY:
        logger.warning("No ElevenLabs API Key found. Skipping TTS cache.")
        return ""

    h = _hash_text(text + voice_id)
    filename = f"{h}.mp3"
    filepath = os.path.join(TTS_CACHE_DIR, filename)
    
    if os.path.exists(filepath):
        return filepath

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {"text": text, "model_id": "eleven_multilingual_v2"}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            with open(filepath, "wb") as f:
                f.write(resp.content)
            return filepath
        logger.error(f"TTS generation failed: {resp.text}")
        return ""

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), retry=retry_if_exception_type(httpx.RequestError))
async def _post_to_vapi(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.post(
            f"{VAPI_BASE_URL}/call", 
            json=payload,
            headers={"Authorization": f"Bearer {VAPI_API_KEY}", "Content-Type": "application/json"}
        )
        r.raise_for_status()
        return r.json()

def build_system_prompt(user_id: str, user_data: Dict[str, Any], construction_data: Dict[str, Any], previous_context: str) -> str:
    lang = user_data.get("language", "en")  
    language_instruction = "The customer prefers Hindi. Greet and converse primarily in natural Hindi/Hinglish." if lang == "hi" else "The customer prefers English. Converse in clear, friendly English."
    visit_info = f"Site visits available: {construction_data['site_visit_timings']}" if construction_data.get("site_visit_available") else "Site visits are currently not available."

    state = MemoryStore.get_user_state(user_id)
    stage = state.get("conversation_stage", "initial_update")
    visit_interest = state.get("visit_interest", "Unknown")

    return f"""You are Aditya, a friendly AI calling assistant for Riverwood Projects LLP.
## Call Context
Customer: {user_data['name']} | Project: {user_data['project']} | {language_instruction}
Conversation Stage: {stage} | Visit Interest: {visit_interest}
## Latest Update
Phase: {construction_data['current_phase']} | Progress: {construction_data['completion_percentage']}% | {visit_info}
## Previous Interaction
{previous_context}
## Flow
1. Greet warmly. Wait for response.
2. Share the construction update conversationally.
3. Ask if they want to visit the site.
4. Call `record_site_visit` tool based on their answer. End call naturally."""

def build_first_message(user_data: Dict[str, Any]) -> str:
    first_name = user_data["name"].split()[0]
    if user_data.get("language") == "hi":
        return f"Namaste! Kya main {first_name} ji se baat kar raha hoon? Main Aditya hoon Riverwood Projects se."
    return f"Hi {first_name}! I'm Aditya, calling from Riverwood Projects. Am I speaking with {user_data['name']}?"

async def trigger_outbound_call(user_id: str) -> Dict[str, Any]:
    user_data = MemoryStore.get_user(user_id)
    if not user_data: raise ValueError(f"User {user_id} not found")
    construction_data = MemoryStore.get_construction_update(user_data["project"])
    previous_context = MemoryStore.get_previous_context(user_id)

    first_msg_text = build_first_message(user_data)
    webhook_url = f"{SERVER_URL}/api/webhook"
    
    # Optional: Cache the first message to reduce latency
    cached_audio_path = await get_or_create_tts_audio(first_msg_text)
    
    assistant_config = {
        "name": f"Riverwood Agent - {user_data['name']}",
        "firstMessage": first_msg_text,
        "model": {
            "provider": "openai",
            "model": "gpt-4o",
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
                                "notes": {"type": "string"}
                            },
                            "required": ["wants_to_visit"],
                        },
                    },
                    "async": False,
                    "server": {"url": webhook_url},
                }
            ],
        },
        "voice": {
            "provider": "11labs",
            "voiceId": ELEVENLABS_VOICE_ID,
        },
        "serverUrl": webhook_url,
        "endCallFunctionEnabled": True,
    }

    payload = {
        "assistant": assistant_config,
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "customer": {"number": user_data["phone"], "name": user_data["name"]},
    }

    call_data = await _post_to_vapi(payload)
    call_id = call_data.get("id")
    if call_id: MemoryStore.register_call(call_id, user_id)
    return call_data

async def _call_worker(user_id: str, semaphore: asyncio.Semaphore) -> Dict[str, Any]:
    async with semaphore:
        try:
            result = await trigger_outbound_call(user_id)
            return {"user_id": user_id, "status": "initiated", "call_id": result.get("id")}
        except Exception as e:
            logger.error(f"Failed call for {user_id}: {e}")
            return {"user_id": user_id, "status": "failed", "error": str(e)}

async def trigger_bulk_calls(user_ids: List[str]) -> List[Dict[str, Any]]:
    results = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    for i in range(0, len(user_ids), BULK_BATCH_SIZE):
        batch = user_ids[i : i + BULK_BATCH_SIZE]
        logger.info(f"Processing batch of {len(batch)} calls...")
        
        tasks = [asyncio.create_task(_call_worker(uid, semaphore)) for uid in batch]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        
        if i + BULK_BATCH_SIZE < len(user_ids):
            await asyncio.sleep(BULK_BATCH_DELAY_SECONDS)

    return results