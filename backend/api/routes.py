from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, FastAPI, Query, WebSocket, WebSocketDisconnect

from backend.storage.contacts_service import SQLiteContactsService


class ContactsBroadcaster:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, *, contacts: list[dict[str, Any]], source_connected: bool, published_at_utc: str | None = None) -> None:
        message = {"type": "contacts_snapshot", "published_at_utc": published_at_utc or datetime.now(timezone.utc).isoformat(), "source_connected": bool(source_connected), "data": {"contacts": contacts}}
        async with self._lock:
            clients = tuple(self._clients)
        for queue in clients:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    async def serve(self, websocket: WebSocket, *, initial_message: dict[str, Any]) -> None:
        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        async with self._lock:
            self._clients.add(queue)
        try:
            await websocket.send_json(initial_message)
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            async with self._lock:
                self._clients.discard(queue)


def create_contacts_router(*, service: SQLiteContactsService, broadcaster: ContactsBroadcaster) -> APIRouter:
    router = APIRouter()
    @router.get("/api/status")
    async def get_status() -> dict[str, Any]:
        return await service.get_status()
    @router.get("/api/contacts")
    async def get_contacts(include_reference: bool = Query(default=True)) -> dict[str, Any]:
        contacts = await service.get_contacts(include_reference=include_reference)
        status = await service.get_status()
        return {"source_connected": status["connected"], "live_contacts_count": status["live_contacts_count"], "reference_contacts_count": status["reference_contacts_count"], "contacts": contacts}
    @router.websocket("/ws/contacts")
    async def ws_contacts(websocket: WebSocket) -> None:
        contacts = await service.get_contacts(include_reference=True)
        status = await service.get_status()
        initial = {"type": "contacts_snapshot", "published_at_utc": datetime.now(timezone.utc).isoformat(), "source_connected": bool(status["connected"]), "data": {"contacts": contacts}}
        await broadcaster.serve(websocket, initial_message=initial)
    return router


def install_contacts_api(app: FastAPI, *, service: SQLiteContactsService, broadcaster: ContactsBroadcaster) -> None:
    app.include_router(create_contacts_router(service=service, broadcaster=broadcaster))
