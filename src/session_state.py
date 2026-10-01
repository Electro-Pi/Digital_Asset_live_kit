"""Session state for one call with Alawaly Real Estate voice assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ClientInterest:
    """Client preferences and registration of interest details."""
    name: str = ""
    phone: str = ""
    identity_type: str = ""  # مواطن, مقيم, زائر, مستثمر دولي
    buying_plan: str = ""  # كاش, تمويل عقاري, دعم سكني
    work_entity: str = ""
    email: str = ""
    is_sakani_eligible: Optional[bool] = None
    monthly_income: str = ""
    preferred_city: str = ""  # الرياض, جدة, مكة, إلخ
    preferred_type: str = ""  # شقة, فيلا, دوبلكس, روف, مكاتب, صالات عرض
    purpose: str = ""  # سكن, استثمار
    notes: str = ""


@dataclass
class SessionState:
    """Session state for one voice call."""
    current_page: str = "/ar"
    current_project_id: str = ""
    client_interest: ClientInterest = field(default_factory=ClientInterest)
    turn_counter: int = 0
    variant_picks: dict[str, int] = field(default_factory=dict)

    def pick_variant(self, key: str, count: int) -> int:
        """Deterministic rotation across phrasing variants so the caller never hears exact duplicate phrases."""
        if count <= 0:
            return 0
        idx = self.variant_picks.get(key, 0)
        self.variant_picks[key] = (idx + 1) % count
        return idx
