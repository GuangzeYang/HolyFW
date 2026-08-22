#!/usr/bin/env python3
"""Tests for the local Sysmon collector (all Windows calls mocked)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from sysmon_collector import collector, elevate, export, paths, sysmon_service


def _close_collector_log_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == collector.COLLECTOR_HANDLER_NAME:
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass


class ResolvePathsTests(unittest.TestCase):
    def test_holyfw_sysmon_env_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Sysmon64.exe"
            exe.write_bytes(b"")
            other = Path(tmp) / "Sysmon.exe"
            other.write_bytes(b"")
            with mock.patch.dict(
                os.environ,
                {paths.HOLYFW_SYSMON: str(exe), paths.SYSMON_ENV: str(other)},
                clear=False,
            ):
                self.assertEqual(paths.resolve_sysmon_exe(), exe.resolve())

    def test_sysmon_env_used_when_holyfw_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Sysmon.exe"
            exe.write_bytes(b"")
            env = os.environ.copy()
            env.pop(paths.HOLYFW_SYSMON, None)
            env[paths.SYSMON_ENV] = str(exe)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(paths.resolve_sysmon_exe(), exe.resolve())

    def test_missing_holyfw_sysmon_file_raises(self) -> None:
        with mock.patch.dict(
            os.environ,
            {paths.HOLYFW_SYSMON: r"C:\missing\Sysmon64.exe"},
            clear=False,
        ):
            with self.assertRaises(FileNotFoundError):
                paths.resolve_sysmon_exe()

    def test_path_fallback_when_env_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Sysmon64.exe"
            exe.write_bytes(b"")
            env = os.environ.copy()
            env.pop(paths.HOLYFW_SYSMON, None)
            env.pop(paths.SYSMON_ENV, None)
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch("shutil.which", return_value=str(exe)):
                    self.assertEqual(paths.resolve_sysmon_exe(), exe.resolve())

    def test_config_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "custom.xml"
            cfg.write_text("<Sysmon/>", encoding="utf-8")
            with mock.patch.dict(
                os.environ, {paths.HOLYFW_SYSMON_CONFIG: str(cfg)}, clear=False
            ):
                self.assertEqual(paths.resolve_sysmon_config(), cfg.resolve())

    def test_evtx_dir_and_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env = os.environ.copy()
            env.pop(paths.HOLYFW_SYSMON_LOG_DIR, None)
            with mock.patch.dict(os.environ, env, clear=True):
                out = paths.evtx_dir(base)
            self.assertEqual(out, (base / "logs" / "sysmon").resolve())
            self.assertEqual(
                paths.evtx_path(out, date(2026, 8, 22)).name,
                "sysmon_2026-08-22.evtx",
            )
            self.assertEqual(
                paths.security_evtx_path(out, date(2026, 8, 22)).name,
                "security_logon_2026-08-22.evtx",
            )


class SysmonServiceTests(unittest.TestCase):
    def test_parse_sc_query_running_and_missing(self) -> None:
        running = "SERVICE_NAME: Sysmon64\n        STATE              : 4  RUNNING\n"
        self.assertEqual(sysmon_service.parse_sc_query(running, 0), "RUNNING")
        self.assertIsNone(sysmon_service.parse_sc_query("", 1060))

    def test_restart_stops_running_then_applies_config(self) -> None:
        calls: list[list[str]] = []
        states = iter(["RUNNING", "STOPPED", "STOPPED"])

        def run_fn(args, **kwargs):
            calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        def query_fn():
            return "Sysmon64", next(states)

        result = sysmon_service.restart_sysmon(
            Path("C:/Sysmon64.exe"),
            Path("C:/sysmonconfig.xml"),
            run_fn=run_fn,
            query_fn=query_fn,
            sleep_fn=lambda _: None,
        )
        self.assertTrue(result["was_running"])
        self.assertEqual(result["action"], "config+start")
        self.assertEqual(calls[0][:2], ["sc", "stop"])
        self.assertEqual(calls[1][:2], ["sc", "start"])
        self.assertIn("-c", calls[2])

    def test_restart_starts_service_even_if_config_apply_fails(self) -> None:
        calls: list[list[str]] = []
        states = iter(["RUNNING", "STOPPED"])

        def run_fn(args, **kwargs):
            calls.append(list(args))
            if "-c" in args:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="config failed")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with self.assertRaises(RuntimeError):
            sysmon_service.restart_sysmon(
                Path("C:/Sysmon64.exe"),
                Path("C:/sysmonconfig.xml"),
                run_fn=run_fn,
                query_fn=lambda: ("Sysmon64", next(states, "STOPPED")),
                sleep_fn=lambda _: None,
            )
        start_calls = [c for c in calls if c[:2] == ["sc", "start"]]
        self.assertGreaterEqual(len(start_calls), 2)

    def test_restart_installs_when_service_missing(self) -> None:
        calls: list[list[str]] = []

        def run_fn(args, **kwargs):
            calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        result = sysmon_service.restart_sysmon(
            Path("C:/Sysmon64.exe"),
            Path("C:/sysmonconfig.xml"),
            run_fn=run_fn,
            query_fn=lambda: (None, None),
            sleep_fn=lambda _: None,
        )
        self.assertFalse(result["was_running"])
        self.assertEqual(result["action"], "install")
        self.assertIn("-i", calls[0])


class ExportTests(unittest.TestCase):
    def test_day_window_and_query(self) -> None:
        from datetime import datetime, time, timedelta, timezone

        day = date(2026, 8, 22)
        start, end = export.day_query_window(day)
        expected_start = (
            datetime.combine(day, time.min)
            .astimezone()
            .astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.000Z")
        )
        expected_end = (
            datetime.combine(day + timedelta(days=1), time.min)
            .astimezone()
            .astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.000Z")
        )
        self.assertEqual(start, expected_start)
        self.assertEqual(end, expected_end)
        query = export.build_export_query(day)
        self.assertIn(f"@SystemTime>='{expected_start}'", query)
        self.assertIn(f"@SystemTime<'{expected_end}'", query)
        auth_query = export.build_export_query(day, (4624, 4625, 4768))
        self.assertIn("EventID=4624 or EventID=4625 or EventID=4768", auth_query)

    def test_export_day_invokes_wevtutil(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)

            def run_fn(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            path = export.export_day(
                date(2026, 8, 22),
                out_dir,
                run_fn=run_fn,
                wevtutil="wevtutil",
            )
            self.assertEqual(path.name, "sysmon_2026-08-22.evtx")

    def test_export_replaces_existing_file_and_skips_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            existing = out_dir / "sysmon_2026-08-22.evtx"
            existing.write_bytes(b"old")

            def run_fn(args, **kwargs):
                return subprocess.CompletedProcess(
                    args,
                    1,
                    stdout="",
                    stderr="No events were found that match the specified query.",
                )

            path = export.export_day(
                date(2026, 8, 22),
                out_dir,
                run_fn=run_fn,
                wevtutil="wevtutil",
            )
            self.assertEqual(path, existing)
            self.assertFalse(existing.exists())

    def test_export_security_logon_filters_event_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            seen: list[list[str]] = []

            def run_fn(args, **kwargs):
                seen.append(list(args))
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            path = export.export_security_logon_day(
                date(2026, 8, 22),
                out_dir,
                run_fn=run_fn,
                wevtutil="wevtutil",
            )
            self.assertEqual(path.name, "security_logon_2026-08-22.evtx")
            self.assertEqual(seen[0][2], "Security")
            query = seen[0][4]
            self.assertIn("EventID=4624", query)
            self.assertIn("EventID=4768", query)
            self.assertIn("EventID=4776", query)

    def test_export_collected_logs_writes_both_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            channels: list[str] = []

            def run_fn(args, **kwargs):
                channels.append(args[2])
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            written = export.export_collected_logs(
                date(2026, 8, 22),
                out_dir,
                run_fn=run_fn,
                wevtutil="wevtutil",
            )
            self.assertEqual(
                [path.name for path in written],
                ["sysmon_2026-08-22.evtx", "security_logon_2026-08-22.evtx"],
            )
            self.assertEqual(
                channels,
                ["Microsoft-Windows-Sysmon/Operational", "Security"],
            )


class CollectorLoopTests(unittest.TestCase):
    def tearDown(self) -> None:
        _close_collector_log_handlers()

    def test_day_change_exports_previous_then_today_on_stop(self) -> None:
        exported: list[date] = []
        today_values = [
            date(2026, 8, 22),
            date(2026, 8, 23),
            date(2026, 8, 23),
        ]

        def today_fn() -> date:
            return today_values.pop(0) if today_values else date(2026, 8, 23)

        waits = {"n": 0}

        def wait_fn(timeout: float) -> bool:
            waits["n"] += 1
            return waits["n"] >= 2

        collector.run_collection_loop(
            export_day_fn=exported.append,
            today_fn=today_fn,
            shutdown_event=threading.Event(),
            interval=0,
            wait_fn=wait_fn,
        )
        self.assertEqual(exported, [date(2026, 8, 22), date(2026, 8, 23)])

    def test_safe_export_day_continues_when_one_channel_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            security_days: list[date] = []

            def fail_sysmon(day: date, dest: Path) -> Path:
                raise RuntimeError("sysmon export down")

            def ok_security(day: date, dest: Path) -> Path:
                security_days.append(day)
                return dest / f"security_logon_{day.isoformat()}.evtx"

            collector._safe_export_day(
                date(2026, 8, 22),
                out_dir,
                export_fn=fail_sysmon,
                security_export_fn=ok_security,
            )
            self.assertEqual(security_days, [date(2026, 8, 22)])

    def test_acquire_instance_replaces_running_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "sysmon_collector.pid"
            pid_path.write_text("111", encoding="utf-8")
            killed: list[int] = []
            replaced = collector.acquire_instance(
                pid_path,
                222,
                running_fn=lambda pid: pid == 111,
                terminate_fn=killed.append,
                identity_fn=lambda pid: True,
            )
            self.assertEqual(replaced, 111)
            self.assertEqual(killed, [111])
            self.assertEqual(pid_path.read_text(encoding="utf-8"), "222")

    def test_acquire_instance_does_not_kill_unrelated_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "sysmon_collector.pid"
            pid_path.write_text("111", encoding="utf-8")
            killed: list[int] = []
            replaced = collector.acquire_instance(
                pid_path,
                222,
                running_fn=lambda pid: pid == 111,
                terminate_fn=killed.append,
                identity_fn=lambda pid: False,
            )
            self.assertIsNone(replaced)
            self.assertEqual(killed, [])
            self.assertEqual(pid_path.read_text(encoding="utf-8"), "222")

    def test_bootstrap_restarts_then_loops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            exe = base / "Sysmon64.exe"
            exe.write_bytes(b"")
            cfg = base / "sysmonconfig.xml"
            cfg.write_text("<Sysmon/>", encoding="utf-8")
            restarted: list[tuple[Path, Path]] = []
            exported: list[date] = []
            security_exported: list[date] = []
            event = threading.Event()
            event.set()

            def loop_fn(**kwargs) -> None:
                kwargs["export_day_fn"](date(2026, 8, 22))

            with mock.patch.dict(
                os.environ,
                {
                    paths.HOLYFW_SYSMON: str(exe),
                    paths.HOLYFW_SYSMON_CONFIG: str(cfg),
                    paths.HOLYFW_SYSMON_LOG_DIR: str(base / "evtx"),
                },
                clear=False,
            ):
                code = collector.bootstrap_and_run(
                    base_dir=base,
                    current_pid=4242,
                    restart_fn=lambda exe_path, config_path, **kwargs: restarted.append(
                        (exe_path, config_path)
                    ),
                    export_fn=lambda day, out_dir, **kwargs: exported.append(day)
                    or (out_dir / f"sysmon_{day.isoformat()}.evtx"),
                    security_export_fn=lambda day, out_dir, **kwargs: security_exported.append(
                        day
                    )
                    or (out_dir / f"security_logon_{day.isoformat()}.evtx"),
                    loop_fn=loop_fn,
                    shutdown_event=event,
                    install_signals=False,
                )

            self.assertEqual(code, 0)
            self.assertEqual(len(restarted), 1)
            self.assertEqual(exported, [date(2026, 8, 22)])
            self.assertEqual(security_exported, [date(2026, 8, 22)])
            self.assertFalse(paths.pid_file(base).exists())

    def test_bootstrap_exports_after_sysmon_restart_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            exported: list[date] = []
            security_exported: list[date] = []
            event = threading.Event()
            event.set()

            def loop_fn(**kwargs) -> None:
                kwargs["export_day_fn"](date(2026, 8, 22))

            def boom(*args, **kwargs):
                raise RuntimeError("sysmon down")

            with mock.patch.dict(
                os.environ,
                {paths.HOLYFW_SYSMON_LOG_DIR: str(base / "evtx")},
                clear=False,
            ):
                code = collector.bootstrap_and_run(
                    base_dir=base,
                    current_pid=4242,
                    restart_fn=boom,
                    export_fn=lambda day, out_dir, **kwargs: exported.append(day)
                    or (out_dir / f"sysmon_{day.isoformat()}.evtx"),
                    security_export_fn=lambda day, out_dir, **kwargs: security_exported.append(
                        day
                    )
                    or (out_dir / f"security_logon_{day.isoformat()}.evtx"),
                    loop_fn=loop_fn,
                    shutdown_event=event,
                    install_signals=False,
                )

            self.assertEqual(code, 0)
            self.assertEqual(exported, [date(2026, 8, 22)])
            self.assertEqual(security_exported, [date(2026, 8, 22)])

    def test_main_rejects_non_windows(self) -> None:
        if os.name == "nt":
            with mock.patch("sysmon_collector.collector.os.name", "posix"):
                self.assertEqual(collector.main([]), 1)
        else:
            self.assertEqual(collector.main([]), 1)

    def test_main_starts_privileged_task_when_not_elevated(self) -> None:
        with mock.patch("sysmon_collector.collector.os.name", "nt"):
            with mock.patch("sysmon_collector.collector.is_elevated", return_value=False):
                with mock.patch(
                    "sysmon_collector.collector._launch_privileged_collector",
                    return_value=0,
                ) as launch:
                    with mock.patch(
                        "sysmon_collector.collector.bootstrap_and_run"
                    ) as boot:
                        self.assertEqual(collector.main([]), 0)
        launch.assert_called_once()
        boot.assert_not_called()

    def test_main_fails_when_privileged_launch_cannot_run(self) -> None:
        with mock.patch("sysmon_collector.collector.os.name", "nt"):
            with mock.patch("sysmon_collector.collector.is_elevated", return_value=False):
                with mock.patch(
                    "sysmon_collector.collector._launch_privileged_collector",
                    side_effect=RuntimeError("need account"),
                ):
                    with mock.patch(
                        "sysmon_collector.collector.bootstrap_and_run"
                    ) as boot:
                        self.assertEqual(collector.main([]), 1)
        boot.assert_not_called()


class SoldierSpawnTests(unittest.TestCase):
    def tearDown(self) -> None:
        import soldier.soldier as soldier

        soldier._ACTIVE_PROCESSES.clear()
        soldier._SHUTTING_DOWN.clear()

    def test_spawn_uses_privileged_task_without_popen(self) -> None:
        import soldier.soldier as soldier

        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            runtime = Path(tmp) / "runtime"
            runtime.mkdir()
            root = Path(tmp) / "HolyFW"
            started: list[dict] = []

            def fake_start(**kwargs):
                started.append(kwargs)
                return r".\admin"

            with (
                mock.patch("soldier.soldier.os.name", "nt"),
                mock.patch("soldier.soldier.subprocess.Popen") as popen,
                mock.patch("soldier.soldier.locate_holyfw_root", return_value=root),
                mock.patch("soldier.soldier.get_logs_dir", return_value=logs),
                mock.patch("soldier.soldier.get_runtime_dir", return_value=runtime),
                mock.patch(
                    "sysmon_collector.elevate.start_collector_privileged",
                    side_effect=fake_start,
                ),
            ):
                proc = soldier.spawn_sysmon_collector()

            self.assertIsNone(proc)
            self.assertEqual(soldier._ACTIVE_PROCESSES, {})
            popen.assert_not_called()
            self.assertEqual(len(started), 1)
            self.assertEqual(started[0]["cwd"], str(root))
            self.assertEqual(started[0]["python"], sys.executable)
            self.assertEqual(started[0]["wrapper_path"], runtime / "sysmon_collector.cmd")
            self.assertEqual(started[0]["log_path"], logs / "sysmon_collector_spawn.log")
            self.assertEqual(started[0]["env"]["HOLYFW_ROOT"], str(root))

    def test_maybe_start_skips_when_disabled_or_not_windows(self) -> None:
        import soldier.soldier as soldier

        with mock.patch("soldier.soldier.spawn_sysmon_collector") as spawn:
            self.assertIsNone(soldier.maybe_start_sysmon_collector(enabled=False))
            spawn.assert_not_called()

        with (
            mock.patch("soldier.soldier.os.name", "posix"),
            mock.patch("soldier.soldier.spawn_sysmon_collector") as spawn,
        ):
            self.assertIsNone(soldier.maybe_start_sysmon_collector(enabled=True))
            spawn.assert_not_called()

    def test_run_listen_spawns_unless_no_sysmon(self) -> None:
        import soldier.soldier as soldier

        fake_sock = mock.MagicMock()
        fake_sock.accept.side_effect = OSError("stop-test")

        def drive(*, no_sysmon: bool):
            with tempfile.TemporaryDirectory() as tmp:
                with (
                    mock.patch("soldier.soldier.socket.socket", return_value=fake_sock),
                    mock.patch(
                        "soldier.soldier.resolve_listen",
                        return_value=("127.0.0.1", 38472),
                    ),
                    mock.patch(
                        "soldier.soldier.resolve_endpoint",
                        return_value=("127.0.0.1", 38471),
                    ),
                    mock.patch("soldier.soldier.load_exec_timeout", return_value=5),
                    mock.patch("soldier.soldier.load_worker_threads", return_value=1),
                    mock.patch(
                        "soldier.soldier.soldier_data_dir",
                        return_value=Path(tmp),
                    ),
                    mock.patch("soldier.soldier.start_report_retry_thread"),
                    mock.patch("soldier.soldier.maybe_start_sysmon_collector") as spawn,
                    mock.patch("soldier.soldier.terminate_all_active_processes") as term,
                ):
                    with self.assertRaises(OSError):
                        soldier.run_listen(
                            Path(tmp) / "soldier.ini",
                            None,
                            None,
                            None,
                            None,
                            no_sysmon=no_sysmon,
                        )
            return spawn, term

        spawn, term = drive(no_sysmon=False)
        spawn.assert_called_once_with(enabled=True)
        term.assert_called_once()

        spawn, _ = drive(no_sysmon=True)
        spawn.assert_called_once_with(enabled=False)

    def test_listen_forwards_no_sysmon_flag(self) -> None:
        import soldier.soldier as soldier

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with (
                mock.patch("soldier.soldier.run_listen") as listen,
                mock.patch("soldier.soldier.soldier_data_dir", return_value=base),
                mock.patch(
                    "soldier.soldier.configure_soldier_root_logging",
                    return_value=base / "soldier.log",
                ),
                mock.patch("soldier.soldier.default_config_path", return_value=base / "soldier.ini"),
            ):
                soldier.main(["listen", "--no-sysmon"])
        self.assertTrue(listen.call_args.kwargs["no_sysmon"])


class ElevateTests(unittest.TestCase):
    def test_write_system_wrapper_sets_env_and_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "run.cmd"
            log_path = Path(tmp) / "out.log"
            elevate.write_system_wrapper(
                python=r"C:\Python\python.exe",
                env={"HOLYFW_ROOT": r"D:\HolyFW", "HOLYFW_SYSMON": r"C:\Sysmon64.exe"},
                cwd=r"D:\HolyFW",
                wrapper_path=wrapper,
                log_path=log_path,
            )
            text = wrapper.read_text(encoding="utf-8")
            self.assertIn('set "HOLYFW_ROOT=D:\\HolyFW"', text)
            self.assertIn('cd /d "D:\\HolyFW"', text)
            self.assertIn("-m sysmon_collector", text)

    def test_write_system_wrapper_forwards_account_config_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "run.cmd"
            elevate.write_system_wrapper(
                python=r"C:\Python\python.exe",
                env={"HOLYFW_SYSMON_ACCOUNT_CONFIG": r"D:\HolyFW\sysmon_collector\config.json"},
                cwd=None,
                wrapper_path=wrapper,
                log_path=Path(tmp) / "out.log",
            )
            text = wrapper.read_text(encoding="utf-8")
            self.assertIn(
                'set "HOLYFW_SYSMON_ACCOUNT_CONFIG=D:\\HolyFW\\sysmon_collector\\config.json"',
                text,
            )

    def test_start_collector_as_system_registers_system_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "run.cmd"
            calls: list[list[str]] = []

            def run_fn(args, **kwargs):
                calls.append(list(args))
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            elevate.start_collector_as_system(
                python=r"C:\Python\python.exe",
                env={"HOLYFW_ROOT": r"D:\HolyFW"},
                cwd=r"D:\HolyFW",
                wrapper_path=wrapper,
                log_path=Path(tmp) / "out.log",
                run_fn=run_fn,
            )
            self.assertTrue(wrapper.is_file())
            self.assertEqual(calls[0][0], "powershell")
            script = calls[0][-1]
            self.assertIn(elevate.TASK_NAME, script)
            self.assertIn("SYSTEM", script)
            self.assertIn("Start-ScheduledTask", script)

    def test_split_connect_identity_local_and_domain(self) -> None:
        self.assertEqual(elevate._split_connect_identity(r".\labadmin"), ("labadmin", "."))
        self.assertEqual(
            elevate._split_connect_identity(r"ndrtest.local\Administrator"),
            ("Administrator", "ndrtest.local"),
        )
        self.assertEqual(elevate._split_connect_identity("admin@corp.local"), ("admin@corp.local", ""))

    def test_load_account_config_reads_plaintext_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                '{"account": {"username": "labadmin", "password": "secret", "domain": "CORP"}}',
                encoding="utf-8",
            )
            account = elevate.load_account_config(path)
            self.assertIsNotNone(account)
            self.assertEqual(account.username, "labadmin")
            self.assertEqual(account.password, "secret")
            self.assertEqual(account.task_user_id(), r"CORP\labadmin")

    def test_load_account_config_empty_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                '{"account": {"username": "", "password": "", "domain": ""}}',
                encoding="utf-8",
            )
            self.assertIsNone(elevate.load_account_config(path))

    def test_account_config_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "account.json"
            cfg.write_text("{}", encoding="utf-8")
            with mock.patch.dict(
                os.environ, {paths.HOLYFW_SYSMON_ACCOUNT_CONFIG: str(cfg)}, clear=False
            ):
                self.assertEqual(paths.resolve_account_config_path(), cfg.resolve())

    def test_start_collector_as_account_reads_password_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "run.cmd"
            cfg = Path(tmp) / "config.json"
            cfg.write_text(
                '{"account": {"username": "labadmin", "password": "s3cret", "domain": ""}}',
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            def run_fn(args, **kwargs):
                calls.append(list(args))
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            elevate.start_collector_as_account(
                account=elevate.Account(username="labadmin", password="s3cret"),
                python=r"C:\Python\python.exe",
                env={"HOLYFW_ROOT": r"D:\HolyFW"},
                cwd=r"D:\HolyFW",
                wrapper_path=wrapper,
                log_path=Path(tmp) / "out.log",
                config_path=cfg,
                run_fn=run_fn,
            )
            script = calls[0][-1]
            self.assertIn(elevate.TASK_NAME, script)
            self.assertIn(r".\labadmin", script)
            self.assertIn("Schedule.Service", script)
            self.assertIn("RegisterTaskDefinition", script)
            self.assertIn("RunLevel", script)
            self.assertIn(str(cfg.resolve()), script)
            self.assertNotIn("s3cret", script)
            if "Register-ScheduledTask" in script:
                self.assertFalse(
                    "-Principal" in script and "-Password" in script,
                    "Register-ScheduledTask cannot mix -Principal with -Password",
                )
            self.assertEqual(len(calls), 1)

    def test_start_collector_as_account_falls_back_to_schtasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "run.cmd"
            cfg = Path(tmp) / "config.json"
            cfg.write_text(
                '{"account": {"username": "labadmin", "password": "s3cret"}}',
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            def run_fn(args, **kwargs):
                calls.append(list(args))
                if args and args[0] == "powershell":
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="access denied")
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            elevate.start_collector_as_account(
                account=elevate.Account(username="labadmin", password="s3cret"),
                python=r"C:\Python\python.exe",
                env={},
                cwd=None,
                wrapper_path=wrapper,
                log_path=Path(tmp) / "out.log",
                config_path=cfg,
                run_fn=run_fn,
            )
            self.assertEqual(calls[0][0], "powershell")
            self.assertEqual(calls[1][:2], ["schtasks", "/Create"])
            self.assertIn("/RU", calls[1])
            self.assertIn(r".\labadmin", calls[1])
            self.assertIn("s3cret", calls[1])
            self.assertIn("/RL", calls[1])
            self.assertEqual(calls[2][:2], ["schtasks", "/Run"])

    def test_start_collector_privileged_requires_account_when_not_elevated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("sysmon_collector.elevate.load_account_config", return_value=None),
                mock.patch("sysmon_collector.elevate.is_elevated", return_value=False),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    elevate.start_collector_privileged(
                        python=r"C:\Python\python.exe",
                        env={},
                        cwd=None,
                        wrapper_path=Path(tmp) / "run.cmd",
                        log_path=Path(tmp) / "out.log",
                    )
            self.assertIn("config.json", str(ctx.exception))

    def test_start_collector_privileged_uses_account_before_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            account = elevate.Account(username="labadmin", password="x")
            started: list[str] = []

            def fake_account(**kwargs):
                started.append("account")

            def fake_system(**kwargs):
                started.append("system")

            with (
                mock.patch("sysmon_collector.elevate.is_elevated", return_value=True),
                mock.patch(
                    "sysmon_collector.elevate.start_collector_as_account",
                    side_effect=fake_account,
                ),
                mock.patch(
                    "sysmon_collector.elevate.start_collector_as_system",
                    side_effect=fake_system,
                ),
            ):
                identity = elevate.start_collector_privileged(
                    python=r"C:\Python\python.exe",
                    env={},
                    cwd=None,
                    wrapper_path=Path(tmp) / "run.cmd",
                    log_path=Path(tmp) / "out.log",
                    account=account,
                )
            self.assertEqual(identity, r".\labadmin")
            self.assertEqual(started, ["account"])


if __name__ == "__main__":
    unittest.main()
