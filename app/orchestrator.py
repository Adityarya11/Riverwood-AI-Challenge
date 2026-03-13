"""Event-driven orchestrator for campaign management."""

import logging
from typing import List

from app.memory import MemoryStore

logger = logging.getLogger(__name__)

def find_users_needing_updates() -> List[str]:
    """
    Identify users who need to be called because their project
    has a new construction update they haven't heard yet.
    """
    users = MemoryStore.get_all_users()
    call_list = []

    for uid, user in users.items():
        project_update = MemoryStore.get_construction_update(user["project"])
        
        # If no project data exists, skip
        if not project_update:
            continue
            
        current_update_id = project_update.get("update_id")
        state = MemoryStore.get_user_state(uid)

        # If user hasn't received this update yet, add to queue
        if state["last_update_version"] != current_update_id:
            logger.info(f"User {uid} needs update {current_update_id} (currently has {state['last_update_version']})")
            call_list.append(uid)
        else:
            logger.debug(f"User {uid} is already up to date on {current_update_id}.")

    return call_list
