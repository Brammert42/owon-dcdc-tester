"""Completed-run graph, explicit selection, watts on X, maximum ten traces."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QCheckBox,
    QLabel,
    QScrollArea,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT


class GraphWidget(QWidget):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        layout = QVBoxLayout(self)
        self.figure = Figure(tight_layout=True)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(NavigationToolbar2QT(self.canvas, self))
        buttons = QHBoxLayout()
        for label, fn in [
            ("Select newest ten", engine.select_all_runs),
            ("Hide all", engine.deselect_all_runs),
        ]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        layout.addLayout(buttons)
        self.message = QLabel("Only completed runs are selectable.")
        layout.addWidget(self.message)
        self.container = QWidget()
        self.checks = QVBoxLayout(self.container)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.container)
        scroll.setMaximumHeight(160)
        layout.addWidget(scroll)
        layout.addWidget(self.canvas)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def schedule(self):
        self.timer.start()

    def refresh(self):
        while self.checks.count():
            item = self.checks.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for run in self.engine.run_data.completed_runs:
            cb = QCheckBox(f"#{run.run_id}: {run.label} ({len(run.points)} points)")
            cb.setChecked(self.engine.run_data.is_selected(run.run_id))
            cb.toggled.connect(lambda checked, rid=run.run_id: self.toggle(rid))
            self.checks.addWidget(cb)
        self.ax.clear()
        self.ax.set_xlabel("Output power (W)")
        self.ax.set_ylabel("Efficiency (%)")
        self.ax.grid(alpha=0.2)
        for run in self.engine.run_data.selected_runs:
            points = sorted(run.points, key=lambda p: p["pout"])
            self.ax.plot(
                [p["pout"] for p in points],
                [p["efficiency_percent"] for p in points],
                marker="o",
                label=f"#{run.run_id} {run.label}",
            )
        if self.engine.run_data.selected_ids:
            self.ax.legend()
        self.canvas.draw_idle()

    def toggle(self, rid):
        try:
            self.engine.toggle_run_selection(rid)
            self.message.setText("Only completed runs are selectable.")
        except ValueError as exc:
            self.message.setText(str(exc))
        self.schedule()
