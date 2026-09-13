"""Headless GUI and Flask integration; no production storage or hardware."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "owon-test-matplotlib"))
import copy
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication
from test_engine.config import load_config
from test_engine.engine import TestEngine
from desktop.main_window import MainWindow


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.audio_patch = patch('desktop.main_window.ResultSounds')
        self.audio_patch.start()
        self.addCleanup(self.audio_patch.stop)
        self.directory = tempfile.TemporaryDirectory()
        c = load_config()
        c.update(measurement_samples=1, settling_time_s=0)
        self.engine = TestEngine(
            dry_run=True, config=c, storage_root=self.directory.name
        )
        self.window = MainWindow(self.engine)
        self.window.show()
        self.drain(lambda: not self.window.controller.pending)

    def drain(self, condition, timeout=5):
        end = time.monotonic() + timeout
        while not condition() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertTrue(condition(), "Qt operation timed out")

    def tearDown(self):
        if not self.engine._closed:
            self.window.close()
            self.drain(lambda: self.engine._closed)
        self.drain(lambda: not self.window.controller.pending)
        self.directory.cleanup()

    def connect(self):
        self.window.connect_instruments()
        self.drain(lambda: not self.window.controller.pending)
        self.assertTrue(self.engine.manager.connected)

    def test_manual_one_row_worker_delivers_on_gui_thread(self):
        self.window.set_manager_mode(True)
        self.connect()
        threads = []
        self.window.controller.event.connect(
            lambda *a: threads.append(QThread.currentThread())
        )
        self.window.manual.load.setValue(1)
        self.window.manual.record()
        self.drain(lambda: not self.window.controller.pending)
        self.assertEqual(self.window.manual.model.rowCount(), 1)
        self.assertTrue(threads)
        self.assertTrue(all(t == self.app.thread() for t in threads))

    def test_gui_responsive_and_stop_while_settling(self):
        self.window.set_manager_mode(True)
        self.connect()
        self.window.sweep.delay.setValue(20)
        self.window.sweep.start()
        self.drain(lambda: self.engine.manager.status == "settling")
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        self.drain(lambda: len(ticks) >= 3)
        timer.stop()
        self.engine.stop_sweep()
        self.drain(lambda: self.engine.manager._sweep_state == "idle")
        self.assertEqual(self.engine.get_runs()[0]["final_result"], "STOPPED")

    def test_background_status_refresh_preserves_text_focus(self):
        self.window.set_manager_mode(True)
        self.window.tabs.setCurrentWidget(self.window.manual)
        self.connect()
        editor = self.window.manual.name
        editor.setFocus()
        editor.setText("typing while status refreshes")
        self.window.poll()
        self.assertIs(QApplication.focusWidget(), editor)
        self.drain(lambda: not self.window.controller.pending)
        self.assertIs(QApplication.focusWidget(), editor)
        self.assertEqual(editor.text(), "typing while status refreshes")

    def test_production_tab_is_initial_page_and_connection_indicators(self):
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertFalse(self.window.brand_logo.pixmap().isNull())
        self.assertIs(self.window.tabs.currentWidget(), self.window.production)
        self.assertIn("DISCONNECTED", self.window.psu_indicator.text())
        self.assertIn("DISCONNECTED", self.window.load_indicator.text())
        self.connect()
        self.assertIn("CONNECTED", self.window.psu_indicator.text())
        self.assertIn("CONNECTED", self.window.load_indicator.text())

    def test_starts_in_operator_mode_with_only_production_visible(self):
        self.assertFalse(self.window.manager_mode)
        visible = [self.window.tabs.tabText(i) for i in range(self.window.tabs.count())
                   if self.window.tabs.isTabVisible(i)]
        self.assertEqual(visible, ['Production'])
        self.assertTrue(self.window.psu_port.isHidden())
        self.assertTrue(self.window.production.thermal_enabled.isHidden())
        self.window.toggle_mode()
        self.assertTrue(self.window.manager_mode)
        self.assertTrue(self.window.tabs.isTabVisible(self.window.tabs.indexOf(self.window.manual)))
        self.assertTrue(self.window.tabs.isTabVisible(self.window.tabs.indexOf(self.window.sweep)))

    def test_return_to_operator_disconnects_and_cannot_switch_during_run(self):
        self.window.toggle_mode()
        self.connect()
        self.window.toggle_mode()
        self.drain(lambda: not self.window.controller.pending)
        self.assertFalse(self.window.manager_mode)
        self.assertFalse(self.engine.manager.connected)
        self.assertIs(self.window.tabs.currentWidget(), self.window.production)
        self.connect()
        profile = self.engine.get_production_profile()
        profile['steps'][0]['wait_s'] = 10
        self.engine.start_production('LOCK-MODE', profile)
        self.window.update_controls()
        self.assertFalse(self.window.mode_button.isEnabled())
        self.window.toggle_mode()
        self.assertFalse(self.window.manager_mode)
        self.engine.abort_production()
        self.engine.wait_idle()

    def test_manager_saves_recipe_and_sounds_for_restart(self):
        from pathlib import Path
        from test_engine.config import save_config
        self.window.toggle_mode()
        editor = self.window.settings_tab
        editor.name.setText('Factory recipe')
        editor.voltage.setValue(48)
        editor.thermal_enabled.setChecked(True)
        editor.thermal_limit.setValue(75)
        editor.warmup.setValue(20)
        editor.sound_enabled.setChecked(False)
        editor.steps.cellWidget(0, 1).setValue(15)
        settings_path = Path(self.directory.name) / 'saved-config.json'
        self.engine._config['dry_run'] = True
        with patch('test_engine.engine.cfg.save_config', side_effect=lambda cfg: save_config(cfg, settings_path)):
            editor.save()
            self.drain(lambda: not self.window.controller.pending)
        self.assertIn('Saved.', editor.message.text())
        saved = load_config(settings_path)
        self.assertEqual(saved['production_test_profile']['input_voltage'], 48)
        self.assertEqual(saved['production_test_profile']['steps'][0]['requested_power_w'], 15)
        self.assertEqual(saved['production_test_profile']['thermal_check']['max_temp_c'], 75)
        self.assertFalse(saved['result_sounds_enabled'])
        self.window.set_manager_mode(False)
        self.assertIn('75°C', self.window.production.profile.text())
        restored = TestEngine(config=saved, storage_root=Path(self.directory.name) / 'restart')
        try:
            other = MainWindow(restored)
            self.assertFalse(other.manager_mode)
            self.assertEqual(other.settings_tab.thermal_limit.value(), 75)
            self.assertEqual(other.settings_tab.warmup.value(), 20)
            other.close()
            self.drain(lambda: restored._closed)
            self.drain(lambda: not other.controller.pending)
        finally:
            if not restored._closed:
                restored.close()

    def test_operator_runs_saved_recipe_not_hidden_widget_overrides(self):
        self.connect()
        for step in self.engine._config['production_test_profile']['steps']:
            step['wait_s'] = 0
        tab = self.window.production
        tab.thermal_enabled.setChecked(True)
        tab.thermal_limit.setValue(20)
        tab.label.setText('SAVED-RECIPE')
        tab.start()
        self.drain(lambda: bool(self.engine.get_runs()) and self.engine.get_runs()[0]['final_result'] is not None)
        run = self.engine.get_runs()[0]
        self.assertEqual(run['thermal_result'], 'DISABLED')
        self.assertEqual(run['thermal_limit_c'], 80)
        self.drain(lambda: self.window.sounds.play.called)
        self.window.sounds.play.assert_called_with('PASS')

    def test_factory_result_banner_distinguishes_all_outcomes(self):
        tab = self.window.production
        tab.on_prod_result('PASS', '', '<PCB-42>')
        self.assertEqual(tab.result.text(), 'SIMULATION · PASS')
        self.assertIn('<PCB-42>', tab.result_detail.text())
        passed_style = tab.result.styleSheet()
        tab.on_prod_result('FAIL', 'Thermal maximum 85°C > 80°C limit', 'PCB-43')
        self.assertIn('Do not accept', tab.result_detail.text())
        self.assertIn('85°C', tab.result_detail.text())
        self.assertNotEqual(tab.result.styleSheet(), passed_style)
        tab.on_prod_result('ABORTED', 'Stopped by operator', 'PCB-44')
        self.assertIn('No PASS recorded', tab.result_detail.text())
        self.assertNotEqual(tab.result.styleSheet(), passed_style)

    def test_graph_checkboxes_appear_after_completion(self):
        self.window.set_manager_mode(True)
        self.connect()
        self.window.sweep.count.setValue(2)
        self.window.sweep.delay.setValue(0)
        self.window.sweep.start()
        self.drain(lambda: len(self.engine.get_completed_runs()) == 1)
        self.drain(lambda: self.window.graph.checks.count() == 1)
        cb = self.window.graph.checks.itemAt(0).widget()
        cb.setChecked(True)
        self.drain(lambda: len(self.window.graph.ax.lines) == 1)
        self.assertEqual(self.window.graph.ax.get_xlabel(), "Output power (W)")

    def test_optional_thermal_entry_and_history(self):
        self.connect()
        tab = self.window.production
        self.assertFalse(tab.thermal_enabled.isChecked())
        self.assertEqual(tab.thermal_limit.value(), 80)
        self.assertFalse(tab.thermal_entry.isEnabled())
        for step in self.engine._config['production_test_profile']['steps']:
            step['wait_s'] = 0
        self.engine._config['production_test_profile']['thermal_check'].update(enabled=True, warmup_s=0)
        tab.refresh_profile()
        tab.label.setText('THERMAL-UI')
        tab.thermal_measured.setText('old value')
        tab.start()
        self.drain(lambda: tab.thermal_entry.isEnabled())
        self.assertEqual(tab.thermal_measured.text(), '')
        self.assertFalse(tab.controls.isEnabled())
        tab.thermal_measured.setText('30.8')
        self.window.poll()
        self.drain(lambda: not self.window.controller.pending)
        self.assertEqual(tab.thermal_measured.text(), '30.8')
        tab.record_thermal()
        self.drain(lambda: 'PASS' in tab.result.text())
        self.drain(lambda: not self.window.controller.pending)
        self.assertFalse(tab.thermal_entry.isEnabled())
        self.assertIn('30.8', tab.thermal_check_status.text())
        self.assertEqual(self.window.history.item(0, 7).text(), 'PASS')
        self.assertEqual(self.window.history.item(0, 8).text(), '30.8 / 80')

    def test_close_during_thermal_entry_aborts(self):
        self.connect()
        profile = self.engine.get_production_profile()
        for step in profile['steps']:
            step['wait_s'] = 0
        profile['thermal_check'] = dict(enabled=True, max_temp_c=80, warmup_s=0)
        self.engine.start_production('THERMAL-CLOSE', profile)
        self.drain(lambda: self.window.production.thermal_entry.isEnabled())
        self.window.close()
        self.drain(lambda: self.engine._closed)
        run = self.engine.get_runs()[0]
        self.assertEqual(run['final_result'], 'ABORTED')
        self.assertEqual(run['thermal_result'], 'ABORTED')

    def test_close_during_production_finishes_before_window_exits(self):
        self.connect()
        p = copy.deepcopy(self.engine.get_production_profile())
        p["steps"][0]["wait_s"] = 20
        self.engine.start_production("CLOSE-TEST", p)
        self.window.close()
        self.drain(lambda: self.engine._closed)
        self.assertEqual(self.engine.get_runs()[0]["final_result"], "ABORTED")
        self.assertTrue(self.window._can_close)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        c = load_config()
        c.update(measurement_samples=1, settling_time_s=0)
        self.engine = TestEngine(
            dry_run=True, config=c, storage_root=self.directory.name
        )
        from ui_web import create_app

        self.app, _, _ = create_app(test_engine=self.engine, dry_run=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.engine.close()
        self.directory.cleanup()

    def test_page_and_safe_control_routes(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertTrue(self.client.post("/api/connect").json["success"])
        self.assertEqual(
            self.client.post(
                "/api/psu/set", json=dict(voltage=100, current=5)
            ).status_code,
            400,
        )
        self.assertFalse(self.engine.manager.psu.on)
        r = self.client.post(
            "/api/manual/set_psu", json=dict(voltage=48, current_limit=5)
        )
        self.assertTrue(r.json["success"])
        self.assertTrue(self.client.post("/api/psu/output_on").json["success"])

    def test_manual_route_single_point_and_csv(self):
        self.engine.connect()
        r = self.client.post(
            "/api/manual/record",
            json=dict(psu_voltage=48, psu_current_limit=5, load_current=1),
        )
        self.assertTrue(r.json["success"])
        self.assertEqual(len(self.engine.manager.manual_points), 1)
        response = self.client.get("/api/csv/download")
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_thermal_routes_gate_production_result(self):
        self.engine.connect()
        for step in self.engine._config['production_test_profile']['steps']:
            step['wait_s'] = 0
        response = self.client.post('/api/production/start', json={
            'label': 'THERMAL-WEB', 'thermal_check': {'enabled': True, 'warmup_s': 0},
        })
        self.assertTrue(response.json['success'])
        rid = response.json['run_id']
        end = time.monotonic() + 3
        while time.monotonic() < end:
            pending = self.client.get('/api/production/thermal').json['pending']
            if pending and pending['phase'] == 'awaiting_reading':
                break
            time.sleep(0.01)
        self.assertEqual(pending['max_temp_c'], 80)
        response = self.client.post('/api/production/thermal', json={'run_id': rid, 'max_temp_c': ''})
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/production/thermal', json={'run_id': rid, 'max_temp_c': 81})
        self.assertTrue(response.json['success'])
        self.engine.wait_idle()
        self.assertEqual(self.engine.store.get_run(rid)['final_result'], 'FAIL')
        self.assertIsNone(self.client.get('/api/production/thermal').json['pending'])

    def test_graph_excludes_incomplete(self):
        self.engine.connect()
        from test_engine.test_profiles import generate_sweep_steps

        steps = generate_sweep_steps(num_steps=1, input_voltage=48)
        self.engine.start_sweep(dict(steps=steps, wait_time=20))
        self.assertEqual(self.client.get("/api/graph").json["runs"], [])
        self.engine.stop_sweep()
        self.engine.wait_idle()
        self.assertEqual(self.client.get("/api/graph").json["runs"], [])
