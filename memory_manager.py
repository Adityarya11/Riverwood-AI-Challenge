"""
memory_manager.py
-----------------
Production-grade Tiered Memory System (Hot/Cold Architecture)

L1 - HOT  : Redis (RAM) — active call, <1ms retrieval
L2 - COLD : SQLite/Postgres — persistent history, survives restarts

Flow:
  - Call starts  → load cold → warm up Redis
  - Each turn    → read/write Redis only (ultra-fast)
  - Call ends    → background task commits Redis → DB, flushes Redis key
"""

import os
import json
import redis
from db import SessionLocal, Interaction

# ── Redis connection (single shared client, thread-safe) ──────────────────────
redis_client = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    decode_responses=False,   # raw bytes — we handle JSON encoding ourselves
    socket_connect_timeout=2,
    socket_timeout=2
)

HOT_TTL_SECONDS = 3600       # Keys auto-expire 1 hr after last activity
MAX_HOT_MESSAGES = 10        # Rolling window — caps LLM context tokens


class FastMemoryManager:
    """
    Unified memory interface.  Callers never touch Redis or DB directly.

    Usage:
        mem = FastMemoryManager(user_id)
        history = mem.get_recent_context()   # <1ms from Redis
        mem.add_message("user", "Hello")
        mem.add_message("assistant", "Hi!")
        # On call end (background task):
        await mem.commit_to_cold_storage()
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.redis_key = f"active_call:{user_id}"

    # ── READ ─────────────────────────────────────────────────────────────────

    def get_recent_context(self) -> list[dict]:
        """
        Primary read path.
        1. Try Redis (hot cache) first — sub-millisecond.
        2. On miss, hydrate from SQLite (cold storage) and warm up Redis.
        Returns a list of {role, content} dicts ready for OpenAI messages[].
        """
        try:
            raw_messages = redis_client.lrange(self.redis_key, 0, -1)
        except redis.RedisError as e:
            print(f"[Memory] Redis read error for {self.user_id}: {e}. Falling back to cold.")
            raw_messages = []

        if raw_messages:
            # Cache HIT — fast path
            redis_client.expire(self.redis_key, HOT_TTL_SECONDS)   # refresh TTL
            return [json.loads(m.decode("utf-8")) for m in raw_messages]

        # Cache MISS — pull from cold storage and warm Redis
        return self._hydrate_from_cold()

    def _hydrate_from_cold(self) -> list[dict]:
        """Load recent history from DB and write it into Redis for this session."""
        db = SessionLocal()
        try:
            rows = (
                db.query(Interaction)
                .filter(Interaction.user_id == self.user_id)
                .order_by(Interaction.timestamp.desc())
                .limit(MAX_HOT_MESSAGES)
                .all()
            )
            rows = list(reversed(rows))
            messages = [{"role": r.role, "content": r.content} for r in rows]

            if messages:
                try:
                    pipeline = redis_client.pipeline()
                    for msg in messages:
                        pipeline.rpush(self.redis_key, json.dumps(msg))
                    pipeline.expire(self.redis_key, HOT_TTL_SECONDS)
                    pipeline.execute()
                    print(f"[Memory] Hydrated {len(messages)} messages from cold → hot for {self.user_id}")
                except Exception as redis_err:
                    print(f"[Memory] Redis hydration skipped: {redis_err}")

            return messages
        except Exception as e:
            print(f"[Memory] Cold hydration error: {e}")
            return []
        finally:
            db.close()

    # ── WRITE ────────────────────────────────────────────────────────────────

    def add_message(self, role: str, content: str):
        """
        Append a message to the hot cache.
        Uses a Redis pipeline so rpush + ltrim + expire are atomic.
        Maintains a rolling window of MAX_HOT_MESSAGES.
        """
        message_json = json.dumps({"role": role, "content": content})
        try:
            pipeline = redis_client.pipeline()
            pipeline.rpush(self.redis_key, message_json)
            pipeline.ltrim(self.redis_key, -MAX_HOT_MESSAGES, -1)   # rolling window
            pipeline.expire(self.redis_key, HOT_TTL_SECONDS)
            pipeline.execute()
        except redis.RedisError as e:
            print(f"[Memory] Redis write error for {self.user_id}: {e}")

    # ── FLUSH ────────────────────────────────────────────────────────────────

    def commit_to_cold_storage(self):
        """
        Called as a background task when a call ends.
        Writes hot cache contents to the SQLite/Postgres DB, then deletes the Redis key.
        This is the ONLY place DB writes happen — keeping the hot path 100% Redis.
        """
        db = SessionLocal()
        try:
            cached_messages = self.get_recent_context()
            if not cached_messages:
                print(f"[Memory] No messages to commit for {self.user_id}")
                return

            for msg in cached_messages:
                db.add(Interaction(
                    user_id=self.user_id,
                    role=msg["role"],
                    content=msg["content"]
                ))
            db.commit()

            # Flush the hot key so the next call starts clean (re-hydrating from DB)
            try:
                redis_client.delete(self.redis_key)
            except Exception as e:
                pass
            print(f"[Memory] Committed {len(cached_messages)} messages to cold storage for {self.user_id} ✓")

        except Exception as e:
            db.rollback()
            print(f"[Memory] Commit error for {self.user_id}: {e}")
        finally:
            db.close()

    # ── UTILITY ──────────────────────────────────────────────────────────────

    def is_returning_user(self) -> bool:
        """Check if user has any prior history (cold or hot)."""
        try:
            if redis_client.llen(self.redis_key) > 0:
                return True
        except redis.RedisError:
            pass

        db = SessionLocal()
        try:
            count = db.query(Interaction).filter(Interaction.user_id == self.user_id).count()
            return count > 0
        finally:
            db.close()
