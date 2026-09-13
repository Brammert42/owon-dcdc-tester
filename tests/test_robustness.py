"""Fault-injection regressions; all storage is temporary, hardware is fake."""

import copy
import csv
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from test_engine.config import load_config
from test_engine.engine import TestEngine, BusyError
from test_engine.database import Store
from test_engine.safety import (
    PRODUCTION_EFFICIENCY_FLOOR,
    validate_limits,
    check_production_step,
)
from test_engine.test_profiles import generate_sweep_steps
from instruments.simulation import SimPSU, SimLoad
from instruments.drivers import OwonPSU, OwonLoad, DriverError
from instruments import discovery


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = load_config()
        self.config.update(measurement_samples=1, settling_time_s=0)
        self.engine = TestEngine(
            dry_run=True, config=self.config, storage_root=self.directory.name
        )
        self.engine.connect()

    def tearDown(self):
        self.engine.request_stop("ABORTED")
        try:
            self.engine.close()
        except Exception:
            pass
        self.directory.cleanup()

    def steps(self, n=3):
        return generate_sweep_steps(
            input_voltage=48,
            input_current_limit=5,
            start_power=10,
            end_power=30,
            num_steps=n,
            config=self.config,
        )

    def start(self, wait=0):
        self.engine.start_sweep(
            dict(steps=self.steps(), wait_time=wait, run_name="Test sweep")
        )

    def profile(self):
        p = copy.deepcopy(self.config["production_test_profile"])
        for step in p["steps"]:
            step["wait_s"] = 0
        return p

    def result(self):
        return self.engine.get_runs()[0]["final_result"]

    def test_sweep_complete_only_after_verified_off(self):
        observed = []
        self.engine.on(
            "sweep_done",
            lambda: observed.append(
                (self.engine.manager.psu.on, self.engine.manager.load.on)
            ),
        )
        self.start()
        self.engine.wait_idle()
        self.assertEqual(self.result(), "COMPLETE")
        self.assertEqual(observed, [(False, False)])
        self.assertEqual(
            len(self.engine.store.get_run_points(self.engine.manager.sweep_run_id)), 3
        )

    def test_sweep_keeps_load_enabled_between_current_steps(self):
        events = []
        load = self.engine.manager.load
        original_on, original_off = load.input_on, load.input_off
        load.input_on = lambda: (events.append("on"), original_on())[1]
        load.input_off = lambda: (events.append("off"), original_off())[1]
        self.start()
        self.engine.wait_idle()
        self.assertEqual(events.count("on"), 1)
        self.assertEqual(
            events.count("off"), 2
        )  # initial safe state and final shutdown
        self.assertEqual(events, ["off", "on", "off"])

    def test_production_efficiency_must_be_strictly_above_90_percent(self):
        step = {"min_efficiency_percent": 70}
        self.assertEqual(PRODUCTION_EFFICIENCY_FLOOR, 90.0)
        self.assertFalse(check_production_step(12, 90.0, step)[0])
        self.assertTrue(check_production_step(12, 90.01, step)[0])

    def test_worker_start_failure_finalizes_and_releases(self):
        with patch("threading.Thread.start", side_effect=RuntimeError("no thread")):
            with self.assertRaisesRegex(RuntimeError, "no thread"):
                self.start()
        self.assertEqual(self.result(), "ERROR")
        self.assertFalse(self.engine.manager.busy)
        self.assertFalse(self.engine.manager.psu.on)
        self.assertFalse(self.engine.manager.load.on)
        self.engine.wait_idle()

    def test_unsafe_sample_cannot_be_hidden_by_average(self):
        from test_engine.measurement import take_measurement, MeasurementError

        with patch.object(
            self.engine.manager.psu, "measure_voltage", side_effect=[100, 20]
        ):
            with self.assertRaises(MeasurementError):
                take_measurement(
                    self.engine.manager.psu,
                    self.engine.manager.load,
                    "manual",
                    samples=2,
                    safety_limits=self.engine.manager.safety_limits,
                )

    def test_manual_psu_enable_removes_existing_load_first(self):
        self.engine.set_psu(48, 5)
        self.engine.manager.load.on = True
        self.engine.psu_on()
        self.assertFalse(self.engine.manager.load.on)
        self.assertTrue(self.engine.manager.psu.on)

    def test_first_step_fault_cannot_activate_next_step(self):
        psu = self.engine.manager.psu
        original = psu.set_voltage
        writes = []
        ons = []

        def fail(v):
            writes.append(v)
            if len(writes) == 1:
                raise IOError("injected USB loss")
            original(v)

        psu.set_voltage = fail
        psu.output_on = lambda: ons.append(True)
        done = []
        self.engine.on("sweep_done", lambda: done.append(True))
        self.start()
        self.engine.wait_idle()
        self.assertEqual(len(writes), 1)
        self.assertFalse(ons)
        self.assertFalse(done)
        self.assertEqual(self.result(), "ERROR")

    def test_fault_is_latched(self):
        self.engine.manager.load.measure_voltage = lambda: float("nan")
        self.start()
        self.engine.wait_idle()
        with self.assertRaises(ValueError):
            self.start()
        self.assertFalse(self.engine.manager.psu.on)

    def test_attempts_both_off_when_load_shutdown_fails(self):
        calls = []
        self.engine.manager.load.input_off = lambda: (_ for _ in ()).throw(
            IOError("load removed")
        )
        self.engine.manager.psu.output_off = lambda: calls.append("psu off")
        self.start()
        self.engine.wait_idle()
        self.assertIn("psu off", calls)
        self.assertEqual(self.result(), "ERROR")
        self.assertIsNone(self.engine.manager.load_on)
        self.assertEqual(self.engine.get_runs()[0]["shutdown_verified"], 0)

    def test_stop_settling_is_prompt_and_terminal(self):
        ready = threading.Event()
        self.engine.on(
            "status_update",
            lambda s: ready.set() if s["status"] == "settling" else None,
        )
        self.start(wait=20)
        self.assertTrue(ready.wait(2))
        t = time.monotonic()
        self.engine.stop_sweep()
        self.engine.wait_idle(2)
        self.assertLess(time.monotonic() - t, 2)
        self.assertEqual(self.result(), "STOPPED")
        self.assertFalse(self.engine.manager.psu.on)
        self.assertFalse(self.engine.manager.load.on)

    def test_stop_idle_does_not_relabel_completed_run(self):
        self.start()
        self.engine.wait_idle()
        self.engine.stop_sweep()
        self.engine.start_new_sweep_run()
        self.assertEqual(self.result(), "COMPLETE")

    def test_overlap_and_clear_rejected(self):
        self.start(wait=20)
        for fn in [
            lambda: self.engine.set_load(1),
            self.engine.clear_sweep,
            self.engine.connect,
            lambda: self.start(),
        ]:
            with self.assertRaises(BusyError):
                fn()
        self.engine.stop_sweep()
        self.engine.wait_idle()

    def test_poll_during_sweep_does_not_touch_hardware(self):
        ready = threading.Event()
        self.engine.on(
            "status_update",
            lambda s: ready.set() if s["status"] == "settling" else None,
        )
        self.start(wait=20)
        self.assertTrue(ready.wait(2))
        with patch.object(
            self.engine.manager.psu,
            "measure_voltage",
            side_effect=AssertionError("poll touched serial"),
        ):
            self.engine.poll_status()
            self.engine.stop_sweep()
            self.engine.wait_idle()

    def test_emergency_stop_no_reactivation(self):
        ready = threading.Event()
        self.engine.on(
            "status_update",
            lambda s: ready.set() if s["status"] == "settling" else None,
        )
        self.start(wait=20)
        self.assertTrue(ready.wait(2))
        self.engine.emergency_stop()
        self.engine.wait_idle()
        self.assertEqual(self.result(), "EMERGENCY_STOP")
        self.assertFalse(self.engine.manager.psu.on)
        with self.assertRaises(ValueError):
            self.engine.psu_on()
        self.engine.acknowledge_fault()
        self.assertFalse(self.engine.manager.fault)

    def test_limits_rejected_before_power(self):
        for field, value in [
            ("psu_voltage", 100),
            ("input_current_limit", 20),
            ("load_current_a", 20),
            ("requested_power_w", 1000),
        ]:
            steps = self.steps()
            steps[-1][field] = value
            with self.assertRaises(ValueError):
                self.engine.start_sweep(dict(steps=steps))
        self.assertEqual(self.engine.get_runs(), [])
        self.assertFalse(self.engine.manager.psu.on)

    def test_empty_sweep_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.start_sweep(dict(steps=[]))

    def test_partial_sample_fails_instead_of_averaging_zero(self):
        self.engine.manager.load.measure_current = lambda: (_ for _ in ()).throw(
            IOError("invalid")
        )
        self.start()
        self.engine.wait_idle()
        self.assertEqual(self.result(), "ERROR")
        self.assertEqual(self.engine.manager.sweep_points, [])

    def test_production_success_traceability_csv(self):
        ok, _, rid = self.engine.start_production(
            "PCB-1", self.profile(), operator="operator"
        )
        self.assertTrue(ok)
        self.engine.wait_idle()
        run = self.engine.store.get_run(rid)
        self.assertEqual(run["final_result"], "PASS")
        self.assertEqual(run["operator"], "operator")
        self.assertEqual(run["dry_run"], 1)
        self.assertEqual(json.loads(run["profile_json"])["steps"][0]["wait_s"], 0)
        points = self.engine.store.get_run_points(rid)
        self.assertEqual([p["step_index"] for p in points], [0, 1, 2])
        with open(run["csv_path"]) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["unit_serial_or_label"], "PCB-1")
        self.assertEqual(rows[0]["final_result"], "PASS")

    def test_production_failure_and_reason(self):
        profile = self.profile()
        profile["steps"][0]["min_efficiency_percent"] = 99
        self.engine.start_production("PCB-2", profile)
        self.engine.wait_idle()
        self.assertEqual(self.result(), "FAIL")
        points = self.engine.store.get_run_points(self.engine.manager.prod_run_id)
        self.assertEqual(len(points), 1)
        self.assertIn("Efficiency", points[0]["fail_reason"])

    def test_production_exception_finalizes_db(self):
        self.engine.manager.psu.set_voltage = lambda v: (_ for _ in ()).throw(
            IOError("PSU lost")
        )
        self.engine.start_production("PCB-3", self.profile())
        self.engine.wait_idle()
        self.assertEqual(self.result(), "ERROR")
        self.assertIn("PSU lost", self.engine.get_runs()[0]["fail_reason"])

    def test_production_abort_not_pass(self):
        p = self.profile()
        p["steps"][0]["wait_s"] = 20
        self.engine.start_production("PCB-4", p)
        self.engine.abort_production()
        self.engine.wait_idle()
        self.assertEqual(self.result(), "ABORTED")

    def test_invalid_production_profile_cannot_skip_to_pass(self):
        p = self.profile()
        p["continue_on_fail"] = True
        p["steps"][0]["requested_power_w"] = 1000
        ok, _, _ = self.engine.start_production("PCB-5", p)
        self.assertFalse(ok)
        p["steps"] = []
        ok, _, _ = self.engine.start_production("PCB-5", p)
        self.assertFalse(ok)
        self.assertEqual(self.engine.get_runs(), [])

    def test_duplicate_labels_are_separate_retests(self):
        self.engine.start_production("PCB-6", self.profile())
        self.engine.wait_idle()
        self.engine.start_production("PCB-6", self.profile())
        self.engine.wait_idle()
        runs = self.engine.get_runs()
        self.assertEqual(len(runs), 2)
        self.assertNotEqual(runs[0]["id"], runs[1]["id"])

    def test_manual_record_exactly_one_and_finishes(self):
        points = []
        self.engine.on("measurement", lambda tab, point: points.append(point))
        self.engine.manual_record(psu_voltage=48, psu_current_limit=5, load_current=1)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["requested_power_w"], 12)
        self.engine.manual_start_new_run()
        self.assertEqual(self.result(), "COMPLETE")
        self.assertFalse(self.engine.manager.psu.on)

    def test_manual_fault_cannot_later_be_completed(self):
        self.engine.manual_record(psu_voltage=48, psu_current_limit=5, load_current=1)
        self.engine.manager.psu.measure_voltage = lambda: (_ for _ in ()).throw(
            IOError("unplugged")
        )
        self.engine.poll_status()
        self.engine.manual_start_new_run()
        self.assertEqual(self.result(), "ERROR")

    def test_manual_emergency_result_not_complete_on_close(self):
        self.engine.manual_record(psu_voltage=48, psu_current_limit=5, load_current=1)
        self.engine.emergency_stop()
        self.engine.close()
        self.assertEqual(self.result(), "EMERGENCY_STOP")

    def test_db_result_is_immutable(self):
        self.start()
        self.engine.wait_idle()
        with self.assertRaises(RuntimeError):
            self.engine.store.update_run_result(
                self.engine.manager.sweep_run_id, "PASS"
            )

    def test_storage_failure_stops_run(self):
        with patch.object(
            self.engine.store, "insert_test_point", side_effect=OSError("disk full")
        ):
            self.start()
            self.engine.wait_idle()
        self.assertEqual(self.result(), "ERROR")
        self.assertFalse(self.engine.manager.psu.on)

    def test_csv_failure_stops_run_and_saves_error(self):
        with patch.object(
            self.engine.store, "export_csv", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                self.start()
        self.assertEqual(self.result(), "ERROR")
        self.assertFalse(self.engine.manager.psu.on)

    def test_observer_failure_does_not_break_storage(self):
        self.engine.on(
            "measurement", lambda *args: (_ for _ in ()).throw(ValueError("UI failed"))
        )
        with self.assertLogs("test_engine.engine", level="ERROR"):
            self.start()
            self.engine.wait_idle()
        self.assertEqual(self.result(), "COMPLETE")

    def test_completed_graph_restore_and_selection(self):
        self.start()
        self.engine.wait_idle()
        rid = self.engine.manager.sweep_run_id
        self.assertEqual(self.engine.get_selected_runs(), [])
        self.engine.toggle_run_selection(rid)
        self.assertEqual(len(self.engine.get_selected_runs()), 1)
        self.engine.close()
        self.engine = TestEngine(
            dry_run=True, config=self.config, storage_root=self.directory.name
        )
        self.assertEqual(len(self.engine.get_completed_runs()), 1)
        self.assertEqual(self.engine.get_selected_runs(), [])

    def test_incomplete_run_not_graphable(self):
        self.start(wait=20)
        self.assertEqual(self.engine.get_completed_runs(), [])
        with self.assertRaises(ValueError):
            self.engine.toggle_run_selection(self.engine.manager.sweep_run_id)
        self.engine.stop_sweep()
        self.engine.wait_idle()
        self.assertEqual(self.engine.get_completed_runs(), [])

    def test_second_engine_same_database_rejected(self):
        with self.assertRaises(BlockingIOError):
            TestEngine(
                dry_run=True, config=self.config, storage_root=self.directory.name
            )

    def test_connect_twice_preserves_connection(self):
        psu = self.engine.manager.psu
        with self.assertRaises(BusyError):
            self.engine.connect()
        self.assertIs(self.engine.manager.psu, psu)
        self.assertTrue(self.engine.manager.connected)

    def test_logging_survives_second_run(self):
        self.start()
        self.engine.wait_idle()
        self.start()
        self.engine.wait_idle()
        with open(self.engine._tlog.path) as f:
            text = f.read()
        self.assertEqual(text.count(",RESULT,"), 2)

    def test_close_active_run_aborts_and_releases_storage(self):
        self.start(wait=20)
        self.engine.close()
        self.assertEqual(self.result(), "ABORTED")
        self.assertTrue(self.engine._closed)


class PureTests(unittest.TestCase):
    def test_nan_negative_and_infinity_rejected(self):
        for value in [float("nan"), float("inf"), -1]:
            self.assertTrue(validate_limits(voltage=value))
        self.assertFalse(check_production_step(float("nan"), float("nan"), {})[0])

    def test_simulation_no_power_when_psu_off(self):
        psu = SimPSU()
        load = SimLoad(psu)
        load.set_cc_current(10)
        load.input_on()
        self.assertEqual(load.measure_power(), 0)

    def test_exact_identity(self):
        self.assertEqual(discovery._classify("OWON,SPE15054,123,1.0"), "psu")
        self.assertEqual(discovery._classify("OWON,OEL1520,123,1.0"), "load")
        for value in ["", "OTHER,SPE15054,123", "OWON,DISPLAY,123", "OWON,ELSE,123"]:
            self.assertEqual(discovery._classify(value), "unknown")

    def test_query_malformed_values_rejected(self):
        class Resource:
            def query(self, c):
                return "NaN"

        with self.assertRaises(DriverError):
            OwonPSU(Resource()).measure_voltage()

    def test_off_verification_failure_not_suppressed(self):
        class Resource:
            def write(self, c):
                pass

            def query(self, c):
                return "ON"

        with patch("instruments.drivers.time.sleep"):
            with self.assertRaises(DriverError):
                OwonLoad(Resource()).safe_off()

    def test_graph_max_ten_and_newest(self):
        from test_engine.run_data_manager import RunDataManager

        model = RunDataManager()
        model.restore_runs(
            [
                dict(
                    id=i,
                    test_mode="automated_sweep",
                    final_result="COMPLETE",
                    timestamp_start="",
                    points=[dict(psu_set_voltage=48)],
                )
                for i in range(12)
            ]
        )
        model.select_all()
        self.assertEqual(model.selected_ids, list(range(11, 1, -1)))
        with self.assertRaises(ValueError):
            model.toggle_selection(0)

    def test_recovery_only_marks_new_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "results.db", Path(d) / "csv")
            rid = store.create_test_run("production_test")
            with store.connection() as c:
                c.execute(
                    "INSERT INTO test_runs(timestamp_start,test_mode) VALUES ('old','manual')"
                )
            store.close()
            store = Store(Path(d) / "results.db", Path(d) / "csv")
            self.assertEqual(store.get_run(rid)["final_result"], "INTERRUPTED")
            self.assertIsNone(store.get_run(rid + 1)["final_result"])
            store.close()


if __name__ == "__main__":
    unittest.main()
