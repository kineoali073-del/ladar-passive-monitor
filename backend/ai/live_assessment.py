from __future__ import annotations

import math
from typing import Any

from backend.ai.threat_assessment import KinematicObservation, ThreatAssessmentEngine, ThreatAssessmentResult

MAX_OBSERVATION_GAP_SEC = 120.0
FT_TO_M = 0.3048
KTS_TO_MPS = 0.514444


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _coordinate(value: Any, minimum: float, maximum: float) -> float | None:
    number = _finite(value)
    return number if number is not None and minimum <= number <= maximum else None


class LiveAssessmentAdapter:
    """Evaluates each distinct measured position once; score is observation priority only."""
    def __init__(self, engine: ThreatAssessmentEngine, *, max_gap_sec: float = MAX_OBSERVATION_GAP_SEC) -> None:
        self.engine = engine
        gap = _finite(max_gap_sec)
        self.max_gap_sec = gap if gap is not None and gap > 0 else MAX_OBSERVATION_GAP_SEC
        self._last_obs: dict[tuple[str, str], KinematicObservation] = {}
        self._last_timestamp: dict[tuple[str, str], float] = {}
        self._last_result: dict[tuple[str, str], ThreatAssessmentResult] = {}

    def reset(self) -> None:
        self._last_obs.clear()
        self._last_timestamp.clear()
        self._last_result.clear()

    @staticmethod
    def _unverified(identifier: str, source: str, reason: str) -> ThreatAssessmentResult:
        return ThreatAssessmentResult(identifier, source, "UNVERIFIED", 0.0, None, None, "UNKNOWN", (reason,))

    def assess_aircraft(self, contact: dict[str, Any], *, observed_at_sec: float | None) -> ThreatAssessmentResult:
        raw_id, raw_source = contact.get("identifier") or contact.get("hex"), contact.get("source")
        identifier = raw_id.strip().lower() if isinstance(raw_id, str) and raw_id.strip() else "unknown"
        source = raw_source.strip() if isinstance(raw_source, str) and raw_source.strip() else "readsb"
        timestamp = _finite(observed_at_sec)
        if timestamp is None or timestamp <= 0:
            return self._unverified(identifier, source, "missing_or_invalid_observation_timestamp")
        lat = _coordinate(contact.get("latitude", contact.get("lat")), -90.0, 90.0)
        lon = _coordinate(contact.get("longitude", contact.get("lon")), -180.0, 180.0)
        if (lat is None) != (lon is None):
            lat = lon = None
        altitude = _finite(contact.get("altitude_ft", contact.get("alt_baro")))
        speed = _finite(contact.get("ground_speed_kts", contact.get("gs")))
        track = _finite(contact.get("track_deg", contact.get("track")))
        if track is not None and not 0 <= track <= 360:
            track = None
        obs = KinematicObservation(identifier, source, timestamp, lat, lon,
                                   altitude * FT_TO_M if altitude is not None else None,
                                   speed * KTS_TO_MPS if speed is not None else None, track)
        key = (source, identifier)
        latest = self._last_timestamp.get(key)
        if latest is not None and timestamp == latest:
            return self._last_result.get(key, self._unverified(identifier, source, "duplicate_without_cached_result"))
        if latest is not None and timestamp < latest:
            return self._unverified(identifier, source, "out_of_order_observation")
        previous = self._last_obs.get(key)
        if previous is not None and not (0 < timestamp - previous.timestamp_sec <= self.max_gap_sec):
            previous = None
        result = self.engine.assess(obs, previous=previous)
        self._last_timestamp[key] = timestamp
        self._last_result[key] = result
        if lat is not None and lon is not None:
            self._last_obs[key] = obs
        else:
            self._last_obs.pop(key, None)
        return result
