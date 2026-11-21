from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("LADAR_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.getenv("LADAR_PORT", "8000"))
    except ValueError as exc:
        raise SystemExit("LADAR_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("LADAR_PORT must be between 1 and 65535")
    uvicorn.run("backend.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
