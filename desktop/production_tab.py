"""Operator label entry, explicit profile display and terminal results."""

from threading import Thread
import sys
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QPixmap
from instruments.thermal_camera import capture_snapshot

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QTableView,
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QScrollArea,
)
from desktop.models import MeasurementTableModel


class ProductionTab(QWidget):
    thermal_completed = Signal(str, str)

    def __init__(self, engine, controller, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.controller = controller
        outer = QVBoxLayout(self)
        self.result = QLabel()
        self.result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result.setTextFormat(Qt.TextFormat.PlainText)
        self.result.setMinimumHeight(100)
        self.result_detail = QLabel()
        self.result_detail.setWordWrap(True)
        self.result_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.result_detail.setStyleSheet("font-size: 18px; color: #ffffff;")
        outer.addWidget(self.result)
        outer.addWidget(self.result_detail)
        self.show_result("READY", "Scan a PCB identifier, then start the test.")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.controls = QWidget()
        form = QFormLayout(self.controls)
        self.label = QLineEdit()
        self.label.setPlaceholderText("Scan or enter PCB serial number")
        self.label.setMaxLength(200)
        self.operator = QLineEdit()
        form.addRow("PCB identifier", self.label)
        form.addRow("Operator", self.operator)
        thermal = (engine.get_production_profile() or {}).get("thermal_check", {})
        self.thermal_enabled = QCheckBox("Require thermal check")
        self.thermal_enabled.setChecked(thermal.get("enabled", False))
        self.thermal_enabled.setParent(self.controls)
        self.thermal_enabled.hide()
        self.thermal_limit = QDoubleSpinBox()
        self.thermal_limit.setRange(-20, 550)
        self.thermal_limit.setDecimals(1)
        self.thermal_limit.setSuffix(" °C")
        self.thermal_limit.setValue(thermal.get("max_temp_c", 80))
        self.thermal_warmup = QDoubleSpinBox()
        self.thermal_warmup.setRange(0, 300)
        self.thermal_warmup.setDecimals(0)
        self.thermal_warmup.setSuffix(" s")
        self.thermal_warmup.setValue(thermal.get("warmup_s", 30))
        thermal_settings_row = QWidget()
        settings_layout = QHBoxLayout(thermal_settings_row)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.addWidget(QLabel("Maximum allowed"))
        settings_layout.addWidget(self.thermal_limit)
        settings_layout.addWidget(QLabel("Extra warm-up at final load"))
        settings_layout.addWidget(self.thermal_warmup)
        thermal_settings_row.setParent(self.controls)
        thermal_settings_row.hide()
        for field in (self.thermal_limit, self.thermal_warmup):
            field.setEnabled(self.thermal_enabled.isChecked())
            self.thermal_enabled.toggled.connect(field.setEnabled)
        self.profile = QLabel()
        self.profile.setWordWrap(True)
        form.addRow(self.profile)
        self.refresh_profile()
        self.start_btn = QPushButton("Start production test")
        self.start_btn.clicked.connect(self.start)
        self.label.returnPressed.connect(self.start)
        form.addRow(self.start_btn)
        layout.addWidget(self.controls)
        abort = QPushButton("Abort test")
        abort.clicked.connect(engine.abort_production)
        layout.addWidget(abort)
        self.model = MeasurementTableModel()
        table = QTableView()
        table.setModel(self.model)
        table.setMinimumHeight(135)
        layout.addWidget(table, 1)
        self.thermal_check_status = QLabel("Thermal check uses the maximum read by the operator from the camera.")
        self.thermal_check_status.setWordWrap(True)
        self.thermal_check_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.thermal_check_status)
        self.thermal_entry = QWidget()
        entry_form = QHBoxLayout(self.thermal_entry)
        entry_form.setContentsMargins(0, 0, 0, 0)
        self.thermal_measured = QLineEdit()
        self.thermal_measured.setPlaceholderText("Enter camera's current maximum in °C")
        entry_form.addWidget(QLabel("Measured maximum (°C)"))
        entry_form.addWidget(self.thermal_measured)
        self.thermal_record = QPushButton("Record temperature and finish test")
        self.thermal_record.clicked.connect(self.record_thermal)
        self.thermal_measured.returnPressed.connect(self.record_thermal)
        entry_form.addWidget(self.thermal_record)
        self.thermal_entry.setEnabled(False)
        self._thermal_run_id = None
        self._thermal_submitted = False
        layout.addWidget(self.thermal_entry)
        self.thermal_btn = QPushButton("Capture thermal image")
        self.thermal_btn.clicked.connect(self.capture_thermal)
        if not sys.platform.startswith('linux'):
            self.thermal_btn.setEnabled(False)
            self.thermal_btn.setToolTip('USB snapshots require Linux; enter the camera maximum for the thermal check.')
        layout.addWidget(self.thermal_btn)
        self.thermal_status = QLabel("THOR002 USB snapshot · visual inspection only")
        self.thermal_status.setWordWrap(True)
        if not sys.platform.startswith('linux'):
            self.thermal_status.setText('Thermal check: enter the camera maximum when prompted. USB snapshots are Linux-only.')
        self.thermal_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.thermal_status)
        self.thermal_preview = QLabel()
        self.thermal_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.thermal_preview)
        self.thermal_completed.connect(self._thermal_finished)
        self.last_label = ""
        self._awaiting_start = False

    def show_result(self, status, detail):
        colors = {
            "PASS": ("#b6f2bf", "#103b1b"),
            "FAIL": ("#bb202b", "#ffffff"),
            "TESTING": ("#254f86", "#ffffff"),
            "READY": ("#313244", "#ffffff"),
        }
        background, foreground = colors.get(status, ("#f9c74f", "#292000"))
        prefix = "SIMULATION · " if self.engine.dry_run and status != "READY" else ""
        self.result.setText(prefix + status)
        self.result.setStyleSheet(
            f"background: {background}; color: {foreground}; font-size: 48px; "
            "font-weight: bold; border-radius: 8px; padding: 12px;"
        )
        self.result_detail.setText(detail)

    def on_thermal_check(self, pending):
        if pending is None:
            self.thermal_entry.setEnabled(False)
            self._thermal_run_id = None
            return
        if self._thermal_run_id != pending["run_id"]:
            self._thermal_run_id = pending["run_id"]
            self._thermal_submitted = False
            self.thermal_measured.clear()
        waiting = pending["phase"] == "awaiting_reading"
        previously_enabled = self.thermal_entry.isEnabled()
        self.thermal_entry.setEnabled(waiting and not self._thermal_submitted)
        remaining = int(pending["remaining_s"] + 0.999)
        if waiting:
            message = (f"Read the camera maximum now for {pending['label']}. "
                       f"Limit {pending['max_temp_c']:g}°C. Outputs ON at final load "
                       f"({pending['power_w']:g} W requested); automatic shutdown in {remaining} s.")
        else:
            message = (f"Thermal warm-up: {remaining} s remaining. Outputs ON at final load "
                       f"({pending['power_w']:g} W requested). Aim the camera at the PCB.")
        self.thermal_check_status.setText(message)
        if waiting and not previously_enabled and not self._thermal_submitted:
            self.thermal_measured.setFocus()

    def record_thermal(self):
        if not self.thermal_entry.isEnabled() or self._thermal_run_id is None:
            return
        try:
            self.engine.submit_thermal_reading(self._thermal_run_id, self.thermal_measured.text())
        except (ValueError, RuntimeError) as exc:
            self.controller.error.emit(str(exc))
            return
        self._thermal_submitted = True
        self.thermal_entry.setEnabled(False)
        self.thermal_check_status.setText("Temperature submitted; waiting for shutdown verification…")

    def capture_thermal(self):
        self.thermal_btn.setEnabled(False)
        self.thermal_preview.clear()
        self.thermal_status.setText("Capturing THOR002 image…")
        directory = self.engine.store.path.parent / 'thermal'
        label, operator = self.label.text().strip(), self.operator.text().strip()

        def capture():
            try:
                path = capture_snapshot(directory, label, operator)
                self.thermal_completed.emit(path, '')
            except Exception as exc:
                self.thermal_completed.emit('', str(exc))

        Thread(target=capture, name='thermal-capture', daemon=True).start()

    def _thermal_finished(self, path, error):
        self.thermal_btn.setEnabled(True)
        if error:
            self.thermal_status.setText(error)
            return
        self.thermal_preview.setPixmap(QPixmap(path).scaled(
            320, 240, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        self.thermal_status.setText(f"Saved thermal image: {path}")

    def refresh_profile(self):
        profile = self.engine.get_production_profile() or {}
        thermal = profile.get('thermal_check', {})
        self.thermal_enabled.setChecked(thermal.get('enabled', False))
        self.thermal_limit.setValue(thermal.get('max_temp_c', 80))
        self.thermal_warmup.setValue(thermal.get('warmup_s', 30))
        lines = [profile.get("name", "No profile")]
        lines.extend(
            f"{s['name']}: {s['requested_power_w']} W, {s['wait_s']} s, {s['min_vout']}–{s['max_vout']} V, >{s['min_efficiency_percent']}%"
            for s in profile.get("steps", [])
        )
        lines.append(
            f"Thermal check: maximum {thermal.get('max_temp_c', 80):g}°C; extra warm-up {thermal.get('warmup_s', 30):g} s"
            if thermal.get('enabled', False) else "Thermal check: disabled"
        )
        lines.append("Saved recipe · Changes are made in manager mode → Test settings.")
        self.profile.setText("\n".join(lines))

    def start(self):
        if not self.controls.isEnabled() or not self.label.text().strip():
            return
        self.last_label = self.label.text().strip()
        self.refresh_profile()
        self.thermal_measured.clear()
        self._thermal_submitted = False
        self._awaiting_start = True
        self.show_result("TESTING", f"PCB: {self.last_label} — Starting test…")
        self.thermal_check_status.setText(
            "Thermal check will follow the electrical steps." if self.thermal_enabled.isChecked()
            else "Thermal check disabled for this run."
        )

        def accepted(result):
            ok, message, rid = result
            if not ok:
                self._awaiting_start = False
                self.show_result("NOT STARTED", f"PCB: {self.last_label} — {message}")
                self.controller.error.emit(message)
            elif self._awaiting_start:
                self.show_result("TESTING", f"PCB: {self.last_label} · Run #{rid} — Test in progress; wait for the final result.")

        def failed(message):
            self._awaiting_start = False
            self.show_result("NOT STARTED", f"PCB: {self.last_label} — {message}")
            self.controller.error.emit(message)

        self.controller.submit(
            self.engine.start_production,
            self.last_label,
            operator=self.operator.text(),
            success=accepted,
            failure=failed,
        )

    def on_prod_step(self, name, status, reason, point):
        if name == "Thermal check":
            self.thermal_check_status.setText(f"Thermal {status}: {reason}")
        if point:
            self.model.append_point(point)

    def on_prod_result(self, result, reason, label):
        self._awaiting_start = False
        self.thermal_entry.setEnabled(False)
        self._thermal_run_id = None
        if result != "PASS":
            self.thermal_check_status.setText(f"Test ended: {result}. {reason or ''}")
        instruction = ("Unit accepted. Scan the next PCB." if result == "PASS"
                       else "Do not accept this unit." if result == "FAIL"
                       else "No PASS recorded. Check the unit before retesting.")
        if self.engine.dry_run:
            instruction = "Simulation result only. " + instruction.replace("Unit accepted. ", "")
        self.show_result(result, f"PCB: {label} — {instruction}\n{reason or ''}".strip())
        self.label.selectAll()
        self.label.setFocus()
