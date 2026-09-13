"""Dark QSS theme matching the web UI aesthetic."""

DARK_QSS = """
/* ── Global ──────────────────────────────────────────── */
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Segoe UI", "SF Pro", sans-serif;
    font-size: 13px;
}

QMainWindow {
    background-color: #1e1e2e;
}

/* ── Tabs ──────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #313244;
    background: #181825;
    border-radius: 6px;
}

QTabBar::tab {
    background: #313244;
    color: #a6adc8;
    border: 1px solid #45475a;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 18px;
    margin-right: 2px;
}

QTabBar::tab:selected {
    background: #1e1e2e;
    color: #cdd6f4;
    border-bottom: 2px solid #89b4fa;
}

QTabBar::tab:hover:!selected {
    background: #45475a;
    color: #cdd6f4;
}

/* ── Buttons ────────────────────────────────────────── */
QPushButton {
    background: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px 16px;
    min-height: 24px;
}

QPushButton:hover {
    background: #45475a;
    border-color: #585b70;
}

QPushButton:pressed {
    background: #585b70;
}

QPushButton:disabled {
    background: #181825;
    color: #585b70;
    border-color: #313244;
}

QPushButton[danger="true"] {
    background: #f38ba8;
    color: #1e1e2e;
    border-color: #eba0ac;
    font-weight: bold;
}

QPushButton[danger="true"]:hover {
    background: #eba0ac;
}

QPushButton[success="true"] {
    background: #a6e3a1;
    color: #1e1e2e;
    border-color: #a6e3a1;
    font-weight: bold;
}

QPushButton[success="true"]:hover {
    background: #94e2d5;
}

/* ── Inputs ─────────────────────────────────────────── */
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
    background: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 22px;
}

QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #89b4fa;
}

QComboBox::drop-down {
    border: none;
    padding-right: 8px;
}

QComboBox QAbstractItemView {
    background: #313244;
    color: #cdd6f4;
    selection-background-color: #45475a;
    border: 1px solid #585b70;
}

/* ── Labels ─────────────────────────────────────────── */
QLabel {
    color: #a6adc8;
}

QLabel[heading="true"] {
    font-size: 16px;
    font-weight: bold;
    color: #cdd6f4;
}

QLabel[value="true"] {
    font-size: 14px;
    font-weight: bold;
    color: #89b4fa;
}

QLabel[danger="true"] {
    color: #f38ba8;
    font-weight: bold;
}

QLabel[success="true"] {
    color: #a6e3a1;
    font-weight: bold;
}

/* ── Tables ─────────────────────────────────────────── */
QTableView {
    background: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 4px;
    gridline-color: #313244;
    selection-background-color: #45475a;
    selection-color: #cdd6f4;
    font-size: 12px;
}

QHeaderView::section {
    background: #313244;
    color: #a6adc8;
    border: 1px solid #45475a;
    padding: 4px 8px;
    font-weight: bold;
}

QHeaderView::section:hover {
    background: #45475a;
}

/* ── Group boxes ─────────────────────────────────────── */
QGroupBox {
    background: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    margin-top: 12px;
    padding: 16px 12px 12px 12px;
    font-weight: bold;
    color: #a6adc8;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    background: #313244;
    border-radius: 4px;
    color: #89b4fa;
}

/* ── Scrollbars ─────────────────────────────────────── */
QScrollBar:vertical {
    background: #181825;
    width: 10px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background: #45475a;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: #585b70;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

/* ── Splitter ─────────────────────────────────────────── */
QSplitter::handle {
    background: #313244;
    width: 2px;
}

/* ── Tooltips ─────────────────────────────────────────── */
QToolTip {
    background: #313244;
    color: #cdd6f4;
    border: 1px solid #585b70;
    border-radius: 4px;
    padding: 4px 8px;
}
"""
