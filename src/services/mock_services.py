"""Mock CRM and Nafath services for end-to-end testing without real APIs.

Activated by setting ``MOCK_CRM_SERVICE=true`` and/or
``MOCK_NAFATH_SERVICE=true`` in ``.env``.
flag is also supported and enables both. The factory functions
``get_crm_service()`` and ``get_nafath_service()`` return mock instances that
return realistic Arabic data in memory, loaded from the ``mock_data/`` JSON
files so that IDs match the real API exactly.
"""

from __future__ import annotations

import json
import random
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging import get_logger
from core.security import create_access_token
from schemas.crm import ActiveComplaint, ActiveComplaintBeneficiary, ActiveComplaintCaseType, ComplainerType, CreateCaseRequest, CreateCaseResponse, LookupItem
from services.crm.service import CRMService
from services.nafath.schemas import NafathChallenge, NafathResult
from services.nafath.service import NafathService

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Load mock data from JSON files (relative to the repo root)
# ---------------------------------------------------------------------------

_MOCK_DATA_DIR = Path(__file__).resolve().parents[2] / "mock_data"


def _load_json(filename: str) -> Any:
    path = _MOCK_DATA_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Parse locations.json once at import time
_locations: List[Dict[str, Any]] = _load_json("locations.json")
_cases_data: List[Dict[str, Any]] = _load_json("cases.json")

# ── Flat region list ──────────────────────────────────────────────────────────

_REGION_ITEMS: List[LookupItem] = [
    LookupItem.model_validate({"Key": r["regionId"], "Value": r["regionName"]})
    for r in _locations
]

# ── Flat city list (all cities across all regions) ────────────────────────────

_CITY_ITEMS: List[LookupItem] = [
    LookupItem.model_validate({"Key": city["cityID"], "Value": city["cityName"]})
    for region in _locations
    for city in region.get("cities", [])
]

# ── Flat office list (all offices across all cities) ─────────────────────────

_OFFICE_ITEMS: List[LookupItem] = [
    LookupItem.model_validate({"Key": office["officeId"], "Value": office["officeName"]})
    for region in _locations
    for city in region.get("cities", [])
    for office in city.get("offices", [])
]

# ── Case type list ────────────────────────────────────────────────────────────

_CASE_TYPE_ITEMS: List[LookupItem] = [
    LookupItem.model_validate({"Key": ct["caseTypeId"], "Value": ct["caseType"]})
    for ct in _cases_data
]

# ── Sub-case type index: caseTypeId → List[LookupItem] ───────────────────────

_SUB_CASE_TYPE_MAP: Dict[str, List[LookupItem]] = {
    ct["caseTypeId"]: [
        LookupItem.model_validate({"Key": sub["nw_subcaseid"], "Value": sub["nw_name"]})
        for sub in ct.get("SubcaseType", [])
    ]
    for ct in _cases_data
}


# ═══════════════════════════════════════════════════════════════════════════════
# MockCRMService
# ═══════════════════════════════════════════════════════════════════════════════

class MockCRMService(CRMService):
    """In-memory CRM service backed by mock_data JSON files."""

    def __init__(self) -> None:
        logger.info(
            "MockCRMService initialised (mock mode enabled)",
            regions=len(_REGION_ITEMS),
            cities=len(_CITY_ITEMS),
            offices=len(_OFFICE_ITEMS),
            case_types=len(_CASE_TYPE_ITEMS),
        )

    # ── Lookups ───────────────────────────────────────────────────────────────

    async def get_regions(self, page_size: int = 100, page_number: int = 1) -> List[LookupItem]:
        return list(_REGION_ITEMS)

    async def get_cities(self, region_id: str) -> List[LookupItem]:
        matched = [r for r in _locations if r["regionId"] == region_id]
        return [
            LookupItem.model_validate({"Key": city["cityID"], "Value": city["cityName"]})
            for region in matched
            for city in region.get("cities", [])
        ]

    async def get_offices(self, city_id: str) -> List[LookupItem]:
        return [
            LookupItem.model_validate({"Key": office["officeId"], "Value": office["officeName"]})
            for region in _locations
            for city in region.get("cities", [])
            if city["cityID"] == city_id
            for office in city.get("offices", [])
        ]

    async def get_case_types(self, page_size: int = 100, page_number: int = 1) -> List[LookupItem]:
        return list(_CASE_TYPE_ITEMS)

    async def get_sub_case_types(self, case_type_id: str) -> List[LookupItem]:
        return list(_SUB_CASE_TYPE_MAP.get(case_type_id, []))

    # ── Case operations ───────────────────────────────────────────────────────

    async def get_complainer_types(self) -> List[ComplainerType]:
        return [
            ComplainerType(name="سكني", value=266990001),
            ComplainerType(name="تجاري", value=266990002),
            ComplainerType(name="منشأة صحية خاصة أو مدرسة أهلية", value=100000002),
        ]

    # ── Case operations ───────────────────────────────────────────────────────

    async def create_case(self, request: CreateCaseRequest) -> CreateCaseResponse:
        case_number = f"26{random.randint(100000, 999999)}"
        logger.info("MockCRMService: case created", case_number=case_number)
        return CreateCaseResponse(
            message="تم إنشاء الشكوى بنجاح",
            isSuccess=True,
            caseId=case_number,
        )

    async def get_case_details(self, case_number: str) -> Dict[str, Any]:
        return {
            "CaseNumber": case_number,
            "CaseId": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"case-{case_number}")),
            "Status": {"Key": "266990010", "Value": "Checking Complaint"},
            "CaseStage": {"Key": "266990001", "Value": "قيد المراجعة"},
            "CaseType": {"Key": _CASE_TYPE_ITEMS[2].key, "Value": _CASE_TYPE_ITEMS[2].value},
            "SubCaseType": {"Key": list(_SUB_CASE_TYPE_MAP.values())[2][5].key, "Value": list(_SUB_CASE_TYPE_MAP.values())[2][5].value},
            "Region": {"Key": _REGION_ITEMS[1].key, "Value": _REGION_ITEMS[1].value},
            "City": {"Key": _CITY_ITEMS[0].key, "Value": _CITY_ITEMS[0].value},
            "Office": {"Key": _OFFICE_ITEMS[0].key, "Value": _OFFICE_ITEMS[0].value},
            "ComplainterType": {"Key": "266990001", "Value": "Residential"},
            "Account": {"Key": str(uuid.uuid5(uuid.NAMESPACE_DNS, "account-sec")), "Value": "الشركة السعودية للكهرباء SEC"},
            "Description": "شكوى تجريبية من نظام المحاكاة",
            "ClosedDate": None,
            "CanReopen": False,
            "CompalintRegistered": True,
        }

    async def get_contact_id(self, national_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"contact-{national_id}"))

    async def get_customer_account_list(self, id_number: str) -> List[str]:
        return ["1234567890"]

    async def get_customer_active_complaints(self, national_id: str) -> List[ActiveComplaint]:
        return [
            ActiveComplaint(
                nw_name="26011755",
                createdon="2025-11-08T23:27:08Z",
                nw_beneficiary=ActiveComplaintBeneficiary(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"contact-{national_id}")),
                    name="عبدالله محمد الشمري",
                ),
                nw_casetype=ActiveComplaintCaseType(
                    id=_CASE_TYPE_ITEMS[2].key,
                    name=_CASE_TYPE_ITEMS[2].value,
                ),
                nw_casepriority=2,
                nw_actiondate="2025-11-08T23:27:24Z",
                nw_caseid=str(uuid.uuid5(uuid.NAMESPACE_DNS, "case-26011755")),
            ),
            ActiveComplaint(
                nw_name="26022301",
                createdon="2025-12-01T10:15:00Z",
                nw_beneficiary=ActiveComplaintBeneficiary(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"contact-{national_id}")),
                    name="عبدالله محمد الشمري",
                ),
                nw_casetype=ActiveComplaintCaseType(
                    id=_CASE_TYPE_ITEMS[0].key,
                    name=_CASE_TYPE_ITEMS[0].value,
                ),
                nw_casepriority=1,
                nw_actiondate="2025-12-01T10:15:30Z",
                nw_caseid=str(uuid.uuid5(uuid.NAMESPACE_DNS, "case-26022301")),
            ),
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# MockNafathService
# ═══════════════════════════════════════════════════════════════════════════════

class MockNafathService(NafathService):
    """Nafath service that auto-verifies any National ID instantly."""

    def __init__(self) -> None:
        self._client = None  # no HTTP client needed
        logger.info("MockNafathService initialised (mock mode enabled)")

    async def close(self) -> None:
        pass

    async def initiate_verification(self, national_id: str) -> NafathChallenge:
        tid = f"mock-txn-{uuid.uuid4().hex[:8]}"
        logger.info("MockNafath: verification initiated", national_id=national_id, transaction_id=tid)
        return NafathChallenge(
            transaction_id=tid,
            challenge_number="12",
            message="[MOCK] يرجى تأكيد الرقم 12 في تطبيق نفاذ.",
        )

    async def check_verification_status(
        self,
        transaction_id: str,
        random: str,
        national_id: str,
    ) -> NafathResult:
        jwt_token = create_access_token(
            subject=national_id,
            extra_claims={"national_id": national_id},
        )
        logger.info("MockNafath: auto-verified", national_id=national_id)
        return NafathResult(
            verified=True,
            national_id=national_id,
            user_id=national_id,
            jwt_token=jwt_token,
            message="[MOCK] Verification completed.",
            status="COMPLETED",
            access_token=f"mock-access-{uuid.uuid4().hex[:12]}",
        )

    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        return {
            "id": 1234567890,
            "full_namear": "عبدالله محمد الشمري",
            "full_nameen": "Abdullah Mohammed Al-Shammari",
            "gender": "M",
            "nationalityar": "سعودي",
            "nationalityen": "Saudi",
            "dobg": "1990-01-15",
            "id_expiry_dateg": "2030-06-20",
        }
