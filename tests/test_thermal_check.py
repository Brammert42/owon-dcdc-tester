"""Production thermal gating, traceability and cancellation using simulated instruments."""

import copy
import csv
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_engine.config import load_config
from test_engine.engine import TestEngine, BusyError


class ThermalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        config = load_config()
        config.update(measurement_samples=1, settling_time_s=0)
        self.engine = TestEngine(dry_run=True, config=config, storage_root=self.directory.name)
        self.engine.connect()
        self.profile = copy.deepcopy(self.engine.get_production_profile())
        for step in self.profile['steps']:
            step['wait_s'] = 0
        self.profile['thermal_check'] = dict(enabled=True, max_temp_c=80, warmup_s=0)

    def tearDown(self):
        self.engine.request_stop('ABORTED')
        self.engine.close()
        self.directory.cleanup()

    def start(self):
        ok, message, rid = self.engine.start_production('THERMAL-PCB', self.profile, operator='Worker')
        self.assertTrue(ok, message)
        return rid

    def submit_on_request(self, value):
        def handler(pending):
            if pending and pending['phase'] == 'awaiting_reading' and self.engine._thermal_reading is None:
                self.engine.submit_thermal_reading(pending['run_id'], value)
        self.engine.on('thermal_check', handler)

    def assert_off(self):
        self.assertFalse(self.engine.manager.psu.on)
        self.assertFalse(self.engine.manager.load.on)

    def test_equal_threshold_passes_and_trace_is_exported(self):
        self.submit_on_request(80)
        rid = self.start()
        self.engine.wait_idle()
        run = self.engine.store.get_run(rid)
        self.assertEqual(run['final_result'], 'PASS')
        self.assertEqual(run['thermal_result'], 'PASS')
        self.assertEqual(run['thermal_max_c'], 80)
        self.assertEqual(run['thermal_limit_c'], 80)
        self.assertEqual(run['thermal_source'], 'operator_entered')
        self.assertTrue(run['thermal_timestamp'])
        self.assertEqual(json.loads(run['profile_json'])['thermal_check']['warmup_s'], 0)
        with open(run['csv_path']) as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(all(row['thermal_max_c'] == '80.0' for row in rows))
        self.assertTrue(all(row['final_result'] == 'PASS' for row in rows))
        self.assert_off()

    def test_over_threshold_fails_overall_run(self):
        self.submit_on_request(80.1)
        rid = self.start()
        self.engine.wait_idle()
        run = self.engine.store.get_run(rid)
        self.assertEqual(run['final_result'], 'FAIL')
        self.assertEqual(run['thermal_result'], 'FAIL')
        self.assertIn('80.1°C > 80°C', run['fail_reason'])
        self.assert_off()

    def test_no_reading_times_out_and_cannot_pass(self):
        with patch('test_engine.engine.THERMAL_INPUT_TIMEOUT_S', 0.05):
            rid = self.start()
            self.engine.wait_idle()
        run = self.engine.store.get_run(rid)
        self.assertEqual(run['final_result'], 'FAIL')
        self.assertEqual(run['thermal_result'], 'MISSING')
        self.assertIsNone(run['thermal_max_c'])
        self.assert_off()

    def test_disabled_check_preserves_electrical_workflow(self):
        self.profile['thermal_check']['enabled'] = False
        events = []
        self.engine.on('thermal_check', events.append)
        rid = self.start()
        self.engine.wait_idle()
        run = self.engine.store.get_run(rid)
        self.assertEqual(run['final_result'], 'PASS')
        self.assertEqual(run['thermal_result'], 'DISABLED')
        self.assertEqual(events, [])
        self.assertEqual(len(self.engine.store.get_run_points(rid)), 3)

    def test_invalid_config_rejected_before_outputs(self):
        for key, value in [('max_temp_c', float('nan')), ('max_temp_c', ''),
                           ('max_temp_c', True), ('max_temp_c', 551),
                           ('warmup_s', -1), ('enabled', 'true')]:
            with self.subTest(key=key, value=value):
                settings = dict(enabled=True, max_temp_c=80, warmup_s=0)
                settings[key] = value
                ok, _, _ = self.engine.start_production('PCB', self.profile, thermal_check=settings)
                self.assertFalse(ok)
                self.assertEqual(self.engine.get_runs(), [])
                self.assert_off()

    def test_reading_is_bound_to_run_and_cannot_be_reused(self):
        ready = threading.Event()
        self.engine.on('thermal_check', lambda p: ready.set() if p and p['phase'] == 'awaiting_reading' else None)
        rid = self.start()
        self.assertTrue(ready.wait(3))
        self.assertIsNone(self.engine.store.get_run(rid)['final_result'])
        for value in ('', 'nan', 'inf', True, 551):
            with self.assertRaises(ValueError):
                self.engine.submit_thermal_reading(rid, value)
        with self.assertRaises(BusyError):
            self.engine.submit_thermal_reading(rid + 1, 25)
        self.engine.submit_thermal_reading(rid, 30.8)
        with self.assertRaises(BusyError):
            self.engine.submit_thermal_reading(rid, 25)
        self.engine.wait_idle()
        with self.assertRaises(BusyError):
            self.engine.submit_thermal_reading(rid, 25)
        with patch('test_engine.engine.THERMAL_INPUT_TIMEOUT_S', 0.05):
            next_id = self.start()
            self.engine.wait_idle()
        self.assertEqual(self.engine.store.get_run(next_id)['thermal_result'], 'MISSING')

    def test_abort_during_warmup_or_entry_shuts_down(self):
        for phase in ('warming', 'awaiting_reading'):
            with self.subTest(phase=phase):
                def stop(pending):
                    if pending and pending['phase'] == phase:
                        self.engine.abort_production()
                self.engine.on('thermal_check', stop)
                rid = self.start()
                self.engine.wait_idle()
                self.engine.off('thermal_check', stop)
                self.assertEqual(self.engine.store.get_run(rid)['final_result'], 'ABORTED')
                self.assertEqual(self.engine.store.get_run(rid)['thermal_result'], 'ABORTED')
                self.assertIsNone(self.engine.get_thermal_check())
                self.assert_off()

    def test_electrical_failure_skips_thermal_and_stays_fail(self):
        self.profile['steps'][0]['min_efficiency_percent'] = 99
        rid = self.start()
        self.engine.wait_idle()
        self.assertEqual(self.engine.store.get_run(rid)['final_result'], 'FAIL')
        self.assertEqual(self.engine.store.get_run(rid)['thermal_result'], 'NOT_RUN')
        self.assert_off()

    def test_electrical_measurements_remain_checked_during_hold(self):
        self.profile['thermal_check']['warmup_s'] = 1
        def break_readback(pending):
            if pending and pending['phase'] == 'warming':
                self.engine.manager.load.measure_voltage = lambda: 0
        self.engine.on('thermal_check', break_readback)
        rid = self.start()
        self.engine.wait_idle()
        self.assertEqual(self.engine.store.get_run(rid)['final_result'], 'ERROR')
        self.assertEqual(self.engine.store.get_run(rid)['thermal_result'], 'ERROR')
        self.assert_off()
