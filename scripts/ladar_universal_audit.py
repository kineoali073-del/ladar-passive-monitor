#!/usr/bin/env python3
"""Read-only LADAR hardware and ADS-B feed auditor.

This utility does not install packages, change system configuration, or transmit RF.
It checks locally visible USB/decoder information, optionally runs a short rtl_test
probe, and validates a configured readsb/dump1090 aircraft JSON endpoint.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


def run_cmd(cmd: list[str], timeout: int = 5) -> tuple[int, str, str]:
    if not shutil.which(cmd[0]):
        return -1, "", f"command not found: {cmd[0]}"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -2, "", "timeout"
    except OSError as exc:
        return -3, "", str(exc)


def detect_environment() -> dict[str, Any]:
    is_termux = os.path.isdir("/data/data/com.termux") or bool(os.environ.get("TERMUX_VERSION"))
    return {
        "os": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "is_termux": is_termux,
    }


def check_usb_and_sdr(is_termux: bool) -> dict[str, Any]:
    info: dict[str, Any] = {
        "usb_devices_raw": [],
        "rtl_sdr_matched": False,
        "matched_lines": [],
        "dvb_conflict_loaded": None,
        "rtl_test_status": "not_run",
        "rtl_test_output": "",
    }

    if is_termux:
        rc, output, error = run_cmd(["termux-usb", "-l"])
        if rc == 0 and output:
            try:
                devices = json.loads(output)
                info["usb_devices_raw"] = devices if isinstance(devices, list) else [output]
            except json.JSONDecodeError:
                info["usb_devices_raw"] = [output]
        else:
            info["usb_devices_raw"] = [error or "No USB devices reported by termux-usb"]
        # Android's USB permission/device listing does not identify an RTL-SDR by itself.
        info["rtl_test_status"] = "skipped_termux_usb_not_proof_of_sdr"
        return info

    rc, output, error = run_cmd(["lsusb"])
    if rc == 0 and output:
        lines = output.splitlines()
        info["usb_devices_raw"] = lines
        keywords = ("0bda:283", "realtek", "rtl283", "rtl-sdr", "r820t", "r828d")
        matches = [line for line in lines if any(keyword in line.lower() for keyword in keywords)]
        info["matched_lines"] = matches
        info["rtl_sdr_matched"] = bool(matches)
    else:
        info["usb_devices_raw"] = [error or "lsusb unavailable"]

    rc_mod, output_mod, _ = run_cmd(["lsmod"])
    if rc_mod == 0:
        info["dvb_conflict_loaded"] = any(
            name in output_mod.lower() for name in ("dvb_usb_rtl28xxu", "rtl2832", "rtl2830")
        )

    # Avoid competing for the receiver if a decoder is already running.
    decoder_running = any(
        subprocess.run(["pgrep", "-x", name], capture_output=True, check=False).returncode == 0
        for name in ("readsb", "dump1090", "dump1090-fa")
    ) if shutil.which("pgrep") else False

    if shutil.which("rtl_test") and info["dvb_conflict_loaded"] is not True:
        if decoder_running:
            info["rtl_test_status"] = "skipped_decoder_running"
        else:
            rc_test, stdout, stderr = run_cmd(["rtl_test", "-t"], timeout=6)
            combined = f"{stdout}\n{stderr}".strip()
            info["rtl_test_output"] = combined[:500]
            lowered = combined.lower()
            if "no supported devices found" in lowered or "no devices found" in lowered:
                info["rtl_test_status"] = "no_device_or_locked"
            elif "found" in lowered:
                info["rtl_test_status"] = "device_output_detected"
            elif rc_test == -2:
                info["rtl_test_status"] = "timed_out"
            else:
                info["rtl_test_status"] = "inconclusive"
    elif not shutil.which("rtl_test"):
        info["rtl_test_status"] = "rtl_test_unavailable"
    elif info["dvb_conflict_loaded"] is True:
        info["rtl_test_status"] = "skipped_dvb_module_loaded"

    return info


def check_adsb_feed(url: str) -> dict[str, Any]:
    feed: dict[str, Any] = {
        "url": url,
        "local_decoder_installed": [
            name for name in ("readsb", "dump1090-fa", "dump1090") if shutil.which(name)
        ],
        "json_valid": False,
        "aircraft_count": 0,
        "error": None,
    }
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LADAR-Auditor/1.0"})
        with urllib.request.urlopen(request, timeout=3) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Feed response exceeds 2 MB audit limit")
        data = json.loads(raw.decode("utf-8", errors="strict"))
        aircraft = data.get("aircraft") if isinstance(data, dict) else None
        if isinstance(aircraft, list):
            feed["json_valid"] = True
            feed["aircraft_count"] = sum(
                1 for record in aircraft if isinstance(record, dict) and record.get("hex")
            )
        else:
            feed["error"] = "JSON missing valid 'aircraft' list"
    except (OSError, ValueError, UnicodeError, urllib.error.URLError) as exc:
        feed["error"] = str(exc)
    return feed


def check_python_packages() -> dict[str, bool]:
    import importlib.util

    return {
        module: importlib.util.find_spec(module) is not None
        for module in ("fastapi", "uvicorn", "httpx", "numpy", "rtlsdr")
    }


def main() -> None:
    adsb_url = os.environ.get("READSB_URL", "http://127.0.0.1:8080/data/aircraft.json")
    environment = detect_environment()
    hardware = check_usb_and_sdr(environment["is_termux"])
    feed = check_adsb_feed(adsb_url)
    packages = check_python_packages()

    if feed["json_valid"]:
        decision = "READY_FOR_API"
    elif hardware["rtl_sdr_matched"] or hardware["rtl_test_status"] == "device_output_detected":
        decision = "HARDWARE_SEEN_FEED_NEEDED"
    elif environment["is_termux"] and hardware["usb_devices_raw"]:
        decision = "ANDROID_USB_REQUIRES_DEVICE_VERIFICATION"
    else:
        decision = "NO_FEED_OR_SDR_CONFIRMED"

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "environment": environment,
        "hardware": hardware,
        "adsb_feed": feed,
        "python_packages": packages,
        "decision": decision,
    }
    report_json = json.dumps(report, ensure_ascii=False, indent=2)
    with open("hardware_audit_report.json", "w", encoding="utf-8") as report_file:
        report_file.write(report_json + "\n")
    print(report_json)


if __name__ == "__main__":
    main()
