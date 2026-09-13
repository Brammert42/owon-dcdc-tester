"""Explicitly saved production recipe editor for manager mode."""

import copy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QDoubleSpinBox, QCheckBox, QTableWidget, QPushButton, QHeaderView,
)


def spin(value, low, high, decimals=1):
    field = QDoubleSpinBox()
    field.setRange(low, high)
    field.setDecimals(decimals)
    field.setValue(value)
    return field


class ManagerTab(QWidget):
    def __init__(self, engine, controller, saved, sounds, parent=None):
        super().__init__(parent)
        self.engine, self.controller = engine, controller
        self.saved_callback = saved
        layout = QVBoxLayout(self)
        notice = QLabel("Edit the production recipe here. Disconnect instruments before saving. Operators use only the saved recipe.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.editor = QWidget()
        form = QFormLayout(self.editor)
        self.name = QLineEdit()
        self.voltage = spin(84, 0.1, 90)
        self.current = spin(5, 0.1, 6, 2)
        self.nominal = spin(12, 0.1, 100, 2)
        self.continue_fail = QCheckBox("Continue electrical steps after a failed step")
        self.thermal_enabled = QCheckBox("Require thermal check")
        self.thermal_limit = spin(80, -20, 550)
        self.warmup = spin(30, 0, 300, 0)
        self.sound_enabled = QCheckBox("Play pass/fail sounds")
        for label, field in [("Profile name", self.name), ("Input voltage (V)", self.voltage),
                             ("Input current limit (A)", self.current), ("Nominal output voltage (V)", self.nominal),
                             ("", self.continue_fail), ("", self.thermal_enabled),
                             ("Maximum temperature (°C)", self.thermal_limit),
                             ("Thermal warm-up (s)", self.warmup), ("", self.sound_enabled)]:
            form.addRow(label, field)
        layout.addWidget(self.editor)
        self.steps = QTableWidget(0, 6)
        self.steps.setHorizontalHeaderLabels(["Step", "Power (W)", "Wait (s)", "Min Vout (V)", "Max Vout (V)", "Efficiency above (%)"])
        self.steps.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.steps, 1)
        buttons = QHBoxLayout()
        for title, fn in [("Add step", self.add_step), ("Remove selected step", self.remove_step),
                          ("Reload saved settings", self.reload), ("Test pass sound", lambda: sounds.play('PASS', force=True)),
                          ("Test fail sound", lambda: sounds.play('FAIL', force=True))]:
            button = QPushButton(title)
            button.clicked.connect(fn)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.save_button = QPushButton("Save production settings")
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        self.reload()

    def reload(self):
        p = self.engine.get_production_profile()
        self._profile = copy.deepcopy(p)
        self.name.setText(p.get('name', 'Production'))
        self.voltage.setValue(p['input_voltage'])
        self.current.setValue(p['input_current_limit'])
        self.nominal.setValue(p.get('nominal_output_voltage', 12))
        self.continue_fail.setChecked(p.get('continue_on_fail', False))
        t = p.get('thermal_check', {})
        self.thermal_enabled.setChecked(t.get('enabled', False))
        self.thermal_limit.setValue(t.get('max_temp_c', 80))
        self.warmup.setValue(t.get('warmup_s', 30))
        self.sound_enabled.setChecked(self.engine._config.get('result_sounds_enabled', True))
        self.steps.setRowCount(0)
        for step in p['steps']:
            self.add_step(step)
        self.message.setText("Showing saved settings. Edits take effect only after Save.")

    def add_step(self, step=None):
        if not isinstance(step, dict):
            step = dict(name='New step', requested_power_w=10, wait_s=2, min_vout=11.5,
                        max_vout=12.7, min_efficiency_percent=90)
        row = self.steps.rowCount()
        self.steps.insertRow(row)
        fields = [QLineEdit(step['name']), spin(step['requested_power_w'], 0, 180),
                  spin(step['wait_s'], 0, 60), spin(step['min_vout'], 0, 100, 2),
                  spin(step['max_vout'], 0, 100, 2), spin(step['min_efficiency_percent'], 90, 105, 2)]
        for col, field in enumerate(fields):
            self.steps.setCellWidget(row, col, field)

    def remove_step(self):
        if self.steps.rowCount() > 1 and self.steps.currentRow() >= 0:
            self.steps.removeRow(self.steps.currentRow())

    def profile(self):
        p = copy.deepcopy(self._profile)
        p.update(name=self.name.text().strip(), input_voltage=self.voltage.value(),
                 input_current_limit=self.current.value(), nominal_output_voltage=self.nominal.value(),
                 continue_on_fail=self.continue_fail.isChecked(), thermal_check={
                     'enabled': self.thermal_enabled.isChecked(), 'max_temp_c': self.thermal_limit.value(),
                     'warmup_s': self.warmup.value()}, steps=[])
        for row in range(self.steps.rowCount()):
            fields = [self.steps.cellWidget(row, col) for col in range(6)]
            p['steps'].append(dict(zip(
                ['name', 'requested_power_w', 'wait_s', 'min_vout', 'max_vout', 'min_efficiency_percent'],
                [fields[0].text().strip()] + [field.value() for field in fields[1:]])))
        return p

    def save(self):
        if not self.save_button.isEnabled():
            return
        def saved(_):
            self.reload()
            self.message.setText("Saved. Operators and future app launches will use these settings.")
            self.saved_callback()
        self.controller.submit(self.engine.save_production_settings, self.profile(), self.sound_enabled.isChecked(),
                               success=saved, failure=self.message.setText)
