"""Parameterized local waveform designs, independent of the device catalogue."""
from dataclasses import dataclass
import math
import numpy as np
from scipy import signal, special

TEMPLATES = ("Sine", "Square", "Triangle", "Sawtooth up", "Sawtooth down", "Pulse",
             "Sinc", "Bessel J", "Bessel Y", "Stair up", "Stair down", "Stair up / down",
             "Rising sine burst", "Falling sine burst", "Gaussian pulse", "Exponential decay", "Chirp")
ALIASES = {"Pulse 25%": "Pulse", "Bessel J0": "Bessel J", "Bessel Y0": "Bessel Y"}


@dataclass(frozen=True)
class ShapeParameter:
    key: str
    label: str
    default: int | float | str
    low: float = 0
    high: float = 100
    step: float = 1
    decimals: int = 2
    suffix: str = ""
    choices: tuple = ()


CYCLES = ShapeParameter("cycles", "Cycles / record", 1, 1, 64)
PHASE = ShapeParameter("phase_deg", "Phase", 0., -180, 180, 5, 1, " °")
CENTER = ShapeParameter("center_pct", "Center position", 50., 0, 100, 1, 1, " %")
BASELINE = ShapeParameter("baseline", "Output shape", "Bipolar", choices=("Bipolar", "Unipolar"))
STAIRS = (ShapeParameter("levels", "Step levels", 9, 2, 64),
          ShapeParameter("dwell_power", "Dwell bias", 1., .25, 4, .25, 2))
BESSEL = (ShapeParameter("order", "Bessel order", 0, 0, 8),
          ShapeParameter("span", "Argument span", 20., 1, 40, 1, 2),
          ShapeParameter("decay", "Envelope decay", 0., 0, 10, .1, 2))
BURST = (ShapeParameter("cycles", "Carrier cycles", 10, 1, 64), PHASE,
         ShapeParameter("power", "Envelope power", 1., .1, 8, .1, 2),
         ShapeParameter("floor_pct", "Envelope floor", 0., 0, 100, 5, 1, " %"))
SCHEMAS = {
    "Sine": (CYCLES, PHASE),
    "Square": (CYCLES, PHASE, ShapeParameter("duty_pct", "Duty cycle", 50., .1, 99.9, 1, 1, " %")),
    "Pulse": (CYCLES, PHASE, ShapeParameter("duty_pct", "Duty cycle", 25., .1, 99.9, 1, 1, " %")),
    "Triangle": (CYCLES, PHASE, ShapeParameter("symmetry_pct", "Peak position", 50., 1, 99, 1, 1, " %")),
    "Sawtooth up": (CYCLES, PHASE), "Sawtooth down": (CYCLES, PHASE),
    "Sinc": (ShapeParameter("lobes", "Zeros each side", 5, 1, 16),
             ShapeParameter("width_pct", "Total window width", 100., 5, 100, 5, 1, " %"), CENTER,
             ShapeParameter("window", "Taper", "Rectangular", choices=("Rectangular", "Hann", "Blackman"))),
    "Stair up": STAIRS, "Stair down": STAIRS,
    "Stair up / down": (*STAIRS, ShapeParameter("turn_pct", "Turnaround position", 50., 10, 90, 5, 1, " %")),
    "Bessel J": (*BESSEL, ShapeParameter("start", "Argument start", 0., 0, 10, .1, 2)),
    "Bessel Y": (*BESSEL, ShapeParameter("start", "Argument start", .5, .05, 10, .05, 2)),
    "Rising sine burst": BURST, "Falling sine burst": BURST,
    "Gaussian pulse": (CENTER, ShapeParameter("sigma_pct", "Gaussian σ width", 10., .5, 50, .5, 2, " %"), BASELINE),
    "Exponential decay": (ShapeParameter("tau_pct", "Time constant", 14.29, 1, 100, 1, 2, " %"),
                          ShapeParameter("direction", "Direction", "Decay", choices=("Decay", "Rise")), BASELINE),
    "Chirp": (ShapeParameter("start_cycles", "Start cycles / record", 1., .1, 64, .5, 2),
              ShapeParameter("end_cycles", "End cycles / record", 12., .1, 64, .5, 2), PHASE,
              ShapeParameter("sweep", "Sweep law", "Linear", choices=("Linear", "Logarithmic"))),
}


def canonical_template(name):
    name = ALIASES.get(name, name)
    if name not in SCHEMAS:
        raise ValueError("Choose a local waveform template")
    return name


def template_parameters(name, parameters=None):
    specs = SCHEMAS[canonical_template(name)]
    if parameters is not None and not isinstance(parameters, dict):
        raise ValueError("Shape parameters must be a mapping")
    parameters = parameters or {}
    if set(parameters)-{s.key for s in specs}:
        raise ValueError("Unknown parameter for this waveform shape")
    result = {}
    for spec in specs:
        value = parameters.get(spec.key, spec.default)
        if spec.choices:
            if value not in spec.choices:
                raise ValueError("Choose " + spec.label + ": " + ", ".join(spec.choices))
        else:
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(spec.label + " must be a number") from exc
            if not math.isfinite(value) or not spec.low <= value <= spec.high:
                raise ValueError(f"{spec.label}: allowed {spec.low:g}–{spec.high:g}")
            if isinstance(spec.default, int):
                if value != int(value):
                    raise ValueError(spec.label + " requires an integer")
                value = int(value)
        result[spec.key] = value
    return result


def validate_recipe(recipe):
    if not isinstance(recipe, dict) or set(recipe) != {"template", "parameters"}:
        raise ValueError("Invalid waveform recipe")
    name = canonical_template(recipe["template"])
    return dict(template=name, parameters=template_parameters(name, recipe["parameters"]))


def template_samples(name, count=1024, parameters=None):
    """Generate a bounded finite record; no device I/O or seamless-loop promise."""
    name = canonical_template(name)
    if not isinstance(count, (int, np.integer)) or not 16 <= count <= 8192:
        raise ValueError("Use 16–8192 samples")
    p = template_parameters(name, parameters)
    cycles = max(p.get("cycles", 1), p.get("start_cycles", 1), p.get("end_cycles", 1))
    if cycles*8 > count:
        raise ValueError("Increase sample count or reduce cycles (at least 8 samples per cycle)")
    x = np.linspace(0, 1, count)
    phase = 2*np.pi*p.get("cycles", 1)*x + np.deg2rad(p.get("phase_deg", 0))
    if name == "Sine": y = np.sin(phase)
    elif name in ("Square", "Pulse"): y = signal.square(phase, p["duty_pct"]/100)
    elif name == "Triangle": y = signal.sawtooth(phase, p["symmetry_pct"]/100)
    elif name.startswith("Sawtooth"): y = signal.sawtooth(phase)*(1 if name.endswith("up") else -1)
    elif name == "Sinc":
        width = p["width_pct"]/100
        u = (x-p["center_pct"]/100)/width
        if count*width/(2*p["lobes"]) < 4:
            raise ValueError("Increase samples or sinc width, or reduce zeros per side")
        y = np.sinc(2*p["lobes"]*u)
        window = np.ones(count) if p["window"] == "Rectangular" else .5+.5*np.cos(2*np.pi*u) if p["window"] == "Hann" else .42+.5*np.cos(2*np.pi*u)+.08*np.cos(4*np.pi*u)
        y = np.where(np.abs(u) <= .5, y*window, 0)
    elif name.startswith("Stair"):
        n = p["levels"]
        if n*(2 if name.endswith("/ down") else 1) > count:
            raise ValueError("Increase samples or reduce step levels")
        turn = p.get("turn_pct", 50)/100
        u = np.where(x <= turn, x/turn, (1-x)/(1-turn)) if name.endswith("/ down") else x
        index = np.minimum(np.floor(np.clip(u, 0, 1)**p["dwell_power"]*n), n-1)
        if len(np.unique(index)) != n:
            raise ValueError("Increase samples or reduce step levels / dwell bias to resolve every level")
        y = 2*index/(n-1)-1
        if name == "Stair down": y = -y
    elif name.startswith("Bessel"):
        argument = p["start"]+p["span"]*x
        y = (special.jv(p["order"], argument) if name == "Bessel J" else special.yv(p["order"], argument))*np.exp(-p["decay"]*x)
    elif name.endswith("sine burst"):
        envelope = (x if name.startswith("Rising") else 1-x)**p["power"]
        y = (p["floor_pct"]/100+(1-p["floor_pct"]/100)*envelope)*np.sin(phase)
    elif name == "Gaussian pulse":
        y = np.exp(-.5*((x-p["center_pct"]/100)/(p["sigma_pct"]/100))**2)
        if p["baseline"] == "Bipolar": y = 2*y-1
    elif name == "Exponential decay":
        y = np.exp(-x/(p["tau_pct"]/100))
        if p["direction"] == "Rise": y = 1-y
        if p["baseline"] == "Bipolar": y = 2*y-1
    else:
        y = signal.chirp(x, f0=p["start_cycles"], t1=1, f1=p["end_cycles"], method=p["sweep"].lower(), phi=p["phase_deg"])
    if not np.all(np.isfinite(y)):
        raise ValueError("These parameters produced nonfinite waveform samples")
    # Scale down only when needed; preserve genuine attenuation and DC baselines.
    return np.clip(y/max(1., float(np.max(np.abs(y)))), -1, 1)
