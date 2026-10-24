"""Screen-frame decoding and a deterministic instrument simulator."""
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import re
import time
import numpy as np
from scipy import signal, special
from .commands import SETTINGS, BY_KEY, quantity, validate_command


def lower_keys(obj):
    if isinstance(obj, dict):
        return {k.lower(): lower_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [lower_keys(x) for x in obj]
    return obj


def trigger_readout(header):
    """Read trigger labels from the captured header, never from edited controls."""
    h = lower_keys(header)
    trig = h.get("trig", {})
    trig = trig if isinstance(trig, dict) else {}
    items = trig.get("items", {})
    items = items if isinstance(items, dict) else {}
    source = str(items.get("channel", "—")).upper()
    edge = str(items.get("edge", "")).upper()
    mode = str(items.get("sweep", trig.get("sweep", "—")))
    mode = {"AUTO": "Auto", "NORM": "Normal", "NORMAL": "Normal", "SING": "Single", "SINGLE": "Single"}.get(mode.upper(), mode)
    try:
        level = quantity(items["level"], "V")
    except (ValueError, KeyError, TypeError):
        level = None
    return dict(source=source, edge={"RISE": "↑ Rising", "FALL": "↓ Falling"}.get(edge, "—"),
                mode=mode, coupling=str(items.get("coupling", "—")).upper(), level_v=level,
                status=str(h.get("runstatus", "—")).upper())


@dataclass
class Frame:
    t: np.ndarray
    channels: np.ndarray
    header: dict
    metadata: dict = field(default_factory=dict)
    envelopes: dict = field(default_factory=dict)


def signature(header):
    h = lower_keys(header)
    channels = [{k: v for k, v in c.items() if k not in ("frequence", "frequency")} for c in h["channel"]]
    sample = {k: v for k, v in h["sample"].items() if k != "samplerate"}
    state = str(h.get("runstatus", "")).lower()
    if state in ("auto", "ready", "trig", "scan"):
        state = "running"
    return h["timebase"], channels, sample, state, h.get("trig")


def decode_frame(header, payloads, scales=None):
    h = lower_keys(header)
    if str(h.get("datatype", "")).lower() != "screen":
        raise ValueError("Expected a SCREEN waveform header")
    if int(h["sample"].get("slowmove", -1)) != -1:
        raise ValueError("Roll / slow-move export is not supported. Select a faster timebase on the scope")
    division = quantity(h["timebase"]["scale"], "s")
    if division <= 0:
        raise ValueError("Invalid screen timebase")
    channels = np.full((2, 300), np.nan)
    envelopes, raw, clipped, envelope = {}, {}, False, False
    for c in h["channel"]:
        name = c["name"].upper()
        i = int(name[-1])-1
        if str(c.get("display", "ON")).upper() != "ON":
            continue
        payload = payloads[name]
        codes = np.frombuffer(payload, dtype=np.int8).astype(float)
        if len(codes) not in (300, 600):
            raise ValueError(f"{name}: unsupported {len(codes)}-byte screen payload (expected 300 or 600)")
        scale = quantity(scales[name] if scales else c["scale"], "V")
        if scale <= 0:
            raise ValueError("Invalid vertical scale")
        offset = float(c["offset"])
        if not np.isfinite(offset):
            raise ValueError("Invalid vertical offset")
        clipped |= bool(np.any((codes <= -127) | (codes >= 126)))
        if len(codes) == 600:
            pairs = codes.reshape(300, 2)
            envelope |= not np.array_equal(pairs[:, 0], pairs[:, 1])
            envelopes[name] = ((pairs.min(axis=1)-offset)*scale/25, (pairs.max(axis=1)-offset)*scale/25)
            codes = pairs.mean(axis=1)
        # The scale query includes the selected probe factor. These are probe-tip
        # volts; do not multiply by the factor again.
        channels[i] = (codes-offset)*scale/25
        if str(c.get("inverse", "off")).lower() == "on":
            raise ValueError("Inverted screen export is unsupported; disable inversion on the scope")
        raw[name] = payload.hex()
    dt = 12*division/300
    meta = dict(source="USB screen export", captured_utc=datetime.now(timezone.utc).isoformat(),
                screen_interval_s=dt, export_rate_hz=1/dt, internal_rate_text=h["sample"].get("samplerate"),
                volts_reference="probe tip; includes scope probe factor once", screen_envelope=envelope,
                clipped=clipped, raw_payload_hex=raw, scales_readback=scales,
                time_origin="Elapsed screen coordinate; trigger offset retained in header, not applied",
                calibration="Screen conversion: 25 signed pixel codes/div and 12 time divisions, not independently calibrated",
                channel_pair="Sequential screen queries; simultaneous ADC samples unverified")
    return Frame(np.arange(300)*dt, channels, header, meta, envelopes)


def capture(transport, identity):
    start = time.monotonic()
    header = json.loads(transport.block(":DATA:WAVE:SCREEN:HEAD?"))
    h = lower_keys(header)
    active = [c["name"].upper() for c in h["channel"] if c["display"].upper() == "ON"]
    scales = {name: transport.text(f":{name}:SCALe?") for name in active}
    payloads = {name: transport.block(f":DATA:WAVE:SCREEN:{name}?") for name in active}
    frozen = str(h.get("runstatus", "")).upper() == "STOP"
    if frozen:
        repeat = {name: transport.block(f":DATA:WAVE:SCREEN:{name}?") for name in active}
        if payloads != repeat:
            raise ValueError("Stopped screen changed during repeat read; retry capture")
    after = json.loads(transport.block(":DATA:WAVE:SCREEN:HEAD?"))
    if signature(header) != signature(after):
        raise ValueError("Settings changed during USB transfer; retry after controls settle")
    frame = decode_frame(header, payloads, scales)
    frame.metadata.update(identity=identity, transfer_seconds=time.monotonic()-start,
                          frozen_repeat_verified=frozen, header_after=after)
    return frame


def generator_wave(shape, phase, duty=.5, symmetry=.5):
    shape = shape.upper()
    if shape == "SINE":
        return np.sin(phase)
    if shape in ("SQUARE", "PULSE"):
        return signal.square(phase, duty)
    if shape == "RAMP":
        return signal.sawtooth(phase, symmetry)
    if shape == "SINC":
        return np.sinc((phase % (2*np.pi)-np.pi)*3)
    if shape.startswith("BESSEL"):
        x = (phase % (2*np.pi))*2
        return special.y0(x+.5) if shape == "BESSELY" else special.j0(x)
    if shape.startswith("STAIR"):
        ramp = signal.sawtooth(phase, .5 if shape == "STAIRUD" else 1)
        return np.round(ramp*4)/4 * (-1 if shape == "STAIRDN" else 1)
    cycle = (phase % (2*np.pi))/(2*np.pi)
    return np.sin(phase) * (.2+.8*cycle if shape == "AMPALT" else 1-.8*cycle)


class DemoTransport:
    """Simulator shares the command API; demo data are always identified."""
    def __init__(self, trace=None):
        self.trace = trace or (lambda _: None)
        self.settings = {s.command.lower(): s.default for s in SETTINGS}
        self.settings.update({":channel": "OFF", ":running": "RUN", ":dmm:configure": "VOLTage", ":dmm:configure:voltage": "DC",
                              ":trigger:single:edge:level": "1.65V"})
        self.created = time.monotonic()
        self.counter = 0
        self.last_frame = None

    def open(self):
        self.trace("Demo instrument connected; no USB access")

    def close(self):
        self.trace("Demo instrument disconnected")

    def write(self, command):
        command = validate_command(command)
        self.trace("→ " + command)
        key, value = command.split(" ", 1)
        key = key.lower()
        if key.startswith(":dmm:configure:"):
            self.settings[":dmm:configure"] = "VOLTage" if "voltage" in key else "CURRent"
        self.settings[key] = value
        if key == ":running" and value == "STOP" and self.last_frame is not None:
            header = json.loads(json.dumps(self.last_frame.header))
            header["RUNSTATUS"] = "STOP"
            self.last_frame = replace(self.last_frame, header=header)
        if key == ":function:period":
            self.settings[":function:frequency"] = str(1/quantity(value, "s"))
        if key == ":function:frequency":
            self.settings[":function:period"] = str(1/quantity(value, "Hz"))
        if key in (":function:frequency", ":function:period", ":function:pulse:dtycycle"):
            frequency = quantity(self.settings[":function:frequency"], "Hz")
            self.settings[":function:width"] = str(quantity(self.settings[":function:pulse:dtycycle"])/100/frequency)
        if key == ":function:width":
            self.settings[":function:pulse:dtycycle"] = str(100*quantity(value, "s")*quantity(self.settings[":function:frequency"], "Hz"))
        if key in (":function:amplitude", ":function:offset"):
            amplitude = quantity(self.settings[":function:amplitude"], "V")
            offset = quantity(self.settings[":function:offset"], "V")
            self.settings[":function:hight"] = str(offset+amplitude/2)
            self.settings[":function:low"] = str(offset-amplitude/2)
        if key in (":function:hight", ":function:low"):
            high = quantity(self.settings[":function:hight"], "V")
            low = quantity(self.settings[":function:low"], "V")
            self.settings[":function:amplitude"] = str(high-low)
            self.settings[":function:offset"] = str((high+low)/2)

    def text(self, command):
        command = validate_command(command)
        key = command.rstrip("?").lower()
        if command == "*IDN?":
            value = "OWON,HDS2202S_DEMO,SIMULATED,Studio-1.0"
        elif key == ":trigger:status":
            value = "STOP" if self.settings[":running"] == "STOP" else "AUTO"
        elif key == ":dmm:meas":
            function = self.settings[":dmm:configure"].upper()
            base, unit = ({"VOLTAGE": (5.002, "V"), "CURRENT": (.015, "A"), "RESISTANCE": (1000, "Ω"),
                           "CAPACITANCE": (100e-9, "F"), "DIODE": (.643, "V"), "CONTINUITY": (1.2, "Ω")}.get(function, (5.002, "V")))
            measured = base*(1 + .0006*np.sin(time.monotonic()))
            if self.settings[":dmm:rel"].upper() == "ON":
                measured -= base
            value = f"{measured:.7g} {unit}"
        elif key.startswith(":measurement:ch"):
            from .analysis import statistics
            frame = self.frame()
            i = int(re.search(r"ch([12])", key)[1])-1
            stats = statistics(frame.t, frame.channels[i])
            item = key.rsplit(":", 1)[1]
            value = str(stats[{"max":"Max", "min":"Min", "pkpk":"Vpp", "vamp":"Vpp", "average":"Mean", "period":"Period", "frequency":"Frequency"}[item]])
        elif key in self.settings:
            value = self.settings[key]
        else:
            raise ValueError("Demo query unavailable: " + command)
        self.trace("→ " + command)
        self.trace("← " + value)
        return value

    def block(self, command):
        raise ValueError("Binary blocks apply to USB mode; demo frames use floating point samples")

    def frame(self):
        if self.settings[":running"] == "STOP" and self.last_frame is not None:
            return self.last_frame
        self.counter += 1
        division = quantity(self.settings[":horizontal:scale"], "s")
        t = np.arange(2400)*(12*division/2400)
        phase = 2*np.pi*1000*t + .08*np.sin(self.counter*.06)
        rng = np.random.default_rng(self.counter)
        y1 = 1.65+1.2*np.sin(phase)+.08*np.sin(3*phase)+rng.normal(0, .008, len(t))
        y2 = .65+ .72*np.sin(phase-.62)+rng.normal(0, .004, len(t))
        channels, channel_header = [], []
        for i, y in enumerate((y1, y2), 1):
            get = lambda suffix: self.settings[f":ch{i}:{suffix}"]
            coupling = get("coupling")
            if coupling == "AC":
                y = y-y.mean()
            if coupling == "GND":
                y = y*0
            if get("display") == "OFF":
                y = y*np.nan
            channels.append(y)
            channel_header.append(dict(NAME=f"CH{i}", DISPLAY=get("display"), COUPLING=coupling,
                                       PROBE=get("probe"), SCALE=get("scale"), OFFSET=25*float(get("offset")), FREQUENCE=1000))
        header = dict(TIMEBASE=dict(SCALE=self.settings[":horizontal:scale"], HOFFSET=self.settings[":horizontal:offset"]),
                      SAMPLE=dict(SAMPLERATE="1GSa/s (simulated)", TYPE=self.settings[":acquire:mode"], DEPMEM=self.settings[":acquire:depmem"]),
                      CHANNEL=channel_header, DATATYPE="SCREEN", RUNSTATUS="AUTO", MODEL="HDS2202S — DEMO",
                      Trig=dict(Items=dict(Channel=self.settings[":trigger:single:source"], Level=self.settings[":trigger:single:edge:level"],
                                           Edge=self.settings[":trigger:single:edge"], Sweep=self.settings[":trigger:single:sweep"], Coupling=self.settings[":trigger:single:coupling"])))
        frame = Frame(t, np.asarray(channels), header,
                      dict(source="DEMO • synthetic waveforms", captured_utc=datetime.now(timezone.utc).isoformat(),
                           export_rate_hz=1/(t[1]-t[0]), screen_envelope=False, clipped=False,
                           channel_pair="Synthetic paired samples", frozen_repeat_verified=False))
        if self.settings[":trigger:single:sweep"].upper() == "SINGLE":
            self.settings[":running"] = "STOP"
            frame.header["RUNSTATUS"] = "STOP"
        self.last_frame = frame
        return frame
