"""Usage + cost tracking. Every provider call is logged so the admin
dashboard can show API spend per user, per provider, per operation."""
from __future__ import annotations

from sqlalchemy.orm import Session

import models
from config import get_settings

# Estimated USD per 1,000 calls, by provider operation.
# HERE freemium covers ~30k txns/mo free; these are the paid-tier rates used
# for cost modeling. Dev (Nominatim/OSRM) is $0 but still counted.
COST_PER_1K: dict[tuple[str, str], float] = {
    ("here", "route"): 0.50,
    ("here", "geocode"): 0.50,
    ("here", "suggest"): 0.50,
    ("here", "discover"): 0.50,
    ("dev-osrm", "route"): 0.0,
    ("dev-nominatim", "geocode"): 0.0,
    ("dev-nominatim", "suggest"): 0.0,
}


def log_usage(
    db: Session,
    provider: str,
    operation: str,
    user_id: str | None = None,
    count: int = 1,
    meta: dict | None = None,
) -> None:
    per_1k = COST_PER_1K.get((provider, operation), get_settings().HERE_COST_PER_1K)
    db.add(
        models.UsageRecord(
            user_id=user_id,
            provider=provider,
            operation=operation,
            count=count,
            est_cost_usd=round(per_1k * count / 1000, 6),
            meta=meta or {},
        )
    )
    db.commit()
