"""
agent.py
--------
Core orchestration layer.

Responsibilities:
  1. trigger_outbound_call(user_id) — the entry point from /trigger/{user_id}
       - Loads user + construction data from DB
       - Builds the personalised first greeting
       - Warms up Redis with any prior conversation history
       - Fires the outbound call via VAPI (or Twilio fallback)

  2. process_user_speech(user_id, speech) — legacy Twilio webhook path
       - Kept for backward compatibility with the /api/process route
       - For new calls routed through VAPI, conversation handling
         is done entirely inside vapi_handler.py

The actual AI turn (LLM streaming + intent detection) lives in vapi_handler.py.
This file only owns the call initiation sequence.
"""

import os
import datetime
from db import SessionLocal, User, ConstructionUpdate, Interaction, CallLog
from llm_openai import get_response
from tts import text_to_speech, get_or_create_canned
from telephony import place_call, place_twilio_call
from memory_manager import FastMemoryManager


# ── Greeting builders ─────────────────────────────────────────────────────────

def build_first_message(user, is_returning: bool) -> str:
    """
    Crafts the very first sentence Ravi says.
    Personalised on: name, returning status, CRM site_visit_interest flag, language.
    This is pre-generated before the call connects so there is ZERO audio delay
    when the customer picks up.
    """
    first_name = user.name.split()[0]

    if is_returning:
        if user.site_visit_interest:
            if user.language == "hi":
                return (
                    f"Namaste {first_name} ji! Main Ravi bol rahi hoon Riverwood se. "
                    f"Aapne pichli baar site visit mein interest dikhaya tha — "
                    f"kya aap abhi bhi plan kar rahe hain?"
                )
            return (
                f"Hi {first_name}! It's Ravi from Riverwood again. "
                f"I know you'd expressed interest in a site visit — are you still planning to come over?"
            )
        else:
            if user.language == "hi":
                return (
                    f"Namaste {first_name} ji! Main Ravi bol rahi hoon Riverwood se. "
                    f"Aapke project ke kuch naye updates hain — kya aap abhi baat kar sakte hain?"
                )
            return (
                f"Hi {first_name}! It's Ravi from Riverwood. "
                f"I have a quick construction update for you — do you have just a minute?"
            )
    else:
        if user.language == "hi":
            return (
                f"Namaste! Kya main {first_name} ji se baat kar rahi hoon? "
                f"Main Ravi hoon Riverwood Projects se — aapke {user.project} ke baare mein call kar rahi hoon."
            )
        return (
            f"Hi, am I speaking with {first_name}? "
            f"This is Ravi calling from Riverwood Projects "
            f"regarding your property at {user.project}."
        )


# ── Outbound call trigger ─────────────────────────────────────────────────────

async def trigger_outbound_call(user_id: str) -> dict:
    """
    Main entry point — called by POST /trigger/{user_id}.

    Steps:
      1. Validate user exists in DB
      2. Determine if returning customer (hot memory or call log)
      3. Build personalised first message
      4. Warm Redis cache with prior history
      5. Fire outbound call via VAPI (or fallback)
      6. Log the initiated call
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError(
                f"User '{user_id}' not found. "
                f"Run seed_db.py to populate the database."
            )

        construction = db.query(ConstructionUpdate).filter(
            ConstructionUpdate.project == user.project
        ).first()
        if not construction:
            raise ValueError(
                f"No construction update found for project '{user.project}'."
            )

        # ── Determine returning status ────────────────────────────────────
        memory       = FastMemoryManager(user_id)
        is_returning = memory.is_returning_user()

        # ── Build greeting ────────────────────────────────────────────────
        first_message = build_first_message(user, is_returning)
        print(f"[Agent] First message for {user_id}: {first_message}")

        # ── Warm up Redis with cold history so first LLM turn is instant ──
        # This pre-loads any prior conversation into Redis before VAPI calls
        # our webhook, eliminating the cold-hydration latency mid-call.
        if is_returning:
            memory.get_recent_context()   # triggers _hydrate_from_cold() if needed

        # ── Store the greeting in hot memory ──────────────────────────────
        memory.add_message("assistant", first_message)

        # ── Fire the call ─────────────────────────────────────────────────
        call_sid, call_status = await place_call(
            user_phone    = user.phone,
            user_id       = user_id,
            first_message = first_message
        )

        # ── Log the initiated call ────────────────────────────────────────
        db.add(CallLog(
            user_id    = user_id,
            status     = call_status,
            created_at = datetime.datetime.utcnow()
        ))
        db.commit()

        return {
            "user_id":  user_id,
            "call_id":  call_sid,
            "status":   call_status,
            "message":  "Outbound call initiated via VAPI"
        }

    finally:
        db.close()


# ── Legacy Twilio speech processing (fallback path) ───────────────────────────

async def process_user_speech(user_id: str, user_speech: str):
    """
    Handles speech from the legacy /api/process Twilio webhook.
    VAPI calls go through vapi_handler.py instead.
    Kept so the Twilio route in main.py continues to work as a fallback.
    """
    db = SessionLocal()
    try:
        user         = db.query(User).filter(User.id == user_id).first()
        construction = db.query(ConstructionUpdate).filter(
            ConstructionUpdate.project == user.project
        ).first()

        user_lower  = user_speech.lower()
        lang_code   = "hi" if user.language == "hi" else "en"
        should_hangup = False

        busy_keywords = [
            "busy", "not right now", "call later", "wrong number",
            "not interested", "baad mein", "abhi time nahi", "stop calling"
        ]
        visit_keywords = [
            "visit", "site visit", "come to site", "dekhne aana",
            "yes", "sure", "haan", "zaroor"
        ]

        memory = FastMemoryManager(user_id)
        memory.add_message("user", user_speech)

        # Fast path — busy
        if any(k in user_lower for k in busy_keywords):
            canned_key    = f"busy_fallback_{lang_code}"
            assistant_text = (
                "I understand you're busy. I'll call you back later. Have a wonderful day!"
                if lang_code == "en"
                else "Maaf kijiye, main aapko baad mein call karungi. Aapka din shubh ho!"
            )
            audio_path = get_or_create_canned(canned_key, lang=lang_code)
            should_hangup = True
            memory.add_message("assistant", assistant_text)
            memory.commit_to_cold_storage()
            return audio_path, assistant_text, should_hangup

        # Fast path — site visit
        if any(k in user_lower for k in visit_keywords) and not user.site_visit_interest:
            user.site_visit_interest = True
            db.commit()
            canned_key    = f"visit_confirm_{lang_code}"
            assistant_text = (
                "Great! I've noted your interest in a site visit. Our team will reach out shortly. Goodbye!"
                if lang_code == "en"
                else "Bahut achha! Maine aapka site visit note kar liya. Hamari team sampark karegi. Namaste!"
            )
            audio_path = get_or_create_canned(canned_key, lang=lang_code)
            should_hangup = True
            memory.add_message("assistant", assistant_text)
            memory.commit_to_cold_storage()
            return audio_path, assistant_text, should_hangup

        # Standard LLM path (non-streaming — Twilio can't handle SSE)
        from vapi_handler import build_system_prompt
        hot_context   = memory.get_recent_context()
        system_prompt = build_system_prompt(user, construction, is_returning=True)
        messages      = [{"role": "system", "content": system_prompt}] + hot_context

        assistant_text = await get_response(messages)

        lower = assistant_text.lower()
        if any(k in lower for k in ["goodbye", "bye", "namaste", "shukriya", "dhanyavaad"]):
            should_hangup = True

        memory.add_message("assistant", assistant_text)
        if should_hangup:
            memory.commit_to_cold_storage()

        audio_path = text_to_speech(assistant_text, lang=lang_code, filename_hint=user_id)
        return audio_path, assistant_text, should_hangup

    finally:
        db.close()
