from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _coordinates(lat_value: Any, lon_value: Any) -> tuple[float | None, float | None]:
    lat = _finite_float(lat_value)
    lon = _finite_float(lon_value)
    if lat is None or lon is None or not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None, None
    return lat, lon


def _observed_at_sec(entry: dict[str, Any], now_sec: float | None) -> float | None:
    """Use readsb's explicit position age; never substitute receive/publish time."""
    if now_sec is None or now_sec <= 0.0:
        return None

    for key in ("seen_pos", "seen"):
        if key not in entry or entry[key] is None:
            continue
        age_sec = _finite_float(entry[key])
        if age_sec is None or age_sec < 0.0:
            return None
        observed = now_sec - age_sec
        return observed if observed > 0.0 else None
    return None


def _iso_utc(epoch_sec: float | None) -> str | None:
    if epoch_sec is None:
        return None
    try:
        return datetime.fromtimestamp(epoch_sec, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def map_readsb_payload(
    payload: Any,
    *,
    received_at_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize a readsb aircraft.json snapshot; this function never accesses RF hardware."""
    if not isinstance(payload, dict):
        raise ValueError("readsb payload root must be a JSON object")
    aircraft = payload.get("aircraft")
    if not isinstance(aircraft, list):
        raise ValueError("readsb payload must contain an 'aircraft' list")

    now_sec = _finite_float(payload.get("now"))
    if now_sec is not None and now_sec <= 0.0:
        now_sec = None

    updated_at_utc = (
        received_at_utc.strip()
        if isinstance(received_at_utc, str) and received_at_utc.strip()
        else datetime.now(timezone.utc).isoformat()
    )

    mapped: list[dict[str, Any]] = []
    for entry in aircraft:
        if not isinstance(entry, dict):
            continue
        raw_identifier = entry.get("hex")
        if not isinstance(raw_identifier, str) or not raw_identifier.strip():
            continue

        lat, lon = _coordinates(entry.get("lat"), entry.get("lon"))
        observed_at_sec = _observed_at_sec(entry, now_sec)
        raw_callsign = entry.get("flight")
        callsign = raw_callsign.strip() if isinstance(raw_callsign, str) and raw_callsign.strip() else None

        mapped.append(
            {
                "record_type": "LIVE",
                "source": "readsb",
                "sensing_modality": "COOPERATIVE_TELEMETRY",
                "identifier": raw_identifier.strip().lower(),
                "callsign": callsign,
                "lat": lat,
                "lon": lon,
                "alt_baro": _finite_float(entry.get("alt_baro")),
                "gs": _finite_float(entry.get("gs")),
                "track": _finite_float(entry.get("track")),
                "observed_at_sec": observed_at_sec,
                "observed_at": _iso_utc(observed_at_sec),
                "updated_at_utc": updated_at_utc,
                "observation_priority": {
                    "assessment_mode": "PASSIVE_OBSERVATION_ONLY",
                    "priority_level": "UNVERIFIED",
                    "priority_score": None,
                    "range_m": None,
                    "closing_speed_mps": None,
                    "approach_trend": "UNKNOWN",
                    "reasons": ["priority_engine_not_configured"],
                },
            }
        )
    return mapped
