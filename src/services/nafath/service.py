"""Nafath authentication service (SERA ESB).

Integrates with SERA ESB Nafath API: OAuth token, Login, CheckStatus, UserInfo.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Dict, Optional

import httpx

from core.config import settings
from core.logging import get_logger
from core.security import create_access_token
from services.nafath.schemas import (
    NafathChallenge,
    NafathCheckStatusRequest,
    NafathCheckStatusResponse,
    NafathLoginRequest,
    NafathLoginResponse,
    NafathResult,
    NafathUserInfoRequest,
    NafathUserInfoResponse,
)

logger = get_logger(__name__)

_oauth_cache: Optional[tuple[str, float]] = None


def _clear_oauth_cache() -> None:
    global _oauth_cache
    _oauth_cache = None


class NafathService:
    """Nafath API client."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = (base_url or settings.NAFATH_ESB_BASE_URL).rstrip("/")
        self._client_id = settings.OAUTH_CLIENT_ID or ""
        self._client_secret = settings.OAUTH_CLIENT_SECRET or ""
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        """Gracefully close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def _get_oauth_token(self, force_refresh: bool = False) -> str:
        """Get Bearer token via client_credentials grant. Cached until near expiry."""
        global _oauth_cache
        now = time.time()
        if not force_refresh and _oauth_cache:
            token, expires_at = _oauth_cache
            if expires_at > now + 60:
                return token

        if not self._client_id or not self._client_secret:
            raise ValueError("OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET must be set")

        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode("utf-8")
        ).decode("ascii")

        resp = await self._client.post(
            f"{self._base_url}/auth/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic}",
            },
            data={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        data = resp.json()

        token = data.get("access_token")
        if not token:
            raise ValueError("OAuth response missing access_token")

        expires_in = data.get("expires_in", 3600)
        _oauth_cache = (token, now + expires_in)
        return token

    def _headers(self, token: str) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

    async def initiate_verification(self, national_id: str) -> NafathChallenge:
        """Start Nafath verification (ESB Login API).

        Returns a challenge (random) the user must confirm in the Nafath app.
        """
        token = await self._get_oauth_token()
        body = NafathLoginRequest(id=national_id)

        resp = await self._client.post(
            f"{self._base_url}/nafath/v1/login",
            json=body.model_dump(),
            headers=self._headers(token),
        )
        if resp.status_code == 401:
            token = await self._get_oauth_token(force_refresh=True)
            resp = await self._client.post(
                f"{self._base_url}/nafath/v1/login",
                json=body.model_dump(),
                headers=self._headers(token),
            )
        resp.raise_for_status()
        data = resp.json()

        login_resp = NafathLoginResponse(**data)
        return NafathChallenge(
            transaction_id=login_resp.transId,
            challenge_number=login_resp.random,
            message="Please confirm in the Nafath app.",
        )

    async def check_verification_status(
        self,
        transaction_id: str,
        random: str,
        national_id: str,
    ) -> NafathResult:
        """Poll Nafath (ESB CheckStatus API).

        Response only contains ``status`` and ``accessToken``.
        """
        token = await self._get_oauth_token()
        body = NafathCheckStatusRequest(
            transId=transaction_id,
            random=random,
            id=national_id,
        )

        resp = await self._client.post(
            f"{self._base_url}/nafath/v1/CheckStatus",
            json=body.model_dump(),
            headers=self._headers(token),
        )
        if resp.status_code == 401:
            token = await self._get_oauth_token(force_refresh=True)
            resp = await self._client.post(
                f"{self._base_url}/nafath/v1/CheckStatus",
                json=body.model_dump(),
                headers=self._headers(token),
            )
        data = resp.json() if resp.content else {}

        check_resp = NafathCheckStatusResponse(**data)
        status = (check_resp.status or "").upper()
        verified = status == "COMPLETED" and bool(check_resp.accessToken)

        jwt_token = None
        if verified:
            jwt_token = create_access_token(
                subject=national_id,
                extra_claims={"national_id": national_id},
            )

        return NafathResult(
            verified=verified,
            national_id=national_id,
            user_id=national_id if verified else None,
            jwt_token=jwt_token,
            message="Verification completed." if verified else "Verification pending or failed.",
            status=status or "PENDING",
            access_token=check_resp.accessToken,
        )

    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Fetch full user details (ESB UserInfo API) using the accessToken from CheckStatus."""
        token = await self._get_oauth_token()
        body = NafathUserInfoRequest(token=access_token)

        resp = await self._client.post(
            f"{self._base_url}/nafath/v1/UserInfo",
            json=body.model_dump(),
            headers=self._headers(token),
        )
        if resp.status_code == 401:
            token = await self._get_oauth_token(force_refresh=True)
            resp = await self._client.post(
                f"{self._base_url}/nafath/v1/UserInfo",
                json=body.model_dump(),
                headers=self._headers(token),
            )
        if resp.status_code != 200:
            logger.warning(
                "UserInfo failed",
                status=resp.status_code,
                body=resp.text[:200],
            )
            return None
        data = resp.json() if resp.content else {}

        info_resp = NafathUserInfoResponse(**data)
        if info_resp.user_Info:
            return info_resp.user_Info.model_dump()
        return None


_service_instance: Optional[NafathService] = None


def get_nafath_service() -> NafathService:
    """Return a module-level singleton. Call ``shutdown_nafath_service()`` on app teardown."""
    global _service_instance
    if _service_instance is None:
        from core.config import settings
        if settings.MOCK_NAFATH_SERVICE:
            from services.mock_services import MockNafathService
            _service_instance = MockNafathService()
        else:
            _service_instance = NafathService()
    return _service_instance


async def shutdown_nafath_service() -> None:
    """Close the shared HTTP client. Wire this to FastAPI lifespan shutdown."""
    global _service_instance
    if _service_instance is not None:
        await _service_instance.close()
        _service_instance = None
