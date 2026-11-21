#!/usr/bin/env bash
# Read-only audit for the local ADS-B receiver/feed setup.
# Does not install packages, change kernel drivers, start services, or transmit RF.
set -u

REPORT_FILE="${REPORT_FILE:-hardware_audit_report.json}"
PASS=0
WARN=0
FAIL=0
ENV_TYPE=linux
SDR_DETECTED=false
SDR_USB_DESCRIPTION=''
SDR_TEST=not_run
ADSB_SOURCE=none
ADSB_URL="${READSB_URL:-http://127.0.0.1:8080/data/aircraft.json}"
JSON_OK=false
AIRCRAFT_COUNT=0
PYTHON_AVAILABLE=false

pass() { printf '[PASS] %s\n' "$1"; PASS=$((PASS + 1)); }
warn() { printf '[WARN] %s\n' "$1"; WARN=$((WARN + 1)); }
fail() { printf '[FAIL] %s\n' "$1"; FAIL=$((FAIL + 1)); }
info() { printf '[INFO] %s\n' "$1"; }

if [ -d /data/data/com.termux ] || [ -n "${TERMUX_VERSION:-}" ]; then
  ENV_TYPE=termux
fi
printf '%s\n' '=== LADAR passive ADS-B hardware/feed audit ==='
info "Environment: $ENV_TYPE ($(uname -srm 2>/dev/null || uname -s))"
info 'This audit is read-only and will not alter device configuration.'

# USB discovery. Product descriptions do not reliably identify the tuner or V3/V4 revision.
if [ "$ENV_TYPE" = termux ]; then
  if command -v termux-usb >/dev/null 2>&1; then
    USB_LIST=$(termux-usb -l 2>/dev/null || true)
    if [ -n "$USB_LIST" ] && [ "$USB_LIST" != '[]' ]; then
      SDR_USB_DESCRIPTION="$USB_LIST"
      pass 'Android USB device(s) listed by termux-usb; this does not prove the device is an RTL-SDR.'
    else
      warn 'No USB device listed by termux-usb; check OTG connection and Android permission.'
    fi
  else
    warn 'termux-usb is unavailable; Android USB discovery skipped.'
  fi
else
  if command -v lsusb >/dev/null 2>&1; then
    USB_LIST=$(lsusb 2>/dev/null || true)
    SDR_USB_DESCRIPTION=$(printf '%s\n' "$USB_LIST" | grep -Ei 'RTL283[238]|RTL-SDR|Realtek.*283|0bda:283' | head -n 1 || true)
    if [ -n "$SDR_USB_DESCRIPTION" ]; then
      SDR_DETECTED=true
      pass "Likely RTL-SDR USB device found: $SDR_USB_DESCRIPTION"
      info 'lsusb labels cannot reliably distinguish RTL-SDR hardware revisions or tuner chips.'
    else
      warn 'No recognizable RTL-SDR entry found in lsusb; generic USB labels may not identify clones.'
    fi
  else
    warn 'lsusb is unavailable; USB discovery skipped.'
  fi

  if command -v lsmod >/dev/null 2>&1; then
    if lsmod 2>/dev/null | grep -Eq '^dvb_usb_rtl28xxu|^rtl2832'; then
      warn 'A DVB kernel module appears loaded. No driver was unloaded or blacklisted; verify device ownership before changing it.'
    else
      pass 'No common RTL DVB kernel module is currently listed.'
    fi
  else
    info 'lsmod unavailable; kernel module check skipped.'
  fi
fi

# Avoid rtl_test while readsb/dump1090 may own the receiver.
if [ "$ENV_TYPE" != termux ] && [ "$SDR_DETECTED" = true ] && command -v rtl_test >/dev/null 2>&1; then
  if pgrep -x readsb >/dev/null 2>&1 || pgrep -x dump1090 >/dev/null 2>&1 || pgrep -x dump1090-fa >/dev/null 2>&1; then
    SDR_TEST=skipped_decoder_running
    warn 'A decoder appears to be running; rtl_test skipped to avoid competing for the SDR.'
  else
    TEST_OUTPUT=$(timeout 5 rtl_test -t 2>&1 || true)
    if printf '%s' "$TEST_OUTPUT" | grep -Eqi 'Found [0-9]+ device|Reading samples|samples per second'; then
      SDR_TEST=output_detected
      pass 'rtl_test produced device/sample output; inspect local output for any overflow or read errors.'
    else
      SDR_TEST=inconclusive
      warn 'rtl_test did not confirm sample reception; inspect the command output and USB permissions.'
    fi
  fi
elif [ "$ENV_TYPE" != termux ] && [ "$SDR_DETECTED" = true ]; then
  SDR_TEST=not_installed
  warn 'rtl_test is not installed; no sample test was performed.'
else
  SDR_TEST=not_run
  info 'RTL-SDR sample test was not run (not detected or Termux environment).'
fi

# Installed/running decoder is informative only; the JSON endpoint is the meaningful feed check.
if command -v readsb >/dev/null 2>&1; then
  if pgrep -x readsb >/dev/null 2>&1; then
    ADSB_SOURCE=readsb
    pass 'readsb is installed and running.'
  else
    ADSB_SOURCE=readsb_installed
    warn 'readsb is installed but does not appear to be running.'
  fi
elif command -v dump1090-fa >/dev/null 2>&1 || command -v dump1090 >/dev/null 2>&1; then
  if pgrep -x dump1090-fa >/dev/null 2>&1 || pgrep -x dump1090 >/dev/null 2>&1; then
    ADSB_SOURCE=dump1090
    pass 'dump1090 is installed and running.'
  else
    ADSB_SOURCE=dump1090_installed
    warn 'dump1090 is installed but does not appear to be running.'
  fi
else
  warn 'No local readsb/dump1090 executable found; the configured feed may be on another host.'
fi

if command -v curl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  PYTHON_AVAILABLE=true
  RESPONSE=$(curl -fsS --max-time 3 "$ADSB_URL" 2>/dev/null || true)
  if [ -n "$RESPONSE" ]; then
    COUNT=$(printf '%s' "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d.get("aircraft"); print(len(a) if isinstance(a,list) else "INVALID")' 2>/dev/null || true)
    if [[ "$COUNT" =~ ^[0-9]+$ ]]; then
      JSON_OK=true
      AIRCRAFT_COUNT=$COUNT
      pass "Valid aircraft JSON feed available at $ADSB_URL; record count: $AIRCRAFT_COUNT."
      if [ "$AIRCRAFT_COUNT" -eq 0 ]; then
        warn 'The endpoint is valid but has zero aircraft records at this moment.'
      fi
    else
      warn 'Endpoint returned data, but it was not valid JSON containing an aircraft array.'
    fi
  else
    warn "Could not fetch the configured feed: $ADSB_URL"
  fi
else
  if ! command -v curl >/dev/null 2>&1; then warn 'curl is unavailable; feed check skipped.'; fi
  if command -v python3 >/dev/null 2>&1; then PYTHON_AVAILABLE=true; else warn 'python3 is unavailable; feed JSON parsing skipped.'; fi
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_AVAILABLE=true
  pass "$(python3 --version 2>&1) is available."
else
  fail 'python3 is unavailable.'
fi

if [ "$JSON_OK" = true ]; then
  DECISION=READY_FOR_API_CHECK
elif [ "$SDR_DETECTED" = true ] && [[ "$ADSB_SOURCE" == *_installed ]]; then
  DECISION=START_RECEIVER
elif [ "$SDR_DETECTED" = true ]; then
  DECISION=RECEIVER_SETUP_NEEDED
elif [ "$ENV_TYPE" = termux ]; then
  DECISION=CHECK_ANDROID_USB_OR_REMOTE_FEED
else
  DECISION=NO_SDR_OR_FEED_CONFIRMED
fi

# Serialize with Python's JSON encoder so environment strings are escaped safely.
REPORT_FILE="$REPORT_FILE" ENV_TYPE="$ENV_TYPE" SDR_DETECTED="$SDR_DETECTED" SDR_USB_DESCRIPTION="$SDR_USB_DESCRIPTION" SDR_TEST="$SDR_TEST" ADSB_SOURCE="$ADSB_SOURCE" ADSB_URL="$ADSB_URL" JSON_OK="$JSON_OK" AIRCRAFT_COUNT="$AIRCRAFT_COUNT" PASS_COUNT="$PASS" WARN_COUNT="$WARN" FAIL_COUNT="$FAIL" DECISION="$DECISION" python3 - <<'PY'
import json, os
from datetime import datetime, timezone
report = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "environment": os.environ["ENV_TYPE"],
    "rtl_sdr_detected": os.environ["SDR_DETECTED"] == "true",
    "rtl_usb_description": os.environ["SDR_USB_DESCRIPTION"] or None,
    "rtl_sample_test": os.environ["SDR_TEST"],
    "adsb_source": os.environ["ADSB_SOURCE"],
    "adsb_url": os.environ["ADSB_URL"],
    "json_ok": os.environ["JSON_OK"] == "true",
    "aircraft_count_now": int(os.environ["AIRCRAFT_COUNT"]),
    "pass_count": int(os.environ["PASS_COUNT"]),
    "warn_count": int(os.environ["WARN_COUNT"]),
    "fail_count": int(os.environ["FAIL_COUNT"]),
    "decision": os.environ["DECISION"],
}
with open(os.environ["REPORT_FILE"], "w", encoding="utf-8") as report_file:
    json.dump(report, report_file, ensure_ascii=False, indent=2)
    report_file.write("\n")
PY

printf '\nSummary: PASS=%s WARN=%s FAIL=%s DECISION=%s\n' "$PASS" "$WARN" "$FAIL" "$DECISION"
printf 'Report: %s\n' "$REPORT_FILE"
