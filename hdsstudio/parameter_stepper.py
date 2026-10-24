"""Independent draft-only increments for the generator's existing SI editors."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget
from .commands import BY_KEY, engineering, quantity
from .generator_profiles import steps_for


class ParameterStepper(QWidget):
    error = Signal(str)

    def __init__(self, key, row, getter, limits):
        super().__init__()
        self.key, self.row, self.getter, self.limits = key, row, getter, limits
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(QLabel("Step"))
        self.step = QComboBox()
        choices, default = steps_for(key)
        self.step.addItems(choices)
        self.step.setCurrentText(default)
        self.step.setMinimumContentsLength(7)
        self.step.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.step, 1)
        self.minus, self.plus = QPushButton("−"), QPushButton("+")
        for button, direction in ((self.minus, -1), (self.plus, 1)):
            button.setFixedSize(27, 25)
            button.setStyleSheet("padding:0;")
            button.clicked.connect(lambda checked=False, d=direction: self.adjust(d))
            layout.addWidget(button)
        self.setToolTip("Edits the local draft only. Up/Down: selected step; Shift: ×10; Ctrl: ÷10. Apply sends settings. UI steps are not a guarantee of device resolution.")
        row.edit.installEventFilter(self)
        row.layout().addWidget(self)

    def adjust(self, direction, multiplier=1.):
        try:
            current = self.getter(self.key)
            text = self.step.currentText()
            if text.endswith(" relative"):
                increment = abs(current)*float(text.split("%")[0])/100
            elif self.key in ("gen_duty", "gen_sym"):
                increment = float(text.rstrip("%"))
            else:
                increment = quantity(text.removesuffix("pp"), BY_KEY[self.key].unit)
            if self.key == "gen_sym":
                increment = max(1., round(increment*multiplier))
            else:
                increment *= multiplier
            candidate = float(f"{current+direction*increment:.12g}")
            low, high = self.limits(self.key)
            if not low-1e-15 <= candidate <= high+1e-15:
                raise ValueError(f"{BY_KEY[self.key].label}: step would exceed {low:.6g}…{high:.6g} {BY_KEY[self.key].unit}")
            suffix = "%" if self.key in ("gen_duty", "gen_sym") else BY_KEY[self.key].unit
            formatted = f"{candidate:.12g}%" if suffix == "%" else engineering(candidate, suffix, 12)+("pp" if self.key == "gen_amp" else "")
            self.row.edit.setText(formatted)
            self.row.mark_dirty()
        except (ValueError, ZeroDivisionError) as exc:
            self.error.emit(str(exc))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Up, Qt.Key_Down) and self.row.edit.isEnabled() and not self.row.edit.isReadOnly():
            modifiers = event.modifiers()
            multiplier = 10. if modifiers & Qt.ShiftModifier else .1 if modifiers & Qt.ControlModifier else 1.
            self.adjust(1 if event.key() == Qt.Key_Up else -1, multiplier)
            return True
        return super().eventFilter(watched, event)
