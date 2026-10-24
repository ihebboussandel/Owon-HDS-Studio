"""Offline CSV/drawing editor. No hardware write or upload path is exposed."""
from pathlib import Path
from copy import deepcopy
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                              QGridLayout, QGroupBox, QInputDialog, QLabel, QPushButton,
                              QSpinBox, QVBoxLayout, QWidget)
from .waveform import (TEMPLATES, WaveformLibrary, template_samples, read_waveform_csv,
                       save_waveform_csv, voltage_values)
from .waveform_shapes import SCHEMAS, canonical_template, template_parameters, validate_recipe


class DrawingPlot(pg.PlotWidget):
    stroke_started = Signal()
    point_drawn = Signal(float, float)

    def __init__(self):
        super().__init__(title="CUSTOM WAVEFORM · NORMALIZED SAMPLES · OFFLINE")
        self.drawing = False
        self.dragging = False

    def point(self, event):
        point = self.getViewBox().mapSceneToView(self.mapToScene(event.position().toPoint()))
        self.point_drawn.emit(float(point.x()), float(point.y()))

    def mousePressEvent(self, event):
        if self.drawing and event.button() == Qt.LeftButton:
            self.dragging = True
            self.stroke_started.emit()
            self.point(event)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drawing and self.dragging:
            self.point(event)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragging:
            self.point(event)
            self.dragging = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class WaveformEditor(QWidget):
    library_changed = Signal()
    layout_changed = Signal()

    def __init__(self, library_path=None):
        super().__init__()
        self.library = WaveformLibrary(library_path)
        self.samples = template_samples("Sine")
        self.recipe = dict(template="Sine", parameters=template_parameters("Sine"))
        self.parameter_cache = {}
        self.parameter_widgets = {}
        self.parameter_error = None
        self.undo = []
        self.last_index = None
        box = QVBoxLayout(self)
        info = QLabel("CSV and drawing workspace · local preview/export only. The HDS200 USB protocol has no verified custom-waveform upload command.")
        info.setObjectName("notice")
        info.setWordWrap(True)
        box.addWidget(info)
        templates = QHBoxLayout()
        self.templates = QComboBox()
        self.templates.addItems(TEMPLATES)
        templates.addWidget(QLabel("Design template"))
        templates.addWidget(self.templates, 1)
        self.generate_button = QPushButton("Generate shape")
        self.generate_button.clicked.connect(lambda: self.load_template(self.templates.currentText()))
        templates.addWidget(self.generate_button)
        self.keep_button = QPushButton("Keep in library")
        self.keep_button.clicked.connect(self.keep_cycle)
        templates.addWidget(self.keep_button)
        box.addLayout(templates)
        self.parameters_group = QGroupBox("Shape parameters · local design")
        self.parameters_grid = QGridLayout(self.parameters_group)
        box.addWidget(self.parameters_group)
        self.source_note = QLabel()
        self.source_note.setWordWrap(True)
        self.source_note.setObjectName("muted")
        box.addWidget(self.source_note)
        tools = QHBoxLayout()
        self.count = QComboBox()
        self.count.addItems(["256", "512", "1024", "2048", "4096", "8192"])
        self.count.setCurrentText("1024")
        self.units = QComboBox()
        self.units.addItems(["Normalized CSV", "Volts CSV"])
        self.draw = QCheckBox("Draw with mouse")
        tools.addWidget(self.draw)
        tools.addWidget(QLabel("Samples"))
        tools.addWidget(self.count)
        tools.addWidget(self.units)
        tools.addStretch()
        box.addLayout(tools)
        filetools = QHBoxLayout()
        for text, callback in (("Import CSV", self.import_csv), ("Export CSV", self.export_csv),
                               ("Undo", self.undo_stroke), ("Clear", self.clear), ("Sine", self.sine)):
            btn = QPushButton(text)
            btn.clicked.connect(callback)
            filetools.addWidget(btn)
            if text == "Export CSV":
                self.export_button = btn
        filetools.addStretch()
        box.addLayout(filetools)
        self.plot = DrawingPlot()
        self.plot.setFixedHeight(230)
        self.plot.setLabel("bottom", "Position within record", units="")
        self.plot.setLabel("left", "Normalized amplitude", units="")
        self.plot.showGrid(x=True, y=True, alpha=.16)
        self.plot.addLegend()
        self.plot.setXRange(0, 1, padding=.01)
        self.plot.setYRange(-1.1, 1.1, padding=0)
        self.curve = self.plot.plot(pen=pg.mkPen("#cf9fff", width=2), name="Custom cycle")
        box.addWidget(self.plot, 1)
        voltagebar = QHBoxLayout()
        self.amplitude = QDoubleSpinBox()
        self.amplitude.setRange(.02, 5)
        self.amplitude.setDecimals(3)
        self.amplitude.setSingleStep(.01)
        self.amplitude.setValue(1)
        self.amplitude.setSuffix(" Vpp")
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-2.5, 2.5)
        self.offset.setDecimals(3)
        self.offset.setSingleStep(.01)
        self.offset.setSuffix(" V")
        voltagebar.addWidget(QLabel("Local export amplitude"))
        voltagebar.addWidget(self.amplitude)
        voltagebar.addWidget(QLabel("Offset"))
        voltagebar.addWidget(self.offset)
        voltagebar.addStretch()
        box.addLayout(voltagebar)
        actions = QHBoxLayout()
        self.use_device = QPushButton("Copy device amplitude / offset")
        actions.addWidget(self.use_device)
        self.transfer = QPushButton("USB upload unavailable")
        self.transfer.setEnabled(False)
        self.transfer.setToolTip("No supported sample-upload command is documented for the HDS2202S. Export CSV for use with compatible equipment.")
        actions.addWidget(self.transfer)
        actions.addStretch()
        box.addLayout(actions)
        self.status = QLabel()
        self.status.setObjectName("notice")
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.count.currentTextChanged.connect(self.resample)
        self.draw.toggled.connect(self.set_drawing)
        self.plot.stroke_started.connect(self.start_stroke)
        self.plot.point_drawn.connect(self.draw_point)
        self.amplitude.valueChanged.connect(self.redraw)
        self.offset.valueChanged.connect(self.redraw)
        self.templates.currentTextChanged.connect(self.select_template)
        self.build_parameters("Sine", self.recipe["parameters"])
        self.redraw()
        if self.library.warning:
            self.status.setText(self.library.warning)

    def remember(self):
        self.undo.append((self.samples.copy(), deepcopy(self.recipe)))
        self.undo = self.undo[-20:]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.layout_changed.emit()

    def build_parameters(self, name, values):
        self.controls_template = name
        while self.parameters_grid.count():
            item = self.parameters_grid.takeAt(0)
            item.widget().deleteLater()
        self.parameter_widgets = {}
        for index, spec in enumerate(SCHEMAS[name]):
            if spec.choices:
                edit = QComboBox()
                edit.addItems(spec.choices)
                edit.setCurrentText(values[spec.key])
                edit.currentTextChanged.connect(self.parameters_changed)
            else:
                edit = QSpinBox() if isinstance(spec.default, int) else QDoubleSpinBox()
                if isinstance(edit, QDoubleSpinBox):
                    edit.setDecimals(spec.decimals)
                    edit.setRange(spec.low, spec.high)
                    edit.setSingleStep(spec.step)
                else:
                    edit.setRange(int(spec.low), int(spec.high))
                    edit.setSingleStep(int(spec.step))
                edit.setSuffix(spec.suffix)
                edit.setKeyboardTracking(False)
                edit.setValue(values[spec.key])
                edit.valueChanged.connect(self.parameters_changed)
            edit.setMinimumWidth(105)
            edit.setToolTip(f"Local {name} design only; saved with the waveform recipe")
            self.parameter_widgets[spec.key] = edit
            row, col = index//2, (index % 2)*2
            self.parameters_grid.addWidget(QLabel(spec.label), row, col)
            self.parameters_grid.addWidget(edit, row, col+1)
        self.parameters_grid.setColumnStretch(1, 1)
        self.parameters_grid.setColumnStretch(3, 1)
        self.layout_changed.emit()

    def select_template(self, name):
        if self.recipe:
            self.load_template(name)
        else:
            values = template_parameters(name, self.parameter_cache.get(name))
            self.build_parameters(name, values)
            self.redraw()

    def parameter_values(self):
        return {key: edit.currentText() if isinstance(edit, QComboBox) else edit.value()
                for key, edit in self.parameter_widgets.items()}

    def parameters_changed(self, *_):
        name = self.templates.currentText()
        values = self.parameter_values()
        self.parameter_cache[name] = values
        if self.recipe and self.recipe["template"] == name:
            try:
                samples = template_samples(name, len(self.samples), values)
            except ValueError as exc:
                self.parameter_error = str(exc)
            else:
                self.remember()
                self.samples = samples
                self.recipe = dict(template=name, parameters=template_parameters(name, values))
                self.parameter_error = None
        self.redraw()

    def sync_count(self):
        self.count.blockSignals(True)
        text = str(len(self.samples))
        if self.count.findText(text) < 0:
            self.count.addItem(text)
        self.count.setCurrentText(text)
        self.count.blockSignals(False)

    def detach_recipe(self):
        self.recipe = None
        self.parameter_error = None

    def set_drawing(self, checked):
        self.plot.drawing = checked
        self.plot.setMouseEnabled(x=not checked, y=not checked)

    def start_stroke(self):
        self.remember()
        self.detach_recipe()
        self.last_index = None

    def draw_point(self, x, y):
        self.detach_recipe()
        index = int(round(np.clip(x, 0, 1)*(len(self.samples)-1)))
        value = float(np.clip(y, -1, 1))
        if self.last_index is None:
            self.samples[index] = value
        else:
            old = self.last_index
            lo, hi = sorted((old, index))
            if index != old:
                a, b = (self.samples[old], value) if old < index else (value, self.samples[old])
                self.samples[lo:hi+1] = np.linspace(a, b, hi-lo+1)
            else:
                self.samples[index] = value
        self.last_index = index
        self.redraw()

    def undo_stroke(self):
        if self.undo:
            self.samples, self.recipe = self.undo.pop()
            self.parameter_error = None
            self.sync_count()
            self.restore_recipe_controls()
            self.redraw()

    def restore_recipe_controls(self):
        if self.recipe:
            name, values = self.recipe["template"], self.recipe["parameters"]
            self.parameter_cache[name] = deepcopy(values)
            self.templates.blockSignals(True)
            self.templates.setCurrentText(name)
            self.templates.blockSignals(False)
            self.build_parameters(name, values)

    def resample(self, text):
        try:
            values = self.parameter_values() if self.recipe else None
            samples = template_samples(self.recipe["template"], int(text), values) if self.recipe else np.interp(
                np.linspace(0, 1, int(text)), np.linspace(0, 1, len(self.samples)), self.samples)
        except ValueError as exc:
            self.sync_count()
            self.redraw()
            self.status.setText("Sample count unchanged: " + str(exc))
            return
        self.remember()
        self.samples = samples
        if self.recipe:
            self.recipe["parameters"] = template_parameters(self.recipe["template"], values)
        self.parameter_error = None
        self.redraw()

    def clear(self):
        self.remember()
        self.detach_recipe()
        self.samples[:] = 0
        self.redraw()

    def sine(self):
        self.load_template("Sine")

    def load_template(self, name, parameters=None):
        name = canonical_template(name)
        values = template_parameters(name, self.parameter_cache.get(name) if parameters is None else parameters)
        try:
            samples = template_samples(name, int(self.count.currentText()), values)
        except ValueError as exc:
            # A failed selection leaves the current samples and controls consistent.
            self.templates.blockSignals(True)
            self.templates.setCurrentText(self.controls_template)
            self.templates.blockSignals(False)
            self.status.setText(str(exc))
            return
        self.remember()
        self.samples = samples
        self.recipe = dict(template=name, parameters=values)
        self.parameter_cache[name] = deepcopy(values)
        self.parameter_error = None
        self.templates.blockSignals(True)
        self.templates.setCurrentText(name)
        self.templates.blockSignals(False)
        self.build_parameters(name, values)
        self.draw.setChecked(False)
        self.redraw()

    def keep_cycle(self):
        name, ok = QInputDialog.getText(self, "Keep a local waveform", "Waveform name", text="My waveform")
        if ok:
            try:
                self.save_cycle(name)
            except (OSError, ValueError) as exc:
                self.status.setText("Cannot save cycle: " + str(exc))

    def save_cycle(self, name):
        if self.parameter_error:
            raise ValueError(self.parameter_error)
        name = self.library.save(name, self.samples, self.amplitude.value(), self.offset.value(), self.recipe)
        self.library_changed.emit()
        self.status.setText("Kept '" + name + "' in the local waveform library; select it again in the generator's waveform selector")

    def load_cycle(self, name):
        entry = self.library.entries[name]
        self.remember()
        self.samples = np.asarray(entry["samples"], dtype=float).copy()
        self.recipe = validate_recipe(entry["recipe"]) if entry.get("recipe") else None
        self.parameter_error = None
        self.sync_count()
        self.restore_recipe_controls()
        self.amplitude.setValue(entry["amplitude"])
        self.offset.setValue(entry["offset"])
        self.draw.setChecked(False)
        self.redraw()

    def redraw(self, *_):
        self.curve.setData(np.linspace(0, 1, len(self.samples)), self.samples)
        name = self.templates.currentText()
        shape_note = (" Dwell bias: 1 = equal step duration; higher values hold lower levels longer." if name.startswith("Stair") else
                      " Width spans the whole sinc window; zeros per side set the oscillations inside it." if name == "Sinc" else
                      " Argument start/span are dimensionless; decay shapes the envelope across the record." if name.startswith("Bessel") else "")
        self.source_note.setText(("Editable recipe: " + self.recipe["template"] + " · controls update the preview; Undo restores the previous design."
                                 if self.recipe else "CSV / drawn samples · controls do not replace this data until you click Generate shape.")
                                + shape_note)
        error = self.parameter_error
        try:
            volts = voltage_values(self.samples, self.amplitude.value(), self.offset.value())
            self.status.setText(f"{len(self.samples)} samples · export voltage {volts.min():.4g} to {volts.max():.4g} V · drawn/imported data remain local; no output is changed")
        except ValueError as exc:
            error = str(exc)
        if error:
            self.status.setText(error + " · previous samples retained; correct settings or Undo")
        self.keep_button.setEnabled(not error)
        self.export_button.setEnabled(not error)
        self.layout_changed.emit()

    def load_csv(self, path):
        samples = read_waveform_csv(path, int(self.count.currentText()), "V" if self.units.currentIndex() else "normalized",
                                    self.amplitude.value(), self.offset.value())
        self.remember()
        self.samples = samples
        self.detach_recipe()
        self.redraw()
        self.status.setText("Imported " + Path(path).name + " · preview only; nothing was transmitted to the device")

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import one-cycle waveform", "", "CSV (*.csv);;Text (*.txt)")
        if path:
            try:
                self.load_csv(path)
            except (OSError, ValueError) as exc:
                self.status.setText("Cannot import: " + str(exc))

    def export_csv(self):
        try:
            if self.parameter_error:
                raise ValueError(self.parameter_error)
            voltage_values(self.samples, self.amplitude.value(), self.offset.value())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export custom cycle", "custom_waveform.csv", "CSV (*.csv)")
        if path:
            try:
                save_waveform_csv(path, self.samples, self.amplitude.value(), self.offset.value())
                self.status.setText("Exported " + str(path) + " · phase, normalized samples and voltage_V; no device upload was performed")
            except (OSError, ValueError) as exc:
                self.status.setText("Cannot export: " + str(exc))
