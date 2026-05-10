"""LangChain tools for SERA CRM operations.

Tools:
  - get_user_info          : Nafath profile (unchanged)
  - retrieve_case          : Case details by case number
  - get_case_types         : List available case types
  - get_sub_case_types     : Sub-types for a given case type
  - get_regions            : List regions
  - get_cities             : List cities
  - get_offices            : List offices
  - get_complainer_types   : List complainer types
  - create_case            : Create a complaint case (auto-fetches beneficiary + first account)
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from core.config import settings
from core.logging import get_logger
from schemas.crm import CreateCaseRequest
from services.crm.service import get_crm_service

logger = get_logger(__name__)

_ERR_API_TIMEOUT = (
    "الخدمة غير متاحة مؤقتاً. يرجى المحاولة لاحقاً."
)
_ERR_USER_INFO_UNAVAILABLE = (
    "معلومات الملف الشخصي غير متوفرة. يرجى إعادة التحقق."
)


def _is_timeout(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "timeout" in msg or "connect" in msg


_AR_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670]")


def normalize_ar(s: str) -> str:
    """Normalize Arabic text: strip tashkeel, unify alif/ya/ta-marbuta, lowercase."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = _AR_DIACRITICS.sub("", s)
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    s = s.replace("ى", "ي").replace("ة", "ه")
    s = re.sub(r"\s+", " ", s)
    return s


def _resolve_name_to_key(items: List[Any], name: str) -> Optional[str]:
    """Resolve a human-readable value name to its GUID key.

    Strategy: exact (normalized) match → substring match with a single candidate.
    Returns None if nothing matches, or if the substring match is ambiguous.
    """
    if not name:
        return None
    target = normalize_ar(name)
    for item in items:
        if normalize_ar(item.value) == target:
            return item.key
    candidates = [
        it for it in items
        if target in normalize_ar(it.value) or normalize_ar(it.value) in target
    ]
    if len(candidates) == 1:
        return candidates[0].key
    return None


# ── Input schemas ─────────────────────────────────────────────────────────────

class EmptyInput(BaseModel):
    pass


class CaseNumberInput(BaseModel):
    case_number: str = Field(..., description="The complaint case number (e.g. 26011522)")


class CaseTypeNameInput(BaseModel):
    case_type_name: str = Field(..., description="The exact case type name (as returned by get_case_types) to get sub-types for")


class RegionFilterInput(BaseModel):
    region: str = Field(
        ...,
        description="Exact region name (as returned by get_regions) to fetch cities for. Required.",
    )


class CityFilterInput(BaseModel):
    city: str = Field(
        ...,
        description="Exact city name (as returned by get_cities) to fetch offices for. Required.",
    )
    region: str = Field(
        ...,
        description="Exact region name (as returned by get_regions) that the city belongs to. Required.",
    )


class CreateCaseInput(BaseModel):
    description: str = Field(..., description="Detailed description of the complaint from the user")
    region: str = Field(..., description="Exact region name as returned by get_regions. NEVER invent this.")
    city: str = Field(..., description="Exact city name as returned by get_cities. NEVER invent this.")
    office: str = Field(..., description="Exact office name as returned by get_offices. NEVER invent this.")
    case_type: str = Field(..., description="Exact case type name as returned by get_case_types. NEVER invent this.")
    sub_case_type: str = Field(..., description="Exact sub case type name as returned by get_sub_case_types. NEVER invent this.")
    originating_case_number: Optional[str] = Field("", description="Optional originating case number")
    order_number: Optional[str] = Field("", description="Optional order number")


# ── Tool implementations ──────────────────────────────────────────────────────

class GetUserInfoTool(BaseTool):
    name: str = "get_user_info"
    description: str = (
        "Get the verified user's profile information (name, nationality, ID details) "
        "from Nafath. No input needed."
    )
    args_schema: Type[BaseModel] = EmptyInput
    nafath_user_info: Optional[Dict[str, Any]] = None

    async def _arun(self) -> str:
        if not self.nafath_user_info:
            return _ERR_USER_INFO_UNAVAILABLE
        info = self.nafath_user_info
        lines = []
        if info.get("id"):
            lines.append(f"National ID: {info['id']}")
        if info.get("full_namear"):
            lines.append(f"Name (AR): {info['full_namear']}")
        if info.get("full_nameen"):
            lines.append(f"Name (EN): {info['full_nameen']}")
        if info.get("gender"):
            lines.append(f"Gender: {info['gender']}")
        nat = info.get("nationalityen") or info.get("nationalityar")
        if nat:
            lines.append(f"Nationality: {nat}")
        if info.get("dobg"):
            lines.append(f"Date of Birth: {info['dobg']}")
        if info.get("id_expiry_dateg"):
            lines.append(f"ID Expiry Date: {info['id_expiry_dateg']}")
        return "\n".join(lines) if lines else _ERR_USER_INFO_UNAVAILABLE

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


class RetrieveCaseTool(BaseTool):
    name: str = "retrieve_case"
    description: str = (
        "Get complaint case status and details by case number. "
        "Use when the user asks about their complaint status."
    )
    args_schema: Type[BaseModel] = CaseNumberInput
    national_id: str = ""

    async def _arun(self, case_number: str) -> str:
        svc = get_crm_service()

        if self.national_id:
            try:
                complaints = await svc.get_customer_active_complaints(self.national_id)
                user_case_numbers = {c.nw_name for c in complaints}
                if case_number not in user_case_numbers:
                    return "هذه الشكوى لا تخصّ حسابك."
            except Exception as exc:
                if _is_timeout(exc):
                    return _ERR_API_TIMEOUT
                logger.warning(
                    "Failed to validate case ownership, proceeding anyway",
                    case_number=case_number,
                    error=str(exc),
                )

        try:
            case_obj = await svc.get_case_details(case_number)
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Could not retrieve case {case_number}: {exc}"

        if not case_obj:
            return f"Case {case_number}: no details available."

        def _kv(field: str) -> str:
            v = case_obj.get(field)
            if isinstance(v, dict):
                return v.get("Value", "N/A")
            return str(v) if v else "N/A"

        lines = [f"Case Number: {case_obj.get('CaseNumber', case_number)}"]
        if case_obj.get("Status"):
            lines.append(f"Status: {_kv('Status')}")
        if case_obj.get("CaseStage"):
            lines.append(f"Stage: {_kv('CaseStage')}")
        if case_obj.get("CaseType"):
            lines.append(f"Case Type: {_kv('CaseType')}")
        if case_obj.get("SubCaseType"):
            lines.append(f"Sub Case Type: {_kv('SubCaseType')}")
        if case_obj.get("Region"):
            lines.append(f"Region: {_kv('Region')}")
        if case_obj.get("City"):
            lines.append(f"City: {_kv('City')}")
        if case_obj.get("Office"):
            lines.append(f"Office: {_kv('Office')}")
        if case_obj.get("ComplainterType"):
            lines.append(f"Complainer Type: {_kv('ComplainterType')}")
        if case_obj.get("Description"):
            lines.append(f"Description: {case_obj['Description']}")
        if case_obj.get("ClosedDate"):
            lines.append(f"Closed Date: {case_obj['ClosedDate']}")
        if case_obj.get("Account"):
            lines.append(f"Account: {_kv('Account')}")
        return "\n".join(lines)

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


class GetCaseTypesTool(BaseTool):
    name: str = "get_case_types"
    description: str = (
        "Fetch the list of available complaint case types. Returns a list of type names. "
        "Present the list to the user so they can choose one."
    )
    args_schema: Type[BaseModel] = EmptyInput

    async def _arun(self) -> str:
        svc = get_crm_service()
        try:
            items = await svc.get_case_types()
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to fetch case types: {exc}"
        if not items:
            return "No case types available."
        lines = [f"- {item.value}" for item in items]
        return "Available Case Types:\n" + "\n".join(lines)

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


class GetSubCaseTypesTool(BaseTool):
    name: str = "get_sub_case_types"
    description: str = (
        "Fetch sub-case-types for a given case type name. "
        "Call this after the user selects a case type from get_case_types. "
        "Pass the exact case type name as shown in the list."
    )
    args_schema: Type[BaseModel] = CaseTypeNameInput

    async def _arun(self, case_type_name: str) -> str:
        svc = get_crm_service()
        try:
            case_types = await svc.get_case_types()
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to fetch case types for lookup: {exc}"

        case_type_id = _resolve_name_to_key(case_types, case_type_name)
        if not case_type_id:
            valid = ", ".join(item.value for item in case_types)
            return f"Case type '{case_type_name}' not found. Valid case types: {valid}"

        try:
            items = await svc.get_sub_case_types(case_type_id)
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to fetch sub case types: {exc}"
        if not items:
            return "No sub-case-types available for this case type."
        lines = [f"- {item.value}" for item in items]
        return "Available Sub Case Types:\n" + "\n".join(lines)

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


class GetRegionsTool(BaseTool):
    name: str = "get_regions"
    description: str = (
        "Fetch the list of available regions. Returns a list of region names. "
        "Present the list to the user so they can choose their region."
    )
    args_schema: Type[BaseModel] = EmptyInput

    async def _arun(self) -> str:
        svc = get_crm_service()
        try:
            items = await svc.get_regions()
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to fetch regions: {exc}"
        if not items:
            return "No regions available."
        lines = [f"- {item.value}" for item in items]
        return "Available Regions:\n" + "\n".join(lines)

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


class GetCitiesTool(BaseTool):
    name: str = "get_cities"
    description: str = (
        "Fetch the list of cities for a given region. "
        "You MUST pass the exact region name (as returned by get_regions). "
        "Present the list to the user so they can choose their city."
    )
    args_schema: Type[BaseModel] = RegionFilterInput

    async def _arun(self, region: str) -> str:
        svc = get_crm_service()
        try:
            regions = await svc.get_regions()
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to fetch regions for lookup: {exc}"

        region_id = _resolve_name_to_key(regions, region)
        if not region_id:
            valid = ", ".join(it.value for it in regions)
            return f"Region '{region}' not found. Valid regions: {valid}"

        try:
            items = await svc.get_cities(region_id)
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to fetch cities: {exc}"
        if not items:
            return "No cities available for this region."
        lines = [f"- {item.value}" for item in items]
        return "Available Cities:\n" + "\n".join(lines)

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


class GetOfficesTool(BaseTool):
    name: str = "get_offices"
    description: str = (
        "Fetch the list of offices for a given city. "
        "You MUST pass the exact city name (as returned by get_cities) "
        "and the exact region name (as returned by get_regions). "
        "Present the list to the user so they can choose the office."
    )
    args_schema: Type[BaseModel] = CityFilterInput

    async def _arun(self, city: str, region: str) -> str:
        svc = get_crm_service()
        try:
            regions = await svc.get_regions()
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to fetch regions for lookup: {exc}"

        region_id = _resolve_name_to_key(regions, region)
        if not region_id:
            valid = ", ".join(it.value for it in regions)
            return f"Region '{region}' not found. Valid regions: {valid}"

        try:
            cities = await svc.get_cities(region_id)
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to fetch cities for lookup: {exc}"

        city_id = _resolve_name_to_key(cities, city)
        if not city_id:
            valid = ", ".join(it.value for it in cities)
            return f"City '{city}' not found in region '{region}'. Valid cities: {valid}"

        try:
            items = await svc.get_offices(city_id)
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to fetch offices: {exc}"
        if not items:
            return "No offices available for this city."
        lines = [f"- {item.value}" for item in items]
        return "Available Offices:\n" + "\n".join(lines)

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


class GetComplainerTypesTool(BaseTool):
    name: str = "get_complainer_types"
    description: str = (
        "Fetch the list of available complainer types. "
        "Present the list to the user so they can choose their complainer type."
    )
    args_schema: Type[BaseModel] = EmptyInput

    async def _arun(self) -> str:
        svc = get_crm_service()
        try:
            items = await svc.get_complainer_types()
        except Exception as exc:
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to fetch complainer types: {exc}"
        if not items:
            return "No complainer types available."
        lines = [f"- {item.name}" for item in items]
        return "Available Complainer Types:\n" + "\n".join(lines)

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


class CreateCaseTool(BaseTool):
    name: str = "create_case"
    description: str = (
        "Create a new complaint case. "
        "Requires: description (from user), region name, city name, "
        "case_type name, sub_case_type name. "
        "All names MUST be exact values returned by the corresponding lookup tools — "
        "unrecognized names will be rejected. "
        "The beneficiary (contact ID) and account number are fetched automatically "
        "from the user's verified national ID. Returns the new case number on success."
    )
    args_schema: Type[BaseModel] = CreateCaseInput
    national_id: str = ""

    async def _resolve_names_to_guids(
        self,
        svc: Any,
        region: str,
        city: str,
        office: str,
        case_type: str,
        sub_case_type: str,
    ) -> tuple[Optional[Dict[str, str]], Optional[str]]:
        """Resolve human-readable names to GUIDs.

        Returns (resolved_dict, None) on success, or (None, error_message) on failure.
        """
        errors: List[str] = []
        resolved: Dict[str, str] = {}

        region_id: Optional[str] = None
        try:
            items = await svc.get_regions()
            region_id = _resolve_name_to_key(items, region)
            if region_id:
                resolved["region"] = region_id
            else:
                valid = ", ".join(it.value for it in items)
                errors.append(f"Region '{region}' not found. Valid regions: {valid}")
        except Exception as exc:
            logger.warning("Name resolution: failed to fetch regions", error=str(exc))
            errors.append(f"Failed to look up region '{region}': {exc}")

        city_id: Optional[str] = None
        if region_id:
            try:
                items = await svc.get_cities(region_id)
                city_id = _resolve_name_to_key(items, city)
                if city_id:
                    resolved["city"] = city_id
                else:
                    valid = ", ".join(it.value for it in items)
                    errors.append(f"City '{city}' not found. Valid cities: {valid}")
            except Exception as exc:
                logger.warning("Name resolution: failed to fetch cities", error=str(exc))
                errors.append(f"Failed to look up city '{city}': {exc}")
        elif not any("Region" in e for e in errors):
            errors.append(f"Cannot resolve city because region '{region}' is invalid.")

        if city_id:
            try:
                items = await svc.get_offices(city_id)
                key = _resolve_name_to_key(items, office)
                if key:
                    resolved["office"] = key
                else:
                    valid = ", ".join(it.value for it in items)
                    errors.append(f"Office '{office}' not found. Valid offices: {valid}")
            except Exception as exc:
                logger.warning("Name resolution: failed to fetch offices", error=str(exc))
                errors.append(f"Failed to look up office '{office}': {exc}")
        elif not any("City" in e or "Region" in e for e in errors):
            errors.append(f"Cannot resolve office because city '{city}' is invalid.")

        try:
            items = await svc.get_case_types()
            key = _resolve_name_to_key(items, case_type)
            if key:
                resolved["case_type"] = key
            else:
                valid = ", ".join(it.value for it in items)
                errors.append(f"Case type '{case_type}' not found. Valid case types: {valid}")
        except Exception as exc:
            logger.warning("Name resolution: failed to fetch case types", error=str(exc))
            errors.append(f"Failed to look up case type '{case_type}': {exc}")

        ct_guid = resolved.get("case_type", "")
        if ct_guid:
            try:
                items = await svc.get_sub_case_types(ct_guid)
                key = _resolve_name_to_key(items, sub_case_type)
                if key:
                    resolved["sub_case_type"] = key
                else:
                    valid = ", ".join(it.value for it in items)
                    errors.append(
                        f"Sub case type '{sub_case_type}' not found for case type '{case_type}'. "
                        f"Valid sub case types: {valid}"
                    )
            except Exception as exc:
                logger.warning("Name resolution: failed to fetch sub case types", error=str(exc))
                errors.append(f"Failed to look up sub case type '{sub_case_type}': {exc}")
        elif not any("Case type" in e for e in errors):
            errors.append(
                f"Cannot resolve sub case type because case type '{case_type}' is invalid."
            )

        if errors:
            msg = (
                "REJECTED: Could not resolve the following names. "
                "Use the exact names from the lookup tools and call create_case again.\n\n"
                + "\n\n".join(errors)
            )
            return None, msg
        return resolved, None

    async def _arun(
        self,
        description: str,
        region: str,
        city: str,
        office: str,
        case_type: str,
        sub_case_type: str,
        originating_case_number: Optional[str] = "",
        order_number: Optional[str] = "",
    ) -> str:
        if not self.national_id:
            return "National ID is missing. Please complete identity verification first."

        svc = get_crm_service()

        resolved, error = await self._resolve_names_to_guids(
            svc, region, city, office, case_type, sub_case_type,
        )
        if error:
            logger.warning(
                "create_case rejected: could not resolve names",
                region=region, city=city, office=office,
                case_type=case_type, sub_case_type=sub_case_type,
            )
            return error

        try:
            beneficiary = await svc.get_contact_id(self.national_id)
        except Exception as exc:
            logger.error("Failed to get contact ID", error=str(exc))
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to get beneficiary contact ID: {exc}"

        account_number = ""
        try:
            accounts = await svc.get_customer_account_list(self.national_id)
            if accounts:
                account_number = str(accounts[0])
        except Exception as exc:
            logger.warning("Failed to get customer accounts, proceeding without", error=str(exc))

        request = CreateCaseRequest(
            channelId=settings.CRM_CHANNEL_ID,
            origatingCaseNumber=originating_case_number or "",
            orderNumber=order_number or "",
            accountNumber=account_number,
            attachmentsObject=None,
            complainterType=settings.CRM_COMPLAINTER_TYPE,
            beneficiary=beneficiary,
            descriptionParam=description,
            isTheConsumerHasPriority=False,
            isThereComplaint=1,
            region=resolved["region"],
            city=resolved["city"],
            office=resolved["office"],
            caseType=resolved["case_type"],
            subCaseType=resolved["sub_case_type"],
            against=settings.CRM_AGAINST,
        )

        logger.info(
            "create_case: resolved GUIDs",
            region=resolved["region"],
            city=resolved["city"],
            office=resolved["office"],
            case_type=resolved["case_type"],
            sub_case_type=resolved["sub_case_type"],
            beneficiary=beneficiary,
        )

        try:
            result = await svc.create_case(request)
        except Exception as exc:
            logger.error("Failed to create case", error=str(exc))
            if _is_timeout(exc):
                return _ERR_API_TIMEOUT
            return f"Failed to create case: {exc}"

        if result.isSuccess:
            return (
                f"Case created successfully!\n"
                f"Case Number: {result.caseId}\n"
                f"Message: {result.message or 'Completed'}"
            )
        return f"Case creation failed: {result.message or 'Unknown error'}"

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


# ── Builder ───────────────────────────────────────────────────────────────────

def build_crm_tools(
    user_id: str,
    nafath_user_info: Optional[Dict[str, Any]] = None,
    national_id: Optional[str] = None,
) -> list[BaseTool]:
    """Return all CRM tools with user context injected from Nafath verification."""
    return [
        GetUserInfoTool(nafath_user_info=nafath_user_info),
        RetrieveCaseTool(national_id=national_id or ""),
        GetCaseTypesTool(),
        GetSubCaseTypesTool(),
        GetRegionsTool(),
        GetCitiesTool(),
        GetOfficesTool(),
        GetComplainerTypesTool(),
        CreateCaseTool(national_id=national_id or ""),
    ]
