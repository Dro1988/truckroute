"""Trip planning math: fuel range, fuel stops estimate, HOS estimates.

All HOS figures are planning ESTIMATES, never ELD data.
"""
from __future__ import annotations

MILES_PER_METER = 1 / 1609.344


def fuel_for_trip(distance_miles: float, avg_mpg: float) -> float:
    if avg_mpg <= 0:
        return 0.0
    return round(distance_miles / avg_mpg, 1)


def remaining_range_miles(fuel_capacity_gal: float, fuel_level_pct: float, avg_mpg: float) -> float:
    usable = fuel_capacity_gal * max(0.0, min(100.0, fuel_level_pct)) / 100.0
    return round(usable * avg_mpg, 1)


def fuel_stops_needed(
    distance_miles: float,
    range_miles: float,
    reserve_pct: float = 15.0,
) -> int:
    """How many fuel stops a trip needs, keeping a reserve in the tank."""
    usable_range = range_miles * (1 - reserve_pct / 100.0)
    if usable_range <= 0:
        return 0
    if distance_miles <= usable_range:
        return 0
    stops = 0
    remaining = distance_miles - usable_range
    while remaining > 0:
        stops += 1
        remaining -= usable_range
    return stops


def hos_estimate(
    drive_minutes: float,
    available_drive_hours: float | None,
    break_interval_hours: float | None = None,
) -> dict:
    """Estimate whether the trip fits the driver's available hours.

    Returns dict with fits_drive_window (bool|None), breaks_needed,
    and a human summary. Everything is an estimate.
    """
    drive_hours = drive_minutes / 60.0
    result: dict = {
        "drive_hours": round(drive_hours, 1),
        "fits_drive_window": None,
        "breaks_needed": 0,
        "summary": "",
    }
    if break_interval_hours and break_interval_hours > 0:
        result["breaks_needed"] = int(drive_hours // break_interval_hours)
    if available_drive_hours is not None and available_drive_hours > 0:
        fits = drive_hours <= available_drive_hours
        result["fits_drive_window"] = fits
        if fits:
            result["summary"] = (
                f"Estimated {drive_hours:.1f}h driving fits your "
                f"{available_drive_hours:.1f}h available (estimate)."
            )
        else:
            result["summary"] = (
                f"Estimated {drive_hours:.1f}h driving EXCEEDS your "
                f"{available_drive_hours:.1f}h available (estimate). Plan a stop."
            )
    else:
        result["summary"] = f"Estimated {drive_hours:.1f}h driving (estimate)."
    if result["breaks_needed"]:
        result["summary"] += f" About {result['breaks_needed']} break(s) suggested."
    return result
