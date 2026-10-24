"""Explicit HDS200 command catalogue, with units and write validation.

Source: https://www.owon.co.jp/download/01a0bfb9-2919-7571-866b-44f3d2bb3464/
RUNning is a local, previously exercised firmware extension (owon_single_shot.py).
The manual has inconsistent spellings for several generator/trigger commands;
we use its syntax sections and expose errors/readback rather than guess success.
"""
from dataclasses import dataclass
import math
import re


def quantity(text, unit=""):
    """Read a finite SI number; case matters for mega versus milli."""
    s = str(text).strip().replace("μ", "u").replace("µ", "u")
    m = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([pnumkKMGT]?)\s*([a-zA-ZΩ%]*)", s)
    if not m or (m[3] and (not unit or m[3].lower() != unit.lower())):
        raise ValueError(f"Expected a number in {unit or 'SI units'}: {text!r}")
    factor = {"": 1, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3,
              "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}[m[2]]
    value = float(m[1]) * factor
    if not math.isfinite(value):
        raise ValueError("Value must be finite")
    return value


def engineering(value, unit="", precision=4):
    if value is None or not math.isfinite(float(value)):
        return "—"
    if value == 0:
        return "0" + (" " + unit if unit else "")
    exp = max(-12, min(12, 3 * int(math.floor(math.log10(abs(value)) / 3))))
    prefix = {-12: "p", -9: "n", -6: "µ", -3: "m", 0: "", 3: "k", 6: "M", 9: "G", 12: "T"}[exp]
    return f"{value / 10**exp:.{precision}g} {prefix}{unit}".strip()


TIMEBASES = tuple(f"{n}{u}" for u in ("ns", "us", "ms", "s") for n in (1, 2, 5, 10, 20, 50, 100, 200, 500)
                 if not (u == "ns" and n == 1)) + ("1000s",)
BASE_SCALES = (.01, .02, .05, .1, .2, .5, 1, 2, 5, 10)
WAVES = ("SINE", "SQUare", "RAMP", "PULSe", "AmpALT", "AttALT", "StairDn", "StairUD", "StairUp", "Besselj", "Bessely", "Sinc")
MEASUREMENTS = ("MAX", "MIN", "PKPK", "VAMP", "AVERage", "PERiod", "FREQuency")


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    command: str
    group: str
    default: str = ""
    choices: tuple = ()
    unit: str = ""
    limits: tuple | None = None
    integer: bool = False
    query: bool = True
    note: str = ""

    def write(self, text):
        text = str(text).strip()
        if self.key == "gen_amp":
            text = re.sub(r"V\s*(?:pp|p-p)$", "V", text, flags=re.I)
        if self.key in ("gen_duty", "gen_sym"):
            text = text.rstrip("%").strip()
        if not text or any(c in text for c in "\r\n;?"):
            raise ValueError("Enter one setting value")
        if self.choices:
            matches = [x for x in self.choices if x.lower() == text.lower()]
            if not matches:
                raise ValueError(f"Choose one of: {', '.join(self.choices)}")
            text = matches[0]
        else:
            value = quantity(text, self.unit)
            if self.limits and not self.limits[0] <= value <= self.limits[1]:
                raise ValueError(f"{self.label}: allowed {self.limits[0]:g} to {self.limits[1]:g} {self.unit}")
            if self.integer and value != int(value):
                raise ValueError("This setting requires an integer")
            # Generator parameters are plain floating-point SI values. Channel
            # scales and trigger levels use the protocol's voltage suffix.
            suffix = "V" if self.unit == "V" and not self.command.startswith(":FUNCtion") else ""
            text = f"{value:.12g}" + suffix
        return f"{self.command} {text}"


SETTINGS = [
    Setting("timebase", "Time / division", ":HORizontal:SCALe", "Horizontal", "500us", TIMEBASES),
    Setting("h_offset", "Horizontal offset (div)", ":HORizontal:OFFSet", "Horizontal", "0", limits=(-200, 200), integer=True),
    Setting("acq", "Acquisition", ":ACQuire:MODE", "Horizontal", "SAMPle", ("SAMPle", "PEAK")),
    Setting("depth", "Memory depth", ":ACQuire:DEPMem", "Horizontal", "4K", ("4K", "8K")),
    Setting("tr_source", "Trigger source", ":TRIGger:SINGle:SOURce", "Trigger", "CH1", ("CH1", "CH2")),
    Setting("tr_edge", "Edge", ":TRIGger:SINGle:EDGe", "Trigger", "RISE", ("RISE", "FALL")),
    Setting("tr_coupling", "Trigger coupling", ":TRIGger:SINGle:COUPling", "Trigger", "DC", ("DC", "AC")),
    Setting("tr_level", "Trigger level", ":TRIGger:SINGle:EDGe:LEVel", "Trigger", "0V", unit="V", limits=(-400, 400)),
    Setting("tr_sweep", "Trigger mode", ":TRIGger:SINGle:SWEep", "Trigger", "Auto", ("Auto", "Normal", "Single")),
    Setting("meas_display", "Scope measurement display", ":MEASurement:DISPlay", "Trigger", "ON", ("ON", "OFF")),
]
for ch in (1, 2):
    group = f"Channel {ch}"
    SETTINGS += [
        Setting(f"ch{ch}_display", "Channel display", f":CH{ch}:DISPlay", group, "ON", ("ON", "OFF")),
        Setting(f"ch{ch}_coupling", "Coupling", f":CH{ch}:COUPling", group, "DC", ("DC", "AC", "GND")),
        Setting(f"ch{ch}_probe", "Probe factor", f":CH{ch}:PROBe", group, "10X", ("1X", "10X", "100X", "1000X")),
        Setting(f"ch{ch}_scale", "Volts / division", f":CH{ch}:SCALe", group, "1V", unit="V", limits=(.01, 10000),
                note="Use a 1–2–5 scale appropriate for the probe factor; read back after applying."),
        Setting(f"ch{ch}_offset", "Vertical offset (div)", f":CH{ch}:OFFSet", group, "0", limits=(-200, 200), integer=True),
    ]
SETTINGS += [
    Setting("gen_wave", "Waveform", ":FUNCtion", "Generator", "SINE", WAVES),
    Setting("gen_freq", "Frequency", ":FUNCtion:FREQuency", "Generator", "1kHz", unit="Hz", limits=(.1, 25e6)),
    Setting("gen_amp", "Amplitude", ":FUNCtion:AMPLitude", "Generator", "1V", unit="V", limits=(.02, 5), note="Vpp, high impedance rating"),
    Setting("gen_offset", "DC offset", ":FUNCtion:OFFSet", "Generator", "0V", unit="V", limits=(-2.5, 2.5)),
    Setting("gen_load", "Device load flag", ":FUNCtion:LOAD", "Generator", "OFF", ("OFF", "ON"),
            note="Raw device load-compensation flag. Numeric Ω load reference is set on the scope's front panel; source impedance remains 50 Ω."),
    Setting("gen_period", "Period", ":FUNCtion:PERiod", "Generator advanced", "1ms", unit="s", limits=(40e-9, 10)),
    Setting("gen_high", "High level", ":FUNCtion:HIGHt", "Generator advanced", "0.5V", unit="V", limits=(-2.5, 2.5)),
    Setting("gen_low", "Low level", ":FUNCtion:LOW", "Generator advanced", "-0.5V", unit="V", limits=(-2.5, 2.5)),
    Setting("gen_sym", "Ramp symmetry (%)", ":FUNCtion:SYMMetry", "Generator advanced", "50", limits=(0, 100), integer=True),
    Setting("gen_width", "Pulse width", ":FUNCtion:WIDTh", "Generator advanced", "500us", unit="s", limits=(1e-9, 10)),
    Setting("gen_rise", "Rise time", ":FUNCtion:RISing", "Generator advanced", "10ns", unit="s", limits=(1e-9, 10)),
    Setting("gen_fall", "Fall time", ":FUNCtion:FALing", "Generator advanced", "10ns", unit="s", limits=(1e-9, 10)),
    Setting("gen_duty", "Pulse duty (%)", ":FUNCtion:PULSe:DTYCycle", "Generator advanced", "50", limits=(.1, 99.9)),
    Setting("dmm_rel", "Relative mode", ":DMM:REL", "Multimeter", "OFF", ("OFF", "ON")),
    Setting("dmm_range", "Range / next range", ":DMM:RANGE", "Multimeter", "V", ("V", "mV", "ON")),
]
BY_KEY = {s.key: s for s in SETTINGS}


def validate_command(command):
    """One known command only; retain manufacturer case on the wire."""
    command = command.strip()
    if not command or len(command) > 180 or any(c in command for c in "\r\n;"):
        raise ValueError("Send one command, without separators")
    if command == "*IDN?":
        return command
    for spec in SETTINGS:
        if command.lower() == (spec.command + "?").lower():
            return spec.command + "?"
        if command.lower().startswith(spec.command.lower() + " "):
            return spec.write(command[len(spec.command) + 1:])
    if re.fullmatch(r":DATA:WAVE:SCREEN:(HEAD|CH[12])\?", command, re.I):
        return command
    if re.fullmatch(r":MEASurement:CH[12]:(" + "|".join(MEASUREMENTS) + r")\?", command, re.I):
        return command
    for canonical in (":TRIGger:STATus?", ":RUNning RUN", ":RUNning STOP", ":CHANnel ON", ":CHANnel OFF", ":CHANnel?",
                      ":DMM:CONFigure?", ":DMM:CONFigure:VOLTage?", ":DMM:CONFigure:CURRent?", ":DMM:MEAS?", ":DMM:AUTO ON"):
        if canonical.lower() == command.lower():
            return canonical
    m = re.fullmatch(r":DMM:CONFigure(?::(VOLTage|CURRent))? (.+)", command, re.I)
    if m:
        choices = ("AC", "DC") if m[1] else ("RESistance", "DIODe", "CONTinuity", "CAPacitance")
        choice = next((x for x in choices if x.lower() == m[2].lower()), None)
        if choice:
            suffix = (":" + ("VOLTage" if m[1].lower() == "voltage" else "CURRent")) if m[1] else ""
            return ":DMM:CONFigure" + suffix + " " + choice
    raise ValueError("Command is outside the HDS Studio catalogue. Use its full spelling (see command reference).")
