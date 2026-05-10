"""Pydantic schemas for chat request / response."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' | 'assistant' | 'system'")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    jwt_token: Optional[str] = Field(
        default=None,
        description="Bearer token for identity verification (optional on first turn)",
    )
    history: List[ChatMessage] = Field(
        default_factory=list,
        description="Prior conversation turns to preserve context",
    )


class SourceDocument(BaseModel):
    content: str
    source: str
    score: float


class ToolCallResult(BaseModel):
    tool_name: str = Field(..., description="Name of the CRM tool that was called")
    args: Dict[str, Any] = Field(default_factory=dict, description="Arguments passed to the tool")
    result: str = Field(..., description="Raw result returned by the tool")


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    request_id: str
    intent: Optional[str] = None
    sources: List[SourceDocument] = Field(default_factory=list)
    requires_verification: bool = False
    verification_stage: Optional[str] = Field(
        default=None,
        description="awaiting_id | awaiting_nafath | verified | failed | exceeded",
    )
    escalated: bool = Field(
        default=False,
        description="True when the conversation has been escalated to a human agent",
    )
    tool_results: Optional[List[ToolCallResult]] = Field(
        default=None,
        description="Tool calls executed by the model during CRM/complex actions, with their results",
    )


class StreamChunk(BaseModel):
    delta: str
    done: bool = False
    request_id: str = ""
