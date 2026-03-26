"""
telephony.py
------------
Outbound call orchestration.

Primary path  : VAPI  (WebRTC edge + ElevenLabs Flash TTS + backchanneling)
Fallback path : Twilio TwiML  (raw PSTN, gTTS — used only if VAPI key is absent)
Simulation    : SIMULATE_TELEPHONY=true skips all external calls (local dev)

VAPI handles:
  • PSTN routing via Twilio SIP trunk
  • ElevenLabs real-time TTS (eleven_flash_v2_5 — lowest latency)
  • Backchanneling ("mhm", "yeah") while the customer speaks
  • Barge-in / interruption detection
  • Endpointing (knows when the customer has finished their sentence)
"""

import os
import httpx
from dotenv import load_dotenv
from twilio.rest import Client as TwilioClient

load_dotenv()

VAPI_API_URL = "https://api.vapi.ai/call"

# ElevenLabs voice tuned for warm Indian English female voice.
# Override via ELEVEN_LABS_VOICE_ID in .env.
# Default: "Shreya" — an Indian-accent English voice on ElevenLabs.
# If you have a custom cloned voice, put its ID here.
DEFAULT_VOICE_ID = os.getenv("ELEVEN_LABS_VOICE_ID", "ThT5KcBeYPX3keUQqHPh")


# ── VAPI outbound call ────────────────────────────────────────────────────────

async def place_vapi_call(
    user_phone: str,
    user_id: str,
    first_message: str,
    ngrok_url: str
) -> tuple[str, str]:
    """
    Create an outbound VAPI call with:
      - Custom LLM webhook pointing to our /api/vapi-webhook
      - ElevenLabs Flash TTS voice
      - Backchanneling enabled
      - user_id in assistant metadata (so our webhook knows who's calling)

    Returns (call_id, status).
    """
    vapi_key            = os.getenv("VAPI_API_KEY")
    twilio_account_sid  = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_auth_token   = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_from_number  = os.getenv("TWILIO_PHONE_NUMBER")

    payload = {
        "assistant": {
            "name": "Ravi",

            # ── Custom LLM: points to our FastAPI streaming webhook ──────────
            "model": {
                "provider":    "custom-llm",
                "url":         f"{ngrok_url}/api/vapi-webhook",
                "model":       "gpt-4o-mini",     # informational — we control the model
                "temperature": 0.75,
                "maxTokens":   120
            },

            # ── ElevenLabs Flash voice (lowest latency TTS) ──────────────────
            "voice": {
                "provider":        "11labs",
                "voiceId":         DEFAULT_VOICE_ID,
                "model":           "eleven_flash_v2_5",  # ~75ms TTS latency
                "stability":       0.45,     # slight natural variation
                "similarityBoost": 0.80,
                "useSpeakerBoost": True
            },

            # ── Call behaviour ───────────────────────────────────────────────
            "firstMessage":           first_message,
            "firstMessageMode":       "assistant-speaks-first",
            "backchannelingEnabled":  True,        # "mhm", "yeah" while customer speaks
            "backgroundSound":        "off",
            "endCallFunctionEnabled": True,
            "endCallPhrases": [
                "goodbye", "bye", "namaste", "shukriya", "dhanyavaad",
                "alvida", "have a wonderful day", "aapka din shubh ho"
            ],

            # ── Webhook events we want VAPI to send us ───────────────────────
            "server": {
                "url": f"{ngrok_url}/api/vapi-webhook"
            },
            "serverMessages": [
                "conversation-update",
                "end-of-call-report",
                "status-update"
            ],

            # ── Metadata passed to every webhook event ────────────────────────
            "metadata": {
                "user_id": user_id
            }
        },

        # ── Inline Twilio credentials — no VAPI dashboard setup required ─────
        # VAPI uses these to provision the outbound call directly via Twilio SIP.
        "phoneNumber": {
            "twilioPhoneNumber": twilio_from_number,
            "twilioAccountSid":  twilio_account_sid,
            "twilioAuthToken":   twilio_auth_token
        },

        "customer": {
            "number": user_phone,
            "name":   user_id       # appears in VAPI dashboard logs
        }
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            VAPI_API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {vapi_key}",
                "Content-Type":  "application/json"
            }
        )
        if resp.status_code != 201:
            print(f"[VAPI] Call creation failed: {resp.status_code} — {resp.text}")
            resp.raise_for_status()

        data = resp.json()
        print(f"[VAPI] Call created → id={data.get('id')}, status={data.get('status')}")
        return data.get("id", "unknown"), data.get("status", "queued")


# ── Twilio fallback ───────────────────────────────────────────────────────────

def place_twilio_call(audio_filename: str, user_id: str) -> tuple[str, str]:
    """
    Legacy Twilio path — used when VAPI key is absent.
    Plays a pre-generated gTTS file and gathers speech via <Gather>.
    """
    TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
    MY_PHONE_NUMBER     = os.getenv("MY_PHONE_NUMBER")
    NGROK_URL           = os.getenv("NGROK_URL", os.getenv("BASE_URL"))

    client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    base_filename = os.path.basename(audio_filename)
    audio_url     = f"{NGROK_URL}/static/{base_filename}"
    process_url   = f"{NGROK_URL}/api/process?user_id={user_id}"

    twiml = f"""<Response>
    <Gather input="speech" action="{process_url}" method="POST"
            speechTimeout="auto" speechModel="phone_call" language="en-IN">
        <Play>{audio_url}</Play>
    </Gather>
</Response>"""

    call = client.calls.create(
        to=MY_PHONE_NUMBER,
        from_=TWILIO_PHONE_NUMBER,
        twiml=twiml
    )
    return call.sid, call.status


# ── Unified entry point ───────────────────────────────────────────────────────

async def place_call(
    user_phone: str,
    user_id: str,
    first_message: str,
    audio_filename: str = ""
) -> tuple[str, str]:
    """
    Smart dispatcher:
      SIMULATE_TELEPHONY=true  → no-op (local dev)
      VAPI_API_KEY present      → VAPI call (primary)
      Else                      → Twilio fallback
    """
    SIMULATE  = os.getenv("SIMULATE_TELEPHONY", "true").lower() == "true"
    VAPI_KEY  = os.getenv("VAPI_API_KEY")
    NGROK_URL = os.getenv("NGROK_URL", os.getenv("BASE_URL", "http://localhost:8000"))

    if SIMULATE:
        print(f"[Telephony] SIMULATED — user_id={user_id}, message='{first_message}'")
        return "simulated_call_id", "simulated"

    if VAPI_KEY:
        return await place_vapi_call(user_phone, user_id, first_message, NGROK_URL)

    # Twilio fallback (requires audio_filename)
    if audio_filename:
        return place_twilio_call(audio_filename, user_id)

    raise RuntimeError("No telephony provider configured. Set VAPI_API_KEY or TWILIO credentials.")


# ── Keep legacy function name for backward compatibility ──────────────────────

async def place_interactive_call(audio_filename: str, user_id: str) -> tuple[str, str]:
    """Backward-compat wrapper — called from old agent.py trigger paths."""
    user_phone = os.getenv("MY_PHONE_NUMBER", "")
    return await place_call(user_phone, user_id, "", audio_filename)