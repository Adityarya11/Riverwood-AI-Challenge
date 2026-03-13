# app/memory.py
import sqlite3
import os
import logging
import json
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("SQLITE_DB_PATH", os.path.join(os.getcwd(), "data", "riverwood.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# seed data (same sample users / updates you had)
SEED_USERS = {
    "user_001": {
        "name": "Akansha",
        "phone": "+919876543210",
        "language": "hi",
        "project": "Riverwood Estate - Tower A",
        "unit": "3BHK - 12th Floor, Unit 1204",
        "booking_date": "2025-03-15",
        "payment_status": "On Track",
    },
    "user_002": {
        "name": "Khushi",
        "phone": "+919876543211",
        "language": "en",
        "project": "Riverwood Estate - Tower B",
        "unit": "2BHK - 8th Floor, Unit 803",
        "booking_date": "2025-06-20",
        "payment_status": "On Track",
    },
    "user_003": {
        "name": "Sakshi",
        "phone": "+919876543212",
        "language": "hi",
        "project": "Riverwood Estate - Tower A",
        "unit": "4BHK Penthouse - 20th Floor, Unit 2001",
        "booking_date": "2025-01-10",
        "payment_status": "On Track",
    },
}

SEED_UPDATES = {
    "Riverwood Estate - Tower A": {
        "update_id": "towerA_phase3_v1",
        "current_phase": "Phase 3 - Interior Finishing",
        "completion_percentage": 72,
        "recent_milestone": "Plumbing and electrical work completed on floors 1-15",
        "next_milestone": "Flooring and wall painting begins next week",
        "expected_completion": "December 2026",
        "site_visit_available": 1,
        "site_visit_timings": "Saturday and Sunday, 10:00 AM to 5:00 PM",
    },
    "Riverwood Estate - Tower B": {
        "update_id": "towerB_phase2_v1",
        "current_phase": "Phase 2 - Structural Work",
        "completion_percentage": 48,
        "recent_milestone": "RCC work completed up to 12th floor",
        "next_milestone": "Brickwork starting on floors 1-8",
        "expected_completion": "March 2027",
        "site_visit_available": 1,
        "site_visit_timings": "Saturday and Sunday, 10:00 AM to 5:00 PM",
    },
}


# ----------------- low-level helpers (run in thread) -------------------------
def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema():
    conn = _connect()
    cur = conn.cursor()
    # users
    cur.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT,
            phone TEXT,
            language TEXT,
            project TEXT,
            unit TEXT,
            booking_date TEXT,
            payment_status TEXT
        )"""
    )
    # construction updates
    cur.execute(
        """CREATE TABLE IF NOT EXISTS construction_updates (
            project TEXT PRIMARY KEY,
            update_id TEXT,
            current_phase TEXT,
            completion_percentage INTEGER,
            recent_milestone TEXT,
            next_milestone TEXT,
            expected_completion TEXT,
            site_visit_available INTEGER,
            site_visit_timings TEXT
        )"""
    )
    # user state
    cur.execute(
        """CREATE TABLE IF NOT EXISTS user_state (
            user_id TEXT PRIMARY KEY,
            last_update_version TEXT,
            last_called_at TEXT,
            conversation_stage TEXT,
            visit_interest INTEGER
        )"""
    )
    # call history
    cur.execute(
        """CREATE TABLE IF NOT EXISTS call_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            summary TEXT,
            transcript TEXT,
            duration REAL,
            timestamp TEXT
        )"""
    )
    # visit intentions
    cur.execute(
        """CREATE TABLE IF NOT EXISTS visit_intentions (
            user_id TEXT PRIMARY KEY,
            wants_to_visit INTEGER,
            preferred_date TEXT,
            notes TEXT,
            recorded_at TEXT
        )"""
    )
    # active calls mapping
    cur.execute(
        """CREATE TABLE IF NOT EXISTS active_calls (
            call_id TEXT PRIMARY KEY,
            user_id TEXT
        )"""
    )
    # callbacks
    cur.execute(
        """CREATE TABLE IF NOT EXISTS scheduled_callbacks (
            user_id TEXT PRIMARY KEY,
            preferred_time TEXT,
            notes TEXT,
            scheduled_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


async def init_db():
    """Initialize schema and seed the database (run at startup)."""
    await asyncio.to_thread(_init_schema)

    # seed users & construction updates only if missing
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(1) as c FROM users")
    if cur.fetchone()["c"] == 0:
        logger.info("Seeding users into sqlite DB")
        for uid, u in SEED_USERS.items():
            cur.execute(
                "INSERT INTO users (id, name, phone, language, project, unit, booking_date, payment_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, u["name"], u["phone"], u["language"], u["project"], u["unit"], u["booking_date"], u["payment_status"]),
            )
        conn.commit()

    cur.execute("SELECT COUNT(1) as c FROM construction_updates")
    if cur.fetchone()["c"] == 0:
        logger.info("Seeding construction updates")
        for project, data in SEED_UPDATES.items():
            cur.execute(
                "INSERT INTO construction_updates (project, update_id, current_phase, completion_percentage, recent_milestone, next_milestone, expected_completion, site_visit_available, site_visit_timings) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project, data["update_id"], data["current_phase"], data["completion_percentage"], data["recent_milestone"], data["next_milestone"], data["expected_completion"], data["site_visit_available"], data["site_visit_timings"]),
            )
        conn.commit()
    conn.close()


# ----------------- Public API (async wrappers) ------------------------------
class MemoryStore:
    """Simple SQLite-backed store with small helper functions."""

    # --- users ---
    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        return dict(row) if row else None

    @staticmethod
    def get_user(user_id: str) -> Optional[Dict[str, Any]]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def get_all_users() -> Dict[str, Dict[str, Any]]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users")
        rows = cur.fetchall()
        conn.close()
        return {r["id"]: dict(r) for r in rows}

    # --- construction updates ---
    @staticmethod
    def get_construction_update(project: str) -> Optional[Dict[str, Any]]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM construction_updates WHERE project = ?", (project,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    # --- active calls ---
    @staticmethod
    def register_call(call_id: str, user_id: str):
        conn = _connect()
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO active_calls (call_id, user_id) VALUES (?, ?)", (call_id, user_id))
        conn.commit()
        conn.close()
        logger.info(f"Registered call {call_id} for {user_id}")

    @staticmethod
    def get_user_for_call(call_id: str) -> Optional[str]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM active_calls WHERE call_id = ?", (call_id,))
        row = cur.fetchone()
        conn.close()
        return row["user_id"] if row else None

    # --- call history ---
    @staticmethod
    def save_call_summary(user_id: str, summary: str, transcript: str = None, duration: float = None):
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO call_history (user_id, summary, transcript, duration, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, summary, transcript, duration, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        logger.info(f"Saved call summary for {user_id}")

    @staticmethod
    def get_call_history(user_id: str) -> List[Dict[str, Any]]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM call_history WHERE user_id = ? ORDER BY id DESC", (user_id,))
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def get_previous_context(user_id: str) -> str:
        history = MemoryStore.get_call_history(user_id)
        if not history:
            return "No previous conversations with this customer."
        last = history[0]
        return f"Last call ({last['timestamp']}): {last['summary']}"

    # --- visit intentions ---
    @staticmethod
    def record_visit_intention(user_id: str, wants_to_visit: bool, preferred_date: str = None, notes: str = None) -> Dict[str, Any]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO visit_intentions (user_id, wants_to_visit, preferred_date, notes, recorded_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, int(bool(wants_to_visit)), preferred_date, notes, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        logger.info(f"Recorded visit intention for {user_id}: {wants_to_visit}")
        return MemoryStore.get_visit_intention(user_id)

    @staticmethod
    def get_visit_intention(user_id: str) -> Optional[Dict[str, Any]]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM visit_intentions WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def get_all_visit_intentions() -> Dict[str, Dict[str, Any]]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM visit_intentions")
        rows = cur.fetchall()
        conn.close()
        return {r["user_id"]: dict(r) for r in rows}

    # --- callbacks ---
    @staticmethod
    def schedule_callback(user_id: str, preferred_time: str, notes: str = None) -> Dict[str, Any]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO scheduled_callbacks (user_id, preferred_time, notes, scheduled_at) VALUES (?, ?, ?, ?)",
                    (user_id, preferred_time, notes, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        logger.info(f"Scheduled callback for {user_id} at {preferred_time}")
        return MemoryStore.get_all_callbacks().get(user_id)

    @staticmethod
    def get_all_callbacks() -> Dict[str, Dict[str, Any]]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM scheduled_callbacks")
        rows = cur.fetchall()
        conn.close()
        return {r["user_id"]: dict(r) for r in rows}

    # --- user state (conversation stage, last update) ---
    @staticmethod
    def get_user_state(user_id: str) -> Dict[str, Any]:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM user_state WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            state = dict(row)
            state["visit_interest"] = bool(state["visit_interest"]) if state["visit_interest"] is not None else None
            conn.close()
            return state
        # default state if missing
        default = {
            "user_id": user_id,
            "last_update_version": None,
            "last_called_at": None,
            "conversation_stage": "initial_update",
            "visit_interest": None,
        }
        cur.execute("INSERT INTO user_state (user_id, last_update_version, last_called_at, conversation_stage, visit_interest) VALUES (?, ?, ?, ?, ?)",
                    (user_id, default["last_update_version"], default["last_called_at"], default["conversation_stage"], default["visit_interest"]))
        conn.commit()
        conn.close()
        return default

    @staticmethod
    def update_user_state(user_id: str, **kwargs) -> Dict[str, Any]:
        state = MemoryStore.get_user_state(user_id)
        # simple upsert: merge kwargs into state
        for k, v in kwargs.items():
            if k in state:
                state[k] = v
        conn = _connect()
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO user_state (user_id, last_update_version, last_called_at, conversation_stage, visit_interest) VALUES (?, ?, ?, ?, ?)",
                    (user_id, state.get("last_update_version"), state.get("last_called_at"), state.get("conversation_stage"), int(state.get("visit_interest")) if state.get("visit_interest") is not None else None))
        conn.commit()
        conn.close()
        logger.info(f"Updated user state for {user_id}: {kwargs}")
        return state