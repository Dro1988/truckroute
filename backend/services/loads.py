"""Load profiles: what kind of freight the truck is hauling, and what that
means for routing.

Honesty rules baked in here:
- A load profile is RECORDED on the truck and threaded into every route
  request, but only a provider that enforces truck restrictions (HERE
  today, Valhalla later) actually honors it.
- Oversize/overweight "permit routing" does not exist as a general
  provider feature: permits are state-issued for designated routes.
  No route this app returns is ever labeled a permit route.
"""
from __future__ import annotations

# Legal-without-a-permit baselines for interstate trucking (typical US).
LEGAL_HEIGHT_CM = 411      # 13'6"
LEGAL_WIDTH_CM = 259       # 102"
LEGAL_WEIGHT_KG = 36287    # 80,000 lb gross

LOAD_TYPES: dict[str, dict] = {
    "general": {
        "label": "General freight",
        "blurb": "Dry van / reefer, standard legal dims.",
    },
    "flatbed": {
        "label": "Flatbed / open deck",
        "blurb": "Open-deck freight; height is load-dependent.",
    },
    "tanker": {
        "label": "Tanker",
        "blurb": "Liquid/gas bulk. Hazmat class applies when placarded.",
    },
    "oversize": {
        "label": "Oversize / overweight (permit load)",
        "blurb": "Over legal dims or weight — moves only under state permits "
                 "on state-designated routes.",
    },
    "hazmat": {
        "label": "Hazmat (placarded)",
        "blurb": "Placarded hazardous materials. Set the UN class below.",
    },
}

# UN hazmat classes -> HERE Routing v8 truck[shippedHazardousGoods] values.
HAZMAT_CLASSES: dict[str, dict] = {
    "1": {"label": "Class 1 — Explosives", "here": "explosive"},
    "2": {"label": "Class 2 — Gases", "here": "gas"},
    "3": {"label": "Class 3 — Flammable liquids", "here": "flammable"},
    "4": {"label": "Class 4 — Flammable solids", "here": "flammable"},
    "5": {"label": "Class 5 — Oxidizers / organic peroxides", "here": "organic"},
    "6": {"label": "Class 6 — Toxic / poison", "here": "poison"},
    "7": {"label": "Class 7 — Radioactive", "here": "radioactive"},
    "8": {"label": "Class 8 — Corrosive", "here": "corrosive"},
    "9": {"label": "Class 9 — Miscellaneous", "here": "other"},
}


def normalize_load_type(value: str | None) -> str:
    key = (value or "general").strip().lower()
    return key if key in LOAD_TYPES else "general"


def normalize_hazmat_class(value: str | None) -> str | None:
    if value is None:
        return None
    key = str(value).strip()
    return key if key in HAZMAT_CLASSES else None


def here_hazmat_value(hazmat_class: str | None, legacy_hazmat: str | None) -> str | None:
    """Resolve the HERE shippedHazardousGoods value: UN class wins; a legacy
    free-text hazmat string (older profiles) falls back to HERE's map."""
    cls = normalize_hazmat_class(hazmat_class)
    if cls:
        return HAZMAT_CLASSES[cls]["here"]
    if not legacy_hazmat:
        return None
    legacy = {
        "explosive": "explosive", "gas": "gas", "flammable": "flammable",
        "combustible": "combustible", "organic": "organic", "poison": "poison",
        "radioactive": "radioactive", "corrosive": "corrosive",
        "diesel": None, "none": None, "": None,
    }
    key = legacy_hazmat.strip().lower()
    return legacy.get(key, key)


def load_summary(spec) -> str:
    """One-line human summary of the load a TruckSpec carries."""
    lt = normalize_load_type(getattr(spec, "load_type", "general"))
    parts = [LOAD_TYPES[lt]["label"]]
    cls = normalize_hazmat_class(getattr(spec, "hazmat_class", None))
    if cls:
        parts.append(HAZMAT_CLASSES[cls]["label"])
    elif getattr(spec, "hazmat", None):
        parts.append(f"hazmat: {spec.hazmat}")
    ft = spec.height_cm / 30.48
    parts.append(
        f"{int(ft)}'{round((ft - int(ft)) * 12)}\" H · "
        f"{round(spec.weight_kg * 2.20462):,} lb · {spec.axle_count} axles"
    )
    return " · ".join(parts)


def is_special_load(spec) -> bool:
    """A load whose restrictions a driver can get in real trouble ignoring:
    permit-type loads, placarded hazmat, or over-legal dims/weight."""
    lt = normalize_load_type(getattr(spec, "load_type", "general"))
    if lt in ("oversize", "hazmat"):
        return True
    if normalize_hazmat_class(getattr(spec, "hazmat_class", None)):
        return True
    if getattr(spec, "hazmat", None):
        return True
    return (
        spec.height_cm > LEGAL_HEIGHT_CM
        or spec.width_cm > LEGAL_WIDTH_CM
        or spec.weight_kg > LEGAL_WEIGHT_KG
    )


def needs_permit(spec) -> bool:
    lt = normalize_load_type(getattr(spec, "load_type", "general"))
    return lt == "oversize" or (
        spec.height_cm > LEGAL_HEIGHT_CM
        or spec.width_cm > LEGAL_WIDTH_CM
        or spec.weight_kg > LEGAL_WEIGHT_KG
    )


def load_warnings(spec, truck_safe: bool) -> list[str]:
    """Warnings to attach to a calculated route for this load. These are
    phrased so the app never claims more enforcement than happened."""
    out: list[str] = []
    summary = load_summary(spec)
    if needs_permit(spec):
        out.append(
            "Permit load: oversize/overweight moves require state-issued permits "
            "and the designated routes on them. This is NOT a permit route — "
            "follow your permit, not this line, where they differ."
        )
    if not truck_safe and is_special_load(spec):
        out.insert(
            0,
            f"⚠ Load profile NOT enforced: your {summary} is recorded, but the "
            "current routing engine does not check height, weight, or hazmat "
            "restrictions. This route may use roads your load cannot legally "
            "or physically use — verify every leg before driving.",
        )
    return out
