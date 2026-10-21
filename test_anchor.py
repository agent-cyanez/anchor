#!/usr/bin/env python3
"""Tests for Anchor backup freshness monitor."""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

import anchor


class TestParseDuration(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(anchor.parse_duration("3600"), 3600)

    def test_minutes(self):
        self.assertEqual(anchor.parse_duration("30m"), 1800)

    def test_hours(self):
        self.assertEqual(anchor.parse_duration("24h"), 86400)

    def test_days(self):
        self.assertEqual(anchor.parse_duration("7d"), 604800)

    def test_whitespace(self):
        self.assertEqual(anchor.parse_duration("  12h  "), 43200)

    def test_case_insensitive(self):
        self.assertEqual(anchor.parse_duration("24H"), 86400)


class TestParseTargets(unittest.TestCase):
    def test_single_target(self):
        result = anchor.parse_targets("db:/backups/db:24h")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "db")
        self.assertEqual(result[0]["path"], "/backups/db")
        self.assertEqual(result[0]["max_age"], 86400)

    def test_multiple_targets(self):
        result = anchor.parse_targets("db:/backups/db:24h,photos:/backups/photos:168h")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["label"], "db")
        self.assertEqual(result[1]["label"], "photos")

    def test_empty_string(self):
        self.assertEqual(anchor.parse_targets(""), [])

    def test_whitespace_only(self):
        self.assertEqual(anchor.parse_targets("   "), [])

    def test_malformed_entry_skipped(self):
        result = anchor.parse_targets("bad-entry,db:/backups/db:24h")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "db")

    def test_invalid_duration_skipped(self):
        result = anchor.parse_targets("db:/backups/db:xyz")
        self.assertEqual(len(result), 0)


class TestFormatDuration(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(anchor.format_duration(45), "45s")

    def test_minutes(self):
        self.assertEqual(anchor.format_duration(120), "2m")

    def test_hours_minutes(self):
        self.assertEqual(anchor.format_duration(5400), "1h 30m")

    def test_hours_only(self):
        self.assertEqual(anchor.format_duration(7200), "2h")

    def test_days_hours(self):
        self.assertEqual(anchor.format_duration(90000), "1d 1h")

    def test_days_only(self):
        self.assertEqual(anchor.format_duration(172800), "2d")

    def test_none(self):
        self.assertEqual(anchor.format_duration(None), "unknown")


class TestNewestMtime(unittest.TestCase):
    def test_single_file(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"data")
            f.flush()
            result = anchor.newest_mtime(f.name)
            self.assertIsNotNone(result)
            self.assertAlmostEqual(result, time.time(), delta=5)

    def test_directory(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, "old.txt")
            new = os.path.join(d, "new.txt")
            with open(old, "w") as f:
                f.write("old")
            os.utime(old, (time.time() - 3600, time.time() - 3600))
            with open(new, "w") as f:
                f.write("new")
            result = anchor.newest_mtime(d)
            self.assertAlmostEqual(result, time.time(), delta=5)

    def test_nonexistent(self):
        self.assertIsNone(anchor.newest_mtime("/nonexistent/path/xyz"))

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(anchor.newest_mtime(d))

    def test_nested_directory(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "sub", "deep")
            os.makedirs(sub)
            f = os.path.join(sub, "file.txt")
            with open(f, "w") as fh:
                fh.write("data")
            result = anchor.newest_mtime(d)
            self.assertIsNotNone(result)


class TestCheckTarget(unittest.TestCase):
    def test_fresh_file(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"data")
            f.flush()
            target = {"label": "test", "path": f.name, "max_age": 3600}
            result = anchor.check_target(target, time.time())
            self.assertEqual(result["status"], "fresh")

    def test_stale_file(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"data")
            f.flush()
            os.utime(f.name, (time.time() - 7200, time.time() - 7200))
            target = {"label": "test", "path": f.name, "max_age": 3600}
            result = anchor.check_target(target, time.time())
            self.assertEqual(result["status"], "stale")
            self.assertIn("old", result["message"])

    def test_missing_path(self):
        target = {"label": "test", "path": "/nonexistent/xyz", "max_age": 3600}
        result = anchor.check_target(target, time.time())
        self.assertEqual(result["status"], "missing")

    def test_fresh_directory(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "backup.sql")
            with open(f, "w") as fh:
                fh.write("data")
            target = {"label": "test", "path": d, "max_age": 3600}
            result = anchor.check_target(target, time.time())
            self.assertEqual(result["status"], "fresh")


class TestAlertKey(unittest.TestCase):
    def test_deterministic(self):
        r = {"label": "db", "status": "stale"}
        self.assertEqual(anchor.alert_key(r), anchor.alert_key(r))

    def test_different_status(self):
        r1 = {"label": "db", "status": "stale"}
        r2 = {"label": "db", "status": "missing"}
        self.assertNotEqual(anchor.alert_key(r1), anchor.alert_key(r2))


class TestSendAlert(unittest.TestCase):
    @patch("anchor.urllib.request.urlopen")
    def test_stale_alert(self, mock_urlopen):
        result = {"label": "db", "status": "stale", "message": "backup is 2d old"}
        anchor.send_alert(result, "http://ntfy:80", "backups")
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertIn("Backup stale: db", req.get_header("Title"))
        self.assertEqual(req.get_header("Priority"), "high")

    @patch("anchor.urllib.request.urlopen")
    def test_missing_alert(self, mock_urlopen):
        result = {"label": "db", "status": "missing", "message": "path not found"}
        anchor.send_alert(result, "http://ntfy:80", "backups")
        req = mock_urlopen.call_args[0][0]
        self.assertIn("Backup missing: db", req.get_header("Title"))
        self.assertEqual(req.get_header("Priority"), "urgent")

    @patch("anchor.urllib.request.urlopen", side_effect=OSError("connection refused"))
    def test_alert_failure_handled(self, mock_urlopen):
        result = {"label": "db", "status": "stale", "message": "old"}
        self.assertFalse(anchor.send_alert(result, "http://ntfy:80", "backups"))


class TestRunChecks(unittest.TestCase):
    @patch("anchor.send_alert")
    def test_fresh_no_alert(self, mock_alert):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"data")
            f.flush()
            targets = [{"label": "test", "path": f.name, "max_age": 3600}]
            results = anchor.run_checks(targets, "http://ntfy:80", "backups", {})
            mock_alert.assert_not_called()
            self.assertEqual(results[0]["status"], "fresh")

    @patch("anchor.send_alert")
    def test_stale_sends_alert(self, mock_alert):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"data")
            f.flush()
            os.utime(f.name, (time.time() - 7200, time.time() - 7200))
            targets = [{"label": "test", "path": f.name, "max_age": 3600}]
            results = anchor.run_checks(targets, "http://ntfy:80", "backups", {})
            mock_alert.assert_called_once()

    @patch("anchor.send_alert")
    def test_cooldown_prevents_repeat(self, mock_alert):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"data")
            f.flush()
            os.utime(f.name, (time.time() - 7200, time.time() - 7200))
            targets = [{"label": "test", "path": f.name, "max_age": 3600}]
            cooldowns = {}
            anchor.run_checks(targets, "http://ntfy:80", "backups", cooldowns)
            mock_alert.assert_called_once()
            mock_alert.reset_mock()
            anchor.run_checks(targets, "http://ntfy:80", "backups", cooldowns)
            mock_alert.assert_not_called()

    @patch("anchor.send_alert", return_value=False)
    def test_failed_alert_does_not_start_cooldown(self, mock_alert):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"data")
            f.flush()
            os.utime(f.name, (time.time() - 7200, time.time() - 7200))
            targets = [{"label": "test", "path": f.name, "max_age": 3600}]
            cooldowns = {}
            anchor.run_checks(targets, "http://ntfy:80", "backups", cooldowns)
            self.assertEqual(cooldowns, {})
            anchor.run_checks(targets, "http://ntfy:80", "backups", cooldowns)
            self.assertEqual(mock_alert.call_count, 2)


class TestCapPriority(unittest.TestCase):
    def test_no_cap_passes_through(self):
        self.assertEqual(anchor.cap_priority("urgent", cap=""), "urgent")

    def test_caps_high_and_urgent(self):
        self.assertEqual(anchor.cap_priority("high", cap="default"), "default")
        self.assertEqual(anchor.cap_priority("urgent", cap="default"), "default")
        self.assertEqual(anchor.cap_priority("5", cap="high"), "high")

    def test_leaves_lower_priorities_alone(self):
        self.assertEqual(anchor.cap_priority("default", cap="default"), "default")
        self.assertEqual(anchor.cap_priority("low", cap="default"), "low")


if __name__ == "__main__":
    unittest.main()
