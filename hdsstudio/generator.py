"""Generator readback and bounds; no undocumented waveform transfer commands."""
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from .commands import BY_KEY, quantity

CORE_KEYS = ("gen_wave", "gen_freq", "gen_amp", "gen_offset", "gen_load")
SOURCE_OHM = 50.


def voltage_pair(amplitude, offset):
    amplitude, offset = float(amplitude), float(offset)
    if not math.isfinite(amplitude) or not .02 <= amplitude <= 5:
        raise ValueError("Amplitude must be 20 mVpp–5 Vpp")
    if not math.isfinite(offset) or abs(offset)+amplitude/2 > 2.5+1e-9:
        raise ValueError("Keep high/low levels within ±2.5 V (reduce amplitude or offset)")
    return offset+amplitude/2, offset-amplitude/2


def levels_pair(high, low):
    high, low = float(high), float(low)
    amplitude, offset = high-low, (high+low)/2
    voltage_pair(amplitude, offset)
    return amplitude, offset


def voltage_plan(state, amplitude, offset):
    """Order coupled writes so each intermediate nominal envelope is valid."""
    voltage_pair(amplitude, offset)
    old_amp = quantity(state.values["gen_amp"], "V")
    old_offset = quantity(state.values["gen_offset"], "V")
    keys = ("gen_amp", "gen_offset") if amplitude <= old_amp else ("gen_offset", "gen_amp")
    values = {"gen_amp": float(amplitude), "gen_offset": float(offset)}
    current = {"gen_amp": old_amp, "gen_offset": old_offset}
    plan = []
    for key in keys:
        if math.isclose(current[key], values[key], rel_tol=1e-10, abs_tol=1e-12):
            continue
        current[key] = values[key]
        voltage_pair(current["gen_amp"], current["gen_offset"])
        plan.append((key, values[key]))
    return plan


def on_off(text):
    key = str(text).strip().upper()
    if key not in ("ON", "OFF", "1", "0"):
        raise ValueError("Unrecognized generator output/load state: " + str(text))
    return key in ("ON", "1")


def wave_name(text):
    raw = str(text).strip().strip('"')
    aliases = {"SIN": "SINE", "SQU": "SQUare", "PULS": "PULSe", "STAIRDOWN": "StairDn",
               "STAIRUPDOWN": "StairUD"}
    raw = aliases.get(raw.upper(), raw)
    matches = [s for s in BY_KEY["gen_wave"].choices if s.upper() == raw.upper()]
    if not matches:
        raise ValueError("Generator waveform readback is not recognized: " + raw)
    return matches[0]


def load_readback(text):
    """Accept explicit load references on readback without inventing writes."""
    raw = str(text).strip().strip('"')
    if raw.upper() in ("ON", "OFF", "1", "0"):
        return "ON" if on_off(raw) else "OFF"
    if re.sub(r"[\s_-]", "", raw).upper() in ("HIGHZ", "HIZ", "HZ", "INF", "INFINITY"):
        return "High Z"
    value = quantity(re.sub(r"ohms?", "Ω", raw, flags=re.I), "Ω")
    if not 1 <= value <= 10000:
        raise ValueError("Unexpected device load reference: " + raw)
    return f"{value:.12g} Ω"


@dataclass(frozen=True)
class GeneratorState:
    values: dict
    raw: dict
    output_on: bool
    captured_utc: str


def read_generator_state(transport):
    """Read only. Connecting/synchronizing never changes output or settings."""
    output = on_off(transport.text(":CHANnel?"))
    raw = {}
    for key in CORE_KEYS:
        command = BY_KEY[key].command+"?"
        try:
            raw[key] = transport.text(command)
        except ValueError as exc:
            raise ValueError(f"Generator readback {command}: {exc}") from exc
    if on_off(transport.text(":CHANnel?")) != output:
        raise ValueError("Generator output changed while loading settings; read the device again")
    values = dict(raw)
    values["gen_wave"] = wave_name(raw["gen_wave"])
    values["gen_load"] = load_readback(raw["gen_load"])
    for key in ("gen_freq", "gen_amp", "gen_offset"):
        spec = BY_KEY[key]
        text = str(raw[key]).strip().strip('"')
        if key == "gen_amp":
            text = re.sub(r"V\s*(?:pp|p-p)$", "V", text, flags=re.I)
        value = quantity(text, spec.unit)
        spec.write(str(value))  # Reject implausible/ambiguous firmware scaling.
        values[key] = f"{value:.12g}" + ("V" if spec.unit == "V" else "Hz" if spec.unit == "Hz" else "")
    return GeneratorState(values, raw, output, datetime.now(timezone.utc).isoformat())


def same_state(a, b):
    if a.output_on != b.output_on:
        return False
    for key in CORE_KEYS:
        if key in ("gen_wave", "gen_load"):
            if a.values[key].upper() != b.values[key].upper():
                return False
        elif not math.isclose(quantity(a.values[key], BY_KEY[key].unit), quantity(b.values[key], BY_KEY[key].unit), rel_tol=1e-8, abs_tol=1e-12):
            return False
    return True


def validate_output_envelope(state):
    amp = quantity(state.values["gen_amp"], "V")
    offset = quantity(state.values["gen_offset"], "V")
    if abs(offset)+amp/2 > 2.5+1e-9:
        raise ValueError("Amplitude and offset exceed the ±2.5 V high-impedance envelope; reduce them before enabling output")


def load_factor(resistance):
    """Theoretical divider from an open-circuit reference, not a device setting."""
    if resistance is None:
        return 1.
    resistance = float(resistance)
    if not math.isfinite(resistance) or not 1 <= resistance <= 10000:
        raise ValueError("Preview resistance must be 1 Ω to 10 kΩ")
    return resistance/(SOURCE_OHM+resistance)
