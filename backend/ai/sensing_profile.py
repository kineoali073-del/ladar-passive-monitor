from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any


class SensingModality(str, Enum):
    UNSPECIFIED = "UNSPECIFIED"
    COOPERATIVE_TELEMETRY = "COOPERATIVE_TELEMETRY"
    PASSIVE_EMITTER = "PASSIVE_EMITTER"
    PASSIVE_EXTERNAL_REFLECTION = "PASSIVE_EXTERNAL_REFLECTION"


class FrontEndKind(str, Enum):
    NETWORK_ADSB_FEED = "NETWORK_ADSB_FEED"
    RTL_SDR_PASSIVE = "RTL_SDR_PASSIVE"
    AD9361_PASSIVE = "AD9361_PASSIVE"
    UNSPECIFIED = "UNSPECIFIED"


@dataclass(frozen=True)
class FrontEndCapability:
    kind: FrontEndKind
    passive_only: bool = True
    supports_active_transmit: bool = False
    supports_aesa_beamforming: bool = False
    supports_multistation_tdoa_aoa: bool = False
    notes: tuple[str, ...] = ()


_PROFILES = {
    FrontEndKind.NETWORK_ADSB_FEED: FrontEndCapability(FrontEndKind.NETWORK_ADSB_FEED, notes=("readsb_json", "cooperative_telemetry")),
    FrontEndKind.RTL_SDR_PASSIVE: FrontEndCapability(FrontEndKind.RTL_SDR_PASSIVE, notes=("receive_front_end_only", "hardware_not_verified")),
    FrontEndKind.AD9361_PASSIVE: FrontEndCapability(FrontEndKind.AD9361_PASSIVE, notes=("receive_front_end_only", "external_processing_and_sync_required")),
    FrontEndKind.UNSPECIFIED: FrontEndCapability(FrontEndKind.UNSPECIFIED, notes=("front_end_not_declared",)),
}


def classify_source_modality(source: str | None) -> SensingModality:
    normalized = (source or "").strip().lower()
    if normalized in {"readsb", "dump1090", "adsb"}:
        return SensingModality.COOPERATIVE_TELEMETRY
    if normalized in {"bistatic", "external_reflection", "passive_radar"}:
        return SensingModality.PASSIVE_EXTERNAL_REFLECTION
    if normalized in {"passive_rf", "emitter_rf", "sigint_rx"}:
        return SensingModality.PASSIVE_EMITTER
    return SensingModality.UNSPECIFIED


def resolve_front_end_capability(raw_kind: str | None) -> FrontEndCapability:
    try:
        return _PROFILES[FrontEndKind(raw_kind.strip().upper())] if isinstance(raw_kind, str) else _PROFILES[FrontEndKind.UNSPECIFIED]
    except ValueError:
        return _PROFILES[FrontEndKind.UNSPECIFIED]


def compute_radio_horizon_km(receiver_height_m: float | None, target_height_m: float | None) -> float | None:
    def valid(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    receiver, target = valid(receiver_height_m), valid(target_height_m)
    if receiver is None or target is None:
        return None
    return round(4.12 * (math.sqrt(receiver) + math.sqrt(target)), 2)
