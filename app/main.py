import json
import logging
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.models import (
    CallRequest,
    BulkCallRequest,
    CallResponse,
    HealthResponse,
)
from app.memory import MemoryStore
from app.services import trigger_outbound_call, trigger_bulk_calls
from app.orchestrator import find_users_needing_updates
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Riverwood AI Voice Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the static directory to serve cached TTS audio
os.makedirs(os.path.join(os.getcwd(), "cache", "tts"), exist_ok=True)
app.mount("/static", StaticFiles(directory="cache"), name="static")

def handle_record_site_visit(call_id: str, args: Dict[str, Any]) -> str:
    user_id = MemoryStore.get_user_for_call(call_id)
    if not user_id:
        logger.warning(f"No user mapped for call {call_id}")
        return "Noted. I will make sure the team follows up."

    wants_to_visit = args.get("wants_to_visit", False)
    preferred_date = args.get("preferred_date")
    notes = args.get("notes")

    MemoryStore.record_visit_intention(user_id, wants_to_visit, preferred_date, notes)
    # Update AI CRM state
    MemoryStore.update_user_state(user_id, visit_interest=wants_to_visit)
    user = MemoryStore.get_user(user_id)
    name = user["name"] if user else "the customer"

    if wants_to_visit:
        date_info = f" for {preferred_date}" if preferred_date else ""
        logger.info(f"Visit recorded: {name} wants to visit{date_info}")
        return f"Visit recorded for {name}{date_info}. The site team will be informed."

    logger.info(f"Visit recorded: {name} does not want to visit at this time")
    return f"Noted that {name} is not planning to visit right now."

def handle_schedule_callback(call_id: str, args: Dict[str, Any]) -> str:
    user_id = MemoryStore.get_user_for_call(call_id)
    if not user_id:
        return "Callback request noted."

    preferred_time = args.get("preferred_time", "later")
    notes = args.get("notes")

    MemoryStore.schedule_callback(user_id, preferred_time, notes)
    # Update AI CRM state
    MemoryStore.update_user_state(user_id, conversation_stage="callback_pending")
    logger.info(f"Callback scheduled for {user_id} at {preferred_time}")
    return f"Callback scheduled for {preferred_time}. Our team will call back."

TOOL_HANDLERS = {
    "record_site_visit": handle_record_site_visit,
    "schedule_callback": handle_schedule_callback,
}

@app.post("/api/webhook")
async def vapi_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    message = payload.get("message", {})
    msg_type = message.get("type", "unknown")
    call_id = message.get("call", {}).get("id", "unknown")

    logger.info(f"Webhook: type={msg_type} call_id={call_id}")

    if msg_type == "tool-calls":
        tool_call_list = message.get("toolCallList", [])
        results = []

        for tc in tool_call_list:
            func = tc.get("function", {})
            name = func.get("name", "")
            raw_args = func.get("arguments", {})

            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw_args

            handler = TOOL_HANDLERS.get(name)
            if handler:
                result = handler(call_id, args)
            else:
                logger.warning(f"Unknown tool called: {name}")
                result = f"Tool '{name}' is not recognized."

            results.append({"toolCallId": tc.get("id", ""), "result": result})

        return {"results": results}

    if msg_type == "status-update":
        return {"ok": True}

    if msg_type == "end-of-call-report":
        from datetime import datetime
        user_id = MemoryStore.get_user_for_call(call_id)
        if user_id:
            MemoryStore.save_call_summary(
                user_id=user_id,
                summary=message.get("summary", "No summary available"),
                transcript=message.get("transcript", ""),
                duration=message.get("artifact", {}).get("duration"),
            )
            
            # UPDATE STATE: Mark that the user has received the latest update
            user_data = MemoryStore.get_user(user_id)
            if user_data:
                project_data = MemoryStore.get_construction_update(user_data["project"])
                if project_data:
                    current_update_id = project_data.get("update_id")
                    MemoryStore.update_user_state(
                        user_id,
                        last_update_version=current_update_id,
                        last_called_at=datetime.now().isoformat(),
                        conversation_stage="visit_followup"
                    )
        return {"ok": True}

    return {"ok": True}

@app.post("/api/call", response_model=CallResponse)
async def make_call(req: CallRequest):
    user = MemoryStore.get_user(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{req.user_id}' not found")
    try:
        call_data = await trigger_outbound_call(req.user_id)
        return CallResponse(status="initiated", call_id=call_data.get("id"), user_id=req.user_id, message=f"Call initiated to {user['name']}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/morning-campaign")
async def start_morning_campaign(background_tasks: BackgroundTasks):
    """Run an orchestrated campaign to call users matching update conditions."""
    users_to_call = find_users_needing_updates()
    
    if not users_to_call:
        return {"status": "no_updates_needed", "message": "All users are up to date."}
    
    background_tasks.add_task(trigger_bulk_calls, users_to_call)
    logger.info(f"Morning campaign queued for {len(users_to_call)} users.")
    return {
        "status": "campaign_queued", 
        "users_called": len(users_to_call),
        "user_ids": users_to_call,
        "message": f"Queued {len(users_to_call)} calls. Processing in background."
    }

# ─── Data / Inspection Endpoints ───────────────────────────────────────────────

@app.get("/api/users")
async def list_users():
    """List all users in the mock database."""
    return MemoryStore.get_all_users()


@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    """Get full details for a user including history, state, and visit status."""

    user = MemoryStore.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")

    return {
        "user": user,
        "state": MemoryStore.get_user_state(user_id),
        "construction_update": MemoryStore.get_construction_update(user["project"]),
        "call_history": MemoryStore.get_call_history(user_id),
        "visit_intention": MemoryStore.get_visit_intention(user_id),
    }


@app.get("/api/visits")
async def list_visits():
    """List all recorded visit intentions."""
    return MemoryStore.get_all_visit_intentions()


@app.get("/api/call-history/{user_id}")
async def get_call_history(user_id: str):
    """Get call history for a specific user."""
    return MemoryStore.get_call_history(user_id)


@app.get("/api/callbacks")
async def list_callbacks():
    """List all scheduled callbacks."""
    return MemoryStore.get_all_callbacks()


@app.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="healthy", service="Riverwood AI Voice Agent", version="1.0.0")