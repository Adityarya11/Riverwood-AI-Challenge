"""
vapi_handler.py
---------------
VAPI Custom LLM Webhook — the brain of the call.

VAPI calls this endpoint with a conversation-update whenever the customer
finishes speaking.  We respond with a Server-Sent Events (SSE) stream in
OpenAI's streaming chat-completion format so that VAPI starts synthesising
ElevenLabs audio immediately as the first token arrives.

Timeline without streaming:   STT → wait 2s for full LLM response → TTS → audio
Timeline WITH streaming:       STT → token 1 arrives in ~200ms → TTS starts → rest of tokens arrive → seamless audio

Event types handled:
  - conversation-update   : main AI turn — returns SSE stream
  - status-update         : call lifecycle logging
  - end-of-call-report    : flush hot memory → cold DB (background task)
"""

import os
import json
import datetime
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from memory_manager import FastMemoryManager
from llm_openai import stream_response
from db import SessionLocal, User, ConstructionUpdate, CallLog

vapi_router = APIRouter()


# ── System Prompt ─────────────────────────────────────────────────────────────

def build_system_prompt(user, construction, is_returning: bool) -> str:
    """
    Injects all real-time context into the system prompt.
    The LLM never needs to hallucinate — everything it can say is here.
    """
    lang_rule = (
        "Respond in warm, natural Hindi/Hinglish. Mix Hindi and English naturally like an educated Indian."
        if user.language == "hi"
        else "Respond in warm, conversational Indian English. Sound like a real person, not a call centre script."
    )

    history_ctx = (
        "This is a RETURNING customer. Greet them warmly without re-introducing yourself from scratch."
        if is_returning
        else "This is your FIRST call to this customer. Introduce yourself briefly."
    )

    crm_ctx = (
        "⚑ CRM FLAG: This customer previously expressed interest in a site visit. "
        "Acknowledge this naturally — ask if they have questions before they arrive."
        if user.site_visit_interest
        else "You may gently suggest a site visit if the conversation flows naturally."
    )

    return f"""You are Ravi, a warm and friendly calling assistant for Riverwood Projects LLP.
You are on a LIVE PHONE CALL. This is critical: keep every reply to 1-2 SHORT sentences maximum.

{history_ctx}
{crm_ctx}

Customer: {user.name} | Project: {user.project} | Unit: {user.unit}
Payment Status: {user.payment_status}

RIVERWOOD KNOWLEDGE BASE (authoritative — do NOT make up anything outside this):
• Phase: {construction.current_phase} — {construction.completion_percentage}% complete
• Recent milestone: {construction.recent_milestone}
• Next milestone: {construction.next_milestone}
• Expected handover: {construction.expected_completion}
• Site visits: {"Available" if construction.site_visit_available else "Not currently available"} — {construction.site_visit_timings or "N/A"}

PERSONA RULES:
1. Sound human. Use natural conversational openers like "So,", "Actually,", "You know," occasionally — but never overdo it.
2. If you don't know something, say "I'll have our team get back to you on that."
3. If the customer says goodbye or wants to end, say a warm farewell.
4. {lang_rule}
5. Never read from a script. React to what the customer actually said."""


# ── Canned fast responses (zero LLM cost, zero latency) ──────────────────────

CANNED = {
    "busy_en":   "Of course, I completely understand! I'll give you a call at a better time. Have a wonderful day!",
    "busy_hi":   "Bilkul samajh gaya! Main aapko baad mein call karungi. Aapka din shubh ho!",
    "visit_en":  "That's great to hear! I've noted your interest in a site visit. Our team will reach out shortly to confirm a time. Goodbye!",
    "visit_hi":  "Bahut achha! Maine aapka site visit note kar liya hai. Hamari team jald aapse sampark karegi. Namaste!",
}

# ── SSE helpers ───────────────────────────────────────────────────────────────

async def _sse_static(text: str):
    """Yield a single complete text block in VAPI-compatible SSE format."""
    payload = {
        "choices": [{
            "delta":         {"content": text},
            "finish_reason": "stop",
            "index":         0
        }]
    }
    yield f"data: {json.dumps(payload)}\n\n"
    yield "data: [DONE]\n\n"


async def _sse_stream(messages: list, memory: FastMemoryManager):
    """
    Stream OpenAI tokens in VAPI's expected SSE format.
    Each token arrives in <300ms from call start, giving ElevenLabs enough
    to start synthesising audio before the full sentence is done.
    """
    full_response = ""

    try:
        async for token in stream_response(messages):
            full_response += token
            payload = {
                "choices": [{
                    "delta":         {"content": token},
                    "finish_reason": None,
                    "index":         0
                }]
            }
            yield f"data: {json.dumps(payload)}\n\n"

        # Signal end-of-stream
        yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop', 'index': 0}]})}\n\n"
        yield "data: [DONE]\n\n"

        # Write completed response into hot memory (after yielding — non-blocking)
        if full_response.strip():
            memory.add_message("assistant", full_response.strip())

    except Exception as e:
        print(f"[VAPI Stream] Error: {e}")
        err_payload = {
            "choices": [{
                "delta":         {"content": "I'm sorry, one moment please."},
                "finish_reason": "stop",
                "index":         0
            }]
        }
        yield f"data: {json.dumps(err_payload)}\n\n"
        yield "data: [DONE]\n\n"


# ── Intent detection ──────────────────────────────────────────────────────────

BUSY_KEYWORDS = [
    "busy", "not now", "call later", "wrong number", "not interested",
    "stop calling", "don't call", "baad mein", "abhi nahi", "time nahi",
    "mat karo call", "galat number", "interested nahi"
]

VISIT_KEYWORDS = [
    "visit", "site visit", "come", "see the site", "dekhne aana",
    "site dekhna", "visit karna", "haan", "yes", "sure", "zaroor",
    "aaunga", "aaungi", "plan kar raha", "interested"
]

GOODBYE_KEYWORDS = [
    "goodbye", "bye", "see you", "take care", "namaste", "shukriya",
    "dhanyavaad", "alvida", "theek hai band karo"
]


def _extract_user_id(payload: dict) -> str:
    """Pull user_id from VAPI metadata (set when we create the call)."""
    try:
        return (
            payload.get("message", {})
            .get("call", {})
            .get("assistant", {})
            .get("metadata", {})
            .get("user_id", "user_001")
        )
    except Exception:
        return "user_001"


def _get_last_user_message(vapi_messages: list) -> str:
    """Extract the most recent user utterance from VAPI's message array."""
    for msg in reversed(vapi_messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return ""


# ── Main webhook handler ──────────────────────────────────────────────────────

@vapi_router.post("/api/vapi-webhook/chat/completions")
async def custom_llm_chat_completions(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    call_id = payload.get("call", {}).get("id", "unknown")
    user_id = payload.get("call", {}).get("assistant", {}).get("metadata", {}).get("user_id", "user_001")

    vapi_messages = payload.get("messages", [])
    last_user_msg = _get_last_user_message(vapi_messages)
    user_lower    = last_user_msg.lower()

    # ── Load user + construction data ──────────────────────────────────
    db = SessionLocal()
    try:
        user         = db.query(User).filter(User.id == user_id).first()
        construction = db.query(ConstructionUpdate).filter(
            ConstructionUpdate.project == user.project
        ).first() if user else None
    finally:
        db.close()

    if not user or not construction:
        print(f"[VAPI] User or construction data missing for {user_id}")
        return JSONResponse({"error": "User data not found"}, status_code=404)

    lang     = "hi" if user.language == "hi" else "en"
    memory   = FastMemoryManager(user_id)
    is_returning = memory.is_returning_user()

    # ── Fast-path: Busy / rejection ────────────────────────────────────
    if any(k in user_lower for k in BUSY_KEYWORDS):
        farewell = CANNED[f"busy_{lang}"]
        memory.add_message("user",      last_user_msg)
        memory.add_message("assistant", farewell)
        return StreamingResponse(_sse_static(farewell), media_type="text/event-stream")

    # ── Fast-path: Site visit confirmation ─────────────────────────────
    if any(k in user_lower for k in VISIT_KEYWORDS) and not user.site_visit_interest:
        # Update CRM flag immediately
        db2 = SessionLocal()
        try:
            u = db2.query(User).filter(User.id == user_id).first()
            if u:
                u.site_visit_interest = True
                db2.commit()
                print(f"[CRM] site_visit_interest → True for {user_id}")
        except Exception as e:
            print(f"[CRM] Update error: {e}")
        finally:
            db2.close()

        confirm = CANNED[f"visit_{lang}"]
        memory.add_message("user",      last_user_msg)
        memory.add_message("assistant", confirm)
        return StreamingResponse(_sse_static(confirm), media_type="text/event-stream")

    # ── Standard LLM path: stream the response ─────────────────────────
    # Save the user utterance to hot memory first
    if last_user_msg:
        memory.add_message("user", last_user_msg)

    # Fetch hot context for the LLM (all previous turns this call)
    hot_context = memory.get_recent_context()

    # Build the full messages array: system + conversation history
    system_prompt   = build_system_prompt(user, construction, is_returning)
    openai_messages = [{"role": "system", "content": system_prompt}] + hot_context

    return StreamingResponse(
        _sse_stream(openai_messages, memory),
        media_type="text/event-stream"
    )


@vapi_router.post("/api/vapi-webhook")
async def vapi_custom_llm_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    message = payload.get("message", {})
    msg_type = message.get("type")
    call_id  = message.get("call", {}).get("id", "unknown")
    user_id  = _extract_user_id(payload)

    # ── 1. Status updates (ringing, in-progress, ended) ──────────────────────
    if msg_type == "status-update":
        status = message.get("status", "unknown")
        print(f"[VAPI] Call {call_id} | status → {status}")
        return {"status": "acknowledged"}

    # ── 2. End-of-call report — flush hot memory → cold DB ───────────────────
    if msg_type == "end-of-call-report":
        print(f"[VAPI] Call {call_id} ended. Scheduling memory commit for {user_id}.")
        memory = FastMemoryManager(user_id)

        # Run in background so the HTTP response returns immediately to VAPI
        background_tasks.add_task(memory.commit_to_cold_storage)

        # Log the call in the call_logs table
        db = SessionLocal()
        try:
            db.add(CallLog(
                user_id=user_id,
                status="completed",
                created_at=datetime.datetime.utcnow()
            ))
            db.commit()
        except Exception as e:
            print(f"[VAPI] CallLog write error: {e}")
        finally:
            db.close()

        return {"status": "transcript_saved"}

    # ── 3. Conversation update — the core AI turn ─────────────────────────────
    if msg_type == "conversation-update":
        # Note: Streaming responses for Custom LLMs are handled via /chat/completions now.
        return {"status": "acknowledged"}

    # ── 4. Unknown event type ─────────────────────────────────────────────────
    print(f"[VAPI] Unhandled event type: {msg_type}")
    return {"status": "ignored"}
