"""Device waveform capabilities and local editing setups; no hardware writes."""
from dataclasses import dataclass
from .commands import WAVES


@dataclass(frozen=True)
class Configuration:
    key: str
    label: str
    tab: int = 0
    focus: str = "gen_freq"
    symmetry: int | None = None


SIGNAL = (Configuration("frequency", "Frequency / output levels"),
          Configuration("period", "Period / output levels", focus="gen_period"))
PULSE = (Configuration("pwm", "PWM · duty cycle (%)", 1, "gen_duty"),
         Configuration("width", "Pulse width · time", 1, "gen_width"),
         Configuration("edges", "Edge timing · rise / fall", 1, "gen_rise"))
RAMP = (Configuration("symmetry", "Ramp · adjustable symmetry (%)", 1, "gen_sym"),
        Configuration("triangle", "Triangle · 50% symmetry", 1, "gen_sym", 50),
        Configuration("rising", "Rising sawtooth · 100% symmetry", 1, "gen_sym", 100),
        Configuration("falling", "Falling sawtooth · 0% symmetry", 1, "gen_sym", 0))
ARBITRARY_NOTES = {
    "AMPALT": "AmpALT built-in oscillation shape",
    "ATTALT": "AttALT built-in oscillation shape",
    "STAIRDN": "Descending staircase",
    "STAIRUD": "Ascending / descending staircase",
    "STAIRUP": "Ascending staircase",
    "BESSELJ": "Built-in Bessel J shape",
    "BESSELY": "Built-in Bessel Y shape",
    "SINC": "Built-in sinc shape",
}


def configurations(shape):
    shape = shape.upper()
    if shape == "PULSE":
        return PULSE
    if shape == "RAMP":
        return RAMP
    if shape == "SQUARE":
        return (Configuration("square", "Square · fixed 50% duty"),
                Configuration("pwm", "PWM (%) · switch draft to Pulse", 1, "gen_duty"))
    if shape not in {s.upper() for s in WAVES}:
        raise ValueError("Unknown device waveform: " + shape)
    return SIGNAL


def max_frequency(shape):
    return 25e6 if shape.upper() == "SINE" else 1e6 if shape.upper() == "RAMP" else 5e6


def profile_note(shape):
    shape = shape.upper()
    if shape == "SINE":
        return "Sine: frequency / period and linked voltage levels. AM/FM design is available in the local designer."
    if shape == "SQUARE":
        return "Square uses fixed 50% duty. Select the PWM setup to prepare a Pulse draft with adjustable duty; Apply waveform changes the device."
    if shape == "PULSE":
        return "Pulse: PWM duty (%), width (time), and rise/fall edge times. Width and duty follow frequency; PWM here sets duty, not continuous modulation."
    if shape == "RAMP":
        return "Ramp: symmetry (%) determines rising time within a cycle. Triangle and sawtooth setups edit the symmetry draft only."
    return ARBITRARY_NOTES.get(shape, "Built-in arbitrary waveform") + ": frequency / period and linked voltage levels. Shape internals have no documented adjustable parameters."


STEP_CHOICES = {
    "frequency": ("0.01% relative", "0.1 Hz", "1 Hz", "10 Hz", "100 Hz", "1 kHz", "10 kHz", "100 kHz", "1 MHz"),
    "voltage": ("1 mV", "10 mV", "100 mV", "1 V"),
    "time": ("1 ns", "10 ns", "100 ns", "1 us", "10 us", "100 us", "1 ms", "10 ms", "100 ms", "1 s"),
    "duty": ("0.1%", "0.5%", "1%", "5%", "10%"),
    "symmetry": ("1%", "5%", "10%"),
}


def steps_for(key):
    if key == "gen_freq":
        return STEP_CHOICES["frequency"], "0.01% relative"
    if key == "gen_duty":
        return STEP_CHOICES["duty"], "1%"
    if key == "gen_sym":
        return STEP_CHOICES["symmetry"], "1%"
    if key in ("gen_amp", "gen_offset", "gen_high", "gen_low"):
        choices = STEP_CHOICES["voltage"]
        return (tuple(s+"pp" for s in choices), "10 mVpp") if key == "gen_amp" else (choices, "10 mV")
    return STEP_CHOICES["time"], "1 ns" if key in ("gen_rise", "gen_fall") else "1 us" if key == "gen_width" else "10 us"
