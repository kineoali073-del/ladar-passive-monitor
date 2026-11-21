from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest

from backend.ai.live_assessment import LiveAssessmentAdapter
from backend.ai.sensing_profile import SensingModality, classify_source_modality, compute_radio_horizon_km
from backend.ai.threat_assessment import Point2D, ThreatAssessmentEngine, haversine_distance_m
from backend.api.routes import ContactsBroadcaster, create_contacts_router
from backend.ingestion.readsb_mapper import map_readsb_payload
from backend.storage.contacts_service import SQLiteContactsService

T = 1_700_000_100.0


def row(**changes):
    # Test-only fixtures; never consumed by runtime feed polling.
    data = {"record_type": "LIVE", "identifier": "test123", "hex": "test123", "source": "readsb", "lat": 24.5, "lon": 46.2, "alt_baro": 15000, "gs": 250, "track": 90}
    data.update(changes)
    return data


def test_unverified_without_sensor_or_valid_measurement():
    assert LiveAssessmentAdapter(ThreatAssessmentEngine(None)).assess_aircraft(row(), observed_at_sec=T).threat_level == "UNVERIFIED"
    adapter = LiveAssessmentAdapter(ThreatAssessmentEngine(Point2D(24, 45)))
    for timestamp in (None, math.nan, math.inf, -math.inf, 0.0):
        assert adapter.assess_aircraft(row(), observed_at_sec=timestamp).threat_level == "UNVERIFIED"
    for coords in ({"lat": None, "lon": 46}, {"lat": 91, "lon": 45}, {"lat": 24, "lon": 181}):
        assert adapter.assess_aircraft(row(**coords), observed_at_sec=T + len(coords)).threat_level == "UNVERIFIED"


def test_duplicate_timestamp_cached_and_stale_rejected(monkeypatch: pytest.MonkeyPatch):
    engine = ThreatAssessmentEngine(Point2D(24, 45))
    adapter = LiveAssessmentAdapter(engine)
    count = 0
    original = engine.assess
    def counted(*args, **kwargs):
        nonlocal count
        count += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(engine, "assess", counted)
    first = adapter.assess_aircraft(row(lat=24.2, lon=45.2), observed_at_sec=T)
    assert adapter.assess_aircraft(row(lat=24.1, lon=45.1), observed_at_sec=T) == first
    assert count == 1
    assert "out_of_order_observation" in adapter.assess_aircraft(row(), observed_at_sec=T - 1).reasons
    assert adapter.assess_aircraft(row(lat=24.15, lon=45.15), observed_at_sec=T + 10).closing_speed_mps is not None


def test_reset_and_max_gap():
    adapter = LiveAssessmentAdapter(ThreatAssessmentEngine(Point2D(24, 45)), max_gap_sec=5)
    adapter.assess_aircraft(row(lat=24.2, lon=45.2), observed_at_sec=T)
    assert adapter.assess_aircraft(row(lat=24.1, lon=45.1), observed_at_sec=T + 1).closing_speed_mps is not None
    adapter.reset()
    assert adapter.assess_aircraft(row(), observed_at_sec=T + 2).closing_speed_mps is None
    assert adapter.assess_aircraft(row(), observed_at_sec=T + 20).closing_speed_mps is None


def test_mapper_rejects_bad_schema_and_uses_seen_pos_only():
    with pytest.raises(ValueError):
        map_readsb_payload({"now": T})
    assert map_readsb_payload({"now": T, "aircraft": []}) == []
    mapped = map_readsb_payload({"now": T, "aircraft": [{"hex": " ABC123 ", "lat": 24.5, "lon": 46.2, "seen": 0.1, "seen_pos": 4.5}]})
    assert mapped[0]["observed_at_sec"] == pytest.approx(T - 4.5)
    assert map_readsb_payload({"now": T, "aircraft": [{"hex": "abc123", "seen": 0.1}]})[0]["observed_at_sec"] is None


def test_sqlite_separates_live_reference_and_empty_snapshot(tmp_path: Path):
    service = SQLiteContactsService(tmp_path / "contacts.sqlite3")
    async def scenario():
        await service.upsert_reference_contact({"record_type": "REFERENCE", "source": "test", "identifier": "ref"}, updated_at_utc="2026-01-01T00:00:00Z")
        await service.upsert_live_contacts([{"record_type": "LIVE", "source": "test", "identifier": "live"}], updated_at_utc="2026-01-01T00:00:01Z")
        await service.upsert_live_contacts([], updated_at_utc="2026-01-01T00:00:02Z")
        records = await service.get_contacts()
        assert [x["record_type"] for x in records] == ["REFERENCE"]
        assert (await service.get_status())["reference_contacts_count"] == 1
    asyncio.run(scenario())


def test_geometry_sensing_and_route_contract(tmp_path: Path):
    assert haversine_distance_m(Point2D(24, 45), Point2D(24, 45)) == pytest.approx(0.0)
    assert compute_radio_horizon_km(10, 1000) is not None
    assert compute_radio_horizon_km(None, 1000) is None
    assert classify_source_modality("readsb") == SensingModality.COOPERATIVE_TELEMETRY
    assert classify_source_modality("unknown") == SensingModality.UNSPECIFIED
    router = create_contacts_router(service=SQLiteContactsService(tmp_path / "routes.sqlite3"), broadcaster=ContactsBroadcaster())
    assert {route.path for route in router.routes} >= {"/api/status", "/api/contacts", "/ws/contacts"}
