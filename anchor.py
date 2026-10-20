#!/usr/bin/env python3
"""Anchor — backup freshness monitor with ntfy alerts."""

import hashlib
import json
import os
import pathlib
import signal
import sys
import time
import urllib.error
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

TARGETS = os.environ.get("BACKUP_TARGETS", "")
NTFY_URL = os.environ.get("NTFY_URL", "http://localhost:80")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "backups")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "3600"))
ALERT_COOLDOWN = int(os.environ.get("ALERT_COOLDOWN", "86400"))
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8586"))

# ntfy priority ceiling. Set NTFY_MAX_PRIORITY (e.g. "default") to stop this
# tool from ever sending high/urgent, so those levels stay reserved for
# messages a human must see. Empty means no cap.
_PRIORITY_LEVELS = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5, "max": 5,
                    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
NTFY_MAX_PRIORITY = os.environ.get("NTFY_MAX_PRIORITY", "").strip().lower()
if NTFY_MAX_PRIORITY and NTFY_MAX_PRIORITY not in _PRIORITY_LEVELS:
    raise SystemExit(f"Invalid NTFY_MAX_PRIORITY: {NTFY_MAX_PRIORITY!r}")


def cap_priority(priority, cap=None):
    """Lower priority to the configured ceiling; leave it alone if under it."""
    cap = NTFY_MAX_PRIORITY if cap is None else cap
    if not cap:
        return priority
    level = _PRIORITY_LEVELS.get(str(priority).strip().lower())
    if level is None or level <= _PRIORITY_LEVELS[cap]:
        return priority
    return cap


running = True


def parse_duration(s):
    s = s.strip().lower()
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("m"):
        return int(s[:-1]) * 60
    return int(s)


def parse_targets(raw):
    targets = []
    if not raw.strip():
        return targets
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 2)
        if len(parts) != 3:
            print(f"[anchor] skipping malformed target: {entry}", file=sys.stderr)
            continue
        label, path, max_age_str = parts
        try:
            max_age = parse_duration(max_age_str)
        except (ValueError, IndexError):
            print(f"[anchor] invalid duration '{max_age_str}' for {label}", file=sys.stderr)
            continue
        targets.append({"label": label, "path": path, "max_age": max_age})
    return targets


def newest_mtime(path_str):
    p = pathlib.Path(path_str)
    if not p.exists():
        return None
    if p.is_file():
        return p.stat().st_mtime
    newest = None
    for child in p.rglob("*"):
        if child.is_file():
            mt = child.stat().st_mtime
            if newest is None or mt > newest:
                newest = mt
    return newest


def check_target(target, now):
    mtime = newest_mtime(target["path"])
    if mtime is None:
        return {
            "label": target["label"],
            "path": target["path"],
            "status": "missing",
            "message": f"path not found: {target['path']}",
            "max_age": target["max_age"],
            "age": None,
        }
    age = now - mtime
    if age > target["max_age"]:
        return {
            "label": target["label"],
            "path": target["path"],
            "status": "stale",
            "message": f"backup is {format_duration(age)} old (max {format_duration(target['max_age'])})",
            "max_age": target["max_age"],
            "age": age,
        }
    return {
        "label": target["label"],
        "path": target["path"],
        "status": "fresh",
        "message": f"backup is {format_duration(age)} old",
        "max_age": target["max_age"],
        "age": age,
    }


def format_duration(seconds):
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    if seconds >= 86400:
        d = seconds // 86400
        h = (seconds % 86400) // 3600
        return f"{d}d {h}h" if h else f"{d}d"
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def send_alert(result, ntfy_url, ntfy_topic):
    if result["status"] == "missing":
        title = f"Backup missing: {result['label']}"
        priority = "urgent"
    else:
        title = f"Backup stale: {result['label']}"
        priority = "high"

    body = result["message"]
    url = f"{ntfy_url.rstrip('/')}/{ntfy_topic}"
    req = urllib.request.Request(
        url,
        data=body.encode(),
        headers={
            "Title": title,
            "Priority": cap_priority(priority),
            "Tags": "warning",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"[anchor] alert sent: {title}")
    except (urllib.error.URLError, OSError) as e:
        print(f"[anchor] alert failed: {e}", file=sys.stderr)


def alert_key(result):
    return hashlib.md5(f"{result['label']}:{result['status']}".encode()).hexdigest()


def run_checks(targets, ntfy_url, ntfy_topic, cooldowns):
    now = time.time()
    results = []
    for target in targets:
        result = check_target(target, now)
        results.append(result)
        status = result["status"]
        print(f"[anchor] {result['label']}: {status} — {result['message']}")
        if status in ("stale", "missing"):
            key = alert_key(result)
            last_alert = cooldowns.get(key, 0)
            if now - last_alert >= ALERT_COOLDOWN:
                send_alert(result, ntfy_url, ntfy_topic)
                cooldowns[key] = now
    return results


last_results = []


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/status":
            body = json.dumps({
                "targets": last_results,
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.write = self.wfile.write
            self.wfile.write(body)
        elif self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    global running, last_results

    targets = parse_targets(TARGETS)
    if not targets:
        print("[anchor] no targets configured — set BACKUP_TARGETS env var")
        print("[anchor] format: label:path:max_age,label:path:max_age")
        print("[anchor] example: daily-db:/backups/db:24h,weekly-photos:/backups/photos:168h")
        sys.exit(1)

    print(f"[anchor] monitoring {len(targets)} backup target(s), checking every {format_duration(CHECK_INTERVAL)}")
    for t in targets:
        print(f"[anchor]   {t['label']}: {t['path']} (max age: {format_duration(t['max_age'])})")

    signal.signal(signal.SIGTERM, lambda *_: setattr(sys.modules[__name__], "running", False))
    signal.signal(signal.SIGINT, lambda *_: setattr(sys.modules[__name__], "running", False))

    server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
    server.timeout = 1

    cooldowns = {}
    last_check = 0

    while running:
        now = time.time()
        if now - last_check >= CHECK_INTERVAL:
            last_results = run_checks(targets, NTFY_URL, NTFY_TOPIC, cooldowns)
            last_check = now
        server.handle_request()


if __name__ == "__main__":
    main()
