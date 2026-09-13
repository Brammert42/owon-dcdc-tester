"""Qt data models for tables."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class MeasurementTableModel(QAbstractTableModel):
    """Generic table model backed by a list of measurement dicts."""

    COLUMNS = [
        ("Step", "step_name"),
        ("Vin (V)", "vin"),
        ("Vout (V)", "vout"),
        ("Iin (A)", "iin"),
        ("Iout (A)", "iout"),
        ("Pin (W)", "pin"),
        ("Pout (W)", "pout"),
        ("η (%)", "efficiency_percent"),
        ("Result", "pass_fail_status"),
        ("Reason", "notes"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: list[dict] = []

    def set_points(self, points: list[dict]) -> None:
        self.beginResetModel()
        self._points = list(points)
        self.endResetModel()

    def append_point(self, point: dict) -> None:
        self.beginInsertRows(QModelIndex(), len(self._points), len(self._points))
        self._points.append(point)
        self.endInsertRows()

    def clear(self) -> None:
        self.set_points([])

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._points) if not parent.isValid() else 0

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS) if not parent.isValid() else 0

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLUMNS[section][0]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._points):
            return None
        point = self._points[index.row()]
        key = self.COLUMNS[index.column()][1]
        val = point.get(key, "")
        if role in (Qt.DisplayRole, Qt.EditRole):
            if isinstance(val, float):
                return f"{val:.3f}"
            return str(val)
        return None


class SweepStepTableModel(QAbstractTableModel):
    """Table model for sweep step definitions."""

    COLUMNS = [
        ("Step", "name"),
        ("Power (W)", "requested_power_w"),
        ("Load (A)", "load_current_a"),
        ("Notes", "notes"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps: list[dict] = []

    def set_steps(self, steps: list[dict]) -> None:
        self.beginResetModel()
        self._steps = list(steps)
        self.endResetModel()

    def clear(self) -> None:
        self.set_steps([])

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._steps) if not parent.isValid() else 0

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS) if not parent.isValid() else 0

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLUMNS[section][0]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._steps):
            return None
        step = self._steps[index.row()]
        key = self.COLUMNS[index.column()][1]
        val = step.get(key, "")
        if role in (Qt.DisplayRole, Qt.EditRole):
            if isinstance(val, float):
                return f"{val:.3f}"
            return str(val)
        return None
