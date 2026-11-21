from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any


class SQLiteContactsService:
    """SQLite persistence with atomic LIVE snapshots and separate REFERENCE rows."""
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS contacts(record_type TEXT NOT NULL CHECK(record_type IN ('LIVE','REFERENCE')),source TEXT NOT NULL,identifier TEXT NOT NULL,updated_at_utc TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(record_type,source,identifier))")
            conn.execute("CREATE TABLE IF NOT EXISTS source_status(id INTEGER PRIMARY KEY CHECK(id=1),connected INTEGER NOT NULL,last_error TEXT,updated_at_utc TEXT)")
            conn.execute("INSERT OR IGNORE INTO source_status(id,connected) VALUES(1,0)")

    async def set_source_status(self, *, connected: bool, last_error: str | None, updated_at_utc: str | None = None) -> None:
        async with self._lock:
            await asyncio.to_thread(self._set_status, connected, last_error, updated_at_utc)

    def _set_status(self, connected: bool, error: str | None, updated: str | None) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE source_status SET connected=?,last_error=?,updated_at_utc=COALESCE(?,updated_at_utc) WHERE id=1", (int(connected), error, updated))

    async def upsert_live_contacts(self, contacts: list[dict[str, Any]], *, updated_at_utc: str, clear_previous_live: bool = True) -> None:
        prepared: list[tuple[str, str, str, str]] = []
        for item in contacts:
            if not isinstance(item, dict) or item.get("record_type") != "LIVE":
                continue
            source, identifier = item.get("source"), item.get("identifier")
            if not isinstance(source, str) or not source.strip() or not isinstance(identifier, str) or not identifier.strip():
                continue
            prepared.append((source.strip(), identifier.strip(), updated_at_utc, json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":"))))
        async with self._lock:
            await asyncio.to_thread(self._replace_live, prepared, clear_previous_live)

    def _replace_live(self, rows: list[tuple[str, str, str, str]], clear: bool) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if clear:
                    conn.execute("DELETE FROM contacts WHERE record_type='LIVE'")
                conn.executemany("INSERT INTO contacts(record_type,source,identifier,updated_at_utc,payload_json) VALUES('LIVE',?,?,?,?) ON CONFLICT(record_type,source,identifier) DO UPDATE SET updated_at_utc=excluded.updated_at_utc,payload_json=excluded.payload_json", rows)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    async def upsert_reference_contact(self, contact: dict[str, Any], *, updated_at_utc: str) -> None:
        if not isinstance(contact, dict) or contact.get("record_type") != "REFERENCE":
            raise ValueError("contact must explicitly have record_type='REFERENCE'")
        source, identifier = contact.get("source"), contact.get("identifier")
        if not isinstance(source, str) or not source.strip() or not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("reference contact requires non-empty source and identifier")
        record = dict(contact, source=source.strip(), identifier=identifier.strip(), updated_at_utc=updated_at_utc)
        payload = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        async with self._lock:
            await asyncio.to_thread(self._save_reference, source.strip(), identifier.strip(), updated_at_utc, payload)

    def _save_reference(self, source: str, identifier: str, updated: str, payload: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO contacts(record_type,source,identifier,updated_at_utc,payload_json) VALUES('REFERENCE',?,?,?,?) ON CONFLICT(record_type,source,identifier) DO UPDATE SET updated_at_utc=excluded.updated_at_utc,payload_json=excluded.payload_json", (source, identifier, updated, payload))

    async def get_contacts(self, *, include_reference: bool = True) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._get_contacts, include_reference)

    def _get_contacts(self, include_reference: bool) -> list[dict[str, Any]]:
        query = "SELECT payload_json FROM contacts ORDER BY CASE record_type WHEN 'LIVE' THEN 0 ELSE 1 END,identifier" if include_reference else "SELECT payload_json FROM contacts WHERE record_type='LIVE' ORDER BY identifier"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        output = []
        for row in rows:
            try:
                record = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(record, dict) and record.get("record_type") in {"LIVE", "REFERENCE"}:
                output.append(record)
        return output

    async def get_status(self) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._get_status)

    def _get_status(self) -> dict[str, Any]:
        with self._connect() as conn:
            status = conn.execute("SELECT connected,last_error,updated_at_utc FROM source_status WHERE id=1").fetchone()
            live = conn.execute("SELECT COUNT(*) FROM contacts WHERE record_type='LIVE'").fetchone()[0]
            reference = conn.execute("SELECT COUNT(*) FROM contacts WHERE record_type='REFERENCE'").fetchone()[0]
        return {"connected": bool(status["connected"]), "last_error": status["last_error"], "updated_at_utc": status["updated_at_utc"], "live_contacts_count": int(live), "reference_contacts_count": int(reference), "database_configured": True, "passive_only": True, "priority_score_semantics": "observation_priority_only_not_probability"}

    async def close(self) -> None:
        return None
