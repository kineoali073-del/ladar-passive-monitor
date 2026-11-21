from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

EARTH_RADIUS_M = 6_371_000.0
PriorityLevel = Literal["LOW", "MEDIUM", "HIGH", "UNVERIFIED"]
ApproachTrend = Literal["APPROACHING", "RECEDING", "STEADY", "UNKNOWN"]


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


@dataclass(frozen=True)
class Point2D:
    lat_deg: float
    lon_deg: float


@dataclass(frozen=True)
class KinematicObservation:
    identifier: str
    source: str
    timestamp_sec: float
    lat_deg: float | None = None
    lon_deg: float | None = None
    altitude_m: float | None = None
    ground_speed_mps: float | None = None
    track_deg: float | None = None


@dataclass(frozen=True)
class ThreatAssessmentResult:
    """Rule-based observation priority, never a probability or threat determination."""
    identifier: str
    source: str
    threat_level: PriorityLevel
    score: float
    range_m: float | None
    closing_speed_mps: float | None
    approach_trend: ApproachTrend
    reasons: tuple[str, ...]


def _valid_point(point: Point2D | None) -> bool:
    return bool(point and _finite(point.lat_deg) and _finite(point.lon_deg)
                and -90 <= point.lat_deg <= 90 and -180 <= point.lon_deg <= 180)


def haversine_distance_m(origin: Point2D, target: Point2D) -> float | None:
    if not _valid_point(origin) or not _valid_point(target):
        return None
    lat1, lon1 = math.radians(origin.lat_deg), math.radians(origin.lon_deg)
    lat2, lon2 = math.radians(target.lat_deg), math.radians(target.lon_deg)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(max(0.0, a)), math.sqrt(max(0.0, 1.0 - a)))


def compute_radio_horizon_km(receiver_height_m: float | None, target_height_m: float | None) -> float | None:
    if not (_finite(receiver_height_m) and _finite(target_height_m)):
        return None
    assert receiver_height_m is not None and target_height_m is not None
    if receiver_height_m < 0 or target_height_m < 0:
        return None
    return round(4.12 * (math.sqrt(receiver_height_m) + math.sqrt(target_height_m)), 2)


class ThreatAssessmentEngine:
    """Computes geometry and a transparent observation-priority heuristic."""
    def __init__(self, sensor_location: Point2D | None, *, receiver_height_m: float | None = None) -> None:
        self.sensor_location = sensor_location if _valid_point(sensor_location) else None
        self.receiver_height_m = receiver_height_m if _finite(receiver_height_m) and receiver_height_m is not None and receiver_height_m >= 0 else None

    @staticmethod
    def _unverified(obs: KinematicObservation, reason: str) -> ThreatAssessmentResult:
        return ThreatAssessmentResult(obs.identifier, obs.source, "UNVERIFIED", 0.0, None, None, "UNKNOWN", (reason,))

    def assess(self, current: KinematicObservation, previous: KinematicObservation | None = None) -> ThreatAssessmentResult:
        if not _finite(current.timestamp_sec) or current.timestamp_sec <= 0:
            return self._unverified(current, "missing_or_invalid_timestamp")
        if self.sensor_location is None:
            return self._unverified(current, "missing_sensor_location")
        target = Point2D(current.lat_deg, current.lon_deg) if current.lat_deg is not None and current.lon_deg is not None else None
        if not _valid_point(target):
            return self._unverified(current, "missing_or_invalid_target_position")
        assert target is not None
        distance = haversine_distance_m(self.sensor_location, target)
        if distance is None:
            return self._unverified(current, "invalid_coordinates")

        reasons = ["valid_position_and_timestamp"]
        closing: float | None = None
        trend: ApproachTrend = "UNKNOWN"
        if previous is not None:
            p = Point2D(previous.lat_deg, previous.lon_deg) if previous.lat_deg is not None and previous.lon_deg is not None else None
            dt = current.timestamp_sec - previous.timestamp_sec if _finite(previous.timestamp_sec) else 0.0
            if previous.source == current.source and previous.identifier == current.identifier and dt > 1e-3 and _valid_point(p):
                assert p is not None
                old_distance = haversine_distance_m(self.sensor_location, p)
                if old_distance is not None:
                    closing = (old_distance - distance) / dt
                    if closing > 1.0:
                        trend = "APPROACHING"
                        reasons.append("range_decreasing")
                    elif closing < -1.0:
                        trend = "RECEDING"
                        reasons.append("range_increasing")
                    else:
                        trend = "STEADY"
                        reasons.append("range_steady")
            else:
                reasons.append("previous_observation_invalid_or_mismatched")
        else:
            reasons.append("no_prior_observation")

        horizon = compute_radio_horizon_km(self.receiver_height_m, current.altitude_m)
        if horizon is not None:
            reasons.append(f"radio_horizon_km={horizon:.2f}")
        score = 10.0
        if distance <= 50_000:
            score += 35.0
            reasons.append("within_50km_band")
        elif distance <= 150_000:
            score += 20.0
            reasons.append("within_150km_band")
        if closing is not None and closing > 1.0:
            score += min(35.0, closing * 0.25)
            reasons.append("positive_closing_rate")
        score = round(min(score, 100.0), 2)
        level: PriorityLevel = "HIGH" if score >= 65 else "MEDIUM" if score >= 35 else "LOW"
        return ThreatAssessmentResult(current.identifier, current.source, level, score, round(distance, 1), round(closing, 2) if closing is not None else None, trend, tuple(reasons))
