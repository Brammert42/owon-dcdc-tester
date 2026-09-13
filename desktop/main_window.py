"""Desktop-first application: asynchronous commands and explicit hardware state."""

import copy
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from app_paths import resource_path
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTabWidget,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
)
from desktop.controller import Controller
from desktop.manual_tab import ManualTab
from desktop.sweep_tab import SweepTab
from desktop.production_tab import ProductionTab
from desktop.graph_widget import GraphWidget
from desktop.theme import DARK_QSS
from desktop.manager_tab import ManagerTab
from desktop.result_sounds import ResultSounds
from test_engine.engine import TestEngine


class MainWindow(QMainWindow):
    def __init__(self, engine=None):
        super().__init__()
        self.setWindowTitle("DC/DC Production Tester")
        logo_path = resource_path('static', 'logo.png')
        self.setWindowIcon(QIcon(str(logo_path)))
        self.resize(1200, 900)
        self.engine = engine or TestEngine()
        self.controller = Controller(self.engine, self)
        self.manager_mode = False
        self.sounds = ResultSounds(self.engine, self)
        self._closing = False
        self._can_close = False
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        mode = (
            "SIMULATION — separate results" if self.engine.dry_run else "REAL HARDWARE"
        )
        header = QHBoxLayout()
        self.brand_logo = QLabel()
        self.brand_logo.setPixmap(QPixmap(str(logo_path)).scaled(
            64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ))
        self.brand_logo.setAccessibleName("Application logo")
        header.addWidget(self.brand_logo)
        title = QLabel("DC/DC Production Tester")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #ffffff;")
        header.addWidget(title)
        header.addStretch()
        self.mode_badge = QLabel("OPERATOR MODE")
        self.mode_badge.setStyleSheet("font-size: 16px; font-weight: bold; color: #ffffff;")
        header.addWidget(self.mode_badge)
        self.mode_button = QPushButton("Switch to manager mode")
        self.mode_button.clicked.connect(self.toggle_mode)
        header.addWidget(self.mode_button)
        header.addWidget(QLabel(mode))
        layout.addLayout(header)
        self.connection_controls = QWidget()
        row = QHBoxLayout(self.connection_controls)
        self.psu_port = QComboBox()
        self.load_port = QComboBox()
        self.psu_port.addItem("Automatic / configured PSU", "")
        self.load_port.addItem("Automatic / configured load", "")
        row.addWidget(self.psu_port)
        row.addWidget(self.load_port)
        self.manager_connection_widgets = [self.psu_port, self.load_port]
        for label, fn in [
            ("Identify ports", self.scan),
            ("Connect", self.connect_instruments),
            ("Disconnect", lambda: self.controller.submit(self.engine.disconnect)),
            ("Save ports", self.save_ports),
        ]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
            if label in ("Identify ports", "Save ports"):
                self.manager_connection_widgets.append(b)
        self.psu_indicator = QLabel("PSU: DISCONNECTED")
        self.load_indicator = QLabel("LOAD: DISCONNECTED")
        row.addWidget(self.psu_indicator)
        row.addWidget(self.load_indicator)
        layout.addWidget(self.connection_controls)
        self.status = QLabel("Disconnected")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color:#f38ba8;")
        layout.addWidget(self.error)
        self.tabs = QTabWidget()
        self.manual = ManualTab(self.engine, self.controller)
        self.sweep = SweepTab(self.engine, self.controller)
        self.production = ProductionTab(self.engine, self.controller)
        self.graph = GraphWidget(self.engine)
        for widget, label in [
            (self.manual, "Manual"),
            (self.sweep, "Sweep"),
            (self.production, "Production"),
            (self.graph, "Graph"),
        ]:
            self.tabs.addTab(widget, label)
        self.history_widget = QWidget()
        history_layout = QVBoxLayout(self.history_widget)
        refresh = QPushButton("Refresh run history")
        refresh.clicked.connect(self.refresh_history)
        history_layout.addWidget(refresh)
        self.history = QTableWidget(0, 9)
        self.history.setHorizontalHeaderLabels(
            ["Run", "Started", "Mode", "PCB", "Result", "Shutdown verified", "CSV", "Thermal", "Max / limit (°C)"]
        )
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        history_layout.addWidget(self.history)
        self.tabs.addTab(self.history_widget, "History")
        self.settings_tab = ManagerTab(self.engine, self.controller, self.settings_saved, self.sounds)
        self.tabs.addTab(self.settings_tab, "Test settings")
        layout.addWidget(self.tabs)
        self.tabs.setCurrentWidget(self.production)
        safety_row = QHBoxLayout()
        stop = QPushButton("EMERGENCY STOP")
        stop.setProperty("danger", True)
        stop.clicked.connect(self.emergency_stop)
        self.ack = QPushButton("Acknowledge fault (verify outputs OFF)")
        self.ack.clicked.connect(
            lambda: self.controller.submit(self.engine.acknowledge_fault)
        )
        safety_row.addWidget(stop)
        safety_row.addWidget(self.ack)
        layout.addLayout(safety_row)
        self.setStyleSheet(DARK_QSS)
        self.controller.event.connect(self.on_event, Qt.ConnectionType.QueuedConnection)
        self.controller.busy_changed.connect(lambda _: self.update_controls())
        self.controller.error.connect(self.show_error)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.set_manager_mode(False)
        self.update_controls()
        self.refresh_history()

    def settings_saved(self):
        self.production.refresh_profile()

    def set_manager_mode(self, enabled):
        self.manager_mode = bool(enabled)
        self.mode_badge.setText("MANAGER MODE" if enabled else "OPERATOR MODE")
        self.mode_button.setText("Return to operator mode" if enabled else "Switch to manager mode")
        for widget in self.manager_connection_widgets:
            widget.setVisible(enabled)
        for tab in (self.manual, self.sweep, self.graph, self.history_widget, self.settings_tab):
            self.tabs.setTabVisible(self.tabs.indexOf(tab), enabled)
        self.tabs.setCurrentWidget(self.settings_tab if enabled else self.production)
        self.production.refresh_profile()
        self.update_controls()

    def toggle_mode(self):
        if not self.mode_button.isEnabled():
            return
        if not self.manager_mode:
            self.settings_tab.reload()
            self.set_manager_mode(True)
        elif self.engine.manager.connected:
            self.controller.submit(self.engine.disconnect, success=lambda _: self.set_manager_mode(False))
        else:
            self.set_manager_mode(False)

    def show_error(self, message):
        self.error.setText(message)
        self.update_controls()

    def update_controls(self):
        status = self.engine.manager.get_status_dict()
        self._set_connection_indicator(
            self.psu_indicator, "PSU", status["psu_connected"]
        )
        self._set_connection_indicator(
            self.load_indicator, "LOAD", status["load_connected"]
        )
        # Status polling is background work. It must not disable the editor that
        # currently owns keyboard focus every two seconds.
        operation_busy = status["busy"] not in ("", "status", None)
        busy = operation_busy or self.controller.foreground_pending or self._closing
        self.mode_button.setEnabled(not busy)
        self.settings_tab.setEnabled(self.manager_mode and not busy)
        self.settings_tab.save_button.setEnabled(self.manager_mode and not busy and not status['connected'])
        self.connection_controls.setEnabled(not busy)
        enabled = status["connected"] and not busy and not status["fault"]
        for tab in (self.manual, self.sweep, self.production):
            tab.controls.setEnabled(enabled and (tab is self.production or self.manager_mode))
        self.ack.setEnabled(status["connected"] and bool(status["fault"]) and not busy)

    @staticmethod
    def _set_connection_indicator(widget, name, connected):
        if connected:
            widget.setText(f"{name}: CONNECTED")
            widget.setStyleSheet("color: #a6e3a1; font-weight: bold;")
        else:
            widget.setText(f"{name}: DISCONNECTED")
            widget.setStyleSheet("color: #f38ba8; font-weight: bold;")

    def connect_instruments(self):
        self.error.setText("")
        self.controller.submit(
            self.engine.connect,
            psu_port=(self.psu_port.currentData() or None) if self.manager_mode else None,
            load_port=(self.load_port.currentData() or None) if self.manager_mode else None,
        )

    def scan(self):
        def show(results):
            self.psu_port.clear()
            self.load_port.clear()
            self.psu_port.addItem("Automatic / configured PSU", "")
            self.load_port.addItem("Automatic / configured load", "")
            for result in results:
                target = (
                    self.psu_port
                    if result["device_type"] == "psu"
                    else self.load_port
                    if result["device_type"] == "load"
                    else None
                )
                if target:
                    target.addItem(
                        f"{result['port']} — {result['idn']}", result["port"]
                    )
                    if target.count() == 2:
                        target.setCurrentIndex(1)
            self.error.setText(
                "\n".join(
                    f"{r['port']}: {r['error']}" for r in results if r.get("error")
                )
                or "Identification complete."
            )

        self.controller.submit(self.engine.scan_ports, success=show)

    def save_ports(self):
        if not self.psu_port.currentData() or not self.load_port.currentData():
            self.show_error("Identify and select both ports first.")
            return
        config = copy.deepcopy(self.engine._config)
        config["serial_ports"] = dict(
            psu=self.psu_port.currentData(), load=self.load_port.currentData()
        )
        self.controller.submit(self.engine.save_configuration, config)

    def emergency_stop(self):
        self.engine.request_stop("EMERGENCY_STOP")
        self.error.setText(
            "Emergency stop requested. Waiting for shutdown verification…"
        )
        self.controller.submit(
            self.engine.emergency_stop,
            safety=True,
            success=lambda _: self.error.setText(
                "OFF commands verified. Fault remains latched until acknowledged."
            ),
        )

    def poll(self):
        if not self.controller.pending and not self._closing:
            self.controller.submit(self.engine.poll_status, background=True)

    def on_event(self, event, args):
        if event == "status_update":
            s = args[0]

            def state(value):
                return "UNKNOWN" if value is None else "ON" if value else "OFF"

            def val(value):
                return "—" if value is None else f"{value:.4f}"

            self.status.setText(
                f"{s['status'].upper()} | PSU {state(s['psu_output_on'])} | Load {state(s['load_input_on'])}\n"
                f"Vin {val(s['psu']['vin'])} V / Iin {val(s['psu']['iin'])} A | Vout {val(s['load']['vout'])} V / Iout {val(s['load']['iout'])} A | η {val(s['efficiency'])}%\n"
                f"Last sample: {s.get('measurement_timestamp') or 'none'}"
            )
            if s["fault"]:
                self.error.setText(s["fault"])
            elif not self._closing:
                self.error.setText("")
            self.update_controls()
        elif event == "measurement":
            self.manual.on_measurement(*args)
            self.sweep.on_measurement(*args)
        elif event == "clear_run":
            {
                "manual": self.manual.model,
                "sweep": self.sweep.model,
                "production": self.production.model,
            }[args[0]].clear()
        elif event == "prod_step":
            self.production.on_prod_step(*args)
        elif event == "prod_result":
            self.production.on_prod_result(*args)
            self.sounds.play(args[0])
            self.refresh_history()
        elif event == "thermal_check":
            self.production.on_thermal_check(*args)
        elif event == "sweep_done":
            self.sweep.status.setText("Complete — select the run in Graph")
            self.refresh_history()
        elif event == "sweep_error":
            self.sweep.status.setText(args[0])
            self.refresh_history()
        elif event == "graph_data_changed":
            self.graph.schedule()

    def refresh_history(self):
        if self._closing:
            return

        def show(runs):
            self.history.setRowCount(len(runs))
            for row, run in enumerate(runs):
                values = [
                    run["id"],
                    run["timestamp_start"],
                    run["test_mode"],
                    run["unit_serial_or_label"],
                    run["final_result"] or "Unknown / unfinished",
                    "YES" if run.get("shutdown_verified") else "NO / UNKNOWN",
                    run.get("csv_path"),
                    run.get("thermal_result") or "—",
                    (f"{run['thermal_max_c']:g} / {run['thermal_limit_c']:g}"
                     if run.get("thermal_max_c") is not None else "—"),
                ]
                for col, value in enumerate(values):
                    self.history.setItem(row, col, QTableWidgetItem(str(value or "")))
            self.history.resizeColumnsToContents()

        self.controller.submit(self.engine.get_runs, success=show)

    def closeEvent(self, event):
        if self._can_close:
            event.accept()
            return
        event.ignore()
        if self._closing:
            return
        self._closing = True
        self.timer.stop()
        self.engine.request_stop("ABORTED")
        self.update_controls()
        self.error.setText("Stopping tests and verifying outputs OFF before exit…")

        def done(_):
            self.controller.shutdown()
            self._can_close = True
            self.close()

        def failed(message):
            self._closing = False
            self.error.setText(
                "Shutdown could not be verified: "
                + message
                + " — use the physical disconnect."
            )
            if self.engine._closed:
                QMessageBox.critical(self, "Shutdown not verified", self.error.text())
                self.controller.shutdown()
                self._can_close = True
                self.close()
            else:
                self.update_controls()

        self.controller.submit(self.engine.close, success=done, failure=failed)
