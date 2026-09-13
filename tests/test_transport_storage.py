from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from instruments.serial_resource import SerialResource
from instruments import discovery
from test_engine.database import Store
from test_engine.engine import TestEngine
from test_engine.config import load_config


class TransportTests(unittest.TestCase):
    def test_exclusive_and_bounded_serial_open(self):
        with patch("instruments.serial_resource.serial.Serial") as serial:
            res = SerialResource("/dev/fake", timeout=0.3)
            res.open()
            self.assertTrue(serial.call_args.kwargs["exclusive"])
            self.assertEqual(serial.call_args.kwargs["write_timeout"], 0.3)
            res.close()
            serial.return_value.close.assert_called_once()

    def test_probe_always_closes_and_sends_only_idn(self):
        with patch("instruments.discovery.SerialResource") as resource:
            resource.return_value.query.side_effect = IOError("gone")
            result = discovery.probe_port("/dev/fake")
            self.assertEqual(result["device_type"], "error")
            resource.return_value.close.assert_called_once()
            resource.return_value.query.assert_called_once_with("*IDN?")

    def test_truncated_response_fails(self):
        with patch("instruments.serial_resource.serial.Serial") as serial:
            serial.return_value.is_open = True
            serial.return_value.write.side_effect = lambda payload: len(payload)
            serial.return_value.read_until.return_value = b"12.0"
            res = SerialResource("/dev/fake")
            res.open()
            with self.assertRaises(TimeoutError):
                res.query("MEASure:VOLTage?")

    def test_swapped_identity_never_gets_control_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = TestEngine(
                dry_run=False, config=load_config(), storage_root=directory
            )
            with (
                patch.object(engine, "_acquire_hardware"),
                patch("test_engine.engine.SerialResource") as resource,
            ):
                resource.return_value.query.return_value = "OWON,OEL1520,123,1.0"
                with self.assertRaises(ConnectionError):
                    engine.connect("/dev/fake-psu", "/dev/fake-load")
                resource.return_value.write.assert_not_called()
                resource.return_value.close.assert_called()
            engine.close()

    def test_same_device_paths_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = TestEngine(
                dry_run=False, config=load_config(), storage_root=directory
            )
            with (
                patch.object(engine, "_acquire_hardware"),
                patch("test_engine.engine.SerialResource") as resource,
            ):
                with self.assertRaises(ConnectionError):
                    engine.connect("/dev/fake", "/dev/fake")
                resource.assert_not_called()
            engine.close()


class MigrationTests(unittest.TestCase):
    def test_legacy_database_backup_and_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test_results.sqlite"
            c = sqlite3.connect(path)
            c.executescript("""CREATE TABLE test_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_start TEXT NOT NULL,timestamp_end TEXT,test_mode TEXT NOT NULL,
                unit_serial_or_label TEXT,profile_name TEXT,operator TEXT,final_result TEXT,fail_reason TEXT,notes TEXT);
                INSERT INTO test_runs(timestamp_start,test_mode,final_result) VALUES ('old','production_test','PASS');
                INSERT INTO test_runs(timestamp_start,test_mode) VALUES ('old','production_test');""")
            c.close()
            store = Store(path, Path(directory) / "csv")
            rows = store.get_recent_runs()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["final_result"], "PASS")
            self.assertIsNone(rows[0]["final_result"])
            backups = list(Path(directory).glob("*.bak"))
            self.assertEqual(len(backups), 1)
            c = sqlite3.connect(backups[0])
            self.assertEqual(
                c.execute("SELECT count(*) FROM test_runs").fetchone()[0], 2
            )
            c.close()
            store.close()


class ExportTests(unittest.TestCase):
    def test_final_export_is_from_committed_result_and_failure_retries(self):
        with tempfile.TemporaryDirectory() as d:
            config = load_config()
            config["measurement_samples"] = 1
            engine = TestEngine(dry_run=True, config=config, storage_root=d)
            engine.connect()
            from test_engine.test_profiles import generate_sweep_steps

            original = engine.store.export_csv

            def export(rid):
                if engine.store.get_run(rid)["final_result"] is not None:
                    raise OSError("export directory unavailable")
                return original(rid)

            with patch.object(engine.store, "export_csv", side_effect=export):
                engine.start_sweep(
                    dict(steps=generate_sweep_steps(num_steps=1), wait_time=0)
                )
                engine.wait_idle()
            run = engine.get_runs()[0]
            self.assertEqual(run["final_result"], "COMPLETE")
            self.assertIn("unavailable", run["csv_export_error"])
            self.assertTrue(engine.manager.fault)
            engine.close()
            engine = TestEngine(dry_run=True, config=config, storage_root=d)
            self.assertIsNone(engine.get_runs()[0]["csv_export_error"])
            engine.close()
