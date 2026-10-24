"""Host-side AM/FM design only; no instrument I/O or background parameter writes."""
import csv
import math
import numpy as np
from scipy import signal
from scipy.integrate import cumulative_trapezoid
from .device import generator_wave
from .generator import voltage_pair


def design_modulation(shape, carrier_hz, amplitude, offset, mode="AM", mod_hz=100.,
                      depth=.5, deviation_hz=100., mod_shape="Sine", count=8192,
                      duty=.5, symmetry=.5):
    numbers = (carrier_hz, amplitude, offset, mod_hz, depth, deviation_hz, duty, symmetry)
    if not all(math.isfinite(float(x)) for x in numbers):
        raise ValueError("Modulation parameters must be finite")
    if mode not in ("AM", "FM") or mod_shape not in ("Sine", "Triangle", "Square"):
        raise ValueError("Choose AM/FM and a modulating shape")
    if shape.upper() not in ("SINE", "SQUARE", "RAMP", "PULSE"):
        raise ValueError("Choose Sine, Square, Ramp or Pulse for local modulation design")
    voltage_pair(amplitude, offset)
    if carrier_hz <= 0 or mod_hz <= 0 or not 256 <= int(count) <= 8192:
        raise ValueError("Use positive carrier/modulation frequencies and 256–8192 samples")
    if not 0 <= depth <= 1 or deviation_hz < 0 or (mode == "FM" and deviation_hz >= carrier_hz):
        raise ValueError("AM depth: 0–100%; FM deviation must be less than the carrier frequency")
    if not 0 < duty < 1 or not 0 <= symmetry <= 1:
        raise ValueError("Check pulse duty and ramp symmetry")
    peak = amplitude/2*(1+depth if mode == "AM" else 1)
    if abs(offset)+peak > 2.5+1e-9:
        raise ValueError("Modulated envelope exceeds ±2.5 V; lower carrier amplitude, depth or offset")
    # Three modulation periods; the sampled record need not be a seamless cycle.
    t = np.arange(int(count))*(3/mod_hz/int(count))
    upper = carrier_hz+(mod_hz if mode == "AM" else deviation_hz+mod_hz)
    if upper > (len(t)/(3/mod_hz))/20:
        raise ValueError("Too few samples for this frequency ratio: increase modulation frequency or lower carrier/deviation")
    p = 2*np.pi*mod_hz*t
    m = np.sin(p) if mod_shape == "Sine" else signal.square(p) if mod_shape == "Square" else signal.sawtooth(p, .5)
    phase = 2*np.pi*carrier_hz*t
    if mode == "FM":
        phase += 2*np.pi*deviation_hz*cumulative_trapezoid(m, t, initial=0)
    carrier = generator_wave(shape, phase, duty, symmetry)
    envelope = amplitude/2*(1+depth*m) if mode == "AM" else np.full(len(t), amplitude/2)
    y = offset+envelope*carrier
    return t, y, offset+envelope, offset-envelope


def export_modulation(path, t, volts):
    t, volts = np.asarray(t), np.asarray(volts)
    if t.ndim != 1 or t.shape != volts.shape or len(t) < 2 or not np.all(np.isfinite(t)) or not np.all(np.isfinite(volts)) or np.any(np.diff(t) <= 0):
        raise ValueError("Export needs finite, increasing time coordinates and matching voltage samples")
    with open(path, "w", newline="", encoding="utf8") as f:
        writer = csv.writer(f)
        writer.writerow(("time_s", "voltage_V"))
        writer.writerows(zip(t, volts))
