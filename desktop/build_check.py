"""Frozen-build smoke check: Qt, assets, storage and simulated production only."""

import json
from pathlib import Path
import tempfile
import time
import traceback


def run_smoke_test(report_path):
    from PySide6.QtWidgets import QApplication
    from app_paths import resource_path
    from test_engine.engine import TestEngine
    from test_engine.config import load_config
    from desktop import main_window

    app = QApplication.instance() or QApplication([])
    engine = window = None
    report = {'ok': False}
    with tempfile.TemporaryDirectory(prefix='owon-build-check-') as tmp:
        try:
            # Build agents may have no audio server. Assets are checked without playing them.
            class SilentSounds:
                def __init__(self, *args, **kwargs):
                    pass
                def play(self, *args, **kwargs):
                    pass
            main_window.ResultSounds = SilentSounds
            config = load_config()
            config['measurement_samples'] = 1
            engine = TestEngine(dry_run=True, config=config, storage_root=tmp)
            window = main_window.MainWindow(engine)
            window.show()
            app.processEvents()
            assert not window.windowIcon().isNull(), 'Missing application icon'
            assert not window.manager_mode, 'Must start in operator mode'
            assert [window.tabs.tabText(i) for i in range(window.tabs.count())
                    if window.tabs.isTabVisible(i)] == ['Production']
            for name in ('pass.wav', 'fail.wav'):
                assert resource_path('static', 'sounds', name).is_file(), f'Missing {name}'
            engine.connect()  # dry_run=True: simulated instruments only
            profile = dict(name='Build smoke test', input_voltage=48, input_current_limit=5,
                           nominal_output_voltage=12, steps=[dict(name='Simulated point',
                           requested_power_w=12, wait_s=0, min_vout=11.5, max_vout=12.7,
                           min_efficiency_percent=90)], thermal_check={'enabled': False})
            ok, message, rid = engine.start_production('BUILD-SMOKE', profile)
            assert ok, message
            engine.wait_idle(20)
            app.processEvents()
            run = engine.store.get_run(rid)
            assert run['final_result'] == 'PASS', run['fail_reason']
            assert run['shutdown_verified'] and run['dry_run']
            assert Path(run['csv_path']).is_file(), 'Missing CSV export'
            report.update(ok=True, operator_mode=True, simulation_result='PASS',
                          assets_present=True, shutdown_verified=True)
        except Exception:
            report['error'] = traceback.format_exc()
        finally:
            if window is not None:
                window.close()
                deadline = time.monotonic() + 10
                while (not engine._closed or window.controller.pending) and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
            if engine is not None and not engine._closed:
                engine.close()
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if report['ok'] else 1
