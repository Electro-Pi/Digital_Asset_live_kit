"""Session model for server-side auth and Nafath state.

Conversation history is persisted by the LangGraph Postgres checkpointer (thread_id = session_id).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class Session:
    """Per-session auth and Nafath state (conversation history lives in checkpointer)."""

    session_id: str
    state: str = "ANONYMOUS"  # ANONYMOUS | NAFATH_AWAITING_ID | NAFATH_AWAITING_CONFIRMATION | NAFATH_VERIFIED | SESSION_EXPIRED
    user_id: Optional[str] = None
    jwt_token: Optional[str] = None
    language: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    nafath_transaction_id: Optional[str] = None
    nafath_challenge_number: Optional[str] = None
    nafath_national_id: Optional[str] = None
    nafath_access_token: Optional[str] = None
    verification_attempts: int = 0
    pending_action_intent: Optional[str] = None
    nafath_user_info: Optional[Dict[str, Any]] = None

    def touch(self) -> None:
        """Update last_activity for session expiry."""
        self.last_activity = datetime.now(timezone.utc)
