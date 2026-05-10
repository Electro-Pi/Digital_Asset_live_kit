"""Pydantic schemas for SERA CRM operations."""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


# ── ESB Envelope ─────────────────────────────────────────────────────────────

class ESBResponse(BaseModel):
    """Common response wrapper returned by all SERA ESB gateway APIs."""
    status: str = ""
    errorCode: Optional[str] = None
    errorMessage: Optional[str] = None
    trace: Optional[str] = None
    data: Optional[Any] = None

    @property
    def is_success(self) -> bool:
        return self.status.upper() == "SUCCESS"


class ESBError(Exception):
    """Raised when the ESB returns a FAILURE envelope."""

    def __init__(self, code: str, message: str, trace: Optional[str] = None) -> None:
        self.code = code
        self.message = message
        self.trace = trace
        super().__init__(f"[{code}] {message}")


# ── Generic lookup item (Region, City, Office, CaseType, SubCaseType) ────────

class LookupItem(BaseModel):
    key: str = Field(..., alias="Key")
    value: str = Field(..., alias="Value")

    model_config = {"populate_by_name": True}


# ── Complainer Type ──────────────────────────────────────────────────────────

class ComplainerType(BaseModel):
    name: str
    value: int


# ── Attachment ────────────────────────────────────────────────────────────────

class CaseAttachment(BaseModel):
    Name: str
    Type: str
    Body: str


# ── Create Case ──────────────────────────────────────────────────────────────

class CreateCaseRequest(BaseModel):
    """Payload for POST /flux/v1/CreateCase."""
    attachmentsObject: Optional[CaseAttachment] = None
    channelID: str
    # origatingCaseNumber: Optional[str] = ""
    complainterType: int
    beneficiary: str
    descriptionParam: str
    isTheConsumerHasPriority: bool = False
    isThereComplaint: int = 1
    accountNumber: str = ""
    orderNumber: Optional[str] = ""
    office: str
    region: str
    city: str
    caseType: str
    subCaseType: str
    against: str


class CreateCaseResponse(BaseModel):
    """Inner data from POST /flux/v1/CreateCase (unwrapped from ESB envelope)."""
    message: Optional[str] = None
    isSuccess: bool = False
    caseId: Optional[str] = None
    surveyResponseId: Optional[str] = None


# ── Customer Account List ────────────────────────────────────────────────────

class CustomerAccount(BaseModel):
    accountNumber: str


class CustomerAccountData(BaseModel):
    """Inner data from GET /flux/v1/getCustomerAccountList."""
    customerAccounts: List[CustomerAccount] = Field(default_factory=list)


# ── Active Complaint (GetCustomerActiveComplaint) ─────────────────────────────

class ActiveComplaintBeneficiary(BaseModel):
    id: str = ""
    name: str = ""


class ActiveComplaintCaseType(BaseModel):
    id: str = ""
    name: str = ""


class ActiveComplaint(BaseModel):
    """Single item from GET /flux/v1/GetCustomerActiveComplaint response array."""
    nw_name: str = ""
    createdon: Optional[str] = None
    nw_beneficiary: Optional[ActiveComplaintBeneficiary] = None
    nw_casetype: Optional[ActiveComplaintCaseType] = None
    nw_casepriority: Optional[int] = None
    nw_actiondate: Optional[str] = None
    nw_caseid: str = ""
    nw_casesubtype: Optional[str] = None
    casetype_nw_name: Optional[str] = Field(None, alias="casetype.nw_name")
    subcase_nw_name: Optional[str] = Field(None, alias="subcase.nw_name")
    ac_fullname: Optional[str] = Field(None, alias="ac.fullname")

    model_config = {"populate_by_name": True}
