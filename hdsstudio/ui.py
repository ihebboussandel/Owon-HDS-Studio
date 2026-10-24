"""Themed Qt desktop workbench for the OWON HDS2202S."""
from collections import deque
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import time

import numpy as np
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
import pyqtgraph as pg
from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer, QRectF, Signal
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .analysis import MathEngine, spectrum, spectrum_values, statistics
from .commands import BY_KEY, SETTINGS, MEASUREMENTS, WAVES, engineering, quantity
from .device import generator_wave, lower_keys, trigger_readout
from .storage import ReadingLog, load_capture, save_capture
from .themes import CHANNEL_COLORS, THEMES, stylesheet
from .worker import InstrumentWorker
from .multimeter import decode_reading
from .generator import CORE_KEYS, SOURCE_OHM, load_factor
from .waveform_editor import WaveformEditor
from .generator_controls import GeneratorControls
from .modulation_editor import ModulationEditor


def label(text, name=None):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    return w


def button(text, callback, name=None, checkable=False):
    w = QPushButton(text)
    if name:
        w.setObjectName(name)
    w.setCheckable(checkable)
    w.clicked.connect(callback)
    return w


def combo(items, current=None):
    w = QComboBox()
    w.addItems(items)
    if current:
        w.setCurrentText(current)
    return w


def scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


def table(headers):
    w = QTableWidget(0, len(headers))
    w.setHorizontalHeaderLabels(headers)
    w.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    w.verticalHeader().hide()
    w.setAlternatingRowColors(True)
    w.setEditTriggers(QTableWidget.NoEditTriggers)
    return w


def fill_table(widget, rows):
    widget.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for col, value in enumerate(values):
            widget.setItem(row, col, QTableWidgetItem(str(value)))


class ModeNotification(QFrame):
    """Floating, non-blocking reminder; never changes the window layout."""
    def __init__(self, parent):
        super().__init__(parent, objectName="modeToast")
        self.setFixedSize(430, 128)
        box = QVBoxLayout(self)
        box.setContentsMargins(13, 10, 13, 10)
        header = QHBoxLayout()
        self.title = label("Press Mode on the scope")
        self.title.setTextFormat(Qt.PlainText)
        self.title.setStyleSheet("font-weight:700;")
        header.addWidget(self.title, 1)
        dismiss = button("×", self.hide)
        dismiss.setFixedWidth(30)
        dismiss.setToolTip("Dismiss notification")
        header.addWidget(dismiss)
        box.addLayout(header)
        body = label("Use the scope's Mode button to match the app workspace. The app cannot switch the front-panel screen remotely through the available firmware protocol.")
        body.setTextFormat(Qt.PlainText)
        body.setWordWrap(True)
        box.addWidget(body)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def place(self):
        parent = self.parentWidget()
        self.move(max(10, parent.width()-self.width()-24), max(10, parent.height()-self.height()-56))

    def show_mode(self, mode):
        self.title.setText("Press Mode on scope → " + mode)
        self.place()
        self.show()
        self.raise_()
        self.timer.start(12000)


class SettingRow(QWidget):
    request = Signal(str, str)
    edited = Signal()

    def __init__(self, spec):
        super().__init__()
        self.spec, self.dirty = spec, False
        self.pending_value = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 2, 0, 5)
        title = QHBoxLayout()
        title.addWidget(label(spec.label))
        title.addStretch()
        self.actual = label("not read", "muted")
        self.actual.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title.addWidget(self.actual)
        box.addLayout(title)
        controls = QHBoxLayout()
        self.edit = combo(spec.choices, spec.default) if spec.choices else QLineEdit(spec.default)
        self.edit.setToolTip(spec.command + "\n" + spec.note)
        self.edit.setMinimumWidth(90)
        controls.addWidget(self.edit, 1)
        self.read = button("↻", self.query)
        self.read.setFixedWidth(35)
        self.read.setToolTip("Read current hardware setting")
        controls.addWidget(self.read)
        self.apply_button = button("Apply", self.apply)
        controls.addWidget(self.apply_button)
        box.addLayout(controls)
        if isinstance(self.edit, QComboBox):
            self.edit.currentTextChanged.connect(self.mark_dirty)
        else:
            self.edit.textEdited.connect(self.mark_dirty)
            self.edit.returnPressed.connect(self.apply)

    def text(self):
        return self.edit.currentText() if isinstance(self.edit, QComboBox) else self.edit.text()

    def mark_dirty(self):
        self.dirty = True
        self.edited.emit()

    def query(self):
        self.pending_value = self.text()
        self.request.emit(self.spec.key, self.spec.command+"?")

    def apply(self):
        if not self.apply_button.isEnabled():
            self.actual.setToolTip(self.apply_button.toolTip())
            return
        try:
            command = self.spec.write(self.text())
        except ValueError as exc:
            self.actual.setText(str(exc)[:28]+"…")
            self.actual.setToolTip(str(exc))
            return
        self.actual.setText("awaiting readback…")
        self.pending_value = self.text()
        self.request.emit(self.spec.key, command)

    def update_actual(self, value, populate=False):
        value = str(value)
        self.actual.setText("read: " + value[:24])
        self.actual.setToolTip("Hardware readback: " + value)
        if populate and not self.dirty and not self.edit.hasFocus():
            self.edit.blockSignals(True)
            if isinstance(self.edit, QComboBox):
                index = next((i for i in range(self.edit.count()) if self.edit.itemText(i).lower() == value.lower()), -1)
                if index >= 0:
                    self.edit.setCurrentIndex(index)
                elif self.spec.key == "gen_load":
                    self.edit.setCurrentIndex(-1)
            else:
                self.edit.setText(value)
            self.edit.blockSignals(False)


class StudioWindow(QMainWindow):
    def __init__(self, start_demo=True, persist=True):
        super().__init__()
        self.setWindowTitle("HDS Studio • OWON HDS2202S")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[1]/"assets"/"icon.svg")))
        self.resize(1510, 980)
        self.setMinimumSize(1120, 740)
        self.settings = QSettings("GeePs", "HDS2202SStudio") if persist else None
        self.worker = InstrumentWorker()
        self.rows, self.plots = {}, []
        self.latest = None
        self.math_trace = None
        self.reference = None
        self.backend = None
        self.last_frame_time = None
        self.frame_count = 0
        self.fps_times = deque(maxlen=20)
        self.spectrum_hold = None
        self.spectrum_average = None
        self.spectrum_key = None
        self.waterfall_rows = deque(maxlen=90)
        self.dmm_history = deque(maxlen=5000)
        self.dmm_unit = None
        self.dmm_measurement_key = None
        self.dmm_function_text = "Unknown (read front panel)"
        self.dmm_range_text = "Unknown"
        self.dmm_rel_text = "Unknown"
        self.meter_function_dirty = False
        self.reading_log = None
        self.record_dir = None
        self.record_count = 0
        self.closing = False
        self.replaying = False
        self.generator_loaded = False
        self.generator_baseline = None
        self.generator_refresh_pending = False
        self.generator_autoload = True
        self.generator_read_fields = set()
        self.last_mode_notice_target = None
        self.build_ui()
        self.mode_notification = ModeNotification(self)
        self.reset_generator_controls(clear=True)
        self.worker.frame.connect(self.receive_frame)
        self.worker.reading.connect(self.receive_reading)
        self.worker.connected.connect(self.on_connected)
        self.worker.disconnected.connect(self.on_disconnected)
        self.worker.reply.connect(self.on_reply)
        self.worker.error.connect(self.show_error)
        self.worker.trace.connect(self.add_trace)
        self.worker.native.connect(self.on_native)
        self.worker.generator_state.connect(self.on_generator_state)
        self.worker.generator_committed.connect(self.on_generator_committed)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()
        self.theme.currentTextChanged.connect(self.apply_theme)
        chosen = self.settings.value("theme", "Midnight") if self.settings else "Midnight"
        self.theme.setCurrentText(chosen if chosen in THEMES else "Midnight")
        self.apply_theme(self.theme.currentText())
        if self.settings:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        self.age_timer = QTimer(self)
        self.age_timer.timeout.connect(self.update_age)
        self.age_timer.start(500)
        if start_demo:
            self.worker.submit("connect", "Demo")

    def build_ui(self):
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 10)
        layout.setSpacing(12)
        header = QHBoxLayout()
        brand = QVBoxLayout()
        brand.addWidget(label("HDS Studio", "title"))
        brand.addWidget(label("OWON HDS2202S  /  MEASUREMENT WORKBENCH", "subtitle"))
        header.addLayout(brand)
        header.addStretch()
        self.source_badge = label("DISCONNECTED", "badge")
        self.source_badge.setMaximumHeight(32)
        header.addWidget(self.source_badge)
        self.theme = combo(list(THEMES))
        self.theme.setToolTip("Appearance")
        header.addWidget(self.theme)
        layout.addLayout(header)
        toolbar = QHBoxLayout()
        self.backend_choice = combo(["Demo", "USB • HDS2202S"])
        toolbar.addWidget(self.backend_choice)
        self.connect_button = button("Connect", self.connect_device, "primary")
        toolbar.addWidget(self.connect_button)
        toolbar.addWidget(button("Disconnect", lambda: self.worker.submit("disconnect")))
        self.identity = label("No instrument connected", "muted")
        self.identity.setTextInteractionFlags(Qt.TextSelectableByMouse)
        toolbar.addWidget(self.identity, 1)
        toolbar.addWidget(button("Open capture", self.open_capture))
        toolbar.addWidget(button("Export", self.export_capture))
        toolbar.addWidget(button("Screenshot", self.screenshot))
        layout.addLayout(toolbar)
        self.mode_status = label("Oscilloscope workspace · no instrument connected", "muted")
        self.mode_status.setWordWrap(True)
        self.mode_status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.mode_status)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.build_scope(), "◉  Oscilloscope")
        self.tabs.addTab(self.build_meter(), "⌁  Multimeter")
        self.tabs.addTab(self.build_generator(), "∿  Generator")
        self.tabs.addTab(self.build_math(), "ƒ  Math lab")
        self.tabs.addTab(self.build_spectrum(), "▥  Spectrum")
        self.tabs.addTab(self.build_instrument(), "⌘  Instrument")
        self.tabs.currentChanged.connect(self.tab_changed)
        layout.addWidget(self.tabs, 1)
        self.notice = label("Demo starts automatically. Connect USB when ready.", "notice")
        self.notice.setWordWrap(True)
        self.notice.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.notice)
        self.statusBar().showMessage("Ready")
        self.age = label("No capture", "muted")
        self.statusBar().addPermanentWidget(self.age)
        for title, shortcut, handler in (("Export capture", "Ctrl+S", self.export_capture), ("Open capture", "Ctrl+O", self.open_capture),
                                          ("Screenshot", "Ctrl+Shift+S", self.screenshot)):
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(handler)
            self.addAction(action)

    def new_plot(self, title, x="Elapsed screen time", xunit="s", y="Probe-tip voltage", yunit="V"):
        plot = pg.PlotWidget(title=title)
        plot.setLabel("bottom", x, units=xunit)
        plot.setLabel("left", y, units=yunit)
        plot.showGrid(x=True, y=True, alpha=.16)
        plot.addLegend(offset=(12, 12))
        plot.setMenuEnabled(True)
        plot.setClipToView(True)
        self.plots.append(plot)
        return plot

    def group(self, title):
        w = QGroupBox(title)
        box = QVBoxLayout(w)
        for spec in SETTINGS:
            if spec.group == title:
                row = SettingRow(spec)
                row.request.connect(self.send_command)
                self.rows[spec.key] = row
                box.addWidget(row)
        return w

    def build_scope(self):
        page, box = QWidget(), QVBoxLayout()
        page.setLayout(box)
        tools = QHBoxLayout()
        self.live_button = button("Live stream", self.toggle_live, "primary", True)
        self.live_button.setChecked(True)
        tools.addWidget(self.live_button)
        tools.addWidget(button("Capture now", lambda: self.worker.submit("capture")))
        tools.addWidget(button("Run scope", lambda: self.send_command("run", ":RUNning RUN")))
        tools.addWidget(button("Stop scope", lambda: self.send_command("run", ":RUNning STOP"), "danger"))
        tools.addWidget(button("Arm single", lambda: self.worker.submit("single")))
        tools.addStretch()
        tools.addWidget(label("Refresh", "muted"))
        self.refresh = combo(["100 ms", "200 ms", "500 ms", "1 s", "2 s"], "200 ms")
        self.refresh.currentTextChanged.connect(lambda x: self.worker.submit("interval", quantity(x, "s")))
        tools.addWidget(self.refresh)
        self.scope_record = button("Record frames", self.toggle_recording, checkable=True)
        tools.addWidget(self.scope_record)
        box.addLayout(tools)
        split = QSplitter()
        left, leftbox = QWidget(), QVBoxLayout()
        left.setLayout(leftbox)
        leftbox.setContentsMargins(0, 0, 0, 0)
        metrics = QHBoxLayout()
        self.metric_values = []
        for name, color in zip(("CH1 · PEAK TO PEAK", "CH2 · PEAK TO PEAK", "TRIGGER", "EXPORT RATE"), CHANNEL_COLORS):
            card = QFrame(objectName="card")
            cardbox = QVBoxLayout(card)
            caption = label(name, "muted")
            caption.setStyleSheet(f"color:{color}")
            cardbox.addWidget(caption)
            value = label("—", "metric")
            cardbox.addWidget(value)
            self.metric_values.append(value)
            metrics.addWidget(card)
        leftbox.addLayout(metrics)
        viewtools = QHBoxLayout()
        self.view_mode = combo(["Time domain", "XY • CH1 vs CH2"])
        self.view_mode.currentIndexChanged.connect(lambda: self.render_scope())
        viewtools.addWidget(self.view_mode)
        self.show_channels = []
        for name in ("CH1", "CH2"):
            check = QCheckBox(name)
            check.setChecked(True)
            check.toggled.connect(lambda: self.render_scope())
            self.show_channels.append(check)
            viewtools.addWidget(check)
        self.cursor_toggle = QCheckBox("Cursors")
        self.cursor_toggle.toggled.connect(self.set_cursors)
        viewtools.addWidget(self.cursor_toggle)
        viewtools.addStretch()
        viewtools.addWidget(button("Reference", self.set_reference))
        viewtools.addWidget(button("Clear ref", self.clear_reference))
        viewtools.addWidget(button("Fit view", lambda: self.scope_plot.autoRange()))
        leftbox.addLayout(viewtools)
        self.trigger_status = label("TRIGGER · waiting for waveform", "triggerReadout")
        self.trigger_status.setTextFormat(Qt.PlainText)
        self.trigger_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.trigger_status.setWordWrap(True)
        self.trigger_status.setToolTip("Source, edge, sweep mode, voltage level, coupling and acquisition state from the captured waveform header.")
        leftbox.addWidget(self.trigger_status)
        self.scope_plot = self.new_plot("DUAL CHANNEL  ·  LIVE WAVEFORM")
        self.scope_curves = [self.scope_plot.plot(pen=pg.mkPen(c, width=1.8), name=f"CH{i+1}") for i, c in enumerate(CHANNEL_COLORS[:2])]
        self.envelope_curves = []
        for color in CHANNEL_COLORS[:2]:
            a = self.scope_plot.plot(pen=pg.mkPen(color, width=.5))
            b = self.scope_plot.plot(pen=pg.mkPen(color, width=.5))
            fill = pg.FillBetweenItem(a, b, brush=pg.mkBrush(color+"24"))
            self.scope_plot.addItem(fill)
            self.envelope_curves.append((a, b, fill))
        self.ref_curve = self.scope_plot.plot(pen=pg.mkPen(CHANNEL_COLORS[2], width=1, style=Qt.DashLine), name="Reference CH1")
        self.trigger_line = pg.InfiniteLine(angle=0, movable=False,
            pen=pg.mkPen(CHANNEL_COLORS[0], width=1.2, style=Qt.DashLine), label="T",
            labelOpts=dict(position=.97, anchors=[(1, 0), (1, 1)],
                           color=CHANNEL_COLORS[0], fill=pg.mkBrush(12, 20, 32, 220)))
        self.trigger_line.setZValue(20)
        self.scope_plot.addItem(self.trigger_line, ignoreBounds=True)
        self.trigger_line.hide()
        self.cursor_lines = []
        for angle, pos, color in ((90, .001, CHANNEL_COLORS[2]), (90, .002, CHANNEL_COLORS[2]),
                                  (0, 0, CHANNEL_COLORS[3]), (0, 1, CHANNEL_COLORS[3])):
            line = pg.InfiniteLine(pos=pos, angle=angle, movable=True, pen=pg.mkPen(color, style=Qt.DashLine))
            self.scope_plot.addItem(line)
            line.hide()
            line.sigPositionChanged.connect(self.update_cursors)
            self.cursor_lines.append(line)
        leftbox.addWidget(self.scope_plot, 1)
        self.cursor_readout = label("Drag to pan · wheel to zoom · right-click for plot options", "muted")
        leftbox.addWidget(self.cursor_readout)
        self.readouts = QTabWidget()
        self.host_table = table(["Software estimate", "CH1", "CH2"])
        self.readouts.addTab(self.host_table, "Software measurements")
        nativepage = QWidget()
        nativebox = QVBoxLayout(nativepage)
        nativebox.addWidget(button("Read all 14 scope measurements", lambda: self.worker.submit("native")))
        self.native_table = table(["Scope measurement", "Hardware response"])
        nativebox.addWidget(self.native_table)
        self.readouts.addTab(nativepage, "Scope readings")
        self.readouts.setMaximumHeight(235)
        leftbox.addWidget(self.readouts)
        split.addWidget(left)
        controls = QTabWidget()
        for name, groups in (("CH1", ["Channel 1"]), ("CH2", ["Channel 2"]), ("Time / trigger", ["Horizontal", "Trigger"])):
            w = QWidget()
            layout = QVBoxLayout(w)
            for group in groups:
                layout.addWidget(self.group(group))
            layout.addStretch()
            controls.addTab(scroll(w), name)
        controls.setMinimumWidth(345)
        split.addWidget(controls)
        split.setSizes([1070, 370])
        box.addWidget(split, 1)
        return page

    def build_meter(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.addWidget(label("Select Multimeter on the instrument front panel before using USB readings. Tabs select the app workspace.", "notice"))
        split = QSplitter()
        main = QWidget()
        mainbox = QVBoxLayout(main)
        self.meter_value = label("— — . — — —", "meterValue")
        self.meter_value.setAlignment(Qt.AlignCenter)
        self.meter_value.setTextFormat(Qt.PlainText)
        self.meter_value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.meter_value.setFixedHeight(112)
        mainbox.addWidget(self.meter_value)
        self.meter_state = label("No multimeter reading yet", "muted")
        self.meter_state.setAlignment(Qt.AlignCenter)
        self.meter_state.setTextFormat(Qt.PlainText)
        self.meter_state.setWordWrap(True)
        self.meter_state.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.meter_state.setFixedHeight(42)
        mainbox.addWidget(self.meter_state)
        self.meter_plot = self.new_plot("READING TREND", "Host elapsed time", "s", "Reading", "V")
        self.meter_plot.getAxis("left").setWidth(90)
        self.meter_curve = self.meter_plot.plot(pen=pg.mkPen(CHANNEL_COLORS[3], width=2))
        mainbox.addWidget(self.meter_plot, 1)
        statsbar = QHBoxLayout()
        self.meter_stat_values = {}
        for name in ("MIN", "MAX", "MEAN", "σ"):
            card = QFrame(objectName="card")
            card.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            card.setFixedHeight(76)
            cardbox = QVBoxLayout(card)
            cardbox.addWidget(label(name, "muted"))
            value = label("—", "meterStat")
            value.setTextFormat(Qt.PlainText)
            value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            cardbox.addWidget(value)
            self.meter_stat_values[name] = value
            statsbar.addWidget(card, 1)
        mainbox.addLayout(statsbar)
        tools = QHBoxLayout()
        self.meter_stream = button("Live readings", self.toggle_live, "primary", True)
        self.meter_stream.setChecked(True)
        tools.addWidget(self.meter_stream)
        self.meter_hold = button("Hold display", lambda: None, checkable=True)
        tools.addWidget(self.meter_hold)
        tools.addWidget(button("Read once", lambda: self.worker.submit("capture")))
        tools.addWidget(button("Clear trend", self.clear_meter))
        self.log_button = button("Log CSV", self.toggle_meter_log, checkable=True)
        tools.addWidget(self.log_button)
        mainbox.addLayout(tools)
        split.addWidget(main)
        side = QWidget()
        sidebox = QVBoxLayout(side)
        function = QGroupBox("Measurement function")
        functionbox = QVBoxLayout(function)
        self.meter_function = combo(["DC voltage", "AC voltage", "DC current", "AC current", "Resistance", "Capacitance", "Diode", "Continuity"])
        self.meter_function.activated.connect(lambda _: setattr(self, "meter_function_dirty", True))
        functionbox.addWidget(self.meter_function)
        functionbox.addWidget(button("Apply function", self.set_meter_function, "primary"))
        functionbox.addWidget(button("Read current function", lambda: self.send_command("dmm_function", ":DMM:CONFigure?")))
        functionbox.addWidget(button("Auto range", lambda: self.send_command("dmm_auto", ":DMM:AUTO ON")))
        sidebox.addWidget(function)
        sidebox.addWidget(self.group("Multimeter"))
        sidebox.addStretch()
        split.addWidget(side)
        split.setSizes([1000, 360])
        box.addWidget(split, 1)
        return page

    def build_generator(self):
        page = QWidget()
        box = QVBoxLayout(page)
        intro = label("For hardware output select Generator on the front panel. CSV/drawing is local. Output changes only through explicit setting/output actions.", "notice")
        intro.setWordWrap(True)
        box.addWidget(intro)
        split = QSplitter()
        main = QWidget()
        mainbox = QVBoxLayout(main)
        heading = QHBoxLayout()
        self.output_badge = label("OUTPUT · UNKNOWN", "badge")
        heading.addWidget(self.output_badge)
        heading.addStretch()
        heading.addWidget(button("Load device settings", self.refresh_generator))
        heading.addWidget(button("Read output", lambda: self.send_command("output", ":CHANnel?")))
        self.enable_output = button("Enable output", lambda: self.send_command("output", ":CHANnel ON"), "primary")
        self.disable_output = button("Disable output", lambda: self.send_command("output", ":CHANnel OFF"), "danger")
        heading.addWidget(self.enable_output)
        heading.addWidget(self.disable_output)
        mainbox.addLayout(heading)
        self.generator_sync_status = label("Load the device's current parameters before configuring output.", "notice")
        self.generator_sync_status.setWordWrap(True)
        mainbox.addWidget(self.generator_sync_status)
        selection = QHBoxLayout()
        selection.addWidget(label("Waveform"))
        self.waveform_selector = QComboBox()
        self.waveform_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.waveform_selector.setMinimumContentsLength(18)
        self.waveform_selector.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        names = ("Sine", "Square", "Ramp / triangle", "Pulse", "Arbitrary · AmpALT", "Arbitrary · AttALT",
                 "Arbitrary · Stair down", "Arbitrary · Stair up/down", "Arbitrary · Stair up",
                 "Arbitrary · Bessel J", "Arbitrary · Bessel Y", "Arbitrary · Sinc")
        for text, code in zip(names, WAVES):
            self.waveform_selector.addItem(text, ("device", code))
        self.waveform_selector.insertSeparator(self.waveform_selector.count())
        for text, code in (("Arbitrary · custom editor", "editor"), ("Arbitrary · import CSV…", "import"),
                           ("Arbitrary · draw your own", "draw")):
            self.waveform_selector.addItem(text, ("custom", code))
        self.waveform_selector.activated.connect(self.choose_generator_waveform)
        selection.addWidget(self.waveform_selector, 1)
        self.apply_waveform = button("Apply waveform", self.apply_selected_waveform, "primary")
        selection.addWidget(self.apply_waveform)
        mainbox.addLayout(selection)
        self.waveform_selection_note = label("Local selection / preview · device settings have not been loaded", "muted")
        self.waveform_selection_note.setWordWrap(True)
        mainbox.addWidget(self.waveform_selection_note)
        impedance = QGroupBox("Source impedance and load reference")
        self.impedance_panel = impedance
        impedancebox = QVBoxLayout(impedance)
        impedance_info = label("Source impedance: 50 Ω (hardware). Set the numeric device load reference on its front panel.", "muted")
        impedance_info.setWordWrap(True)
        impedancebox.addWidget(impedance_info)
        loadbar = QHBoxLayout()
        self.preview_load_mode = combo(["High Z preview", "Resistive load preview"])
        self.preview_load_ohm = QDoubleSpinBox()
        self.preview_load_ohm.setRange(1, 10000)
        self.preview_load_ohm.setDecimals(1)
        self.preview_load_ohm.setValue(50)
        self.preview_load_ohm.setSuffix(" Ω")
        loadbar.addWidget(self.preview_load_mode)
        loadbar.addWidget(self.preview_load_ohm)
        loadbar.addStretch()
        impedancebox.addLayout(loadbar)
        self.load_preview_note = label("The preview load is local only; it sends no impedance command to the scope.", "muted")
        self.load_preview_note.setWordWrap(True)
        impedancebox.addWidget(self.load_preview_note)
        mainbox.addWidget(impedance)
        self.generator_views = QTabWidget()
        self.generator_views.setFixedHeight(350)
        built_in = QWidget()
        built_box = QVBoxLayout(built_in)
        self.generator_plot = self.new_plot("WAVEFORM PREVIEW  ·  EDITED SETTINGS, NOT A MEASUREMENT", "Preview time", "s", "Nominal output voltage", "V")
        self.generator_plot.setFixedHeight(230)
        self.generator_curve = self.generator_plot.plot(pen=pg.mkPen(CHANNEL_COLORS[3], width=2), name="Nominal settings preview")
        self.generator_load_curve = self.generator_plot.plot(pen=pg.mkPen(CHANNEL_COLORS[1], width=1.5, style=Qt.DashLine), name="Open-circuit divider estimate")
        built_box.addWidget(self.generator_plot, 1)
        explanation = label("Principal shapes are previews; built-in arbitrary shapes are illustrative. The dashed load estimate assumes the entered voltage is an open-circuit reference, independently of the device's load flag.", "muted")
        explanation.setWordWrap(True)
        built_box.addWidget(explanation)
        self.generator_views.addTab(built_in, "Built-in waveform")
        library_path = Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation))/"hds-studio"/"waveforms.json" if self.settings else None
        self.waveform_editor = WaveformEditor(library_path)
        self.waveform_editor.use_device.clicked.connect(self.copy_generator_to_editor)
        self.waveform_editor.library_changed.connect(self.refresh_waveform_library)
        self.plots.append(self.waveform_editor.plot)
        self.generator_views.addTab(self.waveform_editor, "CSV / draw waveform")
        mainbox.addWidget(self.generator_views, 1)
        split.addWidget(scroll(main))
        side = QWidget()
        sidebox = QVBoxLayout(side)
        self.generator_raw_groups = [self.group("Generator"), self.group("Generator advanced")]
        for group in self.generator_raw_groups:
            group.setParent(side)
            group.hide()
        self.rows["gen_wave"].hide()
        self.generator_controls = GeneratorControls(self.rows, self.reload_generator_signal)
        self.generator_controls.apply_group.connect(lambda group, revision, payload: self.worker.submit("generator_apply", group, revision, payload))
        self.generator_controls.changed.connect(self.update_generator_controls)
        self.generator_controls.changed.connect(self.render_generator)
        self.generator_controls.waveform_requested.connect(self.prepare_generator_configuration)
        sidebox.addWidget(self.generator_controls)
        self.modulation_editor = ModulationEditor(self.generator_carrier_values)
        self.plots.append(self.modulation_editor.plot)
        self.generator_views.addTab(self.modulation_editor, "AM / FM design")
        split.addWidget(scroll(side))
        split.setSizes([1000, 370])
        box.addWidget(split, 1)
        for spec in SETTINGS:
            if spec.group.startswith("Generator"):
                row = self.rows[spec.key]
                row.edited.connect(self.update_generator_controls)
                edit = row.edit
                (edit.currentTextChanged if isinstance(edit, QComboBox) else edit.textChanged).connect(self.render_generator)
        self.render_generator()
        self.preview_load_mode.currentIndexChanged.connect(self.render_generator)
        self.preview_load_ohm.valueChanged.connect(self.render_generator)
        self.generator_views.currentChanged.connect(self.generator_view_changed)
        self.waveform_editor.layout_changed.connect(lambda: QTimer.singleShot(0, self.resize_waveform_editor))
        self.rows["gen_wave"].edit.currentTextChanged.connect(self.sync_waveform_selector)
        self.refresh_waveform_library()
        return page

    def generator_view_changed(self, index):
        self.impedance_panel.setVisible(index == 0)
        self.generator_views.setFixedHeight(640 if index == 2 else 350)
        self.resize_waveform_editor()
        if index == 1 and self.waveform_selector.currentData()[0] == "device":
            self.waveform_selector.setCurrentIndex(13)
        elif index in (0, 2) and self.waveform_selector.currentData()[0] != "device":
            self.sync_waveform_selector()
        if index == 1:
            self.waveform_selection_note.setText("Custom arbitrary waveform · local design/export; USB upload is unavailable")
        elif index == 2:
            self.waveform_selection_note.setText("AM / FM design · local preview/export; hardware output is not modulated")
        else:
            self.waveform_selection_note.setText("Built-in waveform preview · Apply sends the selected shape to the device")
        self.update_generator_controls()

    def resize_waveform_editor(self):
        if self.generator_views.currentIndex() == 1:
            self.waveform_editor.layout().activate()
            self.generator_views.setFixedHeight(max(650, self.waveform_editor.minimumSizeHint().height()+45))

    def refresh_waveform_library(self):
        selected = self.waveform_selector.currentData()
        while self.waveform_selector.count() > 16:
            self.waveform_selector.removeItem(16)
        for name in self.waveform_editor.library.entries:
            self.waveform_selector.addItem("Saved · " + name, ("library", name))
        for index in range(self.waveform_selector.count()):
            if self.waveform_selector.itemData(index) == selected:
                self.waveform_selector.setCurrentIndex(index)
                break

    def sync_waveform_selector(self):
        if self.generator_views.currentIndex() in (0, 2):
            value = self.rows["gen_wave"].text()
            index = next((i for i in range(12) if self.waveform_selector.itemData(i)[1].upper() == value.upper()), 0)
            self.waveform_selector.setCurrentIndex(index)

    def choose_generator_waveform(self, index):
        choice = self.waveform_selector.itemData(index)
        if not choice:
            return
        kind, value = choice
        if kind == "device":
            self.rows["gen_wave"].edit.setCurrentText(value)
            if self.generator_views.currentIndex() != 2:
                self.generator_views.setCurrentIndex(0)
            self.waveform_selection_note.setText("Selected " + self.waveform_selector.itemText(index) + " · preview only until Apply waveform")
            self.render_generator()
        else:
            self.generator_views.setCurrentIndex(1)
            self.waveform_selection_note.setText("Custom arbitrary waveform · local design/export; USB upload is unavailable")
            if kind == "library":
                self.waveform_editor.load_cycle(value)
            elif value == "draw":
                self.waveform_editor.draw.setChecked(True)
            elif value == "import":
                self.waveform_editor.import_csv()
        self.update_generator_controls()

    def apply_selected_waveform(self):
        if self.generator_views.currentIndex() == 0:
            self.rows["gen_wave"].apply()

    def prepare_generator_configuration(self, shape, configuration):
        index = next(i for i in range(12) if self.waveform_selector.itemData(i)[1].upper() == shape.upper())
        self.waveform_selector.setCurrentIndex(index)
        self.choose_generator_waveform(index)
        selector = self.generator_controls.configuration
        selected = selector.findData(configuration)
        if selected >= 0:
            self.generator_controls.configure(selected)

    def build_math(self):
        page = QWidget()
        box = QVBoxLayout(page)
        title = QHBoxLayout()
        title.addWidget(label("Software math", "title"))
        title.addStretch()
        self.math_presets = combo(["Custom expression", "CH1 - CH2", "CH1 + CH2", "CH1 * CH2", "CH1 / CH2", "diff(CH1)", "integ(ac(CH1))", "smooth(CH1, 9)", "envelope(ac(CH1))", "lowpass(CH1, 10000)", "highpass(CH1, 100)", "phase(ac(CH1))"])
        self.math_presets.currentTextChanged.connect(self.choose_math)
        title.addWidget(self.math_presets)
        box.addLayout(title)
        editbar = QHBoxLayout()
        self.math_expression = QLineEdit("CH1 - CH2")
        self.math_expression.setPlaceholderText("e.g. (CH1 - CH2) / 0.1")
        self.math_expression.returnPressed.connect(self.render_math)
        editbar.addWidget(self.math_expression, 1)
        editbar.addWidget(label("Output unit"))
        self.math_unit = QLineEdit("V")
        self.math_unit.setMaximumWidth(100)
        editbar.addWidget(self.math_unit)
        editbar.addWidget(button("Evaluate", self.render_math, "primary"))
        box.addLayout(editbar)
        self.math_plot = self.new_plot("MATH TRACE")
        self.math_curve = self.math_plot.plot(pen=pg.mkPen(CHANNEL_COLORS[2], width=2), name="MATH")
        box.addWidget(self.math_plot, 1)
        self.math_status = label("Waiting for a waveform", "notice")
        self.math_status.setWordWrap(True)
        box.addWidget(self.math_status)
        helptext = label("CH1, CH2: probe-tip volts · t: elapsed screen seconds · pi, e\n"
                         "Operators: + − * / **   |   Functions: abs, sqrt, log, log10, exp, sin, cos, tan, ac, diff, integ, smooth, lowpass, highpass, bandpass, envelope, phase, clip, rms, mean, min, max\n"
                         "Filter cutoffs are Hz; phase is radians; diff returns per second; integ returns × seconds. Set the output unit for your expression.\n"
                         "Cross-channel math uses sequential USB screen reads. For a stable pair, stop the scope and capture; the app verifies repeat-read equality.", "muted")
        helptext.setWordWrap(True)
        box.addWidget(helptext)
        return page

    def build_spectrum(self):
        page = QWidget()
        box = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.spec_source = combo(["CH1", "CH2", "MATH"])
        self.spec_window = combo(["hann", "hamming", "blackmanharris", "flattop", "boxcar"])
        self.spec_units = combo(["dBV", "Vrms", "dBm", "V²/Hz", "dB(V²/Hz)"])
        self.spec_dc = QCheckBox("Remove DC")
        self.spec_dc.setChecked(True)
        self.spec_max = QCheckBox("Max hold")
        self.spec_average = QCheckBox("Average power")
        self.spec_log = QCheckBox("Log frequency")
        for caption, widget in (("Source", self.spec_source), ("Window", self.spec_window), ("Scale", self.spec_units)):
            controls.addWidget(label(caption, "muted"))
            controls.addWidget(widget)
        controls.addWidget(self.spec_dc)
        controls.addWidget(self.spec_max)
        controls.addWidget(self.spec_average)
        controls.addWidget(self.spec_log)
        controls.addStretch()
        controls.addWidget(label("Ref Ω"))
        self.spec_resistance = QDoubleSpinBox()
        self.spec_resistance.setRange(.1, 1e6)
        self.spec_resistance.setValue(50)
        self.spec_resistance.setMaximumWidth(110)
        controls.addWidget(self.spec_resistance)
        controls.addWidget(button("Reset", self.reset_spectrum))
        box.addLayout(controls)
        self.spectrum_plot = self.new_plot("SPECTRUM  ·  HOST FFT", "Frequency", "Hz", "Amplitude", "dBV")
        self.spectrum_curve = self.spectrum_plot.plot(pen=pg.mkPen(CHANNEL_COLORS[0], width=1.8), name="Spectrum")
        self.spectrum_hold_curve = self.spectrum_plot.plot(pen=pg.mkPen(CHANNEL_COLORS[2], width=1), name="Max hold")
        box.addWidget(self.spectrum_plot, 3)
        self.spec_metrics = label("Waiting for a waveform", "metric")
        box.addWidget(self.spec_metrics)
        self.waterfall_plot = self.new_plot("WATERFALL  ·  LAST 90 REFRESHES", "Frequency", "Hz", "Refresh index", "")
        self.waterfall = pg.ImageItem(axisOrder="row-major")
        self.waterfall_plot.addItem(self.waterfall)
        self.waterfall.setLookupTable(pg.colormap.get("viridis").getLookupTable())
        box.addWidget(self.waterfall_plot, 2)
        self.spec_note = label("FFT uses exported waveform spacing, independently of the internal ADC rate. dBm assumes the entered resistance; it does not set the scope input impedance.", "notice")
        self.spec_note.setWordWrap(True)
        box.addWidget(self.spec_note)
        for w in (self.spec_source, self.spec_window, self.spec_units):
            w.currentTextChanged.connect(self.reset_spectrum)
        for w in (self.spec_dc, self.spec_max, self.spec_average, self.spec_log):
            w.toggled.connect(self.reset_spectrum)
        self.spec_resistance.valueChanged.connect(self.reset_spectrum)
        return page

    def build_instrument(self):
        page = QWidget()
        box = QVBoxLayout(page)
        info = label("HDS2202S command reference and live metadata\nAll exposed controls use the HDS200 SCPI catalogue. Firmware support is confirmed by readback. Front-panel mode switching, battery level, screen pixels and custom arbitrary-wave uploads have no command in the referenced protocol.", "notice")
        info.setWordWrap(True)
        box.addWidget(info)
        split = QSplitter(Qt.Vertical)
        upper = QTabWidget()
        self.metadata_view = QPlainTextEdit()
        self.metadata_view.setReadOnly(True)
        upper.addTab(self.metadata_view, "Complete waveform header + provenance")
        self.command_table = table(["Control", "Set command", "Query", "Notes"])
        rows = [[s.label, s.command+" <value>", s.command+"?", s.note] for s in SETTINGS]
        rows += [["Identity", "—", "*IDN?", "Model, serial, firmware"], ["Output", ":CHANnel ON / OFF", ":CHANnel?", "Generator output"],
                 ["Run / stop", ":RUNning RUN / STOP", ":TRIGger:STATus?", "Locally exercised firmware extension"],
                 ["DMM", ":DMM:CONFigure <function>", ":DMM:MEAS?", "VOLTage:AC/DC, CURRent:AC/DC, resistance, diode, continuity, capacitance"],
                 ["DMM auto range", ":DMM:AUTO ON", ":DMM:RANGE?", "Read current range after enabling auto"],
                 ["Scope readings", "—", ":MEASurement:CH<n>:<item>?", ", ".join(MEASUREMENTS)],
                 ["Screen export", "—", ":DATA:WAVE:SCREEN:HEAD? / CH1? / CH2?", "Length-prefixed JSON / screen bytes"]]
        fill_table(self.command_table, rows)
        upper.addTab(self.command_table, "Command catalogue")
        workspace = QWidget()
        workspacebox = QVBoxLayout(workspace)
        workspacebox.addWidget(label("Workspace presets store edited controls, theme, math and spectrum preferences. Loading a preset fills the controls for review; apply each hardware setting when ready.", "notice"))
        workspacebox.addWidget(button("Save workspace preset", self.save_preset))
        workspacebox.addWidget(button("Load workspace preset", self.load_preset))
        workspacebox.addStretch()
        upper.addTab(workspace, "Workspace presets")
        split.addWidget(upper)
        lower = QWidget()
        lowerbox = QVBoxLayout(lower)
        bar = QHBoxLayout()
        self.command_entry = QLineEdit("*IDN?")
        self.command_entry.returnPressed.connect(self.console_send)
        bar.addWidget(self.command_entry, 1)
        bar.addWidget(button("Send command", self.console_send, "primary"))
        bar.addWidget(button("Save log", self.save_trace))
        bar.addWidget(button("Clear", lambda: self.console.clear()))
        lowerbox.addLayout(bar)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2000)
        self.console.setFont(QFont("DejaVu Sans Mono", 10))
        lowerbox.addWidget(self.console)
        split.addWidget(lower)
        split.setSizes([430, 260])
        box.addWidget(split, 1)
        return page

    def connect_device(self):
        self.connect_button.setEnabled(False)
        self.worker.submit("connect", self.backend_choice.currentText())

    def on_connected(self, backend, identity):
        self.backend = backend
        self.last_mode_notice_target = None
        self.connect_button.setEnabled(True)
        self.identity.setText(identity)
        self.source_badge.setText("DEMO · SYNTHETIC" if backend == "Demo" else "USB · CONNECTED")
        self.output_badge.setText("OUTPUT · UNKNOWN")
        self.latest = None
        self.update_trigger_labels()
        self.replaying = False
        self.last_frame_time = None
        self.fps_times.clear()
        self.clear_meter()
        self.clear_reference()
        self.reset_spectrum()
        self.notice.setText("Demo instrument • synthetic data" if backend == "Demo" else "USB connected • choose the matching front-panel mode for each workspace. No settings are applied on connect.")
        for row in self.rows.values():
            row.actual.setText("not read")
            row.dirty = False
        self.reset_generator_controls(clear=True)
        self.tab_changed(self.tabs.currentIndex())

    def on_disconnected(self):
        self.backend = None
        self.last_mode_notice_target = None
        self.mode_notification.hide()
        self.connect_button.setEnabled(True)
        self.source_badge.setText("DISCONNECTED")
        self.identity.setText("No instrument connected")
        self.output_badge.setText("OUTPUT · UNKNOWN")
        self.reset_generator_controls()
        self.update_trigger_labels()
        self.update_mode_status()
        self.stop_recording()
        self.stop_meter_log()

    def send_command(self, token, command):
        if command.strip().lower() == ":channel on" and any(row.dirty for key, row in self.rows.items() if key.startswith("gen_")):
            self.show_error("Apply or query the pending generator edits before enabling output; the device still has its previous settings")
            return
        self.worker.submit("command", token, command)

    def on_reply(self, token, text):
        if token in self.rows:
            row = self.rows[token]
            if row.pending_value == row.text():
                row.dirty = False
            row.update_actual(text, populate=True)
            if token.startswith("gen_"):
                self.generator_read_fields.add(token)
                self.generator_controls.sync()
                if token == "gen_wave":
                    self.sync_waveform_selector()
                self.update_generator_controls()
                self.render_generator()
        if token == "output":
            self.output_badge.setText("OUTPUT · " + text.upper())
        if token == "dmm_function":
            self.dmm_function_text = text
            self.clear_meter()
        if token in ("dmm_range", "dmm_auto"):
            self.dmm_range_text = text
        if token == "dmm_rel":
            self.dmm_rel_text = text
            self.clear_meter()
        self.statusBar().showMessage(f"{token}: {text[:160]}", 8000)
        self.add_trace(f"[{token}] {text[:1000]}")

    def show_error(self, message):
        self.connect_button.setEnabled(True)
        self.notice.setText("⚠ " + message)
        self.add_trace("ERROR  " + message)
        self.statusBar().showMessage(message, 12000)
        self.generator_controls.failed(message)
        self.update_generator_controls()
        if self.tabs.currentIndex() == 2 and not self.generator_loaded:
            self.generator_sync_status.setText("Device settings unavailable: " + message + ". Selection and local editing remain available. Use Load device settings to retry.")

    def add_trace(self, text):
        self.console.appendPlainText(datetime.now().strftime("%H:%M:%S.%f")[:-3] + "  " + text)

    def tab_changed(self, index):
        mode = "dmm" if index == 1 else "scope" if index in (0, 3, 4) else "idle"
        self.worker.submit("mode", mode)
        self.update_mode_status()
        if index == 2 and self.backend is not None and self.generator_autoload:
            self.refresh_generator()
        if index == 4:
            self.render_spectrum(advance=False)
        if index == 3:
            self.render_math()

    def update_mode_status(self):
        index = self.tabs.currentIndex()
        workspace = ("Oscilloscope", "Multimeter", "Generator", "Math lab", "Spectrum", "Instrument")[index]
        if self.backend is None:
            text = f"{workspace} workspace · no instrument connected"
        elif self.backend == "Demo":
            text = f"DEMO · {workspace} workspace · simulated instrument"
        elif index == 5:
            text = "Instrument console · front-panel mode is unchanged"
        else:
            target = "Multimeter" if index == 1 else "Generator" if index == 2 else "Oscilloscope"
            text = f"{workspace} workspace · select {target} using the instrument's Mode key. Automatic screen-mode switching has no verified USB command."
            if target != self.last_mode_notice_target:
                self.mode_notification.show_mode(target)
                self.last_mode_notice_target = target
        if self.backend is None or self.backend == "Demo" or index == 5:
            self.mode_notification.hide()
        self.mode_status.setText(text)

    def toggle_live(self, checked):
        if checked:
            self.replaying = False
        self.worker.submit("live", checked)
        self.live_button.setChecked(checked)
        self.live_button.setText("Live stream" if checked else "Stream paused")
        self.meter_stream.setChecked(checked)
        self.meter_stream.setText("Live readings" if checked else "Readings paused")

    def receive_frame(self, frame):
        if self.replaying and not frame.metadata.get("replay"):
            return
        self.latest = frame
        self.frame_count += 1
        self.last_frame_time = time.monotonic()
        self.fps_times.append(self.last_frame_time)
        self.source_badge.setText("REPLAY" if frame.metadata.get("replay") else "DEMO · SYNTHETIC" if self.backend == "Demo" else "USB · SCREEN DATA")
        h = lower_keys(frame.header)
        active = [np.all(np.isfinite(y)) for y in frame.channels]
        for i in range(2):
            self.metric_values[i].setText(engineering(np.ptp(frame.channels[i]), "V") if active[i] else "OFF")
        state = h.get("runstatus", "—")
        self.metric_values[2].setText(str(state))
        self.metric_values[3].setText(engineering(frame.metadata.get("export_rate_hz"), "Sa/s"))
        for c in h.get("channel", []):
            name = c["name"].lower()
            for suffix, value in (("display", c.get("display")), ("coupling", c.get("coupling")), ("probe", c.get("probe")),
                                  ("scale", (frame.metadata.get("scales_readback") or {}).get(name.upper(), c.get("scale"))),
                                  ("offset", float(c.get("offset", 0))/25)):
                self.rows[f"{name}_{suffix}"].update_actual(value, populate=True)
        self.rows["timebase"].update_actual(h["timebase"]["scale"], populate=True)
        # The header's horizontal offset representation is retained verbatim in
        # metadata. Only the explicit SCPI readback labels it in divisions.
        for key, field in (("acq", "type"), ("depth", "depmem")):
            self.rows[key].update_actual(h["sample"].get(field, "—"), populate=True)
        trig = h.get("trig", {})
        items = trig.get("items", {})
        for key, value in (("tr_source", items.get("channel")), ("tr_edge", items.get("edge")), ("tr_level", items.get("level")),
                           ("tr_coupling", items.get("coupling")), ("tr_sweep", items.get("sweep", trig.get("sweep")))):
            if value is not None:
                self.rows[key].update_actual(value, populate=True)
        self.render_scope()
        self.render_math()
        if self.tabs.currentIndex() == 4:
            self.render_spectrum()
        self.update_host_measurements()
        self.metadata_view.setPlainText(json.dumps(dict(header=frame.header, metadata=frame.metadata), indent=2))
        if frame.metadata.get("replay"):
            info = "Saved capture • replay • " + frame.metadata.get("original_source", "")
        elif self.backend == "Demo":
            info = "DEMO • synthetic data • software measurements, math and spectrum are active"
        else:
            info = "USB screen export • 300 screen positions • elapsed screen time • probe-tip volts • "
            info += "repeat-verified stopped pair" if frame.metadata.get("frozen_repeat_verified") else "sequential channel reads"
            if frame.metadata.get("screen_envelope"):
                info += " • min/max envelopes: midpoint math and FFT are estimates"
            if frame.metadata.get("clipped"):
                info += " • CLIPPED: increase hardware volts/div"
        self.notice.setText(info)
        self.update_age()
        if self.record_dir:
            try:
                self.record_count += 1
                save_capture(self.record_dir/f"frame_{self.record_count:07d}.npz", frame)
                self.scope_record.setText(f"Recording · {self.record_count}")
            except OSError as exc:
                self.stop_recording()
                self.show_error("Recording stopped: " + str(exc))

    def render_scope(self):
        if self.latest is None:
            return
        frame = self.latest
        xy = self.view_mode.currentIndex() == 1
        self.scope_plot.setLabel("bottom", "CH1 probe-tip voltage" if xy else "Elapsed screen time", units="V" if xy else "s")
        self.scope_plot.setLabel("left", "CH2 probe-tip voltage" if xy else "Probe-tip voltage", units="V")
        for i, curve in enumerate(self.scope_curves):
            visible = self.show_channels[i].isChecked() and np.any(np.isfinite(frame.channels[i]))
            curve.setData(frame.channels[0] if xy else frame.t, frame.channels[1] if xy else frame.channels[i])
            curve.setVisible(visible and (not xy or i == 0))
            a, b, fill = self.envelope_curves[i]
            show_env = visible and not xy and f"CH{i+1}" in frame.envelopes
            if show_env:
                lo, hi = frame.envelopes[f"CH{i+1}"]
                a.setData(frame.t, lo)
                b.setData(frame.t, hi)
            for item in (a, b, fill):
                item.setVisible(show_env)
        self.ref_curve.setVisible(self.reference is not None and not xy)
        self.update_trigger_labels()
        self.set_cursors(self.cursor_toggle.isChecked())

    def update_trigger_labels(self):
        if self.latest is None:
            self.trigger_status.setText("TRIGGER · waiting for waveform")
            self.trigger_line.hide()
            return
        info = trigger_readout(self.latest.header)
        level = engineering(info["level_v"], "V")
        kind = " · saved" if self.latest.metadata.get("replay") else " · cached" if self.backend is None else ""
        self.trigger_status.setText(f"TRIGGER{kind}   {info['source']}   {info['edge']}   |   {info['mode']}   |   Level {level}   |   {info['coupling']}   |   {info['status']}")
        show = self.view_mode.currentIndex() == 0 and info["level_v"] is not None and info["source"] in ("CH1", "CH2")
        self.trigger_line.setVisible(show)
        if show:
            color = CHANNEL_COLORS[int(info["source"][-1])-1]
            self.trigger_line.setPen(pg.mkPen(color, width=1.2, style=Qt.DashLine))
            self.trigger_line.label.setColor(color)
            self.trigger_line.setValue(info["level_v"])
            self.trigger_line.label.setFormat(f"T {info['source']} {info['edge']} · {level}")

    def set_cursors(self, checked):
        for line in self.cursor_lines:
            line.setVisible(checked and self.view_mode.currentIndex() == 0)
        self.update_cursors()

    def update_cursors(self):
        if not self.cursor_toggle.isChecked() or self.view_mode.currentIndex():
            self.cursor_readout.setText("Drag to pan · wheel to zoom · right-click for plot options")
            return
        x1, x2, y1, y2 = [line.value() for line in self.cursor_lines]
        dt = x2-x1
        text = f"Δt {engineering(dt, 's')}   1/|Δt| {engineering(1/abs(dt) if dt else np.nan, 'Hz')}   ΔV {engineering(y2-y1, 'V')}"
        if self.latest is not None:
            for i, y in enumerate(self.latest.channels):
                if np.all(np.isfinite(y)) and self.latest.t[0] <= x1 <= self.latest.t[-1]:
                    text += f"   CH{i+1}(T1) {engineering(np.interp(x1, self.latest.t, y), 'V')}"
        self.cursor_readout.setText(text)

    def set_reference(self):
        if self.latest is not None:
            self.reference = (self.latest.t.copy(), self.latest.channels[0].copy())
            self.ref_curve.setData(*self.reference)
            self.render_scope()

    def clear_reference(self):
        self.reference = None
        self.ref_curve.clear()

    def update_host_measurements(self):
        stats = []
        for y in self.latest.channels:
            try:
                stats.append(statistics(self.latest.t, y))
            except ValueError:
                stats.append({})
        rows = []
        for key in ("Vpp", "RMS", "AC_RMS", "Mean", "Min", "Max", "Frequency", "Period", "Duty", "Crest", "Std"):
            unit = "Hz" if key == "Frequency" else "s" if key == "Period" else "%" if key == "Duty" else "" if key == "Crest" else "V"
            rows.append([key.replace("_", " ")] + [engineering(s.get(key), unit) for s in stats])
        fill_table(self.host_table, rows)

    def on_native(self, readings):
        fill_table(self.native_table, list(readings.items()))

    def choose_math(self, text):
        if text != "Custom expression":
            self.math_expression.setText(text)
            self.render_math()

    def render_math(self):
        if self.latest is None:
            return
        self.math_trace = None
        try:
            self.math_trace = MathEngine().evaluate(self.math_expression.text(), self.latest.t, *self.latest.channels)
            self.math_curve.setData(self.latest.t, self.math_trace)
            self.math_plot.setLabel("left", "Math", units=self.math_unit.text())
            stats = statistics(self.latest.t, self.math_trace)
            self.math_status.setText(f"{self.math_expression.text()}    |    min {engineering(stats['Min'])}    max {engineering(stats['Max'])}    RMS {engineering(stats['RMS'])}    ·    {self.math_unit.text()} (user-defined unit)")
        except (ValueError, SyntaxError, TypeError, OverflowError) as exc:
            self.math_curve.clear()
            self.math_status.setText("Expression: " + str(exc))

    def reset_spectrum(self, *_):
        self.spectrum_hold = self.spectrum_average = self.spectrum_key = None
        self.waterfall_rows.clear()
        self.spectrum_hold_curve.clear()
        self.waterfall.clear()
        self.render_spectrum(advance=True)

    def render_spectrum(self, advance=True):
        if self.latest is None:
            return
        source = self.spec_source.currentText()
        y = self.math_trace if source == "MATH" else self.latest.channels[int(source[-1])-1]
        try:
            if y is None:
                raise ValueError("Evaluate a valid math expression first")
            result = spectrum(self.latest.t, y, self.spec_window.currentText(), self.spec_dc.isChecked())
            unit = self.spec_units.currentText()
            key = (len(y), result.bin_hz, source, unit, self.spec_window.currentText(), self.math_expression.text() if source == "MATH" else "")
            if key != self.spectrum_key:
                self.spectrum_hold = self.spectrum_average = None
                self.waterfall_rows.clear()
                self.spectrum_key = key
            if self.spec_average.isChecked():
                if self.spectrum_average is None:
                    self.spectrum_average = (result.rms**2, result.psd.copy())
                elif advance:
                    a, b = self.spectrum_average
                    self.spectrum_average = (.85*a+.15*result.rms**2, .85*b+.15*result.psd)
                a, b = self.spectrum_average
                result = replace(result, rms=np.sqrt(a), psd=b)
            values = spectrum_values(result, unit, self.spec_resistance.value())
            start = 1 if self.spec_log.isChecked() else 0
            self.spectrum_plot.setLogMode(x=self.spec_log.isChecked())
            self.spectrum_curve.setData(result.frequency[start:], values[start:])
            self.spectrum_plot.setLabel("left", "Math spectrum" if source == "MATH" else "Spectrum", units=unit)
            if self.spec_max.isChecked():
                self.spectrum_hold = values.copy() if self.spectrum_hold is None else np.maximum(values, self.spectrum_hold) if advance else self.spectrum_hold
                self.spectrum_hold_curve.setData(result.frequency[start:], self.spectrum_hold[start:])
            else:
                self.spectrum_hold_curve.clear()
            self.spec_metrics.setText(f"Peak {engineering(result.peak_hz, 'Hz')}    Δf {engineering(result.bin_hz, 'Hz')}    Nyquist {engineering(result.nyquist_hz, 'Hz')}")
            qualifier = "Screen-midpoint estimate" if self.latest.metadata.get("screen_envelope") else "Software estimate"
            source_units = "Math output unit is assumed to be volts for dBV/dBm/PSD." if source == "MATH" else "Probe-tip volts."
            self.spec_note.setText(f"{qualifier} · ENBW {engineering(result.enbw_hz, 'Hz')} · THD {engineering(result.thd_percent, '%')} ({result.harmonic_count} resolved harmonics, dominant peak assumed fundamental). "
                                  f"{source_units} Export-grid Nyquist applies; screen decimation may alias high-frequency content. dBm assumes {self.spec_resistance.value():g} Ω.")
            if advance:
                self.waterfall_rows.append(20*np.log10(np.maximum(result.rms, 1e-15)))
                image = np.array(self.waterfall_rows)
                self.waterfall.setImage(image, autoLevels=False, levels=(-100, 20))
                self.waterfall.setRect(QRectF(0, 0, result.nyquist_hz, len(image)))
        except (ValueError, TypeError) as exc:
            self.spectrum_curve.clear()
            self.spectrum_hold_curve.clear()
            self.spec_metrics.setText("Spectrum unavailable")
            self.spec_note.setText(str(exc))

    def render_generator(self, *_):
        try:
            get = lambda key: self.rows[key].text()
            freq = quantity(get("gen_freq"), "Hz")
            if freq <= 0:
                return
            t = np.linspace(0, 3/freq, 1200)
            shape = get("gen_wave")
            duty = self.generator_controls.get("gen_duty")/100 if shape.upper() == "PULSE" else .5
            symmetry = self.generator_controls.get("gen_sym")/100 if shape.upper() == "RAMP" else .5
            waveform = generator_wave(shape, 2*np.pi*freq*t, duty, symmetry)
            y = self.generator_controls.get("gen_offset") + self.generator_controls.get("gen_amp")/2*waveform
            self.generator_curve.setData(t, y)
            self.generator_plot.setTitle("WAVEFORM PREVIEW · EDITED SETTINGS, NOT A MEASUREMENT")
            resistance = None if self.preview_load_mode.currentIndex() == 0 else self.preview_load_ohm.value()
            factor = load_factor(resistance)
            self.generator_load_curve.setData(t, y*factor)
            self.generator_load_curve.setVisible(resistance is not None)
            self.preview_load_ohm.setEnabled(resistance is not None)
            self.load_preview_note.setText("Local preview only · high impedance" if resistance is None else
                                          f"Local preview only · {resistance:g} Ω load / 50 Ω source · divider factor {factor:.4g}; no device setting is changed")
            self.generator_plot.autoRange()
        except (ValueError, KeyError):
            self.generator_curve.clear()
            self.generator_load_curve.clear()
            self.generator_plot.setTitle("WAVEFORM PREVIEW · READ THE REQUIRED DEVICE PARAMETERS")
        if hasattr(self, "modulation_editor"):
            self.modulation_editor.redraw()

    def generator_carrier_values(self):
        controls = self.generator_controls
        return dict(shape=self.rows["gen_wave"].text(), carrier_hz=controls.get("gen_freq"),
                    amplitude=controls.get("gen_amp"), offset=controls.get("gen_offset"),
                    duty=controls.get("gen_duty")/100, symmetry=controls.get("gen_sym")/100)

    def refresh_generator(self):
        if self.backend is None:
            self.show_error("Connect the scope before loading generator parameters")
            return
        if self.generator_refresh_pending:
            return
        self.generator_refresh_pending = True
        self.generator_loaded = False
        self.generator_sync_status.setText("Loading current output state, waveform, frequency, amplitude, offset and device load flag…")
        self.update_generator_controls()
        self.worker.submit("generator_snapshot")

    def reload_generator_signal(self):
        for key in ("gen_amp", "gen_offset", "gen_high", "gen_low", "gen_freq", "gen_period"):
            self.rows[key].dirty = False
        self.refresh_generator()

    def reset_generator_controls(self, clear=False):
        self.generator_loaded = False
        self.generator_baseline = None
        self.generator_refresh_pending = False
        self.generator_read_fields.clear()
        for key, row in self.rows.items():
            if key.startswith("gen_") and clear:
                row.edit.blockSignals(True)
                if not row.dirty:
                    if isinstance(row.edit, QComboBox):
                        row.edit.setCurrentText(row.spec.default)
                    else:
                        row.edit.setText(row.spec.default)
                row.edit.blockSignals(False)
                row.actual.setText("not loaded")
        self.generator_sync_status.setText("Selection and local previews are available. Load device settings before applying to hardware; draft values are not device readback.")
        self.sync_waveform_selector()
        self.generator_controls.sync()
        self.update_generator_controls()
        self.render_generator()

    def update_generator_controls(self):
        connected = self.backend is not None
        ready = connected and self.generator_loaded
        output_on = self.generator_baseline.output_on if self.generator_baseline else None
        pending_edits = any(row.dirty for key, row in self.rows.items() if key.startswith("gen_"))
        self.enable_output.setEnabled(ready and not output_on and not pending_edits)
        self.enable_output.setToolTip("Apply or query pending edits before enabling the device's current output" if pending_edits else "Enable output using the current device settings")
        self.disable_output.setEnabled(connected)
        self.waveform_editor.use_device.setEnabled(ready)
        self.apply_waveform.setEnabled(ready and self.generator_views.currentIndex() == 0)
        self.apply_waveform.setText("Apply waveform" if self.generator_views.currentIndex() == 0 else "Local modulation design" if self.generator_views.currentIndex() == 2 else "USB upload unavailable")
        reason = "Load device settings before applying" if not ready else "Apply explicitly · output ON, changes are live" if output_on else "Apply selected setting to the device"
        self.apply_waveform.setToolTip(reason)
        if self.generator_views.currentIndex() == 1:
            self.apply_waveform.setToolTip("Custom cycles can be drawn, imported, saved and exported. No device upload command has been verified.")
        for key, row in self.rows.items():
            if key.startswith("gen_"):
                row.edit.setEnabled(True)
                row.read.setEnabled(connected)
                read_or_edited = key in self.generator_read_fields or (row.dirty and key in ("gen_sym", "gen_rise", "gen_fall"))
                required_shape = "RAMP" if key == "gen_sym" else "PULSE" if key in ("gen_rise", "gen_fall", "gen_width", "gen_duty") else None
                matches_shape = not required_shape or self.generator_baseline is not None and self.generator_baseline.values["gen_wave"].upper() == required_shape
                row.apply_button.setEnabled(ready and read_or_edited and matches_shape)
                row.apply_button.setToolTip(reason if not ready else "Read this setting with ↻ first" if key not in self.generator_read_fields else reason)
                if required_shape and not matches_shape:
                    row.apply_button.setToolTip("Apply the " + required_shape + " waveform first")
                elif ready and read_or_edited:
                    row.apply_button.setToolTip(reason)
        actual_shape = self.generator_baseline.values["gen_wave"] if self.generator_baseline else ""
        self.generator_controls.update_state(ready, self.rows["gen_wave"].text(), actual_shape)

    def on_generator_committed(self, group, revision):
        self.generator_controls.committed(group, revision)
        self.update_generator_controls()

    def on_generator_state(self, state):
        self.generator_refresh_pending = False
        self.generator_loaded = state is not None and self.backend is not None
        self.generator_baseline = state if self.generator_loaded else None
        if self.generator_loaded:
            for key, value in state.values.items():
                row = self.rows[key]
                row.update_actual(value, populate=True)
                row.actual.setToolTip("Raw device reply: " + state.raw[key])
                self.generator_read_fields.add(key)
            self.output_badge.setText("OUTPUT · " + ("ON" if state.output_on else "OFF"))
            self.sync_waveform_selector()
            if self.generator_views.currentIndex() == 0:
                self.waveform_selection_note.setText("Device waveform: " + state.raw["gen_wave"] + " · selection changes remain local until Apply waveform")
            self.generator_sync_status.setText("Device settings loaded · " + ("OUTPUT ON: Apply changes live; output stays on" if state.output_on else "output OFF: Apply configures settings without enabling output") + ". Readback follows each Apply.")
        else:
            self.generator_sync_status.setText("Device settings could not be loaded. You can still choose waveforms and edit locally; use Load device settings to retry hardware readback.")
        self.generator_controls.sync()
        self.update_generator_controls()
        self.render_generator()

    def copy_generator_to_editor(self):
        if self.generator_baseline is None:
            return
        values = self.generator_baseline.values
        self.waveform_editor.amplitude.setValue(quantity(values["gen_amp"], "V"))
        self.waveform_editor.offset.setValue(quantity(values["gen_offset"], "V"))
        self.waveform_editor.redraw()

    def set_meter_function(self):
        choice = self.meter_function.currentText()
        commands = {"DC voltage": ":DMM:CONFigure:VOLTage DC", "AC voltage": ":DMM:CONFigure:VOLTage AC",
                    "DC current": ":DMM:CONFigure:CURRent DC", "AC current": ":DMM:CONFigure:CURRent AC",
                    "Resistance": ":DMM:CONFigure RESistance", "Capacitance": ":DMM:CONFigure CAPacitance",
                    "Diode": ":DMM:CONFigure DIODe", "Continuity": ":DMM:CONFigure CONTinuity"}
        self.send_command("dmm_function", commands[choice])

    def receive_reading(self, reading):
        # Unitless values are resolved from the worker's contemporaneous scope
        # function/range snapshot, never from the unapplied function dropdown.
        measured = decode_reading(reading)
        value, unit = measured.value, measured.unit
        if reading.get("function"):
            self.dmm_function_text = measured.function
            if measured.function == self.meter_function.currentText():
                self.meter_function_dirty = False
            if not self.meter_function_dirty:
                index = self.meter_function.findText(measured.function)
                if index >= 0:
                    self.meter_function.setCurrentIndex(index)
        if reading.get("range"):
            self.dmm_range_text = str(reading["range"])
            self.rows["dmm_range"].update_actual(self.dmm_range_text)
        key = (measured.function, unit, self.dmm_rel_text)
        if (value is not None or reading.get("function")) and key != self.dmm_measurement_key:
            self.clear_meter()
            self.dmm_unit = unit
            self.dmm_measurement_key = key
        if value is not None:
            self.dmm_history.append((reading["host_time"], value))
        if self.reading_log:
            try:
                self.reading_log.append(reading, value if value is not None else "", unit,
                                        self.dmm_function_text, self.dmm_range_text, self.dmm_rel_text)
            except OSError as exc:
                self.stop_meter_log()
                self.show_error("DMM logging stopped: " + str(exc))
        if self.meter_hold.isChecked():
            return
        self.meter_value.setToolTip("Original instrument response: " + reading["text"])
        self.meter_value.setText(self.meter_value.fontMetrics().elidedText(measured.display, Qt.ElideRight, max(80, self.meter_value.width()-10)))
        if value is None:
            self.meter_state.setText(measured.status)
            return
        self.meter_state.setText(f"{self.dmm_function_text} · range {self.dmm_range_text} · relative {self.dmm_rel_text} · {datetime.fromtimestamp(reading['host_time']).strftime('%H:%M:%S')}")
        data = np.array(self.dmm_history)
        self.meter_curve.setData(data[:, 0]-data[0, 0], data[:, 1])
        self.meter_plot.setLabel("left", "Reading", units=unit)
        for name, stat in (("MIN", data[:,1].min()), ("MAX", data[:,1].max()), ("MEAN", data[:,1].mean()), ("σ", data[:,1].std())):
            widget = self.meter_stat_values[name]
            text = engineering(stat, unit, precision=6)
            widget.setToolTip(text)
            widget.setText(widget.fontMetrics().elidedText(text, Qt.ElideRight, max(50, widget.width()-4)))

    def clear_meter(self):
        self.dmm_history.clear()
        self.dmm_unit = None
        self.dmm_measurement_key = None
        self.meter_curve.clear()
        for widget in self.meter_stat_values.values():
            widget.setText("—")
            widget.setToolTip("")

    def toggle_meter_log(self, checked):
        if not checked:
            self.stop_meter_log()
            return
        path, _ = QFileDialog.getSaveFileName(self, "New multimeter CSV log", "multimeter_"+datetime.now().strftime("%Y%m%d_%H%M%S")+".csv", "CSV (*.csv)")
        if not path:
            self.log_button.setChecked(False)
            return
        try:
            self.reading_log = ReadingLog(path)
            self.log_button.setText("Logging CSV…")
        except OSError as exc:
            self.log_button.setChecked(False)
            self.show_error(str(exc))

    def stop_meter_log(self):
        if self.reading_log:
            self.reading_log.close()
            self.reading_log = None
        self.log_button.setChecked(False)
        self.log_button.setText("Log CSV")

    def toggle_recording(self, checked):
        if not checked:
            self.stop_recording()
            return
        directory = QFileDialog.getExistingDirectory(self, "Choose parent folder for a new frame recording")
        if not directory:
            self.scope_record.setChecked(False)
            return
        try:
            self.record_dir = Path(directory)/("hds_recording_"+datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            self.record_dir.mkdir()
            self.record_count = 0
            self.scope_record.setText("Recording · 0")
        except OSError as exc:
            self.record_dir = None
            self.scope_record.setChecked(False)
            self.show_error(str(exc))

    def stop_recording(self):
        self.record_dir = None
        self.scope_record.setChecked(False)
        self.scope_record.setText("Record frames")

    def export_capture(self):
        if self.latest is None:
            self.show_error("Capture a waveform before exporting")
            return
        path, selected = QFileDialog.getSaveFileName(self, "Export waveform and provenance", "capture_"+datetime.now().strftime("%Y%m%d_%H%M%S")+".npz", "Compressed capture (*.npz);;CSV + metadata (*.csv)")
        if path:
            try:
                if not Path(path).suffix:
                    path += ".csv" if "CSV" in selected else ".npz"
                save_capture(path, self.latest, self.math_trace, self.math_expression.text())
                self.statusBar().showMessage("Saved " + path, 10000)
            except (ValueError, OSError) as exc:
                self.show_error(str(exc))

    def open_capture(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open saved capture", "", "HDS capture (*.npz)")
        if path:
            try:
                frame = load_capture(path)
                self.live_button.setChecked(False)
                self.toggle_live(False)
                self.replaying = True
                self.stop_recording()
                self.receive_frame(frame)
            except (ValueError, KeyError, OSError) as exc:
                self.show_error("Cannot open capture: " + str(exc))

    def screenshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save workbench screenshot", "hds_studio.png", "PNG (*.png)")
        if path and not self.grab().save(path, "PNG"):
            self.show_error("Could not save screenshot")

    def console_send(self):
        self.send_command("console", self.command_entry.text())

    def save_trace(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save SCPI trace", "hds_scpi.log", "Log (*.log)")
        if path:
            try:
                Path(path).write_text(self.console.toPlainText(), encoding="utf8")
            except OSError as exc:
                self.show_error(str(exc))

    def save_preset(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save workspace controls", "hds_workspace.json", "JSON (*.json)")
        if path:
            document = dict(format="hdsstudio-workspace-v1", controls={key: row.text() for key, row in self.rows.items() if row.text().strip()},
                            generator_modes=dict(frequency=self.generator_controls.frequency_mode.currentIndex(),
                                                 levels=self.generator_controls.voltage_mode.currentIndex(), pulse=self.generator_controls.pulse_mode.currentIndex()),
                            generator_adjustments=self.generator_controls.export_preferences(),
                            theme=self.theme.currentText(), math_expression=self.math_expression.text(), math_unit=self.math_unit.text(),
                            spectrum=dict(source=self.spec_source.currentText(), window=self.spec_window.currentText(),
                                          unit=self.spec_units.currentText(), resistance=self.spec_resistance.value(), dc=self.spec_dc.isChecked()))
            try:
                Path(path).write_text(json.dumps(document, indent=2), encoding="utf8")
                self.statusBar().showMessage("Workspace preset saved", 8000)
            except OSError as exc:
                self.show_error(str(exc))

    def load_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load workspace controls", "", "JSON (*.json)")
        if path:
            try:
                doc = json.loads(Path(path).read_text(encoding="utf8"))
                if doc.get("format") != "hdsstudio-workspace-v1" or not isinstance(doc.get("controls"), dict):
                    raise ValueError("Not an HDS Studio workspace preset")
                for key, value in doc["controls"].items():
                    if key in BY_KEY:
                        BY_KEY[key].write(value)
                for key, value in doc["controls"].items():
                    if key in self.rows:
                        row = self.rows[key]
                        row.edit.blockSignals(True)
                        if isinstance(row.edit, QComboBox):
                            row.edit.setCurrentText(value)
                        else:
                            row.edit.setText(value)
                        row.edit.blockSignals(False)
                        row.dirty = True
                modes = doc.get("generator_modes", {})
                for name, widget in (("frequency", self.generator_controls.frequency_mode), ("levels", self.generator_controls.voltage_mode), ("pulse", self.generator_controls.pulse_mode)):
                    widget.blockSignals(True)
                    widget.setCurrentIndex(1 if modes.get(name) == 1 else 0)
                    widget.blockSignals(False)
                for key in ("gen_period" if self.generator_controls.frequency_mode.currentIndex() else "gen_freq",
                            "gen_high" if self.generator_controls.voltage_mode.currentIndex() else "gen_amp",
                            "gen_width" if self.generator_controls.pulse_mode.currentIndex() else "gen_duty"):
                    self.generator_controls.edited(key)
                self.generator_controls.sync()
                self.sync_waveform_selector()
                self.update_generator_controls()
                self.generator_controls.restore_preferences(doc.get("generator_adjustments", {}))
                if doc.get("theme") in THEMES:
                    self.theme.setCurrentText(doc["theme"])
                self.math_expression.setText(str(doc.get("math_expression", "CH1 - CH2")))
                self.math_unit.setText(str(doc.get("math_unit", "V")))
                spec = doc.get("spectrum", {})
                for field, widget in (("source", self.spec_source), ("window", self.spec_window), ("unit", self.spec_units)):
                    if spec.get(field) in [widget.itemText(i) for i in range(widget.count())]:
                        widget.setCurrentText(spec[field])
                self.spec_resistance.setValue(float(spec.get("resistance", 50)))
                self.spec_dc.setChecked(bool(spec.get("dc", True)))
                self.render_math()
                self.render_generator()
                self.notice.setText("Workspace loaded into controls. Review and apply individual hardware settings; no instrument settings were sent.")
            except (ValueError, TypeError, AttributeError, OSError) as exc:
                self.show_error("Cannot load preset: " + str(exc))

    def update_age(self):
        if self.last_frame_time is None:
            self.age.setText("No waveform capture")
            return
        age = time.monotonic()-self.last_frame_time
        fps = (len(self.fps_times)-1)/(self.fps_times[-1]-self.fps_times[0]) if len(self.fps_times)>1 and self.fps_times[-1]>self.fps_times[0] else 0
        qualifier = "Replay" if self.latest and self.latest.metadata.get("replay") else "Frame"
        self.age.setText(f"{qualifier} {self.frame_count} · {fps:.1f} refresh/s · age {age:.1f}s")

    def apply_theme(self, name):
        self.setStyleSheet(stylesheet(name))
        c = THEMES[name]
        for plot in self.plots:
            plot.setBackground(c["plot"])
            plot.getPlotItem().titleLabel.setText(plot.getPlotItem().titleLabel.text, color=c["muted"], size="11pt")
            for axis in ("left", "bottom"):
                plot.getAxis(axis).setTextPen(c["muted"])
                plot.getAxis(axis).setPen(c["border"])
                axis_item = plot.getAxis(axis)
                axis_item.setLabel(axis_item.labelText, units=axis_item.labelUnits,
                                   **{**axis_item.labelStyle, "color": c["muted"]})
            plot.getPlotItem().legend.setLabelTextColor(c["text"])
        if self.settings:
            self.settings.setValue("theme", name)

    def closeEvent(self, event):
        if self.worker.isRunning():
            event.ignore()
            if not self.closing:
                self.closing = True
                self.stop_recording()
                self.stop_meter_log()
                self.worker.requestInterruption()
                self.statusBar().showMessage("Releasing USB interface…")
            return
        if self.settings:
            self.settings.setValue("geometry", self.saveGeometry())
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "mode_notification"):
            self.mode_notification.place()

    def worker_finished(self):
        if self.closing:
            self.close()
