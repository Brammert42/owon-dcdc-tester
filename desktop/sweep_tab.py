"""Generate fresh validated sweep settings at Start; no stale step snapshots."""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QSpinBox,
    QLineEdit,
    QPushButton,
    QLabel,
    QTableView,
)
from desktop.manual_tab import field
from desktop.models import MeasurementTableModel
from test_engine.test_profiles import generate_sweep_steps


class SweepTab(QWidget):
    def __init__(self, engine, controller, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.controller = controller
        layout = QVBoxLayout(self)
        self.controls = QWidget()
        form = QFormLayout(self.controls)
        limits = engine.manager.safety_limits
        psu = engine._config.get("psu", {})
        self.voltage = field(
            form,
            "Input voltage (V)",
            psu.get("input_voltage", 48),
            limits["max_input_voltage"],
            0.001,
        )
        self.current = field(
            form,
            "Input current limit (A)",
            psu.get("input_current_limit", 5),
            limits["max_input_current"],
            0.001,
        )
        self.start_power = field(
            form, "Start power (W)", 10, limits["max_output_power"]
        )
        self.end_power = field(form, "End power (W)", 120, limits["max_output_power"])
        self.nominal = field(
            form,
            "Nominal output voltage (V)",
            limits["nominal_output_voltage"],
            limits["max_output_voltage_allowed"],
            0.001,
        )
        self.count = QSpinBox()
        self.count.setRange(1, 1000)
        self.count.setValue(10)
        form.addRow("Steps", self.count)
        self.delay = field(
            form, "Settle time (s)", engine._config["settling_time_s"], 60
        )
        self.name = QLineEdit()
        form.addRow("Run name", self.name)
        start = QPushButton("Start sweep")
        start.clicked.connect(self.start)
        form.addRow(start)
        layout.addWidget(self.controls)
        stop = QPushButton("Stop sweep")
        stop.clicked.connect(engine.stop_sweep)
        layout.addWidget(stop)
        self.status = QLabel("Ready")
        layout.addWidget(self.status)
        self.model = MeasurementTableModel()
        table = QTableView()
        table.setModel(self.model)
        layout.addWidget(table)

    def start(self):
        params = dict(
            input_voltage=self.voltage.value(),
            input_current_limit=self.current.value(),
            start_power=self.start_power.value(),
            end_power=self.end_power.value(),
            num_steps=self.count.value(),
            nominal_output_voltage=self.nominal.value(),
        )
        try:
            steps = generate_sweep_steps(**params, config=self.engine._config)
        except Exception as exc:
            self.controller.error.emit(str(exc))
            return
        self.controller.submit(
            self.engine.start_sweep,
            dict(
                **params,
                steps=steps,
                wait_time=self.delay.value(),
                run_name=self.name.text(),
            ),
        )

    def on_measurement(self, tab, point):
        if tab == "sweep":
            self.model.append_point(point)
