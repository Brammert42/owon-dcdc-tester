#!/usr/bin/env python3
"""Desktop launcher. Real hardware is connected only by an explicit Connect action."""

import argparse
import logging
import os
import signal
import sys
from pathlib import Path
from app_paths import resource_path, runtime_root


def main():
    parser = argparse.ArgumentParser(description="DC/DC desktop production tester")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use simulated instruments and separate storage",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Override storage root (use a separate directory for testing)",
    )
    parser.add_argument('--smoke-test', type=Path, metavar='REPORT.json', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_test:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from desktop.build_check import run_smoke_test
        return run_smoke_test(args.smoke_test)
    handlers = []
    if getattr(sys, 'frozen', False):
        log_dir = runtime_root() / 'data' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / 'startup.log', encoding='utf-8'))
    else:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from test_engine.engine import TestEngine
    from desktop.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("DC/DC Production Tester")
    app.setDesktopFileName("owon-dcdc-tester")
    app.setWindowIcon(QIcon(str(resource_path('static', 'logo.png'))))
    try:
        engine = TestEngine(
            dry_run=True if args.dry_run else None, storage_root=args.data_dir
        )
    except Exception as exc:
        QMessageBox.critical(None, "Startup failed", str(exc))
        return 1
    window = MainWindow(engine)
    window.show()
    # Qt needs an active timer to allow Python signal delivery.
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(250)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: window.close())
    try:
        return app.exec()
    finally:
        if not engine._closed:
            engine.request_stop("ABORTED")
            try:
                engine.close()
            except Exception:
                logging.exception("Shutdown could not be verified")


if __name__ == "__main__":
    sys.exit(main())
