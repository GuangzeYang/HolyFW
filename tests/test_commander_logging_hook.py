#!/usr/bin/env python3
"""Tests for commander dated file logging and reattach."""

from __future__ import annotations

import logging
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from commander.logging_setup import (
    COMMANDER_DATED_FILE_HANDLER_NAME,
    configure_commander_root_logging,
    reattach_commander_dated_file_handler,
)


def _clear_root_handlers() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass


class CommanderLoggingHookTests(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_root_handlers()

    def test_configure_creates_today_commander_log(self) -> None:
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        self.addCleanup(_clear_root_handlers)
        logs = Path(td)
        path = configure_commander_root_logging(logs, "INFO")
        expected = logs / f"commander_{date.today().isoformat()}.log"
        self.assertEqual(path.resolve(), expected.resolve())
        self.assertTrue(path.is_file())

    def test_reattach_replaces_only_dated_handler(self) -> None:
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        self.addCleanup(_clear_root_handlers)
        logs = Path(td)
        configure_commander_root_logging(logs, "INFO")
        root = logging.getLogger()
        self.assertGreaterEqual(len(root.handlers), 2)

        dated = [
            h
            for h in root.handlers
            if getattr(h, "name", None) == COMMANDER_DATED_FILE_HANDLER_NAME
        ]
        self.assertEqual(len(dated), 1)

        other_day = date.today() + timedelta(days=1)
        new_path = reattach_commander_dated_file_handler(
            logs, "INFO", target_day=other_day
        )
        self.assertEqual(new_path.resolve(), (logs / f"commander_{other_day.isoformat()}.log").resolve())

        dated_after = [
            h
            for h in root.handlers
            if getattr(h, "name", None) == COMMANDER_DATED_FILE_HANDLER_NAME
        ]
        self.assertEqual(len(dated_after), 1)
        fh = dated_after[0]
        self.assertIsInstance(fh, logging.FileHandler)
        self.assertEqual(Path(fh.baseFilename).resolve(), new_path.resolve())

        consoles = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        self.assertGreaterEqual(len(consoles), 1)


if __name__ == "__main__":
    unittest.main()
