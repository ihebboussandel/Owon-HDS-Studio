"""All instrument I/O is serialized on a single Qt worker thread."""
import math
import queue
import re
import time
from PySide6.QtCore import QThread, Signal
from .commands import BASE_SCALES, BY_KEY, MEASUREMENTS, quantity, validate_command
from .device import DemoTransport, capture
from .transport import USBTransport
from .multimeter import read_multimeter
from .generator import CORE_KEYS, read_generator_state, same_state, validate_output_envelope, voltage_plan, wave_name
from .generator_profiles import max_frequency


class InstrumentWorker(QThread):
    frame = Signal(object)
    reading = Signal(object)
    connected = Signal(str, str)
    disconnected = Signal()
    reply = Signal(str, str)
    error = Signal(str)
    trace = Signal(str)
    native = Signal(object)
    generator_state = Signal(object)
    generator_committed = Signal(str, int)

    def __init__(self):
        super().__init__()
        self.jobs = queue.Queue(maxsize=128)
        self.transport = None
        self.identity = ""
        self.mode = "scope"
        self.live = True
        self.interval = .2
        self.next_poll = 0.
        self.generator_baseline = None

    def submit(self, kind, *args):
        try:
            self.jobs.put_nowait((kind, args))
        except queue.Full:
            self.error.emit("Instrument command queue is full. Wait for the current operations")

    def _close(self):
        self.generator_baseline = None
        if self.transport:
            try:
                self.transport.close()
            except Exception as exc:
                self.trace.emit("USB cleanup: " + str(exc))
            self.transport = None
        self.disconnected.emit()

    def run(self):
        try:
            while not self.isInterruptionRequested():
                try:
                    kind, args = self.jobs.get(timeout=.025)
                    self._job(kind, args)
                except queue.Empty:
                    pass
                except Exception as exc:
                    self._failure(exc)
                if self.transport and self.live and time.monotonic() >= self.next_poll:
                    try:
                        self._poll()
                    except ValueError as exc:
                        # Settings changed or a display representation is not supported;
                        # transport framing remains valid. Keep the connection usable.
                        self.error.emit(str(exc))
                        self.next_poll = time.monotonic()+1
                    except Exception as exc:
                        self._failure(exc)
        finally:
            self._close()

    def _failure(self, exc):
        self.error.emit(f"{type(exc).__name__}: {exc}")
        if not isinstance(exc, ValueError):
            # A timed-out command can leave a late reply on USB. Discard queued
            # work and release the device rather than risk a mislabeled reading.
            self._close()
            while True:
                try:
                    self.jobs.get_nowait()
                except queue.Empty:
                    break

    def _poll(self):
        if self.mode == "dmm":
            self.reading.emit(read_multimeter(self.transport, self.identity))
        elif self.mode == "scope":
            frame = self.transport.frame() if isinstance(self.transport, DemoTransport) else capture(self.transport, self.identity)
            frame.metadata["identity"] = self.identity
            self.frame.emit(frame)
        self.next_poll = time.monotonic()+self.interval

    def _job(self, kind, args):
        if kind == "connect":
            self._close()
            backend = args[0]
            self.transport = DemoTransport(self.trace.emit) if backend == "Demo" else USBTransport(self.trace.emit)
            self.transport.open()
            self.identity = self.transport.text("*IDN?")
            if backend != "Demo" and not re.search(r"\bHDS2202S(?:_LS)?\b", self.identity, re.I):
                self._close()
                raise ValueError("Device is not an HDS2202S: " + self.identity)
            self.connected.emit(backend, self.identity)
            self.next_poll = 0
            return
        if kind == "disconnect":
            self._close()
            return
        if kind == "mode":
            self.mode = args[0]
            self.next_poll = 0
            return
        if kind == "live":
            self.live = bool(args[0])
            return
        if kind == "interval":
            self.interval = max(.05, float(args[0]))
            return
        if not self.transport:
            raise ValueError("Connect an instrument first")
        if kind == "capture":
            self._poll()
        elif kind == "generator_snapshot":
            self._load_generator()
        elif kind == "generator_apply":
            self._apply_generator_group(*args)
        elif kind == "command":
            token, raw = args
            command = validate_command(raw)
            if command.endswith("?"):
                if ":data:" in command.lower():
                    result = self.transport.block(command)
                    self.reply.emit(token, result.decode("utf8") if ":head?" in command.lower() else result.hex())
                else:
                    self.reply.emit(token, self.transport.text(command))
            else:
                generator_write = command.lower().startswith(":function ") or command.lower().startswith(":function:") or command.lower().startswith(":channel ")
                if generator_write and command.lower() != ":channel off":
                    self._guard_generator(command)
                self._validate_channel(command)
                self._validate_generator(command)
                self.transport.write(command)
                base = command.split(" ", 1)[0]
                if base.lower() in (":running", ":dmm:auto"):
                    readback = self.transport.text(":TRIGger:STATus?" if base.lower() == ":running" else ":DMM:RANGE?")
                else:
                    readback = self.transport.text(base+"?")
                if token == "dmm_function" and base.lower() in (":dmm:configure:voltage", ":dmm:configure:current"):
                    readback = base.rsplit(":", 1)[-1] + " " + readback
                self.reply.emit(token, readback)
                if generator_write:
                    self._load_generator()
            self.next_poll = time.monotonic()+.05
        elif kind == "single":
            for cmd in (":RUNning STOP", ":TRIGger:SINGle:SWEep Single", ":RUNning RUN"):
                self.transport.write(cmd)
            self.reply.emit("single", self.transport.text(":TRIGger:STATus?"))
            self.next_poll = 0
        elif kind == "native":
            results = {}
            for ch in (1, 2):
                for name in MEASUREMENTS:
                    if self.isInterruptionRequested():
                        return
                    command = f":MEASurement:CH{ch}:{name}?"
                    results[f"CH{ch} {name}"] = self.transport.text(command)
            self.native.emit(results)

    def _load_generator(self):
        try:
            state = read_generator_state(self.transport)
        except Exception:
            self.generator_baseline = None
            self.generator_state.emit(None)
            raise
        self.generator_baseline = state
        self.generator_state.emit(state)
        return state

    def _checked_generator(self):
        if self.generator_baseline is None:
            raise ValueError("Load the device's current generator settings before changing parameters or enabling output")
        old = self.generator_baseline
        fresh = self._load_generator()
        if not same_state(old, fresh):
            raise ValueError("Generator settings changed on the device. Fresh values were loaded; review them and apply again")
        return fresh

    def _guard_generator(self, command):
        fresh = self._checked_generator()
        if command.lower() == ":channel on":
            validate_output_envelope(fresh)
        else:
            base, value = command.split(" ", 1)
            values = dict(fresh.values)
            key = next((k for k in CORE_KEYS if BY_KEY[k].command.lower() == base.lower()), None)
            if key in ("gen_amp", "gen_offset"):
                values[key] = value
                from dataclasses import replace
                validate_output_envelope(replace(fresh, values=values))

    def _apply_generator_group(self, group, revision, payload):
        state = self._checked_generator()
        if group == "levels":
            plan = voltage_plan(state, payload["amplitude"], payload["offset"])
        elif group == "frequency":
            command = BY_KEY["gen_freq"].write(str(payload["frequency"]))
            self._validate_generator(command)
            plan = [("gen_freq", payload["frequency"])]
        elif group == "pulse":
            if state.values["gen_wave"].upper() != "PULSE":
                raise ValueError("Apply the Pulse waveform before applying pulse timing")
            key, number = payload["key"], float(payload["value"])
            if key not in ("gen_width", "gen_duty"):
                raise ValueError("Choose pulse width or duty cycle")
            period = 1/quantity(state.values["gen_freq"], "Hz")
            duty = number if key == "gen_duty" else number/period*100
            if not .1 <= duty <= 99.9:
                raise ValueError("Pulse width must fit the current period (duty 0.1–99.9%)")
            plan = [(key, number)]
        else:
            raise ValueError("Unknown generator parameter group")
        pulse_reply = None
        for key, number in plan:
            spec = BY_KEY[key]
            command = spec.write(str(number))
            self._validate_generator(command)
            self.transport.write(command)
            if key in CORE_KEYS:
                actual = self._load_generator()
                reported = quantity(actual.values[key], spec.unit)
            else:
                raw = self.transport.text(spec.command+"?")
                reported = quantity(raw.rstrip("%"), spec.unit)
                pulse_reply = key, raw
                actual = self._load_generator()
            if not math.isclose(reported, float(number), rel_tol=1e-5, abs_tol=1e-9):
                raise ValueError(f"{spec.label}: requested {number:g}, device reported {reported:g}; remaining writes were stopped")
            if actual.output_on != state.output_on:
                raise ValueError("Generator output state changed during Apply; review the fresh readback")
            state = actual
        self.generator_committed.emit(group, revision)
        if pulse_reply:
            self.reply.emit(*pulse_reply)
        self.generator_state.emit(state)

    def _validate_channel(self, command):
        match = re.fullmatch(r":CH([12]):SCALe (.+)", command, re.I)
        if match:
            probe = float(self.transport.text(f":CH{match[1]}:PROBe?").upper().rstrip("X"))
            scale = quantity(match[2], "V")
            if not any(math.isclose(scale/probe, base, rel_tol=1e-6) for base in BASE_SCALES):
                raise ValueError(f"Select a 1–2–5 scale from {BASE_SCALES[0]*probe:g} to {BASE_SCALES[-1]*probe:g} V/div for the current {probe:g}× probe")

    def _validate_generator(self, command):
        base, value = command.split(" ", 1)
        low = base.lower()
        pulse_parameters = (":function:width", ":function:pulse:dtycycle", ":function:rising", ":function:faling")
        if low not in (":function", ":function:frequency", ":function:period", ":function:symmetry", *pulse_parameters):
            return
        shape = wave_name(value if low == ":function" else self.transport.text(":FUNCtion?"))
        maximum = max_frequency(shape)
        if low == ":function:symmetry":
            if shape.upper() != "RAMP":
                raise ValueError("Apply Ramp before configuring symmetry")
            return
        if low in pulse_parameters:
            if shape.upper() != "PULSE":
                raise ValueError("Apply Pulse before configuring PWM, width or edge times")
            period = quantity(self.transport.text(":FUNCtion:PERiod?"), "s")
            if low == ":function:pulse:dtycycle":
                return  # Percentage limits have already been checked by the catalogue.
            duration = quantity(value, "s")
            if duration > period or (low == ":function:width" and not .001 <= duration/period <= .999):
                raise ValueError("Pulse timing must fit the current period; width requires 0.1–99.9% duty")
            return
        freq = (quantity(value, "Hz") if low == ":function:frequency" else
                1/quantity(value, "s") if low == ":function:period" else
                quantity(self.transport.text(":FUNCtion:FREQuency?"), "Hz"))
        if not .1 <= freq <= maximum:
            raise ValueError(f"{shape}: frequency must be 0.1 Hz to {maximum:g} Hz. Lower frequency before changing waveform")
