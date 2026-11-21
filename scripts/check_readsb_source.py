from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only check of an actual readsb aircraft JSON feed")
    parser.add_argument("--url", default="http://127.0.0.1:8080/data/aircraft.json")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        with urllib.request.urlopen(args.url, timeout=args.timeout) as response:
            body = response.read(10 * 1024 * 1024 + 1)
        if len(body) > 10 * 1024 * 1024:
            raise ValueError("feed exceeds 10 MiB limit")
        payload = json.loads(body.decode("utf-8", errors="strict"))
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"SOURCE_CHECK_FAILED: {exc}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict) or not isinstance(payload.get("aircraft"), list):
        print("INVALID_READSB_SCHEMA: expected object with aircraft list", file=sys.stderr)
        return 1
    now = payload.get("now")
    print(json.dumps({"status": "ok", "url": args.url, "has_now": isinstance(now, (int, float)) and not isinstance(now, bool), "aircraft_count": len(payload["aircraft"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
