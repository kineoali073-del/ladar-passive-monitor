from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _iso_utc(epoch: float | None) -> str | None:
    if epoch is None or epoch <= 0:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def map_readsb_payload(payload: Any, *, received_at_utc: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("aircraft"), list):
        raise ValueError("Invalid readsb payload: expected object with an aircraft list")
    feed_now = _finite(payload.get("now"))
    if feed_now is not None and feed_now <= 0:
        feed_now = None
    records: list[dict[str, Any]] = []
    for raw in payload["aircraft"]:
        if not isinstance(raw, dict):
            continue
        hex_value = raw.get("hex")
        if not isinstance(hex_value, str) or not hex_value.strip():
            continue
        identifier = hex_value.strip().lower()
        lat, lon = _finite(raw.get("lat")), _finite(raw.get("lon"))
        if lat is None or not -90 <= lat <= 90 or lon is None or not -180 <= lon <= 180:
            lat = lon = None
        track = _finite(raw.get("track"))
        if track is not None and not 0 <= track <= 360:
            track = None
        seen_pos = _finite(raw.get("seen_pos"))
        observed = None
        if feed_now is not None and seen_pos is not None and seen_pos >= 0:
            candidate = feed_now - seen_pos
            if candidate > 0 and _iso_utc(candidate) is not None:
                observed = candidate
        flight = raw.get("flight")
        altitude, speed = _finite(raw.get("alt_baro")), _finite(raw.get("gs"))
        records.append({"record_type": "LIVE", "source": "readsb", "identifier": identifier, "hex": identifier,
                        "callsign": flight.strip() if isinstance(flight, str) and flight.strip() else None,
                        "latitude": lat, "longitude": lon, "lat": lat, "lon": lon,
                        "altitude_ft": altitude, "alt_baro": altitude, "ground_speed_kts": speed, "gs": speed,
                        "track_deg": track, "track": track, "observed_at_sec": observed,
                        "observed_at": _iso_utc(observed), "received_at_utc": received_at_utc})
    return records
