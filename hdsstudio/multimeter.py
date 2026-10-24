"""Decode HDS multimeter readings together with function/range readback."""
from dataclasses import dataclass
import math
import re
import time

NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
UNIT = r"([pnumkKMµμu]?)(V|A|F|[Oo][Hh][Mm][Ss]?|[ΩΩ])"
FACTORS = {"": 1., "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
           "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6}


def function_info(function):
    raw = str(function).strip()
    key = re.sub(r"[^A-Z]", "", raw.upper())
    if key.startswith("RES") or "OHM" in key:
        return "Resistance", "Ω"
    if key.startswith("CONT"):
        return "Continuity", "Ω"
    if key.startswith("DIO"):
        return "Diode", "V"
    if key.startswith("CAP"):
        return "Capacitance", "F"
    if "VOLT" in key or key in ("V", "DCV", "ACV", "VDC", "VAC"):
        return ("AC voltage" if "AC" in key else "DC voltage" if "DC" in key else "Voltage"), "V"
    if "CURR" in key or key in ("A", "DCA", "ACA", "ADC", "AAC"):
        return ("AC current" if "AC" in key else "DC current" if "DC" in key else "Current"), "A"
    return raw or "Reading function…", ""


def _unit(prefix, name):
    base = "Ω" if name.upper().startswith("OHM") or name in ("Ω", "Ω") else name
    return base, prefix.replace("u", "µ").replace("μ", "µ") + base, FACTORS[prefix]


@dataclass(frozen=True)
class MeterValue:
    value: float | None
    unit: str
    display: str
    function: str
    status: str = ""


def decode_reading(reading):
    text = str(reading["text"]).strip()
    function, base = function_info(reading.get("function", ""))
    explicit = re.fullmatch(r"\s*(" + NUMBER + r")\s*[,;]?\s*" + UNIT + r"\s*(?:AC|DC)?\s*", text)
    bare = re.fullmatch(r"\s*(" + NUMBER + r")\s*", text)
    if explicit:
        number, prefix, name = explicit.groups()
        base, shown_unit, factor = _unit(prefix, name)
    elif bare:
        number = bare[1]
        range_match = re.search(UNIT + r"\s*$", str(reading.get("range", "")))
        if range_match:
            range_base, shown_unit, factor = _unit(*range_match.groups())
            if base and range_base != base:
                return MeterValue(None, "", text, function, "Function and range disagree; waiting for matching readback")
            base = range_base
        elif base:
            shown_unit, factor = base, 1.
        else:
            return MeterValue(None, "", text, function, "Reading function/range to determine the unit…")
    else:
        return MeterValue(None, base, text[:40], function, "Status / overload · original instrument response")
    value = float(number)*factor
    if not math.isfinite(value) or abs(value) >= 1e30:
        return MeterValue(None, base, "OL", function, "Overload · original response: " + text)
    return MeterValue(value, base, number+" "+shown_unit, function)


def read_multimeter(transport, identity):
    """Keep a bare numeric reply paired with the scope's actual unit context.

    Query function and range on both sides of the value to reject front-panel
    changes mid-transfer. Never derive a unit from an unapplied UI selection.
    """
    function = transport.text(":DMM:CONFigure?")
    range_text = transport.text(":DMM:RANGE?")
    text = transport.text(":DMM:MEAS?")
    host_time = time.time()
    after_function = transport.text(":DMM:CONFigure?")
    after_range = transport.text(":DMM:RANGE?")
    normalize = lambda s: re.sub(r"\s+", "", str(s)).upper()
    if normalize(function) != normalize(after_function) or normalize(range_text) != normalize(after_range):
        raise ValueError("Multimeter function/range changed during transfer; retrying with the new scope unit")
    return dict(text=text, function=function, range=range_text, host_time=host_time, source=identity)
