"""Waveform-specific controls with linked, explicitly applied draft values."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                              QScrollArea, QTabWidget, QVBoxLayout, QWidget)
from .commands import BY_KEY, engineering, quantity
from .generator import levels_pair, voltage_pair
from .generator_profiles import configurations, max_frequency, profile_note
from .parameter_stepper import ParameterStepper


GROUPS = {"levels": ("gen_amp", "gen_offset", "gen_high", "gen_low"),
          "frequency": ("gen_freq", "gen_period"), "pulse": ("gen_width", "gen_duty")}


def value(key, text):
    # Use the same unit parsing and bounds as the wire command, including Vpp.
    if key in ("gen_duty", "gen_sym"):
        text = text.strip().rstrip("%")
    canonical = BY_KEY[key].write(text).split(" ", 1)[1]
    return quantity(canonical, BY_KEY[key].unit)


class GeneratorControls(QTabWidget):
    apply_group = Signal(str, int, object)
    changed = Signal()
    waveform_requested = Signal(str, str)

    def __init__(self, rows, read_settings):
        super().__init__()
        self.rows = rows
        self.revision = dict.fromkeys(GROUPS, 0)
        self.pending = {}
        self.ready = False
        self.shape = "SINE"
        self.configuration_cache = {}
        self.status = QLabel("Draft controls · load device settings before applying")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setObjectName("notice")
        signal_page = QWidget()
        signal_box = QVBoxLayout(signal_page)
        self.shape_info = QLabel()
        self.shape_info.setWordWrap(True)
        signal_box.addWidget(self.shape_info)
        self.configuration = QComboBox()
        signal_box.addWidget(QLabel("Waveform configuration"))
        signal_box.addWidget(self.configuration)
        self.configuration_note = QLabel()
        self.configuration_note.setWordWrap(True)
        signal_box.addWidget(self.configuration_note)
        self.frequency_mode, frequency_box = self.section(signal_box, "Frequency / period", ("Frequency", "Period"))
        self.frequency_summary = self.add_fields(frequency_box, GROUPS["frequency"])
        self.frequency_apply = self.action(frequency_box, "Apply frequency", lambda: self.apply("frequency"))
        self.voltage_mode, voltage_box = self.section(signal_box, "Output levels", ("Amplitude / offset", "High / low"))
        self.voltage_summary = self.add_fields(voltage_box, GROUPS["levels"])
        self.voltage_apply = self.action(voltage_box, "Apply voltage levels", lambda: self.apply("levels"))
        signal_box.addWidget(self.status)
        signal_box.addWidget(self.action(None, "Reload signal values (discard drafts)", read_settings))
        signal_box.addStretch()
        self.addTab(signal_page, "Signal")
        timing = QWidget()
        timing_box = QVBoxLayout(timing)
        self.timing_configuration = QComboBox()
        timing_box.addWidget(QLabel("Waveform configuration"))
        timing_box.addWidget(self.timing_configuration)
        self.pulse_box = QGroupBox("Pulse width / duty")
        pulse_layout = QVBoxLayout(self.pulse_box)
        self.pulse_mode = QComboBox()
        self.pulse_mode.addItems(("Duty cycle (%)", "Pulse width (s)"))
        pulse_layout.addWidget(self.pulse_mode)
        self.pulse_summary = self.add_fields(pulse_layout, GROUPS["pulse"])
        self.pulse_apply = self.action(pulse_layout, "Apply pulse timing", lambda: self.apply("pulse"))
        self.action(pulse_layout, "Read pulse timing", self.read_pulse)
        timing_box.addWidget(self.pulse_box)
        self.edge_box = QGroupBox("Pulse edge timing")
        edge_layout = QVBoxLayout(self.edge_box)
        for key in ("gen_rise", "gen_fall"):
            edge_layout.addWidget(rows[key])
        timing_box.addWidget(self.edge_box)
        self.ramp_box = QGroupBox("Ramp symmetry")
        ramp_layout = QVBoxLayout(self.ramp_box)
        ramp_layout.addWidget(rows["gen_sym"])
        timing_box.addWidget(self.ramp_box)
        self.timing_status = QLabel()
        self.timing_status.setWordWrap(True)
        timing_box.addWidget(self.timing_status)
        timing_box.addStretch()
        self.addTab(timing, "Timing")
        load_page = QWidget()
        load_box = QVBoxLayout(load_page)
        load_box.addWidget(rows["gen_load"])
        note = QLabel("Device load ON/OFF with readback. Physical source: 50 Ω. Numeric hardware load reference is set on the scope; the preview load remains local.")
        note.setWordWrap(True)
        load_box.addWidget(note)
        load_box.addStretch()
        self.addTab(load_page, "Load")
        for group, keys in GROUPS.items():
            for key in keys:
                rows[key].edited.connect(lambda k=key: self.edited(k))
                # Enter applies the complete pair, instead of a hidden single-field write.
                rows[key].edit.returnPressed.disconnect()
                rows[key].edit.returnPressed.connect(lambda g=group: self.apply(g))
        for mode in (self.frequency_mode, self.voltage_mode, self.pulse_mode):
            mode.currentIndexChanged.connect(self.mode_changed)
        for selector in (self.configuration, self.timing_configuration):
            selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            selector.setMinimumContentsLength(15)
            selector.activated.connect(self.configure)
        self.steppers = {}
        for key in ("gen_freq", "gen_period", *GROUPS["levels"], *GROUPS["pulse"], "gen_rise", "gen_fall", "gen_sym"):
            stepper = ParameterStepper(key, rows[key], self.get, self.step_limits)
            stepper.error.connect(self.show_message)
            self.steppers[key] = stepper
        self.update_configurations()
        self.sync()

    def show_message(self, text):
        self.status.setText(text)
        self.timing_status.setText(text)

    def step_limits(self, key):
        low, high = BY_KEY[key].limits
        if key == "gen_freq":
            high = max_frequency(self.shape)
        elif key == "gen_period":
            low = 1/max_frequency(self.shape)
        elif key in ("gen_width", "gen_rise", "gen_fall"):
            period = 1/self.get("gen_freq")
            if key == "gen_width":
                low, high = max(low, .001*period), min(high, .999*period)
            else:
                high = min(high, period)
        elif key == "gen_amp":
            high = min(high, 2*(2.5-abs(self.get("gen_offset"))))
        elif key == "gen_offset":
            high = 2.5-self.get("gen_amp")/2
            low = -high
        elif key == "gen_high":
            low = max(low, self.get("gen_low")+.02)
            high = min(high, self.get("gen_low")+5)
        elif key == "gen_low":
            low = max(low, self.get("gen_high")-5)
            high = min(high, self.get("gen_high")-.02)
        return low, high

    def update_configurations(self):
        choices = configurations(self.shape)
        selected = self.configuration_cache.get(self.shape, choices[0].key)
        if selected not in {c.key for c in choices}:
            selected = choices[0].key
        self.configuration_cache[self.shape] = selected
        for selector in (self.configuration, self.timing_configuration):
            selector.blockSignals(True)
            selector.clear()
            for choice in choices:
                selector.addItem(choice.label, choice.key)
            selector.setCurrentIndex(next(i for i, c in enumerate(choices) if c.key == selected))
            selector.blockSignals(False)
        self.configuration_note.setText(profile_note(self.shape))

    def configure(self, index):
        choice = configurations(self.shape)[index]
        if self.shape == "SQUARE" and choice.key == "pwm":
            self.waveform_requested.emit("PULSe", "pwm")
            return
        self.configuration_cache[self.shape] = choice.key
        self.update_configurations()
        if choice.key in ("frequency", "period"):
            self.frequency_mode.setCurrentIndex(int(choice.key == "period"))
        if self.shape == "PULSE" and choice.key in ("pwm", "width"):
            self.pulse_mode.setCurrentIndex(int(choice.key == "width"))
        if choice.symmetry is not None:
            self.put("gen_sym", choice.symmetry)
            self.rows["gen_sym"].mark_dirty()
        self.setCurrentIndex(choice.tab)
        self.sync()
        self.changed.emit()
        self.show_message("Configuration prepared locally · Apply waveform / parameters to change the device")
        QTimer.singleShot(0, lambda key=choice.focus: self.focus_parameter(key))

    def focus_parameter(self, key):
        row = self.rows[key]
        if not row.isVisible():
            return
        row.edit.setFocus()
        ancestor = self.parentWidget()
        while ancestor:
            if isinstance(ancestor, QScrollArea):
                ancestor.ensureWidgetVisible(row)
                break
            ancestor = ancestor.parentWidget()

    def export_preferences(self):
        return dict(configurations=dict(self.configuration_cache), steps={k: s.step.currentText() for k, s in self.steppers.items()})

    def restore_preferences(self, document):
        if not isinstance(document, dict):
            return
        cached = document.get("configurations", {})
        if isinstance(cached, dict):
            for shape, selected in cached.items():
                try:
                    if selected in {c.key for c in configurations(shape)}:
                        self.configuration_cache[shape.upper()] = selected
                except (ValueError, AttributeError):
                    pass
        saved_steps = document.get("steps", {})
        if isinstance(saved_steps, dict):
            for key, text in saved_steps.items():
                if key in self.steppers and self.steppers[key].step.findText(str(text)) >= 0:
                    self.steppers[key].step.setCurrentText(str(text))
        self.update_configurations()

    @staticmethod
    def action(layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        if layout is not None:
            layout.addWidget(button)
        return button

    @staticmethod
    def section(layout, title, modes):
        box = QGroupBox(title)
        content = QVBoxLayout(box)
        selector = QComboBox()
        selector.addItems(modes)
        content.addWidget(selector)
        layout.addWidget(box)
        return selector, content

    def add_fields(self, layout, keys):
        for key in keys:
            row = self.rows[key]
            row.read.hide()
            row.apply_button.hide()
            row.edit.setPlaceholderText({"gen_amp": "e.g. 500 mVpp", "gen_freq": "e.g. 2.5 kHz",
                                         "gen_period": "e.g. 400 us", "gen_width": "e.g. 100 us"}.get(key, "SI value with optional unit"))
            layout.addWidget(row)
        summary = QLabel()
        summary.setWordWrap(True)
        summary.setObjectName("muted")
        layout.addWidget(summary)
        return summary

    def get(self, key):
        return value(key, self.rows[key].text())

    def put(self, key, number):
        edit = self.rows[key].edit
        edit.blockSignals(True)
        edit.setText(f"{number:.12g}" + BY_KEY[key].unit)
        edit.blockSignals(False)

    def mode_changed(self, *_):
        if self.shape == "PULSE" and self.sender() is self.pulse_mode:
            self.configuration_cache[self.shape] = "width" if self.pulse_mode.currentIndex() else "pwm"
            self.update_configurations()
        elif self.shape not in ("RAMP", "PULSE", "SQUARE") and self.sender() is self.frequency_mode:
            self.configuration_cache[self.shape] = "period" if self.frequency_mode.currentIndex() else "frequency"
            self.update_configurations()
        self.sync()
        self.changed.emit()

    def edited(self, key):
        group = next(g for g, keys in GROUPS.items() if key in keys)
        self.revision[group] += 1
        for k in GROUPS[group]:
            self.rows[k].dirty = True
        try:
            if group == "levels":
                if key in ("gen_high", "gen_low"):
                    amp, offset = levels_pair(self.get("gen_high"), self.get("gen_low"))
                    self.put("gen_amp", amp)
                    self.put("gen_offset", offset)
                self.sync_levels()
            elif group == "frequency":
                if key == "gen_period":
                    self.put("gen_freq", 1/self.get("gen_period"))
                self.sync_frequency()
                self.sync_pulse()
            else:
                self.sync_pulse()
            self.status.setText("Draft updated · Apply sends this group to the device; output stays in its current state")
        except (ValueError, ZeroDivisionError) as exc:
            self.status.setText(str(exc))
        self.changed.emit()

    def sync_levels(self):
        from_levels = self.voltage_mode.currentIndex() == 1 and any(self.rows[k].dirty for k in ("gen_high", "gen_low"))
        if from_levels:
            high, low = self.get("gen_high"), self.get("gen_low")
            amp, offset = levels_pair(high, low)
            self.put("gen_amp", amp)
            self.put("gen_offset", offset)
        else:
            amp, offset = self.get("gen_amp"), self.get("gen_offset")
            high, low = voltage_pair(amp, offset)
            self.put("gen_high", high)
            self.put("gen_low", low)
        self.voltage_summary.setText(f"{engineering(amp, 'V')}pp · offset {engineering(offset, 'V')}\nHigh {engineering(high, 'V')} · Low {engineering(low, 'V')}")

    def sync_frequency(self):
        from_period = self.frequency_mode.currentIndex() == 1 and self.rows["gen_period"].dirty
        frequency = 1/self.get("gen_period") if from_period else self.get("gen_freq")
        self.put("gen_freq", frequency) if from_period else self.put("gen_period", 1/frequency)
        self.frequency_summary.setText(f"{engineering(frequency, 'Hz')} ↔ {engineering(1/frequency, 's')}")

    def sync_pulse(self):
        period = 1/self.get("gen_freq")
        if self.pulse_mode.currentIndex() == 0:
            self.put("gen_width", period*self.get("gen_duty")/100)
        else:
            self.put("gen_duty", 100*self.get("gen_width")/period)
        self.pulse_summary.setText(f"Width {engineering(self.get('gen_width'), 's')} · duty {self.get('gen_duty'):.6g}%\nPeriod {engineering(period, 's')}")

    def sync(self):
        for callback in (self.sync_frequency, self.sync_levels, self.sync_pulse):
            try:
                callback()
            except (ValueError, ZeroDivisionError) as exc:
                self.status.setText(str(exc))
        self.rows["gen_freq"].setVisible(self.frequency_mode.currentIndex() == 0)
        self.rows["gen_period"].setVisible(self.frequency_mode.currentIndex() == 1)
        for key in GROUPS["levels"]:
            self.rows[key].setVisible((key in ("gen_high", "gen_low")) == (self.voltage_mode.currentIndex() == 1))
        self.rows["gen_duty"].setVisible(self.pulse_mode.currentIndex() == 0)
        self.rows["gen_width"].setVisible(self.pulse_mode.currentIndex() == 1)

    def update_state(self, ready, shape, actual_shape):
        changed_shape = shape.upper() != self.shape
        self.ready, self.shape = ready, shape.upper()
        if changed_shape:
            self.update_configurations()
        pulse, ramp = self.shape == "PULSE", self.shape == "RAMP"
        self.pulse_box.setVisible(pulse)
        self.edge_box.setVisible(pulse)
        self.ramp_box.setVisible(ramp)
        self.setTabVisible(1, pulse or ramp)
        maximum = 25 if self.shape == "SINE" else 1 if ramp else 5
        extra = " · square duty fixed at 50%" if self.shape == "SQUARE" else ""
        self.shape_info.setText(f"{shape} · 0.1 Hz–{maximum} MHz{extra}\nEnter values such as 2.5 kHz, 200 us or 500 mVpp")
        for group, button in (("levels", self.voltage_apply), ("frequency", self.frequency_apply), ("pulse", self.pulse_apply)):
            enabled = ready and group not in self.pending
            if group == "pulse":
                enabled = enabled and pulse and actual_shape.upper() == "PULSE" and not any(self.rows[k].dirty for k in ("gen_wave", *GROUPS["frequency"]))
            button.setEnabled(enabled)
            button.setToolTip("Apply waveform and frequency first" if group == "pulse" and not enabled else "Apply this group explicitly; live output will change" if ready else "Load device settings first")

    def apply(self, group):
        button = {"levels": self.voltage_apply, "frequency": self.frequency_apply, "pulse": self.pulse_apply}[group]
        if not button.isEnabled():
            self.status.setText(button.toolTip())
            return
        try:
            if group == "levels":
                amp, offset = (levels_pair(self.get("gen_high"), self.get("gen_low")) if self.voltage_mode.currentIndex() else (self.get("gen_amp"), self.get("gen_offset")))
                voltage_pair(amp, offset)
                payload = dict(amplitude=amp, offset=offset)
            elif group == "frequency":
                payload = dict(frequency=1/self.get("gen_period") if self.frequency_mode.currentIndex() else self.get("gen_freq"))
            else:
                key = "gen_width" if self.pulse_mode.currentIndex() else "gen_duty"
                payload = dict(key=key, value=self.get(key))
            self.pending[group] = self.revision[group]
            button.setEnabled(False)
            self.status.setText("Applying " + group + " · awaiting device readback…")
            self.apply_group.emit(group, self.revision[group], payload)
        except (ValueError, ZeroDivisionError) as exc:
            self.status.setText(str(exc))
            {"levels": self.voltage_summary, "frequency": self.frequency_summary, "pulse": self.pulse_summary}[group].setText(str(exc))

    def read_pulse(self):
        if not self.ready:
            self.timing_status.setText("Load device settings first")
            return
        for key in GROUPS["pulse"]:
            self.rows[key].dirty = False
        self.rows["gen_width" if self.pulse_mode.currentIndex() else "gen_duty"].query()
        for key in ("gen_rise", "gen_fall"):
            self.rows[key].query()

    def committed(self, group, revision):
        self.pending.pop(group, None)
        if revision == self.revision[group]:
            for key in GROUPS[group]:
                self.rows[key].dirty = False
        self.status.setText("Device readback verified · " + group)
        self.timing_status.setText(self.status.text())

    def failed(self, message):
        if self.pending:
            self.pending.clear()
            self.status.setText(message)
            self.timing_status.setText(message)
