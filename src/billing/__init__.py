"""Server-side billing / quota accounting for the Coda LiveKit agent.

The agent is the only component that knows how long a live conversation
actually ran, so it is the authoritative meter for the per-user live-minutes
budget. This package talks to Supabase's SECURITY DEFINER RPCs using the
service-role key (server-only) to read the remaining budget and record
consumed seconds.
"""

from .supabase_billing import (
    SupabaseBilling,
    billing_from_env,
    parse_participant_budget,
)

__all__ = [
    "SupabaseBilling",
    "billing_from_env",
    "parse_participant_budget",
]
