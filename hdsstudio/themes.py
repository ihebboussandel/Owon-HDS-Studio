THEMES = {
    "Midnight": dict(bg="#0b1019", panel="#121b29", field="#192539", border="#28384e", text="#e6edf7", muted="#8fabc5", accent="#68dcbd", plot="#0b1320"),
    "Daylight": dict(bg="#eef2f7", panel="#ffffff", field="#f4f7fb", border="#d5dfe9", text="#15273c", muted="#5e7289", accent="#007e70", plot="#ffffff"),
    "Phosphor": dict(bg="#0b1411", panel="#101f19", field="#192e24", border="#2a4938", text="#d6f7df", muted="#89b69a", accent="#81ee96", plot="#08130d"),
}
CHANNEL_COLORS = ("#ffcc66", "#62cbff", "#cf9fff", "#67e6bf")


def stylesheet(name):
    c = THEMES[name]
    return """
    QWidget { color: %(text)s; font-family: 'Inter', 'Segoe UI', 'DejaVu Sans'; font-size: 12px; }
    QMainWindow, #root, QTabWidget::pane { background: %(bg)s; }
    QTabWidget::pane { border: 0; }
    QFrame#card, QGroupBox { background: %(panel)s; border: 1px solid %(border)s; border-radius: 10px; }
    QFrame#modeToast { background: %(panel)s; border: 2px solid %(accent)s; border-radius: 9px; }
    QGroupBox { margin-top: 18px; padding: 18px 10px 10px; font-weight: 600; }
    QGroupBox::title { subcontrol-origin: margin; left: 15px; padding: 0 6px; color: %(accent)s; }
    QLabel { background: transparent; }
    QLabel#title { font-size: 29px; font-weight: 700; letter-spacing: -1px; }
    QLabel#subtitle, QLabel#muted { color: %(muted)s; }
    QLabel#badge { color: %(accent)s; background: %(field)s; border-radius: 6px; padding: 7px 10px; font-weight: 700; }
    QLabel#metric { font-family: 'DejaVu Sans Mono'; font-size: 23px; font-weight: 600; }
    QLabel#meterValue { font-family: 'DejaVu Sans Mono'; font-size: 66px; font-weight: 600; color: %(accent)s; }
    QLabel#meterStat { font-family: 'DejaVu Sans Mono'; font-size: 18px; font-weight: 600; }
    QLabel#notice { color: %(muted)s; padding: 8px; background: %(panel)s; border-radius: 6px; }
    QLabel#triggerReadout { color: %(accent)s; background: %(panel)s; border: 1px solid %(border)s; border-radius: 6px; padding: 7px 10px; font-family: 'DejaVu Sans Mono'; font-weight: 600; }
    QPushButton { background: %(field)s; border: 1px solid %(border)s; border-radius: 6px; padding: 8px 12px; font-weight: 600; }
    QPushButton:hover { border-color: %(accent)s; }
    QPushButton:pressed, QPushButton:checked { color: %(bg)s; background: %(accent)s; }
    QPushButton#primary { background: %(accent)s; color: %(bg)s; border: 0; }
    QPushButton#danger { color: #f78692; }
    QPushButton:disabled { color: %(muted)s; }
    QPushButton#primary:disabled { background: %(field)s; color: %(muted)s; border: 1px solid %(border)s; }
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit { background: %(field)s; border: 1px solid %(border)s; border-radius: 5px; padding: 7px; selection-background-color: %(accent)s; }
    QComboBox QAbstractItemView { background: %(panel)s; color: %(text)s; selection-background-color: %(border)s; }
    QTabBar::tab { background: %(bg)s; color: %(muted)s; padding: 13px 22px; border-bottom: 2px solid transparent; font-weight: 600; }
    QTabBar::tab:selected { color: %(accent)s; border-bottom-color: %(accent)s; }
    QTabBar::tab:hover { color: %(text)s; }
    QScrollArea, QScrollArea > QWidget > QWidget { background: %(bg)s; border: 0; }
    QScrollBar:vertical { background: %(bg)s; width: 8px; }
    QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px; min-height: 24px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QTableWidget { background: %(panel)s; alternate-background-color: %(field)s; border: 1px solid %(border)s; gridline-color: %(border)s; }
    QHeaderView::section { background: %(field)s; color: %(muted)s; border: 0; padding: 8px; }
    QCheckBox { spacing: 6px; }
    QSplitter::handle { background: %(bg)s; width: 7px; }
    QStatusBar { color: %(muted)s; background: %(panel)s; }
    QToolTip { background: %(panel)s; color: %(text)s; border: 1px solid %(accent)s; padding: 7px; }
    """ % c
