"""Mock database and memory store for Riverwood AI Voice Agent."""

from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
import logging

logger = logging.getLogger(__name__)


# ─── Mock User Database ────────────────────────────────────────────────────────

USERS_DB: Dict[str, Dict[str, Any]] = {
    "user_001": {
        "name": "Rahul Sharma",
        "phone": "+919876543210",
        "language": "hi",
        "project": "Riverwood Estate - Tower A",
        "unit": "3BHK - 12th Floor, Unit 1204",
        "booking_date": "2025-03-15",
        "payment_status": "On Track",
    },
    "user_002": {
        "name": "Priya Patel",
        "phone": "+919876543211",
        "language": "en",
        "project": "Riverwood Estate - Tower B",
        "unit": "2BHK - 8th Floor, Unit 803",
        "booking_date": "2025-06-20",
        "payment_status": "On Track",
    },
    "user_003": {
        "name": "Amit Verma",
        "phone": "+919876543212",
        "language": "hi",
        "project": "Riverwood Estate - Tower A",
        "unit": "4BHK Penthouse - 20th Floor, Unit 2001",
        "booking_date": "2025-01-10",
        "payment_status": "On Track",
    },
}


# ─── Construction Updates ──────────────────────────────────────────────────────

CONSTRUCTION_UPDATES: Dict[str, Dict[str, Any]] = {
    "Riverwood Estate - Tower A": {
        "update_id": "towerA_phase3_v1",
        "current_phase": "Phase 3 - Interior Finishing",
        "completion_percentage": 72,
        "recent_milestone": "Plumbing and electrical work completed on floors 1-15",
        "next_milestone": "Flooring and wall painting begins next week",
        "expected_completion": "December 2026",
        "site_visit_available": True,
        "site_visit_timings": "Saturday and Sunday, 10:00 AM to 5:00 PM",
    },
    "Riverwood Estate - Tower B": {
        "update_id": "towerB_phase2_v1",
        "current_phase": "Phase 2 - Structural Work",
        "completion_percentage": 48,
        "recent_milestone": "RCC work completed up to 12th floor",
        "next_milestone": "Brickwork starting on floors 1-8",
        "expected_completion": "March 2027",
        "site_visit_available": True,
        "site_visit_timings": "Saturday and Sunday, 10:00 AM to 5:00 PM",
    },
}


# ─── Runtime State (in-memory) ─────────────────────────────────────────────────

VISIT_INTENTIONS: Dict[str, Dict[str, Any]] = {}
CALL_HISTORY: Dict[str, List[Dict[str, Any]]] = {}
ACTIVE_CALLS: Dict[str, str] = {}  # call_id -> user_id
SCHEDULED_CALLBACKS: Dict[str, Dict[str, Any]] = {}
USER_STATE: Dict[str, Dict[str, Any]] = {}


# ─── Memory Store ──────────────────────────────────────────────────────────────

class MemoryStore:
    """Manages application state and mock database operations."""

    # ── User State ──

    @staticmethod
    def get_user_state(user_id: str) -> Dict[str, Any]:
        """Get the progress and state of the user's journey."""
        if user_id not in USER_STATE:
            USER_STATE[user_id] = {
                "last_update_version": None,
                "last_called_at": None,
                "conversation_stage": "initial_update",
                "visit_interest": None,
            }
        return USER_STATE[user_id]

    @staticmethod
    def update_user_state(user_id: str, **kwargs) -> Dict[str, Any]:
        """Update specific fields in user state."""
        state = MemoryStore.get_user_state(user_id)
        state.update(kwargs)
        logger.info(f"Updated user state for {user_id}: {kwargs}")
        return state

    # ── User Lookups ──

    @staticmethod
    def get_user(user_id: str) -> Optional[Dict[str, Any]]:
        return USERS_DB.get(user_id)

    @staticmethod
    def get_user_by_phone(phone: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        for uid, data in USERS_DB.items():
            if data["phone"] == phone:
                return uid, data
        return None

    @staticmethod
    def get_all_users() -> Dict[str, Dict[str, Any]]:
        return USERS_DB

    # ── Construction Data ──

    @staticmethod
    def get_construction_update(project: str) -> Optional[Dict[str, Any]]:
        return CONSTRUCTION_UPDATES.get(project)

    # ── Call Management ──

    @staticmethod
    def register_call(call_id: str, user_id: str):
        ACTIVE_CALLS[call_id] = user_id
        logger.info(f"Registered call {call_id} for user {user_id}")

    @staticmethod
    def get_user_for_call(call_id: str) -> Optional[str]:
        return ACTIVE_CALLS.get(call_id)

    @staticmethod
    def save_call_summary(
        user_id: str,
        summary: str,
        transcript: str = None,
        duration: float = None,
    ):
        if user_id not in CALL_HISTORY:
            CALL_HISTORY[user_id] = []
        CALL_HISTORY[user_id].append({
            "summary": summary,
            "transcript": transcript,
            "duration": duration,
            "timestamp": datetime.now().isoformat(),
        })
        logger.info(f"Saved call summary for {user_id}")

    @staticmethod
    def get_call_history(user_id: str) -> List[Dict[str, Any]]:
        return CALL_HISTORY.get(user_id, [])

    @staticmethod
    def get_previous_context(user_id: str) -> str:
        """Get a summary of past interactions for context injection."""
        history = CALL_HISTORY.get(user_id, [])
        if not history:
            return "No previous conversations with this customer."
        last = history[-1]
        return f"Last call ({last['timestamp']}): {last['summary']}"

    # ── Visit Intentions ──

    @staticmethod
    def record_visit_intention(
        user_id: str,
        wants_to_visit: bool,
        preferred_date: str = None,
        notes: str = None,
    ) -> Dict[str, Any]:
        VISIT_INTENTIONS[user_id] = {
            "wants_to_visit": wants_to_visit,
            "preferred_date": preferred_date,
            "notes": notes,
            "recorded_at": datetime.now().isoformat(),
        }
        logger.info(f"Recorded visit intention for {user_id}: visit={wants_to_visit}")
        return VISIT_INTENTIONS[user_id]

    @staticmethod
    def get_visit_intention(user_id: str) -> Optional[Dict[str, Any]]:
        return VISIT_INTENTIONS.get(user_id)

    @staticmethod
    def get_all_visit_intentions() -> Dict[str, Dict[str, Any]]:
        return VISIT_INTENTIONS

    # ── Callbacks ──

    @staticmethod
    def schedule_callback(
        user_id: str,
        preferred_time: str,
        notes: str = None,
    ) -> Dict[str, Any]:
        SCHEDULED_CALLBACKS[user_id] = {
            "preferred_time": preferred_time,
            "notes": notes,
            "scheduled_at": datetime.now().isoformat(),
        }
        logger.info(f"Scheduled callback for {user_id} at {preferred_time}")
        return SCHEDULED_CALLBACKS[user_id]

    @staticmethod
    def get_all_callbacks() -> Dict[str, Dict[str, Any]]:
        return SCHEDULED_CALLBACKS