"""In-memory session store (upgradeable to Redis)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from core.config import settings
from services.session.models import Session

# In-memory: session_id -> Session
_store: dict[str, Session] = {}


class SessionStore:
    """Store and retrieve sessions. In-memory for Phase 1."""

    def __init__(self) -> None:
        self._sessions = _store

    def get(self, session_id: str) -> Optional[Session]:
        s = self._sessions.get(session_id)
        if s is None:
            return None
        if self._is_expired(s):
            s.state = "SESSION_EXPIRED"
        return s

    def get_or_create(self, session_id: str) -> Session:
        s = self.get(session_id)
        if s is not None:
            return s
        s = Session(session_id=session_id)
        self._sessions[session_id] = s
        return s

    def set(self, session: Session) -> None:
        self._sessions[session.session_id] = session

    def _is_expired(self, session: Session) -> bool:
        delta = datetime.now(timezone.utc) - session.last_activity
        return delta.total_seconds() > settings.SESSION_EXPIRY_MINUTES * 60


_store_instance: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = SessionStore()
    return _store_instance
