#!/usr/bin/env python3
"""Optional localhost web UI using the same engine as the desktop."""

import argparse
import logging
import signal
from pathlib import Path
from ui_web import create_app
from test_engine.engine import TestEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-dry-run", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    if args.dry_run and args.no_dry_run:
        parser.error("Choose one instrument mode")
    logging.basicConfig(level=logging.INFO)
    engine = TestEngine(
        dry_run=True if args.dry_run else False if args.no_dry_run else None,
        storage_root=args.data_dir,
    )
    app, socketio, _ = create_app(test_engine=engine, dry_run=engine.dry_run)

    def stop(*_):
        engine.request_stop("ABORTED")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        socketio.run(
            app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True
        )
    finally:
        engine.close()


if __name__ == "__main__":
    main()
