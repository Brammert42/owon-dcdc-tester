"""Manual setup/capture. All hardware work is submitted to the controller."""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QDoubleSpinBox,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QLabel,
    QTableView,
)
from desktop.models import MeasurementTableModel


def field(form, label, value, maximum, minimum=0):
    widget = QDoubleSpinBox()
    widget.setDecimals(4)
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    form.addRow(label, widget)
    return widget


class ManualTab(QWidget):
    def __init__(self, engine, controller, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.controller = controller
        layout = QVBoxLayout(self)
        self.controls = QWidget()
        form = QFormLayout(self.controls)
        limits = engine.manager.safety_limits
        defaults = engine._config.get("psu", {})
        self.voltage = field(
            form,
            "PSU voltage (V)",
            defaults.get("input_voltage", 48),
            limits["max_input_voltage"],
            0.001,
        )
        self.current = field(
            form,
            "PSU limit (A)",
            defaults.get("input_current_limit", 5),
            limits["max_input_current"],
            0.001,
        )
        self.load = field(form, "Load current (A)", 0, limits["max_load_current"])
        self.name = QLineEdit("Manual point")
        form.addRow("Point name", self.name)
        for label, fn in [
            (
                "Apply PSU (outputs off)",
                lambda: self.controller.submit(
                    engine.set_psu, self.voltage.value(), self.current.value()
                ),
            ),
            ("PSU ON", lambda: self.controller.submit(engine.psu_on)),
            (
                "Apply load (input off)",
                lambda: self.controller.submit(engine.set_load, self.load.value()),
            ),
            ("Load ON", lambda: self.controller.submit(engine.load_on)),
            ("Apply settings and record", self.record),
            (
                "Finish run / outputs OFF",
                lambda: self.controller.submit(engine.manual_start_new_run),
            ),
        ]:
            button = QPushButton(label)
            button.clicked.connect(fn)
            form.addRow(button)
        layout.addWidget(self.controls)
        layout.addWidget(
            QLabel(
                "Record applies these settings and energizes the DUT. Finish run turns both outputs off."
            )
        )
        off = QHBoxLayout()
        for label, method in [
            ("PSU OFF", engine.psu_off),
            ("Load OFF", engine.load_off),
        ]:
            button = QPushButton(label)
            button.clicked.connect(
                lambda checked=False, f=method: self.controller.submit(f)
            )
            off.addWidget(button)
        layout.addLayout(off)
        self.model = MeasurementTableModel()
        table = QTableView()
        table.setModel(self.model)
        layout.addWidget(table)

    def record(self):
        self.controller.submit(
            self.engine.manual_record,
            psu_voltage=self.voltage.value(),
            psu_current_limit=self.current.value(),
            load_current=self.load.value(),
            step_name=self.name.text(),
        )

    def on_measurement(self, tab, point):
        if tab == "manual":
            self.model.append_point(point)
