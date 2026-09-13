"""
ui_web — Thin Flask+SocketIO layer over the shared test_engine backend.

Exposes a create_app() factory that instantiates a TestEngine, subscribes
its callbacks to SocketIO emits, and registers all HTTP routes.
"""

from __future__ import annotations

import logging
import os

from flask import Flask
from flask_socketio import SocketIO

from test_engine import TestEngine, load_config, is_dry_run

logger = logging.getLogger(__name__)

socketio = SocketIO()


def create_app(
    test_engine: TestEngine | None = None, dry_run: bool | None = None
) -> tuple[Flask, SocketIO, TestEngine]:
    """Create and configure the Flask application.

    Args:
        test_engine: Optional pre-configured engine.  If omitted, creates one.
        dry_run: Override for dry-run mode.  If omitted, reads from config.

    Returns:
        (app, socketio, engine) triple.
    """
    config = load_config()
    if dry_run is None:
        dry_run = is_dry_run(config)

    from pathlib import Path

    base_dir = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(base_dir / "templates"),
        static_folder=str(base_dir / "static"),
    )
    app.config["SECRET_KEY"] = os.urandom(16).hex()

    # SocketIO
    socketio.init_app(app, async_mode="threading")

    # Engine
    engine = test_engine or TestEngine(dry_run=dry_run, config=config)

    # ── Wire engine events → SocketIO ──────────────────────────────
    engine.on("graph_data_changed", lambda: socketio.emit("graph_data_changed", {}))
    engine.on("status_update", lambda d: socketio.emit("status_update", d))
    engine.on(
        "measurement",
        lambda tab, pt: socketio.emit("measurement", {"tab": tab, "point": pt}),
    )
    engine.on("sweep_error", lambda msg: socketio.emit("sweep_error", {"message": msg}))
    engine.on("sweep_done", lambda: socketio.emit("sweep_done", {}))
    engine.on("clear_run", lambda tab: socketio.emit("clear_run", {"tab": tab}))
    engine.on(
        "emergency_stop",
        lambda: socketio.emit("status_update", {"status": "emergency_stop"}),
    )

    def _on_prod_step(step_name, status, failure_reason, point):
        payload = {
            "step_name": step_name,
            "status": status,
            "failure_reason": failure_reason,
        }
        if point:
            payload["point"] = point
        socketio.emit("prod_step", payload)

    def _on_prod_result(result, fail_reason, label):
        socketio.emit(
            "prod_result",
            {"result": result, "fail_reason": fail_reason, "label": label},
        )

    engine.on("prod_step", _on_prod_step)
    engine.on("prod_result", _on_prod_result)
    engine.on("thermal_check", lambda pending: socketio.emit("thermal_check", {"pending": pending}))

    # ── Register routes ─────────────────────────────────────────────
    from . import routes

    routes.register_routes(app, engine)

    return app, socketio, engine
