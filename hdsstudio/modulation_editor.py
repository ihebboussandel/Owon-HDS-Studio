"""Explicitly local modulation designer, fed from the generator's draft controls."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout,
                              QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)
import pyqtgraph as pg
from .modulation import design_modulation, export_modulation


class ModulationEditor(QWidget):
    def __init__(self, carrier_values):
        super().__init__()
        self.carrier_values = carrier_values
        self.result = None
        box = QVBoxLayout(self)
        info = QLabel("LOCAL AM / FM DESIGN · preview and CSV export. No native AM/FM commands or custom-waveform upload are verified for the HDS2202S. This tab never modulates hardware output.")
        info.setWordWrap(True)
        info.setObjectName("notice")
        box.addWidget(info)
        controls = QHBoxLayout()
        form = QGridLayout()
        self.mode = QComboBox()
        self.mode.addItems(("AM", "FM"))
        self.mod_shape = QComboBox()
        self.mod_shape.addItems(("Sine", "Triangle", "Square"))
        self.frequency = QDoubleSpinBox()
        self.frequency.setRange(.001, 1e6)
        self.frequency.setDecimals(3)
        self.frequency.setValue(100)
        self.frequency.setSuffix(" Hz")
        self.depth = QDoubleSpinBox()
        self.depth.setRange(0, 100)
        self.depth.setValue(50)
        self.depth.setSuffix(" %")
        self.deviation = QDoubleSpinBox()
        self.deviation.setRange(0, 25e6)
        self.deviation.setDecimals(3)
        self.deviation.setValue(100)
        self.deviation.setSuffix(" Hz")
        for i, (text, widget) in enumerate((("Modulation", self.mode), ("Modulating shape", self.mod_shape),
                                           ("Modulation frequency", self.frequency), ("AM depth", self.depth), ("FM deviation", self.deviation))):
            row, column = divmod(i, 2)
            form.addWidget(QLabel(text), row, 2*column)
            form.addWidget(widget, row, 2*column+1)
        controls.addLayout(form, 1)
        box.addLayout(controls)
        self.carrier_info = QLabel()
        self.carrier_info.setWordWrap(True)
        self.carrier_info.setAlignment(Qt.AlignTop)
        box.addWidget(self.carrier_info)
        self.plot = pg.PlotWidget(title="LOCAL MODULATION · NOT DEVICE OUTPUT")
        self.plot.setFixedHeight(230)
        self.plot.setLabel("bottom", "Design time", units="s")
        self.plot.setLabel("left", "Design voltage", units="V")
        self.plot.showGrid(x=True, y=True, alpha=.16)
        self.plot.addLegend()
        self.curve = self.plot.plot(pen=pg.mkPen("#67e6bf", width=1.4), name="Modulated design")
        self.upper = self.plot.plot(pen=pg.mkPen("#cf9fff", style=Qt.DashLine), name="Envelope")
        self.lower = self.plot.plot(pen=pg.mkPen("#cf9fff", style=Qt.DashLine))
        box.addWidget(self.plot)
        self.export = QPushButton("Export modulation CSV")
        self.export.clicked.connect(self.export_csv)
        form.addWidget(self.export, 2, 2, 1, 2)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setObjectName("notice")
        box.addWidget(self.status)
        for widget in (self.mode, self.mod_shape):
            widget.currentTextChanged.connect(self.redraw)
        for widget in (self.frequency, self.depth, self.deviation):
            widget.valueChanged.connect(self.redraw)
        self.redraw()

    def redraw(self, *_):
        self.depth.setEnabled(self.mode.currentText() == "AM")
        self.deviation.setEnabled(self.mode.currentText() == "FM")
        try:
            values = self.carrier_values()
            self.carrier_info.setText(f"Draft carrier: {values['shape']} · {values['carrier_hz']:g} Hz · {values['amplitude']:g} Vpp · offset {values['offset']:g} V\n8192 samples over 3 modulation periods · adjust the carrier in Signal")
            self.result = design_modulation(**values, mode=self.mode.currentText(), mod_hz=self.frequency.value(),
                                            depth=self.depth.value()/100, deviation_hz=self.deviation.value(), mod_shape=self.mod_shape.currentText())
            t, y, upper, lower = self.result
            self.curve.setData(t, y)
            self.upper.setData(t, upper)
            self.lower.setData(t, lower)
            self.upper.setVisible(self.mode.currentText() == "AM")
            self.lower.setVisible(self.mode.currentText() == "AM")
            self.status.setText(f"Local {self.mode.currentText()} design · {y.min():.5g} to {y.max():.5g} V · export is a finite record, not a guaranteed seamless cycle. Square/triangle edges are sampled approximations.")
            self.export.setEnabled(True)
            self.plot.autoRange()
        except (ValueError, ZeroDivisionError) as exc:
            self.result = None
            for curve in (self.curve, self.upper, self.lower):
                curve.clear()
            self.status.setText(str(exc))
            self.export.setEnabled(False)

    def export_csv(self):
        if self.result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export local modulation design", "modulation.csv", "CSV (*.csv)")
        if path:
            try:
                export_modulation(path, self.result[0], self.result[1])
                self.status.setText("Exported local modulation CSV · no hardware command or upload was sent")
            except (OSError, ValueError) as exc:
                self.status.setText("Cannot export: " + str(exc))
