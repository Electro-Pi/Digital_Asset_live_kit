"""
SERA CRM service — API client for the SERA ESB gateway (Flux APIs).

All endpoints use OAuth Bearer tokens obtained via client_credentials grant.
Responses follow the ESB envelope: {"status": "SUCCESS", "data": ...}.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Dict, List, Optional

import httpx

from core.config import settings
from core.logging import get_logger
from schemas.crm import (
    ActiveComplaint,
    ComplainerType,
    CreateCaseRequest,
    CreateCaseResponse,
    CustomerAccount,
    ESBError,
    ESBResponse,
    LookupItem,
)

logger = get_logger(__name__)

_TIMEOUT = 20.0
_RETRY_ONCE = 1

# Module-level OAuth token cache: (token_string, expires_at_epoch)
_oauth_cache: Optional[tuple[str, float]] = None


def _clear_oauth_cache() -> None:
    global _oauth_cache
    _oauth_cache = None


class CRMService:
    """CRM API client (SERA ESB Gateway)."""

    def __init__(self) -> None:
        self._base_url = settings.CRM_API_URL.rstrip("/")
        self._client_id = settings.OAUTH_CLIENT_ID
        self._client_secret = settings.OAUTH_CLIENT_SECRET

    # ── OAuth ─────────────────────────────────────────────────────────────────

    async def _get_oauth_token(self, force_refresh: bool = False) -> str:
        """Get Bearer token via client_credentials grant, cached until near expiry."""
        global _oauth_cache
        now = time.time()
        if not force_refresh and _oauth_cache:
            token, expires_at = _oauth_cache
            if expires_at > now + 60:
                return token

        if not self._client_id or not self._client_secret:
            raise ValueError("CRM OAuth credentials (OAUTH_CLIENT_SECRET / OAUTH_CLIENT_SECRET) must be set")

        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode("utf-8")
        ).decode("ascii")

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
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

    def _headers(self, token: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

    # ── Generic request ───────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        retries: int = _RETRY_ONCE,
    ) -> Dict[str, Any]:
        token = await self._get_oauth_token()
        last_exc: Exception | None = None

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    headers = self._headers(token)
                    if method == "GET":
                        resp = await client.get(url, headers=headers, params=params)
                    else:
                        resp = await client.post(url, headers=headers, json=data, params=params)

                    if resp.status_code == 401 and attempt == 0:
                        token = await self._get_oauth_token(force_refresh=True)
                        continue

                    resp.raise_for_status()
                    return resp.json()
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < retries:
                    logger.warning(
                        "CRM request timeout/connect, retrying",
                        url=url,
                        attempt=attempt + 1,
                    )
                continue
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "CRM API HTTP error",
                    url=url,
                    status_code=exc.response.status_code,
                    response_body=exc.response.text[:500],
                    request_body=str(data)[:500] if data else None,
                )
                raise exc
        raise last_exc or RuntimeError("CRM request failed")

    # ── ESB envelope unwrapping ───────────────────────────────────────────────

    @staticmethod
    def _unwrap(raw: Any) -> Any:
        """Unwrap the ESB envelope and return the ``data`` field.

        Raises ESBError when the gateway reports FAILURE.
        """
        if not isinstance(raw, dict):
            return raw
        esb = ESBResponse(**raw)
        if not esb.is_success:
            raise ESBError(
                code=esb.errorCode or "UNKNOWN",
                message=esb.errorMessage or "ESB request failed",
                trace=esb.trace,
            )
        return esb.data

    # ── Flux API — lookups ────────────────────────────────────────────────────

    async def get_contact_id(self, national_id: str) -> str:
        """GET /flux/v1/GetContactguid?NationalID=..."""
        url = f"{self._base_url}/flux/v1/GetContactguid"
        raw = await self._request("GET", url, params={"NationalID": national_id})
        data = self._unwrap(raw)
        if isinstance(data, list) and data:
            return str(data[0].get("contactid", ""))
        if isinstance(data, dict):
            return str(data.get("contactid", ""))
        return ""

    async def get_customer_account_list(self, id_number: str) -> List[str]:
        """POST /flux/v1/getCustomerAccountList?idNumber=..."""
        url = f"{self._base_url}/flux/v1/getCustomerAccountList"
        raw = await self._request("POST", url, params={"idNumber": id_number})
        data = self._unwrap(raw)
        if isinstance(data, dict):
            accounts_raw = data.get("customerAccounts", [])
            if isinstance(accounts_raw, list):
                return [
                    str(CustomerAccount(**a).accountNumber)
                    for a in accounts_raw
                    if isinstance(a, dict)
                ]
            if isinstance(accounts_raw, dict) and "accountNumber" in accounts_raw:
                return [str(accounts_raw["accountNumber"])]
        return []

    async def get_regions(self, page_size: int = 100, page_number: int = 1) -> List[LookupItem]:
        """GET /flux/v1/listRegions"""
        url = f"{self._base_url}/flux/v1/listRegions"
        raw = await self._request(
            "GET", url,
            params={"PageSize": page_size, "PageNumber": page_number},
        )
        data = self._unwrap(raw)
        return self._parse_lookup_list(data, key_field="regionId", value_field="regionName")

    async def get_cities(self, region_id: str) -> List[LookupItem]:
        """GET /flux/v1/GetCitiesByRegionid?RegionId=..."""
        url = f"{self._base_url}/flux/v1/GetCitiesByRegionid"
        raw = await self._request(
            "GET", url,
            params={"RegionId": region_id},
        )
        data = self._unwrap(raw)
        return self._parse_lookup_list(data, key_field="cityID", value_field="cityName")

    async def get_offices(self, city_id: str) -> List[LookupItem]:
        """GET /flux/v1/GetOfficesByCityId?CityId=..."""
        url = f"{self._base_url}/flux/v1/GetOfficesByCityId"
        raw = await self._request(
            "GET", url,
            params={"CityId": city_id},
        )
        data = self._unwrap(raw)
        return self._parse_lookup_list(data, key_field="officeId", value_field="officeName")

    async def get_case_types(self, page_size: int = 100, page_number: int = 1) -> List[LookupItem]:
        """GET /flux/v1/GetCaseTypeList"""
        url = f"{self._base_url}/flux/v1/GetCaseTypeList"
        raw = await self._request(
            "GET", url,
            params={"PageSize": page_size, "PageNumber": page_number},
        )
        data = self._unwrap(raw)
        return self._parse_lookup_list(data, key_field="caseTypeId", value_field="caseType")

    async def get_sub_case_types(self, case_type_id: str) -> List[LookupItem]:
        """GET /flux/v1/getSubcaseTypeByCaseTypeId"""
        url = f"{self._base_url}/flux/v1/getSubcaseTypeByCaseTypeId"
        raw = await self._request(
            "GET", url,
            params={"CaseTypeId": case_type_id},
        )
        data = self._unwrap(raw)
        return self._parse_lookup_list(data, key_field="nw_subcaseid", value_field="nw_name")

    async def get_complainer_types(self) -> List[ComplainerType]:
        """GET /flux/v1/GetOptionSetComplainerType"""
        url = f"{self._base_url}/flux/v1/GetOptionSetComplainerType"
        raw = await self._request("GET", url)
        data = self._unwrap(raw)
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    data = v
                    break
        if isinstance(data, list):
            return [
                ComplainerType(name=item.get("name", "").strip(), value=item.get("value", 0))
                for item in data
                if isinstance(item, dict)
            ]
        return []

    # ── Flux API — case operations ────────────────────────────────────────────

    async def create_case(self, request: CreateCaseRequest) -> CreateCaseResponse:
        """POST /flux/v1/CreateCase"""
        url = f"{self._base_url}/flux/v1/CreateCase"
        raw = await self._request(
            "POST", url,
            data=request.model_dump(by_alias=True),
        )
        data = self._unwrap(raw)
        if isinstance(data, dict):
            return CreateCaseResponse(**data)
        return CreateCaseResponse()

    async def get_case_details(self, case_number: str) -> Dict[str, Any]:
        """POST /flux/v1/GetCaseDetails -> first case object from data array."""
        url = f"{self._base_url}/flux/v1/GetCaseDetails"
        raw = await self._request(
            "POST", url,
            data={"caseNumber": case_number},
        )
        data = self._unwrap(raw)
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return {}

    async def get_customer_active_complaints(self, national_id: str) -> List[ActiveComplaint]:
        """GET /flux/v1/GetCustomerActiveComplaint?NationalId=..."""
        url = f"{self._base_url}/flux/v1/GetCustomerActiveComplaint"
        raw = await self._request("GET", url, params={"NationalId": national_id})
        data = self._unwrap(raw)
        if isinstance(data, list):
            return [
                ActiveComplaint.model_validate(item)
                for item in data
                if isinstance(item, dict)
            ]
        return []

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_lookup_list(
        data: Any,
        key_field: str = "Key",
        value_field: str = "Value",
    ) -> List[LookupItem]:
        """Parse a list-endpoint response into LookupItem objects.

        Handles both bare lists and wrapper objects with a nested list field.
        Different endpoints use different field names for the key/value pair,
        so callers can specify which fields to map.
        """
        items: list = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    items = v
                    break
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = {
                "Key": item.get(key_field) or item.get("Key", ""),
                "Value": item.get(value_field) or item.get("Value", ""),
            }
            result.append(LookupItem.model_validate(normalized))
        return result


def get_crm_service() -> CRMService:
    if settings.MOCK_CRM_SERVICE:
        from services.mock_services import MockCRMService
        return MockCRMService()
    return CRMService()
