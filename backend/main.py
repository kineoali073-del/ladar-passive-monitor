from __future__ import annotations

import asyncio
import json
import math
import os
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from backend.ai.live_assessment import LiveAssessmentAdapter
from backend.ai.sensing_profile import classify_source_modality, resolve_front_end_capability
from backend.ai.threat_assessment import Point2D, ThreatAssessmentEngine
from backend.api.routes import ContactsBroadcaster, install_contacts_api
from backend.ingestion.readsb_mapper import map_readsb_payload
from backend.storage.contacts_service import SQLiteContactsService

DEFAULT_READSB_URL = "http://127.0.0.1:8080/data/aircraft.json"


def _env_float(name: str) -> float | None:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return None
    try:
        number = float(raw.strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _sensor_location() -> Point2D | None:
    lat, lon = _env_float("LADAR_SENSOR_LAT"), _env_float("LADAR_SENSOR_LON")
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return Point2D(lat, lon)


def _fetch_readsb(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "LADAR-Passive-Monitor/1.0"})
    with urllib.request.urlopen(request, timeout=3.0) as response:
        body = response.read(10 * 1024 * 1024 + 1)
    if len(body) > 10 * 1024 * 1024:
        raise ValueError("readsb response exceeds 10 MiB")
    return json.loads(body.decode("utf-8", errors="strict"))


def create_app() -> FastAPI:
    feed_url = (os.getenv("READSB_FEED_URL") or DEFAULT_READSB_URL).strip()
    interval = max(0.5, _env_float("READSB_POLL_INTERVAL_SEC") or 2.0)
    receiver_height = _env_float("LADAR_RECEIVER_HEIGHT_M")
    if receiver_height is not None and receiver_height < 0:
        receiver_height = None
    sensor = _sensor_location()
    engine = ThreatAssessmentEngine(sensor, receiver_height_m=receiver_height)
    adapter = LiveAssessmentAdapter(engine)
    service = SQLiteContactsService(os.getenv("LADAR_DB_PATH", "data/ladar_contacts.sqlite3"))
    broadcaster = ContactsBroadcaster()
    front_end = resolve_front_end_capability(os.getenv("LADAR_FRONT_END"))

    async def poll(stop: asyncio.Event) -> None:
        while not stop.is_set():
            received = datetime.now(timezone.utc).isoformat()
            try:
                payload = await asyncio.to_thread(_fetch_readsb, feed_url)
                records = map_readsb_payload(payload, received_at_utc=received)
                assessed: list[dict[str, Any]] = []
                for record in records:
                    result = adapter.assess_aircraft(record, observed_at_sec=record["observed_at_sec"])
                    record["sensing_modality"] = classify_source_modality(record["source"]).value
                    record["observation_priority"] = {"priority_level": result.threat_level, "priority_score": result.score, "range_m": result.range_m, "closing_speed_mps": result.closing_speed_mps, "approach_trend": result.approach_trend, "reasons": list(result.reasons)}
                    assessed.append(record)
                await service.upsert_live_contacts(assessed, updated_at_utc=received, clear_previous_live=True)
                await service.set_source_status(connected=True, last_error=None, updated_at_utc=received)
                connected = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                adapter.reset()
                received = datetime.now(timezone.utc).isoformat()
                await service.upsert_live_contacts([], updated_at_utc=received, clear_previous_live=True)
                await service.set_source_status(connected=False, last_error=str(exc), updated_at_utc=received)
                connected = False
            records = await service.get_contacts(include_reference=True)
            await broadcaster.publish(contacts=records, source_connected=connected)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop = asyncio.Event()
        task = asyncio.create_task(poll(stop), name="readsb-poller")
        try:
            yield
        finally:
            stop.set()
            await task
            await service.close()

    app = FastAPI(title="LADAR Passive ADS-B Monitor", version="1.0.0", lifespan=lifespan)
    app.state.sensor_location_configured = sensor is not None
    app.state.front_end_capability = {"kind": front_end.kind.value, "passive_only": True, "supports_active_transmit": False, "supports_aesa_beamforming": False, "supports_multistation_tdoa_aoa": False, "notes": list(front_end.notes)}
    install_contacts_api(app, service=service, broadcaster=broadcaster)
    return app


app = create_app()
