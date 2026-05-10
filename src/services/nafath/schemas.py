"""Nafath API and session state schemas.

Includes ESB API request/response models and internal models used by the verifier.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


# ── Session state ─────────────────────────────────────────────────────────────

class SessionState(str, Enum):
    """User session state throughout the Nafath verification flow."""
    ANONYMOUS = "ANONYMOUS"
    NAFATH_AWAITING_ID = "NAFATH_AWAITING_ID"
    NAFATH_AWAITING_CONFIRMATION = "NAFATH_AWAITING_CONFIRMATION"
    NAFATH_VERIFIED = "NAFATH_VERIFIED"
    SESSION_EXPIRED = "SESSION_EXPIRED"


# ── ESB shared error ──────────────────────────────────────────────────────────

class ESBErrorResponse(BaseModel):
    """Shared error structure returned by ESB Nafath APIs."""
    code: Optional[str] = None
    requestedURL: Optional[str] = None
    message: Optional[str] = None
    trace: Optional[str] = None


# ── Login API ──────────────────────────────────────────────────────────────────

class NafathLoginRequest(BaseModel):
    """Request body for POST /nafath/v1/login."""
    id: str = Field(..., description="User ID (National ID / Iqama) for authentication")


class NafathLoginResponse(BaseModel):
    """Success response from Login API."""
    transId: str = Field(..., description="Transaction ID for CheckStatus")
    random: str = Field(..., description="Random value to display to user (challenge)")


# ── CheckStatus API ────────────────────────────────────────────────────────────

class NafathCheckStatusRequest(BaseModel):
    """Request body for POST /nafath/v1/CheckStatus."""
    transId: str = Field(..., description="Unique Id received from login API")
    random: str = Field(..., description="Random text received from Login API")
    id: str = Field(..., description="Id of user")


class NafathPersonInfo(BaseModel):
    """Person object in CheckStatus success response."""
    id: Optional[int] = None
    idVersion: Optional[int] = None
    arFirst: Optional[str] = None
    arFather: Optional[str] = None
    arGrand: Optional[str] = None
    arFamily: Optional[str] = None
    enFirst: Optional[str] = None
    enFather: Optional[str] = None
    enGrand: Optional[str] = None
    enFamily: Optional[str] = None
    arTwoNames: Optional[str] = None
    enTwoNames: Optional[str] = None
    arFullName: Optional[str] = None
    enFullName: Optional[str] = None
    gender: Optional[str] = None
    idIssueDateG: Optional[str] = None
    idIssueDateH: Optional[int] = None
    idExpiryDateG: Optional[str] = None
    idExpiryDateH: Optional[int] = None
    nationality: Optional[int] = None
    language: Optional[str] = None
    arNationality: Optional[str] = None
    enNationality: Optional[str] = None
    dobG: Optional[str] = None
    dobH: Optional[int] = None


class NafathCheckStatusResponse(BaseModel):
    """Response from CheckStatus API (success or business/technical error)."""
    code: Optional[str] = None
    requestedURL: Optional[str] = None
    message: Optional[str] = None
    trace: Optional[str] = None
    person: Optional[NafathPersonInfo] = None
    status: Optional[str] = None
    accessToken: Optional[str] = None
    wToken: Optional[str] = None


# ── UserInfo API ──────────────────────────────────────────────────────────────

class NafathUserInfoRequest(BaseModel):
    """Request body for POST /nafath/v1/UserInfo."""
    token: str = Field(..., description="Access token received from Check Request Status API")


class NafathUserDetail(BaseModel):
    """user_Info object in UserInfo success response."""
    id: Optional[int] = None
    id_version: Optional[int] = None
    first_namear: Optional[str] = None
    father_namear: Optional[str] = None
    grand_namear: Optional[str] = None
    family_namear: Optional[str] = None
    first_nameen: Optional[str] = None
    father_nameen: Optional[str] = None
    grand_nameen: Optional[str] = None
    family_nameen: Optional[str] = None
    two_namesar: Optional[str] = None
    two_namesen: Optional[str] = None
    full_namear: Optional[str] = None
    full_nameen: Optional[str] = None
    gender: Optional[str] = None
    id_issue_dateg: Optional[str] = None
    id_issue_dateh: Optional[int] = None
    id_expiry_dateg: Optional[str] = None
    id_expiry_dateh: Optional[int] = None
    nationality: Optional[int] = None
    language: Optional[str] = None
    nationalityar: Optional[str] = None
    nationalityen: Optional[str] = None
    dobg: Optional[str] = None
    dobh: Optional[int] = None
    card_issue_placear: Optional[str] = None
    card_issue_placeen: Optional[str] = None
    birth_placear: Optional[str] = None
    title_descar: Optional[str] = None
    title_descen: Optional[str] = None


class NafathUserInfoResponse(BaseModel):
    """Response from UserInfo API."""
    code: Optional[str] = None
    requestedURL: Optional[str] = None
    message: Optional[str] = None
    trace: Optional[str] = None
    user_Info: Optional[NafathUserDetail] = None
    aud: Optional[str] = None
    iss: Optional[str] = None
    iat: Optional[int] = None
    nbf: Optional[int] = None
    statusCode: Optional[int] = None


# ── Internal models (used by verifier node) ─────────────────────────────────────

class NafathChallenge(BaseModel):
    """Returned when initiating Nafath verification — user confirms in Nafath app."""
    transaction_id: str = Field(..., description="Used to poll verification status")
    challenge_number: str = Field(..., description="Random number to confirm in Nafath app")
    message: str = Field(default="Please confirm in the Nafath app.")


class NafathResult(BaseModel):
    """Result of checking Nafath verification status."""
    verified: bool
    national_id: Optional[str] = None
    user_id: Optional[str] = None
    jwt_token: Optional[str] = None
    message: str
    status: str = Field(default="PENDING", description="PENDING | COMPLETED | EXPIRED | FAILED")
    person_info: Optional[Dict[str, Any]] = None
    access_token: Optional[str] = None
    user_info: Optional[Dict[str, Any]] = None
